"""邮件后台编排：增量拉取、原件留存、消息分析和真实附件索引。"""

import asyncio
from pathlib import Path

from student_assistant.mail_ingest import parse_mail
from student_assistant.tracing import span


class MailRuntime:
    """将阻塞 IMAP 放到线程，把已落盘邮件逐条送入已有业务工作流。"""

    def __init__(self, store, collector, processor, rag, reminders, attachments=None, classifier=None):
        """注入已初始化的共享业务依赖；构造本身不联网、不读取邮箱。"""
        self.store = store
        self.collector = collector
        self.settings = collector.settings
        self.processor = processor
        self.rag = rag
        self.reminders = reminders
        self.attachments = attachments
        self.classifier = classifier
        self._lock = asyncio.Lock()
        self.running = False
        self.last_error = None
        self.last_result = None

    def status(self) -> dict:
        """仅公开开关、配置是否齐全和运行状态，不回显邮箱、授权码或原始异常。"""
        return {
            "enabled": self.settings.enabled, "configured": self.settings.configured,
            "running": self.running, "poll_seconds": self.settings.poll_seconds,
            "last_error": self.last_error, "last_result": self.last_result,
        }

    async def sync(self, retry_failed: bool = False, stop_event: asyncio.Event | None = None) -> dict:
        """单次增量同步；先保存 EML，再处理业务，失败记录留在数据库等待重试。"""
        if not self.settings.configured:
            raise ValueError("QQ 邮箱配置不完整，请在本机完成账号及授权码配置")
        async with self._lock:
            self.running = True
            result = {"received": 0, "processed": 0, "failed": 0, "skipped": 0}
            try:
                if retry_failed:
                    await asyncio.to_thread(self.store.retry_failed, self.settings.account)
                recent = {'received': 0, 'priority_job_ids': []}
                fetch_recent = getattr(self.collector, 'fetch_recent', None)
                if callable(fetch_recent):
                    try:
                        with span('mail.collect_recent'):
                            recent = await asyncio.to_thread(fetch_recent, self.store)
                    except Exception as error:
                        # 最近新信的短连接失败不阻止普通补收，也不清空已有待处理队列。
                        result['recent_collection_error'] = getattr(error, 'error_code', type(error).__name__)
                try:
                    with span("mail.collect"):
                        fetched = await asyncio.to_thread(self.collector.fetch_new, self.store)
                    # 正常增量新到的邮件也要优先于已落库的业务积压；内部ID不返回给状态接口。
                    normal_priority = fetched.pop('received_job_ids', [])
                    result.update(fetched)
                except Exception as error:
                    # 已落盘的邮件不依赖本轮联网成功；断网时也继续清理本地待处理队列。
                    result["collection_error"] = getattr(error, "error_code", type(error).__name__)
                    normal_priority = []
                result['received'] += recent['received']
                jobs = await asyncio.to_thread(
                    self.store.pending_jobs, self.settings.account, self.settings.batch_size,
                    priority_job_ids=(recent['priority_job_ids'] + normal_priority)[:100],
                )
                for job in jobs:
                    if stop_event is not None and stop_event.is_set():
                        break
                    try:
                        await self._process_job(job)
                    except Exception as error:
                        # 解析失败需要修正原邮件/规则；外部模型和本地临时故障可以退避重试。
                        await asyncio.to_thread(
                            self.store.mark_failure, job["job_id"], type(error).__name__,
                            retryable=not isinstance(error, ValueError),
                        )
                        result["failed"] += 1
                    else:
                        result["processed"] += 1
                self.last_error = result.get("collection_error") or result.get("recent_collection_error") or result.get("error_code")
                self.last_result = result
                return result
            except Exception as error:
                self.last_error = type(error).__name__
                raise
            finally:
                self.running = False

    async def _process_job(self, job: dict) -> None:
        """从受管 EML 提取消息和附件；同一邮件重放复用消息及文档稳定编号。"""
        with span("mail.process"):
            await self._process_saved_job(job)

    async def _process_saved_job(self, job: dict) -> None:
        """处理已保存邮件并保留原有重试顺序，Trace上下文不会写入邮件或模型输入。"""
        with span("mail.parse"):
            raw = await asyncio.to_thread(self.store.read_raw, job["job_id"])
            parsed = await asyncio.to_thread(
                parse_mail, raw, job["account_id"], job["mailbox"], job["uidvalidity"], job["uid"]
            )
        output = await self.processor.process(parsed.envelope)
        message_id = output["message"]["message_id"]
        # 邮件队列重试时消息分析可能已成功，显式幂等补计划覆盖上次计划失败的情形。
        await self.reminders.plan_message(output["message"])
        attachment_results = []
        for attachment in parsed.attachments:
            # 所有格式先保留可下载原件；RAG暂不支持不影响用户取得真实文件。
            if self.attachments is not None:
                await asyncio.to_thread(self.attachments.save, message_id, attachment.index, attachment.filename, attachment.data, attachment.media_type)
            if Path(attachment.filename).suffix.lower() not in (".txt", ".md", ".pdf"):
                attachment_results.append({
                    "attachment_index": attachment.index, "filename": attachment.filename,
                    "status": "available" if self.attachments is not None else "preserved_in_eml", "reason": "rag_format_unsupported",
                })
                continue
            # 先前成功的内容索引按哈希复用，失败附件不会使原邮件或已提取事项丢失。
            indexed = await self.rag.index(
                attachment.filename, attachment.data,
                message_id=message_id, attachment_index=attachment.index,
            )
            attachment_results.append({
                "attachment_index": attachment.index, "filename": attachment.filename,
                "status": "indexed", "document_id": indexed["document"]["document_id"],
            })
            if self.classifier is not None:
                try:
                    # 收到可提取正文的文件后自动分类，失败保留索引与原件，允许界面单独重试。
                    classified = await self.classifier.classify(indexed['document']['document_id'])
                    attachment_results[-1]['classification_status'] = classified['status']
                except Exception as error:
                    attachment_results[-1]['classification_status'] = 'failed'
                    attachment_results[-1]['classification_error'] = type(error).__name__
        await asyncio.to_thread(
            self.store.mark_success, job["job_id"], message_id, attachment_results=attachment_results
        )

    async def loop(self, stop_event: asyncio.Event) -> None:
        """按配置轮询邮箱；关闭时等待有限超时的在途 IMAP 完成，再释放共享资源。"""
        while not stop_event.is_set():
            if self.settings.enabled and self.settings.configured:
                try:
                    await self.sync(stop_event=stop_event)
                except Exception:
                    # sync 已记录安全异常类型；下一轮继续，不让一个邮箱故障终止提醒 worker。
                    pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.settings.poll_seconds)
            except TimeoutError:
                pass
