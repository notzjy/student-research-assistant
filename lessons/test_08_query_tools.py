"""第八课离线测试：模型选择查询工具，经真实图和 HTTP 查询临时 SQLite 数据。"""

import importlib
import io
import json
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

lesson07 = importlib.import_module("07_sqlite_api")
lesson05 = importlib.import_module("05_fastapi")
lesson03 = importlib.import_module("03_tool_calling")
TaskStore = importlib.import_module("task_store").TaskStore


class FakeModel:
    """记录工具定义与各轮消息，按顺序返回预设结果，不创建真实模型客户端。"""

    def __init__(self, *responses):
        """保存预设模型消息，并初始化实际输入与绑定工具的记录。"""
        self.responses = list(responses)
        self.inputs = []
        self.tools = []

    def bind_tools(self, tools):
        """保留提供给模型的真实工具对象，以便验证能力和数据库绑定。"""
        self.tools = list(tools)
        return self

    def invoke(self, messages):
        """记录当前图消息并返回下一条预设响应，禁止未预期的额外调用。"""
        self.inputs.append(list(messages))
        if not self.responses:
            raise AssertionError("出现未预期的模型调用")
        return self.responses.pop(0)


def make_query_model(arguments, reply="已读取实际待办列表。"):
    """创建先调用查询工具、再总结结果的模型替身，参数由测试场景明确指定。"""
    return FakeModel(
        AIMessage(content="", tool_calls=[{
            "name": "list_tasks", "id": "query-one", "type": "tool_call", "args": arguments,
        }]),
        AIMessage(content=reply),
    )


class QueryToolTests(unittest.TestCase):
    """用真实临时数据库验证只读查询、工具回传和接口响应，不访问旧 JSON。"""

    def setUp(self):
        """为每个测试隔离存储，禁止读取密钥、创建真实模型或回退到 JSON 工具。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.database = self.folder / "assistant.sqlite3"
        self.store = TaskStore(self.database)
        self.enterContext(redirect_stdout(io.StringIO()))
        self.real_model = self.enterContext(patch.object(
            lesson03, "create_model", side_effect=AssertionError("离线测试禁止创建真实模型"),
        ))
        self.json_load = self.enterContext(patch.object(
            lesson03, "load_tasks", side_effect=AssertionError("查询工具禁止访问旧 JSON"),
        ))
        self.json_save = self.enterContext(patch.object(
            lesson03, "save_task", side_effect=AssertionError("查询工具禁止保存到旧 JSON"),
        ))

    def tearDown(self):
        """确认测试只使用注入模型和临时数据库，没有走真实模型或 JSON 路径。"""
        self.real_model.assert_not_called()
        self.json_load.assert_not_called()
        self.json_save.assert_not_called()

    def make_client(self, factory, database=None):
        """创建指定数据库的应用和进程内客户端，并托管客户端资源。"""
        app = lesson07.create_app(
            db_path=self.database if database is None else database,
            model_factory=factory,
        )
        return self.enterContext(TestClient(app))

    def seed_mixed_tasks(self):
        """写入两项未完成和一项已完成待办，返回数据库中的实际记录。"""
        self.store.save_task("阅读参考论文", "周五前")
        completed = self.store.save_task("提交旧材料")["task"]
        self.store.mark_completed(completed["id"])
        self.store.save_task("参加组会", "明天下午三点", "实验楼302")
        return self.store.list_tasks()

    def test_query_filters_return_real_records_without_changing_database(self):
        """pending、completed 和 all 查询与数据库一致，调用关联与工具名准确且不改状态。"""
        original = self.seed_mixed_tasks()
        for status in ("pending", "completed", "all"):
            with self.subTest(status=status):
                model = make_query_model({"status": status})
                client = self.make_client(Mock(return_value=model))
                expected = original if status == "all" else [task for task in original if task["status"] == status]

                response = client.post("/messages", json={"text": "查询对应状态的待办。"})

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["execution_status"], "completed")
                self.assertEqual(body["tool_names"], ["list_tasks"])
                self.assertEqual(body["node_order"], ["model", "tools", "report", "model"])
                result = body["tool_results"][0]
                self.assertEqual(result, {"status": "queried", "filter_status": status, "count": len(expected), "tasks": expected})
                tool_messages = [message for message in model.inputs[1] if isinstance(message, ToolMessage)]
                self.assertEqual(len(tool_messages), 1)
                self.assertEqual(tool_messages[0].tool_call_id, "query-one")
                self.assertEqual(tool_messages[0].name, "list_tasks")
                self.assertEqual(json.loads(tool_messages[0].content), result)
                self.assertEqual({tool.name for tool in model.tools}, {"save_task", "list_tasks", "complete_task"})
                self.assertEqual(TaskStore(self.database).list_tasks(), original)

    def test_empty_database_returns_successful_zero_count(self):
        """没有待办时查询正常返回 queried 和空列表，不把零条结果当成工具失败。"""
        model = make_query_model({"status": "all"})
        client = self.make_client(Mock(return_value=model))

        response = client.post("/messages", json={"text": "查看所有待办。"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["execution_status"], "completed")
        self.assertEqual(response.json()["tool_results"], [{
            "status": "queried", "filter_status": "all", "count": 0, "tasks": [],
        }])
        self.assertEqual(self.store.list_tasks(), [])

    def test_no_arguments_default_to_pending(self):
        """模型不给筛选参数时采用 pending 默认值，并排除已经完成的任务。"""
        original = self.seed_mixed_tasks()
        expected = [task for task in original if task["status"] == "pending"]
        client = self.make_client(Mock(return_value=make_query_model({})))

        response = client.post("/messages", json={"text": "我还有哪些待办？"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tool_results"], [{
            "status": "queried", "filter_status": "pending", "count": 2, "tasks": expected,
        }])
        self.assertEqual(self.store.list_tasks(), original)

    def test_invalid_filters_and_extra_fields_return_tool_error(self):
        """非法状态、错误类型和额外字段被严格拒绝，失败仍保留实际工具名且不写库。"""
        original = self.seed_mixed_tasks()
        invalid_arguments = [
            {"status": "done"}, {"status": 123}, {"status": None},
            {"status": "pending", "unexpected": "不允许的字段"},
        ]
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                model = make_query_model(arguments)
                client = self.make_client(Mock(return_value=model))

                response = client.post("/messages", json={"text": "查询待办。"})

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["execution_status"], "tool_error")
                self.assertEqual(body["tool_names"], ["list_tasks"])
                self.assertEqual(body["tool_results"][0]["status"], "error")
                self.assertIsNone(body["tool_results"][0]["task"])
                self.assertTrue(body["tool_results"][0]["error"])
                self.assertEqual(self.store.list_tasks(), original)

    def test_save_then_query_across_rounds_preserves_messages_and_results(self):
        """先保存再查询时，下一轮能读取已提交项，工具消息和报告路径按真实顺序累积。"""
        model = FakeModel(
            AIMessage(content="", tool_calls=[{
                "name": "save_task", "id": "save-first", "type": "tool_call",
                "args": {"title": "提交综述", "time_text": "周五前", "location": None},
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "list_tasks", "id": "query-after-save", "type": "tool_call",
                "args": {"status": "pending"},
            }]),
            AIMessage(content="保存结果和当前待办已核对。"),
        )
        client = self.make_client(Mock(return_value=model))

        response = client.post("/messages", json={"text": "周五前提交综述，然后列出未完成的待办。"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_names"], ["save_task", "list_tasks"])
        self.assertEqual([item["status"] for item in body["tool_results"]], ["created", "queried"])
        saved = body["tool_results"][0]["task"]
        self.assertEqual(body["tool_results"][1]["tasks"], [saved])
        self.assertEqual(body["node_order"], ["model", "tools", "report", "model", "tools", "report", "model"])
        self.assertEqual(len(model.inputs), 3)
        earlier = [message for message in model.inputs[1] if isinstance(message, ToolMessage)]
        final = [message for message in model.inputs[2] if isinstance(message, ToolMessage)]
        self.assertEqual([message.tool_call_id for message in earlier], ["save-first"])
        self.assertEqual([message.tool_call_id for message in final], ["save-first", "query-after-save"])
        self.assertEqual([message.name for message in final], ["save_task", "list_tasks"])
        self.assertEqual(self.store.list_tasks(), [saved])

    def test_query_tools_bound_to_different_apps_cannot_mix_records(self):
        """不同应用绑定的查询工具只返回各自数据库，第二个应用不会改变已有工具的绑定。"""
        self.store.save_task("A 的待办")
        database_b = self.folder / "assistant_b.sqlite3"
        store_b = TaskStore(database_b)
        store_b.save_task("B 的待办")
        model_a = make_query_model({"status": "all"})
        model_b = make_query_model({"status": "all"})
        client_a = self.make_client(Mock(return_value=model_a))
        client_b = self.make_client(Mock(return_value=model_b), database_b)

        result_a = client_a.post("/messages", json={"text": "列出全部待办。"})
        result_b = client_b.post("/messages", json={"text": "列出全部待办。"})

        self.assertEqual(result_a.status_code, 200)
        self.assertEqual(result_b.status_code, 200)
        self.assertEqual(result_a.json()["tool_results"][0]["tasks"], self.store.list_tasks())
        self.assertEqual(result_b.json()["tool_results"][0]["tasks"], store_b.list_tasks())
        tools_a = {tool.name: tool for tool in model_a.tools}
        tools_b = {tool.name: tool for tool in model_b.tools}
        self.assertEqual(tools_a["list_tasks"].invoke({"status": "all"})["tasks"], self.store.list_tasks())
        self.assertEqual(tools_b["list_tasks"].invoke({"status": "all"})["tasks"], store_b.list_tasks())

    def test_query_response_model_uses_tool_result_despite_false_save_claim(self):
        """模型结语声称保存成功时仍返回真实查询类型，不能据此生成虚假的保存记录。"""
        original = self.seed_mixed_tasks()
        model = make_query_model({"status": "all"}, reply="保存成功，已经新增了待办。")
        client = self.make_client(Mock(return_value=model))

        response = client.post("/messages", json={"text": "仅列出待办。"})

        self.assertEqual(response.status_code, 200)
        parsed = lesson05.MessageResponse.model_validate(response.json())
        self.assertIsInstance(parsed.tool_results[0], lesson05.QueryToolResult)
        self.assertEqual(parsed.tool_results[0].status, "queried")
        self.assertEqual(parsed.tool_names, ["list_tasks"])
        self.assertNotIn("task", response.json()["tool_results"][0])
        self.assertEqual(parsed.tool_results[0].count, len(original))
        self.assertEqual(self.store.list_tasks(), original)

    def test_user_pending_endpoint_matches_count_and_status_queries(self):
        """用户新增的未完成列表只查 pending，与数量接口和查询工具返回保持一致。"""
        original = self.seed_mixed_tasks()
        expected = [task for task in original if task["status"] == "pending"]
        factory = Mock(return_value=make_query_model({"status": "pending"}))
        client = self.make_client(factory)

        pending = client.get("/tasks/pending")
        count = client.get("/tasks/pending/count")
        all_tasks = client.get("/tasks")

        self.assertEqual(pending.status_code, 200)
        self.assertEqual(count.status_code, 200)
        self.assertEqual(all_tasks.status_code, 200)
        self.assertEqual(pending.json(), {"count": 2, "tasks": expected})
        self.assertEqual(count.json(), {"count": len(pending.json()["tasks"])})
        self.assertEqual(all_tasks.json(), {"count": len(original), "tasks": original})
        factory.assert_not_called()
        queried = client.post("/messages", json={"text": "我还有哪些未完成的任务？"})
        self.assertEqual(queried.status_code, 200)
        self.assertEqual(queried.json()["tool_results"][0]["tasks"], pending.json()["tasks"])
        self.assertEqual(queried.json()["tool_results"][0]["count"], count.json()["count"])


if __name__ == "__main__":
    unittest.main()
