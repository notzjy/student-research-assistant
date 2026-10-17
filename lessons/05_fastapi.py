"""第五课：用 FastAPI 把已有 LangGraph 流程提供为本机 HTTP 服务。"""

import argparse
import importlib
import json
import os
import sqlite3
import sys
from collections.abc import Callable
from contextlib import nullcontext
from threading import Lock
from typing import Literal

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from langchain_core.messages import ToolMessage
from langchain_core.exceptions import OutputParserException
from langgraph.errors import GraphRecursionError
from openai import APIError, APITimeoutError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from task_store import TaskStore
from time_parser import NormalizedTime, TimeNormalizationRequest, normalize_time, parse_reference_time
from time_semantics import TimeInterpretation, interpret_time


# 数字开头的课程文件通过 importlib 复用，不复制用户已完成的图和工具。
lesson04 = importlib.import_module("04_langgraph")
lesson03 = lesson04.lesson03


class MessageRequest(BaseModel):
    """声明 POST 请求正文；只接受去除首尾空白后的非空通知文本。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=5000, description="待处理的完整通知原文")


class TaskRecord(BaseModel):
    """描述本地待办记录，使查询响应的字段和类型清楚可见。"""

    id: str
    title: str
    time_text: str | None = None
    location: str | None = None
    status: str


class ToolResult(BaseModel):
    """校验保存、完成或失败的工具结果，不根据模型答复猜测是否修改成功。"""

    status: Literal["created", "exists", "completed", "error"]
    task: TaskRecord | None = None
    error: str | None = None


class QueryToolResult(BaseModel):
    """校验查询工具的真实返回：筛选条件、记录数量与列表，不把读取误报为新增。"""

    status: Literal["queried"]
    filter_status: Literal["pending", "completed", "all"]
    count: int = Field(ge=0, strict=True)
    tasks: list[TaskRecord]


class MessageResponse(BaseModel):
    """返回模型答复、节点路径、收到反馈的工具请求名称及可核对的保存或查询结果。"""

    execution_status: Literal["completed", "tool_error", "no_tools"]
    reply: str
    node_order: list[str]
    tool_names: list[str]
    tool_results: list[ToolResult | QueryToolResult]


class TasksResponse(BaseModel):
    """返回当前本地待办列表和记录数量，不调用模型。"""

    count: int
    tasks: list[TaskRecord]


def collect_tool_results(state: dict) -> list[ToolResult | QueryToolResult]:
    """从 ToolMessage 按结果类型选择 Pydantic 模型校验，不从模型结语推断执行结果。"""
    results = []
    for message in state["messages"]:
        if not isinstance(message, ToolMessage):
            continue
        if not isinstance(message.content, str):
            raise ValueError("工具结果必须是 JSON 文本")
        data = json.loads(message.content)
        if not isinstance(data, dict):
            raise ValueError("工具结果必须是 JSON 对象")
        # 保存和完成返回一条 task，查询返回一组 tasks；分别校验，避免丢失字段。
        result_model = QueryToolResult if data.get("status") == "queried" else ToolResult
        results.append(result_model.model_validate(data))
    return results


def create_app(model_factory: Callable | None = None, *, store: TaskStore | None = None) -> FastAPI:
    """创建 API；模型工厂用于测试，传入 store 时所有待办读写都使用该存储。"""
    api = FastAPI(title="个人研究与日程助理 · 教学 API", version="0.1.0")
    # 旧 JSON 模式保留进程内锁；SQLite 每次操作独立事务，不让锁覆盖模型网络请求。
    storage_guard = Lock() if store is None else nullcontext()

    @api.get("/health", summary="检查服务是否运行")
    def health() -> dict[str, str]:
        """返回服务存活状态；不访问模型、密钥或待办文件。"""
        return {"status": "ok"}

    @api.post("/time/normalize", response_model=NormalizedTime, summary="预览原文时间的明确时刻或待确认信息")
    def preview_time(payload: TimeNormalizationRequest) -> NormalizedTime:
        """以原消息时间归一化有限时间表达；不调用模型、读写待办或创建提醒。"""
        try:
            return normalize_time(payload.time_text, parse_reference_time(payload.reference_time))
        except ValueError:
            raise HTTPException(status_code=422, detail="时间表达中的日期或钟点不合法，或超出可表示范围。") from None

    @api.post("/time/interpret", response_model=TimeInterpretation, summary="由模型识别意图并返回日期、时刻或范围")
    def interpret_time_message(payload: TimeNormalizationRequest) -> TimeInterpretation:
        """调用模型提取相对时间意图并由程序计算；不访问待办存储或设置提醒。"""
        if model_factory is None:
            names = ("COMPATIBLE_MODEL", "COMPATIBLE_BASE_URL", "COMPATIBLE_API_KEY")
            if any(not os.environ.get(name, "").strip() for name in names):
                raise HTTPException(status_code=503, detail="模型配置不完整，请在本机.env中配置。")
        try:
            model = model_factory() if model_factory is not None else None
            return interpret_time(payload, model=model)
        except APITimeoutError:
            raise HTTPException(status_code=504, detail="时间意图模型请求超时。") from None
        except APIError:
            raise HTTPException(status_code=502, detail="时间意图模型服务请求失败。") from None
        except (OutputParserException, ValidationError):
            raise HTTPException(status_code=502, detail="模型返回的时间结构未通过校验。") from None
        except ValueError:
            raise HTTPException(status_code=422, detail="参考时间或计算结果超出有效日历范围。") from None

    @api.get("/tasks", response_model=TasksResponse, summary="查询已保存待办")
    def get_tasks() -> TasksResponse:
        """从注入存储或旧 JSON 查询待办；存储读取失败时返回不含内部细节的 HTTP 500。"""
        try:
            with storage_guard:
                records = lesson03.load_tasks() if store is None else store.list_tasks()
                tasks = [TaskRecord.model_validate(item) for item in records]
            return TasksResponse(count=len(tasks), tasks=tasks)
        except (OSError, ValueError, sqlite3.Error):
            raise HTTPException(status_code=500, detail="本地待办存储读取失败，请检查服务端数据。") from None

    # 当前图使用同步 invoke 和存储操作，普通 def 由 FastAPI 在线程池执行。
    @api.post("/messages", response_model=MessageResponse, summary="处理通知或操作待办")
    def process_message(payload: MessageRequest) -> MessageResponse:
        """接收校验后的通知，运行图并返回实际结果；明确报告模型或流程错误。"""
        if model_factory is None:
            required = ("COMPATIBLE_MODEL", "COMPATIBLE_BASE_URL", "COMPATIBLE_API_KEY")
            if any(not os.environ.get(name, "").strip() for name in required):
                raise HTTPException(status_code=503, detail="模型配置不完整，请在本机 .env 中配置。")
        try:
            # store 显式传到图和工具；只在旧 JSON 模式串行执行整个请求。
            with storage_guard:
                model = model_factory() if model_factory is not None else None
                # SQLite 允许先查询再完成及一次纠错；这里限制的是图节点步数，不是模型调用次数。
                state = lesson04.run_graph(
                    payload.text, model=model, max_steps=12 if store is not None else 8, store=store
                )
                results = collect_tool_results(state)
            execution_status = "no_tools"
            if results:
                execution_status = "tool_error" if any(item.status == "error" for item in results) else "completed"
            content = state["messages"][-1].content
            return MessageResponse(
                execution_status=execution_status,
                reply=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                node_order=state["node_history"],
                # 失败或未注册的请求也可能有 ToolMessage；名称列表不代表全部执行成功。
                tool_names=[
                    message.name for message in state["messages"]
                    if isinstance(message, ToolMessage) and message.name is not None
                ],
                tool_results=results,
            )
        except GraphRecursionError:
            raise HTTPException(status_code=409, detail="流程达到执行步数上限；已提交的待办写入或状态修改不会回滚，可查询 /tasks。") from None
        except APITimeoutError:
            raise HTTPException(status_code=504, detail="模型请求超时；部分待办可能已保存或修改，请查询 /tasks。") from None
        except APIError:
            # 不把提供商原始错误或凭证内容直接放进 HTTP 响应。
            raise HTTPException(status_code=502, detail="模型服务请求失败；部分待办可能已保存或修改，请查询 /tasks。") from None
        except (OSError, ValueError, sqlite3.Error):
            raise HTTPException(status_code=500, detail="流程或本地数据处理失败；请查询 /tasks 核对记录与完成状态。") from None
    @api.get("/tasks/count", summary="查询待办数量")
    def count_tasks() -> dict[str, int]:
        """复用本地待办查询，仅返回数量，不调用模型。"""
        return {"count": get_tasks().count}
    return api


# 导入应用不会读取 .env 或发起模型请求；运行入口负责配置初始化。
app = create_app()


def main():
    """加载已有本机配置，并以单进程方式启动仅本机可访问的 Uvicorn 服务。"""
    load_dotenv(lesson03.ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000, help="本机服务端口，默认 8000")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 到 65535 之间")
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
