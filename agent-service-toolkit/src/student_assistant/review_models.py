"""人工纠正协议：保留消息原文，用版本号校验可编辑的展示与事项字段。"""

from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, field_validator, model_validator

from student_assistant.message_models import Category, StrictInput
from student_assistant.event_time import EventTimeInterpretation


Label = Annotated[str, Field(min_length=1, max_length=30, pattern=r"\S")]
TIME_ADAPTER = TypeAdapter(EventTimeInterpretation)


class MessageReview(StrictInput):
    """提交消息分类、多个标签和摘要的局部修订；不允许修改来源或原文。"""

    expected_version: int = Field(default=0, ge=0)
    category: Category | None = None
    labels: list[Label] | None = Field(default=None, max_length=10)
    summary: str | None = Field(default=None, min_length=1, max_length=1500, pattern=r"\S")

    @field_validator("labels")
    @classmethod
    def clean_labels(cls, value: list[str] | None) -> list[str] | None:
        """去掉标签两端空白并稳定去重，拒绝全空白标签。"""
        if value is None:
            return None
        cleaned = list(dict.fromkeys(item.strip() for item in value))
        if any(not item for item in cleaned):
            raise ValueError("标签不能为空白")
        return cleaned

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        """要求至少修改一个业务字段，明确清空标签应传空列表而非 null。"""
        changed = self.model_fields_set - {"expected_version"}
        if not changed or any(getattr(self, key) is None for key in changed):
            raise ValueError("至少提交一个非空修订字段；清空标签请使用空列表")
        return self


class DocumentReview(MessageReview):
    """人工纠正文档分类与标签；不覆盖文件原件、模型证据或索引。"""


class EventReview(StrictInput):
    """纠正已有事项的标题、地点、时间或状态，不改模型最初提取的证据。"""

    expected_version: int = Field(default=0, ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"\S")
    location: str | None = Field(default=None, min_length=1, max_length=500, pattern=r"\S")
    time_text: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S")
    time: dict | None = None
    status: Literal["pending", "completed", "cancelled"] | None = None

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: dict | None) -> dict | None:
        """复用正式时间联合模型校验精度、日期、时区与范围，不补缺失钟点。"""
        return None if value is None else TIME_ADAPTER.validate_python(value).model_dump(mode="json")

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        """拒绝空修订和无法表达有效事项的空标题或空状态。"""
        changed = self.model_fields_set - {"expected_version"}
        if not changed:
            raise ValueError("至少提交一个事项修订字段")
        if ("title" in changed and self.title is None) or ("status" in changed and self.status is None):
            raise ValueError("事项标题和状态不能清空")
        return self


class ClassificationEvidence(StrictInput):
    """保存正文页码与连续逐字引用，后续再与真实提取文本核验。"""

    page: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=500, pattern=r"\S")


class DocumentClassification(StrictInput):
    """模型依据文件正文给出的类别、标签、摘要和可核对证据。"""

    category: Category
    labels: list[Label] = Field(max_length=10)
    summary: str = Field(min_length=1, max_length=1500, pattern=r"\S")
    evidence: list[ClassificationEvidence] = Field(min_length=1, max_length=10)

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, value: list[str]) -> list[str]:
        """让模型标签沿用人工标签的空白与去重规则。"""
        return MessageReview.clean_labels(value)
