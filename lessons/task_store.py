"""第六课的待办存储模块：用 SQL 操作 SQLite，供命令行和后续 Agent 工具复用。"""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "local-data" / "lesson06_tasks.sqlite3"
TaskStatus = Literal["pending", "completed"]


class TaskInput(BaseModel):
    """校验写入数据库前的待办内容；复用前几课的原文时间与地点字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=120, pattern=r"\S")
    time_text: str | None = None
    location: str | None = None


class StoredTask(TaskInput):
    """描述数据库的一行记录，并校验从旧 JSON 导入的 ID 和完成状态。"""

    id: str = Field(min_length=1, pattern=r"\S")
    status: TaskStatus = "pending"


class TaskStore:
    """集中处理待办的建表、保存、查询、完成和导入，不负责调用模型或发送提醒。"""

    def __init__(self, db_path: Path | str = DEFAULT_DB):
        """记录数据库文件位置，创建父目录，并初始化不存在的待办表。"""
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 外层 closing 负责关闭连接，内层 connection 负责提交或回滚事务。
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY NOT NULL,
                    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),
                    time_text TEXT,
                    location TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'completed'))
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        """为一次操作建立独立连接；开启显式提交/回滚模式，并允许按列名读取结果。"""
        # 当前项目使用 Python 3.13；autocommit=False 使每次操作处于事务中。
        connection = sqlite3.connect(self.db_path, autocommit=False, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _insert_task(self, connection: sqlite3.Connection, task: StoredTask) -> dict:
        """在调用方的事务中插入一项；相同 ID 复用记录，内容冲突则抛错以回滚。"""
        # SQL 结构与数据分开传入；标题即使带引号或 SQL 字样，也只是普通文本。
        cursor = connection.execute(
            """
            INSERT INTO tasks (id, title, time_text, location, status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (task.id, task.title, task.time_text, task.location, task.status),
        )
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task.id,)).fetchone()
        saved = dict(row)
        # 旧 JSON 不能用相同 ID 覆盖另一件事，也不能把已完成事项重新改成 pending。
        if any(saved[field] != getattr(task, field) for field in ("title", "time_text", "location")):
            raise ValueError("待办 ID 对应的内容冲突，整批写入已回滚")
        return {"status": "created" if cursor.rowcount == 1 else "exists", "task": saved}

    def save_task(self, title: str, time_text: str | None = None, location: str | None = None) -> dict:
        """校验并持久化一项待办；返回 created 或 exists 及实际记录，重复保存保留完成状态。"""
        data = TaskInput(title=title, time_text=time_text, location=location).model_dump()
        # 沿用第三课的内容指纹；只判断字段完全相同，不代表真实消息的来源去重。
        fingerprint = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        task_id = hashlib.sha256(fingerprint).hexdigest()[:16]
        task = StoredTask(id=task_id, **data)
        with closing(self._connect()) as connection, connection:
            return self._insert_task(connection, task)

    def list_tasks(self, status: TaskStatus | None = None) -> list[dict]:
        """按插入顺序查询待办；status 为 None 时查全部，否则仅查指定完成状态。"""
        if status not in (None, "pending", "completed"):
            raise ValueError("status 只允许 pending、completed 或 None")
        with closing(self._connect()) as connection, connection:
            if status is None:
                rows = connection.execute("SELECT * FROM tasks ORDER BY rowid").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY rowid", (status,)
                ).fetchall()
            return [dict(row) for row in rows]

    def count_pending(self) -> int:
        """由数据库统计未完成待办数量，返回整数，避免把完整记录列表读回 Python。"""
        with closing(self._connect()) as connection, connection:
            # 参数绑定把 pending 作为普通状态值传入，SQL 负责筛选并计数。
            row = connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = ?", ("pending",)
            ).fetchone()
            # COUNT(*) 返回一行；第 0 列才是可用于显示和数值判断的计数整数。
            return row[0]

    def mark_completed(self, task_id: str) -> dict | None:
        """只将指定 ID 的待办标记为 completed 并返回记录；ID 不存在时返回 None。"""
        with closing(self._connect()) as connection, connection:
            # WHERE 决定更新哪一行；省略它会把所有待办都标成完成。
            connection.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task_id,))
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row is not None else None

    def import_json(self, source: Path | str) -> dict[str, int]:
        """只读旧 JSON 并整批导入；保留 ID 和已有状态，任何校验或冲突失败均不留下部分写入。"""
        records = json.loads(Path(source).read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("旧 JSON 必须是待办记录列表")
        # 先校验所有输入，避免坏记录通过；数据库操作再使用同一事务保证整批提交。
        tasks = [StoredTask.model_validate(record) for record in records]
        counts = {"created": 0, "exists": 0}
        with closing(self._connect()) as connection, connection:
            for task in tasks:
                result = self._insert_task(connection, task)
                counts[result["status"]] += 1
        return counts
