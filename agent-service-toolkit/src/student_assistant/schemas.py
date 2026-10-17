"""个人助理主服务的请求与响应协议，保留原始消息并明确线程恢复所需字段。"""

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


THREAD_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
REFERENCE_TIME_PATTERN = (
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


class ChatRequest(BaseModel):
    """接收完整消息和可选线程、原消息时间；不剥离原文中的首尾空白。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    message: str = Field(min_length=1, max_length=5000, pattern=r"\S", description="用户消息或完整通知原文")
    thread_id: str | None = Field(default=None, pattern=THREAD_ID_PATTERN)
    reference_time: str | None = Field(
        default=None, description="原消息时间，使用带 Z 或 ±HH:MM 偏移的 ISO 格式"
    )

    @field_validator("reference_time")
    @classmethod
    def validate_reference_time(cls, value: str | None) -> str | None:
        """检查参考时间的日历与偏移是否合法，并保留调用方提供的原始字符串。"""
        if value is None:
            return value
        if re.fullmatch(REFERENCE_TIME_PATTERN, value) is None:
            raise ValueError("reference_time 必须是携带时区偏移的 ISO 时间")
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("reference_time 必须携带时区偏移")
        # fromisoformat 可能规范化偏移分钟，额外检查原始写法，避免接受 +08:99。
        if not value.endswith("Z") and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError("reference_time 的时区偏移不合法")
        return value


class ChatResponse(BaseModel):
    """返回本轮回复、真实工具结果和线程标识，不将旧轮工具结果混入本轮。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    thread_id: str
    turn_id: str
    reply: str
    execution_status: Literal["completed", "tool_error", "no_tools"]
    node_order: list[str]
    tool_names: list[str]
    tool_results: list[dict[str, Any]]
    history_count: int = Field(ge=0)


class ThreadHistory(BaseModel):
    """提供已保存的完整线程消息及待恢复节点，不调用模型补写历史。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    thread_id: str
    messages: list[dict[str, Any]]
    pending: bool
    next_nodes: list[str]


class TaskRecord(BaseModel):
    """描述正式待办的有效状态，明确区分已取消与已完成，不与checkpoint混用。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    title: str
    time_text: str | None = None
    location: str | None = None
    status: Literal["pending", "completed", "cancelled"]


class TasksResponse(BaseModel):
    """返回业务待办列表与数量，供查询和操作结果核对使用。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    count: int = Field(ge=0)
    tasks: list[TaskRecord]
