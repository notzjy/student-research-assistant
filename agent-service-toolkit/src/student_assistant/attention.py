"""重点联系人到达提醒：独立于截止提醒的持久收件箱、可确认状态与有限重试。"""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import Field

from student_assistant.bridge import parse_reference_time
from student_assistant.message_models import StrictInput
from student_assistant.notification import LocalNotifier


class AttentionPolicy(StrictInput):
    """由用户启用重点联系人弹窗，最长消息年龄阻止历史邮件或离线旧通知集中弹出。"""

    enabled: bool = False
    channel: str = Field(default='windows', pattern=r'^(windows|console)$')
    max_age_minutes: int = Field(default=60, ge=1, le=1440)


class AttentionStore:
    """保存重点消息到达记录和投递状态，读消息不等于操作系统通知已读。"""

    def __init__(self, message_store):
        """建立独立于待办的收件箱表，不创建虚假的任务或修改原消息。"""
        self.messages = message_store
        with self.messages._connection(write=True) as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS attention_alerts (
                alert_id TEXT PRIMARY KEY, message_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL, lease_token TEXT, lease_until TEXT, error_code TEXT,
                submitted_at TEXT, read_at TEXT,
                FOREIGN KEY(message_id) REFERENCES messages(message_id))''')
            connection.execute('CREATE TABLE IF NOT EXISTS attention_policy (singleton INTEGER PRIMARY KEY CHECK(singleton=1),value TEXT NOT NULL)')
            connection.execute('INSERT OR IGNORE INTO attention_policy VALUES(1,?)', (AttentionPolicy().model_dump_json(),))

    def get_policy(self):
        """读取用户明确的到达提醒配置，默认不触发桌面通知。"""
        with self.messages._connection() as connection:
            return AttentionPolicy.model_validate_json(connection.execute('SELECT value FROM attention_policy WHERE singleton=1').fetchone()['value']).model_dump()

    def set_policy(self, value):
        """保存经过类型约束的提醒配置，不追溯创建历史消息的提醒记录。"""
        policy = AttentionPolicy.model_validate(value)
        with self.messages._connection(write=True) as connection:
            connection.execute('UPDATE attention_policy SET value=? WHERE singleton=1', (policy.model_dump_json(),))
        return policy.model_dump()

    def register(self, message, now=None):
        """仅给当前已配置为重要且新鲜的联系人消息创建一次到达记录，旧邮件不会进入队列。"""
        now = datetime.now(timezone.utc) if now is None else now
        role = message.get('role_match') or {}
        if not role.get('important'):
            return {'status': 'skipped', 'reason': 'sender_not_important'}
        sent = parse_reference_time(message['source_message']['sent_at'])
        age = (now - sent).total_seconds()
        if age < -300 or age > self.get_policy()['max_age_minutes'] * 60:
            return {'status': 'skipped', 'reason': 'message_not_recent'}
        alert_id = hashlib.sha256(('attention:' + message['message_id']).encode()).hexdigest()[:24]
        summary = (message.get('analysis') or {}).get('summary') or message['source_message']['text']
        title = ('重点联系人 · ' + role['role'] + '：' + summary)[:250]
        clock = now.isoformat()
        with self.messages._connection(write=True) as connection:
            inserted = connection.execute('INSERT OR IGNORE INTO attention_alerts(alert_id,message_id,title,created_at,next_attempt_at) VALUES(?,?,?,?,?)', (alert_id, message['message_id'], title, clock, clock)).rowcount
        return {'status': 'created' if inserted else 'exists', 'alert_id': alert_id}

    def list(self, limit=100):
        """列出到达提醒与用户确认状态，不把submitted解释成已读。"""
        with self.messages._connection() as connection:
            return [dict(row) for row in connection.execute('SELECT alert_id,message_id,title,created_at,status,attempts,error_code,submitted_at,read_at FROM attention_alerts ORDER BY created_at DESC LIMIT ?', (limit,))]

    def acknowledge(self, alert_id):
        """明确标记已读并使在途租约失效；已交给OS的弹窗可能已经显示。"""
        with self.messages._connection(write=True) as connection:
            found = connection.execute('SELECT alert_id FROM attention_alerts WHERE alert_id=?', (alert_id,)).fetchone()
            if not found:
                return None
            connection.execute("UPDATE attention_alerts SET status='read',read_at=?,lease_token=NULL,lease_until=NULL WHERE alert_id=?", (datetime.now(timezone.utc).isoformat(), alert_id))
        return {'alert_id': alert_id, 'status': 'read'}

    def claim(self, now):
        """短事务认领一条到期通知，崩溃后的过期租约允许下一进程重试。"""
        clock = now.isoformat()
        with self.messages._connection(write=True) as connection:
            connection.execute("UPDATE attention_alerts SET status='failed',error_code='AttemptsExhausted',lease_token=NULL,lease_until=NULL WHERE attempts>=5 AND status='leased' AND lease_until<=?", (clock,))
            row = connection.execute("SELECT * FROM attention_alerts WHERE attempts<5 AND ((status IN ('pending','retry') AND next_attempt_at<=?) OR (status='leased' AND lease_until<=?)) ORDER BY created_at LIMIT 1", (clock, clock)).fetchone()
            if not row:
                return None
            token, until = uuid4().hex, (now + timedelta(seconds=30)).isoformat()
            connection.execute("UPDATE attention_alerts SET status='leased',attempts=attempts+1,lease_token=?,lease_until=? WHERE alert_id=?", (token, until, row['alert_id']))
            return {**dict(row), 'lease_token': token, 'attempts': row['attempts'] + 1}

    def owns(self, job, now):
        """在系统投递前再次检查有效租约，已读或取消后不继续发送尚未开始的弹窗。"""
        with self.messages._connection() as connection:
            return bool(connection.execute("SELECT 1 FROM attention_alerts WHERE alert_id=? AND status='leased' AND lease_token=? AND lease_until>?", (job['alert_id'], job['lease_token'], now.isoformat())).fetchone())

    def finish(self, job, status, now, error_code=None):
        """只接受当前租约的回执，已读或其他进程的新租约不会被迟到结果覆盖。"""
        due = (now + timedelta(seconds=min(300, 5 * 2 ** job['attempts']))).isoformat()
        with self.messages._connection(write=True) as connection:
            return bool(connection.execute('''UPDATE attention_alerts SET status=?,next_attempt_at=?,error_code=?,
                lease_token=NULL,lease_until=NULL,submitted_at=CASE WHEN ?='submitted' THEN ? ELSE submitted_at END
                WHERE alert_id=? AND status='leased' AND lease_token=? AND lease_until>?''', (status, due, error_code, status, now.isoformat(), job['alert_id'], job['lease_token'], now.isoformat())).rowcount)


class AttentionRuntime:
    """轮询重点消息到达队列，投递前重新检查角色配置和消息年龄。"""

    def __init__(self, store, notifier=None, clock=None):
        """注入通知边界，测试和验收可不向Windows发送真实弹窗。"""
        self.store = store
        self.notifier = LocalNotifier() if notifier is None else notifier
        self.clock = (lambda: datetime.now(timezone.utc)) if clock is None else clock
        self.lock = asyncio.Lock()

    async def register(self, message):
        """在消息分析完成后幂等创建到达记录，数据库工作不占用异步事件循环。"""
        return await asyncio.to_thread(self.store.register, message, self.clock())

    async def run_once(self):
        """执行至多十条重点通知，配置停用时保持记录供列表查询。"""
        result = {'submitted': 0, 'suppressed': 0, 'failed': 0, 'stale': 0}
        async with self.lock:
            policy = await asyncio.to_thread(self.store.get_policy)
            if not policy['enabled']:
                return result
            for _ in range(10):
                job = await asyncio.to_thread(self.store.claim, self.clock())
                if job is None:
                    break
                try:
                    message = await asyncio.to_thread(self.store.messages.get_message, job['message_id'])
                    age = (self.clock() - parse_reference_time(message['source_message']['sent_at'])).total_seconds() if message else float('inf')
                    important = bool(message and (message.get('role_match') or {}).get('important'))
                    if not important or not -300 <= age <= policy['max_age_minutes'] * 60:
                        acknowledged = await asyncio.to_thread(self.store.finish, job, 'suppressed', self.clock(), 'MessageExpiredOrRoleChanged')
                        result['suppressed' if acknowledged else 'stale'] += 1
                        continue
                    # 高优先级影响本地呈现，不交给模型决定是否弹窗或执行其他操作。
                    notification = {'reminder_id': job['alert_id'], 'task_id': None, 'title': job['title'], 'remind_at': self.clock().isoformat(), 'channel': policy['channel'], 'notice_title': '重点联系人消息', 'priority': 'high'}
                    if not await asyncio.to_thread(self.store.owns, job, self.clock()):
                        result['stale'] += 1
                        continue
                    await asyncio.to_thread(self.notifier.send, notification)
                    acknowledged = await asyncio.to_thread(self.store.finish, job, 'submitted', self.clock())
                    result['submitted' if acknowledged else 'stale'] += 1
                except Exception as error:
                    await asyncio.to_thread(self.store.finish, job, 'retry' if job['attempts'] < 5 else 'failed', self.clock(), type(error).__name__)
                    result['failed'] += 1
        return result

    async def loop(self, stop_event):
        """独立运行重点消息投递，后台停止时等待当前投递完成。"""
        while not stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1)
            except TimeoutError:
                pass
