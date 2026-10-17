"""记忆模块离线集成测试：真实图、业务 SQLite、checkpoint 与进程内 HTTP。"""

import asyncio
import importlib
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from openai import APIError, APITimeoutError


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

service = importlib.import_module("student_assistant.service")


class FakeAsyncModel:
    """记录图实际收到的历史与工具，预设异步答复，并可模拟时间工具内部的结构化提取。"""

    def __init__(self, *responses, structured_data=None):
        """保存模型响应、可选提取数据，以及每次调用的输入记录。"""
        self.responses = list(responses)
        self.structured_data = structured_data
        self.inputs = []
        self.tools = []
        self.schema_calls = []
        self.structured_inputs = []
        self.output_schema = None

    def bind_tools(self, tools):
        """保存本次请求绑定的真实工具对象，返回模型自身供图调用。"""
        self.tools = list(tools)
        return self

    async def ainvoke(self, messages):
        """记录异步模型输入，返回预设消息或抛出指定提供商异常。"""
        self.inputs.append(list(messages))
        if not self.responses:
            raise AssertionError("出现未预期的异步模型调用")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def with_structured_output(self, schema, *, method):
        """记录工具内部使用的真实 schema，让下一次同步调用返回对应 Pydantic 对象。"""
        self.output_schema = schema
        self.schema_calls.append((schema, method))
        return self

    def invoke(self, messages):
        """模拟结构化提取，不替换后续真实日历计算或结果校验。"""
        self.structured_inputs.append(list(messages))
        if self.output_schema is None or self.structured_data is None:
            raise AssertionError("出现未预期的同步模型调用")
        return self.output_schema.model_validate(self.structured_data)


class RequestGates:
    """用线程事件控制请求进入模型的时机，验证锁行为时不靠固定睡眠猜测。"""

    def __init__(self):
        """准备首请求阻塞点、释放信号及同线程和其他线程的进入记录。"""
        self.first_entered = threading.Event()
        self.release_first = threading.Event()
        self.same_entered = threading.Event()
        self.other_entered = threading.Event()


class GatedAsyncModel(FakeAsyncModel):
    """只阻塞指定请求的模型，用于检验线程间并发和线程内串行。"""

    def __init__(self, gates):
        """保存共享事件，每个请求仍使用独立的模型实例。"""
        super().__init__(AIMessage(content="本次请求已完成。"))
        self.gates = gates

    async def ainvoke(self, messages):
        """根据本轮人类消息设置事件；首请求异步等待释放，其他请求正常返回。"""
        latest = next(message for message in reversed(messages) if isinstance(message, HumanMessage))
        if "hold-first" in latest.content:
            self.gates.first_entered.set()
            released = await asyncio.to_thread(self.gates.release_first.wait, 10)
            if not released:
                raise AssertionError("并发测试未按时释放首请求")
        elif "same-second" in latest.content:
            self.gates.same_entered.set()
        elif "other-first" in latest.content:
            self.gates.other_entered.set()
        return await super().ainvoke(messages)


def proposal(name, arguments, call_id):
    """构造一条带调用 ID 的模型工具请求，实际执行仍交给真实图。"""
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": arguments, "id": call_id, "type": "tool_call",
    }])


def message_texts(messages):
    """提取传给模型的纯文本内容，用于核对历史记忆与线程隔离。"""
    return [message.content for message in messages if isinstance(message.content, str)]


class AgentMemoryTests(unittest.TestCase):
    """通过公开 API 验证持久记忆、工具证据与失败恢复，全程使用临时数据。"""

    def setUp(self):
        """为每个场景准备独立的两份 SQLite 路径，避免读写用户真实数据。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.business_db = self.folder / "business.sqlite3"
        self.checkpoint_db = self.folder / "checkpoints.sqlite3"

    def application(self, factory, *, suffix=""):
        """创建使用指定临时文件和注入模型工厂的应用；suffix 用于同场景多应用隔离。"""
        business = self.business_db if not suffix else self.folder / f"business-{suffix}.sqlite3"
        checkpoints = self.checkpoint_db if not suffix else self.folder / f"checkpoints-{suffix}.sqlite3"
        return service.create_app(
            business_db=business, checkpoint_db=checkpoints, model_factory=factory,
        )

    def make_client(self, factory, *, suffix=""):
        """显式进入 TestClient 生命周期，确保异步 checkpointer 已打开且最终正确关闭。"""
        return self.enterContext(TestClient(self.application(factory, suffix=suffix)))

    def checkpoint(self, client, thread_id):
        """通过已启动应用的事件循环读取真实图状态，不创建新的 checkpointer 或连接。"""
        return client.portal.call(
            client.app.state.engine.graph.aget_state,
            {"configurable": {"thread_id": thread_id}},
        )

    def test_same_thread_remembers_followup_and_keeps_raw_human_messages(self):
        """同线程追问收到前轮历史，checkpoint 保留原文和来源时间而不持久化动态 system。"""
        first = FakeAsyncModel(AIMessage(content="已记录多模态检索研究。"))
        second = FakeAsyncModel(AIMessage(content="你刚才提到多模态检索。"))
        factory = Mock(side_effect=[first, second])
        client = self.make_client(factory)
        original = "我正在研究多模态检索。"
        reference = "2026-10-16T20:00:00+08:00"

        response1 = client.post("/invoke", json={
            "message": original, "thread_id": "memory-followup", "reference_time": reference,
        })
        response2 = client.post("/invoke", json={
            "message": "我刚才研究什么？", "thread_id": "memory-followup",
        })

        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response2.status_code, 200)
        body1, body2 = response1.json(), response2.json()
        self.assertEqual(body2["thread_id"], "memory-followup")
        self.assertNotEqual(body1["turn_id"], body2["turn_id"])
        self.assertGreater(body2["history_count"], body1["history_count"])
        self.assertEqual(body2["node_order"], ["model"])
        self.assertTrue(any(original in text for text in message_texts(second.inputs[0])))
        self.assertIn("已记录多模态检索研究。", message_texts(second.inputs[0]))
        history = client.get("/threads/memory-followup")
        self.assertEqual(history.status_code, 200)
        messages = history.json()["messages"]
        saved_human = next(message for message in messages if message["content"] == original)
        self.assertEqual(saved_human["reference_time"], reference)
        self.assertFalse(any(message["role"] == "system" for message in messages))
        self.assertFalse(history.json()["pending"])
        self.assertEqual(history.json()["next_nodes"], [])
        self.assertEqual(factory.call_count, 2)

    def test_threads_keep_conversations_isolated_but_share_business_tasks(self):
        """不同线程不共享聊天历史，但它们查询的是同一业务待办库。"""
        secret = "线程A独有线索：猕猴桃7319。"
        model_a = FakeAsyncModel(AIMessage(content="已记录。"))
        model_b = FakeAsyncModel(
            proposal("list_tasks", {"status": "all"}, "shared-query"),
            AIMessage(content="已查询共享待办。"),
        )
        client = self.make_client(Mock(side_effect=[model_a, model_b]))
        saved = client.app.state.engine.store.save_task("所有线程可查询的待办")["task"]

        response_a = client.post("/invoke", json={"message": secret, "thread_id": "thread-a"})
        response_b = client.post("/invoke", json={"message": "查询全部待办。", "thread_id": "thread-b"})

        self.assertEqual(response_a.status_code, 200)
        self.assertEqual(response_b.status_code, 200)
        self.assertFalse(any(secret in text for text in message_texts(model_b.inputs[0])))
        self.assertEqual(response_b.json()["tool_results"][0]["tasks"], [saved])
        self.assertEqual(client.get("/tasks").json(), {"count": 1, "tasks": [saved]})
        self.assertNotIn(secret, client.get("/threads/thread-b").text)

    def test_recreated_app_restores_checkpoint_history_and_business_data(self):
        """关闭再创建应用后，两份 SQLite 分别恢复对话历史与业务待办。"""
        original = "重启前的上下文：研究 Agent 持久记忆。"
        first_model = FakeAsyncModel(
            proposal("save_task", {"title": "重启后保留的事项"}, "persist-save"),
            AIMessage(content="已保存事项并记录上下文。"),
        )
        with TestClient(self.application(Mock(return_value=first_model))) as first_client:
            first_response = first_client.post("/invoke", json={"message": original, "thread_id": "restartable"})
            self.assertEqual(first_response.status_code, 200)
            saved = first_response.json()["tool_results"][0]["task"]
        self.assertTrue(self.business_db.exists())
        self.assertTrue(self.checkpoint_db.exists())
        second_model = FakeAsyncModel(AIMessage(content="你在研究 Agent 持久记忆。"))

        with TestClient(self.application(Mock(return_value=second_model))) as reopened:
            self.assertIn(original, reopened.get("/threads/restartable").text)
            self.assertEqual(reopened.get("/tasks").json()["tasks"], [saved])
            response = reopened.post("/invoke", json={"message": "重启前研究什么？", "thread_id": "restartable"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(any(original in text for text in message_texts(second_model.inputs[0])))
            self.assertNotEqual(response.json()["turn_id"], first_response.json()["turn_id"])

    def test_old_turn_query_results_do_not_authorize_new_turn_completion(self):
        """模型能看到旧查询历史，但新用户轮次仍必须重新查询才能完成待办。"""
        query_model = FakeAsyncModel(
            proposal("list_tasks", {"status": "all"}, "old-query"), AIMessage(content="已列出。"),
        )
        factory = Mock()
        client = self.make_client(factory)
        target = client.app.state.engine.store.save_task("新轮次必须重新查询")["task"]
        complete_model = FakeAsyncModel(
            proposal("complete_task", {"task_id": target["id"]}, "new-turn-complete"),
            AIMessage(content="已经完成。"),
        )
        # 在生成真实 ID 后设置工厂响应，不用预造或硬编码任务指纹。
        factory.side_effect = [query_model, complete_model]
        first = client.post("/invoke", json={"message": "查询待办。", "thread_id": "fresh-evidence"})
        second = client.post("/invoke", json={"message": "把它完成。", "thread_id": "fresh-evidence"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["execution_status"], "tool_error")
        self.assertEqual(second.json()["tool_names"], ["complete_task"])
        self.assertEqual(second.json()["tool_results"][0]["status"], "error")
        self.assertEqual(client.app.state.engine.store.list_tasks(), [target])

    def test_failed_turn_requires_explicit_resume_and_restores_its_query_evidence(self):
        """工具后模型失败会留下待恢复轮次；新消息被拒绝，resume 沿用该轮查询证据。"""
        provider_request = httpx.Request("POST", "https://model.invalid/chat/completions")
        failing = FakeAsyncModel(
            proposal("list_tasks", {"status": "all"}, "checkpointed-query"),
            APIError("private-provider-detail", request=provider_request, body=None),
        )
        factory = Mock()
        client = self.make_client(factory)
        target = client.app.state.engine.store.save_task("恢复轮次后完成")["task"]
        resumed_model = FakeAsyncModel(
            proposal("complete_task", {"task_id": target["id"]}, "resume-complete"),
            AIMessage(content="恢复后已完成目标事项。"),
        )
        factory.side_effect = [failing, resumed_model]

        failed = client.post("/invoke", json={"message": "先查出任务并完成。", "thread_id": "pending-turn"})

        self.assertEqual(failed.status_code, 502)
        self.assertNotIn("private-provider-detail", failed.text)
        history = client.get("/threads/pending-turn")
        self.assertTrue(history.json()["pending"])
        self.assertTrue(history.json()["next_nodes"])
        saved_turn_id = self.checkpoint(client, "pending-turn").values["turn_id"]
        blocked = client.post("/invoke", json={"message": "这条不能覆盖未完成轮次。", "thread_id": "pending-turn"})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(factory.call_count, 1)
        resumed = client.post("/threads/pending-turn/resume")

        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["turn_id"], saved_turn_id)
        self.assertEqual(resumed.json()["tool_names"], ["list_tasks", "complete_task"])
        self.assertEqual([item["status"] for item in resumed.json()["tool_results"]], ["queried", "completed"])
        self.assertEqual(client.app.state.engine.store.list_tasks()[0]["status"], "completed")
        self.assertFalse(client.get("/threads/pending-turn").json()["pending"])
        self.assertEqual(client.get("/threads/pending-turn").json()["next_nodes"], [])
        self.assertEqual(client.post("/threads/pending-turn/resume").status_code, 409)
        self.assertEqual(factory.call_count, 2)
        tool_messages = [message for message in resumed_model.inputs[-1] if isinstance(message, ToolMessage)]
        self.assertEqual([message.tool_call_id for message in tool_messages], ["checkpointed-query", "resume-complete"])
        exported = client.get("/threads/pending-turn").json()["messages"]
        request_ids = {call["id"] for message in exported for call in message.get("tool_calls", [])}
        result_ids = {message["tool_call_id"] for message in exported if message.get("role") == "tool"}
        self.assertEqual(request_ids, result_ids)

    def test_current_turn_results_exclude_old_errors_and_tool_messages(self):
        """历史中的工具错误不污染新轮结果，无工具的新轮返回空工具列表和本轮节点。"""
        error_model = FakeAsyncModel(
            proposal("unknown_tool", {}, "old-error"), AIMessage(content="该工具不可用。"),
        )
        saving_model = FakeAsyncModel(
            proposal("save_task", {"title": "本轮新事项"}, "current-save"), AIMessage(content="已保存。"),
        )
        plain_model = FakeAsyncModel(AIMessage(content="好的。"))
        client = self.make_client(Mock(side_effect=[error_model, saving_model, plain_model]))

        first = client.post("/invoke", json={"message": "尝试不存在的工具。", "thread_id": "current-results"})
        second = client.post("/invoke", json={"message": "保存本轮事项。", "thread_id": "current-results"})
        third = client.post("/invoke", json={"message": "谢谢。", "thread_id": "current-results"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(third.status_code, 200)
        self.assertEqual(first.json()["execution_status"], "tool_error")
        self.assertEqual(second.json()["execution_status"], "completed")
        self.assertEqual(second.json()["tool_names"], ["save_task"])
        self.assertEqual([item["status"] for item in second.json()["tool_results"]], ["created"])
        self.assertEqual(third.json()["execution_status"], "no_tools")
        self.assertEqual(third.json()["tool_names"], [])
        self.assertEqual(third.json()["tool_results"], [])
        self.assertEqual(third.json()["node_order"], ["model"])

    def test_time_tool_uses_message_reference_and_real_calendar_resolution(self):
        """时间工具接受等价时区写法并真实计算日期，拒绝模型编造的参考时间。"""
        reference = "2026-10-16T20:00:00+08:00"
        correct = FakeAsyncModel(
            proposal("interpret_time", {"time_text": "明天下午三点", "reference_time": "2026-10-16T12:00:00Z"}, "time-correct"),
            AIMessage(content="对应明天十五点。"),
            structured_data={"intent": {"unit": "day", "offset": 1, "hour": 15, "minute": 0}, "reason": None},
        )
        invented = FakeAsyncModel(
            proposal("interpret_time", {"time_text": "明天下午三点", "reference_time": "2026-10-20T20:00:00+08:00"}, "time-invented"),
            AIMessage(content="时间已处理。"),
        )
        client = self.make_client(Mock(side_effect=[correct, invented]))
        original = "导师说：明天下午三点开会。"

        response = client.post("/invoke", json={"message": original, "reference_time": reference})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertRegex(body["thread_id"], r"^[A-Za-z0-9_-]{1,64}$")
        self.assertEqual(body["tool_names"], ["interpret_time"])
        self.assertEqual(body["tool_results"][0]["status"], "interpreted")
        self.assertEqual(body["tool_results"][0]["result"]["precision"], "minute")
        self.assertEqual(body["tool_results"][0]["result"]["at"], "2026-10-17T15:00:00+08:00")
        self.assertEqual(len(correct.schema_calls), 1)
        self.assertEqual(correct.schema_calls[0][1], "json_mode")
        self.assertTrue(any(reference in text for text in message_texts(correct.inputs[0])))
        raw = client.get(f"/threads/{body['thread_id']}").json()["messages"]
        self.assertTrue(any(message["content"] == original and message.get("reference_time") == reference for message in raw))
        rejected = client.post("/invoke", json={"message": original, "reference_time": reference})
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["execution_status"], "tool_error")
        self.assertEqual(rejected.json()["tool_results"][0]["status"], "error")
        self.assertEqual(invented.schema_calls, [])
        self.assertEqual(client.app.state.engine.store.list_tasks(), [])

    def test_health_queries_and_invalid_inputs_do_not_initialize_models(self):
        """健康和待办查询可独立使用，非法消息、线程 ID 和来源时间在模型初始化前拒绝。"""
        factory = Mock(side_effect=AssertionError("只读与非法请求不能初始化模型"))
        client = self.make_client(factory)
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/tasks/pending/count").json(), {"count": 0})
        invalid_bodies = [
            {}, {"message": 123}, {"message": " \t"}, {"message": "字" * 5001},
            {"message": "消息", "thread_id": "../outside"},
            {"message": "消息", "thread_id": "x" * 65},
            {"message": "消息", "thread_id": "中文线程"},
            {"message": "消息", "reference_time": "2026-10-16T20:00:00"},
        ]
        for body in invalid_bodies:
            with self.subTest(body=body):
                self.assertEqual(client.post("/invoke", json=body).status_code, 422)
        factory.assert_not_called()

    def test_missing_configuration_and_provider_errors_return_safe_statuses(self):
        """缺配置不会妨碍健康检查；提供商失败与超时映射明确状态且不暴露原始细节。"""
        missing = {"COMPATIBLE_MODEL": "", "COMPATIBLE_BASE_URL": "", "COMPATIBLE_API_KEY": ""}
        with patch.dict(os.environ, missing):
            unconfigured = self.make_client(None, suffix="unconfigured")
            self.assertEqual(unconfigured.get("/health").status_code, 200)
            self.assertEqual(unconfigured.post("/invoke", json={"message": "你好。"}).status_code, 503)
        request = httpx.Request("POST", "https://model.invalid/chat/completions")
        failures = [
            (APIError("private-provider-detail", request=request, body=None), 502),
            (APITimeoutError(request=request), 504),
        ]
        for index, (error, status) in enumerate(failures):
            with self.subTest(error=type(error).__name__):
                model = FakeAsyncModel(error)
                client = self.make_client(Mock(return_value=model), suffix=f"failure-{index}")
                response = client.post("/invoke", json={"message": "你好。", "thread_id": "failed-thread"})
                self.assertEqual(response.status_code, status)
                self.assertNotIn("private-provider-detail", response.text)
                self.assertTrue(client.get("/threads/failed-thread").json()["pending"])

    def test_business_and_checkpoint_files_must_be_different(self):
        """业务表与 checkpoint 不允许指向同一文件，避免两套持久化职责混用。"""
        with self.assertRaises(ValueError):
            app = service.create_app(
                business_db=self.business_db, checkpoint_db=self.business_db,
                model_factory=Mock(side_effect=AssertionError("配置错误不能初始化模型")),
            )
            with TestClient(app):
                pass

    def test_same_thread_serializes_requests_while_other_threads_continue(self):
        """同线程请求等待前轮结束再读取历史，其他线程可独立完成而不被全局锁阻塞。"""
        gates = RequestGates()

        def make_gated_model():
            """为每次请求创建使用同组控制事件的独立模型。"""
            return GatedAsyncModel(gates)

        client = self.make_client(Mock(side_effect=make_gated_model))
        with ThreadPoolExecutor(max_workers=3) as workers:
            first = workers.submit(client.post, "/invoke", json={"message": "hold-first", "thread_id": "serialized"})
            try:
                self.assertTrue(gates.first_entered.wait(5), "首请求未进入模型")
                second = workers.submit(client.post, "/invoke", json={"message": "same-second", "thread_id": "serialized"})
                other = workers.submit(client.post, "/invoke", json={"message": "other-first", "thread_id": "independent"})
                other_response = other.result(timeout=5)
                self.assertEqual(other_response.status_code, 200)
                self.assertTrue(gates.other_entered.is_set())
                self.assertFalse(gates.same_entered.is_set())
            finally:
                # 无论断言是否通过，都释放首请求，避免测试留下阻塞线程。
                gates.release_first.set()
            first_response = first.result(timeout=10)
            second_response = second.result(timeout=10)
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertTrue(gates.same_entered.is_set())
        messages = client.get("/threads/serialized").json()["messages"]
        human_contents = [message["content"] for message in messages if message["role"] in ("human", "user")]
        self.assertEqual(human_contents, ["hold-first", "same-second"])


if __name__ == "__main__":
    unittest.main()
