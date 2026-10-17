"""正式消息的时间协议：兼容相对日期，并保留绝对日期和起止区间的真实精度。"""

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from student_assistant.bridge import TimeNormalizationRequest, parse_reference_time
from time_parser import BEIJING
from time_semantics import (
    DateOnlyResult, DateRangeResult, ExactTimeResult, RelativeTimeIntent,
    TimeExtraction, TimeResultBase, UnresolvedTimeResult, resolve_intent,
)


class AbsoluteTimeIntent(BaseModel):
    """提取原文明确的月日和可选年份、钟点；省略年份时使用原消息的北京时间年份。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["absolute"] = "absolute"
    year: int | None = Field(default=None, ge=1, le=9999)
    year_offset: int = Field(default=0, ge=-120, le=120, description="只有今年/明年等相对年份时使用；明确年份时必须为0")
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    is_deadline: bool = False
    timezone_offset: str | None = Field(default=None, description="仅原文明确给出UTC偏移时填写±HH:MM；未注明则按北京时间")

    @model_validator(mode="after")
    def validate_fields(self) -> Self:
        """要求钟点成对、年份表达唯一，且仅完整时刻可以携带显式时区偏移。"""
        if (self.hour is None) != (self.minute is None):
            raise ValueError("hour和minute必须同时提供或同时为空")
        if self.year is not None and self.year_offset:
            raise ValueError("明确年份不能同时携带相对年份")
        if self.timezone_offset is not None:
            if self.hour is None:
                raise ValueError("日期级表达不能携带时区偏移")
            if re.fullmatch(r"[+-](?:0[0-9]|1[0-4]):[0-5][0-9]", self.timezone_offset) is None:
                raise ValueError("时区偏移须为±HH:MM且不超过14小时")
            if self.timezone_offset[1:3] == "14" and self.timezone_offset[4:] != "00":
                raise ValueError("14小时偏移的分钟必须为00")
        return self


class TimeRangeIntent(BaseModel):
    """保留两个明确边界；两端都须定位到单日，不能拿整月或整周范围当作端点。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["range"] = "range"
    start: RelativeTimeIntent | AbsoluteTimeIntent
    end: RelativeTimeIntent | AbsoluteTimeIntent

    @model_validator(mode="after")
    def validate_endpoints(self) -> Self:
        """拒绝范围套范围和端点截止标记，避免产生互相冲突的时间性质。"""
        for endpoint in (self.start, self.end):
            if isinstance(endpoint, RelativeTimeIntent):
                if endpoint.unit != "day" and not (endpoint.unit == "week" and endpoint.weekday is not None):
                    raise ValueError("起止端点必须定位到单日")
            if endpoint.is_deadline:
                raise ValueError("起止端点不应单独标为截止")
        return self


class EventTimeExtraction(TimeExtraction):
    """扩展正式消息时间提取，同时完整接受旧unit/offset结构及旧TimeExtraction对象。"""

    intent: RelativeTimeIntent | AbsoluteTimeIntent | TimeRangeIntent | None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_extraction(cls, value):
        """将已有教学模型对象转成字典，保证正式工作流升级不要求调用方重写旧意图。"""
        if isinstance(value, TimeExtraction) and not isinstance(value, cls):
            return value.model_dump()
        return value


class MinuteRangeResult(TimeResultBase):
    """表达两个均精确到分钟的起止时刻，不把一个区间压缩成单个at。"""

    precision: Literal["minute_range"] = "minute_range"
    status: Literal["resolved"] = "resolved"
    time_kind: Literal["period"] = "period"
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def validate_boundary(cls, value: str) -> str:
        """保证每个边界都带北京时间偏移且精确到分钟，避免无时区字符串参与排序。"""
        parsed = parse_reference_time(value)
        if parsed.utcoffset() != timedelta(hours=8) or parsed.second or parsed.microsecond:
            raise ValueError("分钟区间边界必须使用UTC+08:00且秒和微秒为零")
        return value

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        """要求终点晚于起点；零时长或倒置区间需要用户澄清。"""
        if parse_reference_time(self.end) <= parse_reference_time(self.start):
            raise ValueError("区间终点必须晚于起点")
        return self


EventTimeInterpretation = Annotated[
    ExactTimeResult | DateOnlyResult | DateRangeResult | MinuteRangeResult | UnresolvedTimeResult,
    Field(discriminator="precision"),
]


def _resolve_absolute(request: TimeNormalizationRequest, intent: AbsoluteTimeIntent):
    """按原消息北京时间补足省略年份，再校验真实日历；只有明确钟点才生成at。"""
    reference = parse_reference_time(request.reference_time).astimezone(BEIJING)
    year = intent.year if intent.year is not None else reference.year + intent.year_offset
    target_date = date(year, intent.month, intent.day)
    base = request.model_dump()
    kind = "deadline" if intent.is_deadline else "point"
    if intent.hour is None:
        return DateOnlyResult(**base, time_kind=kind, date=target_date.isoformat())
    source_zone = BEIJING
    if intent.timezone_offset is not None:
        sign = -1 if intent.timezone_offset.startswith("-") else 1
        hours, minutes = map(int, intent.timezone_offset[1:].split(":"))
        source_zone = timezone(sign * timedelta(hours=hours, minutes=minutes))
    target = datetime.combine(target_date, time(intent.hour, intent.minute), tzinfo=source_zone).astimezone(BEIJING)
    return ExactTimeResult(**base, time_kind=kind, at=target.isoformat())


def _resolve_endpoint(request: TimeNormalizationRequest, intent: RelativeTimeIntent | AbsoluteTimeIntent):
    """解析区间端点；相对端点各自使用同一原消息时间，不按首端点继续累加。"""
    if isinstance(intent, AbsoluteTimeIntent):
        return _resolve_absolute(request, intent)
    # 区间整体原话不是裸星期，旧解析器仍会遵守weekday和offset的自然周计算。
    return resolve_intent(request, TimeExtraction(intent=intent))


def resolve_event_time(
    request: TimeNormalizationRequest, extraction: EventTimeExtraction | TimeExtraction | dict,
) -> EventTimeInterpretation:
    """仅为正式消息解析扩展时间；非法日历、倒置或精度混杂的区间保留原话并待确认。"""
    request = TimeNormalizationRequest.model_validate(request)
    extraction = EventTimeExtraction.model_validate(extraction)
    base = request.model_dump()
    intent = extraction.intent
    # 用户已明确裸周几属于消息所在自然周；模型偶尔改成绝对日期也不能绕过这条规则。
    if re.fullmatch(r"(?:周|星期)[一二三四五六日天](?:前)?", request.time_text.strip()):
        return resolve_intent(request, TimeExtraction(intent=None, reason="请确认时间"))
    if intent is None:
        return UnresolvedTimeResult(**base, confirmation_question=extraction.reason)
    try:
        if isinstance(intent, RelativeTimeIntent):
            return resolve_intent(request, TimeExtraction(intent=intent))
        if isinstance(intent, AbsoluteTimeIntent):
            return _resolve_absolute(request, intent)
        start = _resolve_endpoint(request, intent.start)
        end = _resolve_endpoint(request, intent.end)
        if isinstance(start, DateOnlyResult) and isinstance(end, DateOnlyResult):
            return DateRangeResult(**base, start_date=start.date, end_date=end.date)
        if isinstance(start, ExactTimeResult) and isinstance(end, ExactTimeResult):
            return MinuteRangeResult(**base, start=start.at, end=end.at)
        return UnresolvedTimeResult(
            **base, confirmation_question="起止时间的精度不一致，请确认两端的日期和钟点；不会补造午夜。",
        )
    except (ValueError, OverflowError):
        # 不自动交换起止、不把2月30日挪到3月，也不把晚于终点的时间猜成跨午夜。
        return UnresolvedTimeResult(
            **base, confirmation_question="日期或起止顺序不合法，请确认真实日期和起止时间。",
        )
