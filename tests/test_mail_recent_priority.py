"""邮箱积压时的最近新信优先验收：真实SQLite和原件，替换外部IMAP与业务处理。"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from tests.test_mail_ingest import FakeImap, ImapCollector, MailSettings, MailStore, TaskStore
from student_assistant.mail_runtime import MailRuntime


class RecentMailTests(unittest.IsolatedAsyncioTestCase):
    """证明最新消息可越过历史业务积压处理，且收取游标不跳过历史UID。"""

    def setUp(self):
        """建立独立临时存储和跨越多个批次的只读IMAP替身。"""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.store = MailStore(TaskStore(self.folder / 'business.sqlite3'), self.folder / 'mail')
        self.settings = MailSettings(account='sample@example.test', auth_code='test-only', batch_size=2)
        self.server = FakeImap({uid: f'raw-{uid}'.encode() for uid in range(1, 9)})
        self.collector = ImapCollector(self.settings, lambda *a, **k: self.server)

    def baseline(self, uid=2):
        """模拟已有历史游标而邮箱中还有多批未收取邮件。"""
        self.store.set_cursor(self.settings.account, 'INBOX', '100', uid)

    async def test_recent_download_preserves_backfill_cursor(self):
        """先取得UID7/8的新信，游标仍为2，随后普通补收从3/4继续。"""
        self.baseline()
        recent = self.collector.fetch_recent(self.store)
        self.assertEqual(recent['received'], 2)
        self.assertEqual(self.store.get_cursor(self.settings.account, 'INBOX', '100'), 2)
        self.assertEqual({j['uid'] for j in self.store.list_jobs(self.settings.account)}, {7, 8})
        self.collector.fetch_new(self.store)
        self.assertEqual(self.store.get_cursor(self.settings.account, 'INBOX', '100'), 4)
        self.assertEqual({j['uid'] for j in self.store.list_jobs(self.settings.account)}, {3, 4, 7, 8})

    async def test_first_connection_obeys_initial_limit(self):
        """首次接入不额外抓旧信，initial_limit=0仍表示只建立正常基线。"""
        self.settings.initial_limit = 0
        self.assertEqual(self.collector.fetch_recent(self.store)['received'], 0)
        self.collector.fetch_new(self.store)
        self.assertEqual(self.store.list_jobs(self.settings.account), [])
        self.assertEqual(self.store.get_cursor(self.settings.account, 'INBOX', '100'), 8)

    async def test_no_backlog_does_not_double_download(self):
        """正常批次足够覆盖新信时，快速通道不重复收取。"""
        self.baseline(6)
        self.assertEqual(self.collector.fetch_recent(self.store)['received'], 0)
        self.assertFalse(any(c[:2] == ('uid', 'FETCH') for c in self.server.calls))

    async def test_received_recent_mail_is_not_redownloaded(self):
        """已经落盘但尚未处理的新信复用原件和稳定job_id。"""
        self.baseline()
        first = self.collector.fetch_recent(self.store)
        self.server.calls.clear()
        second = self.collector.fetch_recent(self.store)
        self.assertEqual(second['received'], 0)
        self.assertEqual(second['priority_job_ids'], first['priority_job_ids'])
        self.assertFalse(any(c[:2] == ('uid', 'FETCH') for c in self.server.calls))

    async def test_new_namespace_obeys_normal_initialization(self):
        """UIDVALIDITY改变时不拿旧游标猜新编号空间，交回普通首次收取。"""
        self.baseline()
        self.server.uidvalidity = '101'
        self.assertEqual(self.collector.fetch_recent(self.store)['received'], 0)
        self.assertIsNone(self.store.get_cursor(self.settings.account, 'INBOX', '101'))

    async def test_recent_fetch_uses_readonly_and_peek(self):
        """优先通道仍是只读IMAP及BODY.PEEK，不标已读或移动邮件。"""
        self.baseline()
        self.collector.fetch_recent(self.store)
        self.assertIn(('select', 'INBOX', True), self.server.calls)
        self.assertIn(('uid', 'FETCH', '7', '(UID BODY.PEEK[])'), self.server.calls)
        self.assertEqual(self.server.calls[-1], ('logout',))

    async def test_priority_does_not_bypass_retry_delay_or_account(self):
        """优先级只改变合法待处理记录的顺序，不能提前重试或跨账号选信。"""
        old = self.store.save_raw(self.settings.account, 'INBOX', '100', 1, b'old')
        recent = self.store.save_raw(self.settings.account, 'INBOX', '100', 8, b'recent', advance_cursor=False)
        self.store.mark_failure(recent['job_id'], 'OSError', retryable=True)
        future = datetime.now(timezone.utc) - timedelta(days=1)
        rows = self.store.pending_jobs(self.settings.account, priority_job_ids=[recent['job_id']], now=future)
        self.assertEqual([j['job_id'] for j in rows], [old['job_id']])
        self.assertEqual(self.store.pending_jobs('other@example.test', priority_job_ids=[old['job_id']]), [])

    async def test_recent_missing_or_oversized_does_not_advance(self):
        """过大邮件不进入快速通道；补历史游标继续保留以便正式记录跳过原因。"""
        self.baseline()
        self.server.oversized = {7, 8}
        self.assertEqual(self.collector.fetch_recent(self.store)['received'], 0)
        self.assertEqual(self.store.get_cursor(self.settings.account, 'INBOX', '100'), 2)

    async def test_runtime_processes_recent_before_old_backlog(self):
        """真实sync先处理7/8新信，3/4旧信仍保存在队列供后续补齐。"""
        self.baseline()
        runtime = MailRuntime(self.store, self.collector, None, None, None)
        processed = []
        async def record(job):
            """只替换模型业务步骤并记录实际调度顺序，收取和存储保持真实。"""
            processed.append(job['uid'])
            self.store.mark_success(job['job_id'], 'offline-message-'+str(job['uid']))
        runtime._process_job = record
        result = await runtime.sync()
        self.assertEqual(processed, [7, 8])
        self.assertEqual(result['received'], 4)
        self.assertEqual([j['uid'] for j in self.store.pending_jobs(self.settings.account)], [3, 4])

    async def test_recent_network_failure_does_not_block_normal_fetch(self):
        """快速通道异常时，已有顺序收取和本地处理仍然继续。"""
        self.baseline()
        def failed(store):
            """模拟最近新信连接故障，不包含账号或原始错误文本。"""
            raise OSError('offline fixture')
        self.collector.fetch_recent = failed
        runtime = MailRuntime(self.store, self.collector, None, None, None)
        runtime._process_job = AsyncMock()
        result = await runtime.sync()
        self.assertEqual(result['processed'], 2)
        self.assertEqual(result['recent_collection_error'], 'OSError')
        self.assertEqual(self.store.get_cursor(self.settings.account, 'INBOX', '100'), 4)

    async def test_single_new_arrival_precedes_already_collected_old_jobs(self):
        """即使收取游标已追上，真正新到的一封也优先于已落库的旧业务积压。"""
        self.store.save_raw(self.settings.account, 'INBOX', '100', 1, b'old')
        self.baseline(7)
        runtime = MailRuntime(self.store, self.collector, None, None, None)
        seen = []
        async def record(job):
            """记录调度次序并确认处理，保留实际队列选择和增量收取代码。"""
            seen.append(job['uid'])
            self.store.mark_success(job['job_id'], 'message-'+str(job['uid']))
        runtime._process_job = record
        await runtime.sync()
        self.assertEqual(seen, [8, 1])

    async def test_cursor_flag_and_priority_inputs_are_validated(self):
        """拒绝非布尔游标开关及不合法优先列表，避免意外跳过历史收取。"""
        with self.assertRaises(ValueError):
            self.store.save_raw(self.settings.account, 'INBOX', '100', 1, b'x', advance_cursor=1)
        with self.assertRaises(ValueError):
            self.store.pending_jobs(self.settings.account, priority_job_ids='untrusted')

    async def test_priority_sql_uses_bound_parameters(self):
        """不可信任务标识作为绑定值使用，不能改变查询或破坏任务表。"""
        old = self.store.save_raw(self.settings.account, 'INBOX', '100', 1, b'old')
        rows = self.store.pending_jobs(self.settings.account, priority_job_ids=["'); DROP TABLE mail_jobs; --"])
        self.assertEqual(rows[0]['job_id'], old['job_id'])


if __name__ == '__main__':
    unittest.main()
