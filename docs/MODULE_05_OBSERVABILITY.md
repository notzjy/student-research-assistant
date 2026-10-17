# 模块五：执行追踪、规则回归与真实模型评测

更新：2026-10-16。本模块给已有服务增加可检查的执行记录，并用固定的8条合成消息评测消息分析链路。沿用8002服务、原业务库、会话checkpoint和DeepSeek配置；用户本机实战结果与最终验收数据分别记录。

后续修复更新：因真实旧续费邮件误提醒，评测集已增加“只告知自动续费”和“明确要求取消自动续费”两条对照样例，当前为10例、81个时间字段断言，最新全量测试198项通过。本文8例/70断言与首例改错7/8=87.5%是原交付时的历史验收；使用当前样例集只改错首例，分类将为9/10=90%。新修复讲解与无弹窗演示见[策略门控与回归测试](LESSON_REMINDER_GUARD.md)，最新实测见[修复记录](results/reminder_guard_fix.json)。

本轮要解决两个具体问题：一次请求慢或失败，究竟发生在哪一步；代码执行成功后，模型提取的内容是否符合预期。前一个主要看Trace，后一个需要带固定预期的评测，不能只看HTTP 200。

## 先在现有服务中看一次Trace

工作目录为 `D:\Student_Assistant`。在原服务终端按Ctrl+C，再重启同一组数据库：

```powershell
.\.venv\Scripts\python.exe .\run_assistant.py --db .\local-data\module01_tasks.sqlite3 --checkpoints .\local-data\module01_memory.sqlite3
```

另开终端，导入本模块的完整教学通知（使用独立来源编号）：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --ingest .\examples\module05_notice.json
```

客户端仍在stdout打印原来的“HTTP状态行＋JSON”。新增诊断信息在stderr，形式为 `Trace ID: 32位编号`，来自响应头 `X-Trace-ID`，不会改变JSON业务协议。在普通终端里两种输出通常都能看到；如果只重定向stdout，Trace编号会继续显示在诊断输出中。

如果这条消息以前已成功导入，本次会返回 `status="exists"`。这是正确的来源去重行为：Trace里可能有 `message.receive`，但不会再次调用 `message.analyze`，也不会重新创建事项。不能因为缺少模型Span就认定埋点失效。

重复导入后若还要观察完整分析链路，可以复制通知并只生成一个新的 `source_message_id`：

```powershell
New-Item -ItemType Directory -Force .\local-data | Out-Null
@'
import json
from pathlib import Path
from uuid import uuid4

source = json.loads(Path("examples/module05_notice.json").read_text(encoding="utf-8-sig"))
source["source_message_id"] = "module05-trace-" + uuid4().hex
Path("local-data/module05_notice.json").write_text(
    json.dumps(source, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
)
'@ | .\.venv\Scripts\python.exe -X utf8 -
.\.venv\Scripts\python.exe .\assistant_client.py --ingest .\local-data\module05_notice.json
```

这里保留原消息的正文、发送时间和附件线索。新的来源编号表示一条新的教学消息，会新增对应事项；若已启用自动提醒，也会沿用当前规则处理。只想查询已有结果时不需要生成新编号。

把刚才导入请求显示的Trace编号粘贴给下面命令：

```powershell
$traceId = Read-Host "粘贴刚才显示的32位Trace ID"
.\.venv\Scripts\python.exe .\assistant_client.py --trace $traceId
.\.venv\Scripts\python.exe .\assistant_client.py --traces
```

`--trace` 查询一次执行，`--traces` 查询最近的摘要，均不调用模型。健康检查 `/health` 和追踪查询 `/traces`、`/traces/{trace_id}` 本身不创建新Trace，避免查一次记录又增加一次记录。

## thread_id、trace_id与span_id分别是什么

| 编号 | 本项目中的含义 | 生命周期 |
| --- | --- | --- |
| `thread_id` | 主Agent的一段持久会话，用于恢复历史和未完成流程 | 多次聊天请求可复用，保存在checkpoint中 |
| `trace_id` | 一次业务HTTP请求的执行记录 | 每次请求由服务生成新编号，重复导入也是新的请求 |
| `span_id` | 这次执行中的一个操作，如模型调用或文件检索 | 属于一个Trace，可用 `parent_span_id` 表达父子关系 |

同一个 `thread_id` 的两次追问会有两个不同的 `trace_id`。消息导入与文件上传也有Trace，但不需要伪造一个聊天线程。Trace记录保存在业务SQLite的独立表中；Trace存储对象和当前Span上下文不写入聊天checkpoint。

这是一次新消息分析可能出现的结构示意，具体记录以实际请求为准：

```text
http.request
├── message.receive
├── message.analyze
├── message.resolve_times
├── message.persist
└── reminder.plan
```

主Agent执行工具时，则可能出现 `agent.model → agent.tools → agent.report → agent.model`，其中 `tool.save_task` 等操作是 `agent.tools` 的子Span。文件检索包含 `document.search` 和其下的查询向量 `document.embed`。

每个Span只保存固定名称、状态、开始时间、`elapsed_ms`、安全 `error_code` 和父子编号。状态通常为 `completed` 或 `error`。Trace摘要在请求进行时为 `running`，结束后再更新。

父Span耗时包含子步骤和等待时间。例如 `document.search` 已经包含生成查询向量的时间，不能再把它与内部 `document.embed` 相加当作总耗时。查看父子关系应使用 `parent_span_id`，不能只看数据库写入先后，因为子操作通常先结束、先落记录。[OpenTelemetry的Trace与Span概念](https://opentelemetry.io/docs/concepts/signals/traces/)也采用这种执行关系；本项目当前实现的是本地教学追踪，没有接入OpenTelemetry SDK或外部收集器。

## 代码中的关键技术

| 技术或位置 | 在项目里做什么 |
| --- | --- |
| [trace_middleware.py](../agent-service-toolkit/src/student_assistant/trace_middleware.py) 的 `RequestTraceMiddleware.__call__()` | 给业务HTTP请求生成Trace，在响应头返回编号；不读取请求正文 |
| [tracing.py](../agent-service-toolkit/src/student_assistant/tracing.py) 的 `use_trace()` / `span()` | 用上下文管理器确定操作开始、结束和父子关系，异常仅记录类型 |
| 同文件的 `ContextVar` | 将当前Trace上下文按异步执行链隔离，避免并发请求串号 |
| [trace_store.py](../agent-service-toolkit/src/student_assistant/trace_store.py) 的 `write_span()` / `get_trace()` | 在业务库的独立表保存和查询有限运行元数据 |
| [graph.py](../agent-service-toolkit/src/student_assistant/graph.py) 的模型、工具和报告节点 | 记录主Agent实际经过的阶段，不把工具参数或结果写入Trace |
| [message_workflow.py](../agent-service-toolkit/src/student_assistant/message_workflow.py) 的分析、日期计算与保存节点 | 区分模型耗时、确定性计算和业务持久化 |
| [rag_runtime.py](../agent-service-toolkit/src/student_assistant/rag_runtime.py) 的索引、检索与回答步骤 | 区分解析、分块、Embedding、索引、回答和引用检查 |
| [evaluation.py](../agent-service-toolkit/src/student_assistant/evaluation.py) 的 `score_case()` / `report_passes()` | 将真实业务结果与固定预期逐项比较，再执行每项指标的质量门禁 |

`with span(...)` 的使用就在实际模型调用外面。下面是主图关键代码的简化摘录：

```python
with span("agent.model"):
    bound_model = context.model.bind_tools(list(context.tools.values()))
    response = await bound_model.ainvoke(messages_for_model(state["messages"]))
```

进入 `with` 时设置当前Span并开始计时；离开代码块时记录状态与耗时，再恢复上一层上下文。`@contextmanager` 让普通生成器函数承担这套进入、退出逻辑；`yield` 前后分别对应执行代码块前后。代码块抛异常时，追踪器记录异常类型后仍将异常交回原业务处理，并不会替业务宣布成功。

同步工具或文件检索放在线程池中：

```python
with span("document.embed"):
    vectors = await asyncio.to_thread(
        self.embedder.embed_documents, [part["text"] for part in chunks]
    )
```

Python的 `asyncio.to_thread()` 会复制当前ContextVar上下文，所以线程中的操作仍能连接到这次请求。ContextVar保存的是当前Trace与父Span的绑定，不需要给每个业务函数增加 `trace_id` 参数，也不需要把追踪依赖放进图状态。

有些工具失败后正常返回 `{"status":"error", ...}`，并不抛Python异常。因此工具节点还会显式执行 `current.fail("ToolExecutionError")`。引用来自本次候选但不足以回答，正常返回 `unknown`；伪造片段编号或quote则会标记 `InvalidCitations`。业务HTTP响应和Trace状态应一起读，HTTP 200不保证所有内部工具都成功。

## Trace的记录与运行边界

- 本批Trace不保存原消息、Prompt、工具参数、工具结果、附件正文、API Key或提供商响应；错误只保存类型或固定标识，不保存 `str(error)`。业务库和checkpoint本来承担的原文保存职责继续保留，不能把“Trace不存原文”误解成整个项目不存原文。
- Span名称采用固定白名单；模型提出未知工具名时也不能把任意名字写入追踪。记录失败只做安全诊断，不改变业务响应，不撤销已经提交的事项或文件索引。
- Trace写入使用独立短事务，不把原业务事务包成“观测事务”。SQLite和Qdrant原有的一致性与重试边界仍然有效。
- 后台邮箱和提醒loop没有绑定HTTP的 `use_trace`，本批不会持续生成后台Trace。后台状态仍查邮件任务、提醒状态和错误码；服务重启后继续依靠这些持久队列。
- 手动 `--sync-mail`、`--run-reminders` 是HTTP请求，可以追踪本次收取、解析或投递。没有新邮件或没有到期提醒时，不能要求一定出现处理或投递子Span。未配置QQ邮箱时同步仍会按原协议失败，不需要为了观察Trace先接通邮箱。

## 跑8条固定样例：先分清两种评测

固定数据为 [module05_eval_cases.jsonl](../examples/module05_eval_cases.jsonl)，每行一条合成消息，包含输入、人工固定预期 `expected` 和离线注入分析 `rule_analysis`。覆盖多事项、只有日期、本周星期、跨时区相对日、自然月范围、截止时刻、没有时间的待办、纯聊天和附件线索。

这些命令不需要启动HTTP服务。评测创建独立临时业务库，复用真实消息接收、Pydantic、日历计算和业务事务，不向主业务库加教学事项，也不启动邮箱、提醒或RAG索引。

先运行规则回归：

```powershell
.\.venv\Scripts\python.exe .\evaluate_assistant.py --mode rules --output .\local-data\module05_rules_report.json --fail-under 1.0
$LASTEXITCODE
```

`rules` 用预设分析替换模型响应，不访问模型、不加载 `.env`。它验证程序能否正确校验、计算、保存和判分。即使所有指标通过，也只能说明这些程序规则通过，不能称为DeepSeek提取准确率。

再运行真实模型的小样本评测：

```powershell
.\.venv\Scripts\python.exe .\evaluate_assistant.py --mode live --output .\local-data\module05_live_report.json --fail-under 1.0
$LASTEXITCODE
```

`live` 从本机现有 `.env` 加载DeepSeek配置，真正向模型发送8条合成消息，按同一份预期判分；不使用 `rule_analysis` 当模型输出。模型结果可能变化，要以本次报告为准。这8例覆盖面有限，不代表全部真实消息、全部Agent能力或生产数据准确率。[LangChain的评测概念](https://docs.langchain.com/langsmith/evaluation-concepts)也强调数据集、预期与评测器的作用；本项目使用本地评测脚本，没有接通LangSmith平台。

脚本退出码：`0` 为全部指标达到门禁，`1` 为至少一项指标低于门禁，`2` 为配置、样例文件或运行准备失败。`--fail-under 1.0` 表示**每一项**指标都要求达到1.0，不是只看平均分。

## 报告究竟评什么

| 指标 | 分母与判分方式 | 不能据此声称什么 |
| --- | --- | --- |
| `classification_accuracy` | 类别完全符合预期的消息数 / 固定消息数 | 不代表所有真实来源的分类准确率 |
| `event_count_accuracy` | 事项数量完全相同的消息数 / 固定消息数 | 数量正确不证明每项标题和全部含义都正确 |
| `time_field_accuracy` | 按原时间表达一对一匹配后，通过的时间字段与形状断言数 / 全部预期断言数 | 不是消息级准确率，也不是提醒投递成功率 |
| `attachment_hint_retention` | 原消息和详情中都保留的输入附件线索数 / 输入线索数 | 不代表下载了附件或读到了文件正文 |
| `attachment_count_accuracy` | 模型提取数量落入预设允许值的消息数 / 固定消息数 | 与原件保存、线索保留是不同问题 |

时间比较包含原发送时间、时区、精度、日期或时刻、截止性质等字段；日期和日期范围中意外出现 `at` 也会失败。缺失事项计失败，不会因为没有结果就跳过评分。标题目前不作硬匹配，时间主要按原话一对一匹配；还没有计算事项语义F1、RAG检索Recall、回答事实正确率或语义蕴含。

无附件线索的样例不为线索保留增加分母；若自选样例的某项分母为0，当前约定score为1，应同时阅读 `total`。不要把不同分母的分数直接当作同一种准确率。

JSON报告包含 `mode`、`model_evaluation`、`metrics`、每例 `checks`、有限的合成实际结果和 `traces`。评测的Trace先存在临时库，导出JSON快照后临时库即清理。报告里的 `cases[].trace_id` 应在**该报告的 `traces` 列表**中寻找，不会出现在主服务的 `/traces` 接口；两个工具用的是不同存储。

## 一次综合练习：让质量门禁真实失败

先保留上一轮规则报告，再复制样例文件。只改复制文件首条样例的预期类别，不改输入、不改注入分析，也不改主fixture：

```powershell
New-Item -ItemType Directory -Force .\local-data | Out-Null
Copy-Item .\examples\module05_eval_cases.jsonl .\local-data\module05_wrong_cases.jsonl
@'
import json
from pathlib import Path

path = Path("local-data/module05_wrong_cases.jsonl")
cases = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
cases[0]["expected"]["category"] = "\u5176\u4ed6"
path.write_text(
    "\n".join(json.dumps(case, ensure_ascii=True) for case in cases) + "\n", encoding="utf-8"
)
'@ | .\.venv\Scripts\python.exe -X utf8 -
.\.venv\Scripts\python.exe .\evaluate_assistant.py --mode rules --cases .\local-data\module05_wrong_cases.jsonl --output .\local-data\module05_wrong_report.json --fail-under 1.0
$LASTEXITCODE
```

这里的Unicode转义就是“其他”，用于让PowerShell传入Python的修改脚本保持ASCII兼容。复制样例的实际研究通知没有改变，预期却变成“其他”，因此应该出现类别比较失败，门禁退出码应为1。门禁失败的报告与原报告分开保存。

查看失败断言和同一例的Span：

```powershell
$report = Get-Content -LiteralPath .\local-data\module05_wrong_report.json -Raw -Encoding UTF8 | ConvertFrom-Json
$failed = $report.cases | Where-Object { -not $_.passed } | Select-Object -First 1
$failed.checks | Where-Object { -not $_.passed } | Format-Table metric, name, expected, actual
($report.traces | Where-Object { $_.trace_id -eq $failed.trace_id }).spans | Format-Table name, status, error_code, elapsed_ms
```

`message.receive`、`message.analyze`、`message.resolve_times`、`message.persist` 可以全部是 `completed`，类别比较仍然失败：程序拿到了合法结果，不代表它符合评分预期。当前评测器会另外把 `evaluation.run` 根Span和本例Trace标为 `EvaluationAssertionError`，指出失败来自结果比较，不要误认为SQLite保存失败。

这次练习的错误是我们有意改错预期造成的，不能据此判断模型变差。真实 `live` 失败时，需要检查输入、预期是否合理、模型提取内容及程序计算结果，再定位问题。Trace帮助回答“执行到哪里、哪个阶段耗时或抛错”，评测断言帮助回答“结果哪里不符合预期”。

提交一次服务Trace中的阶段说明，以及规则失败报告中一条失败断言，并解释两者为何能同时出现即可。若再运行 `live`，请同时说明它和 `rules` 的证据区别；本轮不要求公开私邮、授权码或完整模型输入。

## 接口与本批范围

| 操作 | 接口或命令 | 返回内容 |
| --- | --- | --- |
| 查询服务Trace列表 | `GET /traces?limit=20` / `--traces` | `count` 与追踪摘要列表 |
| 查询一次服务执行 | `GET /traces/{trace_id}` / `--trace ID` | 该Trace及其 `spans`，未知编号返回404 |
| 固定程序规则回归 | `evaluate_assistant.py --mode rules` | 不调用模型的规则结果与独立报告 |
| 真实模型小样本评测 | `evaluate_assistant.py --mode live` | 本次DeepSeek结果、评分及独立Trace快照 |

当前交付是Trace/Span和消息分析质量工具；QQ真实账号、其他平台自动接入及产品部署仍按原目标继续验证。

本轮185项全量测试通过（根tests112＋lessons73；新增32项为Trace12、模型/RAG追踪5、评测8、追踪API7）。[真实验收记录](results/module05_example.json)包含12项检查、17条断言，使用正式CLI/TCP、真实DeepSeek及缓存BGE/Qdrant验证消息/文件/问答追踪、重复导入不再分析、坏PDF定位到 `document.extract`、进程12700停止后由新进程24528恢复同一Trace与Span。临时服务存储及评测SQLite均已清理；验收Trace保存在记录文件，不能再用主服务查询那个临时编号。

真实 `live` 评测本次8条合成消息得到：分类8/8、事项数8/8、时间字段70/70、附件线索4/4、模型附件数量8/8；`rules` 结果也全部通过，但只代表程序规则回归。70是时间字段及匹配/禁止字段断言数，并非70种时间表达；4是输入中附件线索条数，不是下载4份文件。完整本机报告分别在 `local-data/module05_live_report.json`、`module05_rules_report.json`，含各例检查与独立Trace快照。

反例只改首例预期类别，实际CLI返回退出码1、分类7/8（87.5%），其余指标保持不变；`message.analyze` 为completed、评测根Span为 `EvaluationAssertionError`。这验证了比较器能报错，不能由此声称模型性能下降。本轮8例分数不代表生产准确率，也没有测得RAG Recall、回答语义正确率或用户真实QQ接入效果。

项目完成进度：53%，本批质量工具不改变功能里程碑。技术栈学习：400/1300，约31%；用户已能解释提醒为何无需DeepSeek参与，但异步掌握仍待实践证据，不因阅读讲义自动加分。预计核心主线还需约2–4次有效提问/反馈，随QQ本机配置、可演示交付和剩余工作核对。
