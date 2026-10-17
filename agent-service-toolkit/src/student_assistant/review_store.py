"""版本化人工纠正与文件分类存储：原文、来源指纹和初始分析始终保留。"""

import copy
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from student_assistant.bridge import parse_reference_time
from student_assistant.review_models import DocumentClassification, DocumentReview, EventReview, MessageReview


class ReviewConflictError(ValueError):
    """表示客户端基于旧修订号提交，调用方应重新读取后再编辑。"""

    def __init__(self, entity_id: str):
        """保留发生冲突的记录编号，避免异常消息携带业务原文。"""
        self.entity_id = entity_id
        super().__init__("修订版本已变化，请刷新后再修改")


class ReviewStore:
    """复用业务库保存人工修订及历史；更新事项时事务内取消旧未投递提醒。"""

    def __init__(self, task_store):
        """创建独立附加表，不修改原消息结构、来源去重键或旧 tasks 状态约束。"""
        self.db_path = Path(task_store.db_path).resolve()
        with self._connection(write=True) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS review_current (
                    kind TEXT NOT NULL CHECK(kind IN ('message','event','document')),
                    entity_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version >= 1),
                    payload_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(kind, entity_id))"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS review_history (
                    kind TEXT NOT NULL, entity_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 0),
                    payload_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(kind, entity_id, version))"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS document_classifications (
                    document_id TEXT PRIMARY KEY NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('completed','failed')),
                    payload_json TEXT, error_code TEXT, updated_at TEXT NOT NULL)"""
            )

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """在独立短事务中执行读写，写锁覆盖版本检查、状态修改与历史落盘。"""
        with closing(sqlite3.connect(self.db_path, timeout=5, autocommit=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
            with connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection

    @staticmethod
    def _json(value) -> str:
        """生成严格 JSON，用于保存修订快照，拒绝 NaN 等非标准值。"""
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)

    @staticmethod
    def _has_table(connection, name: str) -> bool:
        """检查可选业务表是否已经初始化，避免尚未启用模块时查询不存在的表。"""
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
        ).fetchone() is not None

    @staticmethod
    def _current(connection, kind: str, entity_id: str) -> dict | None:
        """读取当前修订号与累计补丁，不把存储层 JSON 字符串暴露给界面。"""
        row = connection.execute(
            "SELECT * FROM review_current WHERE kind=? AND entity_id=?", (kind, entity_id),
        ).fetchone()
        if row is None:
            return None
        return {"version": row["version"], "updated_at": row["updated_at"], "changes": json.loads(row["payload_json"])}

    def get_review(self, kind: str, entity_id: str) -> dict:
        """返回当前累计修订；尚无人工编辑时版本号为零。"""
        if kind not in ("message", "event", "document"):
            raise ValueError("修订类型不支持")
        with self._connection() as connection:
            return self._current(connection, kind, entity_id) or {"version": 0, "changes": {}, "updated_at": None}

    def _save(self, connection, kind: str, entity_id: str, review, baseline: dict) -> dict:
        """校验期待版本并原子保存累计补丁及历史，首次编辑另保存零版原始快照。"""
        previous = self._current(connection, kind, entity_id)
        version = previous["version"] if previous else 0
        if review.expected_version != version:
            raise ReviewConflictError(entity_id)
        changes = {**(previous["changes"] if previous else {}), **review.model_dump(exclude_unset=True, exclude={"expected_version"})}
        stamp = datetime.now(timezone.utc).isoformat()
        if previous is None:
            connection.execute(
                "INSERT INTO review_history VALUES (?, ?, 0, ?, ?)",
                (kind, entity_id, self._json(baseline), stamp),
            )
        connection.execute(
            "INSERT INTO review_history VALUES (?, ?, ?, ?, ?)",
            (kind, entity_id, version + 1, self._json(changes), stamp),
        )
        connection.execute(
            """INSERT INTO review_current VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(kind, entity_id) DO UPDATE SET
               version=excluded.version,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
            (kind, entity_id, version + 1, self._json(changes), stamp),
        )
        return {"version": version + 1, "changes": changes, "updated_at": stamp}

    def apply_message(self, message_id: str, review: MessageReview | dict) -> dict:
        """保存消息类别、标签及摘要修订，原 envelope 和 analysis JSON 保持原样。"""
        review = MessageReview.model_validate(review)
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT analysis_json FROM messages WHERE message_id=?", (message_id,)).fetchone()
            if row is None:
                raise KeyError(message_id)
            baseline = json.loads(row["analysis_json"]) if row["analysis_json"] else {}
            return self._save(connection, "message", message_id, review, baseline)

    def _event(self, connection, task_id: str) -> dict:
        """读取当前任务、原消息时钟和时间关联，独立覆盖取消状态不修改旧表约束。"""
        row = connection.execute(
            """SELECT tasks.*, links.time_json, links.evidence_json, messages.envelope_json
               FROM tasks JOIN event_links AS links ON links.task_id=tasks.id
               JOIN messages ON messages.message_id=links.message_id WHERE tasks.id=?""", (task_id,),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        event = {"task_id": task_id, **{key: row[key] for key in ("title", "time_text", "location", "status")},
                 "time": json.loads(row["time_json"]) if row["time_json"] else None,
                 "evidence": json.loads(row["evidence_json"])}
        previous = self._current(connection, "event", task_id)
        if previous and previous["changes"].get("status") == "cancelled" and event["status"] == "completed":
            event["status"] = "cancelled"
        event["reference_time"] = json.loads(row["envelope_json"])["sent_at"]
        return event

    def _cancel_old_reminders(self, connection, task_id: str) -> None:
        """在事项修订事务内使旧未投递计划失效，并保留 submitted 投递审计记录。"""
        if not self._has_table(connection, "reminder_jobs"):
            return
        connection.execute(
            """UPDATE reminder_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,
               error_code='EventReviewed' WHERE reminder_id IN
               (SELECT reminder_id FROM reminder_schedules WHERE task_id=?)
               AND status NOT IN ('submitted','cancelled')""", (task_id,),
        )

    def apply_event(self, task_id: str, review: EventReview | dict) -> dict:
        """原子修正既有事项、保留零版和修订历史，所有旧未投递提醒同时取消。"""
        review = EventReview.model_validate(review)
        patch = review.model_dump(exclude_unset=True, exclude={"expected_version"})
        with self._connection(write=True) as connection:
            baseline = self._event(connection, task_id)
            merged = {**baseline, **patch}
            time_value = merged["time"]
            if time_value is not None:
                if time_value["time_text"] != merged["time_text"]:
                    raise ValueError("时间结果必须与事项时间原话一致；清空时间请同时清空结果")
                if parse_reference_time(time_value["reference_time"]) != parse_reference_time(baseline["reference_time"]):
                    raise ValueError("时间参考必须沿用原消息发送时间")
            # 先完成全部校验，再写入历史和业务状态；任何异常将回滚整个事务。
            saved = self._save(connection, "event", task_id, review, baseline)
            actual_status = "completed" if merged["status"] == "cancelled" else merged["status"]
            connection.execute(
                "UPDATE tasks SET title=?,time_text=?,location=?,status=? WHERE id=?",
                (merged["title"], merged["time_text"], merged["location"], actual_status, task_id),
            )
            connection.execute("UPDATE event_links SET time_json=? WHERE task_id=?", (
                self._json(time_value) if time_value is not None else None, task_id,
            ))
            self._cancel_old_reminders(connection, task_id)
            result = {key: value for key, value in merged.items() if key != "reference_time"}
            return {**result, "review": saved}

    def apply_document(self, document_id: str, review: DocumentReview | dict) -> dict:
        """人工纠正文档展示分类；保留自动正文分类与逐字证据作为独立记录。"""
        review = DocumentReview.model_validate(review)
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT filename FROM rag_documents WHERE document_id=?", (document_id,)).fetchone()
            if row is None:
                raise KeyError(document_id)
            auto = connection.execute(
                "SELECT payload_json FROM document_classifications WHERE document_id=?", (document_id,),
            ).fetchone()
            baseline = json.loads(auto["payload_json"]) if auto and auto["payload_json"] else {"filename": row["filename"]}
            return self._save(connection, "document", document_id, review, baseline)

    def get_history(self, kind: str, entity_id: str) -> list[dict]:
        """按版本返回最初快照和后续人工累计补丁，支持审查纠错来源。"""
        if kind not in ("message", "event", "document"):
            raise ValueError("修订类型不支持")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM review_history WHERE kind=? AND entity_id=? ORDER BY version", (kind, entity_id),
            ).fetchall()
            return [{"version": row["version"], "changes": json.loads(row["payload_json"]), "updated_at": row["updated_at"]} for row in rows]

    def classification_pages(self, document_id: str) -> list[dict]:
        """读取索引实际提取并持久化的页面正文，不根据文件名猜测分类。"""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT status,pages_json FROM rag_documents WHERE document_id=?", (document_id,),
            ).fetchone()
            if row is None:
                raise KeyError(document_id)
            if row["status"] != "indexed":
                raise ValueError("文档必须先完成正文提取与索引")
            pages = json.loads(row["pages_json"])
            if not pages:
                raise ValueError("文档没有可分类正文")
            return pages

    def save_classification(self, document_id: str, result: DocumentClassification | dict | None, *, error_code: str | None = None, coverage: dict | None = None) -> dict:
        """保存验证过的正文分类或安全错误标识，失败状态可由显式动作重新执行。"""
        payload = None if result is None else DocumentClassification.model_validate(result).model_dump(mode="json")
        if error_code is not None and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", error_code) is None:
            raise ValueError("错误类型必须是安全标识")
        if (payload is None) == (error_code is None):
            raise ValueError("必须且只能保存分类结果或失败标识")
        if payload is not None:
            pages = {page["page"]: page["text"] for page in self.classification_pages(document_id)}
            for item in payload["evidence"]:
                if item["page"] not in pages or item["quote"] not in pages[item["page"]]:
                    raise ValueError("分类引用必须能在真实正文中逐字复现")
        if payload is not None and coverage is not None:
            # 覆盖范围只能由程序统计，不能接受模型自行声明已读完全部文件。
            payload["coverage"] = dict(coverage)
        with self._connection(write=True) as connection:
            if connection.execute("SELECT 1 FROM rag_documents WHERE document_id=?", (document_id,)).fetchone() is None:
                raise KeyError(document_id)
            connection.execute(
                """INSERT INTO document_classifications VALUES (?,?,?,?,?)
                   ON CONFLICT(document_id) DO UPDATE SET status=excluded.status,
                   payload_json=excluded.payload_json,error_code=excluded.error_code,updated_at=excluded.updated_at""",
                (document_id, "completed" if payload else "failed", self._json(payload) if payload else None,
                 error_code, datetime.now(timezone.utc).isoformat()),
            )
        return self.get_classification(document_id)

    def get_classification(self, document_id: str) -> dict:
        """读取文件正文分类状态，不调用模型，不回传异常原文或服务凭证。"""
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM document_classifications WHERE document_id=?", (document_id,)).fetchone()
            if row is None:
                return {"status": "unclassified", "result": None, "error_code": None, "updated_at": None}
            return {"status": row["status"], "result": json.loads(row["payload_json"]) if row["payload_json"] else None,
                    "error_code": row["error_code"], "updated_at": row["updated_at"]}

    def task_overlay(self, task: dict) -> dict:
        """为任务补充人工取消状态和修订号；外部完成操作仍以实时数据库状态为准。"""
        result = copy.deepcopy(task)
        key = result.get("task_id", result.get("id"))
        review = self.get_review("event", key)
        if review["changes"].get("status") == "cancelled" and result.get("status") == "completed":
            result["status"] = "cancelled"
        result["review"] = review
        return result

    def document_overlay(self, detail: dict | None) -> dict | None:
        """合并自动正文分类和人工修订，人工类别优先，原分类证据保留供核对。"""
        if detail is None:
            return None
        result = copy.deepcopy(detail)
        document_id = result["document_id"]
        classification = self.get_classification(document_id)
        review = self.get_review("document", document_id)
        result.update({"category": None, "labels": [], "summary": None, "classification_basis": None})
        if classification["status"] == "completed":
            result.update(classification["result"])
            result["classification_basis"] = "document_text"
        if review["version"]:
            result.update(review["changes"])
            result["classification_basis"] = "manual_review"
        result["classification"] = classification
        result["review"] = review
        return result

    def detail_overlay(self, detail: dict | None) -> dict | None:
        """组装消息人工类别、多个标签、事项取消状态与正文文件分类，不修改传入原对象。"""
        if detail is None:
            return None
        result = copy.deepcopy(detail)
        review = self.get_review("message", result["message_id"])
        original = result.get("analysis")
        result["original_analysis"] = copy.deepcopy(original)
        if original is None:
            result["analysis"] = {} if review["version"] else None
        if result["analysis"] is not None:
            result["analysis"].setdefault("labels", [])
            result["analysis"].update(review["changes"])
        result["labels"] = (result.get("analysis") or {}).get("labels", [])
        result["review"] = review
        result["events"] = [self.task_overlay(event) for event in result.get("events", [])]
        for attachment in result.get("attachments", []):
            attachment.setdefault("labels", [])
            if attachment.get("document_id"):
                classified = self.document_overlay({"document_id": attachment["document_id"]})
                if classified["classification_basis"] is not None:
                    for key in ("category", "labels", "summary", "classification_basis", "classification", "review"):
                        attachment[key] = classified[key]
        return result
