"""第十课：以原消息时间为基准，将有限的时间表达转换成北京时间或待确认结果。"""

import re
from datetime import datetime, timedelta, timezone
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# 本课只处理固定 UTC+08:00；跨地区和历史夏令时规则需后续引入完整时区支持。
BEIJING = timezone(timedelta(hours=8), name="UTC+08:00")
DAY_OFFSETS = {"今天": 0, "明天": 1, "后天": 2}
CHINESE_HOURS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}


def parse_reference_time(value: str) -> datetime:
    """读取带 Z 或 ±HH:MM 偏移的 ISO 时间；拒绝缺时区、非法日期及非字符串。"""
    pattern = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})"
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValueError("参考时间必须是带时区的 ISO 格式，例如 2026-10-15T20:00:00+08:00")
    # fromisoformat 同时检查真实日历；不能把带偏移的时间当作服务器本地时间。
    result = datetime.fromisoformat(value)
    offset_text = value[-6:]
    if value[-1] != "Z" and (int(offset_text[1:3]) > 23 or int(offset_text[4:6]) > 59):
        raise ValueError("时区偏移的小时或分钟不合法")
    return result


class TimeNormalizationRequest(BaseModel):
    """校验时间预览输入；消息发送时间必填，不允许用服务器当前时间替代。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    time_text: str = Field(min_length=1, max_length=200, pattern=r"\S")
    reference_time: str = Field(description="原消息发送时间，必须携带 Z 或 ±HH:MM 时区偏移")

    @field_validator("reference_time")
    @classmethod
    def validate_reference_time(cls, value: str) -> str:
        """在处理请求前检查参考时间，保留调用方给出的时间文本。"""
        parse_reference_time(value)
        return value


class NormalizedTime(BaseModel):
    """保留原文与参考时间，明确区分已解析时刻和仍需用户确认的表达。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    time_text: str = Field(min_length=1, max_length=200, pattern=r"\S")
    reference_time: str
    timezone: Literal["UTC+08:00"] = "UTC+08:00"
    status: Literal["resolved", "needs_confirmation"]
    time_kind: Literal["point", "deadline", "unknown"]
    at: str | None = None
    confirmation_question: str | None = None

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        """防止出现已解析却无时刻、待确认却带确定时刻等互相矛盾的结果。"""
        parse_reference_time(self.reference_time)
        if self.status == "resolved":
            if self.at is None or self.time_kind == "unknown" or self.confirmation_question is not None:
                raise ValueError("已解析结果必须有明确时刻和时间性质，且不能带确认问题")
            if parse_reference_time(self.at).utcoffset() != timedelta(hours=8):
                raise ValueError("本课已解析时刻必须使用 UTC+08:00")
        elif self.at is not None or not self.confirmation_question or not self.confirmation_question.strip():
            raise ValueError("待确认结果不能带确定时刻，且必须说明需要确认什么")
        return self


def normalize_time(time_text: str, reference_time: datetime) -> NormalizedTime:
    """按明确列出的规则解析整段时间表达；缺信息或不支持时保留原文并请求确认。"""
    if not isinstance(reference_time, datetime):
        raise ValueError("reference_time 必须是带时区的 datetime 对象")
    # CLI、HTTP 和直接 Python 调用共用输入约束；原文不因内部 strip 操作而丢失。
    request = TimeNormalizationRequest(time_text=time_text, reference_time=reference_time.isoformat())
    try:
        local_reference = reference_time.astimezone(BEIJING)
    except OverflowError:
        raise ValueError("参考时间转换到北京时间后超出可表示范围") from None
    text = time_text.strip()
    base = {"time_text": request.time_text, "reference_time": reference_time.isoformat()}

    # fullmatch 要求整段都符合规则，避免将否定句、时间区间或混合语句截成单个时刻。
    relative_clock = re.fullmatch(r"(今天|明天|后天)\s*([0-9]{1,2}):([0-9]{2})(前)?", text)
    relative_chinese = re.fullmatch(
        r"(今天|明天|后天)(上午|下午)(十一|十二|十|一|二|两|三|四|五|六|七|八|九|[0-9]{1,2})点(前)?", text
    )
    absolute_clock = re.fullmatch(r"([0-9]{4}-[0-9]{2}-[0-9]{2})[ T]([0-9]{1,2}):([0-9]{2})(前)?", text)

    try:
        if relative_clock is not None:
            day, hour, minute, before = relative_clock.groups()
            # 先转换到北京时间再加天；相对日期依据原消息，而不是 datetime.now()。
            target_day = local_reference + timedelta(days=DAY_OFFSETS[day])
            target = target_day.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
        elif relative_chinese is not None:
            day, period, hour_text, before = relative_chinese.groups()
            hour = int(hour_text) if hour_text.isascii() and hour_text.isdigit() else CHINESE_HOURS[hour_text]
            if not 1 <= hour <= 12:
                raise ValueError("中文钟点必须在一至十二点之间")
            if hour == 12:
                return NormalizedTime(
                    **base, status="needs_confirmation", time_kind="deadline" if before else "point",
                    confirmation_question="十二点可能涉及正午或午夜，请用24小时制确认，例如明天12:00或明天00:00。",
                )
            # 下午三点对应 15:00；本课只自动处理上午/下午一至十一点。
            hour += 12 if period == "下午" else 0
            target_day = local_reference + timedelta(days=DAY_OFFSETS[day])
            target = target_day.replace(hour=hour, minute=0, second=0, microsecond=0)
        elif absolute_clock is not None:
            date_text, hour, minute, before = absolute_clock.groups()
            target = datetime.fromisoformat(date_text).replace(hour=int(hour), minute=int(minute), tzinfo=BEIJING)
        else:
            # 完整日期即使缺少钟点也应检查日历，2月30日不能变成一个待确认的有效日期。
            date_without_clock = re.fullmatch(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(前)?", text)
            if date_without_clock is not None:
                datetime.fromisoformat(date_without_clock.group(1))
            # “周五前”缺少明确的日期和钟点；不偷偷补成周五23:59。
            vague_deadline = re.fullmatch(r"(?:(?:本|下)?周[一二三四五六日天]|今天|明天|后天|[0-9]{4}-[0-9]{2}-[0-9]{2})前", text)
            return NormalizedTime(
                **base, status="needs_confirmation", time_kind="deadline" if vague_deadline else "unknown",
                confirmation_question=(
                    "请确认具体哪一天、几点截止，例如2026-10-23 18:00前；不会自动补成当天结束。"
                    if vague_deadline else
                    "这段表达缺少明确时刻或超出本课规则，请提供完整日期和24小时制时间，例如2026-10-20 15:00；区间需分别确认起止时间。"
                ),
            )
    except (ValueError, OverflowError):
        # 已识别的格式中出现2月30日或25:00属于非法输入，不能包装为有效时刻。
        raise ValueError("时间表达中的日期或钟点不合法，或超出可表示范围") from None

    return NormalizedTime(
        **base, status="resolved", time_kind="deadline" if before else "point", at=target.isoformat()
    )
