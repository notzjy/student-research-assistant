"""消息业务存储：先保存完整原文，再原子提交分析、待办及来源关联。"""

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from student_assistant.bridge import TaskStore
from student_assistant.message_models import MessageInput, RoleRule

# bridge 已注册教学模块路径；复用旧行模型和插入逻辑，不另开事务保存事项。
from task_store import StoredTask


class MessageConflictError(ValueError):
    """表示同一来源消息编号收到不同内容，保留原消息编号供 HTTP 层报告冲突。"""

    def __init__(self, message_id: str):
        """记录已存在的消息 ID，并提供不包含原文或凭证的冲突说明。"""
        self.message_id = message_id
        super().__init__("同一来源消息编号对应的内容发生变化，原消息未覆盖")


class MessageStore:
    """在既有业务库中追加消息与角色表，不修改旧 tasks 表和旧工具接口。"""

    def __init__(self, task_store: TaskStore):
        """复用传入存储的数据库文件，并在短事务中建立新增表和索引。"""
        self.task_store = task_store
        self.db_path = Path(task_store.db_path).resolve()
        statements = [
            """
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY NOT NULL,
                source TEXT NOT NULL,
                account_id TEXT NOT NULL,
                source_message_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                envelope_json TEXT NOT NULL,
                envelope_hash TEXT NOT NULL,
                processing_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(processing_status IN ('pending', 'failed', 'completed')),
                error_code TEXT,
                category TEXT,
                analysis_json TEXT,
                UNIQUE(source, account_id, source_message_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS role_rules (
                source TEXT NOT NULL,
                account_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                role TEXT NOT NULL,
                important INTEGER NOT NULL CHECK(important IN (0, 1)),
                PRIMARY KEY(source, account_id, sender_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS attachments (
                message_id TEXT NOT NULL,
                attachment_index INTEGER NOT NULL CHECK(attachment_index >= 0),
                hint_json TEXT NOT NULL,
                PRIMARY KEY(message_id, attachment_index),
                FOREIGN KEY(message_id) REFERENCES messages(message_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS event_links (
                event_id TEXT PRIMARY KEY NOT NULL,
                message_id TEXT NOT NULL,
                event_index INTEGER NOT NULL CHECK(event_index >= 0),
                task_id TEXT NOT NULL UNIQUE,
                evidence_json TEXT NOT NULL,
                time_json TEXT,
                UNIQUE(message_id, event_index),
                FOREIGN KEY(message_id) REFERENCES messages(message_id),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS messages_category ON messages(category)",
            "CREATE INDEX IF NOT EXISTS messages_sender ON messages(source, account_id, sender_id)",
        ]
        with self._connection(write=True) as connection:
            # 不使用 executescript，避免其隐式提交破坏本次初始化事务。
            for statement in statements:
                connection.execute(statement)

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """为一次操作打开连接；外键在事务前启用，写事务先获取写锁，异常时整批回滚。"""
        with closing(sqlite3.connect(self.db_path, timeout=5, autocommit=True)) as connection:
            connection.row_factory = sqlite3.Row
            # foreign_keys 在已开始的事务中切换会被忽略，因此必须先设置再 BEGIN。
            connection.execute("PRAGMA foreign_keys = ON")
            connection.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
            with connection:
                # 先获取写锁再检查旧状态，避免两个并发重试都基于过期快照写入。
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection

    @staticmethod
    def _json(value: Any) -> str:
        """生成稳定且严格的 JSON，既用于原始数据保存，也用于幂等内容指纹。"""
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _role_dict(row: sqlite3.Row | None) -> dict | None:
        """把规则记录转为公开字段，并将 SQLite 整数转换回 important 布尔值。"""
        if row is None:
            return None
        return {
            "source": row["source"], "account_id": row["account_id"],
            "sender_id": row["sender_id"], "role": row["role"], "important": bool(row["important"]),
        }

    def receive(self, envelope: MessageInput) -> dict:
        """先持久化完整消息；同来源编号内容相同则复用，不同则抛冲突且不覆盖。"""
        payload = MessageInput.model_validate(envelope).model_dump(mode="json")
        envelope_json = self._json(payload)
        envelope_hash = hashlib.sha256(envelope_json.encode("utf-8")).hexdigest()
        key = {name: payload[name] for name in ("source", "account_id", "source_message_id")}
        message_id = hashlib.sha256(self._json(key).encode("utf-8")).hexdigest()[:24]
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (
                    message_id, source, account_id, source_message_id, sender_id,
                    envelope_json, envelope_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, account_id, source_message_id) DO NOTHING
                """,
                (message_id, payload["source"], payload["account_id"], payload["source_message_id"],
                 payload["sender_id"], envelope_json, envelope_hash),
            )
            row = connection.execute(
                "SELECT message_id, envelope_hash FROM messages WHERE source = ? AND account_id = ? AND source_message_id = ?",
                (payload["source"], payload["account_id"], payload["source_message_id"]),
            ).fetchone()
            if row["envelope_hash"] != envelope_hash:
                raise MessageConflictError(row["message_id"])
            if cursor.rowcount == 1:
                for index, hint in enumerate(payload["attachments"]):
                    connection.execute(
                        "INSERT INTO attachments (message_id, attachment_index, hint_json) VALUES (?, ?, ?)",
                        (message_id, index, self._json(hint)),
                    )
                return {"status": "received", "message_id": row["message_id"]}
            return {"status": "exists", "message_id": row["message_id"]}

    def _detail(self, connection: sqlite3.Connection, message_id: str) -> dict | None:
        """在同一读取快照中组装原文、分析、实时任务状态和当前角色匹配结果。"""
        message = connection.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        if message is None:
            return None
        analysis = json.loads(message["analysis_json"]) if message["analysis_json"] is not None else None
        category = analysis.get("category") if analysis is not None else None
        attachment_rows = connection.execute(
            "SELECT hint_json FROM attachments WHERE message_id = ? ORDER BY attachment_index", (message_id,)
        ).fetchall()
        attachments = [
            {**json.loads(row["hint_json"]), "category": category,
             "classification_basis": "message_text", "availability": "mentioned"}
            for row in attachment_rows
        ]
        # 实际原件状态是额外视图；保留 source_message 内的 mentioned 与原文指纹。
        has_rag = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rag_bindings'"
        ).fetchone()
        if has_rag is not None:
            bindings = connection.execute(
                """SELECT bindings.attachment_index, documents.document_id, documents.status
                   FROM rag_bindings AS bindings
                   JOIN rag_documents AS documents ON documents.document_id = bindings.document_id
                   WHERE bindings.message_id = ?""", (message_id,),
            ).fetchall()
            for binding in bindings:
                attachment = attachments[binding["attachment_index"]]
                attachment.update({
                    "document_id": binding["document_id"], "status": binding["status"],
                    "availability": "indexed" if binding["status"] == "indexed" else "available",
                })
        rows = connection.execute(
            """
            SELECT links.task_id, tasks.title, tasks.time_text, tasks.location, tasks.status,
                   links.evidence_json, links.time_json
            FROM event_links AS links JOIN tasks ON tasks.id = links.task_id
            WHERE links.message_id = ? ORDER BY links.event_index
            """,
            (message_id,),
        ).fetchall()
        events = [
            {"task_id": row["task_id"], "title": row["title"], "time_text": row["time_text"],
             "location": row["location"], "status": row["status"],
             "evidence": json.loads(row["evidence_json"]),
             "time": json.loads(row["time_json"]) if row["time_json"] is not None else None}
            for row in rows
        ]
        role_row = connection.execute(
            "SELECT * FROM role_rules WHERE source = ? AND account_id = ? AND sender_id = ?",
            (message["source"], message["account_id"], message["sender_id"]),
        ).fetchone()
        role_match = self._role_dict(role_row)
        return {
            "message_id": message["message_id"],
            "source_message": json.loads(message["envelope_json"]),
            "processing_status": message["processing_status"], "error_code": message["error_code"],
            "analysis": analysis, "events": events, "attachments": attachments,
            "role_match": role_match,
            "priority": "high" if role_match is not None and role_match["important"] else "normal",
        }

    def get_message(self, message_id: str) -> dict | None:
        """按稳定 ID 查询消息详情；没有该记录时返回 None，不触发模型或处理重试。"""
        with self._connection() as connection:
            return self._detail(connection, message_id)

    def list_messages(self, category: str | None = None, important_only: bool = False, limit: int = 50) -> list[dict]:
        """查询最近接收的消息，可按类别及当前用户配置的重点角色筛选。"""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit 必须是正整数")
        clauses = []
        parameters = []
        if category is not None:
            clauses.append("messages.category = ?")
            parameters.append(category)
        if important_only:
            clauses.append(
                """EXISTS (
                    SELECT 1 FROM role_rules AS roles
                    WHERE roles.source = messages.source AND roles.account_id = messages.account_id
                      AND roles.sender_id = messages.sender_id AND roles.important = 1
                )"""
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT message_id FROM messages" + where + " ORDER BY rowid DESC LIMIT ?",
                (*parameters, limit),
            ).fetchall()
            return [self._detail(connection, row["message_id"]) for row in rows]

    def _reviewed_query_parts(
        self, connection: sqlite3.Connection, category: str | None,
        important_only: bool, label: str | None, query: str | None,
    ) -> tuple[str, str, list]:
        """为修订后列表与总数共用参数化筛选；人工值优先，筛选发生在分页之前。"""
        for name, value in (("category", category), ("label", label), ("query", query)):
            if value is not None and not isinstance(value, str):
                raise ValueError(name + " 必须是字符串或 None")
        if type(important_only) is not bool:
            raise ValueError("important_only 必须是布尔值")
        has_reviews = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_current'"
        ).fetchone() is not None
        source = "messages AS message"
        review_json = "NULL"
        if has_reviews:
            source += " LEFT JOIN review_current AS review ON review.kind='message' AND review.entity_id=message.message_id"
            review_json = "review.payload_json"
        clauses, parameters = [], []
        if category is not None:
            clauses.append(
                "COALESCE(json_extract(" + review_json + ", '$.category'),"
                "json_extract(message.analysis_json, '$.category'),message.category) = ?"
            )
            parameters.append(category)
        if important_only:
            clauses.append(
                """EXISTS (SELECT 1 FROM role_rules AS role
                   WHERE role.source=message.source AND role.account_id=message.account_id
                     AND role.sender_id=message.sender_id AND role.important=1)"""
            )
        if label is not None:
            # 人工显式清空 [] 必须生效，不能被 COALESCE 误退回旧模型标签。
            labels = (
                "CASE WHEN json_type(" + review_json + ",'$.labels')='array'"
                " THEN json_extract(" + review_json + ",'$.labels')"
                " WHEN json_type(message.analysis_json,'$.labels')='array'"
                " THEN json_extract(message.analysis_json,'$.labels') ELSE '[]' END"
            )
            clauses.append("EXISTS (SELECT 1 FROM json_each(" + labels + ") AS labels WHERE labels.value=?)")
            parameters.append(label)
        if query is not None and query.strip():
            # instr 按字面包含检索，用户输入的 %、_ 和 SQL 片段不成为通配符或代码。
            expressions = [
                "COALESCE(json_extract(message.envelope_json,'$.text'),'')",
                "COALESCE(json_extract(" + review_json + ",'$.summary'),json_extract(message.analysis_json,'$.summary'),'')",
                "COALESCE(json_extract(message.envelope_json,'$.sender_name'),'')",
                "message.sender_id",
            ]
            matches = ["instr(lower(" + expression + "),lower(?))>0" for expression in expressions]
            clauses.append("(" + " OR ".join(matches) + ")")
            parameters.extend([query.strip()] * len(expressions))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return source, where, parameters

    def list_reviewed_messages(
        self, category: str | None = None, important_only: bool = False,
        label: str | None = None, limit: int = 50, offset: int = 0,
        query: str | None = None,
    ) -> list[dict]:
        """先按人工类别、标签与原文搜索筛选全部消息，再稳定分页读取原始详情。"""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit 必须是正整数")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset 必须是非负整数")
        with self._connection() as connection:
            source, where, parameters = self._reviewed_query_parts(connection, category, important_only, label, query)
            rows = connection.execute(
                "SELECT message.message_id FROM " + source + where + " ORDER BY message.rowid DESC LIMIT ? OFFSET ?",
                (*parameters, limit, offset),
            ).fetchall()
            # 详情与分页编号共享同一读取快照；人工展示覆盖由既有 ReviewStore 统一组装。
            return [self._detail(connection, row["message_id"]) for row in rows]

    def count_reviewed_messages(
        self, category: str | None = None, important_only: bool = False,
        label: str | None = None, query: str | None = None,
    ) -> int:
        """使用与分页完全一致的修订后筛选条件统计总数，不受最近100条限制。"""
        with self._connection() as connection:
            source, where, parameters = self._reviewed_query_parts(connection, category, important_only, label, query)
            row = connection.execute("SELECT COUNT(*) FROM " + source + where, parameters).fetchone()
            return row[0]

    def upsert_role(self, rule: RoleRule) -> dict:
        """按来源、账号和发送人精确新增或更新用户规则，昵称不参与匹配。"""
        payload = RoleRule.model_validate(rule).model_dump(mode="json")
        key = (payload["source"], payload["account_id"], payload["sender_id"])
        with self._connection(write=True) as connection:
            connection.execute(
                """
                INSERT INTO role_rules (source, account_id, sender_id, role, important)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, account_id, sender_id)
                DO UPDATE SET role = excluded.role, important = excluded.important
                """,
                (*key, payload["role"], int(payload["important"])),
            )
            row = connection.execute(
                "SELECT * FROM role_rules WHERE source = ? AND account_id = ? AND sender_id = ?", key
            ).fetchone()
            return self._role_dict(row)

    def list_roles(self) -> list[dict]:
        """返回当前用户配置的全部身份规则，保留精确匹配键和重要程度。"""
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM role_rules ORDER BY source, account_id, sender_id").fetchall()
            return [self._role_dict(row) for row in rows]

    def mark_failed(self, message_id: str, error_code: str) -> None:
        """只标记未完成消息的安全错误码，迟到的失败不能覆盖已经成功的结果。"""
        if not isinstance(error_code, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", error_code) is None:
            raise ValueError("error_code 必须是安全错误标识，不能包含提供商原始错误内容")
        with self._connection(write=True) as connection:
            connection.execute(
                "UPDATE messages SET processing_status = 'failed', error_code = ? WHERE message_id = ? AND processing_status != 'completed'",
                (error_code, message_id),
            )

    def complete_analysis(self, message_id: str, analysis: dict, events: list[dict]) -> dict:
        """以单一事务提交分析、待办和关联；任何失败均回滚，完成后的重试只返回原结果。"""
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT processing_status FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if row is None:
                raise ValueError("消息不存在，不能保存分析结果")
            if row["processing_status"] == "completed":
                return self._detail(connection, message_id)
            if not isinstance(analysis, dict) or not isinstance(events, list):
                raise ValueError("分析必须是对象，事项必须是列表")
            analysis_json = self._json(analysis)
            for index, event in enumerate(events):
                if not isinstance(event, dict):
                    raise ValueError("每项事项必须是对象")
                fingerprint = hashlib.sha256(f"{message_id}:{index}".encode("utf-8")).hexdigest()
                task = StoredTask(
                    id=fingerprint[:16], title=event["title"],
                    time_text=event.get("time_text"), location=event.get("location"),
                )
                time_value = event.get("time")
                if time_value is not None and not isinstance(time_value, dict):
                    raise ValueError("事项时间必须是对象或 None")
                # 传入本事务的 connection，不能调用会独立建连接并提交的 save_task。
                self.task_store._insert_task(connection, task)
                connection.execute(
                    """
                    INSERT INTO event_links (
                        event_id, message_id, event_index, task_id, evidence_json, time_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (fingerprint[:24], message_id, index, task.id, self._json(event.get("evidence")),
                     self._json(time_value) if time_value is not None else None),
                )
            connection.execute(
                """
                UPDATE messages SET analysis_json = ?, category = ?, processing_status = 'completed', error_code = NULL
                WHERE message_id = ?
                """,
                (analysis_json, analysis.get("category"), message_id),
            )
            return self._detail(connection, message_id)
