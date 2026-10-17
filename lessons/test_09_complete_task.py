"""第九课离线测试：完成工具必须使用本次查询的真实 ID，再修改临时数据库。"""

import importlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage


LESSONS = str(Path(__file__).resolve().parent)
if LESSONS not in sys.path:
    sys.path.insert(0, LESSONS)

lesson03 = importlib.import_module("03_tool_calling")
lesson07 = importlib.import_module("07_sqlite_api")
assistant_tools = importlib.import_module("assistant_tools")
TaskStore = importlib.import_module("task_store").TaskStore


class FakeModel:
    """记录真实工具及消息，返回预设请求与答复，供进程内 HTTP 执行真实图。"""

    def __init__(self, *responses):
        """保存各轮预设响应，并准备输入与工具记录。"""
        self.responses = list(responses)
        self.inputs = []
        self.tools = []

    def bind_tools(self, tools):
        """保留实际绑定的工具对象，不创建网络连接。"""
        self.tools = list(tools)
        return self

    def invoke(self, messages):
        """记录当前图消息并返回下一条预设响应，额外调用立即使测试失败。"""
        self.inputs.append(list(messages))
        if not self.responses:
            raise AssertionError("出现未预期的模型调用")
        return self.responses.pop(0)


def tool_proposal(name, arguments, call_id):
    """构造一条带真实工具名称、参数与调用 ID 的预设模型请求。"""
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": arguments, "id": call_id, "type": "tool_call",
    }])


class CompleteTaskTests(unittest.TestCase):
    """验证查询证据、定向更新和请求隔离，全部使用真实临时 SQLite 文件。"""

    def setUp(self):
        """隔离数据库和输出，禁止任何真实模型或旧 JSON 存储访问。"""
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
            lesson03, "load_tasks", side_effect=AssertionError("完成工具禁止读取旧 JSON"),
        ))
        self.json_save = self.enterContext(patch.object(
            lesson03, "save_task", side_effect=AssertionError("完成工具禁止写入旧 JSON"),
        ))

    def tearDown(self):
        """确认全部场景保持模型与存储隔离，没有使用真实账户或旧文件。"""
        self.real_model.assert_not_called()
        self.json_load.assert_not_called()
        self.json_save.assert_not_called()

    def make_client(self, factory):
        """将模型工厂注入使用临时数据库的应用，并托管进程内客户端。"""
        app = lesson07.create_app(db_path=self.database, model_factory=factory)
        return self.enterContext(TestClient(app))

    def invoke_registered(self, registry, name, arguments, call_id="direct-call"):
        """通过真实工具执行器调用注册工具，同时解析返回的 ToolMessage JSON。"""
        message = lesson03.execute_tool_call({
            "name": name, "args": arguments, "id": call_id,
        }, registry=registry)
        return message, json.loads(message.content)

    def test_only_successfully_queried_ids_can_be_completed(self):
        """数据库有此 ID 或查询失败都不能完成；成功筛选未返回的 ID 也不能完成。"""
        pending = self.store.save_task("需要保留的未完成任务")["task"]
        previous = self.store.save_task("已经完成的任务")["task"]
        self.store.mark_completed(previous["id"])
        original = self.store.list_tasks()
        registry = assistant_tools.create_task_tools(self.store)

        _, not_queried = self.invoke_registered(registry, "complete_task", {"task_id": pending["id"]})
        _, failed_query = self.invoke_registered(registry, "list_tasks", {"status": "invalid"})
        _, after_failure = self.invoke_registered(registry, "complete_task", {"task_id": pending["id"]})
        _, queried = self.invoke_registered(registry, "list_tasks", {"status": "completed"})
        _, excluded = self.invoke_registered(registry, "complete_task", {"task_id": pending["id"]})

        self.assertEqual(not_queried["status"], "error")
        self.assertEqual(failed_query["status"], "error")
        self.assertEqual(after_failure["status"], "error")
        self.assertEqual(queried["status"], "queried")
        self.assertEqual([task["id"] for task in queried["tasks"]], [previous["id"]])
        self.assertEqual(excluded["status"], "error")
        self.assertEqual(self.store.list_tasks(), original)

    def test_completion_is_targeted_and_idempotent_after_same_registry_query(self):
        """同一工具表查询后只完成指定项，重复完成返回实际记录且不影响其他任务。"""
        target = self.store.save_task("提交综述", "周五前")["task"]
        untouched = self.store.save_task("参加组会", "明天下午三点", "实验楼302")["task"]
        registry = assistant_tools.create_task_tools(self.store)
        self.invoke_registered(registry, "list_tasks", {"status": "all"})

        message, completed = self.invoke_registered(registry, "complete_task", {"task_id": target["id"]}, "complete-real")
        _, repeated = self.invoke_registered(registry, "complete_task", {"task_id": target["id"]})

        expected = {**target, "status": "completed"}
        self.assertEqual(completed, {"status": "completed", "task": expected})
        self.assertEqual(repeated, completed)
        self.assertEqual(message.tool_call_id, "complete-real")
        self.assertEqual(message.name, "complete_task")
        self.assertEqual(self.store.list_tasks(), [expected, untouched])
        self.assertEqual(self.store.count_pending(), 1)

    def test_input_rejects_invalid_ids_and_preserves_legacy_non_hex_ids(self):
        """参数严格拒绝空白、错类型和额外字段，查询后允许完成非十六进制旧 ID。"""
        source = self.folder / "legacy_tasks.json"
        source.write_text(json.dumps([{
            "id": "legacy-task-alpha", "title": "旧版待办",
        }], ensure_ascii=False), encoding="utf-8")
        self.store.import_json(source)
        registry = assistant_tools.create_task_tools(self.store)
        self.invoke_registered(registry, "list_tasks", {"status": "all"})
        invalid_arguments = [
            {}, {"task_id": ""}, {"task_id": " \t\n"}, {"task_id": None},
            {"task_id": 123}, {"task_id": ["legacy-task-alpha"]},
            {"task_id": "legacy-task-alpha", "unexpected": True},
        ]
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                _, result = self.invoke_registered(registry, "complete_task", arguments)
                self.assertEqual(result["status"], "error")
                self.assertEqual(self.store.count_pending(), 1)

        _, result = self.invoke_registered(registry, "complete_task", {"task_id": "legacy-task-alpha"})

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["task"]["id"], "legacy-task-alpha")
        self.assertEqual(self.store.count_pending(), 0)

    def test_registry_query_evidence_is_not_shared(self):
        """同数据库的两个工具表不共享已查询 ID，第一个工具表仍可使用自己的证据。"""
        target = self.store.save_task("只允许已查询的工具表完成")["task"]
        first = assistant_tools.create_task_tools(self.store)
        second = assistant_tools.create_task_tools(self.store)
        self.invoke_registered(first, "list_tasks", {"status": "all"})

        _, denied = self.invoke_registered(second, "complete_task", {"task_id": target["id"]})

        self.assertEqual(denied["status"], "error")
        self.assertEqual(self.store.count_pending(), 1)
        _, completed = self.invoke_registered(first, "complete_task", {"task_id": target["id"]})
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.store.count_pending(), 0)

    def test_disappeared_queried_target_returns_error(self):
        """查询后目标被外部删除时不能报告完成，不创建替代记录或修改其他任务。"""
        target = self.store.save_task("即将消失的待办")["task"]
        untouched = self.store.save_task("不应被影响的待办")["task"]
        registry = assistant_tools.create_task_tools(self.store)
        self.invoke_registered(registry, "list_tasks", {"status": "all"})
        # 用独立连接模拟查询与执行之间的外部删除，验证过期查询结果的处理。
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DELETE FROM tasks WHERE id = ?", (target["id"],))

        _, result = self.invoke_registered(registry, "complete_task", {"task_id": target["id"]})

        self.assertEqual(result["status"], "error")
        self.assertEqual(self.store.list_tasks(), [untouched])

    def test_api_query_then_complete_returns_real_record_and_tool_messages(self):
        """HTTP 中先查再完成，响应、工具消息和待办数量都反映真实数据库更新。"""
        target = self.store.save_task("提交综述", "周五前")["task"]
        untouched = self.store.save_task("阅读参考论文")["task"]
        model = FakeModel(
            tool_proposal("list_tasks", {"status": "all"}, "query-first"),
            tool_proposal("complete_task", {"task_id": target["id"]}, "complete-second"),
            AIMessage(content="已将综述标记为完成。"),
        )
        client = self.make_client(Mock(return_value=model))

        response = client.post("/messages", json={"text": "综述交完了，把它标成完成。"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["execution_status"], "completed")
        self.assertEqual(body["tool_names"], ["list_tasks", "complete_task"])
        self.assertEqual([item["status"] for item in body["tool_results"]], ["queried", "completed"])
        expected = {**target, "status": "completed"}
        self.assertEqual(body["tool_results"][1]["task"], expected)
        self.assertEqual(self.store.list_tasks(), [expected, untouched])
        self.assertEqual(client.get("/tasks/count").json(), {"count": 2})
        self.assertEqual(client.get("/tasks/pending/count").json(), {"count": 1})
        self.assertEqual(client.get("/tasks/pending").json()["tasks"], [untouched])
        messages = [message for message in model.inputs[-1] if isinstance(message, ToolMessage)]
        self.assertEqual([message.tool_call_id for message in messages], ["query-first", "complete-second"])
        self.assertEqual([message.name for message in messages], ["list_tasks", "complete_task"])
        self.assertEqual(json.loads(messages[-1].content), {"status": "completed", "task": expected})

    def test_query_in_previous_http_request_cannot_authorize_false_completion_claim(self):
        """上次请求查过的 ID 不能授权下次直接完成，即使模型结语声称成功也不改库。"""
        target = self.store.save_task("必须在本次请求查询")["task"]
        query_model = FakeModel(
            tool_proposal("list_tasks", {"status": "all"}, "previous-query"),
            AIMessage(content="已列出待办。"),
        )
        complete_model = FakeModel(
            tool_proposal("complete_task", {"task_id": target["id"]}, "unverified-complete"),
            AIMessage(content="已经完成，保存成功。"),
        )
        client = self.make_client(Mock(side_effect=[query_model, complete_model]))
        first = client.post("/messages", json={"text": "列出全部待办。"})

        second = client.post("/messages", json={"text": "把它完成。"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        body = second.json()
        self.assertEqual(body["execution_status"], "tool_error")
        self.assertEqual(body["tool_names"], ["complete_task"])
        self.assertEqual(body["tool_results"][0]["status"], "error")
        self.assertIsNone(body["tool_results"][0]["task"])
        self.assertEqual(self.store.list_tasks(), [target])

    def test_error_query_complete_three_tool_rounds_fit_sqlite_step_limit(self):
        """错误后模型先查询再修正完成，三轮工具共十个节点可在 SQLite API 中执行完。"""
        target = self.store.save_task("完成前需要补查证据")["task"]
        model = FakeModel(
            tool_proposal("complete_task", {"task_id": target["id"]}, "premature-complete"),
            tool_proposal("list_tasks", {"status": "all"}, "corrective-query"),
            tool_proposal("complete_task", {"task_id": target["id"]}, "verified-complete"),
            AIMessage(content="补查之后已完成目标待办。"),
        )
        client = self.make_client(Mock(return_value=model))

        response = client.post("/messages", json={"text": "把这条待办标成完成。"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_names"], ["complete_task", "list_tasks", "complete_task"])
        self.assertEqual([item["status"] for item in body["tool_results"]], ["error", "queried", "completed"])
        # 接口保留本轮曾出现错误的汇总状态，最终是否完成仍需查看最后一条真实工具结果。
        self.assertEqual(body["execution_status"], "tool_error")
        self.assertEqual(body["node_order"], ["model", "tools", "report"] * 3 + ["model"])
        self.assertEqual(len(model.inputs), 4)
        messages = [message for message in model.inputs[-1] if isinstance(message, ToolMessage)]
        self.assertEqual([message.tool_call_id for message in messages], [
            "premature-complete", "corrective-query", "verified-complete",
        ])
        self.assertEqual(self.store.list_tasks()[0]["status"], "completed")
        self.assertEqual(self.store.count_pending(), 0)


if __name__ == "__main__":
    unittest.main()
