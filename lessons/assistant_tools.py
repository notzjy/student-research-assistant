"""第七至九课：把注入的待办存储绑定为 Agent 的保存、查询与完成工具。"""

import importlib
from typing import Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field

from task_store import TaskStore


lesson03 = importlib.import_module("03_tool_calling")


class ListTasksInput(BaseModel):
    """限制查询工具的筛选参数，拒绝模型传入未知状态或额外字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["pending", "completed", "all"] = Field(
        default="pending", description="pending查未完成，completed查已完成，all查全部待办"
    )


class CompleteTaskInput(BaseModel):
    """要求模型提交查询所得的任务 ID，拒绝空白、非字符串及额外字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    # 旧 JSON 导入的 ID 不一定是 16 位哈希，因此这里只校验非空字符串。
    task_id: str = Field(min_length=1, pattern=r"\S", description="本轮 list_tasks 返回的目标待办 id")


def create_task_tools(store: TaskStore, *, queried_ids: set[str] | None = None) -> dict[str, BaseTool]:
    """绑定存储并创建工具表；可传入当前用户轮次的查询证据，用于恢复未完成执行。"""
    # 旧课程每张表使用新集合；主服务只在恢复同一轮时传入已持久化查询结果中的ID。
    if queried_ids is None:
        queried_ids = set()

    @tool("save_task", args_schema=lesson03.SaveTaskInput)
    def save_task(title: str, time_text: str | None = None, location: str | None = None) -> dict:
        """保存一项明确待办，保留原文时间与地点；返回实际保存结果，不设置提醒。"""
        # 内部函数保留外层传入的 store；模型只需要提供事项字段，不选择数据库路径。
        return store.save_task(title=title, time_text=time_text, location=location)

    @tool("list_tasks", args_schema=ListTasksInput)
    def list_tasks(status: Literal["pending", "completed", "all"] = "pending") -> dict:
        """查询已经保存的待办：可筛选未完成、已完成或全部；只读取记录，不修改任务状态。"""
        # 工具参数使用易理解的 all；存储方法用 None 表示不按状态筛选。
        tasks = store.list_tasks(status=None if status == "all" else status)
        # 查询成功后才记下 ID；完成工具不能凭模型猜出的 ID 直接修改记录。
        queried_ids.update(task["id"] for task in tasks)
        return {"status": "queried", "filter_status": status, "count": len(tasks), "tasks": tasks}

    @tool("complete_task", args_schema=CompleteTaskInput)
    def complete_task(task_id: str) -> dict:
        """将用户明确指定的待办标为已完成；先用 list_tasks 查询，传入其返回的真实 id。"""
        if task_id not in queried_ids:
            return {"status": "error", "error": "请先调用 list_tasks 查询目标待办，再使用返回的真实 id。"}
        # 存储方法只更新指定 ID；已完成记录重复调用仍保持 completed。
        task = store.mark_completed(task_id)
        if task is None:
            return {"status": "error", "error": "目标待办已不存在，未修改任何任务。"}
        return {"status": "completed", "task": task}

    # 模型绑定与实际执行共用此表；只定义函数而不登记，模型仍无法使用新工具。
    return {save_task.name: save_task, list_tasks.name: list_tasks, complete_task.name: complete_task}
