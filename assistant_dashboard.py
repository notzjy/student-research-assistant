"""本地个人助理界面：只通过HTTP调用8002服务，所有写入都由用户按钮显式发起。"""

import json
import os
from uuid import uuid4

import streamlit as st

from dashboard_api import DashboardAPI, DashboardError
from dashboard_views import citation_label, event_badges, source_label, trace_rows
from dashboard_review import render_message_review, render_document_review, render_message_attachment_download
from dashboard_connections import attention_page, phone_page, render_sender_rule
from dashboard_intake import render_manual_intake


PAGES = ("消息与日程", "研究资料", "提醒与角色", "重点收件箱", "手机接入", "运行追踪", "对话助理")
SAMPLE_MESSAGE = {
    "source": "manual", "account_id": "demo", "source_message_id": "dashboard-demo-001",
    "sender_id": "demo-teacher-001", "sender_name": "示例老师", "sent_at": "2026-10-16T10:00:00+08:00",
    "text": "明天下午三点在实验楼302开组会；下周三前交综述，附件是两篇参考论文。",
    "attachments": [
        {"filename": "reference-a.pdf", "availability": "mentioned"},
        {"filename": "reference-b.pdf", "availability": "mentioned"},
    ],
}


def show_error(error: DashboardError) -> None:
    """显示适配器提供的安全错误及追踪编号，失败时保持界面和会话可继续使用。"""
    st.error(str(error))
    if error.trace_id:
        st.caption("Trace ID: " + error.trace_id)


def request(api: DashboardAPI, method: str, path: str, **kwargs) -> dict | None:
    """执行一次明确HTTP操作并显示可恢复的错误，成功时保留最近追踪编号。"""
    try:
        result = api.request(method, path, **kwargs)
    except DashboardError as error:
        show_error(error)
        return None
    if api.last_trace_id:
        st.session_state["last_trace_id"] = api.last_trace_id
    return result


def render_event(api: DashboardAPI, event: dict, message_id: str, index: int) -> None:
    """呈现事项的文字与色彩语义；只有用户按下按钮才修改待办状态。"""
    st.text(event.get("title", "未命名事项"))
    for badge in event_badges(event):
        # 徽章文字由程序固定；原消息中的时间和地点使用纯文本显示。
        st.badge(badge["label"], color=badge["color"])
        st.text(badge["text"])
    if event.get("evidence"):
        st.caption("原文依据")
        st.text(event["evidence"])
    task_id = event.get("task_id")
    completed = task_id in st.session_state.get("completed_tasks", [])
    if event.get("status") == "pending" and task_id and not completed:
        if st.button("标记完成", key=f"complete_{message_id}_{index}"):
            result = request(api, "POST", f"/tasks/{task_id}/complete")
            if result is not None:
                # 本次按钮状态只更新本地展示，不在rerun时再次提交写入。
                st.session_state.setdefault("completed_tasks", []).append(task_id)
                st.success("已标记完成；相关未投递提醒由服务停止。")
    else:
        st.caption("状态：已完成" if completed or event.get("status") == "completed" else "状态：" + str(event.get("status", "未知")))


def messages_page(api: DashboardAPI) -> None:
    """按用户选择读取消息，保留原文和附件线索，并提供显式的JSON导入表单。"""
    st.header("消息与日程")
    category = st.selectbox("消息类别", ("全部", "研究", "学生生活", "求职", "其他"), key="message_category")
    important = st.checkbox("只看重点联系人", key="important_only")
    query = st.text_input('搜索原文、摘要或发送者', key='message_search')
    label = st.text_input('筛选标签（精确匹配，可留空）', key='message_label')
    selection = (category, important, query, label)
    if st.session_state.get('message_filter_selection') != selection:
        st.session_state['message_page'] = 1
        st.session_state['message_filter_selection'] = selection
    st.session_state.setdefault('message_page', 1)
    page = st.number_input('消息页码', min_value=1, value=None, step=1, key='message_page')
    page = int(page or 1)
    params = {"important_only": important, "limit": 100}
    if page > 1:
        params['offset'] = (int(page) - 1) * 100
    if query.strip():
        params['query'] = query.strip()
    if label.strip():
        params['label'] = label.strip()
    if category != "全部":
        params["category"] = category
    response = request(api, "GET", "/messages", params=params)
    messages = (response or {}).get("messages", [])
    if response is not None:
        st.caption(f"本次显示 {len(messages)} 条消息；符合条件共 {response.get('total', len(messages))} 条，第 {page} 页")
    for message in messages:
        message_id = message.get("message_id", "unknown")
        with st.container(border=True):
            analysis = message.get("analysis") or {}
            st.badge(analysis.get("category", "尚未分类"), color="blue")
            role = message.get("role_match") or {}
            if role.get("important"):
                st.badge("重点联系人", color="orange")
            if role.get("role"):
                st.text("角色：" + role["role"])
            st.text(source_label(message))
            if message.get('source_metadata'):
                basis = '应用提供的消息时间' if message['source_metadata'].get('time_basis') == 'app_message_timestamp' else '通知发布时间，可能不是原聊天发送时间'
                st.warning('系统通知摘要：时间基于' + basis + '；正文和附件可能不完整。')
            if message.get('labels'):
                st.text('标签：' + '、'.join(message['labels']))
            if analysis.get("summary"):
                st.caption("摘要")
                st.text(analysis["summary"])
            with st.expander("完整原消息"):
                st.text((message.get("source_message") or {}).get("text", ""))
                st.caption("消息编号：" + message_id)
            if message.get("processing_status") != "completed":
                st.warning("消息尚未处理完成：" + str(message.get("error_code") or message.get("processing_status")))
            for index, event in enumerate(message.get("events", [])):
                render_event(api, event, message_id, index)
            for attachment in message.get("attachments", []):
                st.text(f"附件：{attachment.get('filename', '未命名')} · {attachment.get('availability', 'mentioned')}")
            render_sender_rule(api, message)
            render_message_review(api, message)
            render_message_attachment_download(api, message)
    render_manual_intake(api)
    with st.expander("高级：导入完整来源JSON消息"):
        st.caption("附件文件名仅表示线索。真实文件请在研究资料页上传。提交后服务会调用模型分析，并按已启用规则安排提醒。")
        with st.form("message_import"):
            raw = st.text_area("完整消息JSON", json.dumps(SAMPLE_MESSAGE, ensure_ascii=False, indent=2), height=300)
            submitted = st.form_submit_button("导入并分析消息")
        if submitted:
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError
            except (ValueError, TypeError):
                st.error("请填写一个完整的JSON对象。")
            else:
                result = request(api, "POST", "/messages", json=payload)
                if result is not None:
                    st.success("导入结果：" + str(result.get("status")))
                    st.json(result)


def render_research_result(result: dict) -> None:
    """显示检索正文或有来源的回答，证据不足时明确表达未知。"""
    if "answer" in result:
        if result.get("status") == "unknown":
            st.info("当前文件证据不足，无法可靠回答。")
        st.text(result.get("answer", ""))
    for citation in result.get("citations", []):
        st.caption("引用：" + citation_label(citation))
        st.text(citation.get("quote", ""))
    for hit in result.get("hits", []):
        st.caption(citation_label(hit) + " · 相似度 " + str(hit.get("score", "未知")))
        st.text(hit.get("text", ""))


def research_page(api: DashboardAPI) -> None:
    """读取文档元数据，分别由明确按钮发起真实文件上传、检索和模型问答。"""
    st.header("研究资料")
    response = request(api, "GET", "/documents")
    documents = (response or {}).get("documents", [])
    st.caption("文件支持txt、md和含文本PDF，最多10MB；扫描PDF暂不支持OCR。")
    for document in documents:
        with st.container(border=True):
            st.text(f"{document.get('filename')} · {document.get('status')} · {document.get('chunk_count', 0)} 个片段")
            doc_id = document.get("document_id", "")
            st.caption("文档编号：" + doc_id)
            render_document_review(api, document)
            if st.button("准备原件下载", key="prepare_" + doc_id):
                try:
                    st.session_state["download_" + doc_id] = api.download(doc_id)
                except DashboardError as error:
                    show_error(error)
            if "download_" + doc_id in st.session_state:
                st.download_button("下载原件", st.session_state["download_" + doc_id], file_name=document.get("filename") or "document", key="download_button_" + doc_id)
    with st.form("document_upload"):
        uploaded = st.file_uploader("选择真实文件", type=["txt", "md", "pdf"])
        binding = st.checkbox("绑定到消息中已有的附件线索")
        message_id = st.text_input("消息编号（绑定时填写）")
        attachment_index = st.number_input("附件序号（从0开始）", min_value=0, value=0, step=1)
        upload = st.form_submit_button("上传并建立索引")
    if upload:
        if uploaded is None or (binding and not message_id.strip()):
            st.error("请选择文件；绑定时还需填写消息编号。")
        else:
            try:
                result = api.upload(uploaded.name, uploaded.getvalue(), message_id.strip() if binding else None, int(attachment_index) if binding else None)
                st.success("文件处理结果：" + str(result.get("status")))
                st.json(result)
            except DashboardError as error:
                show_error(error)
    with st.form("document_question"):
        question = st.text_area("检索词或研究问题", key="research_question")
        top_k = st.number_input("候选片段数", min_value=1, max_value=10, value=5, step=1)
        selected = st.selectbox("检索范围", ["全部文件"] + [item["document_id"] for item in documents], format_func=lambda item: item if item == "全部文件" else next((doc.get("filename", item) for doc in documents if doc["document_id"] == item), item))
        search = st.form_submit_button("仅检索片段")
        ask = st.form_submit_button("基于文件回答（调用模型）")
    if search or ask:
        if not question.strip():
            st.error("请先填写检索词或研究问题。")
        else:
            payload = {"question" if ask else "query": question.strip(), "top_k": int(top_k)}
            if selected != "全部文件":
                payload["document_id"] = selected
            result = request(api, "POST", "/documents/ask" if ask else "/documents/search", json=payload)
            if result is not None:
                st.session_state["research_result"] = result
    if "research_result" in st.session_state:
        render_research_result(st.session_state["research_result"])


def reminders_page(api: DashboardAPI) -> None:
    """读取提醒和安全邮箱状态，通过表单显式保存规则、角色或同步真实邮件。"""
    st.header("提醒与角色")
    st.caption("submitted表示已交给通知通道，不代表已读；取消提醒不会完成待办。")
    response = request(api, "GET", "/reminders")
    for item in (response or {}).get("reminders", []):
        with st.container(border=True):
            st.text(f"{item.get('title')} · {item.get('remind_at')} · {item.get('status')}")
            if item.get("status") in ("pending", "leased", "failed"):
                if st.button("取消提醒", key="cancel_" + item["reminder_id"]):
                    result = request(api, "POST", f"/reminders/{item['reminder_id']}/cancel", params={"expected_version": item["version"]})
                    if result is not None:
                        st.success("提醒已取消。")
    policy = request(api, "GET", "/reminders/policy") or {}
    with st.form("reminder_policy"):
        st.subheader("自动提醒规则")
        enabled = st.checkbox("启用自动提醒", value=policy.get("enabled", False), key='reminder_policy_enabled')
        lead = st.number_input("明确时刻提前多少分钟", min_value=0, max_value=10080, value=policy.get("lead_minutes", 30), step=1)
        date_clock = st.text_input("仅有日期时的提醒钟点（HH:MM，留空则不自动安排）", value=policy.get("date_only_time") or "")
        channel = st.selectbox("提醒通道", ["windows", "console"], index=0 if policy.get("channel") == "windows" else 1)
        save = st.form_submit_button("保存提醒规则")
    if save:
        result = request(api, "PUT", "/reminders/policy", json={"enabled": enabled, "lead_minutes": int(lead), "date_only_time": date_clock.strip() or None, "channel": channel})
        if result is not None:
            st.success("规则已保存，后续消息按此规则处理；不会自动重做全部旧消息。")
    roles = request(api, "GET", "/roles")
    st.subheader("已确认的角色")
    if (roles or {}).get("roles"):
        st.dataframe(roles["roles"], hide_index=True)
    with st.form("role_rule"):
        source = st.selectbox("角色来源", ["manual", "qq_mail", "wechat", "qq", "boss", "android"])
        account = st.text_input("账号标识", value="demo")
        sender = st.text_input("发送者精确标识")
        role = st.text_input("角色名称", value="导师")
        important = st.checkbox("设为重点联系人")
        save_role = st.form_submit_button("保存角色配置")
    if save_role:
        result = request(api, "PUT", "/roles", json={"source": source, "account_id": account, "sender_id": sender, "role": role, "important": important})
        if result is not None:
            st.success("角色配置已保存。")
    st.subheader("QQ邮箱状态")
    status = request(api, "GET", "/mail/status")
    if status is not None:
        st.json(status)
    st.caption("查看状态不连接邮箱；后台收件是否运行由主服务配置决定。账号与授权码只在本机configure_mail.py配置。")
    if st.button("同步邮箱并分析邮件"):
        result = request(api, "POST", "/mail/sync")
        if result is not None:
            st.json(result)


def traces_page(api: DashboardAPI) -> None:
    """查询执行追踪列表和单次请求的Span元数据，不把完成状态当作语义正确。"""
    st.header("运行追踪")
    st.caption("completed表示执行完成；内容正确性还需语义评测。")
    result = request(api, "GET", "/traces", params={"limit": 50})
    traces = (result or {}).get("traces", [])
    if traces:
        st.dataframe(traces, hide_index=True)
    with st.form("trace_detail"):
        trace_id = st.text_input("Trace ID", value=st.session_state.get("last_trace_id", ""))
        submit = st.form_submit_button("查询执行步骤")
    if submit and trace_id.strip():
        detail = request(api, "GET", "/traces/" + trace_id.strip())
        if detail is not None:
            st.session_state["trace_detail"] = detail
    if "trace_detail" in st.session_state:
        detail = st.session_state["trace_detail"]
        st.text("追踪状态：" + str(detail.get("status")))
        st.dataframe(trace_rows(detail.get("spans", [])), hide_index=True)


def chat_page(api: DashboardAPI) -> None:
    """在当前浏览器会话复用线程，只有用户明确新建时才更换对话上下文。"""
    st.header("对话助理")
    st.session_state.setdefault("thread_id", uuid4().hex)
    st.session_state.setdefault("chat_entries", [])
    if st.button("新建对话"):
        st.session_state["thread_id"] = uuid4().hex
        st.session_state["chat_entries"] = []
        st.session_state.pop("chat_result", None)
    thread_id = st.session_state["thread_id"]
    st.caption("当前 thread_id：" + thread_id)
    st.caption("连续发送会复用此编号。失败后先查询线程，显式恢复不会再发一条重复用户消息。")
    with st.form("chat_send", clear_on_submit=True):
        message = st.text_area("发送消息", key="chat_message")
        send = st.form_submit_button("发送")
    if send and message.strip():
        st.session_state["chat_entries"].append({"role": "user", "content": message.strip()})
        result = request(api, "POST", "/invoke", json={"message": message.strip(), "thread_id": thread_id})
        if result is not None:
            st.session_state["chat_entries"].append({"role": "assistant", "content": result.get("reply", "")})
            st.session_state["chat_result"] = result
    query, resume = st.columns(2)
    if query.button("查询当前线程"):
        result = request(api, "GET", "/threads/" + thread_id)
        if result is not None:
            st.json(result)
    if resume.button("恢复未完成流程"):
        result = request(api, "POST", f"/threads/{thread_id}/resume")
        if result is not None:
            st.session_state["chat_entries"].append({"role": "assistant", "content": result.get("reply", "")})
            st.session_state["chat_result"] = result
    for entry in st.session_state["chat_entries"]:
        with st.chat_message(entry["role"]):
            st.text(entry["content"])
    if "chat_result" in st.session_state:
        with st.expander("本轮执行信息"):
            st.json(st.session_state["chat_result"])


def main() -> None:
    """初始化本地页面并只执行当前导航页，首次打开只读取消息列表。"""
    st.set_page_config(page_title="个人研究与日程助理", page_icon="📚", layout="wide")
    st.title("个人研究与日程助理")
    base_url = st.sidebar.text_input("主服务地址", value=os.getenv("ASSISTANT_API_URL", "http://127.0.0.1:8002"))
    page = st.sidebar.radio("导航", PAGES)
    try:
        api = DashboardAPI(base_url)
    except DashboardError as error:
        show_error(error)
        return
    # 地址变化时清除当前界面的临时数据，防止两个后端结果混用。
    if st.session_state.get("active_api_url") != api.base_url:
        for key in list(st.session_state):
            if key.startswith(("download_", 'attachment_', 'event_', 'manual_reminder_', 'review_', 'sender_', 'document_', 'phone_', 'message_', 'paste_', 'manual_intake_', 'manual_paste_')) or key in ("research_result", "trace_detail", "completed_tasks", "chat_entries", "thread_id", "chat_result", "last_trace_id", 'important_only', 'attention_enabled', 'reminder_policy_enabled'):
                del st.session_state[key]
        st.session_state["active_api_url"] = api.base_url
    routes = dict(zip(PAGES, (messages_page, research_page, reminders_page, attention_page, phone_page, traces_page, chat_page)))
    routes[page](api)
    if st.session_state.get("last_trace_id"):
        st.sidebar.caption("最近 Trace ID：" + st.session_state["last_trace_id"])


if __name__ == "__main__":
    main()
