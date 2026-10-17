"""第四课：用 LangGraph 状态、节点和条件边编排模型与待办工具。"""

import argparse
import importlib
import json
import operator
import os
import sys
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from openai import APIError

from assistant_tools import create_task_tools
from task_store import TaskStore


lesson03 = importlib.import_module("03_tool_calling")
TRACE_FILE = Path(__file__).resolve().parents[1] / "local-data" / "lesson04_trace.json"
INSTRUCTION = lesson03.INSTRUCTION + "已保存或已存在的同一事项不重复调用工具。"
QUERY_INSTRUCTION = (
    "如果用户询问已经保存的待办，必须调用 list_tasks 读取数据库，以本轮工具结果回答，不凭空猜测。"
    "询问未完成用 status=pending，询问已完成用 status=completed，询问全部用 status=all。"
    "只有用户明确提供需要新增的事项时才调用 save_task；纯查询问题本身不是新待办，不要把问题保存。"
    "查询没有匹配记录时如实说明；工具失败时说明失败，不将失败说成没有待办。"
    "如果用户明确要求把已有事项标记为完成，先调用 list_tasks(status=all) 查询，"
    "然后仅对能够明确对应用户要求的记录调用 complete_task，task_id 必须来自本轮查询结果。"
    "没有匹配项或存在多项无法确定的匹配时先向用户澄清，不猜 ID、不批量完成。"
    "完成指令不是新待办，不要调用 save_task 保存该指令；已完成事项重复操作仍保持完成。"
)


class AssistantState(MessagesState):
    """定义图内共享数据：对话消息及按执行顺序合并的节点名称。"""

    node_history: Annotated[list[str], operator.add]


def route_after_model(state: AssistantState):
    """检查最后一条模型消息；有工具调用时进入 tools，否则返回结束标记。"""
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "tools"
    return END


def build_agent(model=None, *, store: TaskStore | None = None):
    """构建保留 report 节点的图；传入 store 时工具和统计使用同一数据库存储。"""
    model = model if model is not None else lesson03.create_model()
    # 工具的定义和执行共用同一张注册表，SQLite 工具由传入的 store 创建。
    registry = create_task_tools(store) if store is not None else {"save_task": lesson03.save_task}
    model_with_tools = model.bind_tools(list(registry.values()))

    def call_model(state: AssistantState):
        """把当前消息交给模型，返回新增模型消息和本节点的执行记录。"""
        response = model_with_tools.invoke(state["messages"])
        if response.invalid_tool_calls:
            raise ValueError("模型返回无法解析的工具参数，未执行本轮工具")
        print("[model] 工具调用数量:", len(response.tool_calls))
        # 这里只返回更新；MessagesState 会合并消息，node_history 的 reducer 会连接列表。
        return {"messages": [response], "node_history": ["model"]}

    def call_tools(state: AssistantState):
        """顺序执行模型提出的工具调用，将对应 ToolMessage 返回图状态。"""
        results = []
        for call in state["messages"][-1].tool_calls:
            result = lesson03.execute_tool_call(call, registry=registry)
            results.append(result)
            print("[tools]", call["name"], result.content)
        return {"messages": results, "node_history": ["tools"]}
    def report(state: AssistantState):
        """从与保存工具相同的存储查询数量，保留用户添加的 report 节点与路径。"""
        tasks = lesson03.load_tasks() if store is None else store.list_tasks()
        print("[report] 本地待办数量:", len(tasks))
        if store is not None:
            print("[report] SQLite 未完成数量:", store.count_pending())
        return {"node_history": ["report"]}

    # 保留用户添加的 tools → report → model 路径。
    builder = StateGraph(AssistantState)
    builder.add_node("model", call_model)
    builder.add_node("tools", call_tools)
    builder.add_node("report", report)
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", route_after_model, {"tools": "tools", END: END})
    builder.add_edge("tools", "report")
    builder.add_edge("report", "model")
    return builder.compile()


def run_graph(notice: str, model=None, max_steps: int = 8, *, store: TaskStore | None = None) -> AssistantState:
    """用通知初始化图，将可选存储传给构建函数，返回完整消息与实际节点顺序。"""
    if max_steps < 1:
        raise ValueError("max_steps 必须大于 0")
    agent = build_agent(model, store=store)
    initial_state = {
        # SQLite 模式增加查询和完成指令；旧 JSON 课程保持原来的工具与提示词。
        "messages": [
            SystemMessage(content=INSTRUCTION + (QUERY_INSTRUCTION if store is not None else "")),
            HumanMessage(content=notice),
        ],
        "node_history": [],
    }
    result = agent.invoke(initial_state, config={"recursion_limit": max_steps})
    print("\n实际节点顺序:", " → ".join(result["node_history"]))
    print("最终状态中的消息数量:", len(result["messages"]))
    print("模型答复:")
    print(result["messages"][-1].content)
    return result


class PreviewModel:
    """只供生成图结构的占位模型，禁止实际推理。"""

    def bind_tools(self, tools):
        """接收工具定义并返回自身，使预览模式可以完成图编译。"""
        return self

    def invoke(self, messages):
        """阻止预览模型被用于真实执行，误调用时立即报错。"""
        raise RuntimeError("预览模式不允许调用模型")


def main():
    """读取本机配置和命令行参数，运行、预览流程或查询已保存待办。"""
    load_dotenv(lesson03.ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", default=lesson03.NOTICE, help="替换输入通知")
    parser.add_argument("--max-steps", type=int, default=8, help="图执行步数上限，不是单纯模型调用次数")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preview", action="store_true", help="只打印实际图结构，不联网或执行工具")
    modes.add_argument("--list", action="store_true", help="查看复用第三课工具保存的待办")
    args = parser.parse_args()
    if args.max_steps < 1:
        parser.error("--max-steps 必须大于 0")

    try:
        if args.list:
            print(json.dumps(lesson03.load_tasks(), ensure_ascii=False, indent=2))
            return
        if args.preview:
            print("图结构预览：不调用模型、不执行工具。")
            print(build_agent(PreviewModel()).get_graph().draw_mermaid())
            return

        required = ("COMPATIBLE_MODEL", "COMPATIBLE_BASE_URL", "COMPATIBLE_API_KEY")
        missing = [name for name in required if not os.environ.get(name, "").strip()]
        if missing:
            parser.exit(2, "尚未配置：" + ", ".join(missing) + "。沿用已有 .env。\n")
        state = run_graph(args.message, max_steps=args.max_steps)
        trace = {
            "notice": args.message,
            "node_order": state["node_history"],
            "messages": [message.model_dump(mode="json") for message in state["messages"]],
            "final_reply": state["messages"][-1].content,
        }
        lesson03.write_json(TRACE_FILE, trace)
        print("\n本地待办文件:", lesson03.TASK_FILE)
        print("本次图执行记录:", TRACE_FILE)
    except GraphRecursionError:
        parser.exit(1, "达到图执行步数上限，流程已停止。已保存的待办不会回滚，可用 --list 查看。\n")
    except APIError as error:
        parser.exit(1, f"模型请求失败：{type(error).__name__}，HTTP 状态 {getattr(error, 'status_code', None)}。已保存待办可用 --list 查看。\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"执行失败：{error}。已保存待办可用 --list 查看。\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
