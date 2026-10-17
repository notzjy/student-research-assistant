"""纯ASGI追踪中间件：关联一次HTTP操作及其节点，不读取请求正文或模型凭证。"""

import asyncio
import re
from uuid import uuid4

from student_assistant.trace_store import safe_diagnostic
from student_assistant.tracing import span, use_trace


def operation_name(method: str, path: str) -> str:
    """把内部路由映射到固定操作名；不持久化URL参数、路径中的用户编号或请求内容。"""
    known = {
        ("POST", "/invoke"): "chat.invoke", ("POST", "/student-assistant/invoke"): "chat.invoke",
        ("POST", "/messages"): "messages.ingest", ("GET", "/messages"): "messages.list",
        ("PUT", "/roles"): "roles.write", ("GET", "/roles"): "roles.read",
        ("POST", "/documents"): "documents.index", ("GET", "/documents"): "documents.list",
        ("POST", "/documents/search"): "documents.search", ("POST", "/documents/ask"): "documents.ask",
        ("POST", "/reminders"): "reminders.create", ("GET", "/reminders"): "reminders.list",
        ("POST", "/reminders/plan"): "reminders.plan", ("POST", "/reminders/run-once"): "reminders.run_once",
        ("PUT", "/reminders/policy"): "reminders.policy.write", ("GET", "/reminders/policy"): "reminders.policy.read",
        ("GET", "/mail/status"): "mail.status", ("GET", "/mail/jobs"): "mail.jobs", ("POST", "/mail/sync"): "mail.sync",
        ('GET', '/attention'): 'attention.list', ('GET', '/attention/policy'): 'attention.policy.read',
        ('PUT', '/attention/policy'): 'attention.policy.write', ('POST', '/notifications'): 'notifications.receive',
        ('GET', '/notifications/status'): 'notifications.status', ('POST', '/notifications/config'): 'notifications.configure',
    }
    if (method, path) in known:
        return known[(method, path)]
    if method == 'PUT' and re.fullmatch(r'/messages/[^/]+/review', path):
        return 'messages.review'
    if method == 'PUT' and re.fullmatch(r'/tasks/[^/]+/review', path):
        return 'tasks.review'
    if method == 'POST' and re.fullmatch(r'/tasks/[^/]+/replan', path):
        return 'tasks.replan'
    if method == 'PUT' and re.fullmatch(r'/documents/[^/]+/review', path):
        return 'documents.review'
    if method == 'POST' and re.fullmatch(r'/documents/[^/]+/classify', path):
        return 'documents.classify'
    if method == 'POST' and re.fullmatch(r'/messages/[^/]+/attachments/[0-9]+', path):
        return 'attachments.upload'
    if method == 'GET' and re.fullmatch(r'/messages/[^/]+/attachments/[0-9]+/file', path):
        return 'attachments.file'
    if method == 'POST' and re.fullmatch(r'/attention/[^/]+/read', path):
        return 'attention.read'
    if method == "POST" and re.fullmatch(r"/tasks/[^/]+/complete", path):
        return "tasks.complete"
    if method == "POST" and re.fullmatch(r"/threads/[^/]+/resume", path):
        return "chat.resume"
    if method == "GET" and re.fullmatch(r"/threads/[^/]+", path):
        return "chat.history"
    if method == "GET" and re.fullmatch(r"/messages/[^/]+", path):
        return "messages.detail"
    if method == "GET" and re.fullmatch(r"/documents/[^/]+/file", path):
        return "documents.file"
    if method == "GET" and re.fullmatch(r"/documents/[^/]+", path):
        return "documents.detail"
    if method == "PUT" and re.fullmatch(r"/reminders/[^/]+", path):
        return "reminders.update"
    if method == "POST" and re.fullmatch(r"/reminders/[^/]+/cancel", path):
        return "reminders.cancel"
    return "http.request"


class RequestTraceMiddleware:
    """用进程内ContextVar关联节点，存储失败降级；业务异常和返回正文保持原处理方式。"""

    def __init__(self, app, owner):
        """保存下游ASGI应用与拥有生命周期状态的FastAPI对象。"""
        self.app, self.owner = app, owner

    async def __call__(self, scope, receive, send):
        """为业务HTTP请求生成追踪编号；健康检查和追踪查询不再次创建追踪。"""
        path = scope.get("path", "")
        store = getattr(self.owner.state, "trace_store", None)
        if scope["type"] != "http" or store is None or path == "/health" or path == "/traces" or path.startswith("/traces/"):
            await self.app(scope, receive, send)
            return
        trace_id, http_status, error_code = uuid4().hex, 500, None
        try:
            await asyncio.to_thread(store.start_trace, trace_id, operation_name(scope["method"], path))
        except Exception as error:
            safe_diagnostic(error)

        async def send_traced(message):
            """在响应头加入本次编号，保留原JSON正文和已有的业务协议。"""
            nonlocal http_status
            if message["type"] == "http.response.start":
                http_status = message["status"]
                message = dict(message)
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", []) if key.lower() != b"x-trace-id"
                ] + [(b"x-trace-id", trace_id.encode("ascii"))]
            await send(message)

        try:
            with use_trace(store, trace_id), span("http.request") as current:
                await self.app(scope, receive, send_traced)
                if http_status >= 400:
                    current.fail(f"HTTP{http_status}")
        except asyncio.CancelledError:
            error_code = "CancelledError"
            raise
        except Exception as error:
            error_code = type(error).__name__
            raise
        finally:
            try:
                detail = await asyncio.to_thread(store.get_trace, trace_id)
                failures = [item for item in (detail or {}).get("spans", []) if item["status"] == "error"]
                failed = next((item for item in failures if item["name"] != "http.request"), None) or next(iter(failures), None)
                error_code = error_code or (failed or {}).get("error_code") or (f"HTTP{http_status}" if http_status >= 400 else None)
                await asyncio.to_thread(
                    store.finish_trace, trace_id, "error" if error_code else "completed", error_code, http_status
                )
            except Exception as error:
                # 追踪写失败不能覆盖本来的业务异常或已发送响应。
                safe_diagnostic(error)
