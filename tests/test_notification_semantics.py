"""手机通知语义回归：真实消息时间、聚合历史隔离、投递前来源校验及模型故障到达提醒。"""

import copy
import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)
service = importlib.import_module("student_assistant.service")
gateway = importlib.import_module("student_assistant.notification_gateway")
workflow = importlib.import_module("student_assistant.message_workflow")
MailSettings = importlib.import_module("student_assistant.mail_ingest").MailSettings


class SemanticNotificationModel:
    """故意为聚合通知也返回旧行动，确保程序门控不依赖模型遵守提示。"""

    def __init__(self):
        """初始化调用记录与可选故障，不创建实际模型客户端。"""
        self.calls = []
        self.failure = None
        self.schema = None

    def with_structured_output(self, schema, *, method):
        """接收生产业务schema，返回可记录输入的本地替身。"""
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        """返回合法但可能不符合聚合语义的行动，或模拟提供商异常。"""
        payload = json.loads(messages[-1][1])
        self.calls.append(copy.deepcopy(payload))
        if self.failure:
            raise self.failure
        return self.schema.model_validate({
            "category": "研究", "summary": "通知包含组会安排。", "attachment_description": None,
            "attachment_count": 0, "events": [{
                "title": "组会", "time_text": "明天下午三点", "location": None,
                "evidence": "明天下午三点开组会",
                "time_extraction": {"intent": {"unit": "day", "offset": 1, "hour": 15, "minute": 0, "is_deadline": False}, "reason": None},
            }],
        })


class SemanticEmbeddings:
    """为服务初始化提供离线向量空间，本组测试不会索引或检索真实文档。"""

    model_id = "notification-semantics-offline-v1"
    dimension = 3

    def embed_documents(self, texts):
        """只返回固定非零向量，避免意外加载真实本地模型。"""
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, query):
        """保持查询向量与文档空间一致，不执行网络或模型推理。"""
        return [1.0, 0.0, 0.0]


class NotificationSemanticsTests(unittest.TestCase):
    """用临时真实业务库验收通知时间与来源可靠性，所有外部副作用均关闭。"""

    def setUp(self):
        """固定2029年合成业务时钟，避免系统日期变化影响未来提醒与到达新鲜度。"""
        temporary = tempfile.TemporaryDirectory(prefix="student_notification_semantics_")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.now = datetime(2029, 10, 17, 2, 5, tzinfo=timezone.utc)
        self.model = SemanticNotificationModel()
        self.notifier = Mock()

    def clock(self):
        """返回北京时间10:05，合成消息发送于10:00且次日下午提醒仍未到期。"""
        return self.now

    def client(self, *, missing_model=False):
        """启动真实服务生命周期，关闭后台、实际邮箱与操作系统通知。"""
        factory = None if missing_model else Mock(return_value=self.model)
        app = service.create_app(
            business_db=self.folder / "business.sqlite3", checkpoint_db=self.folder / "checkpoint.sqlite3",
            rag_dir=self.folder / "rag", model_factory=factory, embedding_factory=SemanticEmbeddings,
            notifier_factory=Mock(return_value=self.notifier), mail_settings=MailSettings(), background_enabled=False,
        )
        client = self.enterContext(TestClient(app))
        app.state.reminder_runtime.clock = self.clock
        app.state.attention_runtime.clock = self.clock
        response = client.put("/reminders/policy", json={"enabled": True, "lead_minutes": 30, "date_only_time": None, "channel": "console"})
        self.assertEqual(response.status_code, 200)
        return client

    def payload(self, **changes):
        """构造通知文本与实际消息时间可独立变化的合成输入。"""
        result = {
            "device_id": "semantic-phone", "package_name": "com.tencent.mm", "notification_key": "conversation-1",
            "posted_at": "2029-10-17T10:00:00+08:00", "title": "合成导师",
            "text": "明天下午三点开组会。", "conversation_id": "teacher-conversation",
            "text_source": "notification_text", "message_sent_at": None,
        }
        result.update(changes)
        return result

    def enqueue(self, client, payload, *, trust=False):
        """显式配置接入并202持久接收，不自动运行模型队列。"""
        configured = client.post("/notifications/config", json={"enabled": True, "rotate": False, "trust_posted_time": trust})
        self.assertEqual(configured.status_code, 200)
        token = configured.json()["token"]
        response = client.post("/notifications", json=payload, headers={"Authorization": "Bearer " + token})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def run_queue(self, client):
        """在服务自己的异步循环中显式处理本地队列，不运行通知投递循环。"""
        return client.portal.call(client.app.state.notification_runtime.run_once)

    def important_role(self, client, payload):
        """仅为用户明确配置的设备/应用/会话标识设置重要角色，不猜测真实发送人。"""
        model = gateway.NotificationInput.model_validate(payload)
        envelope = gateway.NotificationRuntime.envelope(model, gateway.NotificationStore.identity(model))
        response = client.put("/roles", json={
            "source": envelope.source, "account_id": envelope.account_id,
            "sender_id": envelope.sender_id, "role": "导师", "important": True,
        })
        self.assertEqual(response.status_code, 200)
        return envelope

    def test_latest_message_uses_actual_timestamp_and_stable_identity(self):
        """最新消息忽略旧聚合内容与通知刷新时刻，但真实新文本或新发送时间生成新身份。"""
        original = gateway.NotificationInput.model_validate(self.payload(
            text_source="latest_message", message_sent_at="2029-10-16T10:00:00+08:00",
            big_text="旧消息：明天下午三点开组会", text_lines=["历史通知"],
        ))
        identity = gateway.NotificationStore.identity(original)
        refreshed = gateway.NotificationInput.model_validate({
            **original.model_dump(), "posted_at": "2029-10-19T10:00:00+08:00",
            "big_text": "另一组历史内容", "text_lines": ["旧行变化"], "notification_key": "new-platform-key",
        })
        self.assertEqual(gateway.NotificationStore.identity(refreshed), identity)
        envelope = gateway.NotificationRuntime.envelope(refreshed, identity)
        self.assertEqual(envelope.sent_at, original.message_sent_at)
        self.assertEqual(envelope.text, original.text)
        for changes in ({"text": "明天下午三点开组会，请带论文。"}, {"message_sent_at": "2029-10-16T10:01:00+08:00"}):
            changed = gateway.NotificationInput.model_validate({**original.model_dump(), **changes})
            self.assertNotEqual(gateway.NotificationStore.identity(changed), identity)
        for changes in ({"message_sent_at": "2029-10-16T10:00:00"}, {"text_source": "unrecognized"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                gateway.NotificationInput.model_validate({**original.model_dump(), **changes})

    def test_metadata_is_visible_before_job_link_and_aggregate_cannot_create_events(self):
        """通知任务尚未回填message_id时就能取元数据，模型返回历史行动也被确定性清空。"""
        client = self.client()
        queued = self.enqueue(client, self.payload(text_source="aggregate", big_text="历史内容：明天下午三点开组会；最新内容：请查收材料。"))
        observed = []
        original_hook = client.app.state.message_processor.after_process

        async def observe_before_hook(message):
            """在正式提醒钩子执行前验证JOIN可读元数据且队列外键还未回填。"""
            metadata = client.app.state.notification_store.metadata(message["message_id"])
            with closing(sqlite3.connect(self.folder / "business.sqlite3")) as connection:
                linked = connection.execute("SELECT message_id FROM notification_jobs WHERE notification_id=?", (queued["notification_id"],)).fetchone()[0]
            observed.append((metadata, linked))
            return await original_hook(message)

        client.app.state.message_processor.after_process = observe_before_hook
        self.assertEqual(self.run_queue(client), {"processed": 1, "failed": 0})
        self.assertEqual(observed[0][0]["text_source"], "aggregate")
        self.assertIsNone(observed[0][1])
        self.assertEqual(self.model.calls[0]["source_metadata"]["text_source"], "aggregate")
        message = client.get("/messages").json()["messages"][0]
        self.assertEqual(message["analysis"]["events"], [])
        self.assertEqual(message["events"], [])
        self.assertEqual(client.get("/tasks").json()["count"], 0)
        self.assertEqual(client.get("/reminders").json()["count"], 0)

    def test_unverified_posted_time_blocks_default_planning_until_explicit_trust(self):
        """仅有通知发布时间的相对事项默认留供核对，用户明确选择后新通知才自动规划。"""
        client = self.client()
        self.enqueue(client, self.payload())
        self.assertEqual(self.run_queue(client), {"processed": 1, "failed": 0})
        messages = client.get("/messages").json()["messages"]
        self.assertEqual(len(messages[0]["events"]), 1)
        self.assertEqual(messages[0]["source_metadata"]["time_basis"], "notification_posted")
        self.assertEqual(client.get("/reminders").json()["count"], 0)
        self.enqueue(client, self.payload(notification_key="conversation-2"), trust=True)
        self.assertEqual(self.run_queue(client), {"processed": 1, "failed": 0})
        reminders = client.get("/reminders").json()["reminders"]
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["remind_at"], "2029-10-18T14:30:00+08:00")
        self.assertTrue(client.get("/notifications/status").json()["trust_posted_time"])
        self.notifier.send.assert_not_called()

    def test_app_timestamp_plans_relative_event_from_original_message_date(self):
        """应用给出真实发送时刻时正常规划，通知刷新为后天也不挪动原消息明天的日期。"""
        client = self.client()
        self.enqueue(client, self.payload(
            text_source="latest_message", message_sent_at="2029-10-17T10:00:00+08:00",
            posted_at="2029-10-19T10:00:00+08:00", big_text="旧聚合消息内容",
        ))
        self.assertEqual(self.run_queue(client), {"processed": 1, "failed": 0})
        message = client.get("/messages").json()["messages"][0]
        self.assertEqual(message["source_metadata"]["time_basis"], "app_message_timestamp")
        self.assertEqual(message["events"][0]["time"]["at"], "2029-10-18T15:00:00+08:00")
        self.assertEqual(message["events"][0]["time"]["reference_time"], "2029-10-17T10:00:00+08:00")
        self.assertEqual(client.get("/reminders").json()["reminders"][0]["remind_at"], "2029-10-18T14:30:00+08:00")

    def test_important_arrival_survives_model_failure_and_repeated_processing(self):
        """重点到达在模型失败前落盘，同一消息再次处理也不会重建或丢失原到达记录。"""
        client = self.client()
        payload = self.payload()
        envelope = self.important_role(client, payload)
        self.enqueue(client, payload)
        self.model.failure = RuntimeError("synthetic provider error")
        self.assertEqual(self.run_queue(client), {"processed": 0, "failed": 1})
        alerts = client.get("/attention").json()["alerts"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("明天下午三点开组会", alerts[0]["title"])
        self.assertEqual(alerts[0]["status"], "pending")
        self.assertEqual(client.get("/messages").json()["messages"][0]["processing_status"], "failed")
        with self.assertRaises(workflow.MessageProcessingError):
            client.portal.call(client.app.state.message_processor.process, envelope)
        self.assertEqual(client.get("/attention").json()["alerts"], alerts)
        self.notifier.send.assert_not_called()

    def test_important_arrival_precedes_missing_model_configuration(self):
        """没有模型配置也先保存重点到达和原消息，重复API重试维持同一记录。"""
        self.enterContext(patch("student_assistant.message_workflow.model_is_configured", return_value=False))
        client = self.client(missing_model=True)
        client.put("/roles", json={"source": "manual", "account_id": "synthetic", "sender_id": "teacher-1", "role": "导师", "important": True})
        payload = {"source": "manual", "account_id": "synthetic", "source_message_id": "no-model-important", "sender_id": "teacher-1", "sent_at": "2029-10-17T10:00:00+08:00", "text": "重要研究通知：明天下午三点开组会。"}
        self.assertEqual(client.post("/messages", json=payload).status_code, 503)
        first = client.get("/attention").json()["alerts"]
        self.assertEqual(len(first), 1)
        self.assertEqual(client.post("/messages", json=payload).status_code, 503)
        self.assertEqual(client.get("/attention").json()["alerts"], first)
        message = client.get("/messages").json()["messages"][0]
        self.assertEqual(message["error_code"], "ModelConfigurationMissing")
        self.assertEqual(message["source_message"]["text"], payload["text"])
        self.notifier.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
