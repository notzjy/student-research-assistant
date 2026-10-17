"""第七课离线集成测试：进程内 HTTP 接口、真实图与 SQLite 配合，模型返回预设消息。"""

import importlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage


LESSONS = str(Path(__file__).resolve().parent)
if LESSONS not in sys.path:
    sys.path.insert(0, LESSONS)

lesson = importlib.import_module("07_sqlite_api")
lesson05 = importlib.import_module("05_fastapi")
lesson03 = importlib.import_module("03_tool_calling")
TaskStore = importlib.import_module("task_store").TaskStore


class FakeModel:
    """记录绑定的真实工具与图输入，按顺序返回预设模型消息，全程不联网。"""

    def __init__(self, *responses):
        """保存待返回消息，并初始化输入记录和绑定工具列表。"""
        self.responses = list(responses)
        self.inputs = []
        self.tools = []

    def bind_tools(self, tools):
        """保留模型实际收到的工具对象，供隔离测试验证各自绑定的数据库。"""
        self.tools = list(tools)
        return self

    def invoke(self, messages):
        """记录本轮完整消息并返回预设结果；额外模型调用直接使测试失败。"""
        self.inputs.append(list(messages))
        if not self.responses:
            raise AssertionError("出现未预期的模型调用")
        return self.responses.pop(0)


def make_saving_model(title="交综述"):
    """创建一轮保存和一轮总结的模型替身，每个 HTTP 请求使用一个新实例。"""
    return FakeModel(
        AIMessage(content="", tool_calls=[{
            "name": "save_task", "id": "save-one", "type": "tool_call",
            "args": {"title": title, "time_text": "周五前", "location": None},
        }]),
        AIMessage(content="已根据工具结果处理通知。"),
    )


class SQLiteAPITests(unittest.TestCase):
    """通过公开 HTTP 与存储接口验证集成，不替换真实图或 SQLite 实现。"""

    def setUp(self):
        """隔离数据库与输出，并禁止课程代码创建真实模型或回退到旧 JSON。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.database = self.folder / "assistant.sqlite3"
        self.output = io.StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.real_model = self.enterContext(patch.object(
            lesson03, "create_model", side_effect=AssertionError("离线测试禁止创建真实模型"),
        ))
        self.json_load = self.enterContext(patch.object(
            lesson03, "load_tasks", side_effect=AssertionError("SQLite 链路禁止读取旧 JSON"),
        ))
        self.json_save = self.enterContext(patch.object(
            lesson03, "save_task", side_effect=AssertionError("SQLite 链路禁止使用旧 JSON 工具"),
        ))

    def tearDown(self):
        """确认所有场景都使用注入的模型与数据库，没有绕过隔离配置。"""
        self.real_model.assert_not_called()
        self.json_load.assert_not_called()
        self.json_save.assert_not_called()

    def make_client(self, factory, database=None):
        """以指定临时数据库创建应用及进程内客户端，并托管客户端生命周期。"""
        application = lesson.create_app(
            db_path=database if database is not None else self.database,
            model_factory=factory,
        )
        return self.enterContext(TestClient(application))

    def test_message_runs_graph_and_all_queries_read_same_database(self):
        """通知经工具和报告节点写入两项，工具消息、数据库和 HTTP 数量保持一致。"""
        model = FakeModel(
            AIMessage(content="", tool_calls=[
                {"name": "save_task", "id": "meeting", "type": "tool_call", "args": {
                    "title": "开组会", "time_text": "明天下午三点", "location": "实验楼302",
                }},
                {"name": "save_task", "id": "review", "type": "tool_call", "args": {
                    "title": "交综述", "time_text": "周五前", "location": None,
                }},
            ]),
            AIMessage(content="工具已返回保存结果。"),
        )
        factory = Mock(return_value=model)
        client = self.make_client(factory)

        response = client.post("/messages", json={"text": "明天下午三点开组会，周五前交综述。"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["execution_status"], "completed")
        self.assertEqual(body["node_order"], ["model", "tools", "report", "model"])
        self.assertEqual([result["status"] for result in body["tool_results"]], ["created", "created"])
        saved = TaskStore(self.database).list_tasks()
        self.assertEqual([result["task"] for result in body["tool_results"]], saved)
        self.assertEqual(client.get("/tasks").json(), {"count": 2, "tasks": saved})
        self.assertEqual(client.get("/tasks/count").json(), {"count": 2})
        self.assertEqual(client.get("/tasks/pending/count").json(), {"count": 2})
        tool_messages = [message for message in model.inputs[1] if isinstance(message, ToolMessage)]
        self.assertEqual([message.tool_call_id for message in tool_messages], ["meeting", "review"])
        self.assertEqual([json.loads(message.content)["task"] for message in tool_messages], saved)
        factory.assert_called_once_with()

    def test_two_apps_and_bound_tools_keep_databases_isolated(self):
        """两个应用及各自模型绑定的工具都保持独立，后创建的应用不能重定向旧工具。"""
        database_b = self.folder / "assistant_b.sqlite3"
        model_a = make_saving_model("A 的任务")
        model_b = make_saving_model("B 的任务")
        client_a = self.make_client(Mock(return_value=model_a))
        client_b = self.make_client(Mock(return_value=model_b), database_b)

        self.assertEqual(client_a.post("/messages", json={"text": "保存 A。"}).status_code, 200)
        self.assertEqual(client_b.post("/messages", json={"text": "保存 B。"}).status_code, 200)
        tools_a = {tool.name: tool for tool in model_a.tools}
        tools_b = {tool.name: tool for tool in model_b.tools}
        # 第二个应用执行后，再调用第一个模型持有的工具，验证闭包未被全局替换。
        tools_a["save_task"].invoke({"title": "A 工具独有任务"})
        tools_b["save_task"].invoke({"title": "B 工具独有任务"})

        titles_a = {task["title"] for task in client_a.get("/tasks").json()["tasks"]}
        titles_b = {task["title"] for task in client_b.get("/tasks").json()["tasks"]}
        self.assertEqual(titles_a, {"A 的任务", "A 工具独有任务"})
        self.assertEqual(titles_b, {"B 的任务", "B 工具独有任务"})
        self.assertEqual(client_a.get("/tasks/pending/count").json(), {"count": 2})
        self.assertEqual(client_b.get("/tasks/count").json(), {"count": 2})

    def test_recreated_app_reuses_saved_task_without_resetting_completion(self):
        """重建应用后任务仍存在，重复通知复用 ID，完成后重投保留 completed 状态。"""
        factory = Mock(side_effect=make_saving_model)
        first_client = self.make_client(factory)
        first = first_client.post("/messages", json={"text": "周五前交综述。"})
        self.assertEqual(first.status_code, 200)
        task_id = first.json()["tool_results"][0]["task"]["id"]

        reopened_client = self.make_client(factory)
        self.assertEqual(reopened_client.get("/tasks/count").json(), {"count": 1})
        duplicate = reopened_client.post("/messages", json={"text": "周五前交综述。"})
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.json()["tool_results"][0]["status"], "exists")
        TaskStore(self.database).mark_completed(task_id)
        after_completion = reopened_client.post("/messages", json={"text": "周五前交综述。"})

        self.assertEqual(after_completion.status_code, 200)
        result = after_completion.json()["tool_results"][0]
        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["task"]["id"], task_id)
        self.assertEqual(result["task"]["status"], "completed")
        self.assertEqual(reopened_client.get("/tasks/count").json(), {"count": 1})
        self.assertEqual(reopened_client.get("/tasks/pending/count").json(), {"count": 0})
        self.assertEqual(factory.call_count, 3)

    def test_health_and_queries_do_not_call_model_factory(self):
        """健康检查和三种查询只访问本地数据，不创建模型或依赖密钥配置。"""
        factory = Mock(side_effect=AssertionError("查询不能调用模型工厂"))
        client = self.make_client(factory)

        expected = {
            "/health": {"status": "ok"},
            "/tasks": {"count": 0, "tasks": []},
            "/tasks/count": {"count": 0},
            "/tasks/pending/count": {"count": 0},
        }
        for route, body in expected.items():
            with self.subTest(route=route):
                response = client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), body)
        factory.assert_not_called()

    def test_invalid_or_unknown_tools_return_error_messages_without_writing(self):
        """非法参数与未知工具均回传对应错误，不因模型声称成功而变成保存成功。"""
        calls = [
            {"name": "save_task", "id": "invalid", "type": "tool_call", "args": {"title": ""}},
            {"name": "unregistered_tool", "id": "unknown", "type": "tool_call", "args": {}},
        ]
        for call in calls:
            with self.subTest(tool=call["name"]):
                model = FakeModel(AIMessage(content="", tool_calls=[call]), AIMessage(content="已经保存。"))
                client = self.make_client(Mock(return_value=model))

                response = client.post("/messages", json={"text": "保存通知中的事项。"})

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["execution_status"], "tool_error")
                self.assertEqual(body["node_order"], ["model", "tools", "report", "model"])
                self.assertEqual(body["tool_results"][0]["status"], "error")
                self.assertIsNone(body["tool_results"][0]["task"])
                tool_messages = [message for message in model.inputs[1] if isinstance(message, ToolMessage)]
                self.assertEqual(len(tool_messages), 1)
                self.assertEqual(tool_messages[0].tool_call_id, call["id"])
                self.assertEqual(json.loads(tool_messages[0].content)["status"], "error")
                self.assertEqual(TaskStore(self.database).list_tasks(), [])

    def test_query_database_errors_return_safe_500_responses(self):
        """数据库查询故障映射成 HTTP 500，列表与数量接口不泄露原始错误细节。"""
        store = TaskStore(self.database)
        factory = Mock(side_effect=AssertionError("查询不能调用模型工厂"))
        client = self.enterContext(TestClient(lesson05.create_app(model_factory=factory, store=store)))
        with patch.object(store, "list_tasks", side_effect=sqlite3.OperationalError("private-db-detail")):
            for route in ("/tasks", "/tasks/count"):
                with self.subTest(route=route):
                    response = client.get(route)
                    self.assertEqual(response.status_code, 500)
                    self.assertNotIn("private-db-detail", response.text)
        pending_client = self.make_client(factory)
        with patch.object(TaskStore, "count_pending", side_effect=sqlite3.OperationalError("private-db-detail")):
            response = pending_client.get("/tasks/pending/count")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private-db-detail", response.text)
        factory.assert_not_called()

    def test_database_error_in_report_returns_500_with_saved_task_preserved(self):
        """工具已提交后报告查询失败应返回安全错误，已保存项仍可重新查询。"""
        store = TaskStore(self.database)
        client = self.enterContext(TestClient(lesson05.create_app(
            model_factory=Mock(side_effect=make_saving_model), store=store,
        )))

        with patch.object(store, "list_tasks", side_effect=sqlite3.OperationalError("private-db-detail")):
            response = client.post("/messages", json={"text": "周五前交综述。"})

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private-db-detail", response.text)
        self.assertEqual(len(TaskStore(self.database).list_tasks()), 1)
        self.assertEqual(client.get("/tasks/count").json(), {"count": 1})


if __name__ == "__main__":
    unittest.main()
