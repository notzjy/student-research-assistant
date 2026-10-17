"""自动化模块集成验证：真实持久层和编排，替换邮箱网络与模型两个外部边界。"""

import asyncio
import copy
import io
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-service-toolkit" / "src"))
from student_assistant.bridge import TaskStore
from student_assistant.mail_ingest import MailSettings
from student_assistant.mail_runtime import MailRuntime
from student_assistant.mail_store import MailStore
from student_assistant.message_store import MessageStore
from student_assistant.message_workflow import MessageProcessor
from student_assistant.reminder_runtime import ReminderRuntime
from student_assistant.reminder_store import ReminderStore
from student_assistant.rag_runtime import RagEngine
from student_assistant.rag_store import RagStore


class NoticeModel:
    """固定返回一项有原文证据的事项，真实校验、日期计算和事务仍由业务代码完成。"""

    def with_structured_output(self, schema, *, method):
        """记录业务实际使用的 Pydantic 协议。"""
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        """为合成邮件返回结构化数据，不绑定执行工具。"""
        return self.schema.model_validate({
            "category": "研究", "summary": "明天开组会，附参考材料。", "attachment_count": 1,
            "attachment_description": "参考材料", "events": [{
                "title": "组会", "time_text": "明天下午三点", "location": "实验楼302",
                "evidence": "明天下午三点在实验楼302开组会。",
                "time_extraction": {"intent": {
                    "unit": "day", "offset": 1, "weekday": None,
                    "hour": 15, "minute": 0, "is_deadline": False,
                }, "reason": None},
            }],
        })


class SmallEmbedding:
    """只替换向量计算，真实原件、文本切片与 Qdrant 写入仍实际运行。"""

    model_id, dimension = "mail-test-v1", 3

    def embed_documents(self, texts):
        """返回合法定长向量，供真实索引检查数据格式。"""
        return [[1.0, 0.0, 0.0] for _ in texts]


class LocalCollector:
    """把受控 EML 字节放入真实接收队列，不连接任何真实邮箱。"""

    def __init__(self, settings, raw, fail=False):
        """保存测试账号与邮件，并允许模拟整轮网络故障。"""
        self.settings, self.raw, self.fail = settings, raw, fail

    def fetch_new(self, store):
        """模拟一次成功收取或断网，UID 去重仍交给真实存储。"""
        if self.fail:
            raise OSError("offline fixture")
        if store.get_cursor(self.settings.account, "INBOX", "100") is None:
            store.save_raw(self.settings.account, "INBOX", "100", 1, self.raw)
            return {"received": 1, "skipped": 0}
        return {"received": 0, "skipped": 0}


class FailingNotifier:
    """模拟本地通知通道暂时不可用，用于确认投递失败保留计划。"""

    def send(self, job):
        """抛出包含敏感示例文本的异常，检查存储只保存类型。"""
        raise OSError("do not persist this private payload")


class BlockingNotifier:
    """通过事件精确控制在途通知，检查退出顺序，不依赖固定等待时间。"""

    def __init__(self):
        """建立开始和释放信号。"""
        self.entered, self.release = threading.Event(), threading.Event()

    def send(self, job):
        """通知调用未结束前保持线程占用，模拟一个正常的慢系统调用。"""
        self.entered.set()
        if not self.release.wait(10):
            raise TimeoutError("test release missing")


class BackgroundServicesTests(unittest.IsolatedAsyncioTestCase):
    """验证提醒精度、幂等执行、优雅退出及邮件到真实附件索引的完整路径。"""

    def setUp(self):
        """每项测试使用临时业务库，避免触碰用户真实任务或模型配置。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.tasks = TaskStore(self.folder / "tasks.sqlite3")
        self.store = ReminderStore(self.tasks)
        self.now = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)
        self.runtime = ReminderRuntime(self.store, clock=self.clock)
        self.task = self.tasks.save_task("编排测试事项")["task"]

    def clock(self):
        """固定北京时间2026年10月16日08:00，历史样例不随运行日期失效。"""
        return self.now

    def message(self, time_value):
        """构造来自已有事项的消息详情，便于检查计划不修改时间数据。"""
        return {"events": [{"task_id": self.task["id"], "status": "pending", "time": time_value}]}

    async def test_date_requires_clock_and_keeps_date_precision(self):
        """有日期无钟点时跳过，用户配置钟点后另建提醒，事项仍无 at。"""
        detail = self.message({"status": "resolved", "precision": "date", "date": "2030-01-02"})
        original = copy.deepcopy(detail)
        self.store.set_policy({"enabled": True})
        result = await self.runtime.plan_message(detail)
        self.assertEqual(result["skipped"][0]["reason"], "date_only_clock_required")
        self.store.set_policy({"date_only_time": "09:00"})
        planned = await self.runtime.plan_message(detail)
        self.assertEqual(planned["planned"][0]["remind_at"], "2030-01-02T09:00:00+08:00")
        self.assertEqual(detail, original)

    async def test_explicit_policy_and_replay_preserve_existing_plan(self):
        """默认禁用；新规则不改旧计划，相同消息重试不重复增加提醒。"""
        detail = self.message({"status": "resolved", "precision": "minute", "at": "2030-01-02T15:00:00+08:00"})
        self.assertFalse((await self.runtime.plan_message(detail))["enabled"])
        self.store.set_policy({"enabled": True})
        first = await self.runtime.plan_message(detail)
        self.assertEqual(first["planned"][0]["remind_at"], "2030-01-02T14:30:00+08:00")
        self.store.set_policy({"lead_minutes": 60})
        await self.runtime.plan_message(detail)
        self.assertEqual(len(self.store.list_reminders()), 1)

    async def test_console_delivery_ack_and_no_replay(self):
        """真实终端通道提交一次，第二次扫描不会重发已确认记录。"""
        self.store.save(self.task["id"], (self.now - timedelta(seconds=1)).isoformat())
        output = io.StringIO()
        with redirect_stdout(output):
            result = await self.runtime.run_once()
            second = await self.runtime.run_once()
        self.assertEqual(result["submitted"], 1)
        self.assertEqual(second["claimed"], 0)
        self.assertIn("reminder_submitted", output.getvalue())
        self.assertEqual(self.store.list_reminders()[0]["status"], "submitted")

    async def test_failed_delivery_backoff_and_safe_error(self):
        """系统异常不丢提醒、不过度忙重试，也不保存原始异常里的敏感文本。"""
        self.store.save(self.task["id"], (self.now - timedelta(seconds=1)).isoformat())
        runtime = ReminderRuntime(self.store, FailingNotifier(), clock=self.clock)
        self.assertEqual((await runtime.run_once())["failed"], 1)
        self.assertEqual((await runtime.run_once())["claimed"], 0)
        record = self.store.list_reminders()[0]
        self.assertEqual(record["error_code"], "OSError")
        self.assertEqual(record["status"], "pending")

    async def test_stop_waits_for_inflight_notification(self):
        """停止信号不丢弃在途通知，线程完成确认后后台循环才退出。"""
        self.store.save(self.task["id"], (self.now - timedelta(seconds=1)).isoformat())
        notifier = BlockingNotifier()
        runtime, stop = ReminderRuntime(self.store, notifier, clock=self.clock), asyncio.Event()
        task = asyncio.create_task(runtime.loop(stop))
        try:
            self.assertTrue(await asyncio.to_thread(notifier.entered.wait, 5))
            stop.set()
            self.assertFalse(task.done())
        finally:
            notifier.release.set()
            await asyncio.wait_for(task, 10)
        self.assertEqual(self.store.list_reminders()[0]["status"], "submitted")

    async def mail_fixture(self, offline=False):
        """使用真实 MIME、消息分析、持久队列和 Qdrant 建立一封可重放的邮件。"""
        email = EmailMessage()
        email["From"], email["To"] = "老师 <teacher@example.test>", "student@example.test"
        email["Date"], email["Subject"] = "Fri, 16 Oct 2026 10:00:00 +0800", "组会通知"
        email.set_content("明天下午三点在实验楼302开组会。")
        email.add_attachment("参考材料正文。".encode(), maintype="text", subtype="markdown", filename="reference.md")
        settings = MailSettings(account="student@example.test", auth_code="offline-test")
        mails = MailStore(self.tasks, self.folder / "source")
        collector = LocalCollector(settings, email.as_bytes(), fail=offline)
        if offline:
            mails.save_raw(settings.account, "INBOX", "100", 1, email.as_bytes())
        messages = MessageStore(self.tasks)
        model = NoticeModel()
        processor = MessageProcessor(messages, model_factory=lambda: model, after_process=self.runtime.plan_message)
        embeddings = SmallEmbedding()
        rag_store = RagStore(self.tasks, self.folder / "rag", model_id=embeddings.model_id, dimension=3)
        self.addCleanup(rag_store.close)
        rag = RagEngine(rag_store, embeddings)
        self.store.set_policy({"enabled": True})
        runtime = MailRuntime(mails, collector, processor, rag, self.runtime)
        return runtime, mails, messages, rag_store

    async def test_mail_to_notice_attachment_and_reminder(self):
        """合成邮件贯通日期锚点、真实附件索引和计划，重收不增加任务或提醒。"""
        runtime, mails, messages, rag_store = await self.mail_fixture()
        result = await runtime.sync()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(mails.list_jobs(runtime.settings.account)[0]["status"], "processed")
        detail = messages.list_messages()[0]
        self.assertEqual(detail["events"][0]["time"]["at"], "2026-10-17T15:00:00+08:00")
        self.assertEqual(detail["attachments"][0]["availability"], "indexed")
        self.assertEqual(len(rag_store.list_documents()), 1)
        self.assertEqual(len(self.store.list_reminders()), 1)
        self.assertEqual((await runtime.sync())["processed"], 0)

    async def test_local_pending_mail_processed_when_network_fails(self):
        """本轮网络故障时仍能处理先前落盘的邮件，不让持久队列依赖重新联网。"""
        runtime, mails, messages, rag_store = await self.mail_fixture(offline=True)
        result = await runtime.sync()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["collection_error"], "OSError")
        self.assertEqual(runtime.status()["last_error"], "OSError")
        self.assertNotIn("offline-test", str(runtime.status()))


if __name__ == "__main__":
    unittest.main()
