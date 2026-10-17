# 实践课程：先跑代码，再理解和修改

课程准备更新：2026-10-16。下方模型配置资料核查仍为 2026-10-15。

用户最新要求加快推进，后续按[六批次核心技术栈路线](../docs/ACCELERATED_ROADMAP.md)整合到主项目，不再把小字段各拆一轮。当前预计从下一条消息起6–8次有效提问/反馈；已有课程保留作参考。用户已正确解释第十一课日期结果不能额外携带at，概念核对完成，独立实现待验，学习分保持约27%。

## 第一课的目标

用 LangChain 调用一次模型：完整导师通知作为输入，屏幕显示模型实际返回。你将能指出模型名、API 地址、鉴权密钥、Prompt、原消息、调用语句和返回值各在哪里，并替换一条输入验证行为。

用户已实践 Prompt、Pydantic 字段、LangGraph 节点、FastAPI 路由、SQLite 计数和依赖注入；[第八课：Agent 查询工具](08_README.md) 与[第九课：通过自然语言完成待办](09_README.md) 已由代理验证，真实第九课记录见 [09_example.json](results/09_example.json)，用户专项 Tool Calling 练习仍待反馈。当前[第十课：时间归一化与歧义预览](10_README.md) 已交付，用原消息发送时间和有限规则转换表达；12 项专测加旧 52 项回归共 64 项通过，真实 TCP 接口与 CLI 核对见 [10_example.json](results/10_example.json)。本课不调用模型，不等于泛化中文时间理解。课程与测试函数均有中文说明。完整上游工具包的服务与界面尚未安装运行；项目 23%、学习 350/13、约 27%。用户本次日期运算与原消息基准练习记时间模块 50/100，Tool Calling 仍为 0，教学新增不自动计为用户掌握。

第九课的已查询 ID 记录属于一次工具表创建；当前每请求重建图与工具表使其隔离，以后缓存图需另处理。同批工具按顺序执行，“先查后完成”不固定模型调用轮数，ID 检查也不代表程序保证语义选择正确。

第十课入口为 `time_parser.py`、`10_time_normalization.py` 和既有服务的 `POST /time/normalize`。必须提供带 ISO 偏移的原消息 `reference_time`，保留原文和参考偏移，固定 UTC+08；周五前、合法日期缺钟点、区间、否定混合等表达先待确认，不补 23:59。非法日期时分为 422，完整日期即使缺钟点也检查合法性；不改原任务表、保存协议或原工具，也不保存截止时间或安排提醒。临时验证服务已停止，合成数据库前后不变。用户已正确给出 `at=2026-10-17T15:00:00+08:00` 并说明应以消息发送时间为准；仅本练习记跟随实践，调度、持久提醒、全球时区及独立设计未验，第九课专项仍待反馈。

用户已确定裸星期按消息所在自然周（周一至周日）解释，过去的星期不自动顺延；日期与周/月/年范围也有独立精度，缺钟点不应抹去已确定的日期。该政策及通用表达、解析器/模型候选加程序验证的路线见[时间解释规则](../docs/TIME_POLICY.md)；旧 `/time/normalize` 保持原有限支持范围，第十一课新增解释接口。

[第十一课：时间精度与模型意图](11_README.md) 入口为 `time_semantics.py`、`11_time_precision.py` 和新增的 `POST /time/interpret`，旧 `/time/normalize` 保留。用 Pydantic 判别联合区分分钟/日期/日期范围/待确认；日期不补成 00:00，范围包含两端日期。模型给结构化意图，Python 做通用日历运算并应用精确裸星期规则；一般语义提取仍可能出错。`--demo` 是预设意图的离线示范；真实 DeepSeek 直接提取、临时 TCP 的 9 条表达与 CLI 经 HTTP 均已验证，9 项新测试加旧 64 项共 73 项通过，记录见 [11_example.json](results/11_example.json)。非法无偏移基准为 422，旧接口保留、临时数据库未变，服务已停止；用户已正确核对日期结果不应含at，独立实现待验，学习分不变。

## 在 Windows PowerShell 运行

工作目录为 `D:\Student_Assistant`。本机根目录 `.venv` 已创建，使用 Python 3.13.9，包含本课依赖。无需激活环境，直接运行它的 Python 即可：

```powershell
.\.venv\Scripts\python.exe .\lessons\01_model_call.py --preview
```

你会看到 `system` 中的任务要求与 `human` 中的完整原文。这一步验证程序能运行和准备输入，尚未执行模型推理。

用户已确认有 DeepSeek API Key。工作区根目录 `.env` 将预填 DeepSeek 官方文档当前列出的兼容地址和 `deepseek-flash`（核查于 2026-10-15），由用户在本机填入 `COMPATIBLE_API_KEY`。不要把密钥贴到聊天中。

```powershell
Copy-Item -LiteralPath .\.env.example -Destination .\.env
```

上述复制命令仅供其他环境首次配置参考。本机已准备 `.env`，直接编辑即可；已有配置不要重复复制覆盖。

配置完成后发送一次真实请求：

```powershell
.\.venv\Scripts\python.exe .\lessons\01_model_call.py
```

没有配置时，程序会列出缺少的环境变量并退出。`401` 通常先查密钥和平台是否匹配，`404` 先查地址和模型名，连接错误先查网络；保留错误码即可，不要分享凭证。

此课使用兼容接口的基本文本请求。第三方提供商扩展字段不在本课范围内；后续需要推理记录、工具调用等能力时核查实际平台与适配器支持。

脚本显式使用 UTF-8 输出以兼容 Windows 管道；`http_socket_options=()` 保留环境代理的默认行为。第一课先关注模型配置、消息和 `invoke`。

## 七个位置，对应七个实际知识点

| 代码位置 | 技术名 | 在这个项目中做什么 |
| --- | --- | --- |
| `from langchain_openai import ChatOpenAI` | LangChain 模型适配器 | 为模型请求提供统一的 Python 调用方式 |
| `load_dotenv(...)` 与 `os.environ[...]` | 环境变量、配置管理 | 将平台地址、模型与凭证从业务逻辑中分离 |
| `model=...` | 模型选择 | 指定让哪个模型处理消息 |
| `base_url=...`、`api_key=...` | 模型 API、鉴权 | 指定访问哪项服务、使用哪个凭证 |
| `INSTRUCTION` 与 `system` | Prompt、消息角色 | 告诉模型本次要完成什么任务 |
| `human` 中的 `notice` | 模型输入 | 把完整原消息交给模型 |
| `model.invoke(...)` 与 `response.content` | 模型调用、响应读取 | 真正发送请求并获取模型返回的正文 |

## 它怎样接回工具包

现有 `agent-service-toolkit/src/core/llm.py` 用 `get_model(...)` 创建模型，`src/agents/chatbot.py` 用 `await model.ainvoke(messages)` 调用。本课的 `invoke` 是同步版本；后面接入服务时再学习异步 `ainvoke`。

这里先独立安装同一模型适配器，避免第一课就安装工具包的全部供应商、数据库和界面依赖。本课环境的完整安装版本记录在 `requirements.lock.txt`；以后运行完整工具包时在其目录使用自身的 `uv.lock` 和独立环境。

## 看到真实返回后再练习

只改变输入，保持其他代码不动：

```powershell
.\.venv\Scripts\python.exe .\lessons\01_model_call.py --message "辅导员通知：周三18点前完成宿舍信息登记。"
```

比较两次返回的类别、事项、时间与附件描述。解释是哪行把修改后的原文交给模型；随后修改 `INSTRUCTION`，要求只回答类别与事项，观察输出如何变化。本次练习直接围绕已运行代码，不要求先手工拆分消息。

## 后续每一步的可见成果

1. 结构化输出与 Pydantic：让模型返回可校验的事项对象。
2. Tool Calling：给 Agent 一个真实的保存待办工具，观察工具调用记录与落库结果。
3. LangGraph：把理解消息、调用工具、确认与重试连接为有状态流程。
4. FastAPI、SQL：通过 HTTP 导入和查询消息，重启后数据仍在。
5. 文件解析与 RAG：导入论文，检索相关段落并基于证据回答。
6. 评测、Trace、部署：用固定样例衡量效果，定位失败步骤，将服务打包运行。

官方资料：[LangChain 模型适配器](https://docs.langchain.com/oss/python/integrations/chat/openai)、[DeepSeek 首次 API 调用](https://api-docs.deepseek.com/)。
