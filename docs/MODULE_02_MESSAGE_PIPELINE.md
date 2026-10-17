# 模块二：完整消息、角色匹配与可追溯事项

更新：2026-10-16。本模块已实现并通过代理验收；用户综合练习待反馈。继续使用根 `run_assistant.py` 的 8002 主服务，复用原业务库和主 Agent。

本轮将 **Pydantic 协议、结构化模型输出、LangGraph 工作流、SQL 事务与幂等** 连成一条业务链路：

```text
完整消息JSON → 校验与来源去重 → 原文先保存
                           ↓
                  模型一次提取类别/事项/时间意图
                           ↓
                  程序校验出处并计算日期
                           ↓
                  分析＋全部事项＋关联原子提交
                           ↓
                  查原文/重点消息/待办 → 标记完成
```

用户不需要预先拆分通知。示例中的组会、综述和两篇参考论文一同发送，模型输出多个事项，原文始终单独保留。精确角色由用户配置的身份键匹配，与模型提取分开处理。

## 先运行整个链路

工作目录：`D:\Student_Assistant`。如果 8002 服务已经在运行，先按 `Ctrl+C` 停止，再用原命令重启以加载新增路由。沿用上一模块的练习双库即可：

```powershell
.\.venv\Scripts\python.exe .\run_assistant.py --db .\local-data\module01_tasks.sqlite3 --checkpoints .\local-data\module01_memory.sqlite3
```

另开终端，先保存演示角色规则：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --role .\examples\module02_role.json
```

[角色示例](../examples/module02_role.json)只配置虚构的 `manual/demo/demo-teacher-001` 为“导师”、`important=true`。这份文件供练习，不代表已经替用户确认任何真实联系人。正式使用时由用户指定实际来源、账号和发送人ID；昵称仅显示。

随后导入完整消息：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --ingest .\examples\module02_notice.json
```

[消息示例](../examples/module02_notice.json)保留：

```json
{
  "source": "manual",
  "account_id": "demo",
  "source_message_id": "module02-001",
  "sender_id": "demo-teacher-001",
  "sender_name": "示例老师",
  "sent_at": "2026-10-16T10:00:00+08:00",
  "text": "明天下午三点在实验楼302开组会；周五前交综述，附件是两篇参考论文。",
  "attachments": [
    {"filename": "reference-a.pdf", "availability": "mentioned"},
    {"filename": "reference-b.pdf", "availability": "mentioned"}
  ]
}
```

文件名是演示线索，文件正文没有下载或解析。`source="manual"` 表示人工输入；填入微信/QQ等来源名称，也只是在声明来源元数据，自动账号接入仍需单独验证。

真实 DeepSeek 验收得到研究类、两项待办和两篇论文附件线索。角色匹配后优先级为 `high`，事项时间如下，标题措辞可能略有不同：

| 原文事项 | 保存的时间 | 含义 |
| --- | --- | --- |
| 明天下午三点在实验楼302开组会 | `precision=minute`，`at=2026-10-17T15:00:00+08:00` | 精确时刻，地点实验楼302 |
| 周五前交综述 | `precision=date`，`date=2026-10-16`，`time_kind=deadline` | 截止日期，无 `at`、未补造钟点 |

时间均以示例消息的 `sent_at` 为基准，与你在哪天运行命令无关。

同一份文件再导入一次：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --ingest .\examples\module02_notice.json
.\.venv\Scripts\python.exe .\assistant_client.py --messages
```

第一次通常是 `status="processed"`；重复是 `status="exists"`，复用已有分析且不新增事项。若你之前已经导入过，首次运行本讲义的命令也可能直接 `exists`。

接着通过已有主 Agent 完成综述，再查看消息详情：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id module02-study --message "请查询全部待办，再将这次演示通知中的综述标记完成；有多个候选就先让我确认。"
.\.venv\Scripts\python.exe .\assistant_client.py --messages
```

主 Agent 使用原来的 `list_tasks` / `complete_task`，原消息详情的 `events[].status` 会读取当前待办状态。单条详情可使用 `--message-id` 加导入响应中的 `message.message_id` 查询。已有练习库可能有旧综述，遇到歧义按真实查询结果确认目标，不能假定每次只有一项。

**`processing_status="completed"` 是分析流程完成；`events[].status="completed"` 才是某项待办做完。** 再次导入同一消息，不会把已完成的待办恢复成未完成。

## 核心技术在哪里体现

| 技术 | 本次要掌握的用途 | 对应代码 |
| --- | --- | --- |
| Pydantic | 区分原始消息协议和模型提取结果，校验时间、字段与类型 | [message_models.py](../agent-service-toolkit/src/student_assistant/message_models.py) 的 `MessageInput`、`MessageAnalysis` |
| LangChain 结构化输出 | 一次提取完整消息，得到受 schema 约束的类别、事项、证据和时间意图 | [message_workflow.py](../agent-service-toolkit/src/student_assistant/message_workflow.py) 的 `analyze()` |
| LangGraph Workflow | 固定串联提取、日期计算和保存节点 | 同文件 `_build_graph()` |
| SQL 唯一键与幂等 | 来源重发时不重复处理，同 ID 内容变更时明确冲突 | [message_store.py](../agent-service-toolkit/src/student_assistant/message_store.py) 的 `receive()` |
| SQL 事务与关联 | 全部事项一起提交或回滚，并保留它们对应的原消息与证据 | 同文件 `complete_analysis()`、`_detail()` |

聊天 `/invoke` 由模型动态选择工具；导入 `/messages` 按固定顺序执行工作流。两者都是智能体平台会用的模式。[LangGraph 官方说明](https://docs.langchain.com/oss/python/langgraph/workflows-agents)也区分预定路径的 workflow 与动态选择流程、工具的 agent。

本模块的原消息是分析数据，提取模型没有绑定执行工具；它输出分析对象，由固定程序校验和入库。模型不决定能否修改角色规则，也不通过来信获得任意工具执行能力。

## 先理解这四处代码

第一处，身份与消息ID各自负责什么：

```sql
-- 一条来源消息在一个来源账号内只保存一次。
UNIQUE(source, account_id, source_message_id)

-- 用户的重点角色按真实来源身份匹配。
PRIMARY KEY(source, account_id, sender_id)
```

`thread_id` 标识一段聊天；`source_message_id` 标识一条来源消息。新聊天可以读已有待办，同一来源消息重发应沿用消息ID。一条新消息即使文字相同，也应有它自己的来源ID。

第二处，去重发生在模型调用之前。`MessageProcessor.process()` 先保存原文，再读取处理状态：

```python
# 已经成功处理过，就直接返回。模型不可用也不会影响复用已完成结果。
if current["processing_status"] == "completed":
    return {"status": "exists", "message": current}
```

如果先调用模型再检查重复，重发会多花时间与费用，还可能让原本可以直接返回的结果受模型故障影响。

第三处，时间意图和日期计算分工。模型为“明天下午三点”填写 `unit=day, offset=1, hour=15, minute=0`；程序将它与 `payload.sent_at` 一起交给已经学过的 `resolve_intent()`。每项无需再次请求模型。日、周、月、年使用通用日历运算；“周三”仍执行用户确定的本周规则。

第四处，模型请求放在业务写事务之外。原文先单独提交；分析成功后，才在同一 `connection` 内保存全部事项、关联和分析状态。不能在外层事务里调用另开连接的 `save_task()`，本次复用接收现有连接的 `_insert_task()`。第二项写入失败时第一项也回滚，避免留下半批结果；先前单独提交的原文仍可查询。

## 一项综合练习：重复、冲突和新消息

1. 重复导入原示例，记录 `status` 与 `message_id`，确认原事项没有重复。
2. 复制消息文件到 `local-data/my_notice.json`，只修改 `text`、保留原来源ID，再导入：应返回 **409**，原文不能被覆盖。
3. 如果这是另一条新消息，再给复制文件换一个新的 `source_message_id`，保留适合的新正文和原发送时间，重新导入：应产生新的消息及关联事项。若是原消息被编辑，当前保留冲突，修订流程后续实现。
4. 用自己的话解释：为什么检查 `exists` 应放在模型调用之前？为什么不能只按 `text` 相同来判断是同一条消息？

提交这一次综合反馈即可。反馈核对后直接进入研究文件 RAG，继续使用当前消息与附件数据。

## 失败重试与当前边界

- HTTP 502/503/500 之后可能已经保存原文。响应的 `detail.message_id` 可用于查详情；模型失败的消息保留 `failed` 和安全错误码。配置或故障修好后，重发同一份来源 JSON 可重试。
- 此业务工作流没有单独的图 checkpoint；失败重试从业务记录检查状态，必要时重新提取。主聊天的 checkpoint 与显式 `--resume` 继续管理聊天执行，不能把它们当成消息分析重试入口。
- 证据片段校验能拒绝脱离原文的时间/地点；它不能证明模型的标题、分类和时间理解一定正确，后续用评测与纠正功能处理。
- 当前固定四个主类别，附件按消息类别给出初步分类，`classification_basis="message_text"`、`availability="mentioned"`。研究文件正文分类、下载与索引在下一模块推进。
- `priority="high"` 是重点消息标记；实际加重通知、颜色界面和持久截止提醒尚待后续交付。不支持的复杂起止与绝对日期可保留待确认结果，不补造确定时间。
- 仍是本机单进程、一个个人助理；没有将模拟来源元数据计作真实平台接入。SQL唯一键保护数据层幂等，消息锁避免同一服务内重复调用模型。

## 验收记录

- [15项新增测试](../tests/test_message_pipeline.py)加原84项，共99项通过；`pip check` 通过。新增测试用模拟模型、真实 LangGraph/ASGI 和临时双 SQLite，覆盖原文保留、并发去重、精确角色、失败重试及真实 SQLite 触发器下第二项失败的整批回滚。
- [真实模型/CLI验收](results/module02_example.json)：完整通知与两附件线索、两项来源时间、角色匹配、重复 `exists`、冲突409、原文查询、旧主Agent完成导入事项、重复不重置及规则更新后优先级即时变化均已核对。停止服务进程后重开业务库记录仍在；合成数据、专用临时库，进程及双库已清理，未修改用户业务库。
- 项目本轮由25%到40%；技术学习375/1300≈29%。学习加分来自用户对模块一线程复用的解释和同/异线程运行证据，本模块尚待用户练习；用户尚未提供服务重启恢复证据。
- 核心主线预计剩余4–6次有效提问/反馈：RAG、自动接入与提醒、评测、部署演示，保留联调机动空间。

离线复现检查：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_agent_memory tests.test_message_pipeline
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s lessons -p "test_*.py"
```
