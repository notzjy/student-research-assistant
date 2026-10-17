"""业务消息协议：保留来源与完整原文，为结构化提取和用户角色配置定义边界。"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from student_assistant.bridge import lesson02
from student_assistant.schemas import ChatRequest
from student_assistant.event_time import EventTimeExtraction


Source = Literal["manual", "wechat", "qq", "boss", "qq_mail", "android"]
Category = Literal["研究", "学生生活", "求职", "其他"]


class StrictInput(BaseModel):
    """所有导入协议拒绝多余字段和隐式类型转换，便于在HTTP入口发现输入问题。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class AttachmentHint(StrictInput):
    """记录消息携带的附件线索；本模块不把文件名当作已下载的文件或已解析的正文。"""

    filename: str = Field(min_length=1, max_length=255, pattern=r"\S")
    media_type: str | None = Field(default=None, max_length=120)
    source_attachment_id: str | None = Field(default=None, min_length=1, max_length=200)
    availability: Literal["mentioned"] = "mentioned"


class MessageInput(StrictInput):
    """接收完整消息与来源身份；来源字段是导入方声明，不代表平台已经自动接通。"""

    source: Source
    account_id: str = Field(default="local", min_length=1, max_length=120, pattern=r"\S")
    source_message_id: str = Field(min_length=1, max_length=200, pattern=r"\S")
    sender_id: str = Field(min_length=1, max_length=200, pattern=r"\S")
    sender_name: str | None = Field(default=None, max_length=120)
    sent_at: str = Field(description="原消息发送时间，必须是带偏移的ISO时间")
    text: str = Field(min_length=1, max_length=5000, pattern=r"\S")
    attachments: list[AttachmentHint] = Field(default_factory=list, max_length=20)

    @field_validator("sent_at")
    @classmethod
    def validate_sent_at(cls, value: str) -> str:
        """复用聊天参考时间的ISO校验，并保留来源原始的时区偏移写法。"""
        return ChatRequest.validate_reference_time(value)


class RoleRule(StrictInput):
    """由用户配置来源、账号和发送者ID的精确匹配，昵称不参与重点角色判定。"""

    source: Source
    account_id: str = Field(default="local", min_length=1, max_length=120, pattern=r"\S")
    sender_id: str = Field(min_length=1, max_length=200, pattern=r"\S")
    role: str = Field(min_length=1, max_length=80, pattern=r"\S")
    important: bool = True


class ExtractedEvent(lesson02.Event):
    """为事项保留原文证据和时间意图，模型理解含义，日历日期交给程序计算。"""

    title: str = Field(min_length=1, max_length=120, pattern=r"\S")
    time_text: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, min_length=1, max_length=500)
    evidence: str = Field(min_length=1, max_length=2000, pattern=r"\S", description="原文中的连续证据片段，逐字摘录")
    time_extraction: EventTimeExtraction | None = Field(
        default=None, description="相对、绝对日期或起止时间意图；无时间则null，不支持的时间使用intent=null及reason说明",
    )

    @model_validator(mode="after")
    def validate_time_evidence(self) -> Self:
        """拒绝没有时间原话却带时间意图的结构，防止计算脱离原文。"""
        if self.time_text is None and self.time_extraction is not None:
            raise ValueError("时间意图必须有对应时间原话")
        return self


class MessageAnalysis(lesson02.NoticeAnalysis):
    """沿用已学的分类、摘要和附件字段，同时让每项事项附原文证据与结构化时间意图。"""

    summary: str = Field(min_length=1, max_length=1500, pattern=r"\S")
    events: list[ExtractedEvent] = Field(max_length=20)
