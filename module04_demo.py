"""模块四综合演示：导入一条教学通知，并为其中事项安排几秒后的独立测试提醒。"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx


def main() -> None:
    """调用正式服务的消息与提醒接口；测试提醒独立于事项真实时间，便于立即观察。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8002")
    parser.add_argument("--channel", choices=("windows", "console"), default="windows")
    parser.add_argument("--delay", type=int, default=8, help="几秒后提交测试提醒，默认8秒")
    args = parser.parse_args()
    if not 1 <= args.delay <= 300:
        parser.error("--delay 必须在1到300秒之间")
    now = datetime.now(timezone(timedelta(hours=8)))
    notice = {
        "source": "manual", "account_id": "demo", "source_message_id": "module04-" + uuid4().hex,
        "sender_id": "demo-teacher-001", "sender_name": "示例老师", "sent_at": now.isoformat(),
        "text": "明天下午三点在实验楼302开组会；周五前交综述，附件是两篇参考论文。",
        "attachments": [{"filename": "reference-a.pdf"}, {"filename": "reference-b.pdf"}],
    }
    with httpx.Client(base_url=args.url.rstrip("/"), timeout=90, trust_env=False) as client:
        # 使用新来源ID，避免已导入的旧消息绕过新消息自动计划回调。
        response = client.post("/messages", json=notice)
        response.raise_for_status()
        processed = response.json()
        events = processed["message"]["events"]
        if not events:
            raise SystemExit("模型本次没有提取事项，请查看原消息与分析结果后重试。")
        reminder = client.post("/reminders", json={
            "task_id": events[0]["task_id"], "channel": args.channel,
            "remind_at": (datetime.now(timezone.utc) + timedelta(seconds=args.delay)).isoformat(),
        })
        reminder.raise_for_status()
        print(json.dumps({
            "message_id": processed["message"]["message_id"], "events": events,
            "automatic_planning": processed.get("reminder_planning"), "test_reminder": reminder.json(),
        }, ensure_ascii=False, indent=2))
        print("保持服务运行；测试提醒到期后查询 --reminders。submitted 表示已交给显示通道。")


if __name__ == "__main__":
    main()
