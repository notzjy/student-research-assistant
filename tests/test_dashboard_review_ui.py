"""人工纠正界面测试：真实 Streamlit 重运行，HTTP 与原件操作只在明确提交时发生。"""

import json
import unittest
from copy import deepcopy
from datetime import date, time
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from dashboard_api import DashboardError
from dashboard_review import parse_labels


SCRIPT = """
import streamlit as st
from dashboard_api import DashboardAPI
from dashboard_review import render_message_review, render_document_review, render_message_attachment_download
api = DashboardAPI('http://127.0.0.1:8002')
render_message_review(api, st.session_state['test_message'])
render_document_review(api, st.session_state['test_document'])
render_message_attachment_download(api, st.session_state['test_message'])
"""


class DashboardReviewUITests(unittest.TestCase):
    """使用合成数据检验纠错入口、分类证据展示、版本传递与下载门控。"""

    def setUp(self):
        """准备既有人工版本与日期事项，避免测试误依赖全新零版本数据。"""
        self.api = Mock()
        self.api.request.return_value = {"status": "updated"}
        self.api.download_attachment.return_value = b"synthetic attachment"
        self.enterContext(patch("dashboard_api.DashboardAPI", return_value=self.api))
        self.message_id = "a" * 24
        self.task_id = "b" * 16
        self.document_id = "c" * 64
        self.suffix = self.message_id + "_" + self.task_id
        self.message = {
            "message_id": self.message_id, "source_message": {"sent_at": "2026-10-17T10:00:00+08:00"},
            "analysis": {"category": "研究", "summary": "组会通知"}, "labels": ["待读"],
            "review": {"version": 3}, "attachments": [{"filename": "note.md", "availability": "available"}],
            "events": [{"task_id": self.task_id, "title": "组会", "location": "302", "status": "pending",
                        "time_text": "明天", "review": {"version": 2},
                        "time": {"precision": "date", "status": "resolved", "time_kind": "point", "date": "2026-10-18", "time_text": "明天", "reference_time": "2026-10-17T10:00:00+08:00", "timezone": "UTC+08:00"}}],
        }
        self.document = {
            "document_id": self.document_id, "filename": "note.md", "status": "indexed", "category": "研究",
            "labels": ["检索"], "review": {"version": 1}, "classification_basis": "document_text",
            "classification": {"status": "completed", "result": {
                "evidence": [{"page": 1, "quote": "检索效果为0.85"}],
                "coverage": {"scope": "sampled_text", "read_characters": 100, "total_characters": 1000},
            }},
        }
        self.app = AppTest.from_string(SCRIPT, default_timeout=10)
        self.app.session_state["test_message"] = deepcopy(self.message)
        self.app.session_state["test_document"] = deepcopy(self.document)

    def run_app(self):
        """执行真实脚本重运行，任何界面异常均作为测试失败。"""
        self.app.run()
        self.assertFalse(list(self.app.exception), list(self.app.exception))

    def test_initial_render_is_read_only_and_discloses_sampling(self):
        """展开式表单首次绘制不读写服务，正文证据与节选覆盖率同时可见。"""
        self.run_app()
        self.api.request.assert_not_called()
        self.api.download_attachment.assert_not_called()
        self.api.upload_attachment.assert_not_called()
        self.assertTrue(any("100/1000" in item.value for item in self.app.warning))
        self.assertIn("检索效果为0.85", [item.value for item in self.app.text])

    def test_message_save_sends_multilabel_and_current_version_once(self):
        """人工类别与去重标签携带已有版本提交，普通重运行不会重复保存。"""
        self.run_app()
        self.app.selectbox(key="review_category_" + self.message_id).select("求职")
        self.app.text_input(key="review_labels_" + self.message_id).input("招聘， 简历,招聘")
        self.app.button(key="message_save_" + self.message_id).click()
        self.run_app()
        self.api.request.assert_called_once_with("PUT", "/messages/" + self.message_id + "/review", json={
            "expected_version": 3, "category": "求职", "labels": ["招聘", "简历"], "summary": "组会通知",
        })
        self.run_app()
        self.assertEqual(self.api.request.call_count, 1)

    def test_event_save_keeps_reference_time_and_explicit_replan_choice(self):
        """事项修订发送完整时间结果与状态，并传递用户关闭重排的选择。"""
        self.run_app()
        self.app.text_input(key="event_location_" + self.suffix).input("402")
        self.app.checkbox(key="event_replan_" + self.suffix).uncheck()
        self.app.selectbox(key="event_status_" + self.suffix).select("已取消")
        self.app.button(key="event_save_" + self.suffix).click()
        self.run_app()
        args, kwargs = self.api.request.call_args
        self.assertEqual(args, ("PUT", "/tasks/" + self.task_id + "/review"))
        self.assertEqual(kwargs["params"], {"replan": False})
        self.assertEqual(kwargs["json"]["expected_version"], 2)
        self.assertEqual(kwargs["json"]["status"], "cancelled")
        self.assertEqual(kwargs["json"]["time"]["reference_time"], self.message["source_message"]["sent_at"])
        self.assertEqual(kwargs["json"]["location"], "402")

    def test_invalid_time_json_stops_before_http_write(self):
        """主动选择高级模式后JSON格式错误会阻止写入，默认不要求用户编辑JSON。"""
        self.run_app()
        self.app.selectbox(key="event_time_mode_" + self.suffix).select("高级JSON")
        self.run_app()
        self.app.text_area(key="event_time_json_" + self.suffix).input("{invalid json")
        self.app.button(key="event_save_" + self.suffix).click()
        self.run_app()
        self.api.request.assert_not_called()
        self.assertTrue(list(self.app.error))

    def test_default_time_mode_hides_json_and_never_prefills_a_clock(self):
        """默认保留已有时间，JSON不可见，手工提醒钟点保持未选择。"""
        self.run_app()
        self.assertEqual(self.app.selectbox(key="event_time_mode_" + self.suffix).value, "保留现有")
        self.assertNotIn("event_time_json_" + self.suffix, [item.key for item in self.app.text_area])
        self.assertIsNone(self.app.time_input(key="manual_reminder_clock_" + self.suffix).value)
        self.api.request.assert_not_called()

    def test_date_picker_saves_deadline_without_inventing_a_clock(self):
        """通过日期控件纠正截止日期，不显示事项钟点，也不生成at字段。"""
        self.run_app()
        self.app.selectbox(key="event_time_mode_" + self.suffix).select("仅日期")
        self.run_app()
        self.app.date_input(key="event_date_" + self.suffix).set_value(date(2026, 10, 21))
        self.app.selectbox(key="event_time_kind_" + self.suffix).select("截止时间")
        self.app.button(key="event_save_" + self.suffix).click()
        self.run_app()
        value = self.api.request.call_args.kwargs["json"]["time"]
        self.assertEqual(value["date"], "2026-10-21")
        self.assertEqual(value["precision"], "date")
        self.assertEqual(value["time_kind"], "deadline")
        self.assertEqual(value["reference_time"], self.message["source_message"]["sent_at"])
        self.assertNotIn("at", value)
        self.assertNotIn("event_clock_" + self.suffix, [item.key for item in self.app.time_input])

    def test_exact_clock_is_required_before_saving_concrete_time(self):
        """选择具体时刻仍须明确选择钟点，日期已选而钟点缺失时不得补午夜。"""
        self.run_app()
        self.app.selectbox(key="event_time_mode_" + self.suffix).select("具体时刻")
        self.run_app()
        self.assertIsNone(self.app.time_input(key="event_clock_" + self.suffix).value)
        self.app.date_input(key="event_date_" + self.suffix).set_value(date(2026, 10, 21))
        self.app.button(key="event_save_" + self.suffix).click()
        self.run_app()
        self.api.request.assert_not_called()
        self.app.time_input(key="event_clock_" + self.suffix).set_value(time(15, 30))
        self.app.button(key="event_save_" + self.suffix).click()
        self.run_app()
        value = self.api.request.call_args.kwargs["json"]["time"]
        self.assertEqual(value["at"], "2026-10-21T15:30:00+08:00")
        self.assertEqual(value["precision"], "minute")

    def test_period_controls_keep_both_boundaries(self):
        """起止时段保存独立的开始和结束时刻，用户不需要填写协议JSON。"""
        self.run_app()
        self.app.selectbox(key="event_time_mode_" + self.suffix).select("起止时段")
        self.run_app()
        self.app.date_input(key="event_start_date_" + self.suffix).set_value(date(2026, 10, 21))
        self.app.date_input(key="event_end_date_" + self.suffix).set_value(date(2026, 10, 21))
        self.app.time_input(key="event_start_clock_" + self.suffix).set_value(time(15, 0))
        self.app.time_input(key="event_end_clock_" + self.suffix).set_value(time(17, 0))
        self.app.button(key="event_save_" + self.suffix).click()
        self.run_app()
        value = self.api.request.call_args.kwargs["json"]["time"]
        self.assertEqual(value["precision"], "minute_range")
        self.assertEqual(value["time_kind"], "period")
        self.assertEqual(value["start"], "2026-10-21T15:00:00+08:00")
        self.assertEqual(value["end"], "2026-10-21T17:00:00+08:00")
        self.assertNotIn("at", value)

    def test_date_range_controls_reject_reversed_boundaries_then_save_dates(self):
        """日期范围在客户端拒绝终点早于起点，合法范围保持日期精度且不显示钟点。"""
        self.run_app()
        self.app.selectbox(key="event_time_mode_" + self.suffix).select("日期范围")
        self.run_app()
        self.app.date_input(key="event_start_date_" + self.suffix).set_value(date(2026, 10, 21))
        self.app.date_input(key="event_end_date_" + self.suffix).set_value(date(2026, 10, 20))
        self.app.button(key="event_save_" + self.suffix).click()
        self.run_app()
        self.api.request.assert_not_called()
        self.app.date_input(key="event_end_date_" + self.suffix).set_value(date(2026, 10, 23))
        self.app.button(key="event_save_" + self.suffix).click()
        self.run_app()
        value = self.api.request.call_args.kwargs["json"]["time"]
        self.assertEqual(value["start_date"], "2026-10-21")
        self.assertEqual(value["end_date"], "2026-10-23")
        self.assertEqual(value["precision"], "date_range")
        self.assertNotIn("event_start_clock_" + self.suffix, [item.key for item in self.app.time_input])

    def test_clear_mode_explicitly_clears_time_and_original_phrase(self):
        """主动清空同时移除当前时间原话与结构，其他事项字段仍可正常保存。"""
        self.run_app()
        self.app.selectbox(key="event_time_mode_" + self.suffix).select("清空")
        self.run_app()
        self.app.button(key="event_save_" + self.suffix).click()
        self.run_app()
        payload = self.api.request.call_args.kwargs["json"]
        self.assertIsNone(payload["time"])
        self.assertIsNone(payload["time_text"])
        self.assertEqual(payload["title"], "组会")

    def test_manual_reminder_requires_explicit_clock_and_posts_once(self):
        """手工提醒先要求明确钟点，完整选择后只POST一次且不修改事项本身。"""
        self.run_app()
        self.app.date_input(key="manual_reminder_date_" + self.suffix).set_value(date(2026, 10, 21))
        self.app.button(key="manual_reminder_save_" + self.suffix).click()
        self.run_app()
        self.api.request.assert_not_called()
        self.app.time_input(key="manual_reminder_clock_" + self.suffix).set_value(time(14, 30))
        self.app.selectbox(key="manual_reminder_channel_" + self.suffix).select("控制台记录")
        self.app.button(key="manual_reminder_save_" + self.suffix).click()
        self.run_app()
        self.api.request.assert_called_once_with("POST", "/reminders", json={
            "task_id": self.task_id, "remind_at": "2026-10-21T14:30:00+08:00", "channel": "console",
        })
        self.run_app()
        self.assertEqual(self.api.request.call_count, 1)

    def test_classify_button_is_only_model_action_and_does_not_repeat(self):
        """只有点击读取正文分类按钮才调用分类接口，重绘不会再次花费模型调用。"""
        self.run_app()
        self.app.button(key="classify_" + self.document_id).click()
        self.run_app()
        self.api.request.assert_called_once_with("POST", "/documents/" + self.document_id + "/classify")
        self.run_app()
        self.assertEqual(self.api.request.call_count, 1)

    def test_file_manual_review_does_not_call_classifier(self):
        """人工纠正文件类别和标签使用独立接口，保留当前修订号且不调用模型。"""
        self.run_app()
        self.app.selectbox(key="document_category_" + self.document_id).select("其他")
        self.app.text_input(key="document_labels_" + self.document_id).input("已读、归档")
        self.app.button(key="document_save_" + self.document_id).click()
        self.run_app()
        self.api.request.assert_called_once_with("PUT", "/documents/" + self.document_id + "/review", json={
            "expected_version": 1, "category": "其他", "labels": ["已读", "归档"],
        })

    def test_attachment_download_requires_explicit_prepare_click(self):
        """仅点击原件准备按钮才取回文件，下载缓存不会在重运行中再次访问服务。"""
        self.run_app()
        self.api.download_attachment.assert_not_called()
        self.app.button(key="attachment_prepare_" + self.message_id + "_0").click()
        self.run_app()
        self.api.download_attachment.assert_called_once_with(self.message_id, 0)
        self.run_app()
        self.assertEqual(self.api.download_attachment.call_count, 1)

    def test_conflict_error_is_safe_and_keeps_trace_reference(self):
        """过期版本冲突显示后端安全消息和追踪编号，编辑页面继续可用。"""
        self.api.request.side_effect = DashboardError("修订版本已变化，请刷新后再修改", 409, "d" * 32)
        self.run_app()
        self.app.button(key="message_save_" + self.message_id).click()
        self.run_app()
        self.assertTrue(any("版本已变化" in item.value for item in self.app.error))
        self.assertIn("Trace ID: " + "d" * 32, [item.value for item in self.app.caption])

    def test_label_parser_limits_and_clear_list(self):
        """标签支持清空、常用分隔符及去重，过长或过多时在写入前拒绝。"""
        self.assertEqual(parse_labels(""), [])
        self.assertEqual(parse_labels("研究， 待读\n研究、重要"), ["研究", "待读", "重要"])
        with self.assertRaises(ValueError):
            parse_labels("x" * 31)
        with self.assertRaises(ValueError):
            parse_labels(",".join(str(value) for value in range(11)))


if __name__ == "__main__":
    unittest.main()
