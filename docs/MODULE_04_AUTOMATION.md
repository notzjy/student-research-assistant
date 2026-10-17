# 模块四：自动收件、异步任务与持久化提醒

更新：2026-10-16。用户选择 Windows 桌面通知＋提醒列表，QQ 邮箱尚未启用 IMAP。代码验证与真实账号连通分别记录；本模块没有发送邮件的功能。

本模块连接：QQ 邮箱只读增量接收 → EML 原件留存 → 现有消息工作流 → 真实附件 RAG → 规则创建提醒 → 后台投递 → 状态查询与失败重试。微信、个人 QQ、BOSS 直聘仍待渠道验证，不因邮箱模块存在而视为完成。

## 先跑通提醒，再配置邮箱

工作目录 `D:\Student_Assistant`，无需新增 pip 依赖。先在原服务终端按 Ctrl+C，再重启同一组数据库：

```powershell
.\.venv\Scripts\python.exe .\run_assistant.py --db .\local-data\module01_tasks.sqlite3 --checkpoints .\local-data\module01_memory.sqlite3
```

在另一个终端启用示例提醒规则，再执行完整演示：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --reminder-policy .\examples\module04_reminder_policy.json
.\.venv\Scripts\python.exe .\module04_demo.py
.\.venv\Scripts\python.exe .\assistant_client.py --reminders
```

示例规则为：有明确时刻的事项提前30分钟提醒；只有日期的事项在该日09:00提醒；通道为 `windows`。这组规则由你显式启用，可以修改 JSON。只有日期时的09:00是**你的提醒时间**，不代表原消息说截止到09:00。日期型事项仍为 `precision="date"`，没有 `at`；日期提醒不再减30分钟。

演示脚本导入一条新的完整教学通知，提取事项，并仅对提醒时刻仍在未来的事项自动计划；如果当天09:00已过，会返回跳过原因。另为第一个事项安排8秒后的显式测试提醒，便于观察 Windows 气泡。重复运行会生成新的教学消息和事项。附件仍只是两篇论文的线索，没有真实 PDF，不能声称已下载两篇论文。

服务必须持续运行。笔记本关机或服务退出期间不会弹提醒；恢复运行后，已提前建立且原事项尚未过期的自动提醒可以补发，用户显式手工提醒仍可补发。新导入历史消息不会因日期已过而立即创建提醒；升级前已有的自动/legacy队列也会在投递前检查原事项日期，过期时记录cancelled与SourceEventExpired，原文和事项保留。Windows 专注模式、桌面会话等可能影响气泡显示，`submitted` 仅表示通知接口调用完成，不表示你已读。若需排查系统气泡，先用终端通道验证：

```powershell
.\.venv\Scripts\python.exe .\module04_demo.py --channel console
```

规则修改影响以后新处理的消息，不扫描所有旧数据。为一条旧消息明确补建计划：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --plan-reminders 你的message_id
```

同一事项已有计划时，自动补建保留已有记录。编辑提醒需要当前 `version`，向 `--edit-reminder` 提供包含 `reminder_id`、`expected_version`、`remind_at`、`channel` 的 JSON。`--cancel-reminder ID` 取消未提交提醒；任务通过原 Agent 工具完成后，数据库会取消它尚未提交的提醒。已交给系统的通知无法撤回。

## QQ 邮箱本机配置

1. 登录 QQ 邮箱网页，在邮箱设置中查找“POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV 服务”或类似服务管理入口，启用 IMAP，按邮箱要求完成验证并生成授权码。界面名称以你当前账号为准。[腾讯官方配置说明](https://cloud.tencent.com/document/product/1270/55456)
2. 在本机运行以下配置脚本，输入完整邮箱地址和刚生成的授权码。授权码隐藏输入，保存在现有 `.env` 中；DeepSeek 配置会保留。不要把授权码粘贴到聊天里，也不要用 `.env.example` 覆盖已有 `.env`。

```powershell
.\.venv\Scripts\python.exe .\configure_mail.py
```

3. Ctrl+C 停止服务，再用前面的命令重启。配置脚本启用每60秒轮询，首次最多导入最新5封邮件，随后按 UID 收取新邮件。原始收件箱可能包含私人内容；成功解析后的正文及附件线索会发给你配置的 DeepSeek 做现有通知分析，支持的附件使用本地 Embedding 建索引，之后提问只将选中的片段发给回答模型。
4. 查询状态和手动同步：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --mail-status
.\.venv\Scripts\python.exe .\assistant_client.py --sync-mail
.\.venv\Scripts\python.exe .\assistant_client.py --mail-jobs
```

`configured=true` 只说明参数齐全；必须看到同步结果、邮件任务 `processed` 和消息详情，才能证明实际接入成功。自动轮询与手动同步使用同一个锁。手动同步会重置失败队列供再次尝试；配置 `QQ_MAIL_ENABLED=false` 关闭自动轮询，手动同步仍可运行。

邮箱收取使用 `imap.qq.com:993`、系统证书校验和只读 INBOX，正文使用 `BODY.PEEK[]`，不会主动标已读、删除或发送邮件。标准库 `imaplib` 是阻塞接口，放到线程执行；本机 Python 3.13.9 采用轮询，未实现 IMAP IDLE。[Python IMAP 文档](https://docs.python.org/3.13/library/imaplib.html)、[IMAP UID 与 PEEK 标准](https://www.rfc-editor.org/rfc/rfc3501)

## 核心技术与代码位置

| 技术 | 项目作用 | 关键文件 / 函数 |
| --- | --- | --- |
| asyncio 后台并发 | API 提问、收件和提醒可以各自推进；停止时等待在途任务结束 | `service.py` 的 lifespan；两个 runtime 的 `loop()` |
| to_thread | 将阻塞网络、SQLite 和 Windows 通知调用移出事件循环 | `mail_runtime.py` 的 `sync()`；`reminder_runtime.py` 的 `run_once()` |
| 协议适配与 MIME | 从真实邮件提取完整正文、Date 和附件字节，转换成已有 MessageInput | `mail_ingest.py` 的 `parse_mail()` / `ImapCollector.fetch_new()` |
| 增量接入与幂等 | UIDVALIDITY＋UID 标识来源；原件落盘后才推进游标 | `mail_store.py` 的 `save_raw()` |
| 持久化任务队列 / outbox | 提醒记录保存在业务 SQLite，重启后继续执行 | `reminder_store.py` 的 `save()` / `claim_due()` |
| 租约与乐观并发 | worker 临时认领；version 防旧编辑覆盖，token 防旧 worker 确认新版本 | 同文件 `validate_claim()` / `mark_submitted()` |
| 有界重试 | 通道失败退避后重试，超限标 failed，避免忙循环 | `mark_failed()` 与 `MailStore.mark_failure()` |
| 通知适配器 | Windows 气泡和终端两个本地通道，文本以数据传递 | `notification.py` 的 `LocalNotifier.send()` |

这些是 Agent 应用连接真实系统所需的工程能力。DeepSeek 不会靠一直对话来计时，LangGraph checkpoint 也不承担系统提醒队列。

## 看懂三段关键代码

后台循环由服务生命周期启动并保留 task 引用：

```python
# 创建可并发推进的后台任务；退出时先设置 stop，再 await task。
task = asyncio.create_task(reminders.loop(stop_event))

# IMAP 是阻塞调用。await 本行时，事件循环可以处理其他请求。
fetched = await asyncio.to_thread(collector.fetch_new, store)
```

`async def` 只是定义异步函数；`create_task()` 才会安排协程运行。`await` 不会自动把普通阻塞函数变成异步 I/O，`to_thread` 在这里有实际作用。[Python asyncio 官方说明](https://docs.python.org/3.13/library/asyncio-task.html)

提醒每次认领一条：

```python
# 短事务原子认领，不在事务中弹通知或等待模型。
jobs = await asyncio.to_thread(store.claim_due, datetime.now(timezone.utc), 1)

# 进入执行线程后再次核对版本和租约，再交给本地显示通道。
delivered = await asyncio.to_thread(self._send_owned, jobs[0])
```

数据库记录 `pending → leased → submitted`；失败返回 `pending` 并设置下次尝试时间，第5次失败终止为 `failed`。进程如果在通知提交后、数据库确认前崩溃，租约到期可能重发。数据库和 Windows 通知系统没有共同事务，因此不能承诺严格只显示一次。任务完成或编辑时可能恰与已开始的系统调用交错，也无法撤回正在显示的气泡。

邮件游标与业务处理分开：

```text
IMAP UID → EML保存＋队列＋游标提交 → 模型分析 → 附件RAG → 邮件processed
                    ↓                         ↓失败
               重启仍有原件              同一来源ID重试
```

模型失败不会要求从邮箱重新下载；支持的附件以真实字节进入 RAG，原消息保留 `mentioned`，当前详情叠加 `indexed`。未支持的格式保留在 EML，邮件任务列出 `preserved_in_eml`，不声称已建立向量索引。大于25MB邮件只记录跳过原因；正文大于5000字符、附件超过20个、无合法带时区Date等会保留原件并明确失败，不用静默截断或处理当天时间替代发送时间。From 是邮件声明，不代表已经验证发件人真实身份。

## 本轮边界与验证

自动规则由程序确定性执行，不调用模型计时。消息分析提交与后续提醒计划目前是两步：手工导入响应如出现 `reminder_planning.error_code`，需要修复后显式 `--plan-reminders`；两步之间进程崩溃也需要补计划，尚无分析事务内的规划 outbox。邮件队列会在重试时显式补计划。提醒记录创建之后则有持久化队列、租约和补发能力。

本轮全量153项测试通过（根tests80项、lessons73项），覆盖真实 SQLite、MIME、Qdrant 和服务编排；离线邮件网络和模型使用替身。另有[真实验收记录](results/module04_example.json)：11项检查、11条断言，使用正式 CLI/TCP、真实 DeepSeek提取、实际 Windows 通知系统调用；实际 Uvicorn 进程30512停止、端口不可访问后，以新进程2016恢复并提交持久化到期提醒。Windows计划重启后仍保持已提交且attempts=1，终端通道有真实输出；临时进程和数据库、原件、向量目录均已清理。实际 QQ 登录、真实邮件内容和用户桌面是否看到气泡，分别等待用户本机配置和观察，不能用离线测试代替实际账号验证。

当前项目53%（阶段分5/5/19/7/13/0/4）；本轮增加已验的持久提醒8分，真实QQ接入仍0分。用户已解释RAG分工，RAG概念25分、总400/1300约31%；新异步练习尚未反馈，不因代理实现和测试加学习分。主线预计仍需3–5次有效提问或反馈。

后续缺陷修复（2026-10-16）：用户实际运行已提供QQ正文处理及桌面弹窗证据，也暴露历史续费邮件误提醒。现增加创建/投递两处日期门控、origin兼容迁移及续费告知/明确取消请求的模型对照样例，详见[修复教学](LESSON_REMINDER_GUARD.md)与[验收记录](results/reminder_guard_fix.json)。当前完整QQ附件、增量和延迟验收仍未完成，不能把正文读取成功当成全渠道完成。上方153项/53%为模块四交付时记录，最新198项全量测试通过，学习仍约31%，主线预计2–4次。

## 一次综合练习

先运行提醒演示并观察 `events[].time`、`remind_at` 与最终 `submitted`。随后按步骤配置邮箱，查询 `--mail-status`、`--sync-mail` 和 `--mail-jobs`，只反馈状态或隐藏个人信息的结果。

最后回答一个与实际代码有关的问题：如果把 `await asyncio.to_thread(collector.fetch_new, store)` 改为直接 `collector.fetch_new(store)`，收件网络卡住10秒时，同一服务中的聊天请求和到期提醒会受到什么影响？为什么函数外层已经是 `async def` 仍不能避免这个问题？
