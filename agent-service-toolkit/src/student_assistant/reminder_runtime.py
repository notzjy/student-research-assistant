"""提醒执行器：确定性计划、短事务认领、投递确认和可停止的异步轮询。"""

import asyncio
from datetime import timedelta

from student_assistant.bridge import parse_reference_time
from student_assistant.notification import LocalNotifier
from student_assistant.reminder_guards import event_expiration_reason, utc_now
from student_assistant.tracing import span


class ReminderRuntime:
    """协调持久化计划和本地通知；数据库事务不跨越系统通知调用。"""

    def __init__(self, store, notifier=None, clock=None):
        """注入存储、通知器和当前时钟；测试可固定时刻而保留真实 SQLite 队列。"""
        self.store = store
        self.notifier = LocalNotifier() if notifier is None else notifier
        self.clock = utc_now if clock is None else clock
        self._lock = asyncio.Lock()
        self.last_error = None

    async def plan_message(self, message: dict, *, replace_existing: bool = False) -> dict:
        """按已启用规则计划未完成事项；显式事项重排可替换旧自动提醒而保留投递历史。"""
        with span("reminder.plan"):
            return await self._plan_message(message, replace_existing=replace_existing)

    async def _plan_message(self, message: dict, *, replace_existing: bool = False) -> dict:
        """执行确定性提醒规划；追踪只测量执行，不记录传入消息或计划内容。"""
        policy = await asyncio.to_thread(self.store.get_policy)
        if not policy["enabled"]:
            return {"enabled": False, "planned": [], "skipped": []}
        now = self.clock()
        planned, skipped = [], []
        for event in message.get("events", []):
            task_id = event["task_id"]
            value = event.get("time") or {}
            reason = None
            if event.get("status") != "pending":
                reason = "task_not_pending"
            elif value.get("status") != "resolved":
                reason = "time_not_resolved"
            elif event_expiration_reason(value, now):
                reason = "source_event_expired"
            elif value.get("precision") == "minute" and value.get("at"):
                target = parse_reference_time(value["at"]) - timedelta(minutes=policy["lead_minutes"])
            elif value.get("precision") == "minute_range" and value.get("start"):
                target = parse_reference_time(value["start"]) - timedelta(minutes=policy["lead_minutes"])
            elif value.get("precision") == "date_range" and value.get("start_date"):
                if policy["date_only_time"] is None:
                    reason = "date_only_clock_required"
                else:
                    target = parse_reference_time(value["start_date"] + "T" + policy["date_only_time"] + ":00+08:00")
            elif value.get("precision") == "date" and value.get("date"):
                if policy["date_only_time"] is None:
                    reason = "date_only_clock_required"
                else:
                    # 日期提醒按北京日历设置；不向 event.time 添加 at 或推测截止钟点。
                    target = parse_reference_time(value["date"] + "T" + policy["date_only_time"] + ":00+08:00")
            else:
                reason = "time_range_or_unsupported"
            if reason:
                skipped.append({"task_id": task_id, "reason": reason})
                continue
            if target <= now:
                # 新导入旧邮件不能伪装为停机漏发；明确手工计划仍由save接口处理。
                skipped.append({"task_id": task_id, "reason": "reminder_time_passed"})
                continue
            # 首个计划保持不变；重放消息和变更规则不会悄悄给同一事项再建提醒。
            saved = await asyncio.to_thread(self.store.save_automatic, task_id, target.isoformat(), policy["channel"], replace_existing=replace_existing)
            if saved["status"] == "exists":
                skipped.append({"task_id": task_id, "reason": "already_planned"})
                continue
            planned.append(saved["reminder"])
        return {"enabled": True, "planned": planned, "skipped": skipped}

    async def run_once(self, stop_event: asyncio.Event | None = None) -> dict:
        """一次处理至多二十条；每次只认领一条，避免慢通知耗尽后面任务的租约。"""
        result = {"claimed": 0, "submitted": 0, "failed": 0, "stale": 0, "suppressed": 0}
        async with self._lock:
            for _ in range(20):
                if stop_event is not None and stop_event.is_set():
                    break
                jobs = await asyncio.to_thread(self.store.claim_due, self.clock(), 1)
                if not jobs:
                    break
                job = jobs[0]
                result["claimed"] += 1
                identity = (job["reminder_id"], job["version"], job["lease_token"])
                try:
                    delivered = await asyncio.to_thread(self._send_owned, job)
                except Exception as error:
                    acknowledged = await asyncio.to_thread(
                        self.store.mark_failed, *identity, type(error).__name__, self.clock()
                    )
                    result["failed" if acknowledged else "stale"] += 1
                else:
                    if delivered != "submitted":
                        result[delivered] += 1
                        continue
                    acknowledged = await asyncio.to_thread(
                        self.store.mark_submitted, *identity, self.clock()
                    )
                    result["submitted" if acknowledged else "stale"] += 1
        return result

    def _send_owned(self, job: dict) -> str:
        """投递前核对租约和原事项时间；兼容旧队列，已过期自动来源只留取消记录。"""
        with span("reminder.deliver"):
            identity = (job["reminder_id"], job["version"], job["lease_token"])
            now = self.clock()
            if not self.store.validate_claim(*identity, now):
                return "stale"
            if job.get("origin", "legacy") != "manual":
                source = self.store.get_source_context(job["reminder_id"])
                if source is not None and event_expiration_reason(source.get("time"), now):
                    # 首次投递、重试和升级前遗留队列共用门控；原消息和待办均保留。
                    suppressed = self.store.suppress_claim(*identity, "SourceEventExpired", now)
                    return "suppressed" if suppressed else "stale"
            self.notifier.send(job)
            return "submitted"

    async def loop(self, stop_event: asyncio.Event, poll_seconds: float = 1) -> None:
        """持续处理到期记录，停止时等待在途通知返回；重启后从数据库继续认领。"""
        while not stop_event.is_set():
            try:
                await self.run_once(stop_event)
                self.last_error = None
            except Exception as error:
                self.last_error = type(error).__name__
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
