"""第三课：模型提出工具调用，Python 保存待办，再把工具结果交回模型。"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from openai import APIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError


ROOT = Path(__file__).resolve().parents[1]
TASK_FILE = ROOT / "local-data" / "lesson03_tasks.json"
TRACE_FILE = ROOT / "local-data" / "lesson03_trace.json"
NOTICE = "明天下午三点在实验楼302开组会；周五前交综述，附件是两篇参考论文。"
INSTRUCTION = (
    "用户希望把通知中明确需要做的事项保存为待办。请为每项事项分别调用 save_task，"
    "标题使用原文动作短语，时间保留原话，地点未说明时为 null。"
    "附件描述保留在通知中，单纯收到附件不自动增加待办。没有明确事项时不调用工具。"
    "以工具执行结果为准，成功前不要声称已保存；不要声称设置了定时提醒。"
    "工具结果返回后，用简短中文说明保存情况和仍不明确的信息。"
)


class SaveTaskInput(BaseModel):
    """定义保存工具允许接收的参数，并拒绝错误类型或额外字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=120, description="待办名称，使用通知中的动作短语")
    time_text: str | None = Field(default=None, description="通知中的时间原话，未说明时为 null")
    location: str | None = Field(default=None, description="该待办对应的地点，未说明时为 null")


def load_tasks() -> list[dict]:
    """读取本地待办列表；文件不存在时返回空列表，格式损坏时抛出异常。"""
    if not TASK_FILE.exists():
        return []
    tasks = json.loads(TASK_FILE.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or any(not isinstance(task, dict) or "id" not in task for task in tasks):
        raise ValueError("本地待办文件结构不正确，未覆盖文件")
    return tasks


def write_json(path: Path, value):
    """将可序列化的数据 value 写入 path，通过临时文件替换目标文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    # 先完整写入临时文件，再替换目标，避免目标文件暴露尚未写完的 JSON。
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


@tool(args_schema=SaveTaskInput)
def save_task(title: str, time_text: str | None = None, location: str | None = None) -> dict:
    """将一项明确待办保存到本机教学文件，保留原文时间，不设置提醒。"""
    data = SaveTaskInput(title=title, time_text=time_text, location=location).model_dump()
    # 相同字段生成相同标识，重复工具调用可据此返回已存在的待办。
    fingerprint = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    task_id = hashlib.sha256(fingerprint).hexdigest()[:16]
    tasks = load_tasks()
    for existing in tasks:
        if existing["id"] == task_id:
            return {"status": "exists", "task": existing}
    saved = {"id": task_id, **data, "status": "pending"}
    tasks.append(saved)
    write_json(TASK_FILE, tasks)
    return {"status": "created", "task": saved}


def execute_tool_call(call: dict, registry: dict[str, BaseTool] | None = None) -> ToolMessage:
    """按工具白名单执行 call；允许外部注入工具表，返回成功或错误的 ToolMessage。"""
    # None 才启用旧课默认工具；显式传空字典表示本次不允许执行任何工具。
    registry = {"save_task": save_task} if registry is None else registry
    selected = registry.get(call["name"])
    if selected is None:
        result = {"status": "error", "error": "未注册的工具，未执行"}
    else:
        try:
            result = selected.invoke(call["args"])
        except ValidationError:
            result = {"status": "error", "error": "工具参数未通过 Pydantic 校验，未保存"}
        except (OSError, ValueError, sqlite3.Error) as error:
            result = {"status": "error", "error": f"本地保存失败：{type(error).__name__}"}
    # tool_call_id 对应模型请求的 id，让下一次模型调用能识别这是谁的执行结果。
    return ToolMessage(
        content=json.dumps(result, ensure_ascii=False), tool_call_id=call["id"], name=call["name"]
    )


def create_model():
    """使用环境中的兼容 API 配置创建模型客户端，并关闭 DeepSeek 思考模式。"""
    return ChatOpenAI(
        model=os.environ["COMPATIBLE_MODEL"],
        base_url=os.environ["COMPATIBLE_BASE_URL"],
        api_key=os.environ["COMPATIBLE_API_KEY"],
        timeout=60,
        max_retries=0,
        http_socket_options=(),
        extra_body={"thinking": {"type": "disabled"}},
    )


def run_assistant(notice: str, model=None) -> dict:
    """执行通知的一轮工具流程，返回原文、工具调用、执行结果和最终回复。

    notice 是通知原文；model 可传入真实客户端或离线测试替身。
    """
    model = model if model is not None else create_model()
    messages = [SystemMessage(content=INSTRUCTION), HumanMessage(content=notice)]
    model_with_tools = model.bind_tools([save_task])
    proposal = model_with_tools.invoke(messages)
    if proposal.invalid_tool_calls:
        raise ValueError("模型返回了无法解析的工具参数，未执行工具")
    messages.append(proposal)
    print("1. 模型提出的工具调用：")
    print(json.dumps(proposal.tool_calls, ensure_ascii=False, indent=2))

    results = []
    # 模型只提出工具请求，由本机 Python 顺序执行并把结果加入消息历史。
    for call in proposal.tool_calls:
        tool_message = execute_tool_call(call)
        messages.append(tool_message)
        result = json.loads(tool_message.content)
        results.append(result)
        print("2. Python 工具执行结果：")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if proposal.tool_calls:
        # 第二次使用未绑定工具的模型总结本轮实际执行结果。
        final = model.invoke(messages)
        if final.tool_calls or final.invalid_tool_calls:
            raise ValueError("本课总结阶段不接受新工具调用，已执行的结果仍保留在本地文件")
        reply = final.content
    else:
        print("本轮没有执行工具。")
        reply = proposal.content
    print("3. 模型读取工具结果后的答复：")
    print(reply)
    return {
        "notice": notice,
        "tool_calls": proposal.tool_calls,
        "tool_results": results,
        "final_reply": reply,
    }


def main():
    """解析命令行参数，执行预览、待办查询或真实工具流程，并保存运行记录。"""
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", default=NOTICE, help="替换输入通知")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preview", action="store_true", help="只预览工具与输入，不调用模型、不写待办")
    modes.add_argument("--list", action="store_true", help="只查看本地待办，不调用模型")
    args = parser.parse_args()

    try:
        if args.list:
            print(json.dumps(load_tasks(), ensure_ascii=False, indent=2))
            return
        if args.preview:
            print("只预览：不调用模型、不执行工具。")
            print("system:", INSTRUCTION)
            print("human:", args.message)
            print("工具:", save_task.name)
            print(SaveTaskInput.model_json_schema())
            return

        required = ("COMPATIBLE_MODEL", "COMPATIBLE_BASE_URL", "COMPATIBLE_API_KEY")
        missing = [name for name in required if not os.environ.get(name, "").strip()]
        if missing:
            parser.exit(2, "尚未配置：" + ", ".join(missing) + "。沿用第一课的本机 .env。\n")
        trace = run_assistant(args.message)
        write_json(TRACE_FILE, trace)
        print("\n本地待办文件:", TASK_FILE)
        print("本次执行记录:", TRACE_FILE)
    except APIError as error:
        parser.exit(1, f"模型请求失败：{type(error).__name__}，HTTP 状态 {getattr(error, 'status_code', None)}。已写入的待办可用 --list 查看。\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"执行失败：{error}。已写入的待办可用 --list 查看。\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
