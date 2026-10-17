"""正式待办状态一致性：取消不会变成完成，SQL、Agent工具与HTTP使用同一有效状态。"""

import importlib
import json
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
business = importlib.import_module("student_assistant.business_tasks")
TaskStore = importlib.import_module("student_assistant.bridge").TaskStore
MessageStore = importlib.import_module("student_assistant.message_store").MessageStore
MessageInput = importlib.import_module("student_assistant.message_models").MessageInput
ReviewStore = importlib.import_module("student_assistant.review_store").ReviewStore
build_turn_tools = importlib.import_module("student_assistant.tools").build_turn_tools
service = importlib.import_module("student_assistant.service")
MailSettings = importlib.import_module("student_assistant.mail_ingest").MailSettings


class CancelledOfflineEmbeddings:
    """仅提供初始化需要的离线向量配置，本组接口不会执行文档推理。"""

    model_id = "cancelled-state-offline-v1"
    dimension = 3

    def embed_documents(self, texts):
        """用固定非零向量替代模型调用，禁止测试下载或加载真实模型。"""
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, query):
        """提供相同向量空间的固定查询结果，不访问外部服务。"""
        return [1.0, 0.0, 0.0]


class CancelledTaskConsistencyTests(unittest.TestCase):
    """使用真实临时SQLite种出待办、完成与取消三种状态，验证全链路表达一致。"""

    def setUp(self):
        """通过既有消息保存和人工取消业务准备三项，不直接改变旧tasks状态约束。"""
        temporary = tempfile.TemporaryDirectory(prefix="student_cancelled_tasks_")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.store = business.BusinessTaskStore(self.folder / "business.sqlite3")
        self.messages = MessageStore(self.store)
        self.reviews = ReviewStore(self.store)
        envelope = MessageInput(source="manual", source_message_id="three-states", sender_id="teacher-1", sent_at="2026-10-17T10:00:00+08:00", text="提交报告，整理笔记，参加组会。")
        message_id = self.messages.receive(envelope)["message_id"]
        self.message_id = message_id
        detail = self.messages.complete_analysis(message_id, {"category": "研究", "summary": "三项安排", "events": []}, [
            {"title": "提交报告", "time": None, "evidence": "提交报告"},
            {"title": "整理笔记", "time": None, "evidence": "整理笔记"},
            {"title": "参加组会", "time": None, "evidence": "参加组会"},
        ])
        self.pending_id, self.completed_id, self.cancelled_id = [event["task_id"] for event in detail["events"]]
        self.store.mark_completed(self.completed_id)
        self.reviews.apply_event(self.cancelled_id, {"status": "cancelled"})

    def test_sql_effective_states_separate_cancelled_from_completed(self):
        """取消仅进入cancelled集合，正式列表仍返回原五字段且待办统计保持准确。"""
        self.assertEqual([row["id"] for row in self.store.list_tasks("pending")], [self.pending_id])
        self.assertEqual([row["id"] for row in self.store.list_tasks("completed")], [self.completed_id])
        self.assertEqual([row["id"] for row in self.store.list_tasks("cancelled")], [self.cancelled_id])
        all_tasks = self.store.list_tasks()
        self.assertEqual([row["status"] for row in all_tasks], ["pending", "completed", "cancelled"])
        self.assertEqual(set(all_tasks[2]), {"id", "title", "time_text", "location", "status"})
        self.assertEqual(self.store.count_pending(), 1)

    def test_cancelled_completion_rejected_until_explicit_reopen(self):
        """直接完成已取消事项会拒绝，明确重开后才允许正常完成且保持幂等。"""
        with self.assertRaises(business.TaskCancelledError):
            self.store.mark_completed(self.cancelled_id)
        self.assertEqual(self.store.list_tasks("cancelled")[0]["id"], self.cancelled_id)
        self.reviews.apply_event(self.cancelled_id, {"expected_version": 1, "status": "pending"})
        completed = self.store.mark_completed(self.cancelled_id)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.store.mark_completed(self.cancelled_id), completed)
        self.assertEqual(self.store.list_tasks("cancelled"), [])

    def test_duplicate_save_reports_actual_cancelled_status_without_reopening(self):
        """内容指纹复用的任务若已有取消修订，save_task如实返回取消而不是完成或待办。"""
        task = self.store.save_task("按指纹复用的事项")["task"]
        # 构造与人工取消相同的持久状态，覆盖按内容指纹保存的独立任务路径。
        with self.reviews._connection(write=True) as connection:
            connection.execute("UPDATE tasks SET status='completed' WHERE id=?", (task["id"],))
            connection.execute("INSERT INTO review_current VALUES ('event',?,1,?,?)", (
                task["id"], json.dumps({"status": "cancelled"}), datetime.now(timezone.utc).isoformat(),
            ))
        duplicate = self.store.save_task("按指纹复用的事项")
        self.assertEqual(duplicate["status"], "exists")
        self.assertEqual(duplicate["task"]["status"], "cancelled")
        self.assertNotIn(task["id"], [row["id"] for row in self.store.list_tasks("completed")])
        # 旧教学接口仍只认识物理pending/completed，未被正式服务改造影响。
        legacy = TaskStore(self.store.db_path)
        self.assertEqual(next(row for row in legacy.list_tasks() if row["id"] == task["id"])["status"], "completed")

    def test_formal_tools_share_queried_ids_and_never_complete_cancelled(self):
        """新查询schema能查取消状态，完成工具共享查询证据且查过也不越过取消保护。"""
        queried = set()
        tools = build_turn_tools(self.store, Mock(), [], queried_ids=queried)
        self.assertEqual(tools["complete_task"].invoke({"task_id": self.pending_id})["status"], "error")
        cancelled = tools["list_tasks"].invoke({"status": "cancelled"})
        self.assertEqual(cancelled["tasks"][0]["status"], "cancelled")
        self.assertIn(self.cancelled_id, queried)
        denied = tools["complete_task"].invoke({"task_id": self.cancelled_id})
        self.assertEqual(denied["status"], "error")
        self.assertIn("已取消", denied["error"])
        completed = tools["list_tasks"].invoke({"status": "completed"})
        self.assertEqual([task["id"] for task in completed["tasks"]], [self.completed_id])
        tools["list_tasks"].invoke({"status": "pending"})
        self.assertEqual(tools["complete_task"].invoke({"task_id": self.pending_id})["status"], "completed")
        old_schema = importlib.import_module("assistant_tools").ListTasksInput
        with self.assertRaises(ValueError):
            old_schema(status="cancelled")

    def test_http_and_engine_use_effective_status_and_cancelled_complete_returns_409(self):
        """正式引擎用业务适配存储，HTTP列表显示取消，完成端点409且状态不会被改写。"""
        app = service.create_app(
            business_db=self.store.db_path, checkpoint_db=self.folder / "checkpoint.sqlite3", rag_dir=self.folder / "rag",
            model_factory=Mock(return_value=Mock()), embedding_factory=CancelledOfflineEmbeddings,
            notifier_factory=Mock(return_value=Mock()), mail_settings=MailSettings(), background_enabled=False,
        )
        with TestClient(app) as client:
            self.assertIsInstance(client.app.state.engine.store, business.BusinessTaskStore)
            all_tasks = client.get("/tasks")
            self.assertEqual(all_tasks.status_code, 200)
            rows = {task["id"]: task for task in all_tasks.json()["tasks"]}
            self.assertEqual(rows[self.cancelled_id]["status"], "cancelled")
            self.assertEqual(client.get("/tasks/pending").json()["count"], 1)
            refused = client.post(f"/tasks/{self.cancelled_id}/complete")
            self.assertEqual(refused.status_code, 409)
            self.assertIn("已取消", refused.json()["detail"]["message"])
            message = client.get(f"/messages/{self.message_id}").json()
            self.assertEqual(message["events"][2]["status"], "cancelled")
            repeated = client.post(f"/tasks/{self.completed_id}/complete")
            self.assertEqual(repeated.status_code, 200)

    def test_business_adapter_reads_old_database_without_review_table(self):
        """旧库尚未建立修订表也能查询与完成，取消筛选为空且未知ID仍返回None。"""
        old = business.BusinessTaskStore(self.folder / "old.sqlite3")
        task = old.save_task("旧库事项")["task"]
        self.assertEqual(old.list_tasks("cancelled"), [])
        self.assertEqual(old.list_tasks("pending"), [task])
        self.assertEqual(old.mark_completed(task["id"])["status"], "completed")
        self.assertIsNone(old.mark_completed("missing"))
        with self.assertRaises(ValueError):
            old.list_tasks("invalid")


if __name__ == "__main__":
    unittest.main()
