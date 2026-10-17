"""界面的纯展示转换：保留消息原文、时间精度和真实来源，不访问网络或数据库。"""

from typing import Any


def event_badges(event: dict) -> list[dict[str, str]]:
    """将事项转为有颜色和文字的时间、截止及地点标签，不补造缺失钟点。"""
    value = event.get("time") or {}
    badges = []
    if value.get("time_kind") == "deadline":
        badges.append({"label": "截止", "color": "orange", "text": event.get("time_text") or value.get("time_text") or "待确认"})
    precision = value.get("precision")
    if precision == "minute" and value.get("at"):
        badges.append({"label": "具体时刻", "color": "blue", "text": value["at"]})
    elif precision == "minute_range" and value.get("start") and value.get("end"):
        badges.append({"label": "起止时段", "color": "blue", "text": value["start"] + " 至 " + value["end"]})
    elif precision == "date" and value.get("date"):
        badges.append({"label": "日期 · 钟点未知", "color": "gray", "text": value["date"]})
    elif precision == "date_range":
        badges.append({"label": "日期范围 · 钟点未知", "color": "green", "text": f"{value.get('start_date', '?')} 至 {value.get('end_date', '?')}"})
    elif event.get("time_text"):
        badges.append({"label": "时间待确认", "color": "gray", "text": event["time_text"]})
    if event.get("location"):
        badges.append({"label": "地点", "color": "violet", "text": event["location"]})
    return badges


def source_label(message: dict) -> str:
    """显示来源和发送者标识，昵称仅供阅读，不作为已确认的角色身份。"""
    source = message.get("source_message") or {}
    sender = source.get("sender_name") or source.get("sender_id") or "未知发送者"
    return f"{source.get('source', '未知来源')} · {sender} · {source.get('sent_at', '时间未知')}"


def citation_label(citation: dict) -> str:
    """根据后端提供的文件名和页码构造出处说明，缺失页码时不猜测。"""
    filename = citation.get("filename") or "未命名文件"
    page = citation.get("page")
    return f"{filename} · 第 {page} 页" if page is not None else filename


def trace_rows(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只选择追踪元数据列，父子编号用于识别执行关系，不生成消息正文。"""
    return [{
        "步骤": item.get("name"), "状态": item.get("status"),
        "耗时毫秒": item.get("elapsed_ms"), "错误类型": item.get("error_code"),
        "Span": item.get("span_id"), "父Span": item.get("parent_span_id"),
    } for item in spans]
