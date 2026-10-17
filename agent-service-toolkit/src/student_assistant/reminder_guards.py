"""提醒时间门控：原消息时间负责解释含义，当前时间只负责判断是否仍适合自动提醒。"""

from datetime import date, datetime, timedelta, timezone

from student_assistant.bridge import parse_reference_time


BEIJING = timezone(timedelta(hours=8))


def event_expiration_reason(value: dict | None, now: datetime) -> str | None:
    """判断已解析事项是否已经过去；日期按北京时间比较，日期精度不补造钟点。"""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("当前时间必须是带时区的datetime")
    if not value or value.get("status") != "resolved":
        return None
    precision = value.get("precision")
    if precision == "minute" and value.get("at"):
        expired = parse_reference_time(value["at"]) <= now
    elif precision == "minute_range" and value.get("end"):
        expired = parse_reference_time(value["end"]) <= now
    elif precision == "date" and value.get("date"):
        # 今天仍是今天，不把“只有日期”解释为该日零点已过期。
        expired = date.fromisoformat(value["date"]) < now.astimezone(BEIJING).date()
    elif precision == "date_range" and value.get("end_date"):
        expired = date.fromisoformat(value["end_date"]) < now.astimezone(BEIJING).date()
    else:
        return None
    return "source_event_expired" if expired else None


def utc_now() -> datetime:
    """提供默认的当前UTC时刻，测试可注入固定时钟以复现过期与跨日边界。"""
    return datetime.now(timezone.utc)
