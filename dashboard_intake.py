"""普通用户的手工收件入口：粘贴原消息并上传附件，保留来源时间与部分成功状态。"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import quote

import streamlit as st

from dashboard_api import DashboardError


BEIJING = timezone(timedelta(hours=8))
MAX_ORIGINAL_BYTES = 25 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def read_uploaded_files(uploaded_files) -> list[dict]:
    """校验附件数量和字节上限，读取实际上传内容；不把文件名当作已有原件。"""
    if len(uploaded_files) > 20:
        raise ValueError("一条消息最多附20个文件。")
    result = []
    for uploaded in uploaded_files:
        filename = PurePosixPath(uploaded.name.replace("\\", "/")).name
        if not filename.strip() or len(filename) > 255:
            raise ValueError("附件文件名须为1至255个字符。")
        if getattr(uploaded, "size", 0) > MAX_ORIGINAL_BYTES:
            raise ValueError("每个附件最多25MB，请移除超限文件后重试。")
        data = uploaded.getvalue()
        if not isinstance(data, bytes) or len(data) > MAX_ORIGINAL_BYTES:
            raise ValueError("每个附件最多25MB，且须提供真实文件字节。")
        media_type = getattr(uploaded, "type", None)
        result.append({"filename": filename, "data": data, "media_type": media_type[:120] if isinstance(media_type, str) else None})
    return result


def content_identity(text: str, sender_id: str, sender_name: str, files: list[dict]) -> str:
    """生成输入内容指纹，附件顺序与字节也参与去重，避免同名不同原件发生来源冲突。"""
    value = {
        "text": text, "sender_id": sender_id.strip(), "sender_name": sender_name.strip(),
        "attachments": [{"filename": item["filename"], "sha256": hashlib.sha256(item["data"]).hexdigest()} for item in files],
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def build_manual_payload(
    text: str, sender_id: str, sender_name: str, original_date, original_time,
    use_now: bool, files: list[dict], *, now: datetime | None = None,
) -> dict:
    """把普通表单转为正式消息协议，必须提供完整原时间或明确选择刚收到。"""
    if not text.strip() or len(text) > 5000:
        raise ValueError("请填写1至5000个字符的原消息正文。")
    if not sender_id.strip() or len(sender_id.strip()) > 200:
        raise ValueError("请填写用于区分联系人的标识，最多200个字符。")
    if len(sender_name.strip()) > 120:
        raise ValueError("联系人显示名最多120个字符。")
    if len(files) > 20 or any(len(item["data"]) > MAX_ORIGINAL_BYTES for item in files):
        raise ValueError("最多20个附件，每个最多25MB。")
    if use_now:
        if original_date is not None or original_time is not None:
            raise ValueError("请选择一种时间方式：使用现在时间时，请清空原消息日期和钟点。")
        sent = now or datetime.now(BEIJING)
        if sent.utcoffset() is None:
            raise ValueError("当前时间必须携带时区。")
        sent = sent.astimezone(BEIJING)
    else:
        if original_date is None or original_time is None:
            raise ValueError("请补齐原消息日期与钟点；仅刚收到时才勾选使用现在的北京时间。")
        sent = datetime.combine(original_date, original_time.replace(second=0, microsecond=0, tzinfo=None), tzinfo=BEIJING)
    sent_at = sent.isoformat()
    fingerprint = content_identity(text, sender_id, sender_name, files)
    source_id = "paste-" + hashlib.sha256((fingerprint + ":" + sent_at).encode("utf-8")).hexdigest()[:32]
    return {
        "source": "manual", "account_id": "manual-paste", "source_message_id": source_id,
        "sender_id": sender_id.strip(), "sender_name": sender_name.strip() or None,
        "sent_at": sent_at, "text": text,
        "attachments": [{"filename": item["filename"], "media_type": item["media_type"], "availability": "mentioned"} for item in files],
    }


def organize_manual_intake(api, payload: dict, files: list[dict]) -> dict:
    """先保存分析消息，再逐件归档、索引与正文分类，单文件失败不会撤销已成功的消息。"""
    received = api.request("POST", "/messages", json=payload)
    message_id = (received.get("message") or {}).get("message_id")
    if not isinstance(message_id, str) or not message_id:
        raise DashboardError("服务未返回消息编号，请先查询消息列表再决定是否重试。")
    result = {"message_id": message_id, "message_status": received.get("status"), "attachments": [], "complete": True}
    for index, item in enumerate(files):
        row = {"filename": item["filename"], "archived": False, "indexed": False, "classified": False}
        stage = "保存原件"
        try:
            api.upload_attachment(message_id, index, item["filename"], item["data"])
            row["archived"] = True
            extension = PurePosixPath(item["filename"]).suffix.lower()
            if extension not in (".txt", ".md", ".pdf"):
                row["notice"] = "原件已保存；当前格式无法提取正文，可在消息卡片人工分类。"
            elif not item["data"] or len(item["data"]) > MAX_DOCUMENT_BYTES:
                row["notice"] = "原件已保存；正文索引需要非空且不超过10MB的txt、md或文本PDF。"
            else:
                stage = "建立正文索引"
                indexed = api.upload(item["filename"], item["data"], message_id, index)
                document = indexed.get("document") or {}
                document_id = document.get("document_id")
                if not isinstance(document_id, str) or not document_id:
                    raise DashboardError("服务未返回文件编号，原件仍已保存，可在研究资料中重试。")
                row.update({"indexed": True, "document_id": document_id})
                stage = "正文分类"
                if indexed.get("status") == "exists":
                    document = api.request("GET", "/documents/" + quote(document_id, safe=""))
                # 相同文件已完成分类时复用结果，重复提交不额外调用分类模型。
                if (document.get("classification") or {}).get("status") != "completed":
                    api.request("POST", "/documents/" + quote(document_id, safe="") + "/classify")
                row["classified"] = True
        except DashboardError as error:
            result["complete"] = False
            row.update({"error": str(error), "error_stage": stage, "trace_id": error.trace_id})
        result["attachments"].append(row)
    return result


def render_intake_result(result: dict) -> None:
    """显示已成功的消息编号及每件附件状态，明确区分部分失败和全部成功。"""
    if result["complete"]:
        st.success("消息已整理并保存。消息编号：" + result["message_id"])
    else:
        st.warning("消息已保存，部分附件步骤尚未完成。消息编号：" + result["message_id"] + "；可在消息卡片或研究资料页重试。")
    if result.get("message_status") == "exists":
        st.caption("这条原消息已经存在，复用原事项；本次仅继续核对和补齐附件。")
    for item in result["attachments"]:
        states = [name for key, name in (("archived", "原件已保存"), ("indexed", "已建索引"), ("classified", "正文已分类")) if item.get(key)]
        st.text(item["filename"] + "：" + ("、".join(states) or "原件尚未保存"))
        if item.get("notice"):
            st.caption(item["notice"])
        if item.get("error"):
            st.error(item["error_stage"] + "未完成：" + item["error"])
            if item.get("trace_id"):
                st.caption("Trace ID: " + item["trace_id"])


def start_next_message() -> None:
    """用户明确开始下一条时清除重试时钟，使内容相同的新消息也能使用新的接收时刻。"""
    st.session_state.pop('manual_paste_clock', None)
    st.session_state.pop('manual_intake_result', None)


def render_manual_intake(api) -> None:
    """提供无需JSON的手工收件表单，只有明确提交后才分析消息并上传实际附件。"""
    with st.expander("粘贴消息并整理附件"):
        st.caption("填写原消息和发送时间；仅点击整理后调用模型。联系人标识由你指定，用于区分同名联系人。")
        with st.form("manual_intake"):
            text = st.text_area("原消息正文", max_chars=5000, height=150, key="paste_text")
            sender_id = st.text_input("联系人标识（必填，例如 mentor-zhang）", max_chars=200, key="paste_sender_id")
            sender_name = st.text_input("联系人显示名（可选）", max_chars=120, key="paste_sender_name")
            original_date = st.date_input("原消息日期（北京时间）", value=None, key="paste_date")
            original_time = st.time_input("原消息钟点（北京时间）", value=None, step=60, key="paste_time")
            use_now = st.checkbox("刚收到，使用现在的北京时间", value=False, key="paste_use_now")
            uploaded = st.file_uploader("附件原件（可选，最多20件，每件最多25MB）", accept_multiple_files=True, key="paste_files")
            submitted = st.form_submit_button("整理消息和附件（调用模型）", key="paste_submit")
        if submitted:
            # 新一次提交失败时不继续显示上次成功结果，避免把旧结果误当本次状态。
            st.session_state.pop("manual_intake_result", None)
            try:
                files = read_uploaded_files(uploaded or [])
                identity = content_identity(text, sender_id, sender_name, files)
                cached = st.session_state.get("manual_paste_clock") or {}
                now = datetime.fromisoformat(cached["at"]) if cached.get("identity") == identity else datetime.now(BEIJING)
                payload = build_manual_payload(text, sender_id, sender_name, original_date, original_time, use_now, files, now=now)
                if use_now:
                    # 重试同一表单保留首次声明的接收时刻，避免每次点击变成一条新消息。
                    st.session_state["manual_paste_clock"] = {"identity": identity, "at": payload["sent_at"]}
            except ValueError as error:
                st.error(str(error))
            else:
                try:
                    with st.spinner("正在整理消息和附件……"):
                        result = organize_manual_intake(api, payload, files)
                except DashboardError as error:
                    st.error(str(error))
                    if error.trace_id:
                        st.caption("Trace ID: " + error.trace_id)
                else:
                    st.session_state["manual_intake_result"] = result
        if st.session_state.get("manual_intake_result"):
            render_intake_result(st.session_state["manual_intake_result"])
            st.button('开始录入下一条消息', key='paste_next_message', on_click=start_next_message)
