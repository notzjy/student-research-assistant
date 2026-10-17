"""第五课离线测试：用进程内 HTTP 请求验证 API、真实图和临时文件。"""

import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError
from openai import APIConnectionError, APIError, APITimeoutError


LESSONS = str(Path(__file__).resolve().parent)
if LESSONS not in sys.path:
    sys.path.insert(0, LESSONS)
lesson = importlib.import_module("05_fastapi")


class FakeModel:
    """依次返回预设消息，记录图的模型输入，完全不访问网络。"""

    def __init__(self, *responses):
        """保存预设响应，并准备记录工具定义和每轮消息。"""
        self.responses = list(responses)
        self.inputs = []
        self.tools = []

    def bind_tools(self, tools):
        """记录提供给模型的工具，返回自身供图节点调用。"""
        self.tools = list(tools)
        return self

    def invoke(self, messages):
        """返回下一条预设消息；超出预期的额外调用直接使测试失败。"""
        self.inputs.append(list(messages))
        if not self.responses:
            raise AssertionError("出现未预期的模型调用")
        return self.responses.pop(0)


def make_saving_model():
    """为每个 HTTP 请求新建模型替身，先请求保存两项待办，再返回结语。"""
    return FakeModel(
        AIMessage(content="", tool_calls=[
            {
                "name": "save_task", "id": "meeting", "type": "tool_call",
                "args": {"title": "开组会", "time_text": "明天下午三点", "location": "实验楼302"},
            },
            {
                "name": "save_task", "id": "review", "type": "tool_call",
                "args": {"title": "交综述", "time_text": "周五前", "location": None},
            },
        ]),
        AIMessage(content="通知已处理，请以工具结果核对保存情况。"),
    )


class FastAPITests(unittest.TestCase):
    """验证 HTTP 合约与保存结果，禁止创建真实模型或访问真实待办文件。"""

    def setUp(self):
        """将待办文件替换为临时路径，并捕获课程图执行时的终端输出。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.task_file = Path(temporary.name) / "local-data" / "lesson03_tasks.json"
        self.enterContext(patch.object(lesson.lesson03, "TASK_FILE", self.task_file))
        self.real_model = self.enterContext(patch.object(
            lesson.lesson03, "create_model",
            side_effect=AssertionError("离线 API 测试不得创建真实模型"),
        ))
        self.enterContext(redirect_stdout(io.StringIO()))

    def tearDown(self):
        """确保每个场景都没有绕过注入模型而创建真实客户端。"""
        self.real_model.assert_not_called()

    def make_client(self, factory):
        """以给定模型工厂创建应用，返回生命周期由测试管理的进程内客户端。"""
        return self.enterContext(TestClient(lesson.create_app(model_factory=factory)))

    def test_health_and_task_queries_do_not_create_a_model(self):
        """健康检查与空待办查询可独立运行，不调用模型工厂或写文件。"""
        factory = Mock(side_effect=make_saving_model)
        client = self.make_client(factory)

        health = client.get("/health")
        tasks = client.get("/tasks")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(tasks.status_code, 200)
        self.assertEqual(tasks.json(), {"count": 0, "tasks": []})
        factory.assert_not_called()
        self.assertFalse(self.task_file.exists())

    def test_invalid_request_bodies_return_422_before_model_creation(self):
        """缺失、空白、类型错误及额外字段都应在运行图之前被请求校验拒绝。"""
        factory = Mock(side_effect=make_saving_model)
        client = self.make_client(factory)
        invalid_bodies = [
            {}, {"text": ""}, {"text": " \t\n"}, {"text": 123},
            {"text": None}, {"text": ["通知"]}, {"text": "通知", "unexpected": True},
        ]

        for body in invalid_bodies:
            with self.subTest(body=body):
                response = client.post("/messages", json=body)
                self.assertEqual(response.status_code, 422)
        factory.assert_not_called()
        self.assertFalse(self.task_file.exists())

    def test_valid_message_runs_real_graph_and_reports_actual_saved_tasks(self):
        """验证 HTTP 请求经过真实图与报告节点，返回结果与临时文件内容一致。"""
        model = make_saving_model()
        factory = Mock(return_value=model)
        client = self.make_client(factory)
        notice = lesson.lesson03.NOTICE

        response = client.post("/messages", json={"text": "  " + notice + "  "})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["execution_status"], "completed")
        self.assertEqual(body["node_order"], ["model", "tools", "report", "model"])
        self.assertEqual([item["status"] for item in body["tool_results"]], ["created", "created"])
        saved = json.loads(self.task_file.read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 2)
        self.assertEqual([item["task"] for item in body["tool_results"]], saved)
        self.assertEqual(model.inputs[0][-1].content, notice)
        self.assertEqual(len(model.inputs), 2)
        factory.assert_called_once_with()
        queried = client.get("/tasks")
        self.assertEqual(queried.status_code, 200)
        self.assertEqual(queried.json(), {"count": 2, "tasks": saved})

    def test_repeated_message_returns_exists_without_duplicate_tasks(self):
        """重复提交相同事项时，新模型请求仍返回已存在结果，任务数量保持不变。"""
        factory = Mock(side_effect=make_saving_model)
        client = self.make_client(factory)
        request_body = {"text": lesson.lesson03.NOTICE}

        first = client.post("/messages", json=request_body)
        original = self.task_file.read_bytes()
        second = client.post("/messages", json=request_body)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual([item["status"] for item in second.json()["tool_results"]], [
            "exists", "exists",
        ])
        self.assertEqual(second.json()["execution_status"], "completed")
        self.assertEqual(self.task_file.read_bytes(), original)
        self.assertEqual(client.get("/tasks").json()["count"], 2)
        self.assertEqual(factory.call_count, 2)

    def test_no_tool_call_returns_no_tools_without_writing(self):
        """普通通知不触发工具时，接口明确返回无工具执行且不创建待办文件。"""
        model = FakeModel(AIMessage(content="参考资料已收到，没有明确待办。"))
        client = self.make_client(Mock(return_value=model))

        response = client.post("/messages", json={"text": "参考资料已收到，谢谢。"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["execution_status"], "no_tools")
        self.assertEqual(body["tool_results"], [])
        self.assertEqual(body["node_order"], ["model"])
        self.assertEqual(len(model.inputs), 1)
        self.assertFalse(self.task_file.exists())

    def test_invalid_tool_arguments_report_error_without_writing(self):
        """模型提出非法参数时，以工具结果标记失败，即使模型结语声称保存成功。"""
        model = FakeModel(
            AIMessage(content="", tool_calls=[{
                "name": "save_task", "id": "invalid", "type": "tool_call",
                "args": {"title": ""},
            }]),
            AIMessage(content="已经保存成功。"),
        )
        client = self.make_client(Mock(return_value=model))

        response = client.post("/messages", json={"text": "保存一个事项。"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["execution_status"], "tool_error")
        self.assertEqual(len(body["tool_results"]), 1)
        self.assertEqual(body["tool_results"][0]["status"], "error")
        self.assertIsNone(body["tool_results"][0]["task"])
        self.assertTrue(body["tool_results"][0]["error"])
        self.assertFalse(self.task_file.exists())

    def test_missing_configuration_returns_503_before_graph_execution(self):
        """未注入模型且必要环境配置为空时，返回服务不可用，不进入图执行。"""
        client = self.make_client(None)
        missing_configuration = {
            "COMPATIBLE_MODEL": "", "COMPATIBLE_BASE_URL": "", "COMPATIBLE_API_KEY": "",
        }

        with patch.dict(os.environ, missing_configuration), patch.object(lesson.lesson04, "run_graph") as graph:
            response = client.post("/messages", json={"text": "周五前交综述。"})

        self.assertEqual(response.status_code, 503)
        graph.assert_not_called()
        self.assertFalse(self.task_file.exists())

    def test_graph_and_provider_failures_have_distinct_safe_http_statuses(self):
        """流程超限、模型失败与超时映射到不同状态码，不泄露提供商原始错误。"""
        client = self.make_client(Mock(side_effect=make_saving_model))
        request = httpx.Request("POST", "https://model.invalid/chat/completions")
        errors = [
            (GraphRecursionError("private-provider-detail"), 409),
            (APIError("private-provider-detail", request=request, body=None), 502),
            (APIConnectionError(request=request), 502),
            (APITimeoutError(request=request), 504),
        ]

        for error, expected_status in errors:
            with self.subTest(error=type(error).__name__):
                with patch.object(lesson.lesson04, "run_graph", side_effect=error):
                    response = client.post("/messages", json={"text": "周五前交综述。"})
                self.assertEqual(response.status_code, expected_status)
                self.assertIn("/tasks", response.json()["detail"])
                self.assertNotIn("private-provider-detail", response.text)
        self.assertFalse(self.task_file.exists())


if __name__ == "__main__":
    unittest.main()
