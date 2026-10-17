"""第十课离线测试：明确时间按北京时间换算，歧义保留确认，接口不触发业务写入。"""

import importlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient


LESSONS = str(Path(__file__).resolve().parent)
if LESSONS not in sys.path:
    sys.path.insert(0, LESSONS)

time_parser = importlib.import_module("time_parser")
lesson05 = importlib.import_module("05_fastapi")
lesson07 = importlib.import_module("07_sqlite_api")
lesson03 = importlib.import_module("03_tool_calling")
TaskStore = importlib.import_module("task_store").TaskStore


class TimeParserTests(unittest.TestCase):
    """通过纯函数、输出模型和进程内 HTTP 验证时间边界，不创建真实模型。"""

    def setUp(self):
        """准备明确带时区的基准，禁止测试过程中访问模型或旧 JSON 待办。"""
        self.reference_text = "2026-10-16T20:00:00+08:00"
        self.reference = datetime.fromisoformat(self.reference_text)
        self.real_model = self.enterContext(patch.object(
            lesson03, "create_model", side_effect=AssertionError("时间归一化禁止调用真实模型"),
        ))
        self.json_load = self.enterContext(patch.object(
            lesson03, "load_tasks", side_effect=AssertionError("时间归一化禁止读取旧待办"),
        ))
        self.json_save = self.enterContext(patch.object(
            lesson03, "save_task", side_effect=AssertionError("时间归一化禁止写入旧待办"),
        ))

    def tearDown(self):
        """确认时间接口未隐式调用真实模型或旧 JSON 存储。"""
        self.real_model.assert_not_called()
        self.json_load.assert_not_called()
        self.json_save.assert_not_called()

    def make_client(self, store=None):
        """创建注入拒绝调用模型工厂的教学 API，并托管进程内客户端。"""
        factory = Mock(side_effect=AssertionError("时间接口禁止调用模型工厂"))
        client = self.enterContext(TestClient(lesson05.create_app(model_factory=factory, store=store)))
        return client, factory

    def test_utc_reference_uses_beijing_calendar_date_and_preserves_offset(self):
        """先把基准转换为北京时间再理解今天，同时返回原基准的 UTC 偏移。"""
        reference = time_parser.parse_reference_time("2026-10-16T18:30:00Z")

        result = time_parser.normalize_time("今天09:00", reference)

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.at, "2026-10-17T09:00:00+08:00")
        self.assertEqual(result.timezone, "UTC+08:00")
        returned_reference = datetime.fromisoformat(result.reference_time)
        self.assertEqual(returned_reference, reference)
        self.assertEqual(returned_reference.utcoffset(), timedelta(0))
        self.assertEqual(result.time_kind, "point")
        self.assertIsNone(result.confirmation_question)

    def test_relative_dates_cross_month_year_and_leap_day(self):
        """相对日期使用日历运算，跨月、跨年和闰年时不能只手工增加日期数字。"""
        cases = [
            ("2026-01-31T20:00:00+08:00", "明天09:00", "2026-02-01T09:00:00+08:00"),
            ("2026-12-31T20:00:00+08:00", "后天09:00", "2027-01-02T09:00:00+08:00"),
            ("2028-02-28T20:00:00+08:00", "明天09:00", "2028-02-29T09:00:00+08:00"),
            ("2028-02-29T20:00:00+08:00", "明天09:00", "2028-03-01T09:00:00+08:00"),
        ]
        for reference, text, expected in cases:
            with self.subTest(reference=reference, text=text):
                result = time_parser.normalize_time(text, datetime.fromisoformat(reference))
                self.assertEqual(result.status, "resolved")
                self.assertEqual(result.at, expected)

    def test_supported_clock_forms_distinguish_point_and_deadline(self):
        """明确钟点转换正确，前字表达截止，明确的过去日期也保留其实际时间。"""
        cases = [
            ("明天下午三点", "point", "2026-10-17T15:00:00+08:00"),
            ("后天上午11点前", "deadline", "2026-10-18T11:00:00+08:00"),
            ("今天03:05前", "deadline", "2026-10-16T03:05:00+08:00"),
            ("2026-10-20 09:30前", "deadline", "2026-10-20T09:30:00+08:00"),
            ("2025-01-01T08:10", "point", "2025-01-01T08:10:00+08:00"),
        ]
        for text, kind, expected in cases:
            with self.subTest(text=text):
                result = time_parser.normalize_time(text, self.reference)
                self.assertEqual(result.status, "resolved")
                self.assertEqual(result.time_text, text)
                self.assertEqual(result.time_kind, kind)
                self.assertEqual(result.at, expected)
                self.assertIsNone(result.confirmation_question)

    def test_ambiguous_negative_and_range_text_require_confirmation(self):
        """不从否定句、区间或混合文本中局部匹配时间，也不替缺钟点消息猜截止时间。"""
        texts = [
            "周五前", "明天", "2026-10-20", "明天下午", "明天下午12点",
            "不是明天下午三点", "明天下午三点到四点", "明天09:00至11:00",
            "明天下午三点开会", "今天09:00，明天10:00", "  周五前  ",
        ]
        for text in texts:
            with self.subTest(text=text):
                result = time_parser.normalize_time(text, self.reference)
                self.assertEqual(result.status, "needs_confirmation")
                self.assertEqual(result.time_text, text)
                self.assertIsNone(result.at)
                self.assertIsInstance(result.confirmation_question, str)
                self.assertTrue(result.confirmation_question.strip())

    def test_reference_parser_accepts_offsets_and_rejects_missing_or_invalid_ones(self):
        """基准必须使用带 T 和明确偏移的 ISO 时间，不能把本机时区默认为用户基准。"""
        accepted = [
            ("2026-10-16T20:00Z", timedelta(0)),
            ("2026-10-16T20:00:01+05:30", timedelta(hours=5, minutes=30)),
            ("2026-10-16T20:00-04:00", -timedelta(hours=4)),
        ]
        for text, offset in accepted:
            with self.subTest(text=text):
                parsed = time_parser.parse_reference_time(text)
                self.assertIsInstance(parsed, datetime)
                self.assertEqual(parsed.utcoffset(), offset)
        invalid = [
            "2026-10-16T20:00:00", "2026-10-16", "2026-10-16 20:00:00+08:00",
            "2026-10-16T20:00:00+0800", "2026-10-16T20:00:00+25:00",
        ]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                time_parser.parse_reference_time(text)

    def test_naive_datetime_is_rejected_by_normalization(self):
        """直接调用纯函数也拒绝无时区 datetime，避免依赖运行机器的本地设置。"""
        with self.assertRaises(ValueError):
            time_parser.normalize_time("明天09:00", datetime(2026, 10, 16, 20, 0))

    def test_recognized_but_invalid_dates_or_clock_values_return_http_422(self):
        """已经识别的时间格式若日期或时分非法，抛错并返回 422，不能降级成猜测。"""
        client, factory = self.make_client()
        for text in (
            "2025-02-29 15:00", "2026-13-01T09:00", "2026-02-30前", "2026-02-30",
            "明天24:00", "明天15:60",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    time_parser.normalize_time(text, self.reference)
                response = client.post("/time/normalize", json={
                    "time_text": text, "reference_time": self.reference_text,
                })
                self.assertEqual(response.status_code, 422)
        factory.assert_not_called()

    def test_timezone_and_relative_date_overflows_return_http_422(self):
        """最大日期在时区转换或加一天时溢出，应作为非法输入返回 422 而非服务器错误。"""
        client, factory = self.make_client()
        cases = [
            ("9999-12-31T23:00:00Z", "今天09:00"),
            ("9999-12-31T20:00:00+08:00", "明天09:00"),
        ]
        for reference_text, text in cases:
            with self.subTest(reference=reference_text, text=text):
                reference = time_parser.parse_reference_time(reference_text)
                with self.assertRaises(ValueError):
                    time_parser.normalize_time(text, reference)
                response = client.post("/time/normalize", json={
                    "time_text": text, "reference_time": reference_text,
                })
                self.assertEqual(response.status_code, 422)
        factory.assert_not_called()

    def test_http_request_rejects_missing_offsets_types_and_extra_fields(self):
        """HTTP 输入严格拒绝错类型、空白、过长文本、缺时区及额外字段。"""
        client, factory = self.make_client()
        valid = {"time_text": "明天09:00", "reference_time": self.reference_text}
        invalid_bodies = [
            {}, {"reference_time": self.reference_text}, {"time_text": "明天09:00"},
            {**valid, "time_text": 123}, {**valid, "time_text": None},
            {**valid, "time_text": " \t\n"}, {**valid, "time_text": "字" * 201},
            {**valid, "reference_time": 123}, {**valid, "reference_time": None},
            {**valid, "reference_time": "2026-10-16T20:00:00"},
            {**valid, "unexpected": True},
        ]
        for body in invalid_bodies:
            with self.subTest(body=body):
                response = client.post("/time/normalize", json=body)
                self.assertEqual(response.status_code, 422)
        factory.assert_not_called()

    def test_output_model_rejects_inconsistent_resolution_fields(self):
        """输出模型禁止已解决但无时间、未确认却带确定时间等彼此矛盾的字段组合。"""
        base = {
            "time_text": "明天15:00", "reference_time": self.reference_text,
            "timezone": "UTC+08:00", "status": "resolved", "time_kind": "point",
            "at": "2026-10-17T15:00:00+08:00", "confirmation_question": None,
        }
        time_parser.NormalizedTime.model_validate(base)
        time_parser.NormalizedTime.model_validate({
            **base, "status": "needs_confirmation", "time_kind": "unknown",
            "at": None, "confirmation_question": "具体几点？",
        })
        invalid = [
            {**base, "at": None}, {**base, "time_kind": "unknown"},
            {**base, "confirmation_question": "具体几点？"},
            {**base, "status": "needs_confirmation", "confirmation_question": "具体几点？"},
            {**base, "status": "needs_confirmation", "at": None},
            {**base, "status": "needs_confirmation", "at": None, "confirmation_question": "  "},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                time_parser.NormalizedTime.model_validate(payload)

    def test_normalization_endpoint_does_not_access_model_or_storage(self):
        """时间接口只计算输入，注入存储的任何方法和模型工厂都不应被访问。"""
        store = Mock(spec=TaskStore)
        client, factory = self.make_client(store=store)

        response = client.post("/time/normalize", json={
            "time_text": "明天下午三点", "reference_time": self.reference_text,
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["at"], "2026-10-17T15:00:00+08:00")
        self.assertEqual(response.json()["time_kind"], "point")
        self.assertEqual(store.mock_calls, [])
        factory.assert_not_called()

    def test_sqlite_app_inherits_endpoint_and_preserves_user_pending_routes(self):
        """第七课应用继承时间接口，时间计算不连接数据库且保留用户未完成查询接口。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / "assistant.sqlite3"
        store = TaskStore(database)
        pending = store.save_task("仍需处理的事项", "周五前")["task"]
        completed = store.save_task("已经处理的事项")["task"]
        store.mark_completed(completed["id"])
        original = store.list_tasks()
        factory = Mock(side_effect=AssertionError("时间接口不调用模型"))
        app = lesson07.create_app(db_path=database, model_factory=factory)
        client = self.enterContext(TestClient(app))

        with patch.object(TaskStore, "_connect", side_effect=AssertionError("时间计算不连接数据库")):
            response = client.post("/time/normalize", json={
                "time_text": "周五前", "reference_time": self.reference_text,
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "needs_confirmation")
        self.assertIsNone(response.json()["at"])
        self.assertEqual(store.list_tasks(), original)
        self.assertEqual(client.get("/tasks/pending").json(), {"count": 1, "tasks": [pending]})
        self.assertEqual(client.get("/tasks/pending/count").json(), {"count": 1})
        self.assertEqual(client.get("/tasks/count").json(), {"count": 2})
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
