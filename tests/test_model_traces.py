"""模型与文档埋点离线验证：保留真实图、checkpoint、SQLite 与 Qdrant，不请求外部模型。"""

import importlib
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage
from openai import APIError


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from student_assistant.bridge import TaskStore
from student_assistant.rag_models import AskRequest, SearchRequest
from student_assistant.rag_runtime import RagEngine
from student_assistant.rag_store import RagStore
from student_assistant.runtime import AssistantEngine
from student_assistant.trace_store import TraceStore
from student_assistant.tracing import current_trace, span, use_trace


class TraceAgentModel:
    """预设主模型的工具请求和答复，业务工具、图节点与检查点仍实际运行。"""

    def __init__(self, *responses):
        """准备模型响应或异常，不构造网络客户端。"""
        self.responses = list(responses)

    def bind_tools(self, tools):
        """接受实际工具表，供主图执行预设工具调用。"""
        return self

    async def ainvoke(self, messages):
        """按顺序返回响应或抛原异常，便于验证 Trace 不改变失败边界。"""
        if not self.responses:
            raise AssertionError("出现未预期的模型调用")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class TraceAnswerModel:
    """预设 RAG 草稿，仅替换生成模型，检索和引用验证照常执行。"""

    def __init__(self, result):
        """保存回答草稿或提供商错误及实际输出模型。"""
        self.result = result
        self.schema = None

    def with_structured_output(self, schema, *, method):
        """记录真实 schema 并保持 JSON 模式调用接口。"""
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        """返回校验后的草稿或原样抛错，不记录输入 Prompt。"""
        if isinstance(self.result, Exception):
            raise self.result
        return self.schema.model_validate(self.result)


class ContextRecordingEmbedder:
    """仅替换向量模型，并观察生产 to_thread 边界中实际继承的 Trace 上下文。"""

    model_id = "trace-test-vector-v1"
    dimension = 3

    def __init__(self):
        """为文档与问题向量记录所在执行线程和当前父 Span 编号。"""
        self.contexts = []

    def _observe(self, operation):
        """只在测试内观察 ContextVar，不把业务文本或依赖写入 Trace 表。"""
        context = current_trace.get()
        self.contexts.append((
            operation, threading.get_ident(),
            context.trace_id if context is not None else None,
            context.parent_span_id if context is not None else None,
        ))

    def embed_documents(self, texts):
        """记录真实线程边界并返回测试向量，Qdrant 使用这些非零向量实际建索引。"""
        self._observe("documents")
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, query):
        """记录查询所继承的 Trace，返回同一测试向量空间中的方向。"""
        self._observe("query")
        return [1.0, 0.0, 0.0]


def tool_request(name, arguments, call_id="trace-tool-call"):
    """构造模型提出的工具调用，实际执行仍交给正式工具注册表。"""
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": arguments, "id": call_id, "type": "tool_call",
    }])


class ModelTraceTests(unittest.IsolatedAsyncioTestCase):
    """用五个场景验证模型与文档 Span、错误归类、线程传播、隐私和失败降级。"""

    def setUp(self):
        """每项测试隔离业务库、检查点与文档目录，不读取真实配置或数据。"""
        temporary = tempfile.TemporaryDirectory(prefix="student_model_traces_")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.tasks = TaskStore(self.folder / "business.sqlite3")
        self.traces = TraceStore(self.tasks)

    @contextmanager
    def recorded(self, operation):
        """建立真实 Trace 上下文并模拟 HTTP 根 Span，结束后查询持久化记录。"""
        trace_id = uuid4().hex
        self.traces.start_trace(trace_id, operation)
        try:
            with use_trace(self.traces, trace_id), span("http.request"):
                yield trace_id
        except Exception as error:
            self.traces.finish_trace(trace_id, "error", error_code=type(error).__name__)
            raise
        else:
            self.traces.finish_trace(trace_id, "completed")

    def detail(self, trace_id):
        """读取真实 Trace，检查 Span 编号、耗时和父子引用的完整性。"""
        result = self.traces.get_trace(trace_id)
        self.assertIsNotNone(result)
        spans = result["spans"]
        identifiers = {item["span_id"] for item in spans}
        for item in spans:
            self.assertGreaterEqual(item["elapsed_ms"], 0)
            if item["parent_span_id"] is not None:
                self.assertIn(item["parent_span_id"], identifiers)
        return result

    def rag(self, model_factory=None):
        """创建真实磁盘 Qdrant 与业务存储，仅向量推理和回答模型使用替身。"""
        embedder = ContextRecordingEmbedder()
        store = RagStore(self.tasks, self.folder / "rag", embedder.model_id, embedder.dimension)
        self.addCleanup(store.close)
        return RagEngine(store, embedder, model_factory=model_factory), embedder

    async def test_agent_success_records_tree_and_keeps_trace_out_of_checkpoint(self):
        """工具保存真实待办，Span 父子正确；业务消息和工具参数不会混入 Trace。"""
        private = "PRIVATE_MESSAGE_AND_TOOL_ARGUMENT_7319"
        model = TraceAgentModel(tool_request("save_task", {"title": private}), AIMessage(content="已保存。"))
        async with AssistantEngine(
            self.tasks.db_path, self.folder / "checkpoints.sqlite3", model_factory=Mock(return_value=model),
        ) as engine:
            with redirect_stdout(io.StringIO()), self.recorded("chat.invoke") as trace_id:
                result = await engine.chat(private, "trace-success")
            checkpoint = await engine.graph.aget_state({"configurable": {"thread_id": "trace-success"}})
            self.assertEqual(result["execution_status"], "completed")
            self.assertEqual(set(checkpoint.values), {"messages", "node_history", "turn_id", "turn_start_history"})
        detail = self.detail(trace_id)
        spans = detail["spans"]
        self.assertEqual(sum(item["name"] == "agent.model" for item in spans), 2)
        tool = next(item for item in spans if item["name"] == "tool.save_task")
        parent = next(item for item in spans if item["span_id"] == tool["parent_span_id"])
        self.assertEqual(parent["name"], "agent.tools")
        self.assertTrue(all(item["status"] == "completed" for item in spans))
        self.assertEqual(self.tasks.list_tasks()[0]["title"], private)
        self.assertNotIn(private, json.dumps(detail, ensure_ascii=False))

    async def test_agent_provider_and_returned_tool_errors_are_classified_safely(self):
        """提供商异常原样抛出，正常返回的工具 error 也被标记，未知工具名不成为记录字段。"""
        private = "PRIVATE_PROVIDER_OR_TOOL_NAME_7281"
        provider_error = APIError(private, request=httpx.Request("POST", "https://model.invalid/chat"), body=None)
        models = [
            TraceAgentModel(provider_error),
            TraceAgentModel(tool_request("save_task", {"title": ""}), AIMessage(content="参数错误。")),
            TraceAgentModel(tool_request(private, {}), AIMessage(content="未知工具。")),
        ]
        factory = Mock(side_effect=models)
        async with AssistantEngine(self.tasks.db_path, self.folder / "checkpoints.sqlite3", model_factory=factory) as engine:
            with self.assertRaises(APIError) as caught:
                with self.recorded("chat.invoke") as provider_trace:
                    await engine.chat("合成请求", "provider-failure")
            self.assertIs(caught.exception, provider_error)
            for index, name in enumerate(("invalid-arguments", "unknown-tool")):
                with redirect_stdout(io.StringIO()), self.recorded("chat.invoke") as tool_trace:
                    response = await engine.chat("合成工具请求", name)
                self.assertEqual(response["execution_status"], "tool_error")
                detail = self.detail(tool_trace)
                failures = [item for item in detail["spans"] if item["error_code"] == "ToolExecutionError"]
                self.assertEqual(len(failures), 1)
                self.assertEqual(failures[0]["name"], "tool.save_task" if index == 0 else "agent.tools")
                self.assertNotIn(private, json.dumps(detail))
        provider_detail = self.detail(provider_trace)
        failed_model = next(item for item in provider_detail["spans"] if item["name"] == "agent.model")
        self.assertEqual((failed_model["status"], failed_model["error_code"]), ("error", "APIError"))
        self.assertNotIn(private, json.dumps(provider_detail))
        self.assertEqual(self.tasks.list_tasks(), [])

    async def test_document_index_and_threaded_search_inherit_parent_context(self):
        """解析、向量化和真实检索都记录阶段，线程中的查询 Span 仍连接当前请求。"""
        rag, embedder = self.rag()
        main_thread = threading.get_ident()
        private = "PRIVATE_DOCUMENT_TEXT_1783"
        with self.recorded("documents.index") as index_trace:
            indexed = await rag.index("private-name.txt", private.encode())
        self.assertEqual(indexed["document"]["status"], "indexed")
        index_detail = self.detail(index_trace)
        self.assertEqual({item["name"] for item in index_detail["spans"]}, {
            "http.request", "document.receive", "document.extract", "document.chunk", "document.embed", "document.index",
        })
        with self.recorded("documents.search") as search_trace:
            searched = await rag.search(SearchRequest(query="PRIVATE_QUERY_8482"))
        self.assertEqual(searched["hits"][0]["text"], private)
        search_detail = self.detail(search_trace)
        search_span = next(item for item in search_detail["spans"] if item["name"] == "document.search")
        embed_span = next(item for item in search_detail["spans"] if item["name"] == "document.embed")
        self.assertEqual(embed_span["parent_span_id"], search_span["span_id"])
        observations = {item[0]: item for item in embedder.contexts}
        for operation, trace_id in (("documents", index_trace), ("query", search_trace)):
            observation = observations[operation]
            self.assertNotEqual(observation[1], main_thread)
            self.assertEqual(observation[2], trace_id)
            parent = next(item for item in self.detail(trace_id)["spans"] if item["span_id"] == observation[3])
            self.assertEqual(parent["name"], "document.embed")
        persisted = json.dumps([index_detail, search_detail])
        for hidden in (private, "private-name.txt", "PRIVATE_QUERY_8482"):
            self.assertNotIn(hidden, persisted)

    async def test_rag_unknown_is_normal_but_invalid_citations_and_provider_failures_are_visible(self):
        """正常拒答不标故障，伪造引用降级未知并标 InvalidCitations，模型错误仅留异常类型。"""
        models = [
            TraceAnswerModel({"status": "unknown", "answer": "资料不足。", "citations": []}),
            TraceAnswerModel({"status": "answered", "answer": "PRIVATE_UNSUPPORTED_ANSWER", "citations": [
                {"chunk_id": "PRIVATE_FORGED_CHUNK", "quote": "原文"},
            ]}),
            TraceAnswerModel(APIError("PRIVATE_RAG_PROVIDER_RESPONSE", request=httpx.Request("POST", "https://model.invalid/chat"), body=None)),
        ]
        rag, _ = self.rag(model_factory=Mock(side_effect=models))
        await rag.index("source.txt", "原文中的合成研究指标是0.85。".encode())
        with self.recorded("documents.ask") as unknown_trace:
            unknown = await rag.ask(AskRequest(question="合成问题"))
        self.assertEqual(unknown["reason"], "insufficient_evidence")
        self.assertTrue(all(item["status"] == "completed" for item in self.detail(unknown_trace)["spans"]))
        with self.recorded("documents.ask") as invalid_trace:
            rejected = await rag.ask(AskRequest(question="另一个合成问题"))
        self.assertEqual((rejected["status"], rejected["reason"]), ("unknown", "invalid_citations"))
        invalid = self.detail(invalid_trace)
        validation = next(item for item in invalid["spans"] if item["name"] == "document.validate_citations")
        self.assertEqual((validation["status"], validation["error_code"]), ("error", "InvalidCitations"))
        with self.assertRaises(APIError):
            with self.recorded("documents.ask") as failed_trace:
                await rag.ask(AskRequest(question="第三个合成问题"))
        failed = self.detail(failed_trace)
        answer = next(item for item in failed["spans"] if item["name"] == "document.answer")
        self.assertEqual((answer["status"], answer["error_code"]), ("error", "APIError"))
        combined = json.dumps([invalid, failed])
        self.assertNotIn("PRIVATE_", combined)

    async def test_trace_write_failure_cannot_undo_successful_business_writes(self):
        """观测写入故障只留下安全诊断，不能改变工具成功响应或回滚已提交待办。"""
        model = TraceAgentModel(tool_request("save_task", {"title": "Trace故障时仍保存"}), AIMessage(content="已保存。"))
        async with AssistantEngine(
            self.tasks.db_path, self.folder / "checkpoints.sqlite3", model_factory=Mock(return_value=model),
        ) as engine:
            with patch.object(self.traces, "write_span", side_effect=sqlite3.OperationalError("PRIVATE_TRACE_DB_ERROR")):
                with self.assertLogs("student_assistant.trace_store", level="WARNING") as logs:
                    with redirect_stdout(io.StringIO()), self.recorded("chat.invoke") as trace_id:
                        result = await engine.chat("合成保存请求", "trace-write-failure")
        self.assertEqual(result["execution_status"], "completed")
        self.assertEqual(self.tasks.list_tasks()[0]["title"], "Trace故障时仍保存")
        self.assertEqual(self.traces.get_trace(trace_id)["status"], "completed")
        self.assertNotIn("PRIVATE_TRACE_DB_ERROR", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
