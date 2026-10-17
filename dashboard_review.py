"""人工纠正与原件操作界面：所有修改、上传与模型分类均由明确按钮触发。"""

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote

import streamlit as st

from dashboard_api import DashboardError


CATEGORIES = ("研究", "学生生活", "求职", "其他")
STATUS_LABELS = {"待办": "pending", "已完成": "completed", "已取消": "cancelled"}
TIME_MODES = ("保留现有", "仅日期", "具体时刻", "日期范围", "起止时段", "清空", "高级JSON")
BEIJING = timezone(timedelta(hours=8))


def show_review_error(error: DashboardError) -> None:
    """只展示适配器提供的安全错误，附追踪编号方便查询失败原因。"""
    st.error(str(error))
    if error.trace_id:
        st.caption("Trace ID: " + error.trace_id)


def review_request(api, method: str, path: str, **kwargs) -> dict | None:
    """执行一次用户明确触发的接口请求，失败后保留当前表单以便修复重试。"""
    try:
        return api.request(method, path, **kwargs)
    except DashboardError as error:
        show_review_error(error)
        return None


def parse_labels(text: str) -> list[str]:
    """把中英文逗号、顿号或换行分隔的标签转成有序去重列表。"""
    labels = list(dict.fromkeys(item.strip() for item in re.split(r"[,，、\n]", text) if item.strip()))
    if len(labels) > 10 or any(len(label) > 30 for label in labels):
        raise ValueError("最多10个标签，每个标签最多30个字符。")
    return labels


def minute_at(day: date | None, clock: time | None) -> str:
    """仅把用户明确选择的日期和分钟合成北京时间，缺少钟点时拒绝猜测。"""
    if type(day) is not date or not isinstance(clock, time):
        raise ValueError("请明确选择日期和提醒钟点。")
    if clock.second or clock.microsecond or clock.tzinfo is not None:
        raise ValueError("钟点请精确到分钟，并按北京时间选择。")
    return datetime.combine(day, clock, tzinfo=BEIJING).isoformat(timespec="seconds")


def build_time_revision(mode: str, time_text: str | None, reference_time: str | None, current_time: dict | None, fields: dict) -> dict | None:
    """将日期控件选择转为现有时间协议；仅日期保持无at，起止时段保留两个边界。"""
    if mode == "清空":
        return None
    if mode == "保留现有":
        if current_time and current_time.get("time_text") != time_text:
            raise ValueError("修改时间原话后，请同时选择对应的时间调整方式。")
        return None if current_time is None else dict(current_time)
    if mode == "高级JSON":
        value = json.loads(fields["raw_json"])
        if value is not None and not isinstance(value, dict):
            raise ValueError("结构化时间必须是JSON对象或null。")
        return value
    if not time_text or not reference_time:
        raise ValueError("请填写时间原话；原消息也必须保留发送时间。")
    result = {"status": "resolved", "time_text": time_text, "reference_time": reference_time, "timezone": "UTC+08:00"}
    if mode in ("仅日期", "具体时刻"):
        day = fields.get("date")
        if type(day) is not date:
            raise ValueError("请选择事项日期。")
        result["time_kind"] = "deadline" if fields.get("deadline") else "point"
        if mode == "仅日期":
            result.update({"precision": "date", "date": day.isoformat()})
        else:
            result.update({"precision": "minute", "at": minute_at(day, fields.get("clock"))})
        return result
    if mode in ("日期范围", "起止时段"):
        start_day, end_day = fields.get("start_date"), fields.get("end_date")
        if type(start_day) is not date or type(end_day) is not date:
            raise ValueError("请选择开始日期和结束日期。")
        result["time_kind"] = "period"
        if mode == "日期范围":
            if end_day < start_day:
                raise ValueError("结束日期不能早于开始日期。")
            result.update({"precision": "date_range", "start_date": start_day.isoformat(), "end_date": end_day.isoformat()})
        else:
            start = minute_at(start_day, fields.get("start_clock"))
            end = minute_at(end_day, fields.get("end_clock"))
            if end <= start:
                raise ValueError("结束时刻必须晚于开始时刻。")
            result.update({"precision": "minute_range", "start": start, "end": end})
        return result
    raise ValueError("请选择支持的时间调整方式。")


def render_time_fields(mode: str, event: dict, suffix: str) -> dict:
    """按用户选择展示所需日期与钟点；高级模式才展示JSON，所有钟点默认未选择。"""
    fields = {}
    if mode == "高级JSON":
        fields["raw_json"] = st.text_area(
            "结构化时间JSON（无时间填写null）", value=json.dumps(event.get("time"), ensure_ascii=False, indent=2),
            height=220, key="event_time_json_" + suffix,
        )
    elif mode in ("仅日期", "具体时刻"):
        fields["date"] = st.date_input("事项日期", value=None, key="event_date_" + suffix)
        if mode == "具体时刻":
            fields["clock"] = st.time_input("事项钟点（北京时间，需明确选择）", value=None, step=60, key="event_clock_" + suffix)
        selected = st.selectbox("时间性质", ("事项发生", "截止时间"), index=1 if (event.get("time") or {}).get("time_kind") == "deadline" else 0, key="event_time_kind_" + suffix)
        fields["deadline"] = selected == "截止时间"
    elif mode in ("日期范围", "起止时段"):
        fields["start_date"] = st.date_input("开始日期", value=None, key="event_start_date_" + suffix)
        fields["end_date"] = st.date_input("结束日期", value=None, key="event_end_date_" + suffix)
        if mode == "起止时段":
            fields["start_clock"] = st.time_input("开始钟点（北京时间，需明确选择）", value=None, step=60, key="event_start_clock_" + suffix)
            fields["end_clock"] = st.time_input("结束钟点（北京时间，需明确选择）", value=None, step=60, key="event_end_clock_" + suffix)
    return fields


def render_event_review(api, event: dict, message_id: str, reference_time: str | None) -> None:
    """通过日期与钟点控件纠正事项，只有主动选择高级模式才需要编辑JSON。"""
    task_id = event.get("task_id") or event.get("id")
    if not task_id:
        return
    suffix = message_id + "_" + task_id
    version = (event.get("review") or {}).get("version", 0)
    with st.expander("纠正事项：" + event.get("title", "未命名事项")):
        st.caption("事项修改将取消关联旧未投递提醒；原消息和最初提取证据保留。")
        if reference_time:
            st.caption("相对时间依据原消息发送时间：" + reference_time)
        # 模式选择放在表单外，让切换后立即显示对应控件，不执行任何保存请求。
        mode = st.selectbox("时间调整方式", TIME_MODES, key="event_time_mode_" + suffix)
        with st.form("event_review_" + suffix):
            title = st.text_input("事项标题", value=event.get("title", ""), key="event_title_" + suffix)
            location = st.text_input("事项地点（留空清除）", value=event.get("location") or "", key="event_location_" + suffix)
            if mode == "清空":
                time_text = None
                st.caption("保存后清空本事项的时间原话和时间信息，完整原消息仍保留。")
            else:
                time_text = st.text_input("事项时间原话", value=event.get("time_text") or "", key="event_time_text_" + suffix)
            fields = render_time_fields(mode, event, suffix)
            selected = next((label for label, state in STATUS_LABELS.items() if state == event.get("status")), "待办")
            status = st.selectbox("事项状态", tuple(STATUS_LABELS), index=tuple(STATUS_LABELS).index(selected), key="event_status_" + suffix)
            replan = st.checkbox("保存后按当前提醒规则重新安排（规则未启用则不新建）", value=True, key="event_replan_" + suffix)
            submitted = st.form_submit_button("保存事项纠正", key="event_save_" + suffix)
        if submitted:
            try:
                time_text = (time_text or "").strip() or None
                time_value = build_time_revision(mode, time_text, reference_time, event.get("time"), fields)
                if not title.strip():
                    raise ValueError("事项标题不能为空。")
            except (ValueError, TypeError) as error:
                st.error("无法保存：" + str(error))
            else:
                result = review_request(api, "PUT", "/tasks/" + quote(task_id, safe="") + "/review", params={"replan": replan}, json={
                    "expected_version": version, "title": title.strip(), "location": location.strip() or None,
                    "time_text": time_text, "time": time_value, "status": STATUS_LABELS[status],
                })
                if result is not None:
                    st.success("事项纠正已保存；刷新列表可查看最新结果。")
                    # 外部完成按钮的本地展示缓存不能阻止人工重新打开的事项。
                    if STATUS_LABELS[status] == "pending":
                        st.session_state["completed_tasks"] = [item for item in st.session_state.get("completed_tasks", []) if item != task_id]


def render_event_reminder(api, event: dict, message_id: str) -> None:
    """为待办提供明确日期和钟点的手工提醒表单，用户未选择钟点时不创建计划。"""
    task_id = event.get("task_id") or event.get("id")
    if event.get("status") != "pending" or not task_id:
        return
    suffix = message_id + "_" + task_id
    with st.expander("手工设置提醒：" + event.get("title", "未命名事项")):
        st.caption("此处只设置通知时间，不改变事项原有日期或截止精度。")
        with st.form("manual_reminder_" + suffix):
            day = st.date_input("提醒日期", value=None, key="manual_reminder_date_" + suffix)
            clock = st.time_input("提醒钟点（北京时间，需明确选择）", value=None, step=60, key="manual_reminder_clock_" + suffix)
            channel = st.selectbox("提醒方式", ("Windows桌面通知", "控制台记录"), key="manual_reminder_channel_" + suffix)
            submitted = st.form_submit_button("保存手工提醒", key="manual_reminder_save_" + suffix)
        if submitted:
            try:
                remind_at = minute_at(day, clock)
            except ValueError as error:
                st.error(str(error))
            else:
                result = review_request(api, "POST", "/reminders", json={
                    "task_id": task_id, "remind_at": remind_at,
                    "channel": "windows" if channel == "Windows桌面通知" else "console",
                })
                if result is not None:
                    st.success("手工提醒已保存；可在提醒列表查看投递状态。")


def render_message_review(api, message: dict) -> None:
    """呈现消息分类纠正，并为各事项提供日期编辑和明确钟点的手工提醒入口。"""
    message_id = message.get("message_id")
    if not message_id:
        return
    analysis = message.get("analysis") or {}
    category = analysis.get("category")
    current = category if category in CATEGORIES else "其他"
    version = (message.get("review") or {}).get("version", 0)
    with st.expander("纠正消息分类、标签和摘要"):
        st.caption("人工纠正另存修订历史，不覆盖完整原消息；多个标签用逗号分隔。")
        with st.form("message_review_" + message_id):
            chosen = st.selectbox("纠正类别", CATEGORIES, index=CATEGORIES.index(current), key="review_category_" + message_id)
            labels_text = st.text_input("消息标签", value="，".join(message.get("labels") or analysis.get("labels") or []), key="review_labels_" + message_id)
            summary = st.text_area("消息摘要", value=analysis.get("summary") or "", key="review_summary_" + message_id)
            submitted = st.form_submit_button("保存消息纠正", key="message_save_" + message_id)
        if submitted:
            try:
                labels = parse_labels(labels_text)
                if not summary.strip():
                    raise ValueError("摘要不能为空。")
            except ValueError as error:
                st.error(str(error))
            else:
                result = review_request(api, "PUT", "/messages/" + quote(message_id, safe="") + "/review", json={
                    "expected_version": version, "category": chosen, "labels": labels, "summary": summary.strip(),
                })
                if result is not None:
                    st.success("消息纠正已保存；刷新列表可查看最新分类和标签。")
        if st.button("查看消息修订历史", key="message_history_" + message_id):
            result = review_request(api, "GET", "/messages/" + quote(message_id, safe="") + "/reviews")
            if result is not None:
                st.json(result)
    for event in message.get("events", []):
        render_event_review(api, event, message_id, (message.get("source_message") or {}).get("sent_at"))
        render_event_reminder(api, event, message_id)


def render_document_review(api, document: dict) -> None:
    """显示正文分类证据并提供显式模型分类、重试和人工多标签纠正入口。"""
    document_id = document.get("document_id")
    if not document_id:
        return
    classification = document.get("classification") or {}
    model_result = classification.get("result") or {}
    if document.get("category"):
        st.text("文件类别：" + document["category"])
    if document.get("labels"):
        st.text("文件标签：" + "、".join(document["labels"]))
    basis = {"document_text": "文件正文", "manual_review": "人工纠正", "message_text": "消息内容推断"}.get(document.get("classification_basis"), "尚未读取正文分类")
    st.caption("分类依据：" + basis)
    coverage = model_result.get("coverage") or {}
    if coverage.get("scope") == "sampled_text":
        st.warning(f"正文分类仅依据节选：{coverage.get('read_characters', 0)}/{coverage.get('total_characters', 0)} 字符，未覆盖全部文件。")
    if classification.get("status") == "failed":
        st.warning("上次正文分类失败，可重新点击分类。错误类型：" + str(classification.get("error_code") or "未知"))
    for evidence in model_result.get("evidence", []):
        st.caption("分类正文依据 · 第" + str(evidence.get("page")) + "页")
        st.text(evidence.get("quote", ""))
    if document.get("status") == "indexed":
        if st.button("读取正文并分类（调用模型）", key="classify_" + document_id):
            result = review_request(api, "POST", "/documents/" + quote(document_id, safe="") + "/classify")
            if result is not None:
                st.success("正文分类已完成；刷新文档列表可查看类别和逐字证据。")
                st.json(result)
    with st.expander("纠正文件类别和标签"):
        current = document.get("category") if document.get("category") in CATEGORIES else "其他"
        with st.form("document_review_" + document_id):
            category = st.selectbox("文件类别", CATEGORIES, index=CATEGORIES.index(current), key="document_category_" + document_id)
            labels_text = st.text_input("文件标签", value="，".join(document.get("labels") or []), key="document_labels_" + document_id)
            submitted = st.form_submit_button("保存文件纠正", key="document_save_" + document_id)
        if submitted:
            try:
                labels = parse_labels(labels_text)
            except ValueError as error:
                st.error(str(error))
            else:
                result = review_request(api, "PUT", "/documents/" + quote(document_id, safe="") + "/review", json={
                    "expected_version": (document.get("review") or {}).get("version", 0), "category": category, "labels": labels,
                })
                if result is not None:
                    st.success("文件纠正已保存；自动正文分类证据仍然保留。")


def render_message_attachment_download(api, message: dict) -> None:
    """只在明确按钮后取回受管原件，缺少原件时允许上传实际字节补齐线索。"""
    message_id = message.get("message_id")
    if not message_id:
        return
    for index, attachment in enumerate(message.get("attachments", [])):
        suffix = message_id + "_" + str(index)
        filename = attachment.get("filename") or "attachment"
        with st.expander("附件原件：" + filename):
            available = attachment.get("availability") in ("available", "indexed", "downloaded", "stored")
            st.caption("已取得原件，可下载。" if available else "目前只有附件线索，尚未取得原件。")
            if available and st.button("准备附件下载", key="attachment_prepare_" + suffix):
                try:
                    st.session_state["attachment_bytes_" + suffix] = api.download_attachment(message_id, index)
                except DashboardError as error:
                    show_review_error(error)
            if "attachment_bytes_" + suffix in st.session_state:
                st.download_button("下载附件原件", st.session_state["attachment_bytes_" + suffix], file_name=filename, key="attachment_download_" + suffix)
            if not available:
                with st.form("attachment_upload_" + suffix):
                    uploaded = st.file_uploader("选择此附件的真实原件（最多25MB）", key="attachment_file_" + suffix)
                    submitted = st.form_submit_button("补传此附件原件", key="attachment_upload_save_" + suffix)
                if submitted:
                    if uploaded is None:
                        st.error("请先选择实际文件。")
                    else:
                        try:
                            result = api.upload_attachment(message_id, index, uploaded.name, uploaded.getvalue())
                        except DashboardError as error:
                            show_review_error(error)
                        else:
                            st.success("附件原件已保存；刷新列表可查看下载入口。")
                            st.json(result)
