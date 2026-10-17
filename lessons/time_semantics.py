"""第十一课：模型提取相对时间意图，程序计算日历，并用不同模型保留时间精度。"""

import calendar
import importlib
import json
import re
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from time_parser import BEIJING, TimeNormalizationRequest, parse_reference_time


class RelativeTimeIntent(BaseModel):
    """描述相对日、周、月、年的计算参数；模型只识别含义，不直接填写计算后的日期。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    unit: Literal["day", "week", "month", "year"]
    offset: int = Field(default=0, ge=-120, le=120)
    weekday: int | None = Field(default=None, ge=0, le=6)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    is_deadline: bool = False

    @model_validator(mode="after")
    def validate_combination(self) -> Self:
        """限制星期和钟点所属单位，防止从一整个月或一周凭空选出某一天。"""
        if self.weekday is not None and self.unit != "week":
            raise ValueError("weekday 只能用于 week")
        if (self.hour is None) != (self.minute is None):
            raise ValueError("hour 和 minute 必须成对提供或同时为空")
        is_date = self.unit == "day" or (self.unit == "week" and self.weekday is not None)
        if not is_date and (self.hour is not None or self.is_deadline):
            raise ValueError("未定位到具体日期的范围不能携带钟点或明确截止标记")
        return self


class TimeExtraction(BaseModel):
    """允许模型明确表示无法识别；不迫使缺信息、否定或复杂表达生成虚假的计算参数。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    intent: RelativeTimeIntent | None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_uncertainty(self) -> Self:
        """识别成功时只返回意图；无法识别时必须说明需要澄清或不支持的原因。"""
        if self.intent is None:
            if not self.reason or not self.reason.strip():
                raise ValueError("无法识别时必须提供非空 reason")
        elif self.reason is not None:
            raise ValueError("明确意图与未确定原因不能同时返回")
        return self


def validate_iso_date(value: str) -> str:
    """校验 YYYY-MM-DD 的日期文本和真实日历，保留日期精度而不补零点。"""
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("日期必须使用 YYYY-MM-DD")
    date.fromisoformat(value)
    return value


class TimeResultBase(BaseModel):
    """为所有精度结果保留时间原话、原参考字符串和本课固定业务时区。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    time_text: str = Field(min_length=1, max_length=200, pattern=r"\S")
    reference_time: str
    timezone: Literal["UTC+08:00"] = "UTC+08:00"

    @field_validator("reference_time")
    @classmethod
    def validate_reference(cls, value: str) -> str:
        """检查参考字符串的时区和日历，保留原输入中的 Z 或时区偏移写法。"""
        parse_reference_time(value)
        return value


class ExactTimeResult(TimeResultBase):
    """表达精确到分钟的时刻或截止边界；at 是后续可计算的时间。"""

    precision: Literal["minute"] = "minute"
    status: Literal["resolved"] = "resolved"
    time_kind: Literal["point", "deadline"]
    at: str

    @field_validator("at")
    @classmethod
    def validate_exact_time(cls, value: str) -> str:
        """要求确定时刻使用 UTC+08:00 且秒与微秒为零，符合分钟精度。"""
        parsed = parse_reference_time(value)
        if parsed.utcoffset() != timedelta(hours=8) or parsed.second or parsed.microsecond:
            raise ValueError("分钟精度时刻须使用 UTC+08:00，且秒和微秒为零")
        return value


class DateOnlyResult(TimeResultBase):
    """表达已确定的日期；没有 at 字段，因此不能把缺少钟点误写成零点。"""

    precision: Literal["date"] = "date"
    status: Literal["resolved"] = "resolved"
    time_kind: Literal["point", "deadline"]
    date: str

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        """确保日期是真实的 YYYY-MM-DD，保持日期级含义。"""
        return validate_iso_date(value)


class DateRangeResult(TimeResultBase):
    """表达自然周、自然月或自然年的日期范围，包含起止日期，不代表起止钟点。"""

    precision: Literal["date_range"] = "date_range"
    status: Literal["resolved"] = "resolved"
    time_kind: Literal["period"] = "period"
    start_date: str
    end_date: str

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        """检查范围两端的真实日期和先后关系，避免终点早于起点。"""
        validate_iso_date(self.start_date)
        validate_iso_date(self.end_date)
        if self.start_date > self.end_date:
            raise ValueError("日期范围终点不能早于起点")
        return self


class UnresolvedTimeResult(TimeResultBase):
    """表达当前无法唯一识别的时间，不携带假设出来的日期或钟点。"""

    precision: Literal["unresolved"] = "unresolved"
    status: Literal["needs_confirmation"] = "needs_confirmation"
    time_kind: Literal["unknown"] = "unknown"
    confirmation_question: str = Field(min_length=1, pattern=r"\S")


# Pydantic 按 precision 选择唯一模型；日期、时刻和范围不能混用字段。
TimeInterpretation = Annotated[
    ExactTimeResult | DateOnlyResult | DateRangeResult | UnresolvedTimeResult,
    Field(discriminator="precision"),
]


def build_time_messages(request: TimeNormalizationRequest) -> list[tuple[str, str]]:
    """向模型提供原文、原消息时间、用户星期政策和意图 schema，不让模型直接计算日期。"""
    schema = json.dumps(TimeExtraction.model_json_schema(), ensure_ascii=False)
    instruction = (
        "你负责识别相对时间的结构，只输出符合schema的JSON，不计算最终年月日。"
        "原消息时间是reference_time；业务时区固定北京时间UTC+08。"
        "unit=day表示相对日期：今天offset=0、昨天=-1、明天=1、后天=2。"
        "unit=week表示自然周，周一至周日；这周offset=0，上周=-1，下周=1。"
        "周几使用weekday，周一=0，周三=2，周日=6；纯一周范围weekday=null。"
        "用户规定裸周三/星期三等属于原消息所在本周，offset=0，即使已过去也不顺延下周。"
        "unit=month表示整自然月：这个月offset=0、下个月=1、上个月=-1。"
        "unit=year表示整自然年：今年offset=0、明年=1、去年=-1。"
        "钟点必须原文明确；15:00或下午三点为hour=15,minute=0，没给钟点两项均null。"
        "只有day或week明确weekday时允许钟点；明确日期前的截止表达is_deadline=true。"
        "例如周三前为week/offset=0/weekday=2/无钟点/is_deadline=true；它只确定截止日期。"
        "自然月/年/纯周范围不能带钟点或截止标记。offset必须在-120到120内。"
        "能唯一识别时intent为结构且reason=null；不能识别时intent=null并提供中文reason。"
        "本课不支持完整绝对日期、多个时间、复杂起止区间、日期缺失的范围截止、否定/纠正后仍不明确的表达；"
        "这些情况返回intent=null，不能强行截取其中一段。"
        "例如不是周三没有给出真实日期，应返回intent=null并询问实际时间。"
        "\nJSON Schema:\n" + schema
    )
    return [("system", instruction), ("human", json.dumps(request.model_dump(), ensure_ascii=False))]


def extract_time_intent(request: TimeNormalizationRequest, model=None) -> TimeExtraction:
    """复用已有模型客户端，用 JSON 模式和 Pydantic 提取意图；导入模块时不创建模型。"""
    if model is None:
        model = importlib.import_module("03_tool_calling").create_model()
    structured_model = model.with_structured_output(TimeExtraction, method="json_mode")
    # JSON模式需要显式把schema放进Prompt；结构校验不代表语义一定正确。
    extracted = structured_model.invoke(build_time_messages(request))
    return TimeExtraction.model_validate(extracted)


def resolve_intent(request: TimeNormalizationRequest, extraction: TimeExtraction) -> TimeInterpretation:
    """应用用户规则并按意图计算日期或范围；不调用模型、写数据库或创建提醒。"""
    request = TimeNormalizationRequest.model_validate(request)
    extraction = TimeExtraction.model_validate(extraction)
    base = request.model_dump()
    intent = extraction.intent
    # 这里只硬性保护用户确认过的裸星期政策，不接管其他中文表达的识别。
    bare_weekday = re.fullmatch(r"(?:周|星期)([一二三四五六日天])(前)?", request.time_text.strip())
    if bare_weekday is not None:
        weekdays = {name: index for index, name in enumerate("一二三四五六日")}
        weekdays["天"] = 6
        intent = RelativeTimeIntent(
            unit="week", offset=0, weekday=weekdays[bare_weekday.group(1)],
            is_deadline=bare_weekday.group(2) is not None,
        )
    if intent is None:
        return UnresolvedTimeResult(**base, confirmation_question=extraction.reason)

    try:
        reference = parse_reference_time(request.reference_time).astimezone(BEIJING)
        current_date = reference.date()
        if intent.unit == "day":
            target_date = current_date + timedelta(days=intent.offset)
        elif intent.unit == "week":
            # 同一公式处理所有星期；差值可为负，不自动把过去日期挪到下一周。
            week_start = current_date + timedelta(days=7 * intent.offset - current_date.weekday())
            if intent.weekday is None:
                return DateRangeResult(
                    **base, start_date=week_start.isoformat(), end_date=(week_start + timedelta(days=6)).isoformat()
                )
            target_date = week_start + timedelta(days=intent.weekday)
        elif intent.unit == "month":
            # 月份索引运算会自然处理跨年；月份不能简单当成30天。
            month_index = (current_date.year - 1) * 12 + current_date.month - 1 + intent.offset
            year_index, month_index = divmod(month_index, 12)
            year, month = year_index + 1, month_index + 1
            return DateRangeResult(
                **base, start_date=date(year, month, 1).isoformat(),
                end_date=date(year, month, calendar.monthrange(year, month)[1]).isoformat(),
            )
        else:
            year = current_date.year + intent.offset
            return DateRangeResult(
                **base, start_date=date(year, 1, 1).isoformat(), end_date=date(year, 12, 31).isoformat()
            )

        kind = "deadline" if intent.is_deadline else "point"
        if intent.hour is None:
            return DateOnlyResult(**base, time_kind=kind, date=target_date.isoformat())
        target = datetime.combine(target_date, time(intent.hour, intent.minute), tzinfo=BEIJING)
        return ExactTimeResult(**base, time_kind=kind, at=target.isoformat())
    except (ValueError, OverflowError):
        raise ValueError("参考时间或计算结果超出有效日历范围") from None


def interpret_time(request: TimeNormalizationRequest, model=None) -> TimeInterpretation:
    """串联模型意图识别与程序日历计算，返回保留精度的结果供CLI和HTTP共用。"""
    extraction = extract_time_intent(request, model=model)
    return resolve_intent(request, extraction)
