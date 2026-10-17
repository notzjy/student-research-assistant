"""为当前用户轮次建立独立工具表，组合待办、通知分析与时间解释能力。"""

from typing import Literal

from langchain_core.exceptions import OutputParserException
from langchain_core.tools import BaseTool, tool
from openai import APIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from student_assistant.bridge import (
    TaskStore, TimeNormalizationRequest, create_task_tools, interpret_time,
    lesson02, parse_reference_time,
)
from student_assistant.business_tasks import TaskCancelledError


class NoticeInput(BaseModel):
    """限制通知分析的输入，只接收完整原文，不把收到附件视作已取得文件。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=5000, pattern=r"\S")


class BusinessListTasksInput(BaseModel):
    """正式查询工具允许单独查询取消事项，旧课程的参数模型保持不变。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["pending", "completed", "cancelled", "all"] = Field(
        default="pending", description="pending查待办，completed查已完成，cancelled查已取消，all查全部",
    )


def build_turn_tools(
    store: TaskStore, model, reference_times: list[str], *, queried_ids: set[str] | None = None,
) -> dict[str, BaseTool]:
    """绑定当前轮次模型和存储；来源参考时间只允许取自本线程已提供的消息元数据。"""
    if queried_ids is None:
        queried_ids = set()
    registry = create_task_tools(store, queried_ids=queried_ids)

    @tool("list_tasks", args_schema=BusinessListTasksInput)
    def list_tasks(status: Literal["pending", "completed", "cancelled", "all"] = "pending") -> dict:
        """查询正式待办，分别筛选待办、已完成、已取消或全部，取消事项不算已完成。"""
        tasks = store.list_tasks(status=None if status == "all" else status)
        # 完成工具复用这一个集合，不能仅扩展查询schema而丢失本轮查询证据。
        queried_ids.update(task["id"] for task in tasks)
        return {"status": "queried", "filter_status": status, "count": len(tasks), "tasks": tasks}

    @tool("complete_task", args_schema=registry["complete_task"].args_schema)
    def complete_task(task_id: str) -> dict:
        """完成本轮已查询的明确事项；已取消事项必须先由用户重新开启。"""
        if task_id not in queried_ids:
            return {"status": "error", "error": "请先调用 list_tasks 查询目标待办，再使用返回的真实 id。"}
        try:
            task = store.mark_completed(task_id)
        except TaskCancelledError:
            return {"status": "error", "error": "该事项已取消，不能标记已完成；请先明确重新开启。"}
        if task is None:
            return {"status": "error", "error": "目标待办已不存在，未修改任何任务。"}
        return {"status": "completed", "task": task}

    registry[list_tasks.name] = list_tasks
    registry[complete_task.name] = complete_task

    @tool("analyze_notice", args_schema=NoticeInput)
    def analyze_notice(text: str) -> dict:
        """分析完整通知，提取类别、摘要、事项和附件描述；只分析，不保存事项或下载文件。"""
        try:
            structured = model.with_structured_output(lesson02.NoticeAnalysis, method="json_mode")
            result = structured.invoke(lesson02.build_messages(text))
            analysis = lesson02.NoticeAnalysis.model_validate(result)
            return {"status": "analyzed", "analysis": analysis.model_dump()}
        except (APIError, OutputParserException, ValidationError) as error:
            return {"status": "error", "error": f"通知分析未完成：{type(error).__name__}"}

    @tool("interpret_time", args_schema=TimeNormalizationRequest)
    def interpret_time_tool(time_text: str, reference_time: str) -> dict:
        """解释时间原话，返回日期、精确时刻或范围；参考时间必须来自本线程消息已提供的时间。"""
        # Z与+00:00可表达同一时刻；仍使用消息原先的参考字符串计算和回传。
        supplied = parse_reference_time(reference_time)
        source_reference = next(
            (value for value in reference_times if parse_reference_time(value) == supplied), None
        )
        if source_reference is None:
            return {"status": "error", "error": "请提供原消息发送时间，或使用本线程消息已提供的参考时间，不能自行猜测。"}
        try:
            request = TimeNormalizationRequest(time_text=time_text, reference_time=source_reference)
            result = interpret_time(request, model=model)
            return {"status": "interpreted", "result": result.model_dump()}
        except (APIError, OutputParserException, ValidationError) as error:
            return {"status": "error", "error": f"时间解释未完成：{type(error).__name__}"}

    registry[analyze_notice.name] = analyze_notice
    registry[interpret_time_tool.name] = interpret_time_tool
    return registry
