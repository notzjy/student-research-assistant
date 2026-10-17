"""手工收件表单测试：明确时间、稳定来源ID、附件归档和正文处理，不访问真实后端。"""

import io
import unittest
from datetime import date, time
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from dashboard_api import DashboardAPI, DashboardError
from dashboard_intake import build_manual_payload, organize_manual_intake


class UploadedBytes(io.BytesIO):
    """提供Streamlit上传对象所需的文件名、类型和大小，不读取本机私人文件。"""

    def __init__(self, filename, data, media_type="application/octet-stream"):
        """保存合成字节供表单测试读取。"""
        super().__init__(data)
        self.name, self.type, self.size = filename, media_type, len(data)


class ManualIntakeUITests(unittest.TestCase):
    """以真实Streamlit控件与HTTP适配器替身验证普通用户导入流程。"""

    def setUp(self):
        """建立独立表单页面，避免其他消息列表GET干扰零请求断言。"""
        self.api = Mock(spec=DashboardAPI)
        self.api.last_trace_id = None
        self.enterContext(patch("dashboard_api.DashboardAPI", return_value=self.api))
        self.app = AppTest.from_string(
            "from dashboard_api import DashboardAPI\nfrom dashboard_intake import render_manual_intake\nrender_manual_intake(DashboardAPI('http://127.0.0.1:8002'))",
            default_timeout=10,
        )

    def run_app(self):
        """运行一次真实表单，并把控件或脚本异常视为失败。"""
        self.app.run()
        self.assertFalse(list(self.app.exception), list(self.app.exception))

    def fill_message(self):
        """填写完整合成原文和明确来源时间，不触发提交。"""
        self.app.text_area(key="paste_text").set_value("明天15:00在实验楼302开组会，附件两篇参考资料。")
        self.app.text_input(key="paste_sender_id").set_value("mentor-demo")
        self.app.text_input(key="paste_sender_name").set_value("示例老师")
        self.app.date_input(key="paste_date").set_value(date(2026, 10, 17))
        self.app.time_input(key="paste_time").set_value(time(10, 30))

    def submit(self):
        """模拟用户明确点击整理按钮，模型与写入只能由该动作触发。"""
        self.app.button(key="paste_submit").click()
        self.run_app()

    def test_default_form_is_empty_and_makes_no_http_requests(self):
        """初始日期钟点为空、刚收到默认关闭，渲染与重运行均不访问后端。"""
        self.run_app()
        self.assertIsNone(self.app.date_input(key="paste_date").value)
        self.assertIsNone(self.app.time_input(key="paste_time").value)
        self.assertFalse(self.app.checkbox(key="paste_use_now").value)
        self.run_app()
        self.api.request.assert_not_called()
        self.api.upload_attachment.assert_not_called()

    def test_missing_source_time_blocks_submission_before_http(self):
        """缺原消息时间时不能把服务器现在时间静默填入。"""
        self.run_app()
        self.app.text_area(key="paste_text").set_value("明天下午组会。")
        self.app.text_input(key="paste_sender_id").set_value("mentor-demo")
        self.submit()
        self.api.request.assert_not_called()
        self.assertTrue(any("补齐原消息日期" in item.value for item in self.app.error))

    def test_plain_message_payload_has_source_time_and_stable_duplicate_identity(self):
        """普通表单发送正式来源字段，重复整理保持同一个来源ID且无隐式重运行请求。"""
        self.api.request.return_value = {"status": "processed", "message": {"message_id": "m" * 24}}
        self.run_app()
        self.fill_message()
        self.submit()
        first = self.api.request.call_args.kwargs["json"]
        self.assertEqual((first["source"], first["account_id"]), ("manual", "manual-paste"))
        self.assertEqual(first["sent_at"], "2026-10-17T10:30:00+08:00")
        self.assertEqual(first["sender_id"], "mentor-demo")
        self.assertTrue(first["source_message_id"].startswith("paste-"))
        self.assertEqual(first["attachments"], [])
        self.run_app()
        self.assertEqual(self.api.request.call_count, 1)
        self.api.request.return_value = {"status": "exists", "message": {"message_id": "m" * 24}}
        self.submit()
        self.assertEqual(self.api.request.call_args.kwargs["json"], first)
        self.assertTrue(any("复用原事项" in item.value for item in self.app.caption))

    def test_attachments_archive_all_formats_and_only_index_supported_documents(self):
        """PDF归档后索引并正文分类，ZIP仅保存原件，界面不声称读取ZIP正文。"""
        self.api.request.side_effect = self.respond
        self.api.upload.return_value = {"status": "indexed", "document": {"document_id": "doc-1"}}
        files = [UploadedBytes("研究.pdf", b"%PDF-synthetic", "application/pdf"), UploadedBytes("数据.zip", b"PK-archive")]
        with patch("dashboard_intake.st.file_uploader", return_value=files):
            self.run_app()
            self.fill_message()
            self.submit()
        self.assertEqual(self.api.upload_attachment.call_count, 2)
        self.assertEqual([call.args[1] for call in self.api.upload_attachment.call_args_list], [0, 1])
        self.api.upload.assert_called_once_with("研究.pdf", b"%PDF-synthetic", "m" * 24, 0)
        self.assertIn(("POST", "/documents/doc-1/classify"), [call.args[:2] for call in self.api.request.call_args_list])
        self.assertTrue(any("无法提取正文" in item.value for item in self.app.caption))

    def respond(self, method, path, **kwargs):
        """响应合成消息和正文分类，拒绝表单未经约定的其他操作。"""
        if (method, path) == ("POST", "/messages"):
            return {"status": "processed", "message": {"message_id": "m" * 24}}
        if (method, path) == ("POST", "/documents/doc-1/classify"):
            return {"status": "classified"}
        raise AssertionError("非预期表单请求")

    def test_partial_attachment_failure_retains_message_and_safe_trace(self):
        """原件成功、索引失败时保留消息编号与已保存状态，允许之后重试。"""
        files = [{"filename": "研究.pdf", "data": b"pdf", "media_type": "application/pdf"}]
        payload = build_manual_payload("参考附件。", "mentor-demo", "", date(2026, 10, 17), time(10, 30), False, files)
        self.api.request.return_value = {"status": "processed", "message": {"message_id": "m" * 24}}
        self.api.upload.side_effect = DashboardError("文件索引暂未完成。", 502, "t" * 32)
        result = organize_manual_intake(self.api, payload, files)
        self.assertFalse(result["complete"])
        self.assertEqual(result["message_id"], "m" * 24)
        self.assertTrue(result["attachments"][0]["archived"])
        self.assertEqual(result["attachments"][0]["trace_id"], "t" * 32)
        self.assertFalse(result["attachments"][0]["indexed"])


if __name__ == "__main__":
    unittest.main()
