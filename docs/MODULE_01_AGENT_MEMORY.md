# 模块一：把工具、主 Agent 和持久会话连起来

更新：2026-10-16。实现与代理验收已完成，用户综合练习待反馈。本次集中学习 **LangGraph 编排、Tool Calling、上下文管理与 checkpoint**，它们共同解决“助理如何接着上一次继续办事”。

## 这次交付的功能

以前第七课服务每次请求重新开始图，模型主要处理本次输入。现在主服务通过工具包 `service:app` 的 `student` 配置档运行：同一个 `thread_id` 会取回之前保存的消息，再把新消息追加进去；服务进程重启后也能恢复。主图整合五个工具：

| 工具 | 做什么 | 复用的代码 |
| --- | --- | --- |
| `save_task` | 保存明确待办，保留时间原话和地点，重复相同内容复用已有项 | `lessons/assistant_tools.py`、`lessons/task_store.py` |
| `list_tasks` | 从业务库查询 `pending`、`completed` 或 `all` | 同上 |
| `complete_task` | 按本轮真实查询到的 ID 完成某一项 | 同上 |
| `analyze_notice` | 用 Pydantic 结构化提取类别、摘要、事项及附件描述/数量 | `lessons/02_structured_output.py` |
| `interpret_time` | 模型提取意图，程序计算日期、时刻或日期范围 | `lessons/time_semantics.py` |

这次完成主 Agent 集成。原消息/来源身份/附件业务表、角色配置、时间结果与待办的持久关联、提醒和研究文件 RAG 在后续模块交付。

## 先运行完整示例

所有命令在 `D:\Student_Assistant` 下执行。若仍开着旧的 8001 服务，可以继续保留；本次主服务使用 **8002**。根目录 `.env` 继续使用已有的 DeepSeek 配置，不把密钥放进客户端命令。

当前环境已安装本模块依赖。换一台机器时先创建 `.venv`，再安装根 `requirements.lock.txt`；根 `requirements.txt` 是直接依赖清单。这里没有安装上游 `pyproject.toml` 的全部 42 项运行直接依赖。

终端 A 启动服务。为便于练习，下面指定单独的练习双库：

```powershell
.\.venv\Scripts\python.exe .\run_assistant.py --db .\local-data\module01_tasks.sqlite3 --checkpoints .\local-data\module01_memory.sqlite3
```

省略两个路径参数时，正式默认路径是 `local-data/assistant.sqlite3` 和 `local-data/assistant_checkpoints.sqlite3`；前者沿用第七课业务库。两文件不能相同。

终端 B 发送第一条消息：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id study-a --message "请记住我这个研究项目的代号是星河计划。只确认称呼，不要新增待办。"
```

接着追问，使用同一个线程编号：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id study-a --message "我刚才给这个研究项目起的代号是什么？"
```

本次真实 DeepSeek 验收的回复为“你给的代号是「星河计划」。”这两条是聊天，无需工具，`execution_status` 是 `no_tools`；没有调用工具并不表示失败。实际文字可能略有不同。

换一个编号再问：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id study-b --message "我给这个研究项目起的代号是什么？"
```

`study-b` 没收到 `study-a` 的历史，应表示不知道这个代号。随后在终端 A 按 `Ctrl+C` 停服务，使用原命令、原双库路径重新启动，再对 `study-a` 追问：它应该仍记得代号。

想看到程序到底保存了什么：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id study-a --history
```

没有提供 `--thread-id` 时服务会生成新编号并随响应返回；想续聊就保存并复用这个编号。固定编号适合本次手动验证。

## 理解决定行为的代码

| 技术 | 在项目里的意义 | 看哪里 |
| --- | --- | --- |
| LangGraph | 组织 `model → tools → report → model` 循环，模型不调用工具时结束 | [graph.py](../agent-service-toolkit/src/student_assistant/graph.py) 的 `build_agent()` |
| Tool Calling | 给模型工具名、说明和参数结构；Python 只执行注册表中的工具并回传真实结果 | [tools.py](../agent-service-toolkit/src/student_assistant/tools.py) 的 `build_turn_tools()`、`graph.py` 的 `call_model()` / `call_tools()` |
| checkpoint + `thread_id` | 保存并找回该会话的消息、图状态和待继续的节点 | [runtime.py](../agent-service-toolkit/src/student_assistant/runtime.py) 的 `__aenter__()` / `chat()` / `resume()` |
| Runtime context | 将当前轮的模型、业务存储、工具表传给节点；这些对象不进入持久状态 | `graph.py` 的 `TurnContext`、`runtime.py` 的 `_context()` |
| FastAPI 生命周期与异步 | 服务启动时打开 saver、关闭时释放；模型请求异步等待，同步工具在线程池执行 | [service.py](../agent-service-toolkit/src/student_assistant/service.py) 的 `create_app()` / `lifespan()` |

主流程的关键行是：

```python
# 为图启用持久状态；实际 saver 是 AsyncSqliteSaver，随服务生命周期打开/关闭。
graph = builder.compile(checkpointer=checkpointer)

# 同一个编号取回同一个会话的状态。
config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 24}

# 只提交新消息；MessagesState 的合并规则会追加到 checkpoint 恢复的历史中。
# 实际 chat() 还记录 turn_id 和 turn_start_history，用于返回本轮结果。
result = await self.graph.ainvoke(
    {"messages": [human], "turn_id": turn_id, "turn_start_history": start},
    config=config, context=context, durability="sync",
)
```

上面是截取后的关键流程，完整可运行代码在 `runtime.py`。`thread_id` 是查找会话的键，`checkpoint` 是存下来的状态，`messages` 是这个状态里的对话内容。新请求先恢复该键的状态，再追加新消息；给模型的实际输入由 `messages_for_model()` 组织。

**模型不会因为接入 LangGraph 就自己记住过去。** 我们的程序取出历史并再次发给模型，模型才有了“上次说过什么”的上下文。它也不能自动看全项目：`bind_tools()` 提供工具的名称、用途和参数定义，执行器决定允许调用哪些 Python 函数。

本次 checkpoint 保存了消息，也保存图执行状态。[LangGraph 官方持久化说明](https://docs.langchain.com/oss/python/langgraph/persistence)将这种按线程组织的状态称为线程级持久化；跨线程的用户画像或知识库需要另行设计。

## 两个 SQLite 文件为什么分开

| 数据 | 正式默认文件 | 例子 |
| --- | --- | --- |
| 业务事实 | `assistant.sqlite3` | “交综述”这项待办是否已经完成 |
| 会话与执行状态 | `assistant_checkpoints.sqlite3` | 用户给项目起了什么代号、模型提过什么工具调用、还有哪个节点没执行完 |

本次练习显式把它们命名为 `module01_tasks.sqlite3` / `module01_memory.sqlite3`，职责相同。

同一个本地助理中的不同线程共用业务库，所以换 `study-b` 仍能查到你保存的待办；对话记录按线程隔离，`study-b` 看不到 `study-a` 的代号。模型必须重新调用查询工具核对待办当前状态，因为任务可能已在另一个请求中完成。

本次称呼记忆没有自动转换成跨会话画像；消息历史也没有自动转换成向量知识库。后续 RAG 会用文件解析、Embedding 和检索处理研究资料。

## 用同一个主 Agent 操作待办、分类与时间

依次执行下面这些命令，观察 JSON 里的 `tool_names`、`tool_results`、`node_order`。它们说明实际执行过程，不能只凭模型说“已完成”判断。

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id study-a --message "请新增待办：周五前交综述，地点实验楼302。"
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id study-b --message "我还有哪些事没做完？"
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id study-a --message "交综述已经做完，请查询全部待办后把这一项标记为完成。"
```

本次验收依次实际调用 `save_task`、`list_tasks`、`list_tasks → complete_task`，最后未完成数量为 0。查询证据只对当前用户轮次有效；长期复用主图时，工具表仍为每轮重新建立。恢复同一未完成轮次时才从该轮真实 `ToolMessage` 重建已查询 ID 集合，避免上一轮查过就永远允许完成的错误。

时间解释示例：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id time-study --reference-time "2026-10-16T20:00:00+08:00" --message "请使用时间解释工具解释原消息中的周三对应哪一天，只解释不保存。"
```

真实工具结果保留 `precision: "date"`、`date: "2026-10-14"`，不包含 `at`。`reference_time` 作为原消息元数据存入历史；传给模型时标出属于哪条消息。工具只接受该线程消息已提供的参考时刻，禁止模型自行猜测服务器今天。该检查能拒绝凭空生成的锚点，但一般情况下模型选择哪条历史消息仍需依原文核对。

通知分类示例，完整原文直接发送：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id notice-study --message "请分析这条完整通知的类别、摘要、事项和附件，不要保存待办：明天下午三点在实验楼302开组会；周五前交综述，附件是两篇参考论文。"
```

本次真实结果是研究类、两项事项、`attachment_description: "两篇参考论文"` 和 `attachment_count: 2`。附件描述代表通知提供了线索；文件下载、正文解析与 RAG 尚未发生。当前系统指令引导模型按请求选择工具，尚未给通知提供单独的只读权限模式。

## 失败后如何继续

模型请求可能在待办保存之后失败。业务库和 checkpoint 分开提交，不是一个跨库原子事务；HTTP 失败不等于之前成功保存的待办已回滚。

1. 出错响应的 `detail.thread_id` 保留会话编号。先用 `--history` 查看 `pending` 和 `next_nodes`。
2. 有未完成流程时，新消息返回 409，避免新消息打断尚未配对的工具调用。
3. 确定继续原流程，执行显式恢复；不追加用户消息，保留同一个 `turn_id`：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id study-a --resume
```

没有待恢复执行时，`--resume` 也返回 409。普通完成的对话直接继续发消息即可。本模块未提供取消未完成轮次的接口。

恢复可能重放尚未存入检查点的工具批次，所以 `save_task` 的稳定 ID 和 `complete_task` 的幂等性仍然必要。不能把 checkpoint 当成所有外部操作“只执行一次”的保证。

响应 `tool_names/tool_results/node_order` 只包括当前用户轮次；完整多轮记录用 `--history` 查看。恢复属于同一轮，因此会包含该轮中断前已完成的工具结果。

## 综合练习：只改线程编号，观察两种数据

完成前面的操作后，只需提交一份反馈：

1. 重启后，`study-a` 对项目代号的回答；`study-b` 对同一问题的回答。
2. 换成 `study-b`，发送“查询全部待办，含已完成”，能否查到 `study-a` 保存的待办？无需贴全部历史，只贴本轮 `tool_names` 和结果即可。前面已完成综述，若只问未完成则可能正确返回空列表。
3. 用自己的话解释：如果想让重启后继续记得“星河计划”，应该保留哪一个练习数据库？“交综述已完成”又保存在另一个库的什么职责里？

这是一项完整验证：同时练会线程键、持久状态、工具调用与业务存储。反馈后核对学习证据，并在条件满足时直接推进模块二“完整消息、来源身份与业务闭环”，不再把每个字段拆成一轮。

## 验收证据与当前边界

- 73 项旧课程回归 + [11 项新增模块集成测试](../tests/test_agent_memory.py)全部通过。新增项使用假模型、真实图/ASGI 和临时双 SQLite，覆盖应用重建恢复、轮次授权、失败恢复、输入边界及受控并发。
- 真实 DeepSeek + TCP 的[验收结果](results/module01_example.json)验证五工具、同线程记忆、不同线程隔离、业务共享与**实际服务进程停止后重启恢复**。周三返回日期无 `at`，两篇论文信息保留。合成消息、临时双库，验证进程和临时数据库已清理，未更改用户业务库。
- 服务入口为工具包 `service:app` 的 `student` 配置档；原多 Agent demo、原 Streamlit、原全量依赖未安装运行。本次主服务尚无流式响应。
- 当前本机单进程、一个本地助理；线程锁只保护当前服务进程。同线程串行、不同线程可以并发。多进程锁、用户认证、上下文压缩和历史保留策略后续按实际需要扩展。
- 本次交付后项目 25%；学习仍为 350/1300≈27%，待用户综合练习后按证据更新；核心学习主线预计剩余 5–7 次有效提问/反馈。

复现离线检查：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_agent_memory -v
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s lessons -p "test_*.py"
.\.venv\Scripts\python.exe -m pip check
```
