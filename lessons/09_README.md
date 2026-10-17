# 第九课：先查询，再把指定待办标记为完成

## 本次学什么、交付什么

本课继续练习 **Tool Calling（工具调用）**，重点是多轮工具协作。本课示例采用的常见流程是：模型先请求 `list_tasks`，看到真实记录后，再请求 `complete_task(task_id)`；Python 校验并调用存储方法，SQLite 修改目标记录，模型根据执行结果回复。

这一能力在个人助理中的作用是：你说“阅读工具调用说明已经做完了”，数据库对应事项从 `pending` 变为 `completed`，未完成数量同步减少。原来的研究分类、消息来源、附件和提醒功能仍需后续开发。

没有增加第三方依赖，也没有新的第九课服务入口。继续运行第七课服务、第五课客户端，保留你写的 `report` 节点和未完成列表接口。

| 技术 | 在哪里体现 | 本课的作用 |
| --- | --- | --- |
| Tool Calling | [assistant_tools.py](assistant_tools.py) 的 `complete_task` 及工具注册表 | 让模型提出明确操作，由 Python 执行。 |
| Pydantic | 同文件的 `CompleteTaskInput` | 只接受非空字符串 `task_id`，拒绝未知字段和错误类型。 |
| LangGraph | [04_langgraph.py](04_langgraph.py) 的 `model → tools → report → model` | 将查询结果送回模型，再进入完成操作。 |
| SQLite / SQL | [task_store.py](task_store.py) 已有的 `mark_completed` | 按 ID 更新数据库中的一行。 |
| FastAPI | [05_fastapi.py](05_fastapi.py) 的 `ToolResult` 与 `process_message` | 返回可核对的完成结果，支持多轮执行。 |

## 先运行一个完整例子

在项目根目录 `D:\Student_Assistant` 打开 PowerShell。先停止旧服务，再启动更新后的服务。本例使用独立练习数据库，方便观察只有两项记录的状态变化：

```powershell
.\.venv\Scripts\python.exe .\lessons\07_sqlite_api.py --db .\local-data\lesson09_practice.sqlite3
```

在第二个终端依次发送两项练习待办：

```powershell
.\.venv\Scripts\python.exe .\lessons\05_client.py --base-url http://127.0.0.1:8001 --message "新增待办，标题为：阅读工具调用说明。"
.\.venv\Scripts\python.exe .\lessons\05_client.py --base-url http://127.0.0.1:8001 --message "新增待办，标题为：整理第九课笔记。"
```

确认客户端最后打印的 `/tasks` 包含这两项 `pending` 记录。如果模型保存的标题不同，下一条请求使用列表中的实际标题。然后发送：

```powershell
.\.venv\Scripts\python.exe .\lessons\05_client.py --base-url http://127.0.0.1:8001 --message "阅读工具调用说明已经做完了，请把这项待办标记为完成。"
```

正常会出现以下调用次序：

```text
list_tasks(status="all")
    ↓ 返回真实的任务列表和 ID
complete_task(task_id="查询结果中目标事项的真实ID")
    ↓ 返回数据库实际记录
模型回复完成结果
```

`tool_names` 应包含先查询、后完成两个工具。若模型额外查询或尝试纠错，调用次数可能增加，应逐项检查 `tool_results`。成功的完成结果里有：

```json
{
  "status": "completed",
  "task": {
    "id": "此处是目标事项的真实ID",
    "title": "阅读工具调用说明",
    "time_text": null,
    "location": null,
    "status": "completed"
  }
}
```

以上是响应片段示意，ID 必须以你的查询结果为准。打开 `http://127.0.0.1:8001/tasks/pending`，应只剩“整理第九课笔记”；`/tasks/pending/count` 应为 1。再次发送同样的完成请求，原事项保持完成，另一项仍未完成，这叫**幂等**：重复执行这一操作，不会额外改变状态。

若这份练习数据库已有记录，数量以实际初始数据为准。想继续原数据，用原先的 `--db` 重启即可；服务输出会显示本次数据库路径。

## 看关键代码，理解模型和程序各做哪一步

`assistant_tools.py` 的 `create_task_tools(store)` 内新增：

```python
    @tool("complete_task", args_schema=CompleteTaskInput)
    def complete_task(task_id: str) -> dict:
        """将用户明确指定的待办标为已完成；先用 list_tasks 查询，传入其返回的真实 id。"""
        if task_id not in queried_ids:
            return {"status": "error", "error": "请先调用 list_tasks 查询目标待办，再使用返回的真实 id。"}
        # 存储方法只更新指定 ID；已完成记录重复调用仍保持 completed。
        task = store.mark_completed(task_id)
        if task is None:
            return {"status": "error", "error": "目标待办已不存在，未修改任何任务。"}
        return {"status": "completed", "task": task}
```

按执行过程理解这几行：

1. `@tool(..., args_schema=CompleteTaskInput)` 将函数包装成可注册的工具；Pydantic 先检查参数。函数的名称、说明、参数格式会提供给模型，函数体不会自动发给模型。
2. `queried_ids` 是外层函数创建的集合，成功调用 `list_tasks` 后，Python 把查询返回的 ID 加进去。完成工具先检查目标 ID 是否确实查询过。
3. `store.mark_completed(task_id)` 调用的是你项目已有的 Python 方法。模型只提出名称和参数，真正修改数据库的是这个方法里的 SQL。
4. 返回值来自数据库实际记录。执行器把它包装成 `ToolMessage`，后续模型根据这个结果组织回答。

工厂返回的注册表也增加了 `complete_task`。如果只写函数但不注册，当前 Agent 就不能调用它。

### 为什么传 ID，不传标题

数据库可能同时存在两项叫“提交材料”的待办，一项是研究申请，另一项是学生事务。标题可能相同，ID 用来准确指向其中一条记录。模型应先读取现有记录，再传目标的真实 ID。

存储层的关键 SQL 已在第六课写好：

```python
# 参数绑定让 ID 作为普通值参与匹配；WHERE 把更新限制到目标记录。
connection.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task_id,))
```

`WHERE id = ?` 决定修改哪一行；没有它会修改整个表。工具参数 `task_id` 最终就在这里体现作用。

### Prompt 和程序检查分别保证什么

SQLite 模式提示词指导模型：只有用户明确要求时才完成，先查 `all`，找不到或无法确定时澄清，不把完成指令保存成新待办。这影响模型选择，不能保证模型始终理解正确。

Python 的检查更具体：**本次图执行没有查询到这个 ID，就拒绝修改**。集合在创建工具表时初始化；当前 `run_graph` 每次重建图和工具表，因此上一条 HTTP 请求查询过也不能替下一条请求跳过查询。将来若缓存并复用编译后的图，就必须重新考虑集合的生命周期。

这只证明 ID 曾被返回，不保证模型对用户意图的理解或同名事项选择一定正确。工具节点顺序执行同一批请求，也可能在同一条模型消息中依次收到查询、完成请求；因此程序没有强制要求模型必须经过两轮调用才能完成。当前还没有跨请求对话记忆，澄清后需要重新发送完整的目标信息。

第六课命令行仍可直接按 ID 调用存储方法；“先查询”的限制属于 Agent 工具层，并未改动通用存储方法。

## 为什么图的步数上限需要调整

正常的先查再完成通常经历：

```text
model → tools → report → model → tools → report → model
```

SQLite HTTP 模式把图执行上限从 8 调到 12，给额外一轮工具纠错留出空间，旧 JSON 模式仍为 8。这个数字是图节点执行步数上限，不是允许调用模型 12 次。LangGraph 只负责继续调用，如何选择工具仍取决于模型与指令。

参数 schema 不符合要求时，当前执行器会回传错误供模型纠正；若模型返回的工具调用本身无法解析，图仍会中止。达到上限或模型后续失败时，先前已提交的数据库操作不会自动回滚，需要查询实际状态。

## 核对结果时，分清三个 completed

| 字段 | 含义 |
| --- | --- |
| 顶层 `execution_status="completed"` | 本轮工具流程没有报错，不表示全部事项已完成。 |
| `tool_results` 中某项 `status="completed"` | 该完成工具返回了成功结果。 |
| 该结果的 `task.status="completed"` | 返回的这一条待办已是完成状态。 |

若本轮先发生工具错误、后成功纠正，顶层仍为 `tool_error`，因为它保留本轮曾发生错误的事实。需要结合每个工具结果和数据库最终状态判断。仅凭模型结语中的“完成了”，不能证明发生了写入。

## 你的练习

按前面的两项待办示例运行一次，反馈三个可核对的结果：

1. 响应中的 `tool_names` 顺序是什么？
2. `complete_task` 返回的 `task.id`，对应前面查询结果里的哪一条记录？另一个任务还是 `pending` 吗？
3. 再发一次相同完成请求，未完成数量有没有继续减少？结合 `WHERE id = ?` 解释原因。

本次掌握证据来自你对实际运行结果的核对和解释。课程代码已经提供，不表示你自动掌握了 Tool Calling，也不表示整个技术模块学完。

## 验证记录

2026-10-16，本课新增 8 项离线测试与第三至八课 44 项回归共 **52 项通过**。覆盖严格参数、本工具表成功查询返回的 ID 检查、目标消失、重复完成幂等、请求隔离、真实结果及三轮工具纠错。模拟模型的请求和回复是预设的，图、工具、HTTP 处理和 SQLite 操作使用真实实现。

另用真实 DeepSeek、随机本地 TCP 端口和临时 SQLite 验证两项合成待办：首次请求得到 HTTP 200、`tool_names=["list_tasks", "complete_task"]`，按七节点路径只完成目标项，另一项保持 `pending`，未完成数量从 2 变为 1。重复同一请求也得到 HTTP 200；模型查询到目标已完成后直接回复，这次只调用 `list_tasks`，没有重复调用完成工具，数据库和未完成数量保持不变。工具函数本身重复完成的幂等性由离线测试验证。

两次未完成列表与数量接口均与数据库一致，结果见 [results/09_example.json](results/09_example.json)。临时验证服务已停止；以上验证未修改你的业务数据库。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s lessons -p "test_09_complete_task.py" -v
```

项目进度保持 21%；技术学习约 23%。本课 Tool Calling 专项练习尚待用户完成，暂不提高掌握分。
