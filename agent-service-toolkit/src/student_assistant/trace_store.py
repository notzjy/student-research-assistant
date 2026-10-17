"""仅保存白名单运行元数据的本地Trace存储；记录故障与业务操作隔离。"""

import logging
import math
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path


TRACE_STATUSES = frozenset({"running", "completed", "error"})
ALLOWED_OPERATIONS = frozenset({
    "http.request", "chat.invoke", "chat.resume", "chat.history", "evaluation.run",
    "messages.ingest", "messages.list", "messages.detail", "roles.read", "roles.write",
    "documents.index", "documents.list", "documents.detail", "documents.file",
    "documents.search", "documents.ask", "reminders.create", "reminders.list",
    "reminders.update", "reminders.cancel", "reminders.plan", "reminders.run_once",
    "reminders.policy.read", "reminders.policy.write", "mail.status", "mail.jobs", "mail.sync",
    "tasks.complete",
    'messages.review', 'tasks.review', 'tasks.replan', 'documents.review', 'documents.classify',
    'attachments.upload', 'attachments.file', 'attention.list', 'attention.read', 'attention.policy.read',
    'attention.policy.write', 'notifications.receive', 'notifications.status', 'notifications.configure',
})
ALLOWED_SPAN_NAMES = frozenset({
    "http.request", "evaluation.run", "agent.model", "agent.tools", "agent.report",
    "tool.save_task", "tool.list_tasks", "tool.complete_task", "tool.analyze_notice",
    "tool.interpret_time", "tool.search_documents", "message.receive", "message.analyze",
    "message.resolve_times", "message.persist", "document.receive", "document.extract",
    "document.chunk", "document.embed", "document.index", "document.search", "document.answer",
    "document.validate_citations", "reminder.plan", "reminder.deliver",
    "mail.collect", "mail.collect_recent", "mail.parse", "mail.process",
    'document.classify',
})
_IDENTIFIER = re.compile(r"[0-9a-f]{32}")
_ERROR_CODE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_LOGGER = logging.getLogger(__name__)


def validate_identifier(value: str) -> str:
    """只接受程序生成的32位小写十六进制编号，避免把原始输入写入标识字段。"""
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("Trace编号必须是32位小写十六进制字符串")
    return value


def validate_error_code(value: str | None) -> str | None:
    """错误字段仅允许异常类型形式的短标识，不接受错误正文、路径或凭证。"""
    if value is not None and (not isinstance(value, str) or _ERROR_CODE.fullmatch(value) is None):
        raise ValueError("Trace错误码必须是安全类型标识")
    return value


def safe_diagnostic(error: Exception) -> None:
    """只输出固定诊断事件和异常类型；日志设施自身失败也不影响业务。"""
    try:
        error_type = type(error).__name__
        if _ERROR_CODE.fullmatch(error_type) is None:
            error_type = "Exception"
        _LOGGER.warning("trace_recording_failed error_type=%s", error_type)
    except Exception:
        pass


class TraceStore:
    """在同一业务库保存Trace和Span的有限元数据，短连接写入失败时安全降级。"""

    def __init__(self, task_store):
        """复用传入业务存储的数据库路径建表，不访问用户原文或模型配置。"""
        self.db_path = Path(task_store.db_path).resolve()
        try:
            with self._connection(write=True) as connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS assistant_traces (
                        trace_id TEXT PRIMARY KEY NOT NULL,
                        operation TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'error')),
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        elapsed_ms REAL,
                        http_status INTEGER,
                        error_code TEXT
                    )
                """)
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS assistant_trace_spans (
                        trace_id TEXT NOT NULL REFERENCES assistant_traces(trace_id),
                        span_id TEXT NOT NULL,
                        parent_span_id TEXT,
                        name TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'error')),
                        elapsed_ms REAL NOT NULL CHECK(elapsed_ms >= 0),
                        error_code TEXT,
                        started_at TEXT,
                        PRIMARY KEY(trace_id, span_id)
                    )
                """)
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(assistant_trace_spans)")}
                if "started_at" not in columns:
                    connection.execute("ALTER TABLE assistant_trace_spans ADD COLUMN started_at TEXT")
        except Exception as error:
            safe_diagnostic(error)

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """单次写入使用短BEGIN IMMEDIATE事务，低超时避免Trace长期阻塞业务库。"""
        with closing(sqlite3.connect(self.db_path, timeout=0.1, autocommit=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
            with connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection

    @staticmethod
    def _status(status: str) -> str:
        """限制状态为running、completed或error，不接受任意业务输出。"""
        if not isinstance(status, str) or status not in TRACE_STATUSES:
            raise ValueError("Trace状态不符合约定")
        return status

    @staticmethod
    def _elapsed(elapsed_ms: float) -> float:
        """检查耗时是有限非负数，拒绝布尔值、NaN和无穷值。"""
        if isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, (int, float)):
            raise ValueError("Trace耗时必须是有限非负数")
        try:
            value = float(elapsed_ms)
        except OverflowError:
            raise ValueError("Trace耗时必须是有限非负数") from None
        if not math.isfinite(value) or value < 0:
            raise ValueError("Trace耗时必须是有限非负数")
        return value

    def start_trace(self, trace_id: str, operation: str) -> None:
        """以running建立Trace；相同编号不覆盖既有记录，存储故障只记录安全诊断。"""
        validate_identifier(trace_id)
        if not isinstance(operation, str) or operation not in ALLOWED_OPERATIONS:
            raise ValueError("Trace操作不在白名单内")
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            with self._connection(write=True) as connection:
                connection.execute("""
                    INSERT INTO assistant_traces(trace_id, operation, status, started_at)
                    VALUES (?, ?, 'running', ?) ON CONFLICT(trace_id) DO NOTHING
                """, (trace_id, operation, started_at))
        except Exception as error:
            safe_diagnostic(error)

    def finish_trace(
        self, trace_id: str, status: str, error_code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        """只将running结束为completed或error，重复结束不覆盖首次结果。"""
        validate_identifier(trace_id)
        self._status(status)
        if status == "running":
            raise ValueError("结束Trace必须指定completed或error")
        validate_error_code(error_code)
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            raise ValueError("HTTP状态必须在100至599之间")
        finished_at = datetime.now(timezone.utc)
        try:
            with self._connection(write=True) as connection:
                row = connection.execute(
                    "SELECT started_at FROM assistant_traces WHERE trace_id=? AND status='running'", (trace_id,),
                ).fetchone()
                if row is None:
                    return
                elapsed_ms = max(0.0, (finished_at - datetime.fromisoformat(row["started_at"])).total_seconds() * 1000)
                connection.execute("""
                    UPDATE assistant_traces SET status=?, finished_at=?, elapsed_ms=?, http_status=?, error_code=?
                    WHERE trace_id=? AND status='running'
                """, (status, finished_at.isoformat(), elapsed_ms, http_status, error_code, trace_id))
        except Exception as error:
            safe_diagnostic(error)

    def write_span(
        self, trace_id: str, span_id: str, parent_span_id: str | None, name: str,
        status: str, elapsed_ms: float, error_code: str | None = None,
        started_at: str | None = None,
    ) -> None:
        """追加Span白名单字段，不接收参数、消息正文、路径或提供商原始响应。"""
        validate_identifier(trace_id)
        validate_identifier(span_id)
        if parent_span_id is not None:
            validate_identifier(parent_span_id)
        if not isinstance(name, str) or name not in ALLOWED_SPAN_NAMES:
            raise ValueError("Span名称不在白名单内")
        self._status(status)
        elapsed_ms = self._elapsed(elapsed_ms)
        validate_error_code(error_code)
        if started_at is not None:
            try:
                if not isinstance(started_at, str) or len(started_at) > 40:
                    raise ValueError
                moment = datetime.fromisoformat(started_at)
                if moment.utcoffset() is None:
                    raise ValueError
                started_at = moment.astimezone(timezone.utc).isoformat()
            except (ValueError, OverflowError):
                raise ValueError("Span开始时间必须是带时区的ISO时间") from None
        try:
            with self._connection(write=True) as connection:
                connection.execute("""
                    INSERT INTO assistant_trace_spans
                        (trace_id, span_id, parent_span_id, name, status, elapsed_ms, error_code, started_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(trace_id, span_id) DO NOTHING
                """, (trace_id, span_id, parent_span_id, name, status, elapsed_ms, error_code, started_at))
        except Exception as error:
            safe_diagnostic(error)

    def get_trace(self, trace_id: str) -> dict | None:
        """读取Trace及其Span元数据；未知编号或存储不可用返回None。"""
        validate_identifier(trace_id)
        try:
            with self._connection() as connection:
                row = connection.execute("SELECT * FROM assistant_traces WHERE trace_id=?", (trace_id,)).fetchone()
                if row is None:
                    return None
                spans = connection.execute("""
                    SELECT span_id, parent_span_id, name, status, elapsed_ms, error_code, started_at
                    FROM assistant_trace_spans WHERE trace_id=? ORDER BY COALESCE(started_at, '9999'), rowid
                """, (trace_id,)).fetchall()
                return {**dict(row), "spans": [dict(span) for span in spans]}
        except Exception as error:
            safe_diagnostic(error)
            return None

    def list_traces(self, limit: int = 50) -> list[dict]:
        """按创建顺序倒序返回有限Trace摘要，单条Span通过详情接口读取。"""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Trace列表limit必须在1至100之间")
        try:
            with self._connection() as connection:
                return [dict(row) for row in connection.execute(
                    "SELECT * FROM assistant_traces ORDER BY rowid DESC LIMIT ?", (limit,),
                ).fetchall()]
        except Exception as error:
            safe_diagnostic(error)
            return []
