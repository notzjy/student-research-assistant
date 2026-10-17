"""历史消息提醒回归：固定时钟、真实SQLite、记录通知替身，不收件也不弹系统通知。"""

import copy
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from student_assistant.bridge import TaskStore
from student_assistant.message_models import MessageInput
from student_assistant.message_store import MessageStore
from student_assistant.reminder_guards import event_expiration_reason
from student_assistant.reminder_runtime import ReminderRuntime
from student_assistant.reminder_store import ReminderStore


BEIJING = timezone(timedelta(hours=8))


class RecordingNotifier:
    """仅记录本次尝试，可模拟暂时失败，全程不访问Windows或外部平台。"""

    def __init__(self):
        """初始化调用列表和可选异常，以验证过期守卫发生在通知调用之前。"""
        self.calls = []
        self.failure = None

    def send(self, job):
        """保存投递副本，配置故障时模拟可重试通道错误。"""
        self.calls.append(dict(job))
        if self.failure is not None:
            raise self.failure


class ReminderGuardTests(unittest.IsolatedAsyncioTestCase):
    """验证自动计划与在途重试双重门控，同时保留用户显式提醒的重启补发。"""

    def setUp(self):
        """只在临时目录建立真实业务数据，并固定到北京时间2026年10月16日10:00。"""
        temporary = tempfile.TemporaryDirectory(prefix="student_reminder_guard_")
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "business.sqlite3"
        self.tasks = TaskStore(self.database)
        self.messages = MessageStore(self.tasks)
        self.store = ReminderStore(self.tasks)
        self.now = datetime(2026, 10, 16, 10, 0, tzinfo=BEIJING)
        self.notifier = RecordingNotifier()
        self.runtime = ReminderRuntime(self.store, self.notifier, clock=self.clock)
        self.store.set_policy({"enabled": True, "date_only_time": "09:00"})

    def clock(self):
        """返回可由测试推进的带时区时刻，避免依赖宿主机年月日和等待真实时间。"""
        return self.now

    def source_message(self, value, *, source_id="notice-1", sent_at="2026-10-16T09:00:00+08:00"):
        """通过真实消息接收和完成事务建立来源关联，不调用模型或读取邮箱。"""
        payload = MessageInput.model_validate({
            "source": "qq_mail", "account_id": "synthetic@example.test",
            "source_message_id": source_id, "sender_id": "sender@example.test",
            "sent_at": sent_at, "text": "合成历史消息，仅用于提醒门控回归。", "attachments": [],
        })
        received = self.messages.receive(payload)
        return self.messages.complete_analysis(received["message_id"], {"category": "其他"}, [{
            "title": "合成通知事项", "time_text": "原消息时间", "evidence": "合成历史消息",
            "time": value,
        }])

    def minute_value(self, at):
        """构造已解析分钟级时间；时区原串保留，用于精确比较边界。"""
        return {"status": "resolved", "precision": "minute", "at": at.isoformat()}

    def test_minute_expiration_compares_instants_including_equal_boundary(self):
        """过去和恰好当前的精确事项均过期，不同时区的相同时刻得到相同结论。"""
        cases = [
            (self.now - timedelta(seconds=1), "source_event_expired"),
            (self.now, "source_event_expired"),
            (self.now.astimezone(timezone.utc), "source_event_expired"),
            (self.now + timedelta(seconds=1), None),
        ]
        for at, expected in cases:
            with self.subTest(at=at):
                self.assertEqual(event_expiration_reason(self.minute_value(at), self.now), expected)

    def test_date_expiration_uses_beijing_calendar_without_inventing_midnight(self):
        """北京时间跨日后昨天过期；今天没有具体钟点，仍不能被当作零点已过期。"""
        now = datetime(2026, 10, 16, 16, 30, tzinfo=timezone.utc)
        yesterday = {"status": "resolved", "precision": "date", "date": "2026-10-16"}
        today = {"status": "resolved", "precision": "date", "date": "2026-10-17"}
        self.assertEqual(event_expiration_reason(yesterday, now), "source_event_expired")
        self.assertIsNone(event_expiration_reason(today, now))
        self.assertNotIn("at", today)

    def test_ranges_unknown_time_and_naive_clock_have_explicit_boundaries(self):
        """范围以结束日判断，未知时间不猜测，调用者缺少时区时明确拒绝。"""
        values = [
            ({"status": "resolved", "precision": "date_range", "end_date": "2026-10-15"}, "source_event_expired"),
            ({"status": "resolved", "precision": "date_range", "end_date": "2026-10-16"}, None),
            ({"status": "unresolved", "precision": "date", "date": "2021-10-09"}, None),
            (None, None),
            ({"status": "resolved"}, None),
        ]
        for value, expected in values:
            with self.subTest(value=value):
                self.assertEqual(event_expiration_reason(value, self.now), expected)
        with self.assertRaises(ValueError):
            event_expiration_reason(None, datetime(2026, 10, 16))

    async def test_importing_historical_notice_preserves_data_but_creates_no_reminder(self):
        """模拟2021年旧邮件在2026年导入，原消息时间和事项保留但不能立即弹提醒。"""
        value = {"status": "resolved", "precision": "date", "date": "2021-10-09"}
        detail = self.source_message(value, sent_at="2021-10-05T10:00:00+08:00")
        original = copy.deepcopy(detail)
        result = await self.runtime.plan_message(detail)
        self.assertEqual(result["planned"], [])
        self.assertEqual(result["skipped"][0]["reason"], "source_event_expired")
        self.assertEqual(self.store.list_reminders(), [])
        self.assertEqual(self.messages.get_message(detail["message_id"]), original)
        self.assertEqual(self.notifier.calls, [])

    async def test_elapsed_lead_window_or_date_clock_does_not_create_catchup_plan(self):
        """事项仍在未来但提醒钟点已过时跳过，日期事项仍不补造精确截止时间。"""
        cases = [
            self.minute_value(self.now + timedelta(minutes=20)),
            {"status": "resolved", "precision": "date", "date": "2026-10-16"},
        ]
        for index, value in enumerate(cases):
            with self.subTest(value=value):
                detail = self.source_message(value, source_id=f"lead-{index}")
                original = copy.deepcopy(detail)
                result = await self.runtime.plan_message(detail)
                self.assertEqual(result["planned"], [])
                self.assertEqual(result["skipped"][0]["reason"], "reminder_time_passed")
                self.assertEqual(detail, original)
        self.assertEqual(self.store.list_reminders(), [])

    async def test_automatic_and_legacy_queue_with_expired_source_are_suppressed(self):
        """升级前遗留和新自动计划均在发送前复查原事项，取消提醒而不完成待办。"""
        for index, origin in enumerate(("automatic", "legacy")):
            detail = self.source_message(self.minute_value(self.now - timedelta(days=1)), source_id=f"old-{index}")
            task_id = detail["events"][0]["task_id"]
            record = self.store.save_automatic(task_id, (self.now - timedelta(days=2)).isoformat())["reminder"]
            if origin == "legacy":
                # 只修改临时数据库，模拟升级时已存在、尚未标注来源类型的旧记录。
                with closing(sqlite3.connect(self.database)) as connection, connection:
                    connection.execute("UPDATE reminder_jobs SET origin = 'legacy' WHERE reminder_id = ?", (record["reminder_id"],))
        result = await self.runtime.run_once()
        self.assertEqual((result["suppressed"], result["submitted"]), (2, 0))
        self.assertEqual(self.notifier.calls, [])
        for record in self.store.list_reminders():
            self.assertEqual((record["status"], record["error_code"]), ("cancelled", "SourceEventExpired"))
        self.assertTrue(all(task["status"] == "pending" for task in self.tasks.list_tasks()))

    async def test_restart_still_delivers_future_event_and_explicit_manual_past_reminder(self):
        """停机错过的未来事项提醒与用户显式补发都保留，不把所有过去提醒一律清除。"""
        future = self.source_message(self.minute_value(self.now + timedelta(hours=1)), source_id="future")
        expired = self.source_message(self.minute_value(self.now - timedelta(days=1)), source_id="manual")
        due = (self.now - timedelta(minutes=5)).isoformat()
        self.store.save_automatic(future["events"][0]["task_id"], due)
        manual = self.store.save(expired["events"][0]["task_id"], due)
        reopened = ReminderStore(TaskStore(self.database))
        runtime = ReminderRuntime(reopened, self.notifier, clock=self.clock)
        result = await runtime.run_once()
        self.assertEqual((result["submitted"], result["suppressed"]), (2, 0))
        self.assertEqual(len(self.notifier.calls), 2)
        self.assertEqual(reopened.get(manual["reminder_id"])["origin"], "manual")
        self.assertEqual((await runtime.run_once())["claimed"], 0)

    async def test_retry_rechecks_source_and_stops_after_event_expires(self):
        """首次失败到重试期间事项过期时，仅取消计划，不第二次调用通知器。"""
        detail = self.source_message(self.minute_value(self.now + timedelta(seconds=3)))
        record = self.store.save_automatic(detail["events"][0]["task_id"], (self.now - timedelta(seconds=1)).isoformat())["reminder"]
        self.notifier.failure = OSError("synthetic offline channel")
        self.assertEqual((await self.runtime.run_once())["failed"], 1)
        self.now += timedelta(seconds=4)
        self.notifier.failure = None
        second = await self.runtime.run_once()
        self.assertEqual((second["suppressed"], second["submitted"]), (1, 0))
        self.assertEqual(len(self.notifier.calls), 1)
        self.assertEqual(self.store.get(record["reminder_id"])["error_code"], "SourceEventExpired")


if __name__ == "__main__":
    unittest.main()
