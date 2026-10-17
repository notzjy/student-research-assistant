"""提醒持久层：同库任务关联、版本化 outbox、原子租约认领与有界退避重试。"""

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from student_assistant.bridge import TaskStore, parse_reference_time


DEFAULT_POLICY = {
    "enabled": False,
    "lead_minutes": 30,
    "date_only_time": None,
    "channel": "console",
}


class ReminderConflictError(ValueError):
    """表示调用方编辑或取消的提醒版本已过期，不能覆盖其他请求的更新。"""

    def __init__(self, reminder_id: str):
        """保存冲突提醒的稳定编号，供接口返回不包含业务原文的安全错误。"""
        self.reminder_id = reminder_id
        super().__init__("提醒版本已变化，请重新读取后再修改")


class ReminderStore:
    """在业务 SQLite 内保存提醒计划、每版投递任务和用户明确配置的自动提醒规则。"""

    def __init__(self, task_store: TaskStore):
        """复用任务数据库并初始化提醒表；不创建后台线程，不调用模型或通知系统。"""
        self.task_store = task_store
        self.db_path = Path(task_store.db_path).resolve()
        statements = [
            """
            CREATE TABLE IF NOT EXISTS reminder_schedules (
                reminder_id TEXT PRIMARY KEY NOT NULL,
                task_id TEXT NOT NULL REFERENCES tasks(id),
                version INTEGER NOT NULL CHECK(version >= 1)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS reminder_jobs (
                reminder_id TEXT NOT NULL REFERENCES reminder_schedules(reminder_id),
                version INTEGER NOT NULL CHECK(version >= 1),
                title TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                due_at_utc TEXT NOT NULL,
                channel TEXT NOT NULL CHECK(channel IN ('console', 'windows')),
                origin TEXT NOT NULL DEFAULT 'legacy'
                    CHECK(origin IN ('manual', 'automatic', 'legacy')),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'leased', 'submitted', 'cancelled', 'failed')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                next_attempt_at TEXT NOT NULL,
                lease_token TEXT,
                lease_until TEXT,
                error_code TEXT,
                submitted_at TEXT,
                PRIMARY KEY(reminder_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS reminder_policy (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                policy_json TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS reminder_jobs_due ON reminder_jobs(status, next_attempt_at, lease_until)",
            "CREATE INDEX IF NOT EXISTS reminder_schedules_task ON reminder_schedules(task_id)",
            """
            CREATE TRIGGER IF NOT EXISTS cancel_reminders_for_finished_task
            AFTER UPDATE OF status ON tasks WHEN NEW.status != 'pending'
            BEGIN
                UPDATE reminder_jobs
                SET status = 'cancelled', lease_token = NULL, lease_until = NULL
                WHERE reminder_id IN (
                    SELECT reminder_id FROM reminder_schedules WHERE task_id = NEW.id
                ) AND status NOT IN ('submitted', 'cancelled');
            END
            """,
        ]
        with self._connection(write=True) as connection:
            for statement in statements:
                connection.execute(statement)
            # 旧计划无法可靠区分手工与自动来源；只补 legacy 标记，不重写版本、状态或时间。
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(reminder_jobs)")}
            if "origin" not in columns:
                connection.execute(
                    "ALTER TABLE reminder_jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'legacy' "
                    "CHECK(origin IN ('manual', 'automatic', 'legacy'))"
                )
            connection.execute(
                "INSERT INTO reminder_policy (singleton, policy_json) VALUES (1, ?) ON CONFLICT(singleton) DO NOTHING",
                (json.dumps(DEFAULT_POLICY, ensure_ascii=False),),
            )

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """为一次操作开启短事务；先启用外键，写操作在读取前获取 SQLite 写锁。"""
        with closing(sqlite3.connect(self.db_path, timeout=5, autocommit=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
            with connection:
                # 认领和版本检查都在 BEGIN IMMEDIATE 后执行，其他 worker 不能双重认领。
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection

    @staticmethod
    def _utc(value: datetime) -> str:
        """拒绝无时区时钟，转为统一微秒精度 UTC 字符串，供 SQLite 正确排序和比较。"""
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("提醒时间必须携带明确时区")
        try:
            return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
        except (ValueError, OverflowError):
            raise ValueError("提醒时间转换到 UTC 后超出可表示范围") from None

    @staticmethod
    def _positive_integer(value: int, name: str) -> int:
        """校验批量、租约、版本和重试上限，避免把布尔值当成整数配置。"""
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} 必须是正整数")
        return value

    @staticmethod
    def _channel(value: str) -> str:
        """限制本模块真实支持的通知通道，不接收任意可执行命令或路径。"""
        if value not in ("console", "windows") or not isinstance(value, str):
            raise ValueError("channel 只允许 console 或 windows")
        return value

    def _detail(self, connection: sqlite3.Connection, reminder_id: str, *, include_token: bool = False) -> dict | None:
        """读取当前版本详情；任务标题实时关联，历史任务仍保留其当时标题作为后备。"""
        row = connection.execute(
            """
            SELECT schedule.reminder_id, schedule.task_id, job.version,
                   COALESCE(task.title, job.title) AS title, job.remind_at, job.channel, job.origin,
                   job.status, job.attempts, job.next_attempt_at, job.lease_until,
                   job.error_code, job.submitted_at, job.lease_token
            FROM reminder_schedules AS schedule
            JOIN reminder_jobs AS job
                ON job.reminder_id = schedule.reminder_id AND job.version = schedule.version
            LEFT JOIN tasks AS task ON task.id = schedule.task_id
            WHERE schedule.reminder_id = ?
            """,
            (reminder_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if not include_token:
            result.pop("lease_token")
        return result

    def save(
        self, task_id: str, remind_at: str, channel: str = "console", *,
        reminder_id: str | None = None, expected_version: int | None = None,
    ) -> dict:
        """创建或按期待版本编辑手工计划；不替用户补钟点，复用已有记录时保留来源。"""
        due_at = self._schedule_time(task_id, remind_at, channel)
        with self._connection(write=True) as connection:
            return self._save_in_transaction(
                connection, task_id, remind_at, due_at, channel,
                reminder_id=reminder_id, expected_version=expected_version,
            )

    def save_automatic(self, task_id: str, remind_at: str, channel: str = "console", *, replace_existing: bool = False) -> dict:
        """原子建立自动首计划；只有明确修订后的重排才为旧自动计划生成新版本。"""
        due_at = self._schedule_time(task_id, remind_at, channel)
        with self._connection(write=True) as connection:
            # 查询和插入共用一份 BEGIN IMMEDIATE 事务；不同规则下并发执行也只会建立首计划。
            previous = connection.execute(
                "SELECT reminder_id FROM reminder_schedules WHERE task_id = ? ORDER BY rowid LIMIT 1",
                (task_id,),
            ).fetchone()
            if previous is not None:
                detail = self._detail(connection, previous['reminder_id'])
                if replace_existing and detail.get('origin') == 'automatic' and not (detail['status'] in ('pending', 'leased') and self._utc(parse_reference_time(detail['remind_at'])) == due_at and detail['channel'] == channel):
                    saved = self._save_in_transaction(connection, task_id, remind_at, due_at, channel, reminder_id=detail['reminder_id'], expected_version=detail['version'], origin='automatic')
                    return {'status': 'updated', 'reminder': saved}
                return {"status": "exists", "reminder": detail}
            saved = self._save_in_transaction(connection, task_id, remind_at, due_at, channel, origin="automatic")
            return {"status": "created", "reminder": saved}

    def _schedule_time(self, task_id: str, remind_at: str, channel: str) -> str:
        """共用任务编号、明确时刻及通道校验，返回用于幂等和排序的规范 UTC 时间。"""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id 必须是非空字符串")
        due_at = self._utc(parse_reference_time(remind_at))
        self._channel(channel)
        return due_at

    def _save_in_transaction(
        self, connection: sqlite3.Connection, task_id: str, remind_at: str, due_at: str, channel: str, *,
        reminder_id: str | None = None, expected_version: int | None = None, origin: str = "manual",
    ) -> dict:
        """在调用方写事务中创建或编辑，并记录计划来源；不嵌套连接或提前提交。"""
        if origin not in ("manual", "automatic", "legacy"):
            raise ValueError("origin 必须是 manual、automatic 或 legacy")
        if reminder_id is None and expected_version is not None:
            raise ValueError("创建提醒不应指定 expected_version")
        task = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise ValueError("目标待办不存在")
        if reminder_id is None:
            # 同一任务、同一真实 UTC 时刻、同一通道生成稳定 ID；Z 与等价偏移不会重复建计划。
            reminder_id = hashlib.sha256((task_id + due_at + channel).encode("utf-8")).hexdigest()[:24]
            existing = self._detail(connection, reminder_id)
            if existing is not None:
                return existing
            if task["status"] != "pending":
                raise ValueError("只有未完成待办可以新建提醒")
            version = 1
            connection.execute(
                "INSERT INTO reminder_schedules (reminder_id, task_id, version) VALUES (?, ?, ?)",
                (reminder_id, task_id, version),
            )
        else:
            if not isinstance(reminder_id, str) or not reminder_id.strip():
                raise ValueError("reminder_id 必须是非空字符串")
            existing = self._detail(connection, reminder_id)
            if existing is None:
                raise ValueError("提醒不存在，不能编辑")
            if expected_version is None:
                raise ReminderConflictError(reminder_id)
            self._positive_integer(expected_version, "expected_version")
            if existing["version"] != expected_version:
                raise ReminderConflictError(reminder_id)
            if existing["task_id"] != task_id:
                raise ValueError("编辑提醒不能更换关联待办")
            if task["status"] != "pending":
                raise ValueError("已结束待办不能重新安排提醒")
            version = existing["version"] + 1
            # 已交给 OS 的旧记录无法撤回；其余旧任务取消，旧 worker 的租约同时失效。
            connection.execute(
                """
                UPDATE reminder_jobs SET status = 'cancelled', lease_token = NULL, lease_until = NULL
                WHERE reminder_id = ? AND status NOT IN ('submitted', 'cancelled')
                """,
                (reminder_id,),
            )
            connection.execute(
                "UPDATE reminder_schedules SET version = ? WHERE reminder_id = ? AND version = ?",
                (version, reminder_id, expected_version),
            )
        connection.execute(
            """
            INSERT INTO reminder_jobs (
                reminder_id, version, title, remind_at, due_at_utc, channel, next_attempt_at, origin
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (reminder_id, version, task["title"], remind_at, due_at, channel, due_at, origin),
        )
        return self._detail(connection, reminder_id)

    def get(self, reminder_id: str) -> dict | None:
        """查询当前提醒版本，不认领或发送任何通知。"""
        with self._connection() as connection:
            return self._detail(connection, reminder_id)

    def get_source_context(self, reminder_id: str) -> dict | None:
        """仅查询提醒关联消息的来源、发送时间和解析时间；旧手工任务或缺表时返回 None。"""
        with self._connection() as connection:
            tables = {
                row["name"] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('messages', 'event_links')"
                )
            }
            if tables != {"messages", "event_links"}:
                return None
            # SQL 只选择时间元数据；完整原文不进入返回值或通知发送守卫。
            row = connection.execute(
                """
                SELECT messages.source,
                       json_extract(messages.envelope_json, '$.sent_at') AS sent_at,
                       links.time_json
                FROM reminder_schedules AS schedule
                JOIN event_links AS links ON links.task_id = schedule.task_id
                JOIN messages ON messages.message_id = links.message_id
                WHERE schedule.reminder_id = ?
                """,
                (reminder_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "source": row["source"], "sent_at": row["sent_at"],
                "time": json.loads(row["time_json"]) if row["time_json"] is not None else None,
            }

    def list_reminders(self) -> list[dict]:
        """按创建顺序返回各提醒当前版本，保留旧 job 在数据库中用于审计。"""
        with self._connection() as connection:
            rows = connection.execute("SELECT reminder_id FROM reminder_schedules ORDER BY rowid").fetchall()
            return [self._detail(connection, row["reminder_id"]) for row in rows]

    def cancel(self, reminder_id: str, expected_version: int | None = None) -> dict | None:
        """取消当前尚未提交的提醒并失效租约；已 submitted 的记录不能声称已经撤回。"""
        with self._connection(write=True) as connection:
            current = self._detail(connection, reminder_id)
            if current is None:
                return None
            if expected_version is not None:
                self._positive_integer(expected_version, "expected_version")
                if current["version"] != expected_version:
                    raise ReminderConflictError(reminder_id)
            connection.execute(
                """
                UPDATE reminder_jobs SET status = 'cancelled', lease_token = NULL, lease_until = NULL
                WHERE reminder_id = ? AND version = ? AND status != 'submitted'
                """,
                (reminder_id, current["version"]),
            )
            return self._detail(connection, reminder_id)

    @staticmethod
    def _cancel_inactive_tasks(connection: sqlite3.Connection) -> None:
        """认领时再复核任务，覆盖旧数据、外部删除或其他更新路径造成的无效提醒。"""
        connection.execute(
            """
            UPDATE reminder_jobs SET status = 'cancelled', lease_token = NULL, lease_until = NULL
            WHERE status NOT IN ('submitted', 'cancelled') AND NOT EXISTS (
                SELECT 1 FROM reminder_schedules AS schedule JOIN tasks AS task ON task.id = schedule.task_id
                WHERE schedule.reminder_id = reminder_jobs.reminder_id AND task.status = 'pending'
            )
            """
        )

    def claim_due(
        self, now: datetime, limit: int = 20, lease_seconds: int = 60, max_attempts: int = 5,
    ) -> list[dict]:
        """在短写事务内原子认领到期、退避结束或租约过期的当前 job，每次使用新 token。"""
        now_utc = self._utc(now)
        self._positive_integer(limit, "limit")
        self._positive_integer(lease_seconds, "lease_seconds")
        self._positive_integer(max_attempts, "max_attempts")
        try:
            lease_until = self._utc(datetime.fromisoformat(now_utc) + timedelta(seconds=lease_seconds))
        except OverflowError:
            raise ValueError("租约结束时间超出可表示范围") from None
        with self._connection(write=True) as connection:
            self._cancel_inactive_tasks(connection)
            # 崩溃 worker 耗尽尝试次数后也不能无限恢复，保留明确终止状态。
            connection.execute(
                """
                UPDATE reminder_jobs SET status = 'failed', lease_token = NULL, lease_until = NULL,
                    error_code = COALESCE(error_code, 'AttemptsExhausted')
                WHERE attempts >= ? AND (
                    (status = 'pending' AND next_attempt_at <= ?) OR
                    (status = 'leased' AND lease_until <= ?)
                ) AND EXISTS (
                    SELECT 1 FROM reminder_schedules AS schedule
                    WHERE schedule.reminder_id = reminder_jobs.reminder_id AND schedule.version = reminder_jobs.version
                )
                """,
                (max_attempts, now_utc, now_utc),
            )
            rows = connection.execute(
                """
                SELECT job.reminder_id, job.version FROM reminder_jobs AS job
                JOIN reminder_schedules AS schedule
                    ON schedule.reminder_id = job.reminder_id AND schedule.version = job.version
                JOIN tasks AS task ON task.id = schedule.task_id
                WHERE task.status = 'pending' AND job.attempts < ? AND (
                    (job.status = 'pending' AND job.next_attempt_at <= ?) OR
                    (job.status = 'leased' AND job.lease_until <= ?)
                ) ORDER BY job.next_attempt_at, job.reminder_id LIMIT ?
                """,
                (max_attempts, now_utc, now_utc, limit),
            ).fetchall()
            claimed = []
            for row in rows:
                token = uuid4().hex
                connection.execute(
                    """
                    UPDATE reminder_jobs SET status = 'leased', attempts = attempts + 1,
                        lease_token = ?, lease_until = ?
                    WHERE reminder_id = ? AND version = ?
                    """,
                    (token, lease_until, row["reminder_id"], row["version"]),
                )
                claimed.append(self._detail(connection, row["reminder_id"], include_token=True))
            return claimed

    @staticmethod
    def _owned_claim(
        connection: sqlite3.Connection, reminder_id: str, version: int, token: str, now_utc: str,
    ) -> sqlite3.Row | None:
        """检查当前版本、随机 token、未过期租约和任务状态，旧回执不得影响新计划。"""
        if type(version) is not int or version < 1 or not isinstance(token, str) or not token:
            return None
        return connection.execute(
            """
            SELECT job.* FROM reminder_jobs AS job
            JOIN reminder_schedules AS schedule
                ON schedule.reminder_id = job.reminder_id AND schedule.version = job.version
            JOIN tasks AS task ON task.id = schedule.task_id
            WHERE job.reminder_id = ? AND job.version = ? AND job.lease_token = ?
                AND job.status = 'leased' AND job.lease_until > ? AND task.status = 'pending'
            """,
            (reminder_id, version, token, now_utc),
        ).fetchone()

    def validate_claim(self, reminder_id: str, version: int, token: str, now: datetime) -> bool:
        """发送前只读复核租约，返回此 worker 现在是否仍有权提交该版本通知。"""
        now_utc = self._utc(now)
        with self._connection() as connection:
            return self._owned_claim(connection, reminder_id, version, token, now_utc) is not None

    def mark_submitted(self, reminder_id: str, version: int, token: str, now: datetime) -> bool:
        """仅用有效租约记录通知已交给通道，submitted 不代表用户已读或 OS 恰好投递一次。"""
        now_utc = self._utc(now)
        with self._connection(write=True) as connection:
            if self._owned_claim(connection, reminder_id, version, token, now_utc) is None:
                return False
            connection.execute(
                """
                UPDATE reminder_jobs SET status = 'submitted', submitted_at = ?,
                    lease_token = NULL, lease_until = NULL, error_code = NULL
                WHERE reminder_id = ? AND version = ? AND lease_token = ?
                """,
                (now_utc, reminder_id, version, token),
            )
            return True

    def suppress_claim(
        self, reminder_id: str, version: int, token: str, error_code: str, now: datetime,
    ) -> bool:
        """用有效租约抑制一条通知并保留安全原因，不修改原消息、任务状态或计划时间。"""
        if not isinstance(error_code, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", error_code) is None:
            raise ValueError("error_code 只允许安全原因名，不允许原文或凭证内容")
        now_utc = self._utc(now)
        with self._connection(write=True) as connection:
            if self._owned_claim(connection, reminder_id, version, token, now_utc) is None:
                return False
            # 校验和状态变更处于同一写事务，过期 token 无法取消新版本或已投递记录。
            connection.execute(
                """
                UPDATE reminder_jobs SET status = 'cancelled', error_code = ?,
                    lease_token = NULL, lease_until = NULL
                WHERE reminder_id = ? AND version = ? AND lease_token = ?
                """,
                (error_code, reminder_id, version, token),
            )
            return True

    def mark_failed(
        self, reminder_id: str, version: int, token: str, error_code: str, now: datetime,
        max_attempts: int = 5,
    ) -> bool:
        """记录安全异常名，失败按 2**attempts 秒退避至多300秒，达到上限后停止重试。"""
        if not isinstance(error_code, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", error_code) is None:
            raise ValueError("error_code 只允许安全异常名，不允许原始错误或凭证内容")
        self._positive_integer(max_attempts, "max_attempts")
        now_utc = self._utc(now)
        with self._connection(write=True) as connection:
            row = self._owned_claim(connection, reminder_id, version, token, now_utc)
            if row is None:
                return False
            exhausted = row["attempts"] >= max_attempts
            try:
                delay = min(2 ** min(row["attempts"], 9), 300)
                next_attempt_at = self._utc(datetime.fromisoformat(now_utc) + timedelta(seconds=delay))
            except OverflowError:
                raise ValueError("下次尝试时间超出可表示范围") from None
            connection.execute(
                """
                UPDATE reminder_jobs SET status = ?, next_attempt_at = ?, error_code = ?,
                    lease_token = NULL, lease_until = NULL
                WHERE reminder_id = ? AND version = ? AND lease_token = ?
                """,
                ("failed" if exhausted else "pending", next_attempt_at, error_code, reminder_id, version, token),
            )
            return True

    @staticmethod
    def _policy(value: dict) -> dict:
        """校验完整自动提醒规则；未启用时不因日期线索自行创建计划。"""
        if not isinstance(value, dict) or set(value) != set(DEFAULT_POLICY):
            raise ValueError("提醒规则只允许 enabled、lead_minutes、date_only_time、channel")
        if type(value["enabled"]) is not bool:
            raise ValueError("enabled 必须是布尔值")
        if type(value["lead_minutes"]) is not int or not 0 <= value["lead_minutes"] <= 10080:
            raise ValueError("lead_minutes 必须是 0 至 10080 的整数")
        date_clock = value["date_only_time"]
        if date_clock is not None and (not isinstance(date_clock, str) or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", date_clock) is None):
            raise ValueError("date_only_time 必须是 HH:MM 或 None")
        ReminderStore._channel(value["channel"])
        return dict(value)

    def get_policy(self) -> dict:
        """读取持久规则，默认禁用且不为日期级事项补造钟点。"""
        with self._connection() as connection:
            row = connection.execute("SELECT policy_json FROM reminder_policy WHERE singleton = 1").fetchone()
            return self._policy(json.loads(row["policy_json"]))

    def set_policy(self, policy: dict) -> dict:
        """原子更新给出的规则字段并返回完整规则，拒绝未知字段和隐式类型转换。"""
        if not isinstance(policy, dict) or not set(policy).issubset(DEFAULT_POLICY):
            raise ValueError("提醒规则包含未知字段")
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT policy_json FROM reminder_policy WHERE singleton = 1").fetchone()
            updated = self._policy({**json.loads(row["policy_json"]), **policy})
            connection.execute("UPDATE reminder_policy SET policy_json = ? WHERE singleton = 1", (json.dumps(updated, ensure_ascii=False),))
            return updated
