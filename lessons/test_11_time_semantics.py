"""第十一课离线测试：模型只提取时间意图，真实日历运算和精度校验由程序完成。"""

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from langchain_core.exceptions import OutputParserException
from openai import APIError, APITimeoutError
from pydantic import TypeAdapter


LESSONS = str(Path(__file__).resolve().parent)
if LESSONS not in sys.path:
    sys.path.insert(0, LESSONS)

semantics = importlib.import_module("time_semantics")
TimeNormalizationRequest = importlib.import_module("time_parser").TimeNormalizationRequest
TaskStore = importlib.import_module("task_store").TaskStore
lesson05 = importlib.import_module("05_fastapi")
lesson03 = importlib.import_module("03_tool_calling")


class FakeStructuredModel:
    """记录结构化调用配置和消息，返回预设提取结果或异常，不访问网络。"""

    def __init__(self, *responses):
        """保存各次请求的提取结果，并初始化配置和消息记录。"""
        self.responses = list(responses)
        self.schema_calls = []
        self.inputs = []

    def with_structured_output(self, schema, *, method):
        """记录真实使用的输出 schema 与模式，返回可执行的模型替身。"""
        self.schema_calls.append((schema, method))
        return self

    def invoke(self, messages):
        """保存本次消息，返回下一项提取结果或抛出指定模型异常。"""
        self.inputs.append(list(messages))
        if not self.responses:
            raise AssertionError("出现未预期的模型调用")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TimeSemanticsTests(unittest.TestCase):
    """验证精度、日历边界与 HTTP 错误映射，不访问模型账户、JSON 或真实数据库。"""

    def setUp(self):
        """设置固定原消息基准，禁止真实模型初始化与旧 JSON 待办访问。"""
        self.reference = "2026-10-16T20:00:00+08:00"
        self.real_model = self.enterContext(patch.object(
            lesson03, "create_model", side_effect=AssertionError("测试禁止创建真实模型"),
        ))
        self.json_load = self.enterContext(patch.object(
            lesson03, "load_tasks", side_effect=AssertionError("时间解释禁止读取旧 JSON"),
        ))
        self.json_save = self.enterContext(patch.object(
            lesson03, "save_task", side_effect=AssertionError("时间解释禁止写入旧 JSON"),
        ))

    def tearDown(self):
        """确认所有场景均使用注入模型，没有触发真实模型或旧待办存储。"""
        self.real_model.assert_not_called()
        self.json_load.assert_not_called()
        self.json_save.assert_not_called()

    def request(self, text, reference=None):
        """构造严格校验后的时间请求，保留调用方原文和原始参考时间字符串。"""
        return TimeNormalizationRequest(
            time_text=text, reference_time=self.reference if reference is None else reference,
        )

    def extraction(self, **intent_fields):
        """以公开意图模型创建结构化提取结果，语义计算仍由真实解析器执行。"""
        intent = semantics.RelativeTimeIntent(**intent_fields)
        return semantics.TimeExtraction(intent=intent, reason=None)

    def make_client(self, factory, store=None):
        """创建教学 API 的进程内客户端，并托管其生命周期。"""
        app = lesson05.create_app(model_factory=factory, store=store)
        return self.enterContext(TestClient(app))

    def test_intent_and_extraction_reject_inconsistent_or_coerced_fields(self):
        """意图严格校验类型及字段关系，不允许月份带钟点或缺少不支持原因。"""
        invalid_intents = [
            {"unit": "day", "offset": True}, {"unit": "day", "offset": "1"},
            {"unit": "day", "offset": 121}, {"unit": "day", "offset": -121},
            {"unit": "day", "weekday": 2}, {"unit": "week", "weekday": 7},
            {"unit": "day", "hour": 9}, {"unit": "day", "minute": 30},
            {"unit": "week", "hour": 9, "minute": 0},
            {"unit": "month", "hour": 9, "minute": 0},
            {"unit": "year", "is_deadline": True}, {"unit": "week", "is_deadline": True},
            {"unit": "day", "unexpected": "额外字段"},
        ]
        for fields in invalid_intents:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                semantics.RelativeTimeIntent.model_validate(fields)
        valid = self.extraction(unit="week", weekday=2, hour=15, minute=0)
        self.assertEqual(valid.intent.weekday, 2)
        for fields in (
            {"intent": None, "reason": None}, {"intent": None, "reason": " \t"},
            {"intent": valid.intent, "reason": "有意图时不应同时报告不支持"},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                semantics.TimeExtraction.model_validate(fields)

    def test_precision_union_rejects_mixed_fields_invalid_dates_and_reversed_ranges(self):
        """判别联合拒绝混用日期与钟点、无效日历日期以及前后颠倒的范围。"""
        adapter = TypeAdapter(semantics.TimeInterpretation)
        common = {"time_text": "时间原文", "reference_time": self.reference, "timezone": "UTC+08:00", "status": "resolved"}
        date_result = {**common, "precision": "date", "time_kind": "point", "date": "2026-10-17"}
        minute_result = {**common, "precision": "minute", "time_kind": "deadline", "at": "2026-10-17T15:00:00+08:00"}
        range_result = {**common, "precision": "date_range", "time_kind": "period", "start_date": "2026-10-01", "end_date": "2026-10-31"}
        for payload in (date_result, minute_result, range_result):
            with self.subTest(valid=payload):
                adapter.validate_python(payload)
        invalid = [
            {**date_result, "at": "2026-10-17T00:00:00+08:00"},
            {**minute_result, "date": "2026-10-17"},
            {**date_result, "date": "2026-02-30"},
            {**range_result, "start_date": "2026-10-31", "end_date": "2026-10-01"},
            {**range_result, "end_date": "2026-02-30"},
            {**common, "precision": "unresolved", "status": "needs_confirmation", "time_kind": "unknown", "confirmation_question": " "},
        ]
        for payload in invalid:
            with self.subTest(invalid=payload), self.assertRaises(ValueError):
                adapter.validate_python(payload)

    def test_calendar_resolution_handles_timezone_leap_month_and_natural_periods(self):
        """以北京时间计算日历日期，自然周、闰月和跨年范围不以固定天数代替。"""
        cases = [
            ("今天", "2026-12-31T18:30:00Z", {"unit": "day"}, {"precision": "date", "date": "2027-01-01"}),
            ("下周", self.reference, {"unit": "week", "offset": 1}, {"precision": "date_range", "start_date": "2026-10-19", "end_date": "2026-10-25"}),
            ("下个月", "2028-01-31T20:00:00+08:00", {"unit": "month", "offset": 1}, {"precision": "date_range", "start_date": "2028-02-01", "end_date": "2028-02-29"}),
            ("明年", "2026-12-31T20:00:00+08:00", {"unit": "year", "offset": 1}, {"precision": "date_range", "start_date": "2027-01-01", "end_date": "2027-12-31"}),
            ("明天上午九点前", self.reference, {"unit": "day", "offset": 1, "hour": 9, "minute": 0, "is_deadline": True}, {"precision": "minute", "at": "2026-10-17T09:00:00+08:00", "time_kind": "deadline"}),
        ]
        for text, reference, fields, expected in cases:
            with self.subTest(text=text, reference=reference):
                result = semantics.resolve_intent(self.request(text, reference), self.extraction(**fields)).model_dump()
                self.assertEqual(result["status"], "resolved")
                self.assertEqual(result["time_text"], text)
                self.assertEqual(result["reference_time"], reference)
                self.assertEqual(result["timezone"], "UTC+08:00")
                for field, value in expected.items():
                    self.assertEqual(result[field], value)
                if expected["precision"] != "minute":
                    self.assertNotIn("at", result)

    def test_bare_weekday_policy_overrides_model_offset_and_clock(self):
        """裸星期按原消息本周保留日期精度，不能被模型猜成下周或擅自补钟点。"""
        mistaken = self.extraction(unit="week", offset=1, weekday=0, hour=23, minute=59)
        cases = [
            ("周三", "2026-10-14", "point"),
            ("星期四前", "2026-10-15", "deadline"),
            ("周日", "2026-10-18", "point"),
        ]
        for text, expected_date, expected_kind in cases:
            with self.subTest(text=text):
                result = semantics.resolve_intent(self.request(text), mistaken).model_dump()
                self.assertEqual(result["precision"], "date")
                self.assertEqual(result["date"], expected_date)
                self.assertEqual(result["time_kind"], expected_kind)
                self.assertNotIn("at", result)

    def test_unsupported_intent_preserves_reason_for_confirmation(self):
        """模型不支持的表达返回确认问题和原始原因，不伪造日期或午夜钟点。"""
        reason = "活动开始前一小时缺少活动的实际开始时间。"
        extraction = semantics.TimeExtraction(intent=None, reason=reason)

        result = semantics.resolve_intent(self.request("活动开始前一小时"), extraction).model_dump()

        self.assertEqual(result["precision"], "unresolved")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(result["time_kind"], "unknown")
        self.assertIn(reason, result["confirmation_question"])
        self.assertNotIn("at", result)
        self.assertNotIn("date", result)

    def test_structured_calls_include_schema_and_do_not_reuse_previous_clock(self):
        """连续请求分别提取意图，显式提供 schema 和原消息背景，后续月份不沿用先前钟点。"""
        model = FakeStructuredModel(
            self.extraction(unit="day", offset=1, hour=9, minute=0),
            self.extraction(unit="month", offset=1),
        )
        first_request = self.request("明天上午九点", "2026-12-31T18:30:00Z")
        second_request = self.request("下个月")

        first = semantics.interpret_time(first_request, model=model).model_dump()
        second = semantics.interpret_time(second_request, model=model).model_dump()

        self.assertEqual(first["precision"], "minute")
        self.assertEqual(first["at"], "2027-01-02T09:00:00+08:00")
        self.assertEqual(second["precision"], "date_range")
        self.assertEqual(second["start_date"], "2026-11-01")
        self.assertNotIn("at", second)
        self.assertEqual(model.schema_calls, [(semantics.TimeExtraction, "json_mode")] * 2)
        for messages, request in zip(model.inputs, (first_request, second_request), strict=True):
            # LangChain 同时接受 BaseMessage 和 (角色, 文本) 二元组，两种输入都符合调用约定。
            content = "\n".join(message[1] if isinstance(message, tuple) else message.content for message in messages)
            self.assertIn(request.time_text, content)
            self.assertIn(request.reference_time, content)
            self.assertIn("RelativeTimeIntent", content)
            self.assertIn("offset", content)

    def test_http_structured_model_runs_real_resolver_without_storage_access(self):
        """HTTP 使用注入模型提取后执行真实日期计算，旧归一化接口仍无需模型或存储。"""
        store = Mock(spec=TaskStore)
        model = FakeStructuredModel(self.extraction(unit="day", offset=1, is_deadline=True))
        factory = Mock(return_value=model)
        client = self.make_client(factory, store=store)
        normalized = client.post("/time/normalize", json={"time_text": "明天09:00", "reference_time": self.reference})
        self.assertEqual(normalized.status_code, 200)
        factory.assert_not_called()

        response = client.post("/time/interpret", json={"time_text": "明天前", "reference_time": self.reference})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["precision"], "date")
        self.assertEqual(body["date"], "2026-10-17")
        self.assertEqual(body["time_kind"], "deadline")
        self.assertNotIn("at", body)
        self.assertEqual(store.mock_calls, [])
        factory.assert_called_once_with()
        self.assertEqual(len(model.inputs), 1)

    def test_http_validation_and_missing_configuration_precede_model_initialization(self):
        """非法请求先返回 422；缺少配置返回 503，二者均不初始化任何模型。"""
        factory = Mock(side_effect=AssertionError("非法请求不能初始化模型"))
        client = self.make_client(factory)
        response = client.post("/time/interpret", json={"time_text": "明天", "reference_time": "2026-10-16T20:00:00"})
        self.assertEqual(response.status_code, 422)
        factory.assert_not_called()
        missing = {"COMPATIBLE_MODEL": "", "COMPATIBLE_BASE_URL": "", "COMPATIBLE_API_KEY": ""}
        unconfigured = self.make_client(None)
        with patch.dict("os.environ", missing):
            response = unconfigured.post("/time/interpret", json={"time_text": "明天", "reference_time": self.reference})
        self.assertEqual(response.status_code, 503)

    def test_http_model_failures_and_calendar_overflow_have_distinct_safe_statuses(self):
        """模型失败、结构解析失败与日历溢出分别映射状态码，并隐藏原始模型错误。"""
        request = httpx.Request("POST", "https://model.invalid/chat/completions")
        failures = [
            (APIError("private-model-detail", request=request, body=None), 502),
            (APITimeoutError(request=request), 504),
            (OutputParserException("private-model-detail"), 502),
        ]
        for error, expected in failures:
            with self.subTest(error=type(error).__name__):
                client = self.make_client(Mock(return_value=FakeStructuredModel(error)))
                response = client.post("/time/interpret", json={"time_text": "明天", "reference_time": self.reference})
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("private-model-detail", response.text)
        overflow_model = FakeStructuredModel(self.extraction(unit="day", offset=1))
        client = self.make_client(Mock(return_value=overflow_model))
        response = client.post("/time/interpret", json={
            "time_text": "明天", "reference_time": "9999-12-31T20:00:00+08:00",
        })
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
