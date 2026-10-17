"""邮件收取队列：原始 EML 与 UID 游标先持久化，业务分析失败按单封邮件重试。"""

import hashlib
import json
import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


MAX_ATTEMPTS = 5


class MailStore:
    """复用业务 SQLite 保存收取游标和重试任务，原始邮件以受管文件保存。"""

    def __init__(self, task_store, root_dir: Path):
        """建立增量表和原件目录，不连接邮箱，不接收或保存授权码。"""
        self.db_path = Path(task_store.db_path).resolve()
        self.root_dir = Path(root_dir).resolve()
        self.mail_dir = self.root_dir / "mail"
        self.mail_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        statements = [
            """CREATE TABLE IF NOT EXISTS mail_cursors (
                account_id TEXT NOT NULL, mailbox TEXT NOT NULL, uidvalidity TEXT NOT NULL,
                last_uid INTEGER NOT NULL CHECK(last_uid >= 0), updated_at TEXT NOT NULL,
                PRIMARY KEY(account_id, mailbox, uidvalidity)
            )""",
            """CREATE TABLE IF NOT EXISTS mail_jobs (
                job_id TEXT PRIMARY KEY NOT NULL, account_id TEXT NOT NULL,
                mailbox TEXT NOT NULL, uidvalidity TEXT NOT NULL, uid INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','retry','processed','failed','skipped')),
                raw_sha256 TEXT, metadata_json TEXT NOT NULL DEFAULT '{}',
                message_id TEXT, error_code TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT, attachment_results_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(account_id, mailbox, uidvalidity, uid)
            )""",
            "CREATE INDEX IF NOT EXISTS mail_jobs_pending ON mail_jobs(account_id, status, next_attempt_at)",
        ]
        with self._connection(write=True) as connection:
            for statement in statements:
                connection.execute(statement)

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """为一次操作打开短连接，并在成功时提交、异常时回滚，最后关闭连接。"""
        with closing(sqlite3.connect(self.db_path, timeout=5, autocommit=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
            with connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection

    @staticmethod
    def _now(now=None) -> datetime:
        """取得带时区的 UTC 时间，允许测试注入确定时刻，拒绝隐式本地时区。"""
        value = datetime.now(timezone.utc) if now is None else now
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now 必须是带时区的 datetime 或 ISO 时间")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        """使用统一精度的 UTC 字符串，让数据库可以直接比较重试时间。"""
        return value.isoformat(timespec="microseconds")

    @staticmethod
    def _identity(account, mailbox, uidvalidity, uid=None) -> tuple:
        """校验账号、邮箱文件夹和 UID 命名空间，避免使用会重编号的邮件序号。"""
        if any(not isinstance(value, str) or not value.strip() for value in (account, mailbox, uidvalidity)):
            raise ValueError("账号、文件夹和 UIDVALIDITY 不能为空")
        if uid is not None and (not isinstance(uid, int) or isinstance(uid, bool) or not 1 <= uid <= 4_294_967_295):
            raise ValueError("UID 必须是有效正整数")
        return (account, mailbox, uidvalidity) if uid is None else (account, mailbox, uidvalidity, uid)

    @staticmethod
    def _json(value) -> str:
        """保存严格 JSON 元数据；其中不得放授权码或提供商原始错误。"""
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)

    @staticmethod
    def _error(error_code: str) -> str:
        """仅接受安全的异常类型标识，禁止将凭证或整段服务器错误写入队列。"""
        if not isinstance(error_code, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", error_code) is None:
            raise ValueError("error_code 必须是安全的错误类型标识")
        return error_code

    @staticmethod
    def _detail(row: sqlite3.Row) -> dict:
        """返回任务状态及业务元数据，不公开原件路径或原始邮件字节。"""
        return {
            **{key: row[key] for key in (
                "job_id", "account_id", "mailbox", "uidvalidity", "uid", "status",
                "message_id", "error_code", "attempts", "next_attempt_at", "created_at", "updated_at",
            )},
            "metadata": json.loads(row["metadata_json"]),
            "attachment_results": json.loads(row["attachment_results_json"]),
            "needs_attention": row["status"] in {"failed", "skipped"},
            "has_raw": row["raw_sha256"] is not None,
        }

    def get_cursor(self, account, mailbox, uidvalidity) -> int | None:
        """读取一个 UIDVALIDITY 命名空间的收取高水位，首次接入时返回 None。"""
        identity = self._identity(account, mailbox, uidvalidity)
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT last_uid FROM mail_cursors WHERE account_id=? AND mailbox=? AND uidvalidity=?", identity,
            ).fetchone()
            return row["last_uid"] if row is not None else None

    def has_other_uidvalidity(self, account, mailbox, uidvalidity) -> bool:
        """检查同一邮箱是否曾使用其他 UIDVALIDITY，供收取器报告服务器命名空间变化。"""
        identity = self._identity(account, mailbox, uidvalidity)
        with self._lock, self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM mail_cursors WHERE account_id=? AND mailbox=? AND uidvalidity!=? LIMIT 1", identity,
            ).fetchone() is not None

    def _advance(self, connection, identity: tuple, last_uid: int, timestamp: str) -> None:
        """在调用方事务内推进游标，重复处理旧 UID 不会使高水位倒退。"""
        connection.execute(
            """INSERT INTO mail_cursors VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(account_id,mailbox,uidvalidity)
               DO UPDATE SET last_uid=MAX(last_uid,excluded.last_uid), updated_at=excluded.updated_at""",
            (*identity, last_uid, timestamp),
        )

    def set_cursor(self, account, mailbox, uidvalidity, last_uid: int) -> None:
        """设置首轮跳过历史邮件的基线，或单调推进已有游标；不会删除旧命名空间。"""
        identity = self._identity(account, mailbox, uidvalidity)
        if not isinstance(last_uid, int) or isinstance(last_uid, bool) or not 0 <= last_uid <= 4_294_967_295:
            raise ValueError("last_uid 必须是有效的非负 UID")
        with self._lock, self._connection(write=True) as connection:
            self._advance(connection, identity, last_uid, self._timestamp(self._now()))

    def save_raw(self, account, mailbox, uidvalidity, uid, raw: bytes, metadata=None) -> dict:
        """原子保存完整 EML、队列记录和游标；相同 UID 原件变更时拒绝覆盖。"""
        identity = self._identity(account, mailbox, uidvalidity, uid)
        if not isinstance(raw, bytes):
            raise ValueError("原始邮件必须是 bytes")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("邮件元数据必须是对象")
        metadata_json = self._json(metadata or {})
        job_id = hashlib.sha256(self._json(identity).encode("utf-8")).hexdigest()[:32]
        fingerprint = hashlib.sha256(raw).hexdigest()
        timestamp = self._timestamp(self._now())
        path = self.mail_dir / (job_id + ".eml")
        temporary_path = self.mail_dir / (uuid4().hex + ".tmp")
        created = False
        with self._lock:
            try:
                with self._connection(write=True) as connection:
                    previous = connection.execute("SELECT * FROM mail_jobs WHERE job_id=?", (job_id,)).fetchone()
                    if previous is not None and previous["raw_sha256"] not in (None, fingerprint):
                        raise ValueError("同一 UID 的原始邮件发生变化，旧原件未覆盖")
                    if path.exists():
                        if hashlib.sha256(path.read_bytes()).hexdigest() != fingerprint:
                            raise OSError("已存邮件原件与指纹不一致")
                    else:
                        # 写完并刷新原件后才提交 SQL 游标，避免重启后游标越过丢失的邮件。
                        with temporary_path.open("wb") as target:
                            target.write(raw)
                            target.flush()
                            os.fsync(target.fileno())
                        os.replace(temporary_path, path)
                        created = True
                    connection.execute(
                        """INSERT INTO mail_jobs (
                            job_id,account_id,mailbox,uidvalidity,uid,status,raw_sha256,metadata_json,created_at,updated_at
                           ) VALUES (?,?,?,?,?,'pending',?,?,?,?)
                           ON CONFLICT(job_id) DO NOTHING""",
                        (job_id, *identity, fingerprint, metadata_json, timestamp, timestamp),
                    )
                    # 被显式跳过的 UID 后续取得原文时可转入队列，其余重复收取不重置业务状态。
                    if previous is not None and previous["raw_sha256"] is None:
                        connection.execute(
                            """UPDATE mail_jobs SET status='pending',raw_sha256=?,metadata_json=?,
                               error_code=NULL,attempts=0,next_attempt_at=NULL,updated_at=? WHERE job_id=?""",
                            (fingerprint, metadata_json, timestamp, job_id),
                        )
                    self._advance(connection, identity[:3], uid, timestamp)
                    result = self._detail(connection.execute("SELECT * FROM mail_jobs WHERE job_id=?", (job_id,)).fetchone())
                return result
            except Exception:
                if created:
                    path.unlink(missing_ok=True)
                raise
            finally:
                temporary_path.unlink(missing_ok=True)

    def record_skipped(self, account, mailbox, uidvalidity, uid, error_code, metadata=None) -> dict:
        """持久化超限或已消失邮件的跳过原因，再推进游标；已有原文的任务不会被覆盖。"""
        identity = self._identity(account, mailbox, uidvalidity, uid)
        error_code = self._error(error_code)
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("邮件元数据必须是对象")
        job_id = hashlib.sha256(self._json(identity).encode("utf-8")).hexdigest()[:32]
        timestamp = self._timestamp(self._now())
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """INSERT INTO mail_jobs (
                    job_id,account_id,mailbox,uidvalidity,uid,status,error_code,metadata_json,created_at,updated_at
                   ) VALUES (?,?,?,?,?,'skipped',?,?,?,?) ON CONFLICT(job_id) DO NOTHING""",
                (job_id, *identity, error_code, self._json(metadata or {}), timestamp, timestamp),
            )
            self._advance(connection, identity[:3], uid, timestamp)
            return self._detail(connection.execute("SELECT * FROM mail_jobs WHERE job_id=?", (job_id,)).fetchone())

    def read_raw(self, job_id: str) -> bytes:
        """按数据库任务定位受管 EML 并验证 SHA256，不接受客户端提供文件路径。"""
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT raw_sha256 FROM mail_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["raw_sha256"] is None:
                raise ValueError("该邮件任务没有已保存的原文")
            if re.fullmatch(r"[a-f0-9]{32}", job_id) is None:
                raise ValueError("邮件任务编号不合法")
            raw = (self.mail_dir / (job_id + ".eml")).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["raw_sha256"]:
                raise OSError("邮件原件未通过指纹校验")
            return raw

    def pending_jobs(self, account, limit: int = 10, now=None) -> list[dict]:
        """返回待处理和已到退避时刻的单信任务，失败邮件与新的收取游标相互独立。"""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit 必须是正整数")
        timestamp = self._timestamp(self._now(now))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM mail_jobs WHERE account_id=? AND
                   (status='pending' OR (status='retry' AND next_attempt_at<=?)) ORDER BY rowid LIMIT ?""",
                (account, timestamp, limit),
            ).fetchall()
            return [self._detail(row) for row in rows]

    def mark_success(self, job_id, message_id, attachment_results=None) -> None:
        """标记业务消息和附件处理完成，保存真实结果，不因重复成功重置已完成任务。"""
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("成功任务必须有真实业务消息编号")
        if attachment_results is not None and not isinstance(attachment_results, list):
            raise ValueError("附件处理结果必须是列表")
        results_json = self._json(attachment_results or [])
        with self._lock, self._connection(write=True) as connection:
            row = connection.execute("SELECT * FROM mail_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["raw_sha256"] is None:
                raise ValueError("邮件原件尚未保存")
            if row["status"] == "processed":
                if row["message_id"] != message_id:
                    raise ValueError("已完成任务对应的业务消息编号不同")
                return
            connection.execute(
                """UPDATE mail_jobs SET status='processed',message_id=?,attachment_results_json=?,
                   error_code=NULL,next_attempt_at=NULL,updated_at=? WHERE job_id=?""",
                (message_id, results_json, self._timestamp(self._now()), job_id),
            )

    def mark_failure(self, job_id, error_code, *, retryable: bool = True, now=None) -> None:
        """记录安全失败类型并按 15/30/60/120 秒退避，第五次失败转为需人工处理。"""
        error_code = self._error(error_code)
        current = self._now(now)
        with self._lock, self._connection(write=True) as connection:
            row = connection.execute("SELECT * FROM mail_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("邮件任务不存在")
            if row["status"] in {"processed", "skipped", "failed"}:
                return
            attempts = min(row["attempts"] + 1, MAX_ATTEMPTS)
            retry = retryable and attempts < MAX_ATTEMPTS
            due = self._timestamp(current + timedelta(seconds=15 * 2 ** (attempts - 1))) if retry else None
            connection.execute(
                """UPDATE mail_jobs SET status=?,error_code=?,attempts=?,next_attempt_at=?,updated_at=?
                   WHERE job_id=?""",
                ("retry" if retry else "failed", error_code, attempts, due, self._timestamp(current), job_id),
            )

    def retry_failed(self, account) -> int:
        """显式重置该账号已失败或正在退避且仍有原件的任务，让下一次同步立即重试。"""
        with self._lock, self._connection(write=True) as connection:
            cursor = connection.execute(
                """UPDATE mail_jobs SET status='pending',attempts=0,next_attempt_at=NULL,error_code=NULL,
                   updated_at=? WHERE account_id=? AND status IN ('failed','retry') AND raw_sha256 IS NOT NULL""",
                (self._timestamp(self._now()), account),
            )
            return cursor.rowcount

    def list_jobs(self, account, limit: int = 50) -> list[dict]:
        """查询单个账号的安全任务详情，不返回磁盘路径、原始邮件或凭证。"""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit 必须是正整数")
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM mail_jobs WHERE account_id=? ORDER BY rowid DESC LIMIT ?", (account, limit),
            ).fetchall()
            return [self._detail(row) for row in rows]
