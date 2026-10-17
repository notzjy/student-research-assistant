# 第二课：Pydantic 与 LangChain 结构化输出

## 这次做成什么

第一课修改 Prompt 后可以拿到类别文字。第二课直接把完整通知交给模型，返回可在 Python 中访问的 `NoticeAnalysis` 对象：类别、事项列表、每项的时间原文与地点，以及附件描述和消息提到的数量。

这一步为后续把结果保存到数据库、展示分类标签和创建提醒提供字段。当前不下载附件、不换算精确时间、不设置提醒。

脚本是 `02_structured_output.py`，沿用根目录 `.venv` 和 `.env`。无需再安装一套环境，第一课用户修改过的文件保持原样。

## 先运行

在 `D:\Student_Assistant` 的 PowerShell 中：

```powershell
.\.venv\Scripts\python.exe .\lessons\02_structured_output.py
```

你会看到模型实际生成的 JSON，以及程序直接读取对象字段的结果。JSON 中的 `null` 在 Python 对象中是 `None`，表示未知或未说明。

2026-10-15 已使用本机模型配置真实调用默认示例并通过校验：类别为研究，两项事项为开组会和交综述，组会地点为实验楼302，综述地点为空，附件提及数量为 2。本次真实返回保存在 [results/02_example.json](results/02_example.json)，记录的是新增摘要字段之前的结果，不是固定返回或模拟推理。其他输入的质量仍需逐条核对。

也可以运行一个不需要模型的实验，看看校验怎样拒绝错误字段：

```powershell
.\.venv\Scripts\python.exe .\lessons\02_structured_output.py --validate-demo
```

预览模式只显示输入和 schema，不发送请求：

```powershell
.\.venv\Scripts\python.exe .\lessons\02_structured_output.py --preview
```

## 新技术具体出现在哪里

| 代码 | 技术和实际作用 |
| --- | --- |
| `class Event(BaseModel)` | Pydantic 数据模型：声明每个事项有哪些字段。 |
| `title: str`、`list[Event]` | Python 类型标注：标题应为字符串，事项集合应为列表。 |
| `Literal[...]` | 将类别限制为四个允许值。 |
| `Field(description=...)` | 描述字段含义，帮助模型理解该填什么。 |
| `Field(ge=0)` | 数量不能为负；strict 配置还会拒绝字符串或布尔值充当数量。 |
| `model_json_schema()` | 自动把 Python 模型转成可发送给模型的 JSON Schema。 |
| `with_structured_output(NoticeAnalysis, method="json_mode")` | LangChain 发出 JSON 模式请求，并把返回内容解析、校验为 Pydantic 对象。 |
| `result.category`、`result.events` | 使用模型提取结果，不必从一大段自然语言中查找字段。 |

`json_mode` 只要求接口返回 JSON，字段含义仍需放进 Prompt。本课显式发送 schema 和格式示例；类别错误、数量为负等内容由本地 Pydantic 校验。格式和类型通过不保证提取的事实正确，仍应对照原通知。

## 对照原文检查一次

默认通知里应能看到开组会和交综述分别记录，组会地点来自原文，未说明的地点为空；两篇论文保留为附件描述及数量线索。`周五前` 保留原话，不自动补成 23:59。

换一条输入，检查缺失信息：

```powershell
.\.venv\Scripts\python.exe .\lessons\02_structured_output.py --message "收到，辛苦了。"
```

这条没有明确待办和附件，预期 `events` 为空，附件字段为空；实际输出需要核对。这是理解模型行为的例子，不是固定返回。

## 看到运行结果后，只改一处

在 `NoticeAnalysis` 类中，紧接 `category` 字段后加入：

```python
summary: str = Field(description="用一句话概括原通知")
```

保存并重新运行。观察 JSON 是否多出 `summary`。因为脚本从类定义自动生成 schema，你新增的字段也会进入模型输入。

当前用户已完成此练习，源码中的必填 `summary` 已保留；Prompt 中的格式示例和本地校验实验数据也已补上该字段。这说明修改数据模型时，还需要核对使用它的示例与测试数据。此处完成的是在指导下扩展字段，后续继续练习独立设计和排错。

再思考一个与代码直接相关的问题：去掉 `attachment_count` 的 `ge=0` 后，本地校验实验还会拒绝负数吗？先预测，再实际运行 `--validate-demo` 验证。

## 在最终项目中的位置

本课 schema 后续会迁入业务模块，提取函数接到 LangGraph 节点中；下一课在此基础上学习 Tool Calling，让模型请求调用一个真实 Python 工具。上游已有 `src/agents/interrupt_agent.py` 的 Pydantic / `with_structured_output` 示例可对照阅读。

资料：[LangChain 模型结构化输出](https://docs.langchain.com/oss/python/langchain/models#structured-output)、[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[Pydantic 模型](https://docs.pydantic.dev/latest/concepts/models/)。
