"""提醒 API 协议：事项时间与用户设置的提醒时刻分别保存。"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from student_assistant.bridge import parse_reference_time


class ReminderCreate(BaseModel):
    """为已有未完成事项创建一个带明确时区的提醒，不猜测日期对应的钟点。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    task_id: str = Field(min_length=1, max_length=128)
    remind_at: str
    channel: Literal["console", "windows"] = "console"

    @field_validator("remind_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        """复用项目 ISO 时间校验，阻止缺少时区或日历不合法的计划入库。"""
        parse_reference_time(value)
        return value


class ReminderUpdate(BaseModel):
    """编辑时携带刚读取的版本，避免旧页面覆盖其他请求更新后的计划。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    remind_at: str
    channel: Literal["console", "windows"] = "console"
    expected_version: int = Field(ge=1)

    @field_validator("remind_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        """检查新的提醒时刻，保留调用方提供的时区表示。"""
        parse_reference_time(value)
        return value


class ReminderPolicy(BaseModel):
    """用户主动启用的自动计划规则；修改规则不会改写已创建的提醒。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    lead_minutes: int = Field(default=30, ge=0, le=10080)
    date_only_time: str | None = None
    channel: Literal["console", "windows"] = "console"

    @field_validator("date_only_time")
    @classmethod
    def validate_clock(cls, value: str | None) -> str | None:
        """日期型事项只能使用用户明确设置的 HH:MM 提醒钟点。"""
        if value is not None and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is None:
            raise ValueError("date_only_time 必须为 HH:MM 或 null")
        return value


class ReminderPlanRequest(BaseModel):
    """显式为一条旧消息应用当前规则，便于用户选择性补建提醒。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    message_id: str = Field(min_length=1, max_length=128)
