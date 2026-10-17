"""服务生命周期内持有检查点连接和主图，管理线程锁、用户轮次与失败后的显式恢复。"""

import asyncio
import json
import re
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from student_assistant.bridge import create_model, parse_reference_time
from student_assistant.business_tasks import BusinessTaskStore
from student_assistant.graph import TurnContext, build_agent
from student_assistant.tools import build_turn_tools
from student_assistant.rag_tools import create_retrieval_tools


class PendingTurnError(Exception):
    """表示线程尚有未完成执行，或没有可以恢复的执行，调用方应返回明确的409状态。"""


class AssistantEngine:
    """隔离业务库、checkpoint与模型依赖；同线程串行、不同线程可并发执行。"""

    def __init__(self, business_db: Path | str, checkpoint_db: Path | str, model_factory=None):
        """仅记录路径与依赖，不建库、不创建模型；两种存储必须使用不同文件。"""
        self.business_db = Path(business_db).resolve()
        self.checkpoint_db = Path(checkpoint_db).resolve()
        if self.business_db == self.checkpoint_db:
            raise ValueError("业务数据库与checkpoint必须使用不同文件")
        self.model_factory = model_factory if model_factory is not None else create_model
        self.store = None
        self.graph = None
        self._stack = None
        self._locks: dict[str, asyncio.Lock] = {}
        # 主服务初始化RAG后注入只读检索函数；旧课程和独立引擎可以继续不接文档库。
        self.document_search = None

    async def __aenter__(self):
        """在服务启动时建业务库、打开持久检查点并编译一次图，异常时关闭已分配资源。"""
        if self._stack is not None:
            raise RuntimeError("引擎已经启动")
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            self.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
            self.store = await asyncio.to_thread(BusinessTaskStore, self.business_db)
            saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(self.checkpoint_db)))
            await saver.setup()
            self.graph = build_agent(saver)
            self._stack = stack
        except BaseException:
            await stack.aclose()
            self.store = None
            self.graph = None
            raise
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """在服务退出时关闭检查点连接，保留已提交的业务记录与线程历史。"""
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self.graph = None
        self.store = None

    def _config(self, thread_id: str) -> dict:
        """校验线程标识并构造checkpoint键，限制最多24个图节点步骤。"""
        if not isinstance(thread_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", thread_id) is None:
            raise ValueError("thread_id必须为1至64位字母、数字、下划线或连字符")
        if self.graph is None:
            raise RuntimeError("引擎尚未启动或已经关闭")
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 24}

    def _lock(self, thread_id: str) -> asyncio.Lock:
        """按真实线程键分配锁，保护读取状态、恢复和运行整个流程。"""
        if thread_id not in self._locks:
            self._locks[thread_id] = asyncio.Lock()
        return self._locks[thread_id]

    def _turn_messages(self, values: dict) -> list:
        """找出最后一条用户输入及其后的消息，作为当前轮工具证据和响应的边界。"""
        messages = values.get("messages", [])
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], HumanMessage):
                return messages[index:]
        return []

    def _context(self, messages: list, *, resume: bool) -> TurnContext:
        """为本次运行建立工具表；仅恢复同一未完成轮时重建已查询ID证据。"""
        model = self.model_factory()
        references = [
            message.additional_kwargs["reference_time"]
            for message in messages
            if isinstance(message, HumanMessage) and message.additional_kwargs.get("reference_time")
        ]
        queried_ids: set[str] = set()
        if resume:
            for message in self._turn_messages({"messages": messages}):
                if not isinstance(message, ToolMessage) or message.name != "list_tasks":
                    continue
                data = json.loads(message.content)
                if data.get("status") == "queried":
                    queried_ids.update(task["id"] for task in data["tasks"])
        tools = build_turn_tools(self.store, model, references, queried_ids=queried_ids)
        if self.document_search is not None:
            tools.update(create_retrieval_tools(self.document_search))
        return TurnContext(model=model, store=self.store, tools=tools)

    def _response(self, thread_id: str, values: dict) -> dict:
        """只从当前轮真实ToolMessage收集结果；历史错误或旧工具不会污染本轮状态。"""
        messages = self._turn_messages(values)
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        results = [json.loads(message.content) for message in tool_messages]
        status = "no_tools"
        if results:
            status = "tool_error" if any(item.get("status") == "error" for item in results) else "completed"
        final = messages[-1]
        content = final.content
        return {
            "thread_id": thread_id,
            "turn_id": values["turn_id"],
            "reply": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
            "execution_status": status,
            "node_order": values.get("node_history", [])[values["turn_start_history"]:],
            "tool_names": [message.name for message in tool_messages if message.name is not None],
            "tool_results": results,
            "history_count": len(values.get("messages", [])),
        }

    async def chat(self, message: str, thread_id: str, reference_time: str | None = None) -> dict:
        """开启新用户轮；若前一轮未结束则拒绝混入新消息，要求显式恢复。"""
        config = self._config(thread_id)
        if not isinstance(message, str) or not message.strip() or len(message) > 5000:
            raise ValueError("消息必须是1至5000字的非空文本")
        if reference_time is not None:
            parse_reference_time(reference_time)
        async with self._lock(thread_id):
            previous = await self.graph.aget_state(config)
            if previous.next:
                raise PendingTurnError("该线程有未完成执行，请先调用恢复接口")
            turn_id = str(uuid4())
            human = HumanMessage(
                content=message, id=turn_id,
                additional_kwargs={"reference_time": reference_time} if reference_time is not None else {},
            )
            context = self._context(previous.values.get("messages", []) + [human], resume=False)
            result = await self.graph.ainvoke(
                {"messages": [human], "turn_id": turn_id,
                 "turn_start_history": len(previous.values.get("node_history", []))},
                config=config, context=context, durability="sync",
            )
            return self._response(thread_id, result)

    async def resume(self, thread_id: str) -> dict:
        """从最后检查点继续未完成轮，不加入新用户消息；业务写入依赖工具自身幂等。"""
        config = self._config(thread_id)
        async with self._lock(thread_id):
            previous = await self.graph.aget_state(config)
            if not previous.next:
                raise PendingTurnError("该线程没有需要恢复的执行")
            context = self._context(previous.values.get("messages", []), resume=True)
            result = await self.graph.ainvoke(None, config=config, context=context, durability="sync")
            return self._response(thread_id, result)

    async def history(self, thread_id: str) -> dict:
        """查询单个线程的持久消息及待运行节点，不调用模型或读取其他线程。"""
        config = self._config(thread_id)
        async with self._lock(thread_id):
            snapshot = await self.graph.aget_state(config)
            messages = []
            for message in snapshot.values.get("messages", []):
                item = {"role": message.type, "content": message.content, "id": message.id}
                if isinstance(message, HumanMessage) and message.additional_kwargs.get("reference_time"):
                    item["reference_time"] = message.additional_kwargs["reference_time"]
                if isinstance(message, AIMessage) and message.tool_calls:
                    item["tool_calls"] = message.tool_calls
                if isinstance(message, ToolMessage):
                    item["tool_name"] = message.name
                    item["tool_call_id"] = message.tool_call_id
                messages.append(item)
            return {"thread_id": thread_id, "messages": messages,
                    "pending": bool(snapshot.next), "next_nodes": list(snapshot.next)}
