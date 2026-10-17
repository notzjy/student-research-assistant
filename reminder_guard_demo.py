"""旧邮件误提醒的离线复现：固定时钟、临时SQLite和记录通知器，不连接邮箱或模型。"""

import asyncio
import copy
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from student_assistant.bridge import TaskStore
from student_assistant.message_models import MessageInput
from student_assistant.message_store import MessageStore
from student_assistant.message_workflow import MessageProcessor
from student_assistant.reminder_runtime import ReminderRuntime
from student_assistant.reminder_store import ReminderStore
from student_assistant.trace_store import TraceStore
from student_assistant.tracing import use_trace
from uuid import uuid4


class DemoAnalysisModel:
    """返回包含一项事项的预设分析，以隔离验证程序门控而非模型理解能力。"""

    def with_structured_output(self, schema, *, method):
        """保存真实通知协议，保持演示经过现有Pydantic和证据校验。"""
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        """模拟修复前模型把自动续费告知提取为事项，检查程序仍能挡住旧日期。"""
        return self.schema.model_validate({
            "category": "其他", "summary": "示例会员服务自动续费告知。", "attachment_count": 0,
            "attachment_description": None, "events": [{
                "title": "示例会员自动续费", "time_text": "4天后", "location": None,
                "evidence": "示例会员将在4天后自动续费，无需操作。",
                "time_extraction": {"intent": {
                    "unit": "day", "offset": 4, "weekday": None,
                    "hour": None, "minute": None, "is_deadline": False,
                }, "reason": None},
            }],
        })


class RecordingNotifier:
    """记录会被投递的提醒数量，不调用Windows通知接口。"""

    def __init__(self):
        """建立独立调用记录，方便演示断言历史消息没有触发投递。"""
        self.calls = []

    def send(self, reminder: dict) -> None:
        """只保存有限的模拟投递元数据，不弹窗。"""
        self.calls.append({"reminder_id": reminder["reminder_id"], "origin": reminder["origin"]})


def fixed_now() -> datetime:
    """固定本案例的处理时刻，任何日期重跑均得到相同结果。"""
    return datetime.fromisoformat("2026-10-16T20:00:00+08:00")


async def demonstrate() -> dict:
    """验证新计划拦截、旧自动队列抑制、明确手工补发三个独立路径，并导出Trace。"""
    parent = ROOT / "local-data"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reminder-guard-demo-", dir=parent) as folder:
        tasks = TaskStore(Path(folder) / "demo.sqlite3")
        messages, reminders = MessageStore(tasks), ReminderStore(tasks)
        notifier = RecordingNotifier()
        runtime = ReminderRuntime(reminders, notifier=notifier, clock=fixed_now)
        reminders.set_policy({"enabled": True, "date_only_time": "09:00", "channel": "console"})
        processor = MessageProcessor(messages, model_factory=DemoAnalysisModel, after_process=runtime.plan_message)
        trace_store, trace_id = TraceStore(tasks), uuid4().hex
        trace_store.start_trace(trace_id, "messages.ingest")
        with use_trace(trace_store, trace_id):
            result = await processor.process(MessageInput(
                source="manual", account_id="synthetic", source_message_id="old-mail-demo",
                sender_id="demo-service", sent_at="2021-10-05T10:00:00+08:00",
                text="示例会员将在4天后自动续费，无需操作。",
            ))
        trace_store.finish_trace(trace_id, "completed")
        event = result["message"]["events"][0]
        assert result["reminder_planning"]["skipped"][0]["reason"] == "source_event_expired"
        assert not reminders.list_reminders()
        # 模拟修复前已经创建的旧自动计划，只有临时库里的人造记录会被写入。
        old = reminders.save_automatic(event["task_id"], "2021-10-09T09:00:00+08:00", "console")
        delivery = await runtime.run_once()
        assert delivery["suppressed"] == 1 and not notifier.calls
        # 同一旧事项的显式手工提醒代表用户新选择，保留可控的补发行为。
        manual = reminders.save(event["task_id"], "2026-10-16T19:59:00+08:00", "console")
        manual_delivery = await runtime.run_once()
        assert manual_delivery["submitted"] == 1 and len(notifier.calls) == 1
        report = {
            "mode": "offline_rule_regression", "uses_real_mail_or_model": False,
            "now": fixed_now().isoformat(), "source_event_time": copy.deepcopy(event["time"]),
            "new_automatic_plan": result["reminder_planning"],
            "existing_automatic_delivery": delivery,
            "suppressed_reminder": reminders.get(old["reminder"]["reminder_id"]),
            "explicit_manual_delivery": manual_delivery,
            "manual_reminder": reminders.get(manual["reminder_id"]),
            "simulated_notification_count": len(notifier.calls),
            "trace": trace_store.get_trace(trace_id),
        }
    report["temporary_database_cleaned"] = not Path(folder).exists()
    return report


def main() -> None:
    """运行完整离线演示并保存JSON报告，报告可用于学习和复核原日期精度。"""
    report = asyncio.run(demonstrate())
    target = ROOT / "local-data" / "reminder_guard_report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "new_old_message_plan": report["new_automatic_plan"]["skipped"][0]["reason"],
        "old_automatic_queue_suppressed": report["existing_automatic_delivery"]["suppressed"],
        "explicit_manual_simulated_submission": report["explicit_manual_delivery"]["submitted"],
        "temporary_database_cleaned": report["temporary_database_cleaned"],
    }, ensure_ascii=False, indent=2))
    print("离线报告：", target)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
