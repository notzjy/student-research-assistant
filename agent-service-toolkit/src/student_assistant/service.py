"""个人助理主服务：将持久化 Agent、线程恢复和业务查询接入工具包服务入口。"""

import asyncio
import ipaddress
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Path as APIPath, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from langchain_core.exceptions import OutputParserException
from langgraph.errors import GraphRecursionError
from openai import APIError, APITimeoutError
from pydantic import ValidationError

from student_assistant.bridge import ROOT, model_is_configured
from student_assistant.business_tasks import TaskCancelledError
from student_assistant.message_models import MessageInput, RoleRule, StrictInput
from student_assistant.mail_ingest import ImapCollector, MailSettings
from student_assistant.mail_runtime import MailRuntime
from student_assistant.mail_store import MailStore
from student_assistant.message_store import MessageConflictError, MessageStore
from student_assistant.message_workflow import (
    MessageModelUnavailable,
    MessageProcessingError,
    MessageProcessor,
)
from student_assistant.rag_documents import MAX_FILE_BYTES
from student_assistant.rag_embeddings import EmbeddingUnavailable, LocalEmbedder
from student_assistant.rag_models import AskRequest, SearchRequest
from student_assistant.rag_runtime import RagEngine, RagModelUnavailable, RagProcessingError
from student_assistant.rag_store import RagStore
from student_assistant.reminder_models import ReminderCreate, ReminderPlanRequest, ReminderPolicy, ReminderUpdate
from student_assistant.reminder_runtime import ReminderRuntime
from student_assistant.reminder_store import ReminderConflictError, ReminderStore
from student_assistant.trace_store import TraceStore
from student_assistant.trace_middleware import RequestTraceMiddleware
from student_assistant.runtime import AssistantEngine, PendingTurnError
from student_assistant.schemas import (
    THREAD_ID_PATTERN,
    ChatRequest,
    ChatResponse,
    TaskRecord,
    TasksResponse,
    ThreadHistory,
)
from student_assistant.attachment_store import AttachmentStore
from student_assistant.attention import AttentionPolicy, AttentionRuntime, AttentionStore
from student_assistant.notification_gateway import BridgeConfiguration, NotificationInput, NotificationRuntime, NotificationStore
from student_assistant.review_models import MessageReview, EventReview, DocumentReview
from student_assistant.review_store import ReviewStore, ReviewConflictError
from student_assistant.document_classification import DocumentClassifier, DocumentClassificationError


ThreadPath = Annotated[str, APIPath(pattern=THREAD_ID_PATTERN, min_length=1, max_length=64)]


class BridgeSetup(StrictInput):
    """本机管理界面明确启停手机通知接入，轮换令牌会使旧手机配置失效。"""

    enabled: bool = True
    rotate: bool = False
    trust_posted_time: bool = False


def _message_view(request: Request, detail):
    """统一叠加人工纠正、附件原件和通知完整性标记，保持模型原始分析可追溯。"""
    if detail is None:
        return None
    state = request.app.state
    detail = state.reviews.detail_overlay(detail)
    detail = state.attachments.overlay(detail)
    metadata = state.notification_store.metadata(detail['message_id'])
    if metadata:
        detail['source_metadata'] = metadata
    return detail


def _review_error(error):
    """将修订和正文分类异常转为固定说明，避免公开原文、路径或模型提供商错误。"""
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, ReviewConflictError):
        return HTTPException(409, detail={'message': '内容已被其他操作更新，请刷新后再修改。'})
    if isinstance(error, KeyError):
        return HTTPException(404, detail={'message': '指定消息、事项或文档不存在。'})
    if isinstance(error, (ValueError, ValidationError)):
        return HTTPException(422, detail={'message': '修订内容或文件正文证据不符合要求，请核对时间、版本和原文。'})
    return HTTPException(503, detail={'message': '操作暂未完成，原始内容仍保留，请查看追踪后重试。'})


def _http_error(status_code: int, message: str, thread_id: str | None) -> HTTPException:
    """创建不含提供商原始错误的 HTTP 异常，同时保留用于核对和恢复的线程编号。"""
    return HTTPException(status_code=status_code, detail={"thread_id": thread_id, "message": message})


def _map_engine_error(error: Exception, thread_id: str | None) -> HTTPException:
    """把引擎异常映射为稳定的 HTTP 状态；已提交的业务写入不因请求失败而回滚。"""
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, PendingTurnError):
        return _http_error(409, "线程存在未完成流程，请查询历史后显式调用 resume 恢复。", thread_id)
    if isinstance(error, GraphRecursionError):
        return _http_error(409, "流程达到步数上限；已提交操作仍保留，请查询历史后显式恢复。", thread_id)
    if isinstance(error, APITimeoutError):
        return _http_error(504, "模型请求超时；请查询线程历史核对已提交操作。", thread_id)
    if isinstance(error, APIError):
        return _http_error(502, "模型服务请求失败；请查询线程历史核对已提交操作。", thread_id)
    if isinstance(error, (OutputParserException, ValidationError, ValueError)):
        return _http_error(502, "模型或流程结果未通过结构校验，请查询线程历史。", thread_id)
    if isinstance(error, (sqlite3.Error, OSError)):
        return _http_error(500, "本地持久化存储操作失败，请检查服务端存储并核对历史。", thread_id)
    return _http_error(500, "助理流程执行失败，请查询线程历史核对实际状态。", thread_id)


def _get_engine(request: Request, thread_id: str | None = None) -> AssistantEngine:
    """取得当前应用生命周期内的引擎，服务尚未初始化时返回明确的不可用状态。"""
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise _http_error(503, "服务尚未完成初始化。", thread_id)
    return engine


def _map_message_error(error: Exception) -> HTTPException:
    """映射消息处理错误并保留已存原文编号，供调用方查询详情和按同一来源幂等键重试。"""
    message_id = getattr(error, "message_id", None)
    if isinstance(error, MessageConflictError):
        return HTTPException(status_code=409, detail={
            "message_id": message_id,
            "message": "相同来源消息编号对应的内容不同，请核对原始输入。",
        })
    if isinstance(error, MessageModelUnavailable):
        return HTTPException(status_code=503, detail={
            "message_id": message_id,
            "message": "模型配置不完整；原消息已保存，请在本机配置后用同一份来源 JSON 重试。",
        })
    cause = error.cause if isinstance(error, MessageProcessingError) else error
    mapped = _map_engine_error(cause, None)
    detail = dict(mapped.detail) if isinstance(mapped.detail, dict) else {"message": str(mapped.detail)}
    detail.pop("thread_id", None)
    detail["message_id"] = message_id
    # 消息导入不属于对话线程；失败后查询原文详情，不让客户端误查线程历史。
    detail["message"] = detail.get("message", "消息处理失败。").replace("线程历史", "消息详情")
    return HTTPException(status_code=mapped.status_code, detail=detail)


def _get_message_store(request: Request) -> MessageStore:
    """取得生命周期内的消息业务存储；只读查询和角色配置不要求模型凭证。"""
    store = getattr(request.app.state, "message_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail={"message_id": None, "message": "消息存储尚未初始化。"})
    return store


def _rag_http_error(status_code: int, message: str, document_id: str | None = None) -> HTTPException:
    """返回文档接口的安全错误正文，保留文档编号但不暴露本地路径或提供商原始信息。"""
    return HTTPException(status_code=status_code, detail={"document_id": document_id, "message": message})


def _map_rag_error(error: Exception, document_id: str | None = None) -> HTTPException:
    """将文档处理内部原因映射为稳定状态码，失败后的业务重试仍由RAG核心负责。"""
    if isinstance(error, HTTPException):
        return error
    cause = error.cause if isinstance(error, RagProcessingError) else error
    document_id = getattr(error, "document_id", None) or document_id
    if isinstance(cause, EmbeddingUnavailable):
        return _rag_http_error(503, "本地向量模型尚不可用，请检查模型下载或运行配置后重试。", document_id)
    if isinstance(cause, RagModelUnavailable):
        return _rag_http_error(503, "回答模型配置不完整，请在本机配置后重试。", document_id)
    if isinstance(cause, APITimeoutError):
        return _rag_http_error(504, "回答模型请求超时，请稍后重试。", document_id)
    if isinstance(cause, APIError):
        return _rag_http_error(502, "回答模型服务请求失败，请稍后重试。", document_id)
    if isinstance(cause, (OutputParserException, ValidationError)):
        return _rag_http_error(502, "回答结果未通过结构校验，请重试或检查模型输出。", document_id)
    if isinstance(cause, ValueError):
        return _rag_http_error(422, "文件、绑定信息或检索参数不符合要求，请核对后重试。", document_id)
    if isinstance(cause, (sqlite3.Error, OSError)):
        return _rag_http_error(500, "本地文档存储操作失败，请检查服务端存储状态。", document_id)
    return _rag_http_error(500, "文档处理失败，请查询文档状态后重试。", document_id)


def _get_rag(request: Request) -> RagEngine:
    """取得当前应用的RAG引擎，不触发向量模型加载或文本模型调用。"""
    rag = getattr(request.app.state, "rag", None)
    if rag is None:
        raise _rag_http_error(503, "文档服务尚未初始化。")
    return rag


def _get_rag_store(request: Request) -> RagStore:
    """取得文档存储供列表、详情和原件读取使用，这些操作不依赖模型配置。"""
    store = getattr(request.app.state, "rag_store", None)
    if store is None:
        raise _rag_http_error(503, "文档存储尚未初始化。")
    return store


def _automation_error(error: Exception, reminder_id: str | None = None) -> HTTPException:
    """隐藏存储、邮箱和通知通道内部信息，保留提醒编号供调用方重新读取状态。"""
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, ReminderConflictError):
        return HTTPException(status_code=409, detail={
            "reminder_id": error.reminder_id, "message": "提醒版本已变化，请重新读取列表后修改。",
        })
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail={
            "reminder_id": reminder_id, "message": "提醒或邮箱操作参数不符合要求，请核对任务、时间与版本。",
        })
    return HTTPException(status_code=500, detail={
        "reminder_id": reminder_id, "message": "本地自动化操作失败，请查询状态后重试。",
    })


def _get_automation(request: Request, name: str):
    """读取生命周期内的提醒或邮箱组件，不在请求中建立额外数据库或后台任务。"""
    component = getattr(request.app.state, name, None)
    if component is None:
        raise HTTPException(status_code=503, detail={"message": "自动化服务尚未初始化。"})
    return component


def create_app(
    business_db: Path | str | None = None,
    checkpoint_db: Path | str | None = None,
    model_factory=None,
    rag_dir: Path | str | None = None,
    embedding_factory=None,
    notifier_factory=None,
    mail_settings: MailSettings | None = None,
    mail_collector_factory=None,
    background_enabled: bool = True,
) -> FastAPI:
    """创建服务配置；显式路径优先，仅在 lifespan 启动时打开业务库和检查点连接。"""
    business_path = Path(
        business_db if business_db is not None else os.environ.get(
            "STUDENT_ASSISTANT_DB", ROOT / "local-data" / "assistant.sqlite3"
        )
    ).resolve()
    checkpoint_path = Path(
        checkpoint_db if checkpoint_db is not None else os.environ.get(
            "STUDENT_ASSISTANT_CHECKPOINT_DB", ROOT / "local-data" / "assistant_checkpoints.sqlite3"
        )
    ).resolve()
    rag_path = Path(rag_dir if rag_dir is not None else business_path.parent / (business_path.stem + "_rag")).resolve()
    mail_path = business_path.parent / (business_path.stem + "_mail")

    @asynccontextmanager
    async def lifespan(api: FastAPI):
        """启动存储与本地轮询，先等待在途任务结束，再关闭文档及检查点资源。"""
        engine = AssistantEngine(
            business_db=business_path, checkpoint_db=checkpoint_path, model_factory=model_factory
        )
        async with engine:
            api.state.engine = engine
            api.state.message_store = None
            api.state.message_processor = None
            api.state.rag = None
            api.state.rag_store = None
            api.state.reminder_store = None
            api.state.reminder_runtime = None
            api.state.mail_store = None
            api.state.mail_runtime = None
            api.state.trace_store = None
            rag_store = None
            stop_event = asyncio.Event()
            workers: list[asyncio.Task] = []
            try:
                api.state.trace_store = await asyncio.to_thread(TraceStore, engine.store)
                # 消息表使用同一业务库；建表不占用异步事件循环，也不调用模型。
                api.state.message_store = await asyncio.to_thread(MessageStore, engine.store)
                api.state.message_processor = MessageProcessor(
                    api.state.message_store, model_factory=engine.model_factory
                )
                # 仅创建配置对象；LocalEmbedder在真正索引或检索时才下载/加载模型。
                embedder = embedding_factory() if embedding_factory is not None else LocalEmbedder(
                    cache_dir=Path(os.environ.get("STUDENT_ASSISTANT_EMBEDDING_CACHE") or ROOT / "local-data" / "embedding_cache")
                )
                rag_store = await asyncio.to_thread(
                    RagStore, engine.store, rag_path, embedder.model_id, embedder.dimension
                )
                rag = RagEngine(rag_store, embedder, model_factory=engine.model_factory)
                api.state.rag_store = rag_store
                api.state.rag = rag
                engine.document_search = rag.search_sync
                api.state.reviews = await asyncio.to_thread(ReviewStore, engine.store)
                api.state.classifier = DocumentClassifier(api.state.reviews, model_factory=engine.model_factory)
                api.state.attachments = await asyncio.to_thread(AttachmentStore, api.state.message_store, business_path.parent / (business_path.stem + '_attachments'))
                api.state.bridge_configuration = BridgeConfiguration(business_path.parent / (business_path.stem + '_bridge.json'))
                api.state.notification_store = await asyncio.to_thread(NotificationStore, api.state.message_store)
                api.state.message_processor.source_metadata = api.state.notification_store.metadata
                api.state.notification_runtime = NotificationRuntime(api.state.notification_store, api.state.message_processor)
                api.state.attention_store = await asyncio.to_thread(AttentionStore, api.state.message_store)
                api.state.attention_runtime = AttentionRuntime(api.state.attention_store, notifier=notifier_factory() if notifier_factory is not None else None)
                api.state.message_processor.on_received = api.state.attention_runtime.register
                reminder_store = await asyncio.to_thread(ReminderStore, engine.store)
                notifier = notifier_factory() if notifier_factory is not None else None
                reminders = ReminderRuntime(reminder_store, notifier=notifier)
                api.state.reminder_store = reminder_store
                api.state.reminder_runtime = reminders
                # 仅新处理成功的消息触发该钩子；重复导入补计划须显式调用plan接口。
                async def after_received(message):
                    """分析提交后独立规划截止提醒和重点到达记录，任一失败均保留已存原文。"""
                    metadata = await asyncio.to_thread(api.state.notification_store.metadata, message['message_id'])
                    trust_time = api.state.bridge_configuration.read().get('trust_posted_time', False)
                    if metadata and (metadata.get('text_source') == 'aggregate' or (metadata['time_basis'] != 'app_message_timestamp' and not trust_time)):
                        result = {'enabled': False, 'planned': [], 'skipped': [{'task_id': item['task_id'], 'reason': 'notification_source_time_unverified'} for item in message.get('events', [])]}
                    else:
                        result = await reminders.plan_message(message)
                    result['attention'] = await api.state.attention_runtime.register(message)
                    return result

                api.state.message_processor.after_process = after_received
                settings = mail_settings if mail_settings is not None else MailSettings.from_env()
                collector = mail_collector_factory(settings) if mail_collector_factory is not None else ImapCollector(settings)
                mail_store = await asyncio.to_thread(MailStore, engine.store, mail_path)
                mail = MailRuntime(mail_store, collector, api.state.message_processor, rag, reminders, attachments=api.state.attachments, classifier=api.state.classifier)
                api.state.mail_store = mail_store
                api.state.mail_runtime = mail
                if background_enabled:
                    # 提醒只投递已明确建立的计划；邮箱只有显式启用且配置齐全才自动收取。
                    workers.append(asyncio.create_task(reminders.loop(stop_event), name="assistant-reminders"))
                    workers.append(asyncio.create_task(api.state.notification_runtime.loop(stop_event), name='assistant-notification-inbox'))
                    workers.append(asyncio.create_task(api.state.attention_runtime.loop(stop_event), name='assistant-priority-notices'))
                    if settings.enabled and settings.configured:
                        workers.append(asyncio.create_task(mail.loop(stop_event), name="assistant-mail"))
                yield
            finally:
                try:
                    stop_event.set()
                    if workers:
                        # 不取消to_thread：数据库、EML与向量写入结束后才能关闭其依赖。
                        await asyncio.gather(*workers, return_exceptions=True)
                finally:
                    try:
                        # Qdrant本地锁先释放，再由外层引擎关闭checkpoint连接。
                        if rag_store is not None:
                            await asyncio.to_thread(rag_store.close)
                    finally:
                        engine.document_search = None
                        for name in (
                            "rag", "rag_store", "message_processor", "message_store", "engine",
                            "reminder_store", "reminder_runtime", "mail_store", "mail_runtime", "trace_store",
                        ):
                            setattr(api.state, name, None)

    api = FastAPI(title="个人研究与日程助理", version="0.1.0", lifespan=lifespan)
    api.add_middleware(RequestTraceMiddleware, owner=api)

    @api.middleware('http')
    async def restrict_network_access(request: Request, call_next):
        """局域网仅提供令牌通知入口；消息、文档、密钥配对等管理接口只供本机访问。"""
        host = request.client.host if request.client else ''
        local = host in ('testclient', 'localhost')
        try:
            local = local or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        # Compose中的UI容器通过专用内部服务地址访问；部署时显式选择该受控网络模式。
        if not local and os.environ.get('STUDENT_ASSISTANT_TRUST_CONTAINER_NETWORK') != 'true' and request.url.path not in ('/notifications', '/notifications/app.apk'):
            return JSONResponse(status_code=403, content={'detail': {'message': '此接口仅允许本机操作。'}})
        return await call_next(request)

    def require_model_configuration(thread_id: str):
        """仅在推理或恢复时检查模型配置；注入测试工厂和只读查询不要求真实凭证。"""
        if model_factory is None and not model_is_configured():
            raise _http_error(503, "模型配置不完整，请在本机配置 COMPATIBLE_*。", thread_id)

    @api.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        """返回不含原始输入的安全校验错误，消息接口不虚构线程或来源消息编号。"""
        if request.url.path.startswith("/documents"):
            return JSONResponse(status_code=422, content={"detail": {
                "document_id": request.path_params.get("document_id"),
                "message": "请求参数未通过校验，请检查文件名、附件绑定、问题和检索参数。",
            }})
        if request.url.path.startswith(("/messages", "/roles")):
            return JSONResponse(status_code=422, content={"detail": {
                "message_id": None,
                "message": "请求参数未通过校验，请检查来源、消息时间、附件、类别或角色字段。",
            }})
        if request.url.path.startswith(("/reminders", "/mail")):
            return JSONResponse(status_code=422, content={"detail": {
                "reminder_id": request.path_params.get("reminder_id"),
                "message": "请求参数未通过校验，请检查提醒时间、通道、版本或策略字段。",
            }})
        candidate = request.path_params.get("thread_id")
        if candidate is None and isinstance(error.body, dict):
            candidate = error.body.get("thread_id")
        thread_id = candidate if isinstance(candidate, str) and re.fullmatch(
            THREAD_ID_PATTERN, candidate
        ) else uuid4().hex
        return JSONResponse(status_code=422, content={"detail": {
            "thread_id": thread_id,
            "message": "请求参数未通过校验，请检查消息、线程编号及带时区的参考时间。",
        }})

    @api.get("/health")
    def health() -> dict[str, str]:
        """返回服务存活状态，不访问模型或业务记录。"""
        return {"status": "ok"}

    @api.get("/traces")
    def list_traces(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict:
        """查询最近追踪的有限元数据；本接口本身不创建新Trace。"""
        store = getattr(request.app.state, "trace_store", None)
        if store is None:
            raise HTTPException(status_code=503, detail={"message": "追踪存储尚未初始化。"})
        traces = store.list_traces(limit)
        return {"count": len(traces), "traces": traces}

    @api.get("/traces/{trace_id}")
    def trace_detail(trace_id: str, request: Request) -> dict:
        """按服务生成的编号读取节点、父子关系与耗时；不返回原文或工具参数。"""
        if re.fullmatch(r"[0-9a-f]{32}", trace_id) is None:
            raise HTTPException(status_code=422, detail={"message": "追踪编号格式不正确。"})
        store = getattr(request.app.state, "trace_store", None)
        if store is None:
            raise HTTPException(status_code=503, detail={"message": "追踪存储尚未初始化。"})
        detail = store.get_trace(trace_id)
        if detail is None:
            raise HTTPException(status_code=404, detail={"message": "追踪记录不存在或当前不可读。"})
        return detail

    @api.get("/info")
    def info() -> dict[str, Any]:
        """说明当前个人助理配置档及能力，不初始化模型或加载上游示例 Agent。"""
        return {
            "profile": "student",
            "default_agent": "student-assistant",
            "agents": [{"key": "student-assistant", "description": "支持工具调用和持久线程的个人助理"}],
            "capabilities": [
                "tool_calling", "time_interpretation", "sqlite_checkpoint", "thread_history", "explicit_resume",
                "message_ingestion", "role_rules", "source_provenance",
                "document_rag", "persistent_reminders", "optional_qq_imap",
            ],
        }

    @api.post("/invoke", response_model=ChatResponse)
    @api.post("/student-assistant/invoke", response_model=ChatResponse)
    async def invoke(payload: ChatRequest, request: Request) -> dict:
        """为消息生成或复用线程编号，调用主 Agent，并返回仅属于本用户轮次的结果。"""
        # 先确定编号；即使模型请求失败，客户端也能用同一编号查询与恢复。
        thread_id = payload.thread_id or uuid4().hex
        require_model_configuration(thread_id)
        engine = _get_engine(request, thread_id)
        try:
            return await engine.chat(
                payload.message, thread_id, reference_time=payload.reference_time
            )
        except Exception as error:
            raise _map_engine_error(error, thread_id) from None

    @api.post("/threads/{thread_id}/resume", response_model=ChatResponse)
    async def resume(thread_id: ThreadPath, request: Request) -> dict:
        """显式续跑线程的未完成流程，补齐中断时的工具结果并保留当前轮次编号。"""
        require_model_configuration(thread_id)
        engine = _get_engine(request, thread_id)
        try:
            return await engine.resume(thread_id)
        except Exception as error:
            raise _map_engine_error(error, thread_id) from None

    @api.get("/threads/{thread_id}", response_model=ThreadHistory)
    async def history(thread_id: ThreadPath, request: Request) -> dict:
        """读取已保存消息和待恢复节点，不调用模型，也不自动重放任何工具。"""
        engine = _get_engine(request, thread_id)
        try:
            return await engine.history(thread_id)
        except Exception as error:
            raise _map_engine_error(error, thread_id) from None

    def read_tasks(request: Request, status: str | None = None) -> TasksResponse:
        """从该应用独立的业务存储读取待办并校验响应字段，不读取 checkpoint 表。"""
        engine = _get_engine(request)
        try:
            tasks = [TaskRecord.model_validate(item) for item in engine.store.list_tasks(status=status)]
            return TasksResponse(count=len(tasks), tasks=tasks)
        except Exception as error:
            raise _map_engine_error(error, None) from None

    @api.get("/tasks", response_model=TasksResponse)
    def tasks(request: Request) -> TasksResponse:
        """查询全部业务待办，不要求模型配置。"""
        return read_tasks(request)

    @api.get("/tasks/pending", response_model=TasksResponse)
    def pending_tasks(request: Request) -> TasksResponse:
        """只查询未完成待办，供状态修改后核对剩余事项。"""
        return read_tasks(request, status="pending")

    @api.get("/tasks/pending/count")
    def pending_count(request: Request) -> dict[str, int]:
        """由业务数据库直接统计未完成数量，避免将全部记录加载到内存。"""
        engine = _get_engine(request)
        try:
            return {"count": engine.store.count_pending()}
        except Exception as error:
            raise _map_engine_error(error, None) from None

    @api.post("/tasks/{task_id}/complete")
    def complete_task_from_ui(
        task_id: Annotated[str, APIPath(pattern=r"^[0-9a-f]{16}$")], request: Request,
    ) -> dict:
        """完成用户在界面明确选择的一项待办；同库触发器停止其未提交提醒，不调用模型。"""
        try:
            task = _get_engine(request).store.mark_completed(task_id)
        except TaskCancelledError:
            raise HTTPException(status_code=409, detail={"message": "事项已取消，请先明确重新开启后再完成。"}) from None
        except Exception as error:
            raise _map_engine_error(error, None) from None
        if task is None:
            raise HTTPException(status_code=404, detail={"message": "待办不存在，请刷新列表后再操作。"})
        return {"status": "completed", "task": task}

    @api.post("/messages")
    async def ingest_message(payload: MessageInput, request: Request) -> dict:
        """先由处理器持久化完整原消息，再提取业务结果；重复输入沿用来源幂等键。"""
        processor = getattr(request.app.state, "message_processor", None)
        if processor is None:
            raise HTTPException(status_code=503, detail={"message_id": None, "message": "消息处理器尚未初始化。"})
        try:
            # 不在服务层预检模型：配置缺失时也要先保存原文并返回可查询编号。
            result = await processor.process(payload)
            result['message'] = await asyncio.to_thread(_message_view, request, result['message'])
            return result
        except Exception as error:
            raise _map_message_error(error) from None

    @api.get("/messages")
    def list_messages(
        request: Request,
        category: Literal["研究", "学生生活", "求职", "其他"] | None = None,
        important_only: bool = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        label: str | None = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        query: Annotated[str | None, Query(max_length=200)] = None,
    ) -> dict:
        """按类别和重点角色筛选持久化消息，返回实际列表及本次查询数量。"""
        store = _get_message_store(request)
        try:
            # 在SQL分页前按人工修订后的类别、标签和搜索条件筛选，历史消息不会被最新100条截断。
            selected = store.list_reviewed_messages(category=category, important_only=important_only, label=label, limit=limit, offset=offset, query=query)
            messages = [_message_view(request, item) for item in selected]
            total = store.count_reviewed_messages(category=category, important_only=important_only, label=label, query=query)
            return {"count": len(messages), "total": total, "offset": offset, "messages": messages}
        except Exception as error:
            raise _map_message_error(error) from None

    @api.get("/messages/{message_id}")
    def message_detail(message_id: str, request: Request) -> dict:
        """读取原文及其处理详情；编号不存在时返回404，不调用模型补造内容。"""
        store = _get_message_store(request)
        try:
            detail = store.get_message(message_id)
        except Exception as error:
            raise _map_message_error(error) from None
        if detail is None:
            raise HTTPException(status_code=404, detail={"message_id": message_id, "message": "消息不存在。"})
        return _message_view(request, detail)

    @api.put("/roles")
    def upsert_role(payload: RoleRule, request: Request) -> dict:
        """保存用户明确给出的来源身份角色规则；不从昵称猜测或合并身份。"""
        store = _get_message_store(request)
        try:
            return store.upsert_role(payload)
        except Exception as error:
            raise _map_message_error(error) from None

    @api.get("/roles")
    def list_roles(request: Request) -> dict:
        """列出已配置的精确身份规则，供导入前核对重点角色设置。"""
        store = _get_message_store(request)
        try:
            roles = store.list_roles()
            return {"count": len(roles), "roles": roles}
        except Exception as error:
            raise _map_message_error(error) from None

    @api.post("/documents")
    async def index_document(
        request: Request,
        filename: Annotated[str, Query(min_length=1, max_length=255)],
        message_id: Annotated[str | None, Query(min_length=1)] = None,
        attachment_index: Annotated[int | None, Query(ge=0)] = None,
    ) -> dict:
        """接收真实文件字节并可关联消息附件，按流限制大小，禁止以服务器路径代替上传。"""
        if (message_id is None) != (attachment_index is None):
            raise _rag_http_error(422, "message_id与attachment_index必须成对提供。")
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/octet-stream":
            raise _rag_http_error(415, "文件上传必须使用application/octet-stream。")
        declared_size = request.headers.get("content-length", "")
        if declared_size.isdigit() and int(declared_size) > MAX_FILE_BYTES:
            raise _rag_http_error(413, "上传文件超过10MB上限。")
        data = bytearray()
        try:
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_FILE_BYTES:
                    raise _rag_http_error(413, "上传文件超过10MB上限。")
                data.extend(chunk)
            return await _get_rag(request).index(
                filename, bytes(data), message_id=message_id, attachment_index=attachment_index
            )
        except Exception as error:
            raise _map_rag_error(error) from None

    @api.get("/documents")
    def list_documents(request: Request) -> dict:
        """列出已保存文档及数量，读取元数据时不加载向量模型或调用文本模型。"""
        try:
            documents = [request.app.state.reviews.document_overlay(item) for item in _get_rag_store(request).list_documents()]
            return {"count": len(documents), "documents": documents}
        except Exception as error:
            raise _map_rag_error(error) from None

    @api.get("/documents/{document_id}")
    def document_detail(document_id: str, request: Request) -> dict:
        """返回文档元数据和处理状态，未知编号返回404。"""
        try:
            document = _get_rag_store(request).get_document(document_id)
        except Exception as error:
            raise _map_rag_error(error, document_id) from None
        if document is None:
            raise _rag_http_error(404, "文档不存在。", document_id)
        return request.app.state.reviews.document_overlay(document)

    @api.get("/documents/{document_id}/file")
    def document_file(document_id: str, request: Request) -> Response:
        """下载已取得的原件字节，用编码后的文件名构造响应头，不公开服务器存储路径。"""
        store = _get_rag_store(request)
        try:
            document = store.get_document(document_id)
            if document is None:
                raise _rag_http_error(404, "文档不存在。", document_id)
            data = store.read_document(document_id)
        except FileNotFoundError:
            raise _rag_http_error(404, "文档原件不存在。", document_id) from None
        except Exception as error:
            raise _map_rag_error(error, document_id) from None
        encoded_name = quote(document.get("filename", document_id), safe="")
        return Response(content=data, media_type="application/octet-stream", headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + encoded_name,
        })

    @api.post("/documents/search")
    async def search_documents(payload: SearchRequest, request: Request) -> dict:
        """用本地向量模型检索文件片段，返回核心给出的真实来源和分数。"""
        try:
            return await _get_rag(request).search(payload)
        except Exception as error:
            raise _map_rag_error(error, payload.document_id) from None

    @api.post("/documents/ask")
    async def ask_documents(payload: AskRequest, request: Request) -> dict:
        """基于检索片段生成带来源回答，是否存在证据及是否调用模型由核心决定。"""
        try:
            return await _get_rag(request).ask(payload)
        except Exception as error:
            raise _map_rag_error(error, payload.document_id) from None

    @api.get("/reminders/policy")
    def read_reminder_policy(request: Request) -> dict:
        """读取用户显式保存的自动提醒规则，不补建旧消息或自动开启提醒。"""
        try:
            return _get_automation(request, "reminder_store").get_policy()
        except Exception as error:
            raise _automation_error(error) from None

    @api.put("/reminders/policy")
    def save_reminder_policy(payload: ReminderPolicy, request: Request) -> dict:
        """保存自动规则；日期级事项只有显式提供date_only_time才具有提醒钟点。"""
        try:
            return _get_automation(request, "reminder_store").set_policy(payload.model_dump())
        except Exception as error:
            raise _automation_error(error) from None

    @api.post("/reminders/plan")
    async def plan_reminders(payload: ReminderPlanRequest, request: Request) -> dict:
        """对指定已保存消息显式补建提醒；沿用幂等计划且保留原事件的时间精度。"""
        try:
            message = await asyncio.to_thread(_get_message_store(request).get_message, payload.message_id)
            if message is None:
                raise HTTPException(status_code=404, detail={"message_id": payload.message_id, "message": "消息不存在。"})
            return await _get_automation(request, "reminder_runtime").plan_message(message)
        except Exception as error:
            raise _automation_error(error) from None

    @api.post("/reminders/run-once")
    async def run_reminders(request: Request) -> dict:
        """显式认领和投递当前到期计划，返回提交或失败数量，不把提交等同用户已读。"""
        try:
            return await _get_automation(request, "reminder_runtime").run_once()
        except Exception as error:
            raise _automation_error(error) from None

    @api.post("/reminders")
    def create_reminder(payload: ReminderCreate, request: Request) -> dict:
        """按用户明确提供的带时区时间给待办创建提醒，相同计划由存储幂等复用。"""
        try:
            return _get_automation(request, "reminder_store").save(**payload.model_dump())
        except Exception as error:
            raise _automation_error(error) from None

    @api.get("/reminders")
    def list_reminders(request: Request) -> dict:
        """列出各提醒当前版本和投递状态，不主动发送通知。"""
        try:
            reminders = _get_automation(request, "reminder_store").list_reminders()
            return {"count": len(reminders), "reminders": reminders}
        except Exception as error:
            raise _automation_error(error) from None

    @api.put("/reminders/{reminder_id}")
    def edit_reminder(reminder_id: str, payload: ReminderUpdate, request: Request) -> dict:
        """用期望版本修改提醒时间及通道，防止覆盖并发编辑且不替换关联任务。"""
        try:
            store = _get_automation(request, "reminder_store")
            current = store.get(reminder_id)
            if current is None:
                raise HTTPException(status_code=404, detail={"reminder_id": reminder_id, "message": "提醒不存在。"})
            return store.save(current["task_id"], reminder_id=reminder_id, **payload.model_dump())
        except Exception as error:
            raise _automation_error(error, reminder_id) from None

    @api.post("/reminders/{reminder_id}/cancel")
    def cancel_reminder(
        reminder_id: str, request: Request,
        expected_version: Annotated[int | None, Query(ge=1)] = None,
    ) -> dict:
        """取消尚未提交的提醒，保留任务状态和已经交给系统的历史提交记录。"""
        try:
            reminder = _get_automation(request, "reminder_store").cancel(reminder_id, expected_version=expected_version)
            if reminder is None:
                raise HTTPException(status_code=404, detail={"reminder_id": reminder_id, "message": "提醒不存在。"})
            return reminder
        except Exception as error:
            raise _automation_error(error, reminder_id) from None

    @api.get("/mail/status")
    def mail_status(request: Request) -> dict:
        """返回邮箱安全运行状态；不返回账号、授权码，也不建立IMAP连接。"""
        return _get_automation(request, "mail_runtime").status()

    @api.get("/mail/jobs")
    def mail_jobs(request: Request) -> dict:
        """查询当前配置账号的持久收件任务，保留失败类型供本机核对与重试。"""
        try:
            runtime = _get_automation(request, "mail_runtime")
            jobs = _get_automation(request, "mail_store").list_jobs(runtime.settings.account)
            return {"count": len(jobs), "jobs": jobs}
        except Exception as error:
            raise _automation_error(error) from None

    @api.post("/mail/sync")
    async def sync_mail(request: Request) -> dict:
        """显式只读收取邮件并重试本地失败队列；不发送、移动或修改远端邮件。"""
        runtime = _get_automation(request, "mail_runtime")
        if not runtime.settings.configured:
            raise HTTPException(status_code=503, detail={"message": "QQ邮箱尚未配置，请在本机运行configure_mail.py。"})
        try:
            return await runtime.sync(retry_failed=True)
        except Exception:
            raise HTTPException(status_code=503, detail={"message": "邮箱同步未能完成，请查询安全状态与本地任务列表后重试。"}) from None

    @api.put('/messages/{message_id}/review')
    def review_message(message_id: str, payload: MessageReview, request: Request):
        """保存人工分类、标签或摘要，原消息与原模型分析保持可查询。"""
        try:
            request.app.state.reviews.apply_message(message_id, payload)
            return {'status': 'updated', 'message': _message_view(request, _get_message_store(request).get_message(message_id))}
        except Exception as error:
            raise _review_error(error) from None

    @api.get('/messages/{message_id}/reviews')
    def message_reviews(message_id: str, request: Request):
        """查询消息修订历史，包括首次人工纠正前的零版。"""
        return {'history': request.app.state.reviews.get_history('message', message_id)}

    @api.put('/tasks/{task_id}/review')
    async def review_event(task_id: str, payload: EventReview, request: Request, replan: bool = False):
        """事务纠正事项并取消旧待投递计划；明确选择时按新时间重排自动提醒。"""
        try:
            state = request.app.state
            event = await asyncio.to_thread(state.reviews.apply_event, task_id, payload)
            result = {'status': 'updated', 'event': event}
            if replan and event['status'] == 'pending':
                try:
                    result['reminder_planning'] = await state.reminder_runtime.plan_message({'events': [event]}, replace_existing=True)
                except Exception as error:
                    result['reminder_planning'] = {'error_code': type(error).__name__, 'retry_path': f'/tasks/{task_id}/replan'}
            return result
        except Exception as error:
            raise _review_error(error) from None

    @api.post('/tasks/{task_id}/replan')
    async def replan_event(task_id: str, request: Request):
        """在已保存修订后显式重试当前事项自动计划，不再次修改事项或原文。"""
        with request.app.state.reviews._connection() as connection:
            try:
                event = request.app.state.reviews._event(connection, task_id)
            except KeyError:
                raise HTTPException(404, detail={'message': '事项不存在。'}) from None
        event['task_id'] = task_id
        return await request.app.state.reminder_runtime.plan_message({'events': [event]}, replace_existing=True)

    @api.get('/tasks/{task_id}/reviews')
    def event_reviews(task_id: str, request: Request):
        """查询事项修订记录，保留时间与取消操作的追溯信息。"""
        return {'history': request.app.state.reviews.get_history('event', task_id)}

    @api.put('/documents/{document_id}/review')
    def review_document(document_id: str, payload: DocumentReview, request: Request):
        """保存文件人工分类与多标签，正文分类证据仍单独保留。"""
        try:
            request.app.state.reviews.apply_document(document_id, payload)
            return {'status': 'updated', 'document': request.app.state.reviews.document_overlay(_get_rag_store(request).get_document(document_id))}
        except Exception as error:
            raise _review_error(error) from None

    @api.post('/documents/{document_id}/classify')
    async def classify_document(document_id: str, request: Request):
        """明确调用模型依据已提取正文分类，返回实际阅读范围和逐字出处。"""
        try:
            result = await request.app.state.classifier.classify(document_id)
            return {'status': 'classified', 'classification': result, 'document': request.app.state.reviews.document_overlay(_get_rag_store(request).get_document(document_id))}
        except Exception as error:
            raise _review_error(error) from None

    @api.get('/documents/{document_id}/reviews')
    def document_reviews(document_id: str, request: Request):
        """查询文件人工分类修订历史，不重复调用模型。"""
        return {'history': request.app.state.reviews.get_history('document', document_id)}

    @api.post('/messages/{message_id}/attachments/{attachment_index}')
    async def archive_attachment(message_id: str, attachment_index: int, request: Request, filename: str):
        """接受真实附件字节并绑定已有线索，任何格式均可保存，不执行附件。"""
        if request.headers.get('content-type', '').split(';')[0] != 'application/octet-stream':
            raise HTTPException(415, detail={'message': '附件必须以原始字节上传。'})
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 25 * 1024 * 1024:
                raise HTTPException(413, detail={'message': '附件不能超过25MB。'})
            data.extend(chunk)
        try:
            original = await asyncio.to_thread(request.app.state.attachments.save, message_id, attachment_index, filename, bytes(data))
            return {'status': 'available', 'attachment': original}
        except Exception as error:
            raise _review_error(error) from None

    @api.get('/messages/{message_id}/attachments/{attachment_index}/file')
    def attachment_file(message_id: str, attachment_index: int, request: Request):
        """优先下载独立归档原件，也兼容此前通过RAG取得并绑定的原件。"""
        try:
            saved = request.app.state.attachments.read(message_id, attachment_index)
            if saved is not None:
                info, data = saved
                return Response(data, media_type='application/octet-stream', headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(info['filename'], safe='')})
            message = _get_message_store(request).get_message(message_id)
            if not message or not 0 <= attachment_index < len(message['attachments']):
                raise KeyError(attachment_index)
            document_id = message['attachments'][attachment_index].get('document_id')
            if not document_id:
                raise KeyError(attachment_index)
            return document_file(document_id, request)
        except Exception as error:
            raise _review_error(error) from None

    @api.get('/attention/policy')
    def get_attention_policy(request: Request):
        """读取重点联系人到达提醒开关，与截止提醒策略分别配置。"""
        return request.app.state.attention_store.get_policy()

    @api.put('/attention/policy')
    def set_attention_policy(payload: AttentionPolicy, request: Request):
        """保存重点联系人新消息的加重提醒规则，不补建历史消息通知。"""
        return request.app.state.attention_store.set_policy(payload.model_dump())

    @api.get('/attention')
    def list_attention(request: Request):
        """查询重点联系人到达记录和用户已读状态，不发送弹窗。"""
        return {'alerts': request.app.state.attention_store.list()}

    @api.post('/attention/{alert_id}/read')
    def read_attention(alert_id: str, request: Request):
        """由用户确认一条重点消息已读，未开始投递的到达弹窗随之停止。"""
        result = request.app.state.attention_store.acknowledge(alert_id)
        if result is None:
            raise HTTPException(404, detail={'message': '到达提醒不存在。'})
        return result

    @api.get('/notifications/status')
    def notification_status(request: Request):
        """查看手机接入启用状态、设备最后连接及队列数量，不返回配对令牌。"""
        configuration = request.app.state.bridge_configuration.read()
        return {'enabled': configuration['enabled'], 'configured': bool(configuration['token']), 'trust_posted_time': configuration.get('trust_posted_time', False), 'listen_host': os.environ.get('STUDENT_ASSISTANT_BIND_HOST', '127.0.0.1'), **request.app.state.notification_store.status()}

    @api.get('/notifications/app.apk')
    def phone_app():
        """提供固定的已构建通知助手APK，供同网手机无线下载安装；不提供任意文件访问。"""
        path = ROOT / 'android-notification-bridge' / 'dist' / 'student-assistant-bridge.apk'
        if not path.is_file():
            raise HTTPException(404, detail={'message': '安卓安装包尚未放到当前交付目录。'})
        return Response(path.read_bytes(), media_type='application/vnd.android.package-archive', headers={'Content-Disposition': 'attachment; filename="student-assistant-bridge.apk"'})

    @api.post('/notifications/config')
    def configure_notification(payload: BridgeSetup, request: Request):
        """仅在本机明确操作时生成或查看配对信息，令牌只授权通知投递端点。"""
        import socket
        value = request.app.state.bridge_configuration.configure(payload.enabled, payload.rotate, payload.trust_posted_time)
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET) if not ipaddress.ip_address(item[4][0]).is_loopback})
        return {**value, 'upload_urls': [f'http://{address}:{request.url.port or 8002}/notifications' for address in addresses], 'completeness': 'notification_excerpt'}

    @api.post('/notifications', status_code=202)
    async def receive_notification(payload: NotificationInput, request: Request):
        """令牌验证后先持久接收再异步分析，202仅表示已入队而不是模型处理已完成。"""
        if not request.app.state.bridge_configuration.accepts(request.headers.get('authorization')):
            raise HTTPException(401, detail={'message': '通知接入未启用或配对令牌无效。'})
        return await asyncio.to_thread(request.app.state.notification_store.receive, payload)

    return api


# 导入时只描述应用；数据库连接和模型调用均推迟到明确的生命周期或请求内。
app = create_app()
