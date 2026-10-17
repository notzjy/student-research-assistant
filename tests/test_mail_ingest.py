"""邮件接入离线验证：真实 MIME/SQLite/文件持久化，加假 IMAP 检查只读协议和游标。"""

import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-service-toolkit" / "src"))

from student_assistant.bridge import TaskStore
from student_assistant.mail_ingest import ImapCollector, MailCollectionError, MailSettings, parse_mail
from student_assistant.mail_store import MailStore


class FakeImap:
    """只在内存响应 IMAP 命令，记录只读选择、PEEK 和退出动作，绝不连接邮箱。"""

    def __init__(self, messages, uidvalidity="100", fail_uid=None, oversized=None, uid_after_literal=False):
        """注入邮件、UID 命名空间及单信错误场景，供协议边界测试。"""
        self.messages = messages
        self.uidvalidity = uidvalidity
        self.fail_uid = fail_uid
        self.oversized = oversized or set()
        self.uid_after_literal = uid_after_literal
        self.calls = []

    def login(self, account, auth_code):
        """只记录登录动作，不把合成凭证加入日志或返回值。"""
        self.calls.append(("login",))
        return "OK", []

    def select(self, mailbox, readonly=False):
        """返回邮箱数量，并保留 readonly 参数用于验证。"""
        self.calls.append(("select", mailbox, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def response(self, name):
        """返回合成 UIDVALIDITY，不把邮件序号当唯一标识。"""
        self.calls.append(("response", name))
        return name, [self.uidvalidity.encode()]

    def uid(self, command, *args):
        """提供 UID 搜索、大小和 PEEK 正文；搜索刻意含旧 UID 以验证客户端过滤。"""
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b" ".join(str(uid).encode() for uid in sorted(self.messages))]
        if command != "FETCH":
            raise AssertionError("收取器调用了非只读 UID 命令")
        uid = int(args[0])
        if uid == self.fail_uid:
            return "NO", [b"synthetic temporary failure"]
        raw = self.messages[uid]
        if "RFC822.SIZE" in args[1]:
            size = 99_999_999 if uid in self.oversized else len(raw)
            return "OK", [f'1 (UID {uid} RFC822.SIZE {size} INTERNALDATE "16-Oct-2026 10:00:00 +0800")'.encode()]
        if args[1] != "(UID BODY.PEEK[])":
            raise AssertionError("正文必须使用 BODY.PEEK[]")
        if self.uid_after_literal:
            return "OK", [(f"1 (BODY[] {{{len(raw)}}}".encode(), raw), f" UID {uid})".encode()]
        return "OK", [(f"1 (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw), b")"]

    def logout(self):
        """记录退出；测试对象没有 close/expunge/store，误调用这些方法会直接失败。"""
        self.calls.append(("logout",))
        return "BYE", []


class MailIngestTests(unittest.TestCase):
    """验证单信解析、可靠队列和只读增量收取，不依赖模型或真实邮箱账号。"""

    def setUp(self):
        """为每个用例准备独立临时业务库和受管邮件目录。"""
        self.temporary = TemporaryDirectory(prefix="student_mail_test_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tasks = TaskStore(self.root / "business.sqlite3")
        self.store = MailStore(self.tasks, self.root / "mail_data")
        self.account = "synthetic@example.com"
        self.settings = MailSettings(account=self.account, auth_code="synthetic-secret")
        self.fake = None

    def _message(self, body="明天下午三点在实验室开组会。", subject="研究通知"):
        """生成具有明确发件地址和原发送时区的合成 MIME 邮件。"""
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = "导师 <teacher@example.com>"
        message["To"] = self.account
        message["Subject"] = subject
        message["Date"] = format_datetime(datetime(2026, 10, 16, 10, tzinfo=timezone(timedelta(hours=8))))
        message["Message-ID"] = "<synthetic-mail@example.com>"
        message.set_content(body)
        return message

    def _parse(self, message):
        """通过正式解析入口读取一封合成邮件，固定 IMAP 来源身份。"""
        return parse_mail(message.as_bytes(), self.account, "INBOX", "100", 7)

    def _factory(self, host, port, **kwargs):
        """注入假连接并确认调用方要求 TLS 验证上下文和有限 socket 超时。"""
        self.assertEqual((host, port), ("imap.qq.com", 993))
        self.assertTrue(kwargs["ssl_context"].check_hostname)
        self.assertGreater(kwargs["timeout"], 0)
        return self.fake

    def test_settings_environment_and_secret_repr(self):
        """环境配置可独立注入，配置完整与后台启用分开，repr 不泄漏授权码。"""
        with patch.dict(os.environ, {
            "QQ_MAIL_ENABLED": "true", "QQ_MAIL_ACCOUNT": self.account,
            "QQ_MAIL_AUTH_CODE": "do-not-expose", "QQ_MAIL_POLL_SECONDS": "30", "QQ_MAIL_INITIAL_LIMIT": "2",
        }, clear=True):
            settings = MailSettings.from_env()
        self.assertTrue(settings.enabled and settings.configured)
        self.assertEqual((settings.poll_seconds, settings.initial_limit), (30, 2))
        self.assertNotIn("do-not-expose", repr(settings))
        self.assertTrue(self.settings.configured)
        self.assertFalse(self.settings.enabled)
        # 空白示例配置直接复制到 .env 后也应允许未配置邮箱的主服务正常启动。
        with patch.dict(os.environ, {
            "QQ_MAIL_ENABLED": "", "QQ_MAIL_ACCOUNT": "", "QQ_MAIL_AUTH_CODE": "",
            "QQ_MAIL_POLL_SECONDS": "", "QQ_MAIL_INITIAL_LIMIT": "  ",
        }, clear=True):
            defaults = MailSettings.from_env()
        self.assertFalse(defaults.configured or defaults.enabled)
        self.assertEqual((defaults.poll_seconds, defaults.initial_limit), (60, 5))
        with patch.dict(os.environ, {"QQ_MAIL_POLL_SECONDS": "not-a-number"}, clear=True):
            with self.assertRaises(ValueError):
                MailSettings.from_env()

    def test_plain_body_sender_date_and_real_attachment_bytes(self):
        """优先纯文本并正确映射发送者和原时区，安全文件名不影响附件真实字节。"""
        message = self._message()
        message.add_alternative("<p>这段替代 HTML 不应重复进入正文</p>", subtype="html")
        pdf = b"%PDF-synthetic-bytes"
        archive = b"PK-synthetic-archive"
        message.add_attachment(pdf, maintype="application", subtype="pdf", filename="..\\nested/paper.pdf")
        message.add_attachment(archive, maintype="application", subtype="zip", filename="data.zip")
        parsed = self._parse(message)
        self.assertEqual(parsed.envelope.source, "qq_mail")
        self.assertEqual(parsed.envelope.source_message_id, "INBOX:100:7")
        self.assertEqual(parsed.envelope.sender_id, "teacher@example.com")
        self.assertEqual(parsed.envelope.sent_at, "2026-10-16T10:00:00+08:00")
        self.assertIn("开组会", parsed.envelope.text)
        self.assertNotIn("替代 HTML", parsed.envelope.text)
        self.assertEqual([item.data for item in parsed.attachments], [pdf, archive])
        self.assertEqual(parsed.attachments[0].filename, "paper.pdf")
        self.assertEqual([item.index for item in parsed.attachments], [0, 1])
        self.assertEqual(parsed.envelope.attachments[0].availability, "mentioned")
        self.assertTrue(parsed.envelope.attachments[0].source_attachment_id.startswith("part-"))

    def test_html_fallback_removes_script_and_preserves_readable_text(self):
        """HTML 备用正文只提取文字和实体，不把脚本样式或远程图片当作正文。"""
        message = self._message()
        message.set_content(
            '<html><head><style>bad-style</style></head><body><p>组会&amp;讨论</p>'
            '<script>bad-script</script><p>明天15:00<br>实验室</p><img src="https://example.invalid/a.png"></body></html>',
            subtype="html",
        )
        parsed = self._parse(message)
        self.assertIn("组会&讨论", parsed.envelope.text)
        self.assertIn("明天15:00\n实验室", parsed.envelope.text)
        self.assertNotIn("bad-script", parsed.envelope.text)
        self.assertNotIn("bad-style", parsed.envelope.text)
        self.assertNotIn("example.invalid", parsed.envelope.text)

    def test_missing_ambiguous_sender_or_date_never_uses_processing_time(self):
        """日期缺失、无偏移或发件人不唯一时明确失败，不推测发件身份和发送时间。"""
        for case in ("no_date", "bad_date", "naive_date", "no_from", "two_from"):
            with self.subTest(case=case):
                message = self._message()
                if case == "no_date":
                    del message["Date"]
                elif case == "bad_date":
                    message.replace_header("Date", "not a date")
                elif case == "naive_date":
                    message.replace_header("Date", "Fri, 16 Oct 2026 10:00:00 -0000")
                elif case == "no_from":
                    del message["From"]
                else:
                    message.replace_header("From", "a@example.com, b@example.com")
                with self.assertRaises(ValueError):
                    self._parse(message)

    def test_text_and_attachment_limits_do_not_silently_truncate(self):
        """超过现有业务协议上限时拒绝解析，完整原件仍可留在队列中。"""
        with self.assertRaises(ValueError):
            self._parse(self._message(body="字" * 5000))
        message = self._message()
        for index in range(21):
            message.add_attachment(b"x", maintype="application", subtype="octet-stream", filename=f"{index}.bin")
        with self.assertRaises(ValueError):
            self._parse(message)

    def test_raw_cursor_survives_restart_and_hash_detects_corruption(self):
        """EML、任务和游标在重开后恢复，公开列表不含路径，原件被改动时指纹报错。"""
        raw = self._message().as_bytes()
        job = self.store.save_raw(self.account, "INBOX", "100", 7, raw, {"rfc822_size": len(raw)})
        reopened = MailStore(self.tasks, self.root / "mail_data")
        self.assertEqual(reopened.get_cursor(self.account, "INBOX", "100"), 7)
        self.assertEqual(reopened.read_raw(job["job_id"]), raw)
        self.assertEqual(len(reopened.pending_jobs(self.account)), 1)
        public = reopened.list_jobs(self.account)[0]
        self.assertNotIn("path", " ".join(public))
        self.assertNotIn("synthetic-secret", str(public))
        (reopened.mail_dir / (job["job_id"] + ".eml")).write_bytes(b"corrupted")
        with self.assertRaises(OSError):
            reopened.read_raw(job["job_id"])

    def test_raw_and_cursor_rollback_together_on_sql_failure(self):
        """文件写入后 SQL 触发器失败时清除孤儿原件，游标不能跳过未持久化 UID。"""
        self.store.set_cursor(self.account, "INBOX", "100", 6)
        with self.store._connection(write=True) as connection:
            connection.execute("CREATE TRIGGER fail_mail BEFORE INSERT ON mail_jobs BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_raw(self.account, "INBOX", "100", 7, b"raw")
        self.assertEqual(self.store.get_cursor(self.account, "INBOX", "100"), 6)
        self.assertEqual(self.store.list_jobs(self.account), [])
        self.assertEqual(list(self.store.mail_dir.iterdir()), [])

    def test_retry_backoff_terminal_limit_and_explicit_reset(self):
        """单信失败独立退避且最多五次，新邮件游标和成功任务不受影响。"""
        first = self.store.save_raw(self.account, "INBOX", "100", 1, b"first")
        second = self.store.save_raw(self.account, "INBOX", "100", 2, b"second")
        self.store.mark_success(second["job_id"], "message-2")
        now = datetime(2026, 10, 16, 10, tzinfo=timezone.utc)
        self.store.mark_failure(first["job_id"], "APIError", now=now)
        self.assertEqual(self.store.pending_jobs(self.account, now=now + timedelta(seconds=14)), [])
        self.assertEqual(self.store.pending_jobs(self.account, now=now + timedelta(seconds=15))[0]["job_id"], first["job_id"])
        for attempt in range(2, 6):
            self.store.mark_failure(first["job_id"], "APIError", now=now + timedelta(minutes=attempt))
        jobs = {job["job_id"]: job for job in self.store.list_jobs(self.account)}
        self.assertEqual((jobs[first["job_id"]]["status"], jobs[first["job_id"]]["attempts"]), ("failed", 5))
        self.assertEqual(jobs[second["job_id"]]["status"], "processed")
        self.assertEqual(self.store.get_cursor(self.account, "INBOX", "100"), 2)
        self.assertEqual(self.store.retry_failed(self.account), 1)
        self.assertEqual(self.store.pending_jobs(self.account)[0]["attempts"], 0)

    def test_uid_idempotency_conflict_and_skipped_attention(self):
        """重复 UID 保留已完成状态，内容冲突不覆盖，跳过超限信留下可核对原因。"""
        job = self.store.save_raw(self.account, "INBOX", "100", 1, b"original")
        self.store.mark_success(job["job_id"], "message-1", [{"status": "available"}])
        again = self.store.save_raw(self.account, "INBOX", "100", 1, b"original")
        self.assertEqual(again["status"], "processed")
        with self.assertRaises(ValueError):
            self.store.save_raw(self.account, "INBOX", "100", 1, b"different")
        self.assertEqual(self.store.read_raw(job["job_id"]), b"original")
        skipped = self.store.record_skipped(self.account, "INBOX", "100", 2, "MessageTooLarge")
        self.assertTrue(skipped["needs_attention"])
        self.assertFalse(skipped["has_raw"])
        self.assertEqual(self.store.get_cursor(self.account, "INBOX", "100"), 2)
        with self.assertRaises(ValueError):
            self.store.mark_failure(job["job_id"], "unsafe error includes secret")

    def test_first_sync_recent_limit_peek_uid_tail_and_old_uid_filter(self):
        """首次只取最近两封，PEEK 不修改已读，UID 位于 literal 后也可解析，旧 UID 不重取。"""
        raw = self._message().as_bytes()
        self.fake = FakeImap({uid: raw for uid in range(1, 6)}, uid_after_literal=True)
        self.settings.initial_limit = 2
        collector = ImapCollector(self.settings, self._factory)
        result = collector.fetch_new(self.store)
        self.assertEqual(result["received"], 2)
        self.assertTrue(result["initialized"])
        self.assertIn(("select", "INBOX", True), self.fake.calls)
        self.assertEqual({job["uid"] for job in self.store.list_jobs(self.account)}, {4, 5})
        self.assertEqual(self.fake.calls[-1], ("logout",))
        self.assertEqual(collector.fetch_new(self.store)["received"], 0)
        self.assertEqual(self.store.get_cursor(self.account, "INBOX", "100"), 5)

    def test_uidvalidity_reset_keeps_old_jobs_in_separate_namespace(self):
        """UIDVALIDITY 改变后重新处理近期邮件，同号 UID 不能覆盖旧原件和旧任务。"""
        self.store.save_raw(self.account, "INBOX", "100", 1, b"old raw")
        self.fake = FakeImap({1: self._message().as_bytes()}, uidvalidity="200")
        result = ImapCollector(self.settings, self._factory).fetch_new(self.store)
        self.assertTrue(result["uidvalidity_reset"])
        self.assertEqual(result["received"], 1)
        self.assertEqual(len(self.store.list_jobs(self.account)), 2)
        self.assertEqual(self.store.get_cursor(self.account, "INBOX", "100"), 1)
        self.assertEqual(self.store.get_cursor(self.account, "INBOX", "200"), 1)

    def test_oversize_skipped_but_fetch_failure_does_not_jump_later_uid(self):
        """超限先持久化原因再跳过，临时失败则停在该 UID，下次同步补齐而不漏信。"""
        raw = self._message().as_bytes()
        self.store.set_cursor(self.account, "INBOX", "100", 0)
        self.fake = FakeImap({1: raw, 2: raw, 3: raw}, fail_uid=2, oversized={1})
        collector = ImapCollector(self.settings, self._factory)
        result = collector.fetch_new(self.store)
        self.assertEqual((result["received"], result["skipped"]), (0, 1))
        self.assertIn("error_code", result)
        self.assertEqual(self.store.get_cursor(self.account, "INBOX", "100"), 1)
        self.assertFalse(any(call[:4] == ("uid", "FETCH", "1", "(UID BODY.PEEK[])") for call in self.fake.calls))
        self.fake.fail_uid = None
        result = collector.fetch_new(self.store)
        self.assertEqual(result["received"], 2)
        self.assertEqual(self.store.get_cursor(self.account, "INBOX", "100"), 3)
        self.assertEqual(len(self.store.pending_jobs(self.account)), 2)


if __name__ == "__main__":
    unittest.main()
