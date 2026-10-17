"""RAG 持久化：SQLite 保存原文溯源，Qdrant 保存真实向量，用索引状态隔离半完成写入。"""

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from qdrant_client import QdrantClient, models

from student_assistant.bridge import TaskStore
from student_assistant.rag_documents import validate_file, validate_pages


COLLECTION_NAME = "student_documents"


class RagStore:
    """管理真实原件、附件绑定和磁盘向量索引；单进程内串行访问本地 Qdrant。"""

    def __init__(self, task_store: TaskStore, root_dir: Path, model_id: str, dimension: int):
        """复用业务库并打开本地向量目录，校验已有模型配置，不创建或下载 Embedding。"""
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id 不能为空")
        if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension < 1:
            raise ValueError("向量维度必须是正整数")
        self.task_store = task_store
        self.db_path = Path(task_store.db_path).resolve()
        self.root_dir = Path(root_dir).resolve()
        self.files_dir = self.root_dir / "files"
        self.model_id = model_id
        self.dimension = dimension
        self.collection_name = COLLECTION_NAME
        self._lock = threading.RLock()
        self._closed = False
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_database()
        self.client = QdrantClient(
            path=str(self.root_dir / "vectors"), force_disable_check_same_thread=True,
        )
        try:
            if not self.client.collection_exists(self.collection_name):
                self.client.create_collection(
                    self.collection_name,
                    vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
                )
            existing = self.client.get_collection(self.collection_name).config.params.vectors
            if not isinstance(existing, models.VectorParams) or existing.size != dimension or existing.distance != models.Distance.COSINE:
                raise ValueError("已有向量集合的维度或距离配置不一致，不能混用索引")
        except Exception:
            self.client.close()
            raise

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """为一次短事务建立并关闭连接，先启用外键，写事务先获取 SQLite 写锁。"""
        with closing(sqlite3.connect(self.db_path, timeout=5, autocommit=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
            with connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection

    def _initialize_database(self) -> None:
        """追加 RAG 表和配置；模型、前缀版本、维度或磁盘目录不一致时拒绝复用。"""
        statements = [
            """CREATE TABLE IF NOT EXISTS rag_config (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                model_id TEXT NOT NULL, dimension INTEGER NOT NULL, root_dir TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS rag_documents (
                document_id TEXT PRIMARY KEY NOT NULL, filename TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE, suffix TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('received', 'failed', 'indexed')),
                error_code TEXT, page_count INTEGER NOT NULL DEFAULT 0,
                chunk_count INTEGER NOT NULL DEFAULT 0, model_id TEXT NOT NULL,
                pages_json TEXT NOT NULL DEFAULT '[]'
            )""",
            """CREATE TABLE IF NOT EXISTS rag_chunks (
                chunk_id TEXT PRIMARY KEY NOT NULL, document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL, page INTEGER NOT NULL,
                start INTEGER NOT NULL, end INTEGER NOT NULL, text TEXT NOT NULL,
                UNIQUE(document_id, chunk_index),
                FOREIGN KEY(document_id) REFERENCES rag_documents(document_id)
            )""",
            """CREATE TABLE IF NOT EXISTS rag_bindings (
                message_id TEXT NOT NULL, attachment_index INTEGER NOT NULL,
                document_id TEXT NOT NULL, PRIMARY KEY(message_id, attachment_index),
                FOREIGN KEY(document_id) REFERENCES rag_documents(document_id),
                FOREIGN KEY(message_id, attachment_index) REFERENCES attachments(message_id, attachment_index)
            )""",
            "CREATE INDEX IF NOT EXISTS rag_chunks_document ON rag_chunks(document_id)",
            "CREATE INDEX IF NOT EXISTS rag_bindings_document ON rag_bindings(document_id)",
        ]
        with self._connection(write=True) as connection:
            for statement in statements:
                connection.execute(statement)
            row = connection.execute("SELECT * FROM rag_config WHERE singleton = 1").fetchone()
            if row is not None and (row["model_id"], row["dimension"], row["root_dir"]) != (
                self.model_id, self.dimension, str(self.root_dir),
            ):
                raise ValueError("已有 RAG 配置不同；更换模型或前缀版本须重建独立索引")
            connection.execute(
                "INSERT INTO rag_config VALUES (1, ?, ?, ?) ON CONFLICT(singleton) DO NOTHING",
                (self.model_id, self.dimension, str(self.root_dir)),
            )

    @staticmethod
    def _safe_error(error_code: str) -> str:
        """仅接受安全的异常类型标识，不保存模型提供商原文或凭证。"""
        if not isinstance(error_code, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", error_code) is None:
            raise ValueError("error_code 必须是安全的错误标识")
        return error_code

    def _sources(self, connection: sqlite3.Connection, document_id: str) -> list[dict]:
        """按附件绑定读取真实消息身份和发送时间，未绑定原件返回空来源列表。"""
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'").fetchone() is None:
            return []
        rows = connection.execute(
            """SELECT bindings.message_id, bindings.attachment_index, messages.envelope_json
               FROM rag_bindings AS bindings JOIN messages ON messages.message_id = bindings.message_id
               WHERE bindings.document_id = ? ORDER BY bindings.rowid""", (document_id,),
        ).fetchall()
        sources = []
        for row in rows:
            envelope = json.loads(row["envelope_json"])
            sources.append({
                "message_id": row["message_id"], "attachment_index": row["attachment_index"],
                **{key: envelope.get(key) for key in (
                    "source", "account_id", "source_message_id", "sender_id", "sender_name", "sent_at",
                )},
            })
        return sources

    def _detail(self, connection: sqlite3.Connection, document_id: str) -> dict | None:
        """在同一 SQL 快照组装文档状态与消息来源，不向调用者暴露内部文件路径。"""
        row = connection.execute("SELECT * FROM rag_documents WHERE document_id = ?", (document_id,)).fetchone()
        if row is None:
            return None
        return {
            **{key: row[key] for key in (
                "document_id", "filename", "sha256", "status", "error_code",
                "page_count", "chunk_count", "model_id",
            )},
            "sources": self._sources(connection, document_id),
        }

    def _validate_binding(self, connection: sqlite3.Connection, message_id, attachment_index, document_id: str) -> None:
        """先确认消息附件存在且未绑定其他文档，避免非法绑定留下文件或文档孤儿。"""
        if (message_id is None) != (attachment_index is None):
            raise ValueError("message_id 与 attachment_index 必须同时提供")
        if message_id is None:
            return
        if not isinstance(message_id, str) or not message_id or not isinstance(attachment_index, int) or isinstance(attachment_index, bool) or attachment_index < 0:
            raise ValueError("消息编号和附件索引不合法")
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attachments'").fetchone()
        if exists is None or connection.execute(
            "SELECT 1 FROM attachments WHERE message_id = ? AND attachment_index = ?", (message_id, attachment_index),
        ).fetchone() is None:
            raise ValueError("指定消息或附件索引不存在")
        previous = connection.execute(
            "SELECT document_id FROM rag_bindings WHERE message_id = ? AND attachment_index = ?",
            (message_id, attachment_index),
        ).fetchone()
        if previous is not None and previous["document_id"] != document_id:
            raise ValueError("该消息附件已绑定另一份原件，不能覆盖")

    def receive(self, filename: str, data: bytes, *, message_id=None, attachment_index=None) -> dict:
        """按原件 SHA256 幂等保存真实文件和元数据，可同时精确绑定已有消息中的一个附件。"""
        suffix = validate_file(filename, data)
        document_id = hashlib.sha256(data).hexdigest()
        created_path = None
        temporary_path = None
        with self._lock:
            try:
                with self._connection(write=True) as connection:
                    self._validate_binding(connection, message_id, attachment_index, document_id)
                    previous = connection.execute(
                        "SELECT suffix FROM rag_documents WHERE document_id = ?", (document_id,),
                    ).fetchone()
                    target = self.files_dir / (document_id + (previous["suffix"] if previous else suffix))
                    if target.exists():
                        if target.read_bytes() != data:
                            raise OSError("原件存储内容与指纹不一致")
                    else:
                        temporary_path = self.files_dir / (uuid4().hex + ".tmp")
                        temporary_path.write_bytes(data)
                        os.replace(temporary_path, target)
                        created_path = target
                    connection.execute(
                        """INSERT INTO rag_documents (document_id, filename, sha256, suffix, status, model_id)
                           VALUES (?, ?, ?, ?, 'received', ?) ON CONFLICT(document_id) DO NOTHING""",
                        (document_id, filename, document_id, suffix, self.model_id),
                    )
                    if message_id is not None:
                        connection.execute(
                            """INSERT INTO rag_bindings VALUES (?, ?, ?)
                               ON CONFLICT(message_id, attachment_index) DO NOTHING""",
                            (message_id, attachment_index, document_id),
                        )
                    detail = self._detail(connection, document_id)
                return {"status": "exists" if previous else "received", "document": detail}
            except Exception:
                if created_path is not None:
                    created_path.unlink(missing_ok=True)
                raise
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def get_document(self, document_id: str) -> dict | None:
        """查询已接收原件及实际索引状态；不存在时返回 None。"""
        with self._lock, self._connection() as connection:
            return self._detail(connection, document_id)

    def list_documents(self) -> list[dict]:
        """按接收顺序列出文档状态和来源，重复上传的同一原件只出现一次。"""
        with self._lock, self._connection() as connection:
            rows = connection.execute("SELECT document_id FROM rag_documents ORDER BY rowid").fetchall()
            return [self._detail(connection, row["document_id"]) for row in rows]

    def read_document(self, document_id: str) -> bytes:
        """读取实际保存的原件并核对内容指纹，供失败重试复用，不重新索要文件。"""
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT suffix, sha256 FROM rag_documents WHERE document_id = ?", (document_id,),
            ).fetchone()
            if row is None:
                raise ValueError("文档不存在")
            data = (self.files_dir / (row["sha256"] + row["suffix"])).read_bytes()
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise OSError("原件内容未通过指纹校验")
            return data

    def _document_filter(self, document_id: str) -> models.Filter:
        """构造精确文档过滤条件，供失败清理使用，避免影响其他文档的向量。"""
        return models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))])

    def _discard_index(self, document_id: str, error_code: str) -> None:
        """先令 SQL 状态不可检索并删除片段，再清除对应向量；原件和消息绑定保持可恢复。"""
        with self._connection(write=True) as connection:
            connection.execute("DELETE FROM rag_chunks WHERE document_id = ?", (document_id,))
            connection.execute(
                """UPDATE rag_documents SET status='failed', error_code=?, page_count=0,
                   chunk_count=0, pages_json='[]' WHERE document_id=?""", (error_code, document_id),
            )
        self.client.delete(
            self.collection_name,
            points_selector=models.FilterSelector(filter=self._document_filter(document_id)), wait=True,
        )

    def mark_failed(self, document_id: str, error_code: str) -> None:
        """标记尚未完成的原件失败并清理残余索引；迟到的失败不会覆盖已完成索引。"""
        error_code = self._safe_error(error_code)
        with self._lock:
            detail = self.get_document(document_id)
            if detail is None:
                raise ValueError("文档不存在")
            if detail["status"] != "indexed":
                self._discard_index(document_id, error_code)

    def _vector(self, vector) -> list[float]:
        """校验真实向量的维度和有限非零数值，返回 Qdrant 可接受的浮点列表。"""
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError, OverflowError):
            raise ValueError("向量必须是有限浮点数序列") from None
        if len(values) != self.dimension or not all(math.isfinite(value) for value in values):
            raise ValueError("向量维度不符或含非有限数值")
        if not any(value != 0 for value in values):
            raise ValueError("余弦检索不接受零向量")
        return values

    def _index_records(self, document_id: str, pages: list[dict], chunks: list[dict], vectors) -> tuple[list, list]:
        """先整批校验页内偏移和向量，生成稳定 UUID 片段 ID 与实际向量点。"""
        validate_pages(pages)
        if not isinstance(chunks, list) or not chunks:
            raise ValueError("文档必须至少产生一个非空片段")
        vectors = list(vectors)
        if len(vectors) != len(chunks):
            raise ValueError("片段数与向量数不一致")
        page_texts = {page["page"]: page["text"] for page in pages}
        records, points = [], []
        for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
            if not isinstance(chunk, dict):
                raise ValueError("片段必须是对象")
            page, start, end = (chunk.get(key) for key in ("page", "start", "end"))
            if any(not isinstance(value, int) or isinstance(value, bool) for value in (page, start, end)):
                raise ValueError("片段页码和偏移必须是整数")
            text = chunk.get("text")
            if page not in page_texts or not 0 <= start < end <= len(page_texts[page]):
                raise ValueError("片段超出对应页面范围")
            if not isinstance(text, str) or not text.strip() or text != page_texts[page][start:end] or chunk.get("chunk_index") != index:
                raise ValueError("片段文本或序号无法按页面原文复现")
            chunk_id = str(uuid5(NAMESPACE_URL, f"student-assistant:{document_id}:{index}"))
            records.append((chunk_id, document_id, index, page, start, end, text))
            points.append(models.PointStruct(
                id=chunk_id, vector=self._vector(vector), payload={"document_id": document_id},
            ))
        return records, points

    def complete_index(self, document_id: str, pages: list[dict], chunks: list[dict], vectors) -> dict:
        """先写真实向量再提交 SQL 片段与 indexed 状态；失败清理整份索引，重试沿用原件。"""
        with self._lock:
            detail = self.get_document(document_id)
            if detail is None:
                raise ValueError("文档不存在")
            if detail["status"] == "indexed":
                return detail
            try:
                records, points = self._index_records(document_id, pages, chunks, vectors)
                # 两种存储无法共享事务；未提交 indexed 前，search 的 SQL 门禁隐藏全部向量。
                self.client.delete(
                    self.collection_name,
                    points_selector=models.FilterSelector(filter=self._document_filter(document_id)), wait=True,
                )
                for start in range(0, len(points), 128):
                    self.client.upsert(self.collection_name, points=points[start:start + 128], wait=True)
                with self._connection(write=True) as connection:
                    connection.execute("DELETE FROM rag_chunks WHERE document_id = ?", (document_id,))
                    connection.executemany("INSERT INTO rag_chunks VALUES (?, ?, ?, ?, ?, ?, ?)", records)
                    connection.execute(
                        """UPDATE rag_documents SET status='indexed', error_code=NULL, page_count=?,
                           chunk_count=?, pages_json=? WHERE document_id=?""",
                        (len(pages), len(chunks), json.dumps(pages, ensure_ascii=False, allow_nan=False), document_id),
                    )
                    result = self._detail(connection, document_id)
                return result
            except Exception as error:
                # 清理再次失败时保留原异常；SQL 失败门禁先于向量删除，不暴露未完成片段。
                try:
                    self._discard_index(document_id, self._safe_error(type(error).__name__))
                except Exception:
                    pass
                raise

    def search(self, vector, top_k: int = 5, min_score: float = 0.35, document_id: str | None = None) -> list[dict]:
        """用余弦相似度查真实向量，只返回 SQL 中已完成索引且仍有原文片段的命中。"""
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 100:
            raise ValueError("top_k 必须是 1 到 100 的整数")
        if isinstance(min_score, bool) or not isinstance(min_score, (int, float)) or not math.isfinite(min_score) or not -1 <= min_score <= 1:
            raise ValueError("min_score 必须是 -1 到 1 的有限数值")
        query = self._vector(vector)
        with self._lock:
            with self._connection() as connection:
                rows = connection.execute("SELECT document_id FROM rag_documents WHERE status='indexed'").fetchall()
                allowed = [row["document_id"] for row in rows if document_id is None or row["document_id"] == document_id]
            if not allowed:
                return []
            result = self.client.query_points(
                self.collection_name, query=query,
                query_filter=models.Filter(must=[models.FieldCondition(
                    key="document_id", match=models.MatchAny(any=allowed),
                )]), limit=top_k, score_threshold=float(min_score), with_payload=False,
            )
            hits = []
            with self._connection() as connection:
                for point in result.points:
                    row = connection.execute(
                        """SELECT chunks.*, documents.filename FROM rag_chunks AS chunks
                           JOIN rag_documents AS documents ON documents.document_id = chunks.document_id
                           WHERE chunks.chunk_id=? AND documents.status='indexed'""", (str(point.id),),
                    ).fetchone()
                    if row is not None and row["document_id"] in allowed:
                        hits.append({
                            **{key: row[key] for key in (
                                "chunk_id", "document_id", "filename", "page", "start", "end", "text",
                            )},
                            "score": float(point.score), "sources": self._sources(connection, row["document_id"]),
                        })
            return hits

    def close(self) -> None:
        """释放磁盘 Qdrant 的目录锁，允许后续进程或新实例重新打开同一索引。"""
        with self._lock:
            if not self._closed:
                self.client.close()
                self._closed = True
