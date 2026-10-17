"""个人助理客户端：聊天、消息、研究文档、持久提醒与本机QQ邮箱同步。"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

import httpx


def load_json_input(path: Path) -> dict:
    """按UTF8-sig读取JSON对象，兼容带BOM的输入文件并拒绝列表等非对象内容。"""
    with path.open("r", encoding="utf-8-sig") as source:
        payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError("输入 JSON 必须是对象")
    return payload


def main():
    """校验互斥操作和上下文参数，发送本机HTTP请求并根据结构化结果决定退出状态。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002", help="主服务地址，默认使用本机 8002 端口")
    parser.add_argument("--thread-id", help="复用已有线程；仅允许 1 至 64 个英文字母、数字、下划线或连字符")
    parser.add_argument("--reference-time", help="原消息时间，例如 2026-10-16T20:00:00+08:00")
    # message-id既保留旧详情查询，也可作为文件上传的附件绑定参数，随后显式校验组合。
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--message", help="用户聊天消息；可搭配线程与原消息参考时间")
    modes.add_argument("--history", action="store_true", help="查看指定线程的历史与待恢复节点")
    modes.add_argument("--resume", action="store_true", help="显式恢复指定线程，不追加新的用户消息")
    modes.add_argument("--ingest", type=Path, help="导入完整来源消息 JSON 文件；原发送时间写在文件内")
    modes.add_argument("--role", type=Path, help="保存精确来源身份的角色规则 JSON 文件")
    modes.add_argument("--messages", action="store_true", help="查询已保存的来源消息列表")
    modes.add_argument("--index-file", type=Path, help="上传本地文件原件并建立索引，不向服务传递服务器路径")
    modes.add_argument("--documents", action="store_true", help="列出已保存的文档")
    modes.add_argument("--search", help="检索研究文档中的相关片段")
    modes.add_argument("--ask", help="基于研究文档提出问题并查看回答来源")
    modes.add_argument("--remind", type=Path, help="按JSON中的task_id、remind_at和channel创建明确时间提醒")
    modes.add_argument("--reminders", action="store_true", help="列出提醒当前版本和投递状态")
    modes.add_argument("--cancel-reminder", help="取消指定提醒；不会将关联待办标为完成")
    modes.add_argument("--run-reminders", action="store_true", help="显式投递一次当前到期提醒")
    modes.add_argument("--reminder-policy", type=Path, help="保存自动提醒规则JSON；日期级事项需明确date_only_time")
    modes.add_argument("--get-reminder-policy", action="store_true", help="查询当前自动提醒规则")
    modes.add_argument("--plan-reminders", metavar="MESSAGE_ID", help="按当前规则显式补建一条已保存消息的提醒")
    modes.add_argument("--edit-reminder", type=Path, help="修改提醒JSON，包含reminder_id及expected_version")
    modes.add_argument("--mail-status", action="store_true", help="查看安全邮箱状态，不显示账号或授权码")
    modes.add_argument("--sync-mail", action="store_true", help="显式同步只读邮箱并重试本地失败队列")
    modes.add_argument("--mail-jobs", action="store_true", help="查看本地收件任务和附件处理状态")
    modes.add_argument("--traces", action="store_true", help="查询最近的执行追踪，不调用模型")
    modes.add_argument("--trace", help="查看一次执行的节点、耗时和安全错误类型")
    parser.add_argument("--message-id", help="单独查询消息详情，或与文件上传及附件序号一起绑定来源")
    parser.add_argument("--attachment-index", type=int, help="上传原件对应的消息附件序号，从0开始")
    parser.add_argument("--top-k", type=int, choices=range(1, 11), help="检索或问答返回片段上限，默认5")
    parser.add_argument("--document-id", help="仅在检索或问答时筛选某一文档")
    args = parser.parse_args()
    if args.thread_id is not None and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.thread_id) is None:
        parser.error("--thread-id 只允许 1 至 64 个英文字母、数字、下划线或连字符")
    automation_mode = any((
        args.remind is not None, args.reminders, args.cancel_reminder is not None,
        args.run_reminders, args.reminder_policy is not None, args.get_reminder_policy,
        args.plan_reminders is not None, args.edit_reminder is not None,
        args.mail_status, args.sync_mail, args.mail_jobs, args.traces, args.trace is not None,
    ))
    for name in ("cancel_reminder", "plan_reminders"):
        value = getattr(args, name)
        if value is not None and not value.strip():
            parser.error("提醒或消息编号不能为空")
    if args.trace is not None and re.fullmatch(r"[0-9a-f]{32}", args.trace) is None:
        parser.error("--trace 必须为32位小写十六进制编号")
    primary_mode = any((
        args.message is not None, args.history, args.resume, args.ingest is not None,
        args.role is not None, args.messages, args.index_file is not None,
        args.documents, args.search is not None, args.ask is not None, automation_mode,
    ))
    if not primary_mode and args.message_id is None:
        parser.error("必须选择聊天、消息、文档或自动化操作，或用 --message-id 查询详情")
    if args.message_id is not None:
        if not args.message_id.strip():
            parser.error("--message-id 不能为空")
        if primary_mode and args.index_file is None:
            parser.error("--message-id 只能单独查询详情，或与 --index-file 关联附件")
    if args.index_file is not None:
        if (args.message_id is None) != (args.attachment_index is None):
            parser.error("上传绑定必须同时提供 --message-id 和 --attachment-index")
        if args.attachment_index is not None and args.attachment_index < 0:
            parser.error("--attachment-index 必须大于或等于0")
    elif args.attachment_index is not None:
        parser.error("--attachment-index 只适用于 --index-file")
    retrieval_mode = args.search is not None or args.ask is not None
    if not retrieval_mode and (args.top_k is not None or args.document_id is not None):
        parser.error("--top-k 和 --document-id 只适用于 --search 或 --ask")
    if args.document_id is not None and not args.document_id.strip():
        parser.error("--document-id 不能为空")
    nonchat_mode = any((
        args.ingest is not None, args.role is not None, args.messages, args.message_id is not None,
        args.index_file is not None, args.documents, retrieval_mode, automation_mode,
    ))
    if nonchat_mode:
        if args.thread_id is not None or args.reference_time is not None:
            parser.error("消息、文档和自动化操作不接收 --thread-id 或 --reference-time")
    elif args.history or args.resume:
        if args.thread_id is None:
            parser.error("--history 或 --resume 必须同时指定 --thread-id")
        if args.message is not None or args.reference_time is not None:
            parser.error("历史查询与恢复不接收 --message 或 --reference-time")

    try:
        # trust_env=False 避免本机请求意外经过系统代理，客户端不读取任何模型凭证。
        with httpx.Client(base_url=args.base_url, timeout=180, trust_env=False) as client:
            if args.traces:
                route, method = "/traces", "GET"
                response = client.get(route)
            elif args.trace is not None:
                route, method = f"/traces/{args.trace}", "GET"
                response = client.get(route)
            elif args.remind is not None:
                route, method = "/reminders", "POST"
                response = client.post(route, json=load_json_input(args.remind))
            elif args.reminders:
                route, method = "/reminders", "GET"
                response = client.get(route)
            elif args.cancel_reminder is not None:
                route, method = f"/reminders/{quote(args.cancel_reminder, safe='')}/cancel", "POST"
                response = client.post(route)
            elif args.run_reminders:
                route, method = "/reminders/run-once", "POST"
                response = client.post(route)
            elif args.reminder_policy is not None:
                route, method = "/reminders/policy", "PUT"
                response = client.put(route, json=load_json_input(args.reminder_policy))
            elif args.get_reminder_policy:
                route, method = "/reminders/policy", "GET"
                response = client.get(route)
            elif args.plan_reminders is not None:
                route, method = "/reminders/plan", "POST"
                response = client.post(route, json={"message_id": args.plan_reminders})
            elif args.edit_reminder is not None:
                payload = load_json_input(args.edit_reminder)
                reminder_id = payload.pop("reminder_id", None)
                if not isinstance(reminder_id, str) or not reminder_id.strip():
                    raise ValueError("修改提醒JSON必须包含非空reminder_id")
                route, method = f"/reminders/{quote(reminder_id, safe='')}", "PUT"
                response = client.put(route, json=payload)
            elif args.mail_status:
                route, method = "/mail/status", "GET"
                response = client.get(route)
            elif args.sync_mail:
                route, method = "/mail/sync", "POST"
                response = client.post(route)
            elif args.mail_jobs:
                route, method = "/mail/jobs", "GET"
                response = client.get(route)
            elif args.index_file is not None:
                route = "/documents"
                method = "POST"
                params = {"filename": args.index_file.name}
                if args.message_id is not None:
                    params["message_id"] = args.message_id
                    params["attachment_index"] = args.attachment_index
                # 只把客户端真实读取的字节发给服务；Path本身不会进入上传协议。
                response = client.post(
                    route, params=params, content=args.index_file.read_bytes(),
                    headers={"Content-Type": "application/octet-stream"},
                )
            elif args.documents:
                route = "/documents"
                method = "GET"
                response = client.get(route)
            elif retrieval_mode:
                route = "/documents/search" if args.search is not None else "/documents/ask"
                method = "POST"
                field = "query" if args.search is not None else "question"
                payload = {field: args.search if args.search is not None else args.ask, "top_k": args.top_k or 5}
                if args.document_id is not None:
                    payload["document_id"] = args.document_id
                response = client.post(route, json=payload)
            elif args.ingest is not None:
                route = "/messages"
                method = "POST"
                response = client.post(route, json=load_json_input(args.ingest))
            elif args.role is not None:
                route = "/roles"
                method = "PUT"
                response = client.put(route, json=load_json_input(args.role))
            elif args.messages:
                route = "/messages"
                method = "GET"
                response = client.get(route)
            elif args.message_id is not None:
                route = f"/messages/{quote(args.message_id, safe='')}"
                method = "GET"
                response = client.get(route)
            elif args.history:
                route = f"/threads/{args.thread_id}"
                method = "GET"
                response = client.get(route)
            elif args.resume:
                route = f"/threads/{args.thread_id}/resume"
                method = "POST"
                response = client.post(route)
            else:
                route = "/invoke"
                method = "POST"
                payload = {"message": args.message}
                if args.thread_id is not None:
                    payload["thread_id"] = args.thread_id
                if args.reference_time is not None:
                    payload["reference_time"] = args.reference_time
                response = client.post(route, json=payload)
    except (OSError, UnicodeError, ValueError) as error:
        print("输入文件读取失败:", type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
    except httpx.HTTPError as error:
        print("HTTP 请求失败:", type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None

    print(f"{method} {route} HTTP 状态: {response.status_code}")
    try:
        result = response.json()
    except ValueError:
        print("服务没有返回有效 JSON。", file=sys.stderr)
        print(response.text)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if response.headers.get("X-Trace-ID"):
        # 编号走诊断输出，stdout继续保留原先“状态行＋JSON”的可解析格式。
        print("Trace ID:", response.headers["X-Trace-ID"], file=sys.stderr)
    # HTTP 成功不等于工具全部成功；只依据协议字段，不检查模型回复中的措辞。
    tool_failed = isinstance(result, dict) and result.get("execution_status") == "tool_error"
    if not response.is_success or tool_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
