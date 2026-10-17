"""第六课离线测试：用临时 SQLite 文件验证持久化、参数绑定和事务结果。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path


LESSONS = str(Path(__file__).resolve().parent)
if LESSONS not in sys.path:
    sys.path.insert(0, LESSONS)

from task_store import TaskStore


class SQLiteTests(unittest.TestCase):
    """只检查存储的公开行为，不访问真实待办、不调用模型，也不替换数据库实现。"""

    def setUp(self):
        """为每个场景创建独立临时目录，测试结束后连同数据库一起清理。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.database = self.folder / "nested" / "tasks.sqlite3"
        self.store = TaskStore(self.database)

    def write_import(self, records):
        """把指定记录写成临时旧版 JSON，返回供公开导入接口使用的路径。"""
        source = self.folder / "old_tasks.json"
        source.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        return source

    def test_saved_tasks_survive_reopening_database(self):
        """重新构造存储对象仍能查询已写入的数据，证明结果已提交到文件。"""
        result = self.store.save_task("阅读两篇参考论文", "周五前", "图书馆")

        reopened = TaskStore(str(self.database))

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["task"]["status"], "pending")
        self.assertEqual(reopened.list_tasks(), [result["task"]])

    def test_repeated_save_reuses_id_and_keeps_completed_status(self):
        """相同参数重复保存只复用一项，完成后重试保存也不能重置完成状态。"""
        first = self.store.save_task("提交综述", "周五前")
        completed = self.store.mark_completed(first["task"]["id"])

        repeated = TaskStore(self.database).save_task("提交综述", "周五前", None)

        self.assertEqual(repeated, {"status": "exists", "task": completed})
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.store.list_tasks(), [completed])

    def test_changed_time_or_location_creates_distinct_tasks(self):
        """内容指纹只复用字段完全相同的任务，变更时间或地点应分别保存。"""
        original = self.store.save_task("组会", "周四", "实验楼")
        other_time = self.store.save_task("组会", "周五", "实验楼")
        other_place = self.store.save_task("组会", "周四", "线上")

        identifiers = {result["task"]["id"] for result in (original, other_time, other_place)}

        self.assertEqual(len(identifiers), 3)
        self.assertEqual(len(self.store.list_tasks()), 3)

    def test_sql_special_text_is_preserved_without_affecting_other_records(self):
        """带引号与 SQL 片段的输入应作为普通文本保存，不能改变表或其他任务。"""
        normal = self.store.save_task("保留的正常任务")["task"]
        title = "论文'; DROP TABLE tasks; --"
        time_text = "周五' OR 1=1 --"
        location = '实验室 "; DELETE FROM tasks; --'

        suspicious = self.store.save_task(title, time_text, location)["task"]
        missing = self.store.mark_completed("' OR 1=1 --")

        self.assertEqual(suspicious["title"], title)
        self.assertEqual(suspicious["time_text"], time_text)
        self.assertEqual(suspicious["location"], location)
        self.assertIsNone(missing)
        self.assertEqual(self.store.list_tasks(), [normal, suspicious])

    def test_completion_updates_only_selected_task_and_supports_filters(self):
        """完成操作只修改目标项；筛选、重复完成和不存在 ID 均有明确结果。"""
        first = self.store.save_task("提交材料")["task"]
        second = self.store.save_task("阅读论文")["task"]

        completed = self.store.mark_completed(first["id"])

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.store.mark_completed(first["id"]), completed)
        self.assertIsNone(self.store.mark_completed("missing-id"))
        self.assertEqual(self.store.list_tasks("completed"), [completed])
        self.assertEqual(self.store.list_tasks("pending"), [second])
        self.assertEqual(len(self.store.list_tasks()), 2)
        with self.assertRaises(ValueError):
            self.store.list_tasks("unknown")

    def test_invalid_inputs_are_rejected_without_creating_records(self):
        """空白标题和错误字段类型应在入库前拒绝，避免悄悄转换数据。"""
        invalid_arguments = [
            {"title": ""}, {"title": " \t\n"}, {"title": 123},
            {"title": "任务", "time_text": 123},
            {"title": "任务", "location": ["实验楼"]},
        ]

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.store.save_task(**arguments)

        self.assertEqual(self.store.list_tasks(), [])

    def test_import_preserves_old_ids_and_existing_completed_state(self):
        """导入保留旧 ID；重复导入计数准确，旧 pending 状态不能覆盖当前完成状态。"""
        records = [
            {"id": "legacy-paper", "title": "阅读参考论文", "time_text": "周五前", "location": None},
            {"id": "legacy-meeting", "title": "组会", "time_text": None, "location": "302", "status": "completed"},
        ]
        source = self.write_import(records)
        original_source = source.read_bytes()

        first_import = self.store.import_json(source)
        completed = self.store.mark_completed("legacy-paper")
        second_import = self.store.import_json(source)

        self.assertEqual(first_import, {"created": 2, "exists": 0})
        self.assertEqual(second_import, {"created": 0, "exists": 2})
        self.assertEqual(self.store.list_tasks()[0], completed)
        self.assertEqual({task["id"] for task in self.store.list_tasks()}, {"legacy-paper", "legacy-meeting"})
        self.assertEqual(len(self.store.list_tasks("completed")), 2)
        self.assertEqual(source.read_bytes(), original_source)

    def test_import_content_conflict_rolls_back_new_rows_in_same_batch(self):
        """一批中先新增、后遇到同 ID 内容冲突时，新增项也必须被事务撤回。"""
        original = self.store.save_task("原有待办", "周五前", "图书馆")["task"]
        for field, changed in (("title", "另一件事"), ("time_text", "周六"), ("location", "线上")):
            with self.subTest(field=field):
                conflicting = dict(original)
                conflicting[field] = changed
                source = self.write_import([
                    {"id": "new-before-conflict", "title": "冲突前新增的一项"},
                    conflicting,
                ])

                with self.assertRaises(ValueError):
                    self.store.import_json(source)

                self.assertEqual(TaskStore(self.database).list_tasks(), [original])

    def test_conflicting_ids_inside_one_import_leave_no_partial_data(self):
        """空数据库的一批记录内部 ID 冲突，也不能留下第一项已写入的数据。"""
        source = self.write_import([
            {"id": "same-id", "title": "第一件事"},
            {"id": "same-id", "title": "不同的第二件事"},
        ])

        with self.assertRaises(ValueError):
            self.store.import_json(source)

        self.assertEqual(TaskStore(self.database).list_tasks(), [])

    def test_invalid_import_records_preserve_existing_database(self):
        """结构、状态或字段类型错误时整批失败，原有数据保持不变。"""
        original = self.store.save_task("不能丢失的原有待办")["task"]
        invalid_records = [
            {"id": "bad", "title": "任务", "status": "done"},
            {"id": "bad", "title": 123},
            {"id": "bad", "title": "任务", "location": False},
            {"id": "bad", "title": "任务", "unexpected": "字段"},
            {"title": "缺少 ID"},
            {"id": "bad", "title": "  "},
            "不是记录对象",
        ]
        for invalid in invalid_records:
            with self.subTest(invalid=invalid):
                source = self.write_import([{"id": "valid-first", "title": "合法的前一项"}, invalid])
                with self.assertRaises(ValueError):
                    self.store.import_json(source)
                self.assertEqual(self.store.list_tasks(), [original])

        source = self.write_import({"tasks": []})
        with self.assertRaises(ValueError):
            self.store.import_json(source)
        source.write_text("这不是合法 JSON", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.import_json(source)
        self.assertEqual(self.store.list_tasks(), [original])


if __name__ == "__main__":
    unittest.main()
