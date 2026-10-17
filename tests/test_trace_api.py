"""Trace接口集成验证：真实ASGI、业务SQLite和追踪存储，模型与向量使用离线替身。"""

import asyncio
import contextlib
import copy
import importlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

service = importlib.import_module("student_assistant.service")
assistant_client = importlib.import_module("assistant_client")
from student_assistant.mail_ingest import MailSettings
from student_assistant.tracing import current_trace, span, use_trace
from tests.test_document_rag import FakeEmbeddings
from tests.test_message_pipeline import AnalysisModel


class ConcurrentAnalysisModel(AnalysisModel):
    """让两条消息同时到达模型阶段，检验实际并发请求的Trace隔离。"""

    def __init__(self, response, gate):
        """保存固定分析和共享异步关卡，不创建网络或线程资源。"""
        super().__init__(response)
        self.gate = gate

    async def ainvoke(self, messages):
        """等另一请求进入后返回合成分析，使两条请求的上下文真实交错。"""
        self.gate["count"] += 1
        if self.gate["count"] == 2:
            self.gate["ready"].set()
        await asyncio.wait_for(self.gate["ready"].wait(), timeout=5)
        return await super().ainvoke(messages)


class TraceApiTests(unittest.TestCase):
    """在独立临时库测试追踪协议、隐私、失败隔离和客户端参数约束。"""

    def setUp(self):
        """准备带敏感哨兵的合成消息和模型结果，绝不读取真实配置或业务库。"""
        temporary = tempfile.TemporaryDirectory(prefix="student-trace-api-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source_sentinel = "trace-source-sentinel-do-not-store"
        self.provider_secret = "provider-secret-never-record"
        self.envelope = {
            "source": "manual", "account_id": "trace-synthetic-account",
            "source_message_id": "trace-synthetic-message-001",
            "sender_id": "trace-synthetic-sender", "sender_name": "示例老师",
            "sent_at": "2026-10-16T10:00:00+08:00",
            "text": self.source_sentinel + "。明天下午三点开组会。", "attachments": [],
        }
        self.analysis = {
            "category": "研究", "summary": self.provider_secret,
            "attachment_description": "无附件", "attachment_count": 0,
            "events": [{
                "title": "组会", "time_text": "明天下午三点", "location": None,
                "evidence": "明天下午三点开组会",
                "time_extraction": {"intent": {
                    "unit": "day", "offset": 1, "weekday": None, "hour": 15,
                    "minute": 0, "is_deadline": False,
                }, "reason": None},
            }],
        }

    def make_app(self, model_factory=None):
        """注入离线模型和向量器，关闭后台任务并明确使用空邮箱设置。"""
        factory = model_factory if model_factory is not None else Mock(side_effect=self.new_model)
        return service.create_app(
            business_db=self.folder / "business.sqlite3",
            checkpoint_db=self.folder / "checkpoint.sqlite3",
            model_factory=factory, embedding_factory=FakeEmbeddings,
            mail_settings=MailSettings(), background_enabled=False,
        )

    def new_model(self):
        """为一条合成消息创建独立模型替身，不共享可变结构化输出协议。"""
        return AnalysisModel(copy.deepcopy(self.analysis))

    def trace_detail(self, client, response):
        """从响应头核对Trace编号，读取已完成的追踪详情而不依赖业务正文新增字段。"""
        trace_id = response.headers.get("X-Trace-ID")
        self.assertRegex(trace_id, r"^[0-9a-f]{32}$")
        detail_response = client.get(f"/traces/{trace_id}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertNotIn("X-Trace-ID", detail_response.headers)
        detail = detail_response.json()
        self.assertEqual(detail["trace_id"], trace_id)
        return detail

    def test_message_trace_has_nodes_without_raw_data_and_reads_do_not_trace(self):
        """成功请求有完整节点及安全元数据，健康与Trace查询不新增追踪记录。"""
        app = self.make_app()
        with TestClient(app) as client:
            self.assertEqual(client.get("/traces").json()["count"], 0)
            response = client.post(
                "/messages?private=trace-query-sentinel", json=self.envelope,
                headers={"Authorization": "Bearer " + self.provider_secret, "X-Trace-ID": self.source_sentinel},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "processed")
            self.assertIn(self.source_sentinel, response.json()["message"]["source_message"]["text"])
            detail = self.trace_detail(client, response)
            self.assertEqual((detail["operation"], detail["status"], detail["http_status"]), ("messages.ingest", "completed", 200))
            names = {item["name"] for item in detail["spans"]}
            self.assertTrue({"http.request", "message.receive", "message.analyze", "message.resolve_times",
                             "message.persist", "reminder.plan"}.issubset(names))
            serialized = json.dumps(detail, ensure_ascii=False)
            for secret in (self.source_sentinel, self.provider_secret, self.envelope["sent_at"],
                           self.envelope["account_id"], self.envelope["sender_id"], "trace-query-sentinel", "Authorization"):
                self.assertNotIn(secret, serialized)
            self.assertNotIn("X-Trace-ID", client.get("/health").headers)
            self.assertEqual(client.get("/traces/" + "f" * 32).status_code, 404)
            self.assertEqual(client.get("/traces/not-a-trace").status_code, 422)
            for _ in range(2):
                self.assertEqual(client.get("/traces").json()["count"], 1)
                self.assertEqual(client.get("/traces/" + detail["trace_id"]).status_code, 200)

    def test_duplicate_ingest_has_new_trace_without_reanalysis(self):
        """同来源重复导入复用业务结果，但形成独立Trace且不重复分析或补计划。"""
        factory = Mock(side_effect=self.new_model)
        with TestClient(self.make_app(factory)) as client:
            first = client.post("/messages", json=self.envelope)
            second = client.post("/messages", json=self.envelope)
            self.assertEqual((first.json()["status"], second.json()["status"]), ("processed", "exists"))
            self.assertEqual(first.json()["message"]["message_id"], second.json()["message"]["message_id"])
            self.assertNotEqual(first.headers["X-Trace-ID"], second.headers["X-Trace-ID"])
            detail = self.trace_detail(client, second)
            self.assertEqual(detail["status"], "completed")
            self.assertEqual({item["name"] for item in detail["spans"]}, {"http.request", "message.receive"})
            self.assertEqual(factory.call_count, 1)

    def test_model_error_is_located_without_provider_secret(self):
        """模型抛错仍保留原业务500协议，Trace定位analyze但不记录提供商错误正文。"""
        model = AnalysisModel(RuntimeError(self.provider_secret))
        with TestClient(self.make_app(Mock(return_value=model))) as client:
            response = client.post("/messages", json=self.envelope)
            self.assertEqual(response.status_code, 500)
            detail = self.trace_detail(client, response)
            self.assertEqual((detail["status"], detail["http_status"]), ("error", 500))
            analyze = next(item for item in detail["spans"] if item["name"] == "message.analyze")
            self.assertEqual((analyze["status"], analyze["error_code"]), ("error", "RuntimeError"))
            self.assertNotIn("message.persist", {item["name"] for item in detail["spans"]})
            self.assertNotIn(self.provider_secret, json.dumps(detail))
            self.assertNotIn(self.provider_secret, response.text)
            self.assertIsInstance(response.json()["detail"]["message_id"], str)

    def test_trace_storage_failure_does_not_change_successful_business(self):
        """start、Span和finish写入均失败时，原消息分析与业务提交仍成功。"""
        app = self.make_app()
        with TestClient(app) as client:
            store = app.state.trace_store
            with patch.object(store, "start_trace", side_effect=OSError(self.provider_secret)), \
                 patch.object(store, "write_span", side_effect=OSError(self.provider_secret)), \
                 patch.object(store, "finish_trace", side_effect=OSError(self.provider_secret)), \
                 self.assertLogs("student_assistant.trace_store", level="WARNING") as logs:
                response = client.post("/messages", json=self.envelope)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "processed")
            self.assertRegex(response.headers["X-Trace-ID"], r"^[0-9a-f]{32}$")
            message_id = response.json()["message"]["message_id"]
            self.assertEqual(app.state.message_store.get_message(message_id)["processing_status"], "completed")
            self.assertEqual(len(app.state.engine.store.list_tasks()), 1)
            self.assertNotIn(self.provider_secret, " ".join(logs.output))

    def test_concurrent_asgi_requests_keep_trace_and_parents_separate(self):
        """两条实际交错的ASGI请求具有独立Trace、独立Span编号及正确父子关系。"""
        async def run_requests():
            """在同一事件循环运行生命周期、并发模型关卡和两个HTTP请求。"""
            gate = {"count": 0, "ready": asyncio.Event()}

            def model_factory():
                """为每条并发消息建立模型实例，共享关卡仅用于精确交错执行。"""
                return ConcurrentAnalysisModel(copy.deepcopy(self.analysis), gate)

            app = self.make_app(model_factory)
            second_envelope = copy.deepcopy(self.envelope)
            second_envelope["source_message_id"] = "trace-synthetic-message-002"
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://trace.test") as client:
                    responses = await asyncio.gather(
                        client.post("/messages", json=self.envelope),
                        client.post("/messages", json=second_envelope),
                    )
                    self.assertEqual([response.status_code for response in responses], [200, 200])
                    trace_ids = [response.headers["X-Trace-ID"] for response in responses]
                    self.assertNotEqual(*trace_ids)
                    details = [app.state.trace_store.get_trace(trace_id) for trace_id in trace_ids]
                    all_ids = []
                    for detail in details:
                        self.assertEqual(detail["status"], "completed")
                        spans = detail["spans"]
                        ids = {item["span_id"] for item in spans}
                        all_ids.append(ids)
                        root = [item for item in spans if item["parent_span_id"] is None]
                        self.assertEqual(len(root), 1)
                        self.assertEqual(root[0]["name"], "http.request")
                        self.assertTrue(all(item["parent_span_id"] in ids for item in spans if item["parent_span_id"] is not None))
                        self.assertIn("message.analyze", {item["name"] for item in spans})
                    self.assertTrue(all_ids[0].isdisjoint(all_ids[1]))
                    self.assertEqual(gate["count"], 2)
                    self.assertEqual((await client.get("/traces")).json()["count"], 2)

        asyncio.run(run_requests())

    def test_trace_cli_rejects_invalid_or_mixed_context_before_http(self):
        """Trace客户端拒绝非法编号和聊天上下文混用，正确参数只请求只读接口。"""
        trace_id = "a" * 32
        invalid = [["--trace", value] for value in ("", "A" * 32, "a" * 31, "not-a-trace")]
        for mode in (["--traces"], ["--trace", trace_id]):
            invalid.extend(mode + extra for extra in (
                ["--thread-id", "study-a"], ["--reference-time", "2026-10-16T10:00:00+08:00"],
                ["--message-id", "source-id"], ["--message", "hello"],
            ))
        for arguments in invalid:
            with self.subTest(arguments=arguments), patch.object(sys, "argv", ["assistant_client.py", *arguments]), \
                 patch("assistant_client.httpx.Client") as factory, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    assistant_client.main()
                self.assertEqual(raised.exception.code, 2)
                factory.assert_not_called()
        for arguments, route in ((["--traces"], "/traces"), (["--trace", trace_id], "/traces/" + trace_id)):
            with patch.object(sys, "argv", ["assistant_client.py", *arguments]), \
                 patch("assistant_client.httpx.Client") as factory, contextlib.redirect_stdout(io.StringIO()):
                client = factory.return_value.__enter__.return_value
                client.get.return_value = httpx.Response(200, json={"traces": []})
                assistant_client.main()
                client.get.assert_called_once_with(route)

    def test_cancelled_coroutine_records_error_and_preserves_cancellation(self):
        """取消正在等待的协程后Span标为error，原CancelledError继续传播且上下文清理。"""
        async def run_cancelled_span():
            """使用临时服务的真实Trace存储，精确等待目标进入后发送取消信号。"""
            app = self.make_app()
            async with app.router.lifespan_context(app):
                store, trace_id = app.state.trace_store, uuid4().hex
                store.start_trace(trace_id, "messages.ingest")
                entered, hold = asyncio.Event(), asyncio.Event()

                async def waiting_operation():
                    """在可取消的业务等待中绑定Trace，不捕获或转换外部取消请求。"""
                    with use_trace(store, trace_id), span("message.analyze"):
                        entered.set()
                        await hold.wait()

                pending = asyncio.create_task(waiting_operation())
                await asyncio.wait_for(entered.wait(), timeout=5)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
                self.assertTrue(pending.cancelled())
                spans = store.get_trace(trace_id)["spans"]
                self.assertEqual(len(spans), 1)
                self.assertEqual((spans[0]["status"], spans[0]["error_code"]), ("error", "CancelledError"))
                self.assertIsNone(current_trace.get())

        asyncio.run(run_cancelled_span())


if __name__ == "__main__":
    unittest.main()
