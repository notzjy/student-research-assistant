"""界面显式完成待办的接口测试：验证作用范围、提醒取消和不调用模型的行为。"""

import importlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

service = importlib.import_module("student_assistant.service")
from student_assistant.mail_ingest import MailSettings


class UnusedEmbeddings:
    """提供存储初始化所需配置，完成待办不应加载任何向量模型。"""

    model_id = "completion-api-offline-vector"
    dimension = 3

    def embed_documents(self, texts):
        """如果完成接口错误触发索引，则立即让测试失败。"""
        raise AssertionError("完成待办不应调用Embedding")

    def embed_query(self, text):
        """如果完成接口错误触发检索，则立即让测试失败。"""
        raise AssertionError("完成待办不应检索向量")


class TaskCompletionAPITests(unittest.TestCase):
    """在独立业务库中通过真实ASGI请求完成用户明确选择的事项。"""

    def setUp(self):
        """隔离数据库并关闭后台收件提醒，任何模型和通知调用都视为错误。"""
        temporary = tempfile.TemporaryDirectory(prefix="student-task-completion-")
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        self.model_factory = Mock(side_effect=AssertionError("不应调用模型"))
        self.notifier = Mock()
        app = service.create_app(
            business_db=folder / "business.sqlite3", checkpoint_db=folder / "checkpoint.sqlite3",
            rag_dir=folder / "rag", model_factory=self.model_factory,
            embedding_factory=UnusedEmbeddings, notifier_factory=lambda: self.notifier,
            mail_settings=MailSettings(), background_enabled=False,
        )
        self.client = self.enterContext(TestClient(app))
        self.store = app.state.engine.store
        self.reminders = app.state.reminder_store

    def test_explicit_completion_is_scoped_idempotent_and_traceable_without_model(self):
        """无需先查询工具即可完成指定事项，重复请求不影响另一项并保存安全追踪。"""
        chosen = self.store.save_task("用户选中的事项")["task"]
        other = self.store.save_task("仍需完成的另一项")["task"]
        first = self.client.post(f"/tasks/{chosen['id']}/complete")
        repeated = self.client.post(f"/tasks/{chosen['id']}/complete")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), {"status": "completed", "task": {**chosen, "status": "completed"}})
        self.assertEqual(repeated.json(), first.json())
        self.assertEqual(self.store.list_tasks("pending"), [other])
        trace_id = first.headers["X-Trace-ID"]
        trace = self.client.get(f"/traces/{trace_id}").json()
        self.assertEqual(trace["operation"], "tasks.complete")
        self.assertEqual(trace["status"], "completed")
        self.model_factory.assert_not_called()
        self.notifier.send.assert_not_called()

    def test_completion_cancels_unsubmitted_reminders_but_retains_origins_and_submitted(self):
        """同库完成触发器取消手工及自动待发提醒，已提交通知和其他任务保持原状。"""
        chosen = self.store.save_task("包含多种提醒的事项")["task"]
        other = self.store.save_task("另一项提醒事项")["task"]
        automatic = self.reminders.save_automatic(chosen["id"], "2035-01-01T09:00:00+08:00")["reminder"]
        manual = self.reminders.save(chosen["id"], "2035-01-01T10:00:00+08:00")
        submitted = self.reminders.save(chosen["id"], "2029-01-01T09:00:00+08:00")
        leased = self.reminders.save(chosen["id"], "2029-01-01T10:00:00+08:00")
        untouched = self.reminders.save(other["id"], "2035-01-01T09:00:00+08:00")
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        claim, leased_claim = self.reminders.claim_due(now, limit=2)
        self.assertEqual(claim["reminder_id"], submitted["reminder_id"])
        self.assertEqual(leased_claim["reminder_id"], leased["reminder_id"])
        self.assertTrue(self.reminders.mark_submitted(claim["reminder_id"], claim["version"], claim["lease_token"], now))

        response = self.client.post(f"/tasks/{chosen['id']}/complete")
        self.assertEqual(response.status_code, 200, response.text)
        for reminder, origin in ((automatic, "automatic"), (manual, "manual"), (leased, "manual")):
            detail = self.reminders.get(reminder["reminder_id"])
            self.assertEqual(detail["status"], "cancelled")
            self.assertEqual(detail["origin"], origin)
        # 已认领任务在用户完成待办后也失效，迟到的提交回执不能把取消状态改回已提交。
        self.assertFalse(self.reminders.mark_submitted(
            leased_claim["reminder_id"], leased_claim["version"], leased_claim["lease_token"], now,
        ))
        self.assertEqual(self.reminders.get(submitted["reminder_id"])["status"], "submitted")
        self.assertEqual(self.reminders.get(untouched["reminder_id"])["status"], "pending")
        self.model_factory.assert_not_called()
        self.notifier.send.assert_not_called()

    def test_unknown_or_invalid_identifier_never_changes_existing_tasks(self):
        """未知合法编号返回404，非法编号返回422且不会批量修改任何已有待办。"""
        task = self.store.save_task("必须保留的未完成事项")["task"]
        response = self.client.post("/tasks/0000000000000000/complete")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["message"], "待办不存在，请刷新列表后再操作。")
        for task_id in ("too-short", "a" * 17, "A" * 16, "x" * 16):
            with self.subTest(task_id=task_id):
                self.assertEqual(self.client.post(f"/tasks/{task_id}/complete").status_code, 422)
        self.assertEqual(self.store.list_tasks(), [task])
        self.model_factory.assert_not_called()
        self.notifier.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
