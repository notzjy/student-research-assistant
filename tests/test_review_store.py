"""人工纠错验收：真实临时 SQLite、原文不变、冲突回滚和旧提醒失效。"""

import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)
TaskStore = importlib.import_module("student_assistant.bridge").TaskStore
MessageStore = importlib.import_module("student_assistant.message_store").MessageStore
MessageInput = importlib.import_module("student_assistant.message_models").MessageInput
ReminderStore = importlib.import_module("student_assistant.reminder_store").ReminderStore
review_module = importlib.import_module("student_assistant.review_store")
ReviewStore = review_module.ReviewStore
ReviewConflictError = review_module.ReviewConflictError


class ReviewStoreTests(unittest.TestCase):
    """仅使用合成消息与临时库，人工修改不覆盖消息原文或模型初始分析。"""

    def setUp(self):
        """创建一条有日期事项和附件线索的已分析消息，供不同纠错场景复用。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tasks = TaskStore(Path(temporary.name) / "business.sqlite3")
        self.messages = MessageStore(self.tasks)
        self.reminders = ReminderStore(self.tasks)
        self.reviews = ReviewStore(self.tasks)
        self.input = MessageInput(
            source="manual", source_message_id="review-01", sender_id="teacher-1",
            sent_at="2026-10-17T10:00:00+08:00", text="明天下午三点在302开组会，附件是论文。",
            attachments=[{"filename": "paper.pdf"}],
        )
        self.message_id = self.messages.receive(self.input)["message_id"]
        self.time = {
            "precision": "minute", "status": "resolved", "time_kind": "point",
            "time_text": "明天下午三点", "at": "2026-10-18T15:00:00+08:00",
            "reference_time": self.input.sent_at, "timezone": "UTC+08:00",
        }
        self.analysis = {"category": "研究", "summary": "通知组会", "events": []}
        detail = self.messages.complete_analysis(self.message_id, self.analysis, [{
            "title": "组会", "time_text": "明天下午三点", "location": "302",
            "evidence": "明天下午三点在302开组会", "time": self.time,
        }])
        self.task_id = detail["events"][0]["task_id"]

    def document(self):
        """添加最小真实文档元数据表，以测试独立正文分类和人工覆盖的持久边界。"""
        with closing(sqlite3.connect(self.tasks.db_path)) as connection, connection:
            connection.execute("CREATE TABLE rag_documents(document_id TEXT PRIMARY KEY,filename TEXT,status TEXT,pages_json TEXT)")
            connection.execute("INSERT INTO rag_documents VALUES ('doc-1','paper.pdf','indexed',?)", (
                json.dumps([{"page": 1, "text": "这是一篇检索研究论文。"}], ensure_ascii=False),
            ))
        return "doc-1"

    def test_message_multilabel_revision_keeps_raw_message_and_analysis(self):
        """类别与多个标签显示人工纠正，重复导入仍复用同一原消息与指纹。"""
        original = self.messages.get_message(self.message_id)
        saved = self.reviews.apply_message(self.message_id, {
            "category": "学生生活", "labels": [" 组会 ", "研究", "组会"], "summary": "人工摘要",
        })
        self.assertEqual(saved["version"], 1)
        viewed = self.reviews.detail_overlay(original)
        self.assertEqual(viewed["analysis"]["category"], "学生生活")
        self.assertEqual(viewed["labels"], ["组会", "研究"])
        self.assertEqual(viewed["original_analysis"], self.analysis)
        self.assertEqual(self.messages.get_message(self.message_id), original)
        self.assertEqual(self.messages.receive(self.input)["status"], "exists")
        self.assertEqual(original["analysis"]["category"], "研究")
        self.assertEqual([row["version"] for row in self.reviews.get_history("message", self.message_id)], [0, 1])

    def test_stale_version_rolls_back_and_later_patch_preserves_labels(self):
        """旧页面版本不能覆盖新标签；基于最新版本的摘要修改保留前次标签。"""
        self.reviews.apply_message(self.message_id, {"labels": ["重要", "待读"]})
        with self.assertRaises(ReviewConflictError):
            self.reviews.apply_message(self.message_id, {"category": "其他"})
        saved = self.reviews.apply_message(self.message_id, {"expected_version": 1, "summary": "更新摘要"})
        self.assertEqual(saved["changes"]["labels"], ["重要", "待读"])
        self.assertEqual(saved["version"], 2)
        self.assertEqual(len(self.reviews.get_history("message", self.message_id)), 3)

    def test_event_time_edit_cancels_old_jobs_and_preserves_submitted(self):
        """修改钟点后旧待投递提醒取消，已交通道的记录保持 submitted。"""
        pending = self.reminders.save(self.task_id, "2026-10-18T14:30:00+08:00")
        submitted = self.reminders.save(self.task_id, "2026-10-18T14:00:00+08:00")
        with closing(sqlite3.connect(self.tasks.db_path)) as connection, connection:
            connection.execute("UPDATE reminder_jobs SET status='submitted' WHERE reminder_id=?", (submitted["reminder_id"],))
        corrected = {**self.time, "at": "2026-10-18T16:00:00+08:00", "time_text": "明天下午四点"}
        result = self.reviews.apply_event(self.task_id, {"time_text": "明天下午四点", "time": corrected, "location": "402"})
        self.assertEqual(result["location"], "402")
        self.assertEqual(self.messages.get_message(self.message_id)["events"][0]["time"], corrected)
        self.assertEqual(self.reminders.get(pending["reminder_id"])["status"], "cancelled")
        self.assertEqual(self.reminders.get(submitted["reminder_id"])["status"], "submitted")
        baseline = self.reviews.get_history("event", self.task_id)[0]["changes"]
        self.assertEqual(baseline["time"], self.time)
        self.assertEqual(baseline["location"], "302")
        self.assertEqual(self.messages.get_message(self.message_id)["analysis"], self.analysis)

    def test_invalid_reference_or_precision_rolls_back_without_cancelling(self):
        """错误的参考日期或日期精度混入 at 字段会整体拒绝，提醒不会误取消。"""
        plan = self.reminders.save(self.task_id, "2026-10-18T14:30:00+08:00")
        for value in (
            {**self.time, "reference_time": "2026-10-18T10:00:00+08:00"},
            {**self.time, "precision": "date", "date": "2026-10-18"},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.reviews.apply_event(self.task_id, {"time": value})
        self.assertEqual(self.reviews.get_history("event", self.task_id), [])
        self.assertEqual(self.reminders.get(plan["reminder_id"])["status"], "pending")

    def test_cancel_and_reopen_event_use_override_without_changing_task_constraint(self):
        """取消事项通过独立覆盖表达；底层已结束状态阻止提醒，显式重开可恢复待办。"""
        self.reviews.apply_event(self.task_id, {"status": "cancelled"})
        raw = self.tasks.list_tasks()[0]
        self.assertEqual(raw["status"], "completed")
        self.assertEqual(self.reviews.task_overlay(raw)["status"], "cancelled")
        self.reviews.apply_event(self.task_id, {"status": "pending", "expected_version": 1})
        self.assertEqual(self.reviews.task_overlay(self.tasks.list_tasks()[0])["status"], "pending")
        self.tasks.mark_completed(self.task_id)
        self.assertEqual(self.reviews.task_overlay(self.tasks.list_tasks()[0])["status"], "completed")

    def test_minute_range_edit_preserves_two_explicit_boundaries(self):
        """新正式区间协议可人工纠正，不把起止时刻压缩成单个时间点。"""
        time_value = {
            "precision": "minute_range", "status": "resolved", "time_kind": "period",
            "time_text": "明天下午三点到四点", "start": "2026-10-18T15:00:00+08:00",
            "end": "2026-10-18T16:00:00+08:00", "reference_time": self.input.sent_at,
            "timezone": "UTC+08:00",
        }
        saved = self.reviews.apply_event(self.task_id, {"time_text": time_value["time_text"], "time": time_value})
        self.assertEqual(saved["time"], time_value)
        self.assertNotIn("at", saved["time"])

    def test_manual_document_labels_win_and_model_evidence_remains(self):
        """文件正文分类与人工标签独立保存，人工编辑不丢弃模型原始证据。"""
        key = self.document()
        auto = {"category": "研究", "labels": ["检索"], "summary": "论文", "evidence": [{"page": 1, "quote": "检索研究论文"}]}
        self.reviews.save_classification(key, auto)
        self.reviews.apply_document(key, {"category": "其他", "labels": ["已归档", "参考"]})
        result = self.reviews.document_overlay({"document_id": key, "filename": "paper.pdf"})
        self.assertEqual(result["classification_basis"], "manual_review")
        self.assertEqual(result["category"], "其他")
        self.assertEqual(result["classification"]["result"], auto)
        self.assertEqual(result["evidence"], auto["evidence"])
        self.assertEqual(self.reviews.classification_pages(key)[0]["page"], 1)

    def test_invalid_fields_empty_review_and_missing_entities_are_rejected(self):
        """严格边界拒绝原文修改、空编辑、空标签、未知记录和不安全错误。"""
        for value in ({}, {"text": "覆盖原文"}, {"labels": [" "]}, {"category": None}, {"labels": ["x" * 31]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.reviews.apply_message(self.message_id, value)
        with self.assertRaises(KeyError):
            self.reviews.apply_message("missing", {"labels": []})
        with self.assertRaises(KeyError):
            self.reviews.apply_event("missing", {"status": "cancelled"})
        self.assertEqual(self.reviews.detail_overlay(None), None)


if __name__ == "__main__":
    unittest.main()
