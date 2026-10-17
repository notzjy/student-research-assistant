"""本地界面交互测试：通过AppTest运行真实页面，HTTP边界只返回合成资料。"""

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from dashboard_api import DashboardAPI, DashboardError


ROOT = Path(__file__).resolve().parents[1]


class DashboardUITests(unittest.TestCase):
    """验证界面筛选、显式写入及会话复用，不连接模型、数据库、邮箱或通知。"""

    def setUp(self):
        """用合成服务响应替换HTTP适配器，保留Streamlit真实重运行和控件状态。"""
        self.calls = []
        self.read_error = False
        self.messages = [
            {
                "message_id": "demo-research", "processing_status": "completed",
                "source_message": {
                    "source": "manual", "sender_name": "示例老师",
                    "sent_at": "2026-10-16T10:00:00+08:00",
                    "text": "<script>原消息只按纯文本显示</script>；周三前在实验楼302交综述。",
                },
                "analysis": {"category": "研究", "summary": "交综述并准备组会"},
                "role_match": {"role": "导师", "important": True},
                "events": [
                    {
                        "task_id": "a" * 16, "title": "交综述", "status": "pending",
                        "time_text": "周三前", "location": "实验楼302", "evidence": "周三前交综述",
                        "time": {"precision": "date", "time_kind": "deadline", "date": "2026-10-14"},
                    },
                    {
                        "task_id": "b" * 16, "title": "组会", "status": "pending",
                        "time_text": "明天下午三点", "location": None,
                        "time": {"precision": "minute", "time_kind": "point", "at": "2026-10-17T15:00:00+08:00"},
                    },
                ],
                "attachments": [{"filename": "reference-a.pdf", "availability": "mentioned"}],
            },
            {
                "message_id": "demo-life", "processing_status": "completed",
                "source_message": {"source": "manual", "text": "领取学生卡"},
                "analysis": {"category": "学生生活", "summary": "领取学生卡"},
                "role_match": {"important": False}, "events": [], "attachments": [],
            },
        ]
        self.api = Mock(spec=DashboardAPI)
        self.api.base_url = "http://127.0.0.1:8002"
        self.api.last_trace_id = None
        self.api.request.side_effect = self.respond
        self.enterContext(patch("dashboard_api.DashboardAPI", return_value=self.api))
        self.app = AppTest.from_file(ROOT / "assistant_dashboard.py", default_timeout=10)

    def respond(self, method, path, **kwargs):
        """记录HTTP语义并返回最小合成数据，未约定的请求直接暴露为测试错误。"""
        self.calls.append((method, path, deepcopy(kwargs)))
        if method == "GET" and path == "/messages":
            if self.read_error:
                raise DashboardError("主服务暂不可用，请稍后重试。", 503, "c" * 32)
            params = kwargs.get("params") or {}
            selected = [
                item for item in self.messages
                if (not params.get("category") or item["analysis"]["category"] == params["category"])
                and (not params.get("important_only") or item["role_match"].get("important"))
            ]
            return {"messages": deepcopy(selected)}
        if method == "GET" and path == "/documents":
            return {"documents": [{"document_id": "doc-demo", "filename": "实验记录.md", "status": "indexed", "chunk_count": 1}]}
        if method == "GET" and path == "/reminders":
            return {"reminders": []}
        if method == "GET" and path == "/reminders/policy":
            return {"enabled": False, "lead_minutes": 30, "date_only_time": None, "channel": "console"}
        if method == "GET" and path == "/roles":
            return {"roles": []}
        if method == "GET" and path == "/mail/status":
            return {"enabled": False, "configured": False}
        if method == "POST" and path.endswith("/complete"):
            return {"status": "completed", "task": {"id": path.split("/")[2], "status": "completed"}}
        if method == "POST" and path == "/documents/ask":
            return {"status": "answered", "answer": "实验结果为0.85。", "citations": [{"filename": "实验记录.md", "page": None, "quote": "实验结果为0.85。"}]}
        if method == "POST" and path == "/documents/search":
            return {"hits": [{"filename": "实验记录.md", "text": "实验结果为0.85。", "score": 0.9}]}
        if method == "POST" and path == "/invoke":
            return {"reply": "已收到：" + kwargs["json"]["message"], "thread_id": kwargs["json"]["thread_id"]}
        raise AssertionError(f"界面发出了非预期请求：{method} {path}")

    def run_app(self):
        """执行一次真实界面重运行，并将任何脚本异常作为测试失败报告。"""
        self.app.run()
        self.assertFalse(list(self.app.exception), list(self.app.exception))

    def navigate(self, page):
        """选择唯一导航页并重运行，验证只会执行选中页面。"""
        self.app.sidebar.radio[0].set_value(page)
        self.run_app()

    def click(self, label):
        """按可见文字点击一个明确按钮，避免依赖控件在页面中的排列位置。"""
        button = next(item for item in self.app.button if item.label == label)
        button.click()
        self.run_app()

    def writes(self):
        """提取所有实际写入意图，便于核对重运行没有隐式重复请求。"""
        return [call for call in self.calls if call[0] != "GET"]

    def test_default_page_reads_only_messages_and_retains_original_text(self):
        """默认进入消息页只读一次，邮件原文进入纯文本控件，附件仍显示线索状态。"""
        self.run_app()
        self.assertEqual(self.calls, [("GET", "/messages", {"params": {"important_only": False, "limit": 100}})])
        texts = [item.value for item in self.app.text]
        self.assertIn(self.messages[0]["source_message"]["text"], texts)
        self.assertTrue(any("reference-a.pdf" in text and "mentioned" in text for text in texts))
        self.assertFalse(self.writes())

    def test_category_and_important_filters_reach_backend_without_writes(self):
        """分类及重点联系人控件改变服务查询条件，筛选过程不会调用分析或保存接口。"""
        self.run_app()
        self.app.selectbox(key="message_category").select("研究")
        self.app.checkbox(key="important_only").check()
        self.run_app()
        self.assertEqual(self.calls[-1], ("GET", "/messages", {"params": {"important_only": True, "limit": 100, "category": "研究"}}))
        self.assertNotIn("领取学生卡", [item.value for item in self.app.text])
        self.assertFalse(self.writes())

    def test_time_precision_and_location_keep_distinct_visible_badges(self):
        """日期级截止不补造钟点，具体时刻与地点使用不同颜色并同时保留说明文字。"""
        self.run_app()
        badges = [item.value for item in self.app.markdown]
        for expected in (":orange-badge[截止]", ":gray-badge[日期 · 钟点未知]", ":blue-badge[具体时刻]", ":violet-badge[地点]"):
            self.assertIn(expected, badges)
        texts = [item.value for item in self.app.text]
        self.assertIn("2026-10-14", texts)
        self.assertIn("2026-10-17T15:00:00+08:00", texts)
        self.assertIn("实验楼302", texts)
        self.assertFalse(any(value.startswith("2026-10-14T") for value in texts))

    def test_complete_is_sent_once_and_does_not_repeat_on_rerun(self):
        """仅点击指定事项才完成它；即使列表仍返回旧状态，重运行也不重复提交完成请求。"""
        self.run_app()
        self.assertFalse(self.writes())
        self.app.button(key="complete_demo-research_0").click()
        self.run_app()
        expected = [("POST", "/tasks/" + "a" * 16 + "/complete", {})]
        self.assertEqual(self.writes(), expected)
        self.run_app()
        self.assertEqual(self.writes(), expected)
        self.assertNotIn("complete_demo-research_0", [item.key for item in self.app.button])
        self.assertIn("complete_demo-research_1", [item.key for item in self.app.button])

    def test_research_search_and_model_answer_require_separate_submissions(self):
        """切页和填写问题不调用模型；检索与问答各只发送一次请求，回答保留真实引用字段。"""
        self.run_app()
        self.navigate("研究资料")
        self.app.text_area(key="research_question").input("实验结果是多少？")
        # 表单输入先留在客户端缓冲区，与提交按钮一起送达脚本，符合真实浏览器行为。
        self.assertFalse(self.writes())
        self.click("仅检索片段")
        self.assertEqual(self.writes()[0], ("POST", "/documents/search", {"json": {"query": "实验结果是多少？", "top_k": 5}}))
        self.click("基于文件回答（调用模型）")
        self.assertEqual(self.writes()[1], ("POST", "/documents/ask", {"json": {"question": "实验结果是多少？", "top_k": 5}}))
        self.assertIn("引用：实验记录.md", [item.value for item in self.app.caption])
        self.assertIn("实验结果为0.85。", [item.value for item in self.app.text])
        self.run_app()
        self.assertEqual(len(self.writes()), 2)

    def test_chat_reuses_thread_until_explicit_new_conversation(self):
        """连续两轮共用同一线程，显式新建才更换线程且不会自动发送任何消息。"""
        self.run_app()
        self.navigate("对话助理")
        original = self.app.session_state["thread_id"]
        self.assertFalse(self.writes())
        for text in ("请记住项目代号星河", "项目代号是什么？"):
            self.app.text_area(key="chat_message").input(text)
            self.click("发送")
        self.assertEqual([call[2]["json"]["thread_id"] for call in self.writes()], [original, original])
        self.run_app()
        self.assertEqual(len(self.writes()), 2)
        self.click("新建对话")
        newer = self.app.session_state["thread_id"]
        self.assertNotEqual(newer, original)
        self.assertEqual(self.app.session_state["chat_entries"], [])
        self.assertEqual(len(self.writes()), 2)
        self.app.text_area(key="chat_message").input("这是新对话")
        self.click("发送")
        self.assertEqual(self.writes()[-1][2]["json"]["thread_id"], newer)

    def test_service_error_keeps_trace_and_recovers_without_restart(self):
        """服务失败时展示安全错误和Trace，服务恢复后同一界面会话可重新读取。"""
        self.read_error = True
        self.run_app()
        self.assertEqual(self.app.error[0].value, "主服务暂不可用，请稍后重试。")
        self.assertIn("Trace ID: " + "c" * 32, [item.value for item in self.app.caption])
        self.read_error = False
        self.run_app()
        self.assertFalse(list(self.app.error))
        self.assertIn("交综述", [item.value for item in self.app.text])
        self.assertFalse(self.writes())

    def test_reminder_page_only_reads_configuration_without_email_sync(self):
        """打开提醒页只读取已有记录和安全状态，不自动保存规则或同步真实邮箱。"""
        self.run_app()
        self.calls.clear()
        self.navigate("提醒与角色")
        self.assertEqual([(method, path) for method, path, _ in self.calls], [
            ("GET", "/reminders"), ("GET", "/reminders/policy"), ("GET", "/roles"), ("GET", "/mail/status"),
        ])
        self.run_app()
        self.assertFalse(self.writes())


if __name__ == "__main__":
    unittest.main()
