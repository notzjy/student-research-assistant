"""主Agent：显式运行上下文与持久图状态分开，保留模型、工具和报告节点。"""

import asyncio
import json
import operator
from dataclasses import dataclass
from typing import Annotated, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.runtime import Runtime

from student_assistant.bridge import TaskStore, execute_tool_call
from student_assistant.tracing import span


INSTRUCTION = (
    "你是个人研究与日程助理，用简短中文回答。对话历史属于当前thread_id，可用于理解追问和用户称呼。"
    "待办事实必须通过工具查业务数据库，不能把聊天记忆当成数据库当前状态。"
    "用户明确给出需执行的新事项时调用save_task，保留时间原话及地点；每项单独保存。"
    "纯聊天、查询、完成指令本身都不是新待办，不能保存成任务。"
    "询问已有待办必须list_tasks；pending是未完成、completed是已完成、all是全部。"
    "用户明确要求完成已有事项时，本轮先list_tasks(all)，从真实结果取ID后调用complete_task。"
    "任务歧义先澄清；不猜ID，不自动批量完成，不重复保存已经存在的事项。"
    "用户要求通知分类、摘要或结构化分析时调用analyze_notice，完整保留原文及附件线索。"
    "用户要求解释时间时调用interpret_time；参考时间只能取自本轮或历史消息提供的元数据。"
    "询问研究文件内容时先调用search_documents检索，依据真实片段回答并说明文件和页码；"
    "没有支持答案的片段就如实说明不知道，不能声称读过未上传的附件。"
    "元数据缺失就询问原消息时间，不使用服务器今天替代；引用旧消息时仍以那条消息为准。"
    "裸周三/周四等按消息本周，周一至周日，即使已过去也不顺延。"
    "日期、日期范围和精确时刻分别处理，缺钟点不补零点或23:59。"
    "以ToolMessage的实际结果说明执行是否成功，失败不能说成功，空查询不能编造任务。"
    "当前仅提供本地分析与待办操作，不能声称已经下载论文、设置提醒或接通微信等应用。"
)


class AssistantState(MessagesState):
    """持久化消息与节点记录；当前用户轮次标识和起点用于精确截取本轮结果。"""

    node_history: Annotated[list[str], operator.add]
    turn_id: str
    turn_start_history: int


@dataclass
class TurnContext:
    """仅本次运行使用的依赖，不写入checkpoint，防止模型对象和工具闭包跨轮污染。"""

    model: Any
    store: TaskStore
    tools: dict[str, BaseTool]


def messages_for_model(messages: list) -> list:
    """为模型补充每条消息的原参考时间；checkpoint中的原始Human内容保持不变。"""
    prepared = [SystemMessage(content=INSTRUCTION)]
    for message in messages:
        if isinstance(message, HumanMessage) and message.additional_kwargs.get("reference_time"):
            reference = message.additional_kwargs["reference_time"]
            prepared.append(HumanMessage(
                content=f"[这条消息提供的原消息参考时间：{reference}]\n{message.content}", id=message.id,
            ))
        else:
            prepared.append(message)
    return prepared


def build_agent(checkpointer):
    """编译可长期复用的图；所有当前轮依赖由Runtime.context传入，不保存在共享闭包。"""

    async def call_model(state: AssistantState, runtime: Runtime[TurnContext]):
        """调用并校验当前轮模型，仅记录操作耗时，不把输入、响应或 Trace 依赖写入图状态。"""
        with span("agent.model"):
            context = runtime.context
            bound_model = context.model.bind_tools(list(context.tools.values()))
            response = await bound_model.ainvoke(messages_for_model(state["messages"]))
            if not isinstance(response, AIMessage) or response.invalid_tool_calls:
                raise ValueError("模型未返回有效的AI消息或工具参数")
        return {"messages": [response], "node_history": ["model"]}

    async def call_tools(state: AssistantState, runtime: Runtime[TurnContext]):
        """在线程池顺序执行工具，只对固定工具名和安全失败类型建立操作记录。"""
        results = []
        known_tools = {"save_task", "list_tasks", "complete_task", "analyze_notice", "interpret_time", "search_documents"}
        with span("agent.tools"):
            for call in state["messages"][-1].tool_calls:
                name = call.get("name")
                # 工具名可能来自模型，不允许任意字符串成为 Trace 名称。
                operation = "tool." + name if isinstance(name, str) and name in known_tools else "agent.tools"
                with span(operation) as current:
                    result = await asyncio.to_thread(execute_tool_call, call, registry=runtime.context.tools)
                    # 工具通常正常返回含 error 状态的 JSON；不抛异常也必须记录业务失败。
                    try:
                        payload = json.loads(result.content)
                        if isinstance(payload, dict) and payload.get("status") == "error":
                            current.fail("ToolExecutionError")
                    except (AttributeError, TypeError, ValueError):
                        # Trace 的检查不得改变既有工具响应或业务执行结果。
                        pass
                    results.append(result)
        return {"messages": results, "node_history": ["tools"]}

    async def report(state: AssistantState, runtime: Runtime[TurnContext]):
        """保留用户增加的统计与打印，并只为此报告操作记录耗时和安全状态。"""
        with span("agent.report"):
            tasks = await asyncio.to_thread(runtime.context.store.list_tasks)
            pending = await asyncio.to_thread(runtime.context.store.count_pending)
            print("[report] 本地待办数量:", len(tasks))
            print("[report] SQLite 未完成数量:", pending)
        return {"node_history": ["report"]}

    def route_after_model(state: AssistantState):
        """仅在最后一条AI消息提出工具调用时进入tools，否则结束当前轮。"""
        return "tools" if state["messages"][-1].tool_calls else END

    builder = StateGraph(AssistantState, context_schema=TurnContext)
    builder.add_node("model", call_model)
    builder.add_node("tools", call_tools)
    builder.add_node("report", report)
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", route_after_model, {"tools": "tools", END: END})
    builder.add_edge("tools", "report")
    builder.add_edge("report", "model")
    return builder.compile(checkpointer=checkpointer)
