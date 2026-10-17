# 第三课：Tool Calling，让模型请求执行 Python 工具

## 这次实际完成什么

第二课返回一个结构化对象；第三课增加一个真实的 `save_task` 工具。模型提出函数名与参数，Python 校验并执行保存，把执行结果作为 `ToolMessage` 交回模型，最后生成答复。

本课完成一轮工具执行与反馈。多个工具轮次、状态恢复和更复杂分支将在 LangGraph 课程继续实现。

本课保存待办到 `local-data/lesson03_tasks.json`，每次运行记录保存到 `local-data/lesson03_trace.json`。这里只保留原文时间，没有设置提醒、换算日期或取得论文文件。两份本地数据文件不进入 Git。

## 先运行并观察

在 `D:\Student_Assistant` 的 PowerShell 中，沿用已有 `.venv` 和 `.env`：

```powershell
.\.venv\Scripts\python.exe .\lessons\03_tool_calling.py
```

2026-10-15 已真实调用默认导师通知，模型提出两次 `save_task`，Python 保存了“开组会”和“交综述”，模型收到工具结果后说明保存情况。固定演示记录见 [results/03_example.json](results/03_example.json)。这份记录是当时的真实返回，之后输入和模型输出可变化。

输出按三个阶段打印：

1. **模型提出的工具调用**：包含 `name`、`args`、`id`。这时只是执行请求。
2. **Python 工具执行结果**：`created` 是新保存，`exists` 是参数完全相同的记录已存在，`error` 是未成功。
3. **模型收到结果后的答复**：根据实际执行结果向用户说明。

只查看已保存内容，不调用模型：

```powershell
.\.venv\Scripts\python.exe .\lessons\03_tool_calling.py --list
```

只预览工具与消息，不调用模型或写待办：

```powershell
.\.venv\Scripts\python.exe .\lessons\03_tool_calling.py --preview
```

## 先看四处关键代码

### 1. 把函数定义为工具

```python
@tool(args_schema=SaveTaskInput)
def save_task(title: str, time_text: str | None = None, location: str | None = None) -> dict:
    """将一项明确待办保存到本机教学文件，保留原文时间，不设置提醒。"""
```

`@tool` 是装饰器，使 LangChain 能读取工具名称、描述和参数。`SaveTaskInput` 是上一课学过的 Pydantic 模型，描述工具接收哪些参数并检查类型。

函数体中的 `write_json(TASK_FILE, tasks)` 才是实际写文件的位置。模型返回函数名与参数时，文件还没有因此自动写入。

### 2. 把工具说明交给模型

```python
model_with_tools = model.bind_tools([save_task])
proposal = model_with_tools.invoke(messages)
```

`bind_tools` 把工具说明和参数格式放进请求；`invoke` 让模型决定这次是否需要调用。默认例子可能提出如下请求：

```json
{
  "name": "save_task",
  "args": {"title": "交综述", "time_text": "周五前", "location": null},
  "id": "本次调用的标识"
}
```

此处 `id` 是为了让执行结果与这次调用对应，不是待办的 ID。

### 3. Python 执行工具

```python
for call in proposal.tool_calls:
    tool_message = execute_tool_call(call)
    messages.append(tool_message)
```

`execute_tool_call` 只从注册表中查找 `save_task`，调用 `selected.invoke(call["args"])`。Pydantic 校验通过后，才执行实际保存函数。函数返回值明确说明新增、已存在或失败。

### 4. 回传执行结果

```python
ToolMessage(content=工具结果的JSON文字, tool_call_id=call["id"])
```

完整脚本会把这条消息追加到对话，再调用一次 `model.invoke(messages)`，让模型根据执行结果回答。第二次请求用于总结，本课不在总结阶段再执行新工具。

## 为什么还要调用一次模型

第一轮模型只提出“请用这些参数调用这个工具”。执行后，它还不知道文件是否写入成功、记录是否重复。回传工具结果后，才能据此组织答复。保存与否以 Python 返回和本地文件为准。

## 你来做一个实际检查

先预测下面这条输入是否应该新增待办，再运行：

```powershell
.\.venv\Scripts\python.exe .\lessons\03_tool_calling.py --message "收到，谢谢。"
.\.venv\Scripts\python.exe .\lessons\03_tool_calling.py --list
```

核对第一阶段的 `tool_calls`、有没有第二阶段的执行，以及待办数量是否变化。若结果不符合预测，保留输入、工具调用和文件变化，下一步一起从 Prompt 和工具描述定位。

接着换成自己的脱敏通知，确认工具参数里的时间与地点是否确实来自原文。不要只看最后的自然语言答复。

## 保存与兼容实现

- 完全相同的标题、时间原文和地点复用同一演示记录。模型改写标题或同一句相对时间来自不同日期时，这种演示去重方式不够；最终项目会基于来源消息 ID 与事项身份设计。
- 本课是单进程 JSON 文件存储。临时文件写完后再替换正式文件；损坏的既有 JSON 不会被当作空列表覆盖。后续用 SQLite 事务处理服务并发。
- 使用 DeepSeek 非思考模式，配置位于 `create_model()` 的 `extra_body`，避免当前通用适配器未保留 `reasoning_content` 导致工具对话不完整。后续按实际提供商能力选择适配器。
- 工具执行成功但最后一次模型请求失败时，待办仍可能已保存。先运行 `--list` 查看，不凭最后一句模型答复判断执行状态。

已有测试使用临时目录和模拟模型，不联网、不修改真实待办：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s lessons -p "test_03_tool_calling.py" -v
```

在上游工具包中，可对照 `src/agents/research_assistant.py` 的 `bind_tools` 与 `ToolNode`；本课先展开执行过程，下一课再交给 LangGraph 编排。

资料：[LangChain 工具调用](https://docs.langchain.com/oss/python/langchain/models#tool-calling)、[DeepSeek 工具调用](https://api-docs.deepseek.com/guides/tool_calls/)、[DeepSeek 模式配置](https://api-docs.deepseek.com/guides/thinking_mode/)。
