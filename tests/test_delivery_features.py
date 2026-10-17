"""交付功能离线验收：安卓收件、重点联系人到达提醒和任意格式附件归档。"""

import hashlib
import importlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

gateway = importlib.import_module("student_assistant.notification_gateway")
attention_module = importlib.import_module("student_assistant.attention")
attachment_module = importlib.import_module("student_assistant.attachment_store")
message_module = importlib.import_module("student_assistant.message_store")
models = importlib.import_module("student_assistant.message_models")
bridge = importlib.import_module("student_assistant.bridge")


class MutableClock:
    """提供可推进的UTC时钟，测试退避和消息过期时不实际等待。"""

    def __init__(self, now):
        """保存测试当前时刻，不依赖真实系统日期。"""
        self.now = now

    def __call__(self):
        """返回当前测试时刻，供正式runtime注入使用。"""
        return self.now


class FakeProcessor:
    """替代模型分析但使用真实MessageStore保存收到的标准消息。"""

    def __init__(self, messages, failures=0, error_type=RuntimeError):
        """指定前几次调用失败，便于验证持久队列和错误类型脱敏。"""
        self.messages = messages
        self.failures = failures
        self.error_type = error_type
        self.calls = []

    async def process(self, payload):
        """记录输入并持久化原文；合成失败不携带真实凭证或发送任何消息。"""
        self.calls.append(payload)
        if self.failures:
            self.failures -= 1
            raise self.error_type("SYNTHETIC_PRIVATE_ERROR_BODY")
        received = self.messages.receive(payload)
        return {"message": self.messages.get_message(received["message_id"])}


class FakeNotifier:
    """记录通知尝试，仅在内存执行，不接触Windows桌面或邮箱。"""

    def __init__(self, failures=0, callback=None):
        """允许合成失败或投递中的已读回调以测试迟到回执。"""
        self.failures = failures
        self.callback = callback
        self.calls = []

    def send(self, value):
        """记录本次投递，可主动抛出仅含合成数据的异常。"""
        self.calls.append(dict(value))
        if self.callback is not None:
            self.callback(value)
        if self.failures:
            self.failures -= 1
            raise OSError("SYNTHETIC_PRIVATE_NOTIFICATION_DETAIL")


class DeliveryFeatureTests(unittest.IsolatedAsyncioTestCase):
    """所有用例使用独立临时目录、业务库和注入的处理器/通知器。"""

    def setUp(self):
        """建立真实新增表，并冻结安卓收件器时钟以确定性验证重试。"""
        temporary = tempfile.TemporaryDirectory(prefix="assistant-delivery-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tasks = bridge.TaskStore(self.root / "business.sqlite3")
        self.messages = message_module.MessageStore(self.tasks)
        self.notifications = gateway.NotificationStore(self.messages)
        self.attention = attention_module.AttentionStore(self.messages)
        self.archive = attachment_module.AttachmentStore(self.messages, self.root / "originals")
        self.clock = MutableClock(datetime(2026, 10, 17, 2, tzinfo=timezone.utc))
        self.gateway_datetime = self.enterContext(patch.object(gateway, "datetime", wraps=datetime))
        self.gateway_datetime.now.side_effect = self.gateway_now
        self.sequence = 0

    def gateway_now(self, requested_timezone=None):
        """让通知队列的时间戳随测试时钟推进，避免修改真实业务数据。"""
        return self.clock.now.astimezone(requested_timezone) if requested_timezone else self.clock.now

    def payload(self, **updates):
        """创建只含合成导师文字的安卓通知，并执行正式输入验证。"""
        value = {
            "device_id": "test-device", "package_name": "com.tencent.mm", "notification_key": "notice-1",
            "posted_at": self.clock.now.isoformat(), "title": "示例老师", "text": "明天15:00开组会。",
            "conversation_id": "synthetic-conversation",
        }
        value.update(updates)
        return gateway.NotificationInput.model_validate(value)

    def seed_message(self, *, important=True, age_minutes=0, filenames=()):
        """保存一条完整合成消息和用户角色配置，返回真实数据库详情。"""
        self.sequence += 1
        payload = models.MessageInput(
            source="manual", account_id="synthetic-account", source_message_id=f"message-{self.sequence}",
            sender_id=f"sender-{self.sequence}", sender_name="示例老师",
            sent_at=(self.clock.now - timedelta(minutes=age_minutes)).isoformat(), text="研究通知及参考附件。",
            attachments=[models.AttachmentHint(filename=name) for name in filenames],
        )
        received = self.messages.receive(payload)
        self.messages.upsert_role(models.RoleRule(
            source=payload.source, account_id=payload.account_id, sender_id=payload.sender_id,
            role="导师", important=important,
        ))
        return self.messages.complete_analysis(received["message_id"], {
            "category": "研究", "summary": "研究通知，保留参考附件。", "events": [],
            "attachment_description": "参考附件" if filenames else None, "attachment_count": len(filenames),
        }, [])

    def enable_attention(self, *, max_age_minutes=60):
        """明确启用console配置；实际投递仍由FakeNotifier拦截。"""
        return self.attention.set_policy({"enabled": True, "channel": "console", "max_age_minutes": max_age_minutes})

    def update_role(self, message, important):
        """模拟用户修改精确角色规则，不通过昵称猜测角色。"""
        source = message["source_message"]
        self.messages.upsert_role(models.RoleRule(
            source=source["source"], account_id=source["account_id"], sender_id=source["sender_id"],
            role="导师", important=important,
        ))

    def test_notification_schema_rejects_unsupported_packages_naive_time_and_oversize(self):
        """输入边界拒绝其他应用、无时区时间和超长摘要，数据库保持空队列。"""
        invalid = [
            {"package_name": "com.other.private"}, {"posted_at": "2026-10-17T10:00:00"},
            {"text": "字" * 5001}, {"big_text": "字" * 5001},
            {"text_lines": ["字" * 4001] * 3}, {"text_lines": ["行"] * 31},
            {"device_id": " "},
        ]
        for change in invalid:
            with self.subTest(change=list(change)), self.assertRaises(ValidationError):
                self.payload(**change)
        self.assertEqual(self.notifications.status()["counts"], {})

    def test_notification_snapshot_dedup_ignores_reposted_time_but_keeps_changes(self):
        """相同设备通知只变发布时间时去重，正文更新或设备变化会建立新快照。"""
        first = self.notifications.receive(self.payload())
        later = self.notifications.receive(self.payload(posted_at=(self.clock.now + timedelta(minutes=1)).isoformat()))
        changed = self.notifications.receive(self.payload(text="组会改到明天17:00。"))
        other_device = self.notifications.receive(self.payload(device_id="second-device"))
        self.assertEqual(first["status"], "queued")
        self.assertEqual(later["status"], "exists")
        self.assertEqual(later["notification_id"], first["notification_id"])
        self.assertNotEqual(changed["notification_id"], first["notification_id"])
        self.assertNotEqual(other_device["notification_id"], first["notification_id"])
        self.assertEqual(self.notifications.status()["counts"], {"pending": 3})

    async def test_notification_envelope_excerpt_metadata_and_source_identity(self):
        """通知长摘要优先保存且标为摘录，公开元数据不伪称全文或已验证身份。"""
        payload = self.payload(big_text="完整可见的通知摘要", text_lines=["折叠行"], text="短摘要")
        queued = self.notifications.receive(payload)
        processor = FakeProcessor(self.messages)
        result = await gateway.NotificationRuntime(self.notifications, processor).run_once()
        self.assertEqual(result["processed"], 1)
        envelope = processor.calls[0]
        self.assertEqual(envelope.text, "完整可见的通知摘要")
        self.assertEqual(envelope.sent_at, payload.posted_at)
        self.assertEqual(envelope.source, "wechat")
        self.assertTrue(envelope.sender_id.startswith("notification-"))
        received = self.notifications.receive(payload)
        metadata = self.notifications.metadata(received["message_id"])
        self.assertEqual(metadata["completeness"], "notification_excerpt")
        self.assertEqual(metadata["time_basis"], "notification_posted")
        self.assertFalse(metadata["sender_identity_verified"])
        self.assertNotIn("notification_key", metadata)
        self.assertNotIn("token", json.dumps(metadata))
        self.assertEqual(received["notification_id"], queued["notification_id"])
        lines = gateway.NotificationRuntime.envelope(self.payload(text='', text_lines=["第一行", "第二行"]), "synthetic")
        self.assertEqual(lines.text, "第一行\n第二行")
        fallback = gateway.NotificationRuntime.envelope(self.payload(text="", title="仅标题"), "synthetic")
        self.assertEqual(fallback.text, "仅标题")

    async def test_notification_pending_queue_survives_store_and_runtime_restart(self):
        """接收成功即落库，新存储和runtime恢复处理后不会再次调用处理器。"""
        queued = self.notifications.receive(self.payload())
        reopened = gateway.NotificationStore(message_module.MessageStore(bridge.TaskStore(self.tasks.db_path)))
        processor = FakeProcessor(self.messages)
        runtime = gateway.NotificationRuntime(reopened, processor)
        self.assertEqual((await runtime.run_once())["processed"], 1)
        self.assertEqual((await runtime.run_once())["processed"], 0)
        self.assertEqual(len(processor.calls), 1)
        self.assertEqual(reopened.status()["counts"], {"processed": 1})
        self.assertEqual(self.notifications.receive(self.payload())["notification_id"], queued["notification_id"])

    async def test_notification_transient_failure_backoff_and_terminal_limit(self):
        """暂时失败保存安全类型并退避，第五次失败终止且不会无限调用模型。"""
        self.notifications.receive(self.payload())
        processor = FakeProcessor(self.messages, failures=10)
        runtime = gateway.NotificationRuntime(self.notifications, processor)
        for attempt in range(1, 6):
            outcome = await runtime.run_once()
            self.assertEqual(outcome["failed"], 1)
            self.assertEqual((await runtime.run_once())["failed"], 0)
            self.clock.now += timedelta(minutes=10)
        self.assertEqual(self.notifications.status()["counts"], {"failed": 1})
        with self.messages._connection() as connection:
            row = dict(connection.execute("SELECT * FROM notification_jobs").fetchone())
        self.assertEqual((row["attempts"], row["error_code"]), (5, "RuntimeError"))
        self.assertNotIn("SYNTHETIC_PRIVATE_ERROR_BODY", str(row))
        self.assertEqual((await runtime.run_once())["failed"], 0)

    async def test_notification_invalid_payload_failure_is_terminal(self):
        """不可重试的业务值错误直接终止，不因后台重启再次执行。"""
        self.notifications.receive(self.payload())
        processor = FakeProcessor(self.messages, failures=1, error_type=ValueError)
        await gateway.NotificationRuntime(self.notifications, processor).run_once()
        self.clock.now += timedelta(hours=1)
        self.assertEqual(self.notifications.status()["counts"], {"failed": 1})
        self.assertEqual(self.notifications.pending(), [])

    def test_bridge_token_defaults_toggle_rotation_constant_comparison_and_non_ascii(self):
        """令牌默认拒绝，启停保留凭证、轮换废弃旧凭证，非ASCII请求也安全拒绝。"""
        config = gateway.BridgeConfiguration(self.root / "bridge.json")
        self.assertFalse(config.accepts(None))
        self.assertFalse(config.accepts("Bearer not-configured"))
        enabled = config.configure(True)
        header = "Bearer " + enabled["token"]
        with patch.object(gateway.hmac, "compare_digest", wraps=gateway.hmac.compare_digest) as constant:
            self.assertTrue(config.accepts(header))
            self.assertTrue(constant.called)
        self.assertFalse(config.accepts("Bearer 非ASCII输入"))
        self.assertFalse(config.accepts(123))
        self.assertFalse(config.accepts("bearer " + enabled["token"]))
        self.assertEqual(config.configure(False)["token"], enabled["token"])
        self.assertFalse(config.accepts(header))
        self.assertEqual(config.configure(True)["token"], enabled["token"])
        rotated = config.configure(True, rotate=True)
        self.assertNotEqual(rotated["token"], enabled["token"])
        self.assertFalse(config.accepts(header))
        self.assertTrue(gateway.BridgeConfiguration(config.path).accepts("Bearer " + rotated["token"]))

    def test_bridge_corrupt_configuration_fails_closed(self):
        """配置损坏、根类型错误或令牌过短时停用接入，不暴露解析异常。"""
        path = self.root / "bridge.json"
        config = gateway.BridgeConfiguration(path)
        for raw in ("not-json", "[]", "null", '{"enabled":true,"token":"short"}'):
            with self.subTest(raw=raw):
                path.write_text(raw, encoding="utf-8")
                self.assertFalse(config.read()["enabled"])
                self.assertFalse(config.accepts("Bearer short"))

    def test_attention_registers_fresh_important_once_and_skips_old_or_unconfigured_sender(self):
        """重点消息只建立一次到达记录，历史和明显未来时间以及非重点发送者均跳过。"""
        recent = self.seed_message()
        first = self.attention.register(recent, self.clock.now)
        self.assertEqual(first["status"], "created")
        self.assertEqual(self.attention.register(recent, self.clock.now)["status"], "exists")
        self.assertEqual(self.attention.register(self.seed_message(age_minutes=61), self.clock.now)["reason"], "message_not_recent")
        self.assertEqual(self.attention.register(self.seed_message(age_minutes=-6), self.clock.now)["reason"], "message_not_recent")
        self.assertEqual(self.attention.register(self.seed_message(important=False), self.clock.now)["reason"], "sender_not_important")
        self.assertEqual(len(self.attention.list()), 1)
        self.assertEqual(self.tasks.list_tasks(), [])

    async def test_attention_default_disabled_then_explicit_enable_submits_once(self):
        """默认只保留到达记录，启用后交给假通道且submitted不等于已读。"""
        notifier = FakeNotifier()
        runtime = attention_module.AttentionRuntime(self.attention, notifier, self.clock)
        await runtime.register(self.seed_message())
        self.assertEqual((await runtime.run_once())["submitted"], 0)
        self.assertEqual(notifier.calls, [])
        self.enable_attention()
        self.assertEqual((await runtime.run_once())["submitted"], 1)
        self.assertEqual((await runtime.run_once())["submitted"], 0)
        alert = self.attention.list()[0]
        self.assertEqual(alert["status"], "submitted")
        self.assertIsNone(alert["read_at"])
        self.assertEqual(notifier.calls[0]["priority"], "high")
        self.assertEqual(notifier.calls[0]["channel"], "console")

    async def test_attention_acknowledgement_prevents_delivery_and_re_registration(self):
        """用户先读消息后队列不再弹窗，同一消息重放仍保持已读。"""
        message = self.seed_message()
        created = self.attention.register(message, self.clock.now)
        self.attention.acknowledge(created["alert_id"])
        self.enable_attention()
        notifier = FakeNotifier()
        await attention_module.AttentionRuntime(self.attention, notifier, self.clock).run_once()
        self.assertEqual(notifier.calls, [])
        self.assertEqual(self.attention.register(message, self.clock.now)["status"], "exists")
        self.assertEqual(self.attention.list()[0]["status"], "read")
        self.assertIsNone(self.attention.acknowledge("missing"))

    def test_attention_restart_reclaims_expired_lease_and_rejects_stale_ack(self):
        """进程重开可接管过期租约，旧租约提交和已读后的回执无法覆盖当前状态。"""
        self.attention.register(self.seed_message(), self.clock.now)
        old = self.attention.claim(self.clock.now)
        reopened = attention_module.AttentionStore(message_module.MessageStore(bridge.TaskStore(self.tasks.db_path)))
        self.assertIsNone(reopened.claim(self.clock.now + timedelta(seconds=29)))
        later = self.clock.now + timedelta(seconds=31)
        replacement = reopened.claim(later)
        self.assertNotEqual(old["lease_token"], replacement["lease_token"])
        self.assertFalse(reopened.owns(old, later))
        self.assertFalse(reopened.finish(old, "submitted", later))
        self.assertTrue(reopened.owns(replacement, later))
        reopened.acknowledge(replacement["alert_id"])
        self.assertFalse(reopened.owns(replacement, later))
        self.assertFalse(reopened.finish(replacement, "submitted", later))
        self.assertEqual(reopened.list()[0]["status"], "read")

    async def test_attention_role_change_and_expiration_are_rechecked_before_send(self):
        """创建后取消重点身份或拖到超过新鲜窗口的消息，投递前转为suppressed。"""
        self.enable_attention()
        first = self.seed_message()
        self.attention.register(first, self.clock.now)
        self.update_role(first, False)
        second = self.seed_message()
        self.attention.register(second, self.clock.now)
        self.clock.now += timedelta(minutes=61)
        notifier = FakeNotifier()
        result = await attention_module.AttentionRuntime(self.attention, notifier, self.clock).run_once()
        self.assertEqual(result["suppressed"], 2)
        self.assertEqual(notifier.calls, [])
        self.assertTrue(all(row["status"] == "suppressed" for row in self.attention.list()))

    async def test_attention_transient_failure_retries_and_preserves_safe_error(self):
        """通道失败只记录类型并退避，重试成功后不重复发送。"""
        self.enable_attention()
        self.attention.register(self.seed_message(), self.clock.now)
        notifier = FakeNotifier(failures=1)
        runtime = attention_module.AttentionRuntime(self.attention, notifier, self.clock)
        self.assertEqual((await runtime.run_once())["failed"], 1)
        failed = self.attention.list()[0]
        self.assertEqual((failed["status"], failed["error_code"]), ("retry", "OSError"))
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(failed))
        self.assertEqual((await runtime.run_once())["submitted"], 0)
        self.clock.now += timedelta(seconds=11)
        self.assertEqual((await runtime.run_once())["submitted"], 1)
        self.assertEqual((await runtime.run_once())["submitted"], 0)
        self.assertEqual(len(notifier.calls), 2)

    async def test_attention_read_during_notifier_does_not_overwrite_or_count_submission(self):
        """投递中的已读使迟到回执失效，runtime不能把失效回执统计为submitted。"""
        self.enable_attention()
        created = self.attention.register(self.seed_message(), self.clock.now)
        notifier = FakeNotifier(callback=self.acknowledge_during_send)
        self.alert_to_acknowledge = created["alert_id"]
        outcome = await attention_module.AttentionRuntime(self.attention, notifier, self.clock).run_once()
        self.assertEqual(outcome["submitted"], 0)
        self.assertEqual(outcome["stale"], 1)
        self.assertEqual(self.attention.list()[0]["status"], "read")

    def acknowledge_during_send(self, notification):
        """模拟用户在系统调用开始后标记已读，仅用于验证状态合并。"""
        self.attention.acknowledge(self.alert_to_acknowledge)

    def test_archive_keeps_all_formats_hash_and_exact_attachment_ordinal(self):
        """不支持正文提取的docx/zip也保存原件，相同内容共享受管文件而附件序号独立。"""
        message = self.seed_message(filenames=("论文.pdf", "资料.docx", "数据.zip"))
        message_id = message["message_id"]
        payloads = [b"%PDF-synthetic", b"PK-docx-synthetic", b"PK-docx-synthetic"]
        for index, payload in enumerate(payloads):
            original = self.archive.save(message_id, index, message["attachments"][index]["filename"], payload)
            self.assertEqual(original["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(self.archive.read(message_id, index)[1], payload)
        self.assertEqual([row["attachment_index"] for row in self.archive.list(message_id)], [0, 1, 2])
        self.assertEqual(len(list(self.archive.root.iterdir())), 2)
        reopened = attachment_module.AttachmentStore(self.messages, self.archive.root)
        self.assertEqual(reopened.read(message_id, 2)[1], payloads[2])
        for invalid in (True, -1, 3):
            with self.subTest(index=invalid), self.assertRaises(ValueError):
                self.archive.save(message_id, invalid, "other.bin", b"x")
        self.assertIsNone(self.archive.read(message_id, 9))

    def test_archive_duplicate_conflict_size_and_corruption_never_overwrite_original(self):
        """同序号原件冲突不覆盖，超限或非bytes拒绝，磁盘篡改通过内容哈希发现。"""
        message = self.seed_message(filenames=("原件.bin",))
        message_id = message["message_id"]
        first = self.archive.save(message_id, 0, "原件.bin", b"original")
        self.assertEqual(self.archive.save(message_id, 0, "原件.bin", b"original"), first)
        with self.assertRaises(ValueError):
            self.archive.save(message_id, 0, "changed.bin", b"changed")
        self.assertEqual(self.archive.read(message_id, 0)[1], b"original")
        for invalid in (bytearray(b"x"), b"x" * (25 * 1024 * 1024 + 1)):
            with self.subTest(kind=type(invalid).__name__), self.assertRaises(ValueError):
                self.archive.save(message_id, 0, "bad.bin", invalid)
        (self.archive.root / first["sha256"]).write_bytes(b"tampered")
        with self.assertRaises(OSError):
            self.archive.read(message_id, 0)

    def test_archive_overlay_preserves_mentioned_source_and_existing_index_status(self):
        """真实原件状态只叠加详情，原消息保持mentioned，已索引标记不被降级。"""
        message = self.seed_message(filenames=("附件.zip", "已索引.pdf"))
        self.archive.save(message["message_id"], 0, "附件.zip", b"zip")
        self.archive.save(message["message_id"], 1, "已索引.pdf", b"pdf")
        message["attachments"][1]["availability"] = "indexed"
        result = self.archive.overlay(message)
        self.assertEqual(result["attachments"][0]["availability"], "available")
        self.assertEqual(result["attachments"][1]["availability"], "indexed")
        self.assertTrue(result["attachments"][0]["original_available"])
        self.assertEqual(message["attachments"][0]["availability"], "mentioned")
        self.assertTrue(all(hint["availability"] == "mentioned" for hint in result["source_message"]["attachments"]))


if __name__ == "__main__":
    unittest.main()
