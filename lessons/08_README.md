# 第八课：让 Agent 调用查询工具，读取已保存的待办

## 本次交付与技术位置

你已新增并验证 `GET /tasks/pending`。这次给 Agent 增加 `list_tasks` 工具，让“我还有哪些事没做完？”这样的自然语言问题，也能查询同一 SQLite。

本课继续练习 **Tool Calling**：定义输入、注册工具、模型提出名称与参数、Python执行、回传真实结果、模型组织回答。模型负责选择，存储方法负责查询。

### 模型能看到全部代码吗

本项目中的模型不会自动读取工作区源码。客户端只是把 `--message` 的内容放入HTTP正文；后端将问题、查询指令和通过 `bind_tools` 转换的工具描述发给模型。工具描述包括名称、来自函数docstring的说明、参数JSON Schema，不包含函数体或数据库路径。本机离线转换实际 `list_tasks` 工具已确认这一点，没有调用模型或读取真实待办。

查询指令明确告诉模型：询问已保存待办时使用 `list_tasks`，未完成对应 `pending`。模型根据这些信息提出名称和参数；`execute_tool_call` 才在本地按注册表验证并调用函数。工具函数内部再调用存储方法，SQL由Python程序执行。模型不会随意选择整个项目中的函数运行；未注册的请求会被执行器拒绝。

查询得到的记录会放进 `ToolMessage`，随后发送给模型组织回答，因此匹配的待办内容会进入模型输入。`report` 当前打印的数量和图的 `node_history` 则没有作为模型消息发送。若今后主动提供源码或文件读取工具，模型可见范围会随配置改变，不能把当前的可见范围泛化到所有智能体。

| 文件 | 本课新增或复用的关键位置 |
| --- | --- |
| [assistant_tools.py](assistant_tools.py) | `ListTasksInput` 限制筛选参数；`create_task_tools` 注册保存和查询两个工具。 |
| [04_langgraph.py](04_langgraph.py) | SQLite模式增加查询提示词；绑定工具；沿原有 `model → tools → report → model` 路径执行。 |
| [03_tool_calling.py](03_tool_calling.py) | `execute_tool_call` 继续用注册表校验和执行，构造 `ToolMessage`。 |
| [05_fastapi.py](05_fastapi.py) | 新增 `QueryToolResult`，保留列表结果；新增 `tool_names` 便于核对模型请求的工具。 |
| [07_sqlite_api.py](07_sqlite_api.py) | 继续作为服务入口，保留你新增的未完成列表接口。 |
| [task_store.py](task_store.py) | 复用 `list_tasks(status=...)` 的SQL查询。 |

没有新增第八课服务脚本。继续使用第七课8001服务和第五课客户端，减少重复代码；本课所有新增函数和类都有中文说明。

## 第一步：重启现有服务

代码更新后，先在服务端终端按 Ctrl+C，再用原来选择的数据库路径启动。以下示例使用默认的 `local-data/assistant.sqlite3`：

```powershell
.\.venv\Scripts\python.exe .\lessons\07_sqlite_api.py
```

如果你在第七课使用 `lesson07_practice.sqlite3`，就保留 `--db .\local-data\lesson07_practice.sqlite3`。查询结果取决于当前文件里的数据，换路径会切换记录。

需要准备样例事项时，可以在另一个终端使用原客户端发送默认通知：

```powershell
.\.venv\Scripts\python.exe .\lessons\05_client.py --base-url http://127.0.0.1:8001
```

也可以直接查询空数据库；正确结果应是没有匹配待办，模型不应编造记录。

## 第二步：用自然语言查询

```powershell
.\.venv\Scripts\python.exe .\lessons\05_client.py --base-url http://127.0.0.1:8001 --message "我还有哪些事没做完？"
```

观察响应中的 `tool_names` 和 `tool_results`，并与 `http://127.0.0.1:8001/tasks/pending` 核对。空数据库时，响应的关键字段应类似下面这样；这只是完整响应的一部分：

```json
{
  "execution_status": "completed",
  "tool_names": ["list_tasks"],
  "tool_results": [
    {"status": "queried", "filter_status": "pending", "count": 0, "tasks": []}
  ]
}
```

数据库有匹配记录时，`count` 和 `tasks` 相应变化。服务端通常会显示 `model → tools → report → model`；工具纠错或追加调用可能增加轮次。

| 字段 | 含义 |
| --- | --- |
| `execution_status="completed"` | 本轮工具流程成功，不表示所有待办已经做完。 |
| `tool_results[].status="queried"` | 这个结果来自查询；保存仍返回 `created` 或 `exists`。 |
| `filter_status` | 本次查询的筛选条件：pending、completed或all。 |
| `tasks[].status` | 每一项待办自己的完成状态。 |

`tool_names` 取自实际 `ToolMessage.name`，表示收到工具反馈的请求名称；失败或未注册的工具请求也可能出现，所以判断成功还需要查看实际结果状态。

## 第三步：看完整工具调用过程

这次模型可能提出类似下面的调用。ID只是示例，实际由模型接口返回：

```json
{
  "name": "list_tasks",
  "args": {"status": "pending"},
  "id": "example-call-id",
  "type": "tool_call"
}
```

1. `bind_tools(...)` 把工具名称、说明与参数格式提供给模型。
2. 模型根据问题提出 `list_tasks` 和 `status="pending"`；这时数据库查询还没有执行。
3. Python的 `execute_tool_call(...)` 在注册表中找到工具，验证参数后调用它，工具再调用 `store.list_tasks(...)`。
4. Python把真实查询结果包装成 `ToolMessage`，关联原调用ID；下一轮模型读取这些结果再回答。

这与第三课的保存工具使用同一套机制。保存和查询是两项不同能力，模型应根据用户要求选择。[LangChain官方工具说明](https://docs.langchain.com/oss/python/langchain/tools)

## 关键代码：查询工具怎样定义和注册

`ListTasksInput` 将 `status` 限制为 `pending`、`completed`、`all`，默认 `pending`，拒绝额外字段。

下面的工具定义位于 `create_task_tools(store)` 内部，因此可以使用传入的 `store`：

```python
    @tool("list_tasks", args_schema=ListTasksInput)
    def list_tasks(status: Literal["pending", "completed", "all"] = "pending") -> dict:
        """查询已保存的待办，按完成状态筛选，返回真实记录与数量。"""
        # all表示不筛选，对应存储方法的status=None。
        tasks = store.list_tasks(status=None if status == "all" else status)
        return {"status": "queried", "filter_status": status, "count": len(tasks), "tasks": tasks}
```

工厂末尾的注册表也增加了新工具：

```python
return {save_task.name: save_task, list_tasks.name: list_tasks}
```

只有函数定义还不够；模型绑定和程序执行都需要通过这张注册表找到工具。第七课的依赖注入继续生效：每个应用的工具都使用各自绑定的存储。

SQLite模式的提示词还补充了行为要求：查询已有事项时调用 `list_tasks`，仅明确新增事项时调用 `save_task`，查询失败不能说成“没有待办”。提示词用于引导模型选择；本课实例通过实际调用记录验证行为，不将提示词当作严格的只读权限控制。

## 查询结果为什么需要另一种响应模型

原保存工具返回单条 `task`，新查询工具返回一组 `tasks`。因此 `05_fastapi.py` 增加 `QueryToolResult`：校验 `queried`、筛选条件、非负数量和待办列表。

`collect_tool_results` 从实际 `ToolMessage` 读取JSON，按结果状态选择保存模型或查询模型校验。`MessageResponse.tool_results` 允许这两种结果。即使模型结语误说“已保存”，查询结果仍保留 `queried`；真实写入情况应以程序结果为准。

## 你的练习：查询已完成事项

先预测两个值，再运行下面的请求：

- 模型应该选择哪个工具？
- 这个工具的 `status` 参数应该是什么？

```powershell
.\.venv\Scripts\python.exe .\lessons\05_client.py --base-url http://127.0.0.1:8001 --message "我已经完成了哪些待办？"
```

用响应中的 `tool_names`、`filter_status` 和实际列表验证预测。如果目前没有已完成记录，`count=0` 是合理结果；想准备一条已完成样例，可先从 `/tasks` 复制ID，再通过第六课命令行操作同一数据库：

```powershell
.\.venv\Scripts\python.exe .\lessons\06_sqlite.py --db .\local-data\assistant.sqlite3 --complete 实际ID
```

若服务使用其他 `--db`，上述命令也要使用同一路径。完成练习后，请反馈选择的工具名、筛选参数和数量，并指出真正执行数据库查询的是模型还是Python代码。

## 验证与当前范围

本课8项离线测试与第三至第七课36项回归测试通过，共44项，使用模拟模型、真实LangGraph、进程内HTTP和临时SQLite。覆盖筛选、空结果、非法参数、消息回传、跨轮保存后查询、数据库隔离，以及你新增接口的一致性。

2026-10-15还验证了真实DeepSeek与本机TCP服务：临时数据库准备一项pending和一项completed，分别提问“我还有哪些事没做完？”与“我已经完成了哪些待办？”。两次均返回HTTP200，`tool_names`只有list_tasks，筛选分别为pending/completed，数量各1；结果ID与数据库一致，查询前后所有记录和完成状态不变。用户新增的未完成列表接口也与工具结果一致。记录见 [results/08_example.json](results/08_example.json)，临时验证服务已停止。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s lessons -p "test_08_query_tools.py" -v
```

本课扩展已有待办查询的自然语言入口，项目进度仍为21%；用户已完成第七课接口跟随练习，Python模块记50，技术学习约23%。Tool Calling掌握分等用户本课实践与解释后更新，不因代理实现自动加分。

当前仍未接入完整原消息、来源ID去重、重点角色、附件、提醒调度或完整上游界面。后续继续把这些核心功能逐步接入。
