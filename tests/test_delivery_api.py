"""完整交付接口验收：临时真实存储与HTTP路由，外部模型、邮箱和通知均使用替身。"""

import copy
import importlib
import json
import os
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
MessageInput = importlib.import_module("student_assistant.message_models").MessageInput
MailSettings = importlib.import_module("student_assistant.mail_ingest").MailSettings


class DeliveryModel:
    """按真实结构化输出schema返回合成内容，保留文件与通知处理的实际业务链路。"""

    def __init__(self):
        """记录模型边界调用，可指定一次失败以验证持久队列重试。"""
        self.calls = []
        self.schema = None
        self.failure = None
        self.classification_quote = "毕业生招聘通知：请提交简历和作品集。"

    def with_structured_output(self, schema, *, method):
        """接收正式业务选择的输出模型，强制使用项目现有JSON模式约定。"""
        if method != "json_mode":
            raise AssertionError("必须使用JSON结构化输出")
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        """依据本次真实输入生成最小合成结果，不访问外部服务或本机环境配置。"""
        payload = json.loads(messages[-1][1])
        self.calls.append(copy.deepcopy(payload))
        if self.failure:
            raise self.failure
        if self.schema.__name__ == "DocumentClassification":
            result = {"category": "求职", "labels": ["招聘", "简历"], "summary": "招聘材料提交要求", "evidence": [{"page": 1, "quote": self.classification_quote}]}
        else:
            result = {
                "category": "研究", "summary": "合成通知摘要", "attachment_description": None,
                "attachment_count": len(payload.get("attachments", [])), "events": [],
            }
        return self.schema.model_validate(result)


class DeliveryEmbeddings:
    """仅替换向量模型推理，保留真实文档解析、SQLite与Qdrant索引。"""

    model_id = "delivery-api-offline-v1"
    dimension = 3

    def embed_documents(self, texts):
        """为每段合成正文生成固定非零向量，不宣称真实语义效果。"""
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text):
        """查询与文件使用同一固定向量空间，避免加载真实Embedding模型。"""
        return [1.0, 0.0, 0.0]


class DeliveryAPITests(unittest.TestCase):
    """通过实际服务生命周期验收纠错、文件原件、重点联系人与手机接入路由。"""

    def setUp(self):
        """隔离业务双库及所有原件目录，不读用户数据库、邮箱或模型凭证。"""
        temporary = tempfile.TemporaryDirectory(prefix="student_delivery_api_")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.model = DeliveryModel()
        self.factory = Mock(return_value=self.model)
        self.notifier = Mock()
        self.enterContext(patch.dict(os.environ, {"STUDENT_ASSISTANT_TRUST_CONTAINER_NETWORK": "false"}))
        self.now = datetime(2026, 10, 17, 2, 5, tzinfo=timezone.utc)
        self.sent_at = "2026-10-17T10:00:00+08:00"
        self.time = {
            "precision": "minute", "status": "resolved", "time_kind": "point",
            "time_text": "明天下午三点", "at": "2026-10-18T15:00:00+08:00",
            "reference_time": self.sent_at, "timezone": "UTC+08:00",
        }

    def clock(self):
        """提供固定北京时间10:05，使合成消息属于新消息且提醒仍在未来。"""
        return self.now

    def make_client(self, host="testclient"):
        """运行正式生命周期，关闭后台轮询并替换全部外部副作用边界。"""
        app = service.create_app(
            business_db=self.folder / "business.sqlite3", checkpoint_db=self.folder / "checkpoint.sqlite3",
            rag_dir=self.folder / "rag", model_factory=self.factory, embedding_factory=DeliveryEmbeddings,
            notifier_factory=Mock(return_value=self.notifier), mail_settings=MailSettings(), background_enabled=False,
        )
        client = self.enterContext(TestClient(app, client=(host, 12345)))
        app.state.reminder_runtime.clock = self.clock
        app.state.attention_runtime.clock = self.clock
        return client

    def seed_message(self, client, source_id="delivery-notice", *, sender="teacher-1"):
        """直接持久化一条已分析合成消息，纠错接口验收不额外消耗模型调用。"""
        source = MessageInput(
            source="manual", account_id="synthetic", source_message_id=source_id,
            sender_id=sender, sent_at=self.sent_at,
            text="明天下午三点在302开组会，附件是材料。", attachments=[{"filename": "materials.zip"}],
        )
        store = client.app.state.message_store
        message_id = store.receive(source)["message_id"]
        detail = store.complete_analysis(message_id, {
            "category": "研究", "summary": "组会通知", "attachment_count": 1,
            "attachment_description": "材料", "events": [],
        }, [{"title": "组会", "location": "302", "time_text": self.time["time_text"],
             "time": self.time, "evidence": "明天下午三点在302开组会"}])
        return detail

    def check(self, response, status=200):
        """读取预期状态码的安全JSON响应，将路由错误直接定位到具体场景。"""
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def enable_reminders(self, client, *, date_only_time=None):
        """明确启用控制台提醒规则，测试通知边界仍由替身接管。"""
        return self.check(client.put("/reminders/policy", json={
            "enabled": True, "lead_minutes": 30, "date_only_time": date_only_time, "channel": "console",
        }))

    def upload_document(self, client, *, message=None):
        """上传真实合成文本并建立离线向量索引，可精确绑定消息附件线索。"""
        params = {"filename": "研究论文.md"}
        if message:
            params.update({"message_id": message["message_id"], "attachment_index": 0})
        result = self.check(client.post("/documents", params=params, content=self.model.classification_quote.encode("utf-8"), headers={"Content-Type": "application/octet-stream"}))
        return result["document"]

    def notification(self, *, text="请查收组会安排", posted_at="2026-10-17T10:00:00+08:00"):
        """构造安卓实际通知协议，字段明确表示通知摘要而非聊天完整消息。"""
        return {"device_id": "synthetic-phone", "package_name": "com.tencent.mm", "notification_key": "notification-1", "posted_at": posted_at, "title": "合成老师", "text": text, "conversation_id": "synthetic-conversation"}

    def pairing(self, client):
        """经本机管理接口取得本轮临时令牌，测试不打印或保存到结果文档。"""
        return self.check(client.post("/notifications/config", json={"enabled": True, "rotate": False}))["token"]

    def test_message_revision_changes_filter_and_retains_original_history(self):
        """人工分类与多标签参与HTTP筛选，原文及最初模型分析保持不变。"""
        client = self.make_client()
        message = self.seed_message(client)
        message_id = message["message_id"]
        revised = self.check(client.put(f"/messages/{message_id}/review", json={"category": "求职", "labels": ["招聘", "待读"], "summary": "人工摘要"}))["message"]
        self.assertEqual(revised["analysis"]["category"], "求职")
        self.assertEqual(revised["original_analysis"]["category"], "研究")
        self.assertEqual(revised["source_message"], message["source_message"])
        self.assertEqual(self.check(client.get("/messages", params={"category": "求职"}))["count"], 1)
        self.assertEqual(self.check(client.get("/messages", params={"category": "研究"}))["count"], 0)
        history = self.check(client.get(f"/messages/{message_id}/reviews"))["history"]
        self.assertEqual([item["version"] for item in history], [0, 1])
        self.assertEqual(len(self.model.calls), 0)

    def test_message_revision_conflict_missing_and_invalid_input(self):
        """旧修订返回409，未知消息404，修改原文及空编辑422。"""
        client = self.make_client()
        message_id = self.seed_message(client)["message_id"]
        self.check(client.put(f"/messages/{message_id}/review", json={"labels": ["重要"]}))
        self.check(client.put(f"/messages/{message_id}/review", json={"category": "其他"}), 409)
        self.check(client.put("/messages/" + "f" * 24 + "/review", json={"labels": []}), 404)
        for payload in ({}, {"text": "覆盖原消息"}, {"labels": ["x" * 31]}):
            self.check(client.put(f"/messages/{message_id}/review", json=payload), 422)

    def test_event_revision_replans_without_losing_original_reminder_version(self):
        """修正时间会取消旧计划并按新时间创建版本，重复重排不继续增版。"""
        client = self.make_client()
        message = self.seed_message(client)
        task_id = message["events"][0]["task_id"]
        self.enable_reminders(client)
        first = self.check(client.post("/reminders/plan", json={"message_id": message["message_id"]}))["planned"][0]
        corrected = {**self.time, "at": "2026-10-18T16:00:00+08:00", "time_text": "明天下午四点"}
        result = self.check(client.put(f"/tasks/{task_id}/review", params={"replan": True}, json={"time_text": corrected["time_text"], "time": corrected, "location": "402"}))
        self.assertEqual(result["event"]["time"], corrected)
        planned = result["reminder_planning"]["planned"][0]
        self.assertEqual(planned["remind_at"], "2026-10-18T15:30:00+08:00")
        self.assertEqual(planned["reminder_id"], first["reminder_id"])
        self.assertEqual(planned["version"], first["version"] + 1)
        with closing(sqlite3.connect(self.folder / "business.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT status FROM reminder_jobs WHERE reminder_id=? AND version=1", (first["reminder_id"],)).fetchone()[0], "cancelled")
        repeated = self.check(client.post(f"/tasks/{task_id}/replan"))
        self.assertEqual(repeated["planned"], [])
        self.assertEqual(self.check(client.get("/reminders"))["reminders"][0]["version"], planned["version"])

    def test_event_date_range_keeps_precision_and_original_reference(self):
        """日期区间人工修正保留起止日期，使用明确钟点策略规划而不新增at。"""
        client = self.make_client()
        message = self.seed_message(client)
        task_id = message["events"][0]["task_id"]
        self.enable_reminders(client, date_only_time="09:00")
        value = {"precision": "date_range", "status": "resolved", "time_kind": "period", "time_text": "下周", "start_date": "2026-10-19", "end_date": "2026-10-25", "reference_time": self.sent_at, "timezone": "UTC+08:00"}
        result = self.check(client.put(f"/tasks/{task_id}/review", params={"replan": True}, json={"time_text": "下周", "time": value}))
        self.assertNotIn("at", result["event"]["time"])
        self.assertEqual(result["reminder_planning"]["planned"][0]["remind_at"], "2026-10-19T09:00:00+08:00")
        invalid = {**value, "reference_time": "2026-10-18T10:00:00+08:00"}
        self.check(client.put(f"/tasks/{task_id}/review", json={"expected_version": 1, "time": invalid}), 422)
        viewed = self.check(client.get(f"/messages/{message['message_id']}"))
        self.assertEqual(viewed["events"][0]["time"], value)

    def test_event_cancel_and_replan_do_not_create_new_plan(self):
        """取消事项通过覆盖状态展示，旧计划取消且显式重排仍跳过已结束事项。"""
        client = self.make_client()
        message = self.seed_message(client)
        task_id = message["events"][0]["task_id"]
        self.enable_reminders(client)
        self.check(client.post("/reminders/plan", json={"message_id": message["message_id"]}))
        self.check(client.put(f"/tasks/{task_id}/review", params={"replan": True}, json={"status": "cancelled"}))
        viewed = self.check(client.get(f"/messages/{message['message_id']}"))
        self.assertEqual(viewed["events"][0]["status"], "cancelled")
        self.assertEqual(self.check(client.post(f"/tasks/{task_id}/replan"))["planned"], [])
        self.assertEqual(self.check(client.get("/reminders"))["reminders"][0]["status"], "cancelled")

    def test_document_classify_and_manual_review_preserve_body_evidence(self):
        """显式分类读取正文而非误导文件名，人工纠正保留模型证据和修订历史。"""
        client = self.make_client()
        document = self.upload_document(client)
        document_id = document["document_id"]
        self.assertEqual(len(self.model.calls), 0)
        classified = self.check(client.post(f"/documents/{document_id}/classify"))["document"]
        self.assertEqual(classified["category"], "求职")
        self.assertEqual(classified["classification_basis"], "document_text")
        self.assertEqual(classified["classification"]["result"]["evidence"][0]["quote"], self.model.classification_quote)
        self.assertNotIn("filename", self.model.calls[0])
        reviewed = self.check(client.put(f"/documents/{document_id}/review", json={"category": "其他", "labels": ["已读", "归档"]}))["document"]
        self.assertEqual(reviewed["classification_basis"], "manual_review")
        self.assertEqual(reviewed["classification"]["result"]["category"], "求职")
        self.assertEqual([item["version"] for item in self.check(client.get(f"/documents/{document_id}/reviews"))["history"]], [0, 1])
        self.check(client.put(f"/documents/{document_id}/review", json={"labels": []}), 409)

    def test_failed_document_classification_is_safe_and_retryable(self):
        """伪造正文引用返回安全失败，结果落盘后修复模型输出可显式重试。"""
        client = self.make_client()
        document = self.upload_document(client)
        document_id = document["document_id"]
        real_quote = self.model.classification_quote
        self.model.classification_quote = "不存在的正文片段"
        failed = client.post(f"/documents/{document_id}/classify")
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn(self.model.classification_quote, failed.text)
        detail = self.check(client.get(f"/documents/{document_id}"))
        self.assertEqual(detail["classification"]["status"], "failed")
        self.model.classification_quote = real_quote
        self.check(client.post(f"/documents/{document_id}/classify"))
        self.assertEqual(self.check(client.get(f"/documents/{document_id}"))["classification"]["status"], "completed")

    def test_arbitrary_attachment_bytes_round_trip_and_original_hint_is_unchanged(self):
        """任意格式真实字节可归档下载，不把文件名或原mentioned字段当作已取得文件。"""
        client = self.make_client()
        message = self.seed_message(client)
        path = f"/messages/{message['message_id']}/attachments/0"
        data = b"PK\x03\x04synthetic zip\x00\xff"
        self.check(client.post(path, params={"filename": "真实材料.zip"}, content=data, headers={"Content-Type": "application/octet-stream"}))
        downloaded = client.get(path + "/file")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, data)
        self.assertIn("filename*=UTF-8", downloaded.headers["Content-Disposition"])
        viewed = self.check(client.get(f"/messages/{message['message_id']}"))
        self.assertEqual(viewed["attachments"][0]["availability"], "available")
        self.assertEqual(viewed["source_message"]["attachments"][0]["availability"], "mentioned")
        self.assertEqual(len(self.model.calls), 0)

    def test_attachment_boundary_media_size_conflict_and_missing_file(self):
        """附件入口拒绝错误媒体类型、超限内容和覆盖不同原件，缺少文件返回404。"""
        client = self.make_client()
        message = self.seed_message(client)
        path = f"/messages/{message['message_id']}/attachments/0"
        self.check(client.get(path + "/file"), 404)
        self.check(client.post(path, params={"filename": "a.zip"}, content=b"a"), 415)
        headers = {"Content-Type": "application/octet-stream"}
        self.check(client.post(path, params={"filename": "a.zip"}, content=b"a", headers=headers))
        self.check(client.post(path, params={"filename": "a.zip"}, content=b"different", headers=headers), 422)
        self.check(client.post(path, params={"filename": "large.zip"}, content=b"x" * (25 * 1024 * 1024 + 1), headers=headers), 413)
        self.assertEqual(client.get(path + "/file").content, b"a")

    def test_attachment_download_falls_back_to_existing_rag_original(self):
        """已有RAG绑定原件也能从统一附件下载入口访问，无需用户重复补传。"""
        client = self.make_client()
        message = self.seed_message(client)
        self.upload_document(client, message=message)
        response = client.get(f"/messages/{message['message_id']}/attachments/0/file")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.model.classification_quote.encode("utf-8"))

    def test_attention_policy_new_message_list_and_acknowledge(self):
        """重点联系人新消息进入独立列表，标记已读不创建任务也不投递系统通知。"""
        client = self.make_client()
        message = self.seed_message(client)
        self.check(client.put("/roles", json={"source": "manual", "account_id": "synthetic", "sender_id": "teacher-1", "role": "导师", "important": True}))
        self.check(client.put("/attention/policy", json={"enabled": True, "channel": "console", "max_age_minutes": 60}))
        self.assertTrue(self.check(client.get("/attention/policy"))["enabled"])
        current = client.app.state.message_store.get_message(message["message_id"])
        created = client.app.state.attention_store.register(current, self.now)
        alerts = self.check(client.get("/attention"))["alerts"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["status"], "pending")
        self.assertEqual(self.check(client.post(f"/attention/{created['alert_id']}/read"))["status"], "read")
        self.assertEqual(self.check(client.get("/attention"))["alerts"][0]["status"], "read")
        self.check(client.post("/attention/" + "f" * 24 + "/read"), 404)
        self.notifier.send.assert_not_called()

    def test_notification_auth_queue_duplicate_and_actual_processing(self):
        """无令牌拒绝，合法摘要先202入队，同一快照幂等且显式处理后保留完整性标记。"""
        client = self.make_client()
        self.check(client.post("/notifications", json=self.notification()), 401)
        token = self.pairing(client)
        headers = {"Authorization": "Bearer " + token}
        queued = self.check(client.post("/notifications", json=self.notification(), headers=headers), 202)
        self.assertEqual(queued["processing_status"], "pending")
        self.assertIsNone(queued["message_id"])
        self.assertEqual(len(self.model.calls), 0)
        repeated = self.check(client.post("/notifications", json=self.notification(posted_at="2026-10-17T10:01:00+08:00"), headers=headers), 202)
        self.assertEqual(repeated["status"], "exists")
        self.assertEqual(repeated["notification_id"], queued["notification_id"])
        outcome = client.portal.call(client.app.state.notification_runtime.run_once)
        self.assertEqual(outcome, {"processed": 1, "failed": 0})
        messages = self.check(client.get("/messages"))["messages"]
        self.assertEqual(messages[0]["source_message"]["source"], "wechat")
        self.assertEqual(messages[0]["source_metadata"]["completeness"], "notification_excerpt")
        self.assertFalse(messages[0]["source_metadata"]["sender_identity_verified"])
        status = self.check(client.get("/notifications/status"))
        self.assertEqual(status["counts"]["processed"], 1)
        self.assertNotIn(token, json.dumps(status))

    def test_notification_token_rotation_disable_and_invalid_package(self):
        """轮换或停用使旧凭证立即失效，未允许应用不能进入通知队列。"""
        client = self.make_client()
        first = self.pairing(client)
        rotated = self.check(client.post("/notifications/config", json={"enabled": True, "rotate": True}))["token"]
        self.assertNotEqual(first, rotated)
        self.check(client.post("/notifications", json=self.notification(), headers={"Authorization": "Bearer " + first}), 401)
        self.check(client.post("/notifications", json={**self.notification(), "package_name": "unrelated.app"}, headers={"Authorization": "Bearer " + rotated}), 422)
        self.check(client.post("/notifications/config", json={"enabled": False, "rotate": False}))
        self.check(client.post("/notifications", json=self.notification(), headers={"Authorization": "Bearer " + rotated}), 401)

    def test_notification_processing_failure_keeps_safe_retry_state(self):
        """模型失败不会丢弃已202接收摘要，队列仅保存安全错误类型等待退避重试。"""
        client = self.make_client()
        token = self.pairing(client)
        self.check(client.post("/notifications", json=self.notification(), headers={"Authorization": "Bearer " + token}), 202)
        self.model.failure = RuntimeError("synthetic-provider-secret")
        self.assertEqual(client.portal.call(client.app.state.notification_runtime.run_once), {"processed": 0, "failed": 1})
        status = self.check(client.get("/notifications/status"))
        self.assertEqual(status["counts"]["retry"], 1)
        self.assertNotIn("synthetic-provider-secret", json.dumps(status))
        self.notifier.send.assert_not_called()

    def test_lan_can_upload_notification_but_cannot_manage_or_read_private_data(self):
        """模拟手机局域网地址只能访问通知与APK固定入口，配对和私人数据管理返回403。"""
        client = self.make_client("192.168.1.2")
        token = client.app.state.bridge_configuration.configure(True)["token"]
        self.check(client.post("/notifications", json=self.notification(), headers={"Authorization": "Bearer " + token}), 202)
        for path in ("/messages", "/documents", "/notifications/status", "/attention", "/health"):
            self.check(client.get(path), 403)
        self.check(client.post("/notifications/config", json={"enabled": True}), 403)
        apk = client.get("/notifications/app.apk")
        self.assertIn(apk.status_code, (200, 404))
        if apk.status_code == 200:
            self.assertEqual(apk.headers["Content-Type"], "application/vnd.android.package-archive")


if __name__ == "__main__":
    unittest.main()
