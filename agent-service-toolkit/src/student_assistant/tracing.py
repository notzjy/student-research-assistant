"""通过ContextVar传播父子Span，不把Trace上下文放入业务状态或checkpoint。"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from student_assistant.trace_store import ALLOWED_SPAN_NAMES, safe_diagnostic, validate_error_code


@dataclass(frozen=True)
class TraceContext:
    """保存当前调用链的存储、Trace编号和父Span编号；to_thread自动复制此上下文。"""

    store: object
    trace_id: str
    parent_span_id: str | None = None


@dataclass
class SpanHandle:
    """允许调用方把已捕获并转为结构化结果的业务错误显式标记为Span错误。"""

    status: str = "completed"
    error_code: str | None = None

    def fail(self, error_code: str = "ToolExecutionError") -> None:
        """标记安全错误类型；非法记录参数只降级诊断，不打断业务执行。"""
        self.status = "error"
        try:
            self.error_code = validate_error_code(error_code) or "SpanFailure"
        except Exception as error:
            self.error_code = "SpanFailure"
            safe_diagnostic(error)


current_trace: ContextVar[TraceContext | None] = ContextVar("student_assistant_trace", default=None)


@contextmanager
def use_trace(store, trace_id: str):
    """在当前执行上下文绑定Trace，退出后恢复上一层；并发请求互不覆盖。"""
    token = current_trace.set(TraceContext(store, trace_id))
    try:
        yield current_trace.get()
    finally:
        current_trace.reset(token)


@contextmanager
def span(name: str):
    """测量同步或异步函数中的代码块；异常仅记录类型并原样重抛，无Trace时直接执行。"""
    handle = SpanHandle()
    context = current_trace.get()
    if context is None:
        yield handle
        return
    if not isinstance(name, str) or name not in ALLOWED_SPAN_NAMES:
        safe_diagnostic(ValueError("Span名称不在白名单内"))
        yield handle
        return
    span_id = uuid4().hex
    token = current_trace.set(TraceContext(context.store, context.trace_id, span_id))
    started_at = datetime.now(timezone.utc).isoformat()
    started = perf_counter()
    try:
        yield handle
    except asyncio.CancelledError:
        # 取消属于BaseException；标记已中断并继续传播，不能伪记为正常完成。
        handle.fail("CancelledError")
        raise
    except Exception as error:
        # 异常优先于手工标记；绝不记录str(error)或模型响应内容。
        handle.fail(type(error).__name__)
        raise
    finally:
        current_trace.reset(token)
        try:
            context.store.write_span(
                context.trace_id, span_id, context.parent_span_id, name,
                handle.status, max(0.0, (perf_counter() - started) * 1000), handle.error_code,
                started_at=started_at,
            )
        except Exception as error:
            safe_diagnostic(error)
