"""安卓通知接入：专用令牌、持久收件队列与摘要完整性标记，不把通知当成聊天全文。"""

import asyncio
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import Field, field_validator

from student_assistant.bridge import parse_reference_time
from student_assistant.message_models import MessageInput, StrictInput


PACKAGES = {'com.tencent.mm': 'wechat', 'com.tencent.mobileqq': 'qq', 'com.hpbr.bosszhipin': 'boss'}


class NotificationInput(StrictInput):
    """接收设备实际展示的通知字段；posted_at是通知发布时间，不能冒充聊天发送时间。"""

    device_id: str = Field(min_length=1, max_length=120, pattern=r'\S')
    package_name: str = Field(min_length=1, max_length=150)
    notification_key: str = Field(min_length=1, max_length=512)
    posted_at: str
    title: str = Field(default='', max_length=500)
    text: str = Field(default='', max_length=5000)
    big_text: str | None = Field(default=None, max_length=5000)
    text_lines: list[str] = Field(default_factory=list, max_length=30)
    conversation_id: str | None = Field(default=None, max_length=512)
    message_sent_at: str | None = None
    text_source: str = Field(default='notification_text', pattern=r'^(latest_message|notification_text|aggregate)$')

    @field_validator('posted_at', 'message_sent_at')
    @classmethod
    def validate_time(cls, value):
        """强制使用带时区的真实通知时间，拒绝由后端猜测时间基准。"""
        if value is not None:
            parse_reference_time(value)
        return value

    @field_validator('package_name')
    @classmethod
    def validate_package(cls, value):
        """第一版仅接受用户需求中的三个应用，避免收集其他应用的私人通知。"""
        if value not in PACKAGES:
            raise ValueError('该应用尚未启用通知接入')
        return value

    @field_validator('text_lines')
    @classmethod
    def validate_lines(cls, value):
        """限制折叠通知的总文本大小，不接受无限扩张的通知摘要。"""
        if any(len(line) > 5000 for line in value) or sum(map(len, value)) > 12000:
            raise ValueError('通知行内容过长')
        return value


class BridgeConfiguration:
    """在本机运行目录保存接入令牌；HTTP状态接口不回显令牌内容。"""

    def __init__(self, path: Path):
        """指定本机配置路径，初始化不自动开启网络监听或生成凭证。"""
        self.path = Path(path).resolve()

    def read(self):
        """读取本机配置，不存在或损坏时视为未配置且禁止外部接入。"""
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value.get('token'), str) or len(value['token']) < 32:
                return {'enabled': False, 'token': ''}
            return {'enabled': value.get('enabled') is True, 'token': value['token'], 'trust_posted_time': value.get('trust_posted_time') is True}
        except (OSError, ValueError, TypeError, AttributeError):
            return {'enabled': False, 'token': ''}

    def configure(self, enabled: bool, rotate: bool = False, trust_posted_time: bool = False):
        """由本机明确操作启停或轮换令牌，保留既有设备凭证避免无故断开手机。"""
        current = self.read()
        token = secrets.token_urlsafe(32) if rotate or not current['token'] else current['token']
        value = {'enabled': enabled, 'token': token, 'trust_posted_time': trust_posted_time}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value), encoding='utf-8')
        temporary.replace(self.path)
        return value

    def accepts(self, authorization: str | None):
        """恒定时间比较专用Bearer令牌，停用后立即拒绝手机新上传。"""
        value = self.read()
        return bool(value['enabled'] and isinstance(authorization, str) and hmac.compare_digest(authorization.encode('utf-8'), ('Bearer ' + value['token']).encode('utf-8')))


class NotificationStore:
    """复用业务数据库保存已接收通知、设备状态和到消息的来源关联。"""

    def __init__(self, message_store):
        """建立收件队列，复用现有短事务工具，避免另开常驻数据库连接。"""
        self.messages = message_store
        with self.messages._connection(write=True) as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS notification_jobs (
                notification_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                device_id TEXT NOT NULL, received_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL, error_code TEXT, message_id TEXT)''')
            connection.execute('CREATE INDEX IF NOT EXISTS notification_jobs_pending ON notification_jobs(status,next_attempt_at)')

    @staticmethod
    def identity(payload: NotificationInput):
        """将通知内容快照生成稳定指纹，重复上传不重复分析，通知更新可保留新快照。"""
        stable = payload.model_dump()
        if payload.text_source == 'latest_message' and payload.message_sent_at:
            stable = {'device_id': payload.device_id, 'package_name': payload.package_name, 'conversation': payload.conversation_id or payload.title or payload.notification_key, 'text': payload.text, 'message_sent_at': payload.message_sent_at}
        # 应用反复刷新同一通知但内容不变时，不因post时间变化重复导入。
        stable.pop('posted_at', None)
        return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:32]

    def receive(self, payload: NotificationInput):
        """在返回接收成功前持久化通知，设备可据此移除其本地待传项。"""
        notification_id = self.identity(payload)
        now = datetime.now(timezone.utc).isoformat()
        with self.messages._connection(write=True) as connection:
            exists = connection.execute('SELECT status,message_id FROM notification_jobs WHERE notification_id=?', (notification_id,)).fetchone()
            if exists:
                return {'status': 'exists', 'notification_id': notification_id, 'processing_status': exists['status'], 'message_id': exists['message_id']}
            connection.execute('INSERT INTO notification_jobs(notification_id,payload_json,device_id,received_at,next_attempt_at) VALUES(?,?,?,?,?)', (notification_id, payload.model_dump_json(), payload.device_id, now, now))
        return {'status': 'queued', 'notification_id': notification_id, 'processing_status': 'pending', 'message_id': None}

    def pending(self):
        """查询可重试通知；单进程运行时由runtime锁防止同时处理同一批。"""
        with self.messages._connection() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM notification_jobs WHERE status IN ('pending','retry') AND next_attempt_at<=? ORDER BY received_at LIMIT 10", (datetime.now(timezone.utc).isoformat(),))]

    def finish(self, notification_id, message_id=None, error_code=None, *, retry=False):
        """持久化处理结果或有限退避，只记录安全错误类型而不记录模型原始异常。"""
        with self.messages._connection(write=True) as connection:
            row = connection.execute('SELECT attempts FROM notification_jobs WHERE notification_id=?', (notification_id,)).fetchone()
            attempts = row['attempts'] + 1
            status = 'processed' if error_code is None else ('retry' if retry and attempts < 5 else 'failed')
            due = (datetime.now(timezone.utc) + timedelta(seconds=min(300, 5 * 2 ** min(attempts, 6)))).isoformat()
            connection.execute('UPDATE notification_jobs SET status=?,attempts=?,next_attempt_at=?,error_code=?,message_id=? WHERE notification_id=?', (status, attempts, due, error_code, message_id, notification_id))

    def metadata(self, message_id):
        """返回摘要完整性与通知来源标记，不向界面暴露原始通知key或设备凭证。"""
        with self.messages._connection() as connection:
            row = connection.execute("SELECT payload_json,received_at FROM notification_jobs WHERE message_id=? OR notification_id=(SELECT substr(source_message_id,14) FROM messages WHERE message_id=? AND source_message_id LIKE 'notification-%') ORDER BY received_at DESC LIMIT 1", (message_id, message_id)).fetchone()
        if row is None:
            return None
        payload = json.loads(row['payload_json'])
        return {'completeness': 'notification_excerpt', 'time_basis': 'app_message_timestamp' if payload.get('message_sent_at') else 'notification_posted', 'text_source': payload.get('text_source', 'notification_text'), 'identity_scope': 'device_app_conversation', 'package_name': payload['package_name'], 'received_at': row['received_at'], 'sender_identity_verified': False}

    def status(self):
        """仅公开队列数量、设备最后接收时间和安全错误码，不公开正文或令牌。"""
        with self.messages._connection() as connection:
            counts = {row['status']: row['n'] for row in connection.execute('SELECT status,COUNT(*) AS n FROM notification_jobs GROUP BY status')}
            devices = [dict(row) for row in connection.execute('SELECT device_id,MAX(received_at) AS last_received_at FROM notification_jobs GROUP BY device_id')]
        return {'counts': counts, 'devices': devices}


class NotificationRuntime:
    """把安卓通知队列接入既有消息工作流，原文、幂等、角色和提醒继续统一处理。"""

    def __init__(self, store, processor):
        """注入收件存储和处理器，构造时不读取通知、不调用模型。"""
        self.store, self.processor = store, processor
        self.lock = asyncio.Lock()

    @staticmethod
    def envelope(payload: NotificationInput, notification_id):
        """保留应用提供的摘要文字，设备/会话散列只表示通知范围，不声称真实用户唯一ID。"""
        text = payload.text if payload.text_source == 'latest_message' else (payload.big_text or payload.text or '\n'.join(payload.text_lines))
        text = text.strip()[:5000] or payload.title.strip() or '[通知未提供可读取正文]'
        scope = payload.conversation_id or payload.title or payload.notification_key
        sender = 'notification-' + hashlib.sha256((payload.package_name + ':' + scope).encode('utf-8')).hexdigest()[:24]
        return MessageInput(source=PACKAGES[payload.package_name], account_id=payload.device_id, source_message_id='notification-' + notification_id, sender_id=sender, sender_name=payload.title[:120] or None, sent_at=payload.message_sent_at or payload.posted_at, text=text)

    async def run_once(self):
        """逐条分析已接收通知；崩溃重启靠原消息幂等键恢复，不会丢弃已接收摘要。"""
        result = {'processed': 0, 'failed': 0}
        async with self.lock:
            for job in await asyncio.to_thread(self.store.pending):
                try:
                    payload = NotificationInput.model_validate_json(job['payload_json'])
                    output = await self.processor.process(self.envelope(payload, job['notification_id']))
                    await asyncio.to_thread(self.store.finish, job['notification_id'], output['message']['message_id'])
                    result['processed'] += 1
                except Exception as error:
                    await asyncio.to_thread(self.store.finish, job['notification_id'], error_code=type(error).__name__, retry=not isinstance(error, ValueError))
                    result['failed'] += 1
        return result

    async def loop(self, stop_event):
        """保持网络接收与模型分析分离，轮询本地队列，服务关闭时等待在途分析。"""
        while not stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1)
            except TimeoutError:
                pass
