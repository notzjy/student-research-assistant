"""自动化接口离线验收：真实 HTTP/SQLite/MIME/Qdrant，替换邮箱、模型、向量推理和通知边界。"""

import copy
import importlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

service = importlib.import_module("student_assistant.service")
from student_assistant.mail_ingest import ImapCollector, MailSettings


class StructuredNoticeModel:
    """仅模拟结构化通知提取，保留真实消息工作流、日历计算和数据库写入。"""

    def __init__(self, analysis):
        """保存合成分析并记录使用的输出 schema，不创建网络客户端。"""
        self.analysis = analysis
        self.schema = None

    def with_structured_output(self, schema, *, method):
        """接收生产代码的 Pydantic schema 和 JSON 模式约定。"""
        if method != "json_mode":
            raise AssertionError("通知应使用 JSON 结构化提取")
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        """返回经过真实schema校验的通知分析或正文分类，验证两次模型调用各自的职责。"""
        if self.schema is None:
            raise AssertionError("不应绕过结构化输出调用模型")
        payload = json.loads(messages[-1][1])
        if self.schema.__name__ == 'DocumentClassification':
            return self.schema.model_validate({'category': '研究', 'labels': ['检索实验'], 'summary': '研究文件设定Recall@5阈值。', 'evidence': [{'page': payload['pages'][0]['page'], 'quote': 'Recall@5 target is 0.85.'}]})
        result = copy.deepcopy(self.analysis)
        result["attachment_count"] = len(payload["attachments"])
        return self.schema.model_validate(result)


class OfflineEmbeddings:
    """只替换本地模型推理，实际文件解析和 Qdrant 存取仍由生产代码执行。"""

    model_id = "automation-offline-vector-v1"
    dimension = 3

    def __init__(self):
        """记录向量调用，便于验证重复邮箱同步没有再次索引。"""
        self.document_calls = []
        self.query_calls = []

    def embed_documents(self, texts):
        """为合成研究材料生成固定非零向量，不声称具有真实语义模型能力。"""
        self.document_calls.append(list(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, query):
        """为单文档检索提供同一空间的查询向量。"""
        self.query_calls.append(query)
        return [1.0, 0.0, 0.0]


class RecordingNotifier:
    """替换 Windows 和控制台投递，记录实际调用或模拟外部通道故障。"""

    def __init__(self):
        """初始化投递记录和可选故障，不向操作系统发送通知。"""
        self.calls = []
        self.failure = None

    def send(self, job):
        """记录一次投递尝试，按场景抛错以验证持久队列退避。"""
        self.calls.append(dict(job))
        if self.failure is not None:
            raise self.failure


class SingleMailIMAP:
    """只返回一封合成邮件的假 IMAP，不触碰网络或真实账号。"""

    def __init__(self, raw):
        """保存完整 MIME 字节和协议调用记录，固定 UID 命名空间。"""
        self.raw = raw
        self.calls = []

    def login(self, account, authorization):
        """模拟登录成功，避免将授权字符串加入可输出记录。"""
        self.calls.append("login")
        return "OK", []

    def select(self, mailbox, readonly=False):
        """要求只读打开 INBOX，防止入口测试放过会改邮箱状态的实现。"""
        if mailbox != "INBOX" or not readonly:
            raise AssertionError("邮箱必须只读打开 INBOX")
        self.calls.append("readonly")
        return "OK", [b"1"]

    def response(self, name):
        """返回合成 UIDVALIDITY，允许正式收取器建立可靠游标。"""
        return name, [b"12345"]

    def uid(self, command, *args):
        """提供大小元数据和 PEEK 原文，重复搜索仍返回旧 UID 以验证过滤。"""
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"7"]
        if command != "FETCH" or args[0] != "7":
            raise AssertionError("出现未预期的 IMAP 操作")
        if "RFC822.SIZE" in args[1]:
            return "OK", [f"1 (UID 7 RFC822.SIZE {len(self.raw)})".encode()]
        if args[1] != "(UID BODY.PEEK[])":
            raise AssertionError("不能以普通 BODY[] 修改已读状态")
        return "OK", [(f"1 (UID 7 BODY[] {{{len(self.raw)}}}".encode(), self.raw), b")"]

    def logout(self):
        """退出假连接；不提供 close/expunge 等会改邮箱状态的方法。"""
        self.calls.append("logout")
        return "BYE", []


class AutomationAPITests(unittest.TestCase):
    """通过六个 HTTP 场景覆盖显式计划、自动规则、可靠投递与邮箱到附件索引闭环。"""

    def setUp(self):
        """隔离所有业务、checkpoint、文档和 EML 数据，准备合成通知与可控外部依赖。"""
        temporary = tempfile.TemporaryDirectory(prefix="student_automation_api_")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.notifier = RecordingNotifier()
        self.embedding = OfflineEmbeddings()
        self.notifier_factory = Mock(return_value=self.notifier)
        self.embedding_factory = Mock(return_value=self.embedding)
        self.envelope = {
            "source": "manual", "account_id": "synthetic", "source_message_id": "notice-1",
            "sender_id": "teacher@example.com", "sender_name": "导师",
            "sent_at": "2026-10-16T10:00:00+08:00",
            "text": "明天下午三点在实验楼302开组会；周五前交综述，附件是一份研究笔记。",
            "attachments": [],
        }
        self.analysis = {
            "category": "研究", "summary": "通知组会和综述提交。",
            "attachment_description": None, "attachment_count": 0,
            "events": [
                {
                    "title": "开组会", "time_text": "明天下午三点", "location": "实验楼302",
                    "evidence": "明天下午三点在实验楼302开组会",
                    "time_extraction": {"intent": {
                        "unit": "day", "offset": 1, "weekday": None,
                        "hour": 15, "minute": 0, "is_deadline": False,
                    }, "reason": None},
                },
                {
                    "title": "交综述", "time_text": "周五前", "location": None,
                    "evidence": "周五前交综述",
                    "time_extraction": {"intent": {
                        "unit": "week", "offset": 0, "weekday": 4,
                        "hour": None, "minute": None, "is_deadline": True,
                    }, "reason": None},
                },
            ],
        }
        self.model_factory = Mock(side_effect=self.new_model)
        self.fake_imap = None
        self.now = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)

    def clock(self):
        """固定北京时间08:00，使同日09:00提醒和次日组会在任意运行日期都保持未来。"""
        return self.now

    def new_model(self):
        """为每次新消息分析提供独立模型替身，重复来源应跳过此工厂。"""
        return StructuredNoticeModel(self.analysis)

    def make_client(self, **options):
        """进入正式服务生命周期，关闭后台任务并注入邮箱、模型和通知边界。"""
        arguments = {
            "business_db": self.folder / "business.sqlite3",
            "checkpoint_db": self.folder / "checkpoints.sqlite3",
            "rag_dir": self.folder / "rag", "model_factory": self.model_factory,
            "embedding_factory": self.embedding_factory, "notifier_factory": self.notifier_factory,
            "mail_settings": MailSettings(), "background_enabled": False,
        }
        arguments.update(options)
        client = self.enterContext(TestClient(service.create_app(**arguments)))
        client.app.state.reminder_runtime.clock = self.clock
        return client

    def task(self, client, title="合成待办"):
        """通过真实业务存储准备一条待办，提醒测试不额外调用聊天模型。"""
        return client.app.state.engine.store.save_task(title)["task"]

    def reminder(self, response):
        """读取提醒操作直接返回的持久化详情，避免用模型文字推断实际状态。"""
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def policy(self, client, *, enabled=True, date_only_time=None):
        """通过公开接口显式选择自动规则，不直接修改内部 policy 表。"""
        response = client.put("/reminders/policy", json={
            "enabled": enabled, "lead_minutes": 30, "date_only_time": date_only_time, "channel": "console",
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def ingest(self, client, source_id="notice-1"):
        """将完整合成通知交给实际消息工作流，返回持久化消息详情。"""
        response = client.post("/messages", json={**self.envelope, "source_message_id": source_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def imap_factory(self, host, port, **kwargs):
        """把正式 IMAP 收取器的连接边界替换为同一合成邮箱。"""
        self.assertGreater(kwargs["timeout"], 0)
        return self.fake_imap

    def collector_factory(self, settings):
        """让服务使用实际 UID 收取逻辑，但不建立任何外部连接。"""
        return ImapCollector(settings, connection_factory=self.imap_factory)

    def test_manual_reminder_edit_conflict_cancel_and_missing_plan(self):
        """手动提醒可幂等创建、按版本编辑并取消，旧版本冲突和不存在计划有明确状态码。"""
        client = self.make_client()
        task = self.task(client)
        payload = {"task_id": task["id"], "remind_at": "2099-10-17T09:00:00+08:00", "channel": "console"}
        first = self.reminder(client.post("/reminders", json=payload))
        repeated = self.reminder(client.post("/reminders", json=payload))
        self.assertEqual(repeated["reminder_id"], first["reminder_id"])
        self.assertEqual(first["version"], 1)
        edited = self.reminder(client.put(f"/reminders/{first['reminder_id']}", json={
            "remind_at": "2099-10-17T10:00:00+08:00", "channel": "windows", "expected_version": 1,
        }))
        self.assertEqual((edited["version"], edited["channel"]), (2, "windows"))
        conflict = client.put(f"/reminders/{first['reminder_id']}", json={
            "remind_at": "2099-10-17T11:00:00+08:00", "channel": "console", "expected_version": 1,
        })
        self.assertEqual(conflict.status_code, 409, conflict.text)
        listing = client.get("/reminders")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["count"], 1)
        self.assertEqual(listing.json()["reminders"][0]["version"], 2)
        cancelled = self.reminder(client.post(f"/reminders/{first['reminder_id']}/cancel"))
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(client.post("/reminders/missing-plan/cancel").status_code, 404)
        self.assertEqual(client.put("/reminders/missing-plan", json={
            "remind_at": "2099-10-17T11:00:00+08:00", "channel": "console", "expected_version": 1,
        }).status_code, 404)
        self.model_factory.assert_not_called()
        self.assertEqual(self.notifier.calls, [])

    def test_explicit_plan_keeps_date_precision_and_requires_user_clock(self):
        """日期事项默认不补时刻，用户明确日期提醒钟点后可补计划，但原事项仍没有 at。"""
        client = self.make_client()
        detail = self.ingest(client)["message"]
        date_task = detail["events"][1]["task_id"]
        self.assertEqual(client.get("/reminders").json()["count"], 0)
        self.policy(client)
        queried_policy = client.get("/reminders/policy")
        self.assertEqual(queried_policy.status_code, 200)
        first = client.post("/reminders/plan", json={"message_id": detail["message_id"]})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(client.get("/reminders").json()["count"], 1)
        self.assertIn("date_only_clock_required", first.text)
        self.policy(client, date_only_time="09:00")
        second = client.post("/reminders/plan", json={"message_id": detail["message_id"]})
        self.assertEqual(second.status_code, 200, second.text)
        reminders = client.get("/reminders").json()["reminders"]
        self.assertEqual(len(reminders), 2)
        self.assertTrue(any(item["task_id"] == date_task and "T09:00:00+08:00" in item["remind_at"] for item in reminders))
        saved = client.get(f"/messages/{detail['message_id']}").json()["events"][1]["time"]
        self.assertEqual(saved["precision"], "date")
        self.assertNotIn("at", saved)
        self.assertEqual(client.post("/reminders/plan", json={"message_id": "missing-source"}).status_code, 404)

    def test_auto_policy_hooks_only_new_processed_messages(self):
        """启用规则不追溯旧消息，重复 exists 不补计划，之后新 processed 消息才自动计划。"""
        client = self.make_client()
        old = self.ingest(client, "old-notice")
        self.assertEqual(old["status"], "processed")
        self.policy(client)
        repeated = self.ingest(client, "old-notice")
        self.assertEqual(repeated["status"], "exists")
        self.assertEqual(client.get("/reminders").json()["count"], 0)
        fresh = self.ingest(client, "new-notice")
        self.assertEqual(fresh["status"], "processed")
        self.assertEqual(client.get("/reminders").json()["count"], 1)
        self.assertEqual(self.ingest(client, "new-notice")["status"], "exists")
        self.assertEqual(client.get("/reminders").json()["count"], 1)
        self.assertEqual(self.model_factory.call_count, 2)

    def test_safe_validation_and_unconfigured_mail_status(self):
        """错误参数不回显授权数据，未配置邮箱的查询可用而同步明确返回503。"""
        client = self.make_client()
        secret = "synthetic-authorization-do-not-echo"
        invalid = client.post("/reminders", json={
            "task_id": "any-task", "remind_at": "2026-10-16T10:00:00", "auth_code": secret,
        })
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn(secret, invalid.text)
        invalid_policy = client.put("/reminders/policy", json={"enabled": "yes", "authorization": secret})
        self.assertEqual(invalid_policy.status_code, 422)
        self.assertNotIn(secret, invalid_policy.text)
        status = client.get("/mail/status")
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["configured"])
        self.assertNotIn("auth_code", status.text)
        jobs = client.get("/mail/jobs")
        self.assertEqual(jobs.status_code, 200)
        self.assertEqual(jobs.json()["count"], 0)
        sync = client.post("/mail/sync")
        self.assertEqual(sync.status_code, 503, sync.text)
        self.assertNotIn(secret, sync.text)
        self.model_factory.assert_not_called()

    def test_delivery_failure_keeps_persisted_retry_without_claiming_success(self):
        """外部通知通道失败后保留待重试计划和安全错误码，HTTP成功不冒充投递成功。"""
        self.notifier.failure = OSError("private-notifier-details")
        client = self.make_client()
        task = self.task(client)
        reminder = self.reminder(client.post("/reminders", json={
            "task_id": task["id"], "remind_at": "2000-01-01T10:00:00+08:00", "channel": "windows",
        }))
        result = client.post("/reminders/run-once")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["submitted"], 0)
        self.assertEqual(result.json()["failed"], 1)
        self.assertNotIn("private-notifier-details", result.text)
        saved = client.get("/reminders").json()["reminders"][0]
        self.assertEqual(saved["reminder_id"], reminder["reminder_id"])
        self.assertEqual((saved["status"], saved["attempts"], saved["error_code"]), ("pending", 1, "OSError"))
        self.assertIsNone(saved["submitted_at"])
        self.assertIsNotNone(saved["next_attempt_at"])
        self.assertEqual(len(self.notifier.calls), 1)

    def test_mail_sync_real_mime_raw_message_and_document_index(self):
        """假邮箱入口仍走真实 MIME、受管 EML、消息库与 Qdrant，附件绑定和重试不重复。"""
        account = "synthetic-account@example.com"
        settings = MailSettings(account=account, auth_code="synthetic-key-not-public", enabled=False)
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = "导师 <teacher@example.com>"
        message["To"] = account
        message["Date"] = format_datetime(datetime(2026, 10, 16, 10, tzinfo=timezone(timedelta(hours=8))))
        message["Subject"] = "合成研究通知"
        message.set_content(self.envelope["text"])
        original = b"Synthetic research document: Recall@5 target is 0.85."
        message.add_attachment(original, maintype="text", subtype="plain", filename="research.txt")
        raw = message.as_bytes()
        self.fake_imap = SingleMailIMAP(raw)
        client = self.make_client(mail_settings=settings, mail_collector_factory=self.collector_factory)
        synced = client.post("/mail/sync")
        self.assertEqual(synced.status_code, 200, synced.text)
        self.assertEqual((synced.json()["received"], synced.json()["processed"]), (1, 1))
        jobs_response = client.get("/mail/jobs")
        self.assertEqual(jobs_response.status_code, 200)
        self.assertEqual(jobs_response.json()["count"], 1)
        job = jobs_response.json()["jobs"][0]
        self.assertEqual(job["status"], "processed")
        details = client.get(f"/messages/{job['message_id']}").json()
        self.assertEqual(details["source_message"]["sender_id"], "teacher@example.com")
        self.assertEqual(details["source_message"]["source_message_id"], "INBOX:12345:7")
        self.assertIn(self.envelope["text"], details["source_message"]["text"])
        self.assertEqual(details["source_message"]["attachments"][0]["availability"], "mentioned")
        self.assertEqual(details["attachments"][0]["availability"], "indexed")
        document_id = details["attachments"][0]["document_id"]
        document = client.get(f"/documents/{document_id}").json()
        self.assertEqual(document['classification_basis'], 'document_text')
        self.assertEqual(document['category'], '研究')
        self.assertEqual(document["sources"][0]["message_id"], job["message_id"])
        downloaded = client.get(f"/documents/{document_id}/file")
        self.assertEqual(downloaded.content, original)
        eml_files = list(self.folder.rglob("*.eml"))
        self.assertEqual(len(eml_files), 1)
        self.assertEqual(eml_files[0].read_bytes(), raw)
        searched = client.post("/documents/search", json={"query": "研究指标"})
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(searched.json()["hits"][0]["document_id"], document_id)
        again = client.post("/mail/sync")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["received"], 0)
        self.assertEqual(client.get("/documents").json()["count"], 1)
        self.assertEqual(self.model_factory.call_count, 2)
        self.assertEqual(len(self.embedding.document_calls), 1)
        self.assertEqual(self.fake_imap.calls[-1], "logout")
        self.assertNotIn(settings.auth_code, client.get("/mail/status").text)
        self.assertNotIn(account, client.get("/mail/status").text)


if __name__ == "__main__":
    unittest.main()
