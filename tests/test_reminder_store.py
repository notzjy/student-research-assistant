"""提醒持久层单测：真实 SQLite、显式时钟、版本和租约，全程不发送通知。"""

import importlib
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

TaskStore = importlib.import_module("student_assistant.bridge").TaskStore
reminders = importlib.import_module("student_assistant.reminder_store")
ReminderStore = reminders.ReminderStore
ReminderConflictError = reminders.ReminderConflictError


class ReminderStoreTests(unittest.TestCase):
    """验证公开持久化行为，不使用真实业务库、系统时钟、模型或通知通道。"""

    def setUp(self):
        """为每个场景创建独立任务库和固定带时区时钟。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "business.sqlite3"
        self.tasks = TaskStore(self.database)
        self.store = ReminderStore(self.tasks)
        self.now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        self.task = self.tasks.save_task("提交综述", "周五前")["task"]

    def due_reminder(self, *, task_id=None, seconds=0):
        """按相对固定时钟的明确时刻创建提醒，不从原文日期推断钟点。"""
        return self.store.save(
            self.task["id"] if task_id is None else task_id,
            (self.now + timedelta(seconds=seconds)).isoformat(),
        )

    def test_creation_is_persistent_and_idempotent_for_equivalent_offsets(self):
        """等价偏移时刻复用稳定 ID 并保留首写原文，不同通道可以各自建立计划。"""
        first = self.store.save(self.task["id"], "2026-10-16T20:00:00+08:00")
        equivalent = self.store.save(self.task["id"], "2026-10-16T12:00:00Z")
        windows = self.store.save(self.task["id"], "2026-10-16T12:00:00Z", channel="windows")

        self.assertEqual(first, equivalent)
        self.assertEqual(first["remind_at"], "2026-10-16T20:00:00+08:00")
        self.assertRegex(first["reminder_id"], r"^[a-f0-9]{24}$")
        self.assertEqual(first["title"], self.task["title"])
        self.assertEqual(first["version"], 1)
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first["attempts"], 0)
        self.assertNotEqual(first["reminder_id"], windows["reminder_id"])
        reopened = ReminderStore(TaskStore(self.database))
        self.assertEqual(reopened.get(first["reminder_id"]), first)
        self.assertEqual(len(reopened.list_reminders()), 2)
        self.assertNotIn("lease_token", reopened.get(first["reminder_id"]))

    def test_invalid_or_ended_task_and_ambiguous_clock_cannot_create_reminders(self):
        """只有真实未完成任务和带时区的具体钟点能建计划，日期级原话不自动补时间。"""
        for value in ("周五前", "2026-10-16", "2026-10-16T20:00:00", "2026-02-30T20:00:00+08:00"):
            with self.subTest(remind_at=value), self.assertRaises(ValueError):
                self.store.save(self.task["id"], value)
        with self.assertRaises(ValueError):
            self.store.save("missing-task", self.now.isoformat())
        with self.assertRaises(ValueError):
            self.store.save(self.task["id"], self.now.isoformat(), channel="shell-command")
        self.tasks.mark_completed(self.task["id"])
        with self.assertRaises(ValueError):
            self.due_reminder()
        self.assertEqual(self.store.list_reminders(), [])
        with self.assertRaises(ValueError):
            self.store.claim_due(datetime(2026, 10, 16, 12, 0))

    def test_claim_respects_due_time_limit_and_lease_visibility(self):
        """只认领到期计划并遵守批量上限，已租约记录不会再次返回给其他认领者。"""
        past = self.due_reminder(seconds=-10)
        current = self.due_reminder()
        future = self.due_reminder(seconds=10)

        first = self.store.claim_due(self.now, limit=1)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["reminder_id"], past["reminder_id"])
        self.assertEqual(first[0]["status"], "leased")
        self.assertEqual(first[0]["attempts"], 1)
        self.assertTrue(first[0]["lease_token"])
        self.assertEqual(datetime.fromisoformat(first[0]["lease_until"]), self.now + timedelta(seconds=60))
        second = self.store.claim_due(self.now)
        self.assertEqual([job["reminder_id"] for job in second], [current["reminder_id"]])
        self.assertEqual(self.store.claim_due(self.now), [])
        self.assertEqual(self.store.get(future["reminder_id"])["status"], "pending")

    def test_two_workers_atomically_claim_one_job_only_once(self):
        """两个独立存储实例并发访问同库时，SQLite 原子认领只允许一个 worker 成功。"""
        self.due_reminder()
        other = ReminderStore(TaskStore(self.database))
        barrier = threading.Barrier(2)

        def claim_together(store):
            """同步两个线程的起点，再调用实际数据库认领操作。"""
            barrier.wait(timeout=5)
            return store.claim_due(self.now)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(claim_together, (self.store, other)))

        self.assertEqual(sum(len(result) for result in results), 1)
        self.assertEqual(self.store.list_reminders()[0]["attempts"], 1)

    def test_automatic_plans_are_atomic_across_different_times_and_channels(self):
        """并发自动规则即使计算出不同时间和通道也只建首计划，显式多条计划仍然允许。"""
        other = ReminderStore(TaskStore(self.database))
        barrier = threading.Barrier(2)
        options = [
            (self.store, (self.now + timedelta(minutes=10)).isoformat(), "console"),
            (other, (self.now + timedelta(minutes=20)).isoformat(), "windows"),
        ]

        def plan_together(option):
            """让两种规则同时竞争同任务，实际去重由 SQLite 写事务保证。"""
            store, remind_at, channel = option
            barrier.wait(timeout=5)
            return store.save_automatic(self.task["id"], remind_at, channel)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(plan_together, options))

        self.assertEqual({result["status"] for result in results}, {"created", "exists"})
        self.assertEqual(results[0]["reminder"], results[1]["reminder"])
        self.assertEqual(len(self.store.list_reminders()), 1)
        first = results[0]["reminder"]
        manual = self.store.save(self.task["id"], (self.now + timedelta(hours=1)).isoformat())
        self.assertNotEqual(manual["reminder_id"], first["reminder_id"])
        self.assertEqual(len(self.store.list_reminders()), 2)
        self.store.cancel(first["reminder_id"])
        repeated = other.save_automatic(self.task["id"], (self.now + timedelta(hours=2)).isoformat(), "windows")
        self.assertEqual(repeated["status"], "exists")
        self.assertEqual(repeated["reminder"]["reminder_id"], first["reminder_id"])
        self.assertEqual(repeated["reminder"]["status"], "cancelled")
        self.assertEqual(len(self.store.list_reminders()), 2)

    def test_expired_lease_can_be_recovered_after_restart_and_old_token_is_invalid(self):
        """重启后过期租约可补跑，新 token 使旧 worker 无法提交或记录失败。"""
        reminder = self.due_reminder()
        original = self.store.claim_due(self.now, lease_seconds=10)[0]
        self.assertTrue(self.store.validate_claim(reminder["reminder_id"], 1, original["lease_token"], self.now))
        expires = self.now + timedelta(seconds=10)
        self.assertFalse(self.store.validate_claim(reminder["reminder_id"], 1, original["lease_token"], expires))
        reopened = ReminderStore(TaskStore(self.database))

        recovered = reopened.claim_due(expires, lease_seconds=10)[0]

        self.assertEqual(recovered["attempts"], 2)
        self.assertNotEqual(recovered["lease_token"], original["lease_token"])
        self.assertFalse(reopened.mark_submitted(reminder["reminder_id"], 1, original["lease_token"], expires))
        self.assertFalse(reopened.mark_failed(reminder["reminder_id"], 1, original["lease_token"], "TimeoutError", expires))
        self.assertTrue(reopened.validate_claim(reminder["reminder_id"], 1, recovered["lease_token"], expires))

    def test_failures_use_safe_codes_bounded_backoff_and_attempt_limit(self):
        """错误仅保存安全类型名，按认领次数指数退避至多300秒，达到次数上限后终止。"""
        reminder = self.due_reminder()
        clock = self.now
        for attempt in range(1, 10):
            job = self.store.claim_due(clock, max_attempts=9)[0]
            self.assertEqual(job["attempts"], attempt)
            if attempt == 1:
                with self.assertRaises(ValueError):
                    self.store.mark_failed(reminder["reminder_id"], 1, job["lease_token"], "ProviderError: secret-key", clock)
                self.assertTrue(self.store.validate_claim(reminder["reminder_id"], 1, job["lease_token"], clock))
            self.assertTrue(self.store.mark_failed(reminder["reminder_id"], 1, job["lease_token"], "TimeoutError", clock, max_attempts=9))
            detail = self.store.get(reminder["reminder_id"])
            self.assertEqual(detail["error_code"], "TimeoutError")
            next_attempt = datetime.fromisoformat(detail["next_attempt_at"])
            self.assertEqual((next_attempt - clock).total_seconds(), min(2 ** attempt, 300))
            self.assertEqual(detail["status"], "failed" if attempt == 9 else "pending")
            self.assertEqual(self.store.claim_due(next_attempt - timedelta(microseconds=1), max_attempts=9), [])
            clock = next_attempt
        self.assertEqual(self.store.claim_due(clock + timedelta(days=1), max_attempts=9), [])

    def test_submitted_reminder_is_not_reclaimed_or_relabelled_as_cancelled(self):
        """已提交仅记录一次，重启或完成任务后不重新认领，也不声称已撤回系统通知。"""
        reminder = self.due_reminder()
        job = self.store.claim_due(self.now)[0]

        self.assertTrue(self.store.mark_submitted(reminder["reminder_id"], 1, job["lease_token"], self.now))

        detail = self.store.get(reminder["reminder_id"])
        self.assertEqual(detail["status"], "submitted")
        self.assertEqual(datetime.fromisoformat(detail["submitted_at"]), self.now)
        self.assertFalse(self.store.mark_submitted(reminder["reminder_id"], 1, job["lease_token"], self.now))
        self.tasks.mark_completed(self.task["id"])
        self.assertEqual(self.store.cancel(reminder["reminder_id"])["status"], "submitted")
        reopened = ReminderStore(TaskStore(self.database))
        self.assertEqual(reopened.claim_due(self.now + timedelta(days=1)), [])
        self.assertEqual(reopened.get(reminder["reminder_id"])["status"], "submitted")

    def test_task_completion_trigger_cancels_pending_and_leased_reminders(self):
        """旧任务完成路径直接触发同库取消，既阻止未来提醒，也失效已经认领的旧 token。"""
        due = self.due_reminder()
        future = self.due_reminder(seconds=120)
        other_task = self.tasks.save_task("仍需处理的另一事项")["task"]
        other = self.due_reminder(task_id=other_task["id"])
        jobs = {job["reminder_id"]: job for job in self.store.claim_due(self.now)}

        self.tasks.mark_completed(self.task["id"])

        self.assertEqual(self.store.get(due["reminder_id"])["status"], "cancelled")
        self.assertEqual(self.store.get(future["reminder_id"])["status"], "cancelled")
        self.assertFalse(self.store.validate_claim(due["reminder_id"], 1, jobs[due["reminder_id"]]["lease_token"], self.now))
        self.assertFalse(self.store.mark_submitted(due["reminder_id"], 1, jobs[due["reminder_id"]]["lease_token"], self.now))
        self.assertTrue(self.store.validate_claim(other["reminder_id"], 1, jobs[other["reminder_id"]]["lease_token"], self.now))

    def test_cancel_checks_version_and_does_not_complete_task(self):
        """取消只影响提醒，可检测过期版本，不能偷偷改变待办完成状态。"""
        reminder = self.due_reminder()
        job = self.store.claim_due(self.now)[0]
        with self.assertRaises(ReminderConflictError):
            self.store.cancel(reminder["reminder_id"], expected_version=2)

        cancelled = self.store.cancel(reminder["reminder_id"], expected_version=1)

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.store.cancel(reminder["reminder_id"]), cancelled)
        self.assertIsNone(self.store.cancel("missing-reminder"))
        self.assertEqual(self.tasks.list_tasks()[0]["status"], "pending")
        self.assertFalse(self.store.mark_failed(reminder["reminder_id"], 1, job["lease_token"], "TimeoutError", self.now))
        self.assertEqual(self.store.claim_due(self.now + timedelta(days=1)), [])

    def test_edit_increments_version_and_old_worker_cannot_overwrite_new_schedule(self):
        """编辑保持提醒和任务 ID，增加版本并取消旧 job；旧回执不能覆盖新计划。"""
        reminder = self.due_reminder()
        old = self.store.claim_due(self.now)[0]
        changed_time = self.now + timedelta(minutes=10)

        edited = self.store.save(self.task["id"], changed_time.isoformat(), reminder_id=reminder["reminder_id"], expected_version=1)

        self.assertEqual(edited["reminder_id"], reminder["reminder_id"])
        self.assertEqual(edited["version"], 2)
        self.assertEqual(edited["status"], "pending")
        self.assertEqual(edited["attempts"], 0)
        self.assertFalse(self.store.mark_submitted(reminder["reminder_id"], 1, old["lease_token"], self.now))
        self.assertFalse(self.store.mark_failed(reminder["reminder_id"], 1, old["lease_token"], "TimeoutError", self.now))
        with self.assertRaises(ReminderConflictError):
            self.store.save(self.task["id"], changed_time.isoformat(), reminder_id=reminder["reminder_id"], expected_version=1)
        with self.assertRaises(ReminderConflictError):
            self.store.save(self.task["id"], changed_time.isoformat(), reminder_id=reminder["reminder_id"])
        other_task = self.tasks.save_task("不能替换成的另一待办")["task"]
        with self.assertRaises(ValueError):
            self.store.save(other_task["id"], changed_time.isoformat(), reminder_id=reminder["reminder_id"], expected_version=2)
        self.assertEqual(self.store.get(reminder["reminder_id"]), edited)
        self.assertEqual(self.store.claim_due(self.now), [])
        current = self.store.claim_due(changed_time)[0]
        self.assertEqual(current["version"], 2)

    def test_expired_worker_that_exhausted_attempts_is_not_reclaimed_forever(self):
        """worker 崩溃且已到尝试上限时，过期 job 转为 failed，避免无限重复投递。"""
        reminder = self.due_reminder()
        original = self.store.claim_due(self.now, lease_seconds=1, max_attempts=1)[0]

        self.assertEqual(self.store.claim_due(self.now + timedelta(seconds=1), max_attempts=1), [])

        self.assertEqual(self.store.get(reminder["reminder_id"])["status"], "failed")
        self.assertFalse(self.store.mark_submitted(reminder["reminder_id"], 1, original["lease_token"], self.now + timedelta(seconds=1)))

    def test_policy_is_disabled_by_default_strictly_validated_and_persisted(self):
        """自动规则默认禁用且不补日期钟点，用户明确配置后持久化并拒绝类型和范围错误。"""
        self.assertEqual(self.store.get_policy(), {
            "enabled": False, "lead_minutes": 30, "date_only_time": None, "channel": "console",
        })
        enabled = self.store.set_policy({"enabled": True, "lead_minutes": 15, "date_only_time": "18:30"})
        self.assertEqual(ReminderStore(TaskStore(self.database)).get_policy(), enabled)
        invalid = [
            {"enabled": 1}, {"lead_minutes": True}, {"lead_minutes": -1},
            {"lead_minutes": 10081}, {"date_only_time": "24:00"},
            {"date_only_time": "9:00"}, {"channel": "unknown"}, {"extra": True},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.set_policy(value)
        self.assertEqual(self.store.get_policy(), enabled)
        self.assertEqual(self.store.list_reminders(), [])

    def test_legacy_schema_migration_preserves_every_existing_job_value(self):
        """旧 schema 补 legacy 列时不重写历史版本、状态、租约或时间，重复初始化仍保持不变。"""
        legacy_database = self.database.parent / "legacy.sqlite3"
        legacy_tasks = TaskStore(legacy_database)
        task = legacy_tasks.save_task("旧版已安排事项")["task"]
        with closing(sqlite3.connect(legacy_database)) as connection, connection:
            connection.execute(
                "CREATE TABLE reminder_schedules (reminder_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, version INTEGER NOT NULL)"
            )
            connection.execute(
                """CREATE TABLE reminder_jobs (
                    reminder_id TEXT NOT NULL, version INTEGER NOT NULL, title TEXT NOT NULL,
                    remind_at TEXT NOT NULL, due_at_utc TEXT NOT NULL, channel TEXT NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL, next_attempt_at TEXT NOT NULL,
                    lease_token TEXT, lease_until TEXT, error_code TEXT, submitted_at TEXT,
                    PRIMARY KEY(reminder_id, version)
                )"""
            )
            connection.execute("INSERT INTO reminder_schedules VALUES (?, ?, 2)", ("old-reminder", task["id"]))
            for version, status, token, submitted in (
                (1, "submitted", None, "2026-10-15T12:00:00+00:00"),
                (2, "leased", "old-token", None),
            ):
                connection.execute(
                    "INSERT INTO reminder_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("old-reminder", version, task["title"], "2026-10-16T20:00:00+08:00",
                     "2026-10-16T12:00:00.000000+00:00", "windows", status, 2,
                     "2026-10-16T12:00:00.000000+00:00", token,
                     "2026-10-16T12:01:00.000000+00:00", "TimeoutError", submitted),
                )
            before = connection.execute("SELECT * FROM reminder_jobs ORDER BY version").fetchall()

        migrated = ReminderStore(legacy_tasks)
        self.assertEqual(migrated.get("old-reminder")["origin"], "legacy")
        self.assertEqual(migrated.get("old-reminder")["status"], "leased")
        self.assertEqual(migrated.get("old-reminder")["version"], 2)
        ReminderStore(legacy_tasks)
        with closing(sqlite3.connect(legacy_database)) as connection:
            after = connection.execute("SELECT * FROM reminder_jobs ORDER BY version").fetchall()
        self.assertEqual([row[:-1] for row in after], before)
        self.assertEqual([row[-1] for row in after], ["legacy", "legacy"])

    def test_origins_distinguish_new_plans_and_manual_edit_preserves_old_version(self):
        """自动新增标 automatic，手工编辑的新版本标 manual，复用已有计划不篡改来源。"""
        automatic = self.store.save_automatic(self.task["id"], self.now.isoformat())["reminder"]
        self.assertEqual(automatic["origin"], "automatic")
        reused = self.store.save_automatic(self.task["id"], (self.now + timedelta(hours=1)).isoformat())
        self.assertEqual(reused["status"], "exists")
        self.assertEqual(reused["reminder"]["origin"], "automatic")
        edited = self.store.save(
            self.task["id"], (self.now + timedelta(hours=1)).isoformat(),
            reminder_id=automatic["reminder_id"], expected_version=1,
        )
        self.assertEqual(edited["origin"], "manual")
        self.assertEqual(edited["version"], 2)
        with closing(sqlite3.connect(self.database)) as connection:
            old_origin = connection.execute(
                "SELECT origin FROM reminder_jobs WHERE reminder_id=? AND version=1", (automatic["reminder_id"],)
            ).fetchone()[0]
        self.assertEqual(old_origin, "automatic")
        manual = self.store.save(self.task["id"], (self.now + timedelta(hours=2)).isoformat())
        self.assertEqual(manual["origin"], "manual")
        other_task = self.tasks.save_task("手工计划先建立")["task"]
        prior_manual = self.store.save(other_task["id"], self.now.isoformat())
        self.assertEqual(self.store.save_automatic(other_task["id"], self.now.isoformat())["reminder"], prior_manual)

    def test_source_context_only_exposes_linked_time_metadata_and_handles_missing_tables(self):
        """无消息表或无关联时返回 None，关联消息仅暴露来源、发送时间和已保存时间对象。"""
        manual = self.due_reminder()
        self.assertIsNone(self.store.get_source_context(manual["reminder_id"]))
        MessageStore = importlib.import_module("student_assistant.message_store").MessageStore
        MessageInput = importlib.import_module("student_assistant.message_models").MessageInput
        messages = MessageStore(self.tasks)
        self.assertIsNone(self.store.get_source_context(manual["reminder_id"]))
        envelope = MessageInput(
            source="qq_mail", account_id="fixture", source_message_id="fixture-001",
            sender_id="teacher@example.invalid", sent_at="2026-10-15T10:00:00+08:00",
            text="仅测试原文，不应出现在提醒来源元数据中。",
        )
        received = messages.receive(envelope)
        event_time = {"status": "resolved", "precision": "date", "date": "2026-10-16"}
        processed = messages.complete_analysis(
            received["message_id"], {"category": "研究", "summary": "测试摘要"},
            [{"title": "消息关联事项", "time": event_time, "evidence": "仅测试原文"}],
        )
        reminder = self.store.save_automatic(processed["events"][0]["task_id"], self.now.isoformat())["reminder"]
        context = self.store.get_source_context(reminder["reminder_id"])
        self.assertEqual(context, {"source": "qq_mail", "sent_at": envelope.sent_at, "time": event_time})
        self.assertNotIn("text", context)
        self.assertIsNone(self.store.get_source_context("missing-reminder"))
        self.assertEqual(messages.get_message(received["message_id"])["source_message"]["text"], envelope.text)

    def test_suppress_requires_current_owned_lease_and_never_completes_the_task(self):
        """抑制仅取消有效租约当前通知并留原因；过期或重复回执无效，原任务和时刻保留。"""
        reminder = self.due_reminder()
        old = self.store.claim_due(self.now, lease_seconds=10)[0]
        identity = (reminder["reminder_id"], 1, old["lease_token"])
        later = self.now + timedelta(seconds=10)
        self.assertFalse(self.store.suppress_claim(*identity, "SourceEventExpired", later))
        recovered = self.store.claim_due(later)[0]
        self.assertFalse(self.store.suppress_claim(*identity, "SourceEventExpired", later))
        identity = (reminder["reminder_id"], 1, recovered["lease_token"])
        with self.assertRaises(ValueError):
            self.store.suppress_claim(*identity, "private body: should not persist", later)
        self.assertTrue(self.store.validate_claim(*identity, later))
        self.assertTrue(self.store.suppress_claim(*identity, "SourceEventExpired", later))
        detail = self.store.get(reminder["reminder_id"])
        self.assertEqual(detail["status"], "cancelled")
        self.assertEqual(detail["error_code"], "SourceEventExpired")
        self.assertEqual(detail["remind_at"], reminder["remind_at"])
        self.assertIsNone(detail["lease_until"])
        self.assertEqual(self.tasks.list_tasks()[0]["status"], "pending")
        self.assertFalse(self.store.suppress_claim(*identity, "SourceEventExpired", later))
        self.assertFalse(self.store.mark_submitted(*identity, later))
        self.assertEqual(self.store.claim_due(later + timedelta(days=1)), [])


if __name__ == "__main__":
    unittest.main()
