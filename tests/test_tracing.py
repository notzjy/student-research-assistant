"""Trace基础验收：真实临时SQLite、嵌套调用、异步隔离及记录失败降级。"""

import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from student_assistant.bridge import TaskStore
from student_assistant.trace_store import TraceStore
from student_assistant.tracing import current_trace, span, use_trace


class TracingTests(unittest.TestCase):
    """只使用临时业务库验证记录元数据，不读取真实配置、消息或用户数据库。"""

    def setUp(self):
        """为每项测试创建独立临时业务和Trace表，避免状态串扰。"""
        self.directory = tempfile.TemporaryDirectory(prefix="student-tracing-")
        self.addCleanup(self.directory.cleanup)
        self.tasks = TaskStore(Path(self.directory.name) / "assistant.sqlite3")
        self.store = TraceStore(self.tasks)
        self.trace_id = uuid4().hex
        self.store.start_trace(self.trace_id, "chat.invoke")

    def test_lifecycle_persists_only_metadata(self):
        """真实重建存储后仍能读取状态和耗时，详情只含约定的白名单字段。"""
        with use_trace(self.store, self.trace_id), span("agent.model"):
            pass
        self.store.finish_trace(self.trace_id, "completed", http_status=200)
        detail = TraceStore(self.tasks).get_trace(self.trace_id)
        self.assertEqual(set(detail), {
            "trace_id", "operation", "status", "started_at", "finished_at", "elapsed_ms",
            "http_status", "error_code", "spans",
        })
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["http_status"], 200)
        self.assertGreaterEqual(detail["elapsed_ms"], 0)
        self.assertEqual(set(detail["spans"][0]), {
            "span_id", "parent_span_id", "name", "status", "elapsed_ms", "error_code", "started_at",
        })
        self.assertTrue(detail["spans"][0]["started_at"].endswith("+00:00"))

    def test_identifiers_names_and_error_codes_reject_raw_input(self):
        """非法编号和原文形态的操作、Span名及错误正文均在写库前被拒绝。"""
        for value in ("user message", "A" * 32, "a" * 31, "a" * 33, "g" * 32, None):
            with self.subTest(identifier=value), self.assertRaises(ValueError):
                self.store.start_trace(value, "chat.invoke")
        with self.assertRaises(ValueError):
            self.store.start_trace(uuid4().hex, "/invoke?secret=payload")
        with self.assertRaises(ValueError):
            self.store.write_span(self.trace_id, uuid4().hex, None, "private message", "completed", 1)
        with self.assertRaises(ValueError):
            self.store.finish_trace(self.trace_id, "error", "provider response with secret")
        self.assertEqual(self.store.get_trace(self.trace_id)["spans"], [])

    def test_status_duration_http_and_start_time_are_validated(self):
        """拒绝NaN、负耗时、布尔状态码和无时区开始时间，防止无效元数据落库。"""
        for elapsed in (-1, float("nan"), float("inf"), 10 ** 1000, True, "3"):
            with self.subTest(elapsed=elapsed), self.assertRaises(ValueError):
                self.store.write_span(self.trace_id, uuid4().hex, None, "agent.model", "completed", elapsed)
        for status in ("success", "running", None):
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.store.finish_trace(self.trace_id, status)
        for status in (True, 99, 600, "200"):
            with self.subTest(http_status=status), self.assertRaises(ValueError):
                self.store.finish_trace(self.trace_id, "completed", http_status=status)
        with self.assertRaises(ValueError):
            self.store.write_span(self.trace_id, uuid4().hex, None, "agent.model", "completed", 1,
                                  started_at="2026-10-16T10:00:00")

    def test_nested_spans_manual_error_and_exception_precedence(self):
        """嵌套Span具有真实父子关系；抛出的异常类型优先于工具手工错误标记。"""
        secret = "private provider output must not persist"
        with use_trace(self.store, self.trace_id):
            with span("agent.tools"):
                with span("tool.list_tasks") as current:
                    current.fail("ToolExecutionError")
                with self.assertRaises(OSError) as caught:
                    with span("tool.save_task") as current:
                        current.fail("ToolExecutionError")
                        raise OSError(secret)
        self.assertEqual(str(caught.exception), secret)
        detail = self.store.get_trace(self.trace_id)
        spans = detail["spans"]
        self.assertEqual([item["name"] for item in spans], ["agent.tools", "tool.list_tasks", "tool.save_task"])
        self.assertTrue(all(item["parent_span_id"] == spans[0]["span_id"] for item in spans[1:]))
        self.assertEqual(spans[1]["error_code"], "ToolExecutionError")
        self.assertEqual(spans[2]["error_code"], "OSError")
        self.assertNotIn(secret, json.dumps(detail))

    def test_nested_trace_context_resets_even_after_business_exception(self):
        """切换另一条Trace后恢复外层上下文，业务异常退出后没有残留Trace。"""
        second = uuid4().hex
        self.store.start_trace(second, "mail.sync")
        with self.assertRaises(RuntimeError):
            with use_trace(self.store, self.trace_id):
                with span("agent.model"):
                    original = current_trace.get()
                    with use_trace(self.store, second):
                        with span("mail.collect"):
                            self.assertEqual(current_trace.get().trace_id, second)
                    self.assertEqual(current_trace.get(), original)
                    raise RuntimeError("business remains visible")
        self.assertIsNone(current_trace.get())
        self.assertEqual(self.store.get_trace(self.trace_id)["spans"][0]["status"], "error")
        self.assertIsNone(self.store.get_trace(second)["spans"][0]["parent_span_id"])

    def test_async_tasks_and_to_thread_preserve_separate_parents(self):
        """两条并发异步请求及其工作线程分别继承自己的父Span，不串到另一会话。"""
        second = uuid4().hex
        self.store.start_trace(second, "documents.search")

        def record_in_thread():
            """在线程内创建子Span，用真实SQLite验证ContextVar已由to_thread传播。"""
            with span("document.embed"):
                return current_trace.get().trace_id

        async def run_both():
            """通过事件交叉执行两条请求，使上下文隔离检查包含实际并发等待。"""
            first_ready, second_ready = asyncio.Event(), asyncio.Event()

            async def worker(trace_id, mine, other):
                """绑定一条请求并等待另一条进入，再把上下文复制到工作线程。"""
                with use_trace(self.store, trace_id), span("document.search"):
                    mine.set()
                    await other.wait()
                    self.assertEqual(await asyncio.to_thread(record_in_thread), trace_id)

            await asyncio.gather(
                worker(self.trace_id, first_ready, second_ready),
                worker(second, second_ready, first_ready),
            )

        asyncio.run(run_both())
        for trace_id in (self.trace_id, second):
            spans = self.store.get_trace(trace_id)["spans"]
            self.assertEqual(len(spans), 2)
            self.assertEqual(spans[1]["parent_span_id"], spans[0]["span_id"])
        self.assertIsNone(current_trace.get())

    def test_no_trace_is_noop_and_does_not_swallow_business_error(self):
        """后台未绑定Trace时不写入记录，手工失败仍可调用且业务异常照常传播。"""
        with span("mail.process") as current:
            current.fail("ToolExecutionError")
        with self.assertRaises(ZeroDivisionError):
            with span("agent.model"):
                raise ZeroDivisionError("private input")
        self.assertEqual(self.store.get_trace(self.trace_id)["spans"], [])

    def test_recording_failure_never_replaces_result_or_business_error(self):
        """记录器抛错时正常代码继续返回，失败代码仍抛出原业务异常且诊断无正文。"""
        broken = Mock()
        broken.write_span.side_effect = OSError("sensitive storage path")
        with self.assertLogs("student_assistant.trace_store", level="WARNING") as logs:
            with use_trace(broken, self.trace_id):
                with span("agent.model"):
                    result = 42
                with self.assertRaisesRegex(ValueError, "business original"):
                    with span("agent.tools"):
                        raise ValueError("business original")
        self.assertEqual(result, 42)
        self.assertNotIn("sensitive storage path", " ".join(logs.output))
        self.assertNotIn("business original", " ".join(logs.output))
        self.assertIsNone(current_trace.get())

    def test_sqlite_write_and_initialization_failures_are_safe(self):
        """真实存储入口面对数据库故障安全降级，日志不含内部路径或错误正文。"""
        secret = "private database and provider text"
        with patch("student_assistant.trace_store.sqlite3.connect", side_effect=sqlite3.OperationalError(secret)):
            with self.assertLogs("student_assistant.trace_store", level="WARNING") as logs:
                broken = TraceStore(self.tasks)
                broken.start_trace(uuid4().hex, "chat.invoke")
                broken.finish_trace(self.trace_id, "error", "OSError", 500)
                broken.write_span(self.trace_id, uuid4().hex, None, "agent.model", "completed", 1)
                self.assertIsNone(broken.get_trace(self.trace_id))
                self.assertEqual(broken.list_traces(), [])
        self.assertNotIn(secret, " ".join(logs.output))
        self.assertEqual(self.store.get_trace(self.trace_id)["status"], "running")

    def test_span_validation_failure_is_isolated_from_business(self):
        """非法Span名称和错误标记只降级诊断，不让记录参数校验破坏业务操作。"""
        with self.assertLogs("student_assistant.trace_store", level="WARNING"):
            with use_trace(self.store, self.trace_id):
                with span("raw query?private=true"):
                    result = 7
                with span("agent.tools") as current:
                    current.fail("private error body")
        self.assertEqual(result, 7)
        spans = self.store.get_trace(self.trace_id)["spans"]
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["error_code"], "SpanFailure")

    def test_no_transaction_is_held_while_business_runs(self):
        """Span包住业务时不持有SQLite事务，另一个连接可以立即开始写入并提交。"""
        with use_trace(self.store, self.trace_id), span("agent.model"):
            with closing(sqlite3.connect(self.tasks.db_path, timeout=0.1, autocommit=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("UPDATE tasks SET status='pending'")
                connection.execute("COMMIT")
            self.tasks.save_task("synthetic task")
        self.assertEqual(len(self.tasks.list_tasks()), 1)
        self.assertEqual(len(self.store.get_trace(self.trace_id)["spans"]), 1)

    def test_listing_limits_and_duplicate_lifecycle_do_not_rewrite_history(self):
        """列表有界且倒序，重复start和finish不能覆盖首次记录或生成虚构Trace。"""
        second = uuid4().hex
        self.store.start_trace(second, "mail.sync")
        self.assertEqual(self.store.list_traces(1)[0]["trace_id"], second)
        self.store.start_trace(self.trace_id, "documents.index")
        self.store.finish_trace(self.trace_id, "completed", http_status=200)
        self.store.finish_trace(self.trace_id, "error", "OSError", 500)
        self.store.finish_trace(uuid4().hex, "completed")
        detail = self.store.get_trace(self.trace_id)
        self.assertEqual((detail["operation"], detail["status"], detail["http_status"]), ("chat.invoke", "completed", 200))
        self.assertEqual(len(self.store.list_traces()), 2)
        self.assertIsNone(self.store.get_trace(uuid4().hex))
        for limit in (0, 101, True, "3"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.list_traces(limit)


if __name__ == "__main__":
    unittest.main()
