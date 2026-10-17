"""正式服务待办视图：用人工修订区分取消与完成，不迁移教学库的旧状态约束。"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Literal

from student_assistant.bridge import TaskStore


class TaskCancelledError(ValueError):
    """表示事项已由用户取消，不能通过普通完成操作悄悄改成已完成。"""

    def __init__(self, task_id: str):
        """保存稳定事项编号，错误说明不包含业务原文。"""
        self.task_id = task_id
        super().__init__("事项已取消，请先明确重新开启后再完成")


class BusinessTaskStore(TaskStore):
    """为正式Agent和HTTP查询提供有效状态，保留旧tasks表与教学代码行为。"""

    @contextmanager
    def _business_connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """使用短事务读取有效状态；完成操作在版本检查前获取写锁避免覆盖并发取消。"""
        with closing(sqlite3.connect(self.db_path, timeout=5, autocommit=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
            with connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection

    @staticmethod
    def _status_source(connection) -> tuple[str, str]:
        """生成固定SQL片段；旧库没有修订表时仍按教学库原状态正常读取。"""
        has_review = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_current'"
        ).fetchone() is not None
        if not has_review:
            return "tasks AS task", "task.status"
        source = "tasks AS task LEFT JOIN review_current AS review ON review.kind='event' AND review.entity_id=task.id"
        effective = "CASE WHEN task.status='completed' AND json_extract(review.payload_json,'$.status')='cancelled' THEN 'cancelled' ELSE task.status END"
        return source, effective

    def _task(self, connection, task_id: str) -> dict | None:
        """按ID读取五个既有业务字段，只将物理已结束且人工取消的事项映射为cancelled。"""
        source, effective = self._status_source(connection)
        row = connection.execute(
            "SELECT task.id,task.title,task.time_text,task.location," + effective + " AS status FROM " + source + " WHERE task.id=?",
            (task_id,),
        ).fetchone()
        return None if row is None else dict(row)

    def list_tasks(self, status: Literal["pending", "completed", "cancelled"] | None = None) -> list[dict]:
        """按有效状态查询正式待办，取消事项不混入已完成集合，返回字段与旧接口一致。"""
        if status not in (None, "pending", "completed", "cancelled"):
            raise ValueError("status 只允许 pending、completed、cancelled 或 None")
        with self._business_connection() as connection:
            source, effective = self._status_source(connection)
            where = " WHERE " + effective + "=?" if status is not None else ""
            rows = connection.execute(
                "SELECT task.id,task.title,task.time_text,task.location," + effective + " AS status FROM " + source + where + " ORDER BY task.rowid",
                (status,) if status is not None else (),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_completed(self, task_id: str) -> dict | None:
        """仅完成非取消事项；读取有效状态与更新同处写事务，重复完成仍幂等。"""
        with self._business_connection(write=True) as connection:
            current = self._task(connection, task_id)
            if current is None:
                return None
            if current["status"] == "cancelled":
                raise TaskCancelledError(task_id)
            # 更新仍触发既有停止未投递提醒的数据库触发器。
            connection.execute("UPDATE tasks SET status='completed' WHERE id=?", (task_id,))
            return self._task(connection, task_id)

    def save_task(self, title: str, time_text: str | None = None, location: str | None = None) -> dict:
        """沿用原内容指纹保存；若复用被取消的记录，如实返回cancelled而不重新开启。"""
        result = super().save_task(title, time_text, location)
        with self._business_connection() as connection:
            result["task"] = self._task(connection, result["task"]["id"])
        return result
