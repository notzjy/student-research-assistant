"""正式消息时间扩展的日历、精度、时区与真实工作流回归。"""

import importlib
import sys
import tempfile
import unittest
from functools import partial
from pathlib import Path

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

event_time = importlib.import_module("student_assistant.event_time")
workflow = importlib.import_module("student_assistant.message_workflow")
models = importlib.import_module("student_assistant.message_models")
bridge = importlib.import_module("student_assistant.bridge")
store_module = importlib.import_module("student_assistant.message_store")
legacy = importlib.import_module("time_semantics")


class EventTimeTests(unittest.TestCase):
    """使用合成意图验证日历，不调用模型、邮箱或系统通知。"""

    def resolve(self, text, intent, reference="2026-10-17T10:00:00+08:00"):
        """通过正式入口解析一段时间，返回适合业务持久化的结果字典。"""
        request = bridge.TimeNormalizationRequest(time_text=text, reference_time=reference)
        return event_time.resolve_event_time(request, {"intent": intent, "reason": None}).model_dump()

    def test_absolute_date_only_keeps_precision_and_original_reference(self):
        """明确日期截止不补午夜，原消息参考串保持不变。"""
        result = self.resolve("2026年10月20日前", {
            "kind": "absolute", "year": 2026, "month": 10, "day": 20, "is_deadline": True,
        }, "2026-10-16T18:00:00Z")
        self.assertEqual(result["date"], "2026-10-20")
        self.assertEqual(result["precision"], "date")
        self.assertEqual(result["time_kind"], "deadline")
        self.assertEqual(result["reference_time"], "2026-10-16T18:00:00Z")
        self.assertNotIn("at", result)

    def test_year_omission_uses_message_beijing_year_without_rolling_forward(self):
        """跨UTC年份的消息使用北京时间年份，已过月日也不猜下一年。"""
        intent = {"kind": "absolute", "month": 1, "day": 1}
        result = self.resolve("1月1日", intent, "2026-12-31T18:00:00Z")
        self.assertEqual(result["date"], "2027-01-01")
        past = self.resolve("1月1日", intent)
        self.assertEqual(past["date"], "2026-01-01")
        next_year = self.resolve("明年1月1日", {**intent, "year_offset": 1})
        self.assertEqual(next_year["date"], "2027-01-01")

    def test_valid_leap_day_and_invalid_calendar_require_confirmation(self):
        """闰日按真实年份校验，非法日期不滚动修正为其他日期。"""
        result = self.resolve("2028年2月29日", {"kind": "absolute", "year": 2028, "month": 2, "day": 29})
        self.assertEqual(result["date"], "2028-02-29")
        for year, month, day in ((2026, 2, 29), (2026, 2, 30), (2026, 4, 31)):
            with self.subTest(year=year, month=month, day=day):
                invalid = self.resolve("待确认日期", {"kind": "absolute", "year": year, "month": month, "day": day})
                self.assertEqual(invalid["status"], "needs_confirmation")
                self.assertNotIn("date", invalid)
                self.assertNotIn("at", invalid)

    def test_explicit_foreign_offset_converts_to_beijing_including_cross_day(self):
        """原文明确UTC偏移时先构造源时刻，再转换为北京时间。"""
        result = self.resolve("2026-10-20 23:30 UTC-04:00", {
            "kind": "absolute", "year": 2026, "month": 10, "day": 20,
            "hour": 23, "minute": 30, "timezone_offset": "-04:00",
        })
        self.assertEqual(result["at"], "2026-10-21T11:30:00+08:00")
        self.assertEqual(result["timezone"], "UTC+08:00")

    def test_absolute_date_range_keeps_both_boundaries_and_no_at(self):
        """明确日期区间保留包括首末日的范围，不伪造任何边界钟点。"""
        result = self.resolve("2026年10月20日到22日", {
            "kind": "range",
            "start": {"kind": "absolute", "year": 2026, "month": 10, "day": 20},
            "end": {"kind": "absolute", "year": 2026, "month": 10, "day": 22},
        })
        self.assertEqual(result["precision"], "date_range")
        self.assertEqual((result["start_date"], result["end_date"]), ("2026-10-20", "2026-10-22"))
        self.assertNotIn("at", result)
        self.assertNotIn("start", result)

    def test_relative_minute_range_uses_same_source_anchor_for_both_endpoints(self):
        """明天两端均相对于原消息计算，终点不在起点基础上再加一天。"""
        result = self.resolve("明天15:00到17:00", {
            "kind": "range", "start": {"unit": "day", "offset": 1, "hour": 15, "minute": 0},
            "end": {"unit": "day", "offset": 1, "hour": 17, "minute": 0},
        }, "2026-10-16T18:00:00Z")
        self.assertEqual(result["precision"], "minute_range")
        self.assertEqual(result["start"], "2026-10-18T15:00:00+08:00")
        self.assertEqual(result["end"], "2026-10-18T17:00:00+08:00")
        self.assertEqual(result["reference_time"], "2026-10-16T18:00:00Z")
        self.assertNotIn("at", result)

    def test_explicit_cross_midnight_and_cross_year_ranges(self):
        """只有明确给出次日/次年边界才生成跨午夜、跨年的分钟区间。"""
        result = self.resolve("2026年12月31日23:00到2027年1月1日01:00", {
            "kind": "range",
            "start": {"kind": "absolute", "year": 2026, "month": 12, "day": 31, "hour": 23, "minute": 0},
            "end": {"kind": "absolute", "year": 2027, "month": 1, "day": 1, "hour": 1, "minute": 0},
        })
        self.assertEqual(result["start"], "2026-12-31T23:00:00+08:00")
        self.assertEqual(result["end"], "2027-01-01T01:00:00+08:00")

    def test_reversed_or_zero_minute_range_and_reversed_date_range_stay_unresolved(self):
        """不交换倒置边界，不把17到15点偷偷解释为隔夜。"""
        for start_hour, end_hour in ((17, 15), (15, 15)):
            with self.subTest(start=start_hour, end=end_hour):
                result = self.resolve("明天起止", {
                    "kind": "range", "start": {"unit": "day", "offset": 1, "hour": start_hour, "minute": 0},
                    "end": {"unit": "day", "offset": 1, "hour": end_hour, "minute": 0},
                })
                self.assertEqual(result["status"], "needs_confirmation")
                self.assertNotIn("start", result)
        reversed_dates = self.resolve("10月22日至20日", {
            "kind": "range", "start": {"kind": "absolute", "month": 10, "day": 22},
            "end": {"kind": "absolute", "month": 10, "day": 20},
        })
        self.assertEqual(reversed_dates["status"], "needs_confirmation")

    def test_mixed_precision_range_does_not_supply_missing_midnight(self):
        """一端只有日期、一端有钟点的区间须澄清，不能补起点零点。"""
        result = self.resolve("10月20日到22日17:00", {
            "kind": "range", "start": {"kind": "absolute", "month": 10, "day": 20},
            "end": {"kind": "absolute", "month": 10, "day": 22, "hour": 17, "minute": 0},
        })
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertIn("精度", result["confirmation_question"])
        self.assertNotIn("at", result)

    def test_legacy_intent_object_natural_month_and_bare_weekday_stay_compatible(self):
        """旧对象、整月范围与裸周几规则继续可用，不因扩展改变已有评测字段。"""
        request = bridge.TimeNormalizationRequest(time_text="下个月", reference_time="2026-12-17T10:00:00+08:00")
        old = legacy.TimeExtraction(intent=legacy.RelativeTimeIntent(unit="month", offset=1))
        result = event_time.resolve_event_time(request, old).model_dump()
        self.assertEqual((result["start_date"], result["end_date"]), ("2027-01-01", "2027-01-31"))
        weekday = self.resolve("周三", {"kind": "absolute", "year": 2099, "month": 1, "day": 1})
        self.assertEqual(weekday["date"], "2026-10-14")
        self.assertNotIn("at", weekday)

    def test_malformed_structures_and_offsets_are_rejected(self):
        """结构错误在Pydantic边界拒绝，不接受不完整钟点、非法偏移或整月端点。"""
        invalid = [
            {"kind": "absolute", "month": 10, "day": 20, "hour": 15},
            {"kind": "absolute", "month": 10, "day": 20, "hour": 15, "minute": 0, "timezone_offset": "+14:01"},
            {"kind": "absolute", "month": 10, "day": 20, "hour": 15, "minute": 0, "timezone_offset": "+24:00"},
            {"kind": "absolute", "month": 10, "day": 20, "timezone_offset": "+08:00"},
            {"kind": "absolute", "year": 2026, "year_offset": 1, "month": 10, "day": 20},
            {"kind": "range", "start": {"unit": "month", "offset": 0}, "end": {"unit": "day", "offset": 1}},
        ]
        for intent in invalid:
            with self.subTest(intent=intent), self.assertRaises(ValidationError):
                event_time.EventTimeExtraction(intent=intent)
        with self.assertRaises(ValidationError):
            bridge.TimeNormalizationRequest(time_text="明天", reference_time="2026-10-17T10:00:00")

    def test_unsupported_expression_keeps_explanation_without_fake_fields(self):
        """无法唯一识别的表达保留确认问题，不构造可计划的日期。"""
        request = bridge.TimeNormalizationRequest(time_text="农历下个月初三", reference_time="2026-10-17T10:00:00+08:00")
        result = event_time.resolve_event_time(request, {"intent": None, "reason": "请提供公历日期"}).model_dump()
        self.assertEqual(result["confirmation_question"], "请提供公历日期")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertNotIn("date", result)
        self.assertNotIn("at", result)


class EventTimeWorkflowTests(unittest.IsolatedAsyncioTestCase):
    """使用真实业务图与独立临时库验证扩展结构可完整保存。"""

    async def test_workflow_persists_explicit_range_and_retains_source_evidence(self):
        """正式消息工作流接入新解析，两个边界和原始来源时间均进入持久化结果。"""
        from student_assistant.evaluation import RuleAnalysisModel

        payload = models.MessageInput(
            source="manual", source_message_id="absolute-range", sender_id="synthetic-sender",
            sent_at="2026-10-17T10:00:00+08:00", text="请于2026年10月20日15:00到17:00在实验楼302参加组会。",
        )
        analysis = models.MessageAnalysis.model_validate({
            "category": "研究", "summary": "10月20日下午到实验楼302参加组会。",
            "attachment_description": None, "attachment_count": 0,
            "events": [{
                "title": "参加组会", "time_text": "2026年10月20日15:00到17:00", "location": "实验楼302",
                "evidence": payload.text,
                "time_extraction": {"intent": {
                    "kind": "range",
                    "start": {"kind": "absolute", "year": 2026, "month": 10, "day": 20, "hour": 15, "minute": 0},
                    "end": {"kind": "absolute", "year": 2026, "month": 10, "day": 20, "hour": 17, "minute": 0},
                }, "reason": None},
            }],
        })
        with tempfile.TemporaryDirectory(prefix="event-time-test-") as folder:
            store = store_module.MessageStore(bridge.TaskStore(Path(folder) / "events.sqlite3"))
            processor = workflow.MessageProcessor(store, model_factory=partial(RuleAnalysisModel, analysis))
            result = await processor.process(payload)
            detail = store.get_message(result["message"]["message_id"])
            self.assertEqual(detail["source_message"], payload.model_dump())
            event = detail["events"][0]
            self.assertEqual(event["evidence"], payload.text)
            self.assertEqual(event["time"]["start"], "2026-10-20T15:00:00+08:00")
            self.assertEqual(event["time"]["end"], "2026-10-20T17:00:00+08:00")
            self.assertNotIn("at", event["time"])


if __name__ == "__main__":
    unittest.main()
