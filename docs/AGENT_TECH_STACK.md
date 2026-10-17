# 智能体开发核心技术栈与个人学习看板

## 收尾最新状态（2026-10-17）

项目进度：`█████████████████░░░` **84%**，阶段 **[10,10,20,15,20,0,9]**。
技术学习：`██████░░░░░░░░░░░░░░` **31%（400/1300，暂缓）**。预计学习对话次数：项目交付后重新安排。

真实QQ附件已取得：用户指定PDF经IMAP下载，与本地原件哈希一致；正式工作流的正文分类、BGE/Qdrant检索、引用问答、UID重放去重与生命周期重启共[9项实测通过](results/qq_attachment_acceptance.json)。另发现历史积压会拖慢新信，已补最近邮件优先和已入队积压下的新到邮件优先；不推进快速通道的历史游标，不丢历史邮件。[4项真实QQ检查](results/mail_recent_priority_live.json)与13项新增专项测试通过。本机与GitHub Actions最新回归350项（根277＋lessons73）均通过，pip check通过。

GitHub Actions已实际完成Docker镜像构建、Compose后端和界面启动、APK下载及重启后SQLite角色持久性验收，见[运行记录](https://github.com/notzjy/student-research-assistant/actions/runs/39026274490)。M6剩余2分按原要求拆为容器1分已验、手机实机1分待验，因此升至9/10；本机Windows仍未安装Docker，不把Linux容器当作Windows通知实测。M5按原完整闭环规则保持0/15，仍缺真正新邮件到达与通知延迟；旧样本重收8.137秒不等于新信延迟。

安卓局域网服务和下载入口已准备，手机安装/授权/真实通知仍待本人操作；教学继续暂停。新邮箱修复需通过桌面控制台停止并重新启动后加载，自动进程重启被执行策略拒绝，现有服务未被我停止。最新交付状态见[收尾报告](results/closeout_status.json)。以下历史节点保持其当时分数和测试数量。

## 历史：GitHub初次发布节点（2026-10-17）

项目进度：`█████████████████░░░` **83%**，阶段 **[10,10,20,15,20,0,8]**。
技术学习：`██████░░░░░░░░░░░░░░` **31%（400/1300）**，分数冻结。

用户已明确要求**先停止学习，直接交付项目，后续再围绕完整项目安排技术栈学习**。当前不布置练习、不以答题阻塞交付，也不再估算学习对话次数；既有学习证据保留，代理实现和验收不增加个人掌握分。

[本轮交付证据](results/final_delivery.json)的 **29项检查全部通过**：实际启动器管理后端与Streamlit、真实DeepSeek/BGE512/Qdrant、人工分类与多标签、消息修订、文件正文分类、完整起止时间、编辑重排提醒、重点收件箱、无线通知入口、APK下载、Edge截图和真实重启后的双库/索引/修订/通知队列持久性。新增事项“已取消”与“已完成”分离，普通粘贴表单可直接收件；实际后端进程16444→25688验证重启。专用临时数据与进程已清理。最终回归 **337项通过（根tests264＋lessons73）**，已逐项核查最新日志根测试本机日志的`Ran 264 tests / OK`与教学脚本本机日志的`Ran 73 tests / OK`，并核对[公开汇总](results/final_delivery.json)；此前326项及旧240项失败日志均不是当前最终结论。

[QQ实际只读检查](results/qq_delivery_check.json)取得2封邮件正文（1015、2034字符），重新连接再同步0封重复；未取得真实附件，未测新邮件到达至提醒的延迟，故不满足原M5完整闭环规则，仍为0/15。[Android通知桥](CHANNEL_INTEGRATION.md)APK已编译验签、提供无线下载与配对；当前设备数0，尚未手机安装、授权或真实通知验收，不能将HTTP合成输入计为真机接入。Git已进行公开发布整理，当前提交与远端状态见[发布记录](PUBLICATION.md)；Docker仍未实际构建运行。

以下60%/218项、53%/198项以及原对话次数均为对应历史节点，不再作为当前状态或当前学习安排。市场与岗位热度资料仍以**2026-10-15**核查范围为准。

## 历史：模块六交付节点（2026-10-17，项目60%）

项目进度：`████████████░░░░░░░░` **60%**，阶段[5,7,19,7,16,0,6]。技术栈学习：`██████░░░░░░░░░░░░░░` **31%**，400/1300；预计主线还需约 **1–3次有效提问或反馈**，用于QQ真实附件、增量同步与延迟完整联调、本轮界面练习及补缺；原始多平台接入目标尚未完成。

[模块六](MODULE_06_DELIVERY.md)的五页本地Streamlit客户端已由代理完成[16项真实检查](results/module06_example.json)，包含实际HTTP、AppTest、DeepSeek、BGE512/Qdrant及[独立Edge截图](results/module06_dashboard.png)；截图中文、重点角色、时刻与地点已目视检查。实际服务进程28216→26300重启后双库、文件索引和Trace仍在，临时验收数据已清理；218项回归（根145＋lessons73）通过，新增20项为适配器9、完成接口3、界面8，96包依赖pip check通过。

Git仅初始化main，无暂存、提交和远程；Docker无CLI，仅文件与静态检查，未构建运行。用户界面综合练习待反馈，Streamlit和Git/部署仍0。创建过滤不能覆盖已入队旧计划的回答仅补充守卫职责理解，时间50不变；评测仍0。热度核查仍为2026-10-15，本次只更新实现与验收状态。以下2026-10-16的53%、198项和2–4次为历史节点。

## 历史：旧邮件误提醒修复（2026-10-16）

- 已确认QQ邮箱实际登录与部分正文读取；用户也已看到Windows弹窗，但历史邮件被误建提醒。真实附件、增量同步与延迟完整验收待补，M5完整闭环暂不计分。
- [本轮排错讲义](LESSON_REMINDER_GUARD.md)与[离线演示](../reminder_guard_demo.py)区分原消息时间和当前决策时间：2021年消息的“四天后”仍算2021年，不应据此在今天新建自动提醒。新自动计划跳过过期事项和已过提醒钟点；旧自动/legacy队列在投递前检查来源时间，显式manual计划保留补发。升级仅添加origin标记，不批量删除用户消息或事项。
- Prompt区分无需操作的商业自动续费告知与明确取消请求；不是按“续费”关键词一律封禁。固定语义评测当前为10条，新增两条对照样例；历史8条与70项时间字段结果仍是模块五交付时的记录，不能直接套到当前10条。
- 用户已完成消息导入和Trace查询跟练；“执行完成不等于语义正确”的解释与评测修改练习仍待反馈，评测掌握仍0。项目53%、学习400/1300约31%、主线预计2–4次保持；本轮198项全量测试（根tests125＋lessons73）通过；真实DeepSeek的10条live得到分类10/10、事项数10/10、时间字段81/81、附件线索4/4、模型附件数量10/10，rules同分只属工程回归。结果见[本轮修复验收](results/reminder_guard_fix.json)。

课程与实现状态更新：2026-10-17；市场热度资料核查日期：2026-10-15。适用目标：通过个人研究与日程助理，学习可用于求职的 Agent 应用开发能力。

模块六历史计分依据：M1新界面启动+2%、M4时间地点标签+3%、M6本地演示/Git初始化+2%，当时由53%升60%；不包含Docker部署。QQ实际登录与部分正文读取已有证据，完整附件/增量/延迟仍待验，M5当时保持0；文件正文分类等当时待完成，现已补齐的内容以顶部权威交付与下表为准。

学习计分依据：13模块合计400/1300，约30.77%；最近得分变化仍为RAG概念25。LangGraph当前线程应用75、时间50等其他分不变；Tool Calling、异步、Streamlit、Git/部署与评测仍0，独立实现和本轮用户综合操作待验。

当前教学已暂停，取消按剩余对话推进学习的安排；原1–3次只保留为模块六历史估计。项目实现83%，个人学习400/1300约31%保持，后续围绕完整项目重新排课。

这里把技术名称、项目位置、你的学习证据和当前热度放在一起。学习分以 [详细学习记录](LEARNING_PROGRESS.md) 为准，项目验收以 [项目方案](PROJECT_PLAN.md) 为准；后续练习完成时同步更新本文件。

## 先回答：SQLite 适合现在的智能体开发吗

**适合本项目当前的本地个人应用阶段。SQLite 是仍广泛使用的成熟嵌入式关系数据库。** 它直接保存本地文件，无需单独部署数据库服务，适合先学习表、SQL、主键与事务。官方同时说明：每个数据库文件同一时刻只有一个写入者；需要很多并发写入者或跨机器直接访问数据时，应考虑客户端/服务端数据库。[SQLite 官方适用场景](https://www.sqlite.org/whentouse.html)

“先进”要结合任务判断：本地、低写入并发、安装维护简单，是 SQLite 的优势；服务端多用户、并发事务等能力，则值得通过 PostgreSQL 继续学习。**掌握 SQL、数据建模、唯一约束、事务和迁移，是可以跨数据库迁移的核心能力。** 换到 PostgreSQL 仍需学习驱动、方言差异、连接池和并发行为，不是改一个文件名就完成迁移。

Stack Overflow 2026 调查中，数据库题作答者使用 PostgreSQL 的比例为 **57.9%**，SQLite 为 **42.1%**。这说明两者都有广泛的通用开发使用基础；这些数字不是 Agent 专用数据库的份额，也不是中国招聘岗位占比。[2026 官方技术调查](https://survey.stackoverflow.co/2026/technology)

| 技术 | 在本项目中承担什么职责 | 当前采用情况 | 如何判断是否值得学 |
| --- | --- | --- | --- |
| SQLite | 保存待办、完成状态、消息、角色、提醒及Trace；checkpoint另库保存 | [TaskStore](../lessons/task_store.py) 与正式业务存储共用业务库；第七课显式注入工具、图与HTTP，正式服务已验证重启持久性 | 成熟常用，适合本地个人应用；当前练习继续使用。 |
| PostgreSQL | 服务端业务数据，也可承载独立的 Agent 状态存储 | 上游有 [PostgreSQL 实现](../agent-service-toolkit/src/memory/postgres.py)，尚未运行或接入本项目业务 | 通用主流，建议作为后续求职工程实践补充；官方支持 MVCC、事务隔离等并发机制。[官方说明](https://www.postgresql.org/docs/17/mvcc-intro.html) |
| pgvector | 在 PostgreSQL 中存储向量并进行相似度检索，为论文 RAG 提供候选方案 | 尚未接入；当前采用Qdrant local，后续按部署需求再评估 | 是向量检索扩展，与业务 SQL 可以组合；不能用官方功能说明推导“市场第一”。[官方仓库](https://github.com/pgvector/pgvector) |
| Qdrant local | 保存文档片段向量，按语义相似度返回候选，来源正文仍保存在业务存储中 | [模块三](MODULE_03_DOCUMENT_RAG.md)采用磁盘本地模式；[模块六](MODULE_06_DELIVERY.md)已从真实界面验证BGE512引用问答与重启后检索 | 本项目当前选型，先单进程使用；实现验收更新至2026-10-17，市场资料核查仍10-15，不据此推断市场份额。 |
| Redis | 如出现实际需要，可用于缓存、消息流或其他状态服务 | 本项目尚未使用，不作为当前必装组件 | 通用生态常用，按需求学习；Redis 也支持持久化，不能简单理解为只存临时数据。[官方用途](https://redis.io/docs/latest/develop/get-started/)、[持久化说明](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/) |

这些用途需要分别理解：**业务表**记录“待办是否完成、提醒是否已投递”；**checkpoint**记录图执行到了哪里；**向量索引**帮助检索论文片段。它们可以共享一种数据库产品，但保存的事实与访问方式不同。[LangGraph 持久化说明](https://docs.langchain.com/oss/python/langgraph/persistence)

注意当前上游 [SQLite memory 模块](../agent-service-toolkit/src/memory/sqlite.py)：checkpointer 使用 SQLite，而 `get_sqlite_store()` 实际返回内存 store。不能因为文件名是 sqlite，就认为其中所有长期数据都会在重启后保留。

## “热门”的判断口径

- **通用主流**：有当前使用调查或广泛生态使用证据，例如 Python、FastAPI、PostgreSQL。
- **Agent 领域常用或重点能力**：模型调用、结构化输出、Tool Calling、状态编排、RAG、上下文管理、评测与 Trace。能力重要，具体品牌可以替换。
- **场景成熟或按需选择**：SQLite、Streamlit、IMAP、调度库等，适配项目需要比追逐新名称更重要。
- **生态扩展方向**：例如 MCP。可以有较强行业关注，但不能把“值得了解”写成“每个项目必须使用”。

使用率证据来自 Stack Overflow 2026 调查：Python 为语言题作答者的 **58.0%**；FastAPI 为 Web 技术题作答者的 **18.5%**；Agent 编排题中 LangChain 为 **30.2%**、LangGraph 为 **18.8%**。各题分母分别为 14,099、10,281、4,400，允许多选，不能相加或当作全球市场份额。[语言题](https://survey.stackoverflow.co/2026/technology/data/languages)、[Web 技术题](https://survey.stackoverflow.co/2026/technology/data/web-framework)、[Agent 编排题](https://survey.stackoverflow.co/2026/ai/data/ai-orch)

该调查采集于 2026-06-23 至 2026-08-05，共 30,903 份有效回答，主要经社区和邮件招募，属于自选样本。因此以下热度判断是结合这些资料与项目用途的判断，不是中国求职市场的精确排名。[官方调查方法](https://survey.stackoverflow.co/2026/methodology)

## 核心技术栈、你的学习情况与热度

掌握分解释：**0＝尚无专项验收证据；25＝能解释；50＝能在提示下完成；75＝能独立应用；100＝能排错并迁移。** “0”不代表你完全不会；“50”也不代表学完了整个库。范围沿用现有 13 个模块，不因把一个模块拆成多个热门名称而改变总分。

| 核心模块 | 本项目中的作用与具体技术 | 当前代码或实现状态 | 你的学习与掌握情况 | 掌握分 | 当前热度判断 |
| --- | --- | --- | --- | --- | --- |
| 1. Python 基础、类型与模块 | 函数、字典/列表、类型提示、异常、模块拆分；承载整个应用 | [课程目录](../lessons/README.md)，第六课独立存储模块，第七课通过函数参数使用同一存储 | 已解释对象参数传递，并完成带中文说明的 `/tasks/pending` 函数、调用同一 store 后校对成功；仅记本次跟随实践，独立设计未验 | 50/100 | **通用主流，优先掌握。** 上述 2026 语言调查有当前使用证据。 |
| 2. Git、虚拟环境、依赖与部署 | `.venv` 隔离依赖；Git 记录变更；Docker 与数据卷复现服务 | 当前根[97包锁文件](../requirements.lock.txt)、[一键启动器](../start_assistant.py)、Dockerfile与Compose；实际启动/停止/重启已验，Git仅初始化、容器未运行 | 已使用指定虚拟环境启动脚本；Git暂存/提交、依赖排错与Docker部署尚未专项实践，不因代理初始化计分 | 0/100 | **工程基础。** 是否能交付和复现是重点；不把单一部署工具称为 Agent 专属热门框架。 |
| 3. JSON、Pydantic 与结构化输出 | 用 `BaseModel`、`Field`、JSON Schema 定义并校验类别、摘要和事项 | 教学NoticeAnalysis保留；正式message_models/review_models与event_time已扩展多标签、修订与完整起止，并通过服务验收 | 已按指导新增必填 `summary`；第十一课正确指出日期级“周三”不能带 `at`，补充概念证据；独立实现 schema 和排错未验，保持 50 | 50/100 | **Python/Agent 生态常用。** Pydantic 用于 FastAPI、LangChain 等；官方文档说明生态集成，不能据此计算行业份额。[Pydantic](https://docs.pydantic.dev/latest/) |
| 4. HTTP、FastAPI 与接口校验 | 用路由接收通知、查询待办；Uvicorn 启动，httpx 调用，Pydantic 校验正文 | 教学05/07入口保留；正式service.py新增修订、文件正文分类、重点收件箱与专用通知入口，实际HTTP已验 | 已完成 `/tasks/count` 和 `/tasks/pending` 并反馈校对成功，继续记跟随实践；独立设计、异常处理与并发待深化 | 50/100 | **当前常用 API 框架。** FastAPI 有上述 2026 使用调查证据；HTTP 本身是通用基础。 |
| 5. 模型 API、Prompt 与 LangChain | 调用 DeepSeek；区分 system/user；控制输出；用模型适配器读取真实响应 | [01_model_call.py](../lessons/01_model_call.py)，模型配置在 [03_tool_calling.py](../lessons/03_tool_calling.py) | 已修改 Prompt，完成跟随调用；超时、重试、流式响应、上下文预算待实践 | 50/100 | **Agent 核心方向；LangChain 是当前常用选择。** 上述编排调查有使用证据，不要求只能使用该品牌。 |
| 6. Tool Calling / Function Calling | 模型提出工具名与参数；Python 校验并执行；用 `ToolMessage` 回传实际结果 | [assistant_tools.py](../lessons/assistant_tools.py) 已提供保存、查询和 `complete_task`；[第九课](../lessons/09_README.md) 已验证本轮查询 ID 检查与完成链路 | 第七课 HTTP 路由练习已完成；查询、先查后写及工具结果核对仍无用户专项答题证据，暂不计分 | 0/100 | **Agent 重点能力。** 模型调用与程序执行的边界需要掌握；官方接口文档证明功能支持，不代表使用率排名。[DeepSeek 工具调用](https://api-docs.deepseek.com/guides/tool_calls/) |
| 7. LangGraph 状态与流程 | 用共享状态、节点、条件边组织模型/工具循环；接入持久线程和失败恢复 | [04_langgraph.py](../lessons/04_langgraph.py) 保留教学流程；正式 [graph.py](../agent-service-toolkit/src/student_assistant/graph.py) 已接入异步 SQLite checkpoint 与独立请求上下文 | 已新增 report；正确解释并截图核对同线程history2→4、另一线程独立2，记线程应用75；用户重启恢复、复杂图设计与独立排错未验 | 75/100 | **当前常用的 Agent 编排选择。** 上述调查有使用证据；持久执行等功能见[官方定位](https://docs.langchain.com/oss/python/langgraph/overview)。 |
| 8. SQL 与持久化 | 表、主键、参数绑定、事务、CRUD；先 SQLite，后续建议补 PostgreSQL | task_store/message_store及正式review_store.py、notification_gateway.py；原文/审计修订/队列与双库重启已验 | 已修正表名与状态条件，实际运行确认 `pending_count` 与列表一致，记跟随实践；函数示范由代理实现，独立设计与参数绑定解释未验 | 50/100 | **SQL 是通用核心；SQLite 是成熟场景选型。** PostgreSQL 值得补充服务端工程实践，详见前面的数据库对照。 |
| 9. 异步与数据源接入 | `asyncio`、异步 HTTP、IMAP/MIME、重试与补同步；保证处理模型时仍能接收其他消息 | mail_runtime/UID队列、notification_gateway.py与[Android通知桥](CHANNEL_INTEGRATION.md)已实现；实际QQ2封正文与重连无重复已验，手机/真实附件/到达延迟待验 | 用户正确解释提醒执行不需DeepSeek，仅记职责分工理解；asyncio/to_thread应用、综合操作与QQ完整联调待验，保持0 | 0/100 | **后端工程基础。** 异步是并发 I/O 的常见机制；IMAP 是成熟邮件协议，按真实数据源需要使用，不包装为新兴 Agent 框架。[asyncio](https://docs.python.org/3.13/library/asyncio.html) |
| 10. 时间、时区与提醒调度 | 保留时间原话、确认歧义、转换时间；持久化提醒、重启补执行、完成后停止 | [event_time.py](../agent-service-toolkit/src/student_assistant/event_time.py)、review_store与reminder_runtime已支持完整起止、友好时间编辑、版本重排和取消；原文精度与旧提醒守卫保留 | 已解释原消息基准；本轮正确指出创建时过滤不能覆盖已入队旧计划，补充守卫职责理解，保持50；独立调度、全球时区和设计未验 | 50/100 | **本项目的关键业务工程能力。** 调度库按需求选；当前没有依据把某个品牌写成 Agent 行业必备。 |
| 11. Streamlit 与功能验证 | 展示消息、分类、文件、时间地点标签和筛选；验证真实用户流程 | assistant_dashboard/dashboard_review/dashboard_connections七页HTTP界面与一键启动器；实际AppTest、Edge、编辑/重点已读/手机本机配对通过；原上游完整UI未运行 | [模块六](MODULE_06_DELIVERY.md)综合运行、rerun/form与session_state/checkpoint边界解释待用户反馈，不因代理验收加分 | 0/100 | **适合 Python 数据应用与快速界面。** 本项目沿用工具包选型；不等于企业前端岗位普遍指定 Streamlit。[官方定位](https://docs.streamlit.io/) |
| 12. 文件 RAG、Embedding 与向量检索 | 解析上传文件、按页分块、本地语义Embedding与向量检索，再生成带出处回答 | 正式rag_documents/embeddings/store/runtime/tools及[document_classification.py](../agent-service-toolkit/src/student_assistant/document_classification.py)；真实BGE512/Qdrant/文件正文分类和逐字证据已验 | 用户概念答题通过，细化了chunk切分、Embedding、查询同模型向量化及程序调用Qdrant；记25，独立实现与完整运行/语义评测未验 | 25/100 | **知识库/研究助理的常见方向。** 本项目采用Qdrant local，不据此推断品牌市场份额；原市场核查日期仍为2026-10-15。[检索说明](https://docs.langchain.com/oss/python/deepagents/retrieval) |
| 13. 评测、Trace 与可观测性 | 记录模型、工具、耗时和失败；用标注集评估分类、时间提取、引用及回归 | [本地Trace](../agent-service-toolkit/src/student_assistant/trace_store.py)、[ContextVar/Span](../agent-service-toolkit/src/student_assistant/tracing.py)与[当前10条合成消息评测入口](../evaluate_assistant.py)已实现，见[模块五](MODULE_05_OBSERVABILITY.md)；未接OTEL SDK/LangSmith，非全域RAG评测 | 用户导入与Trace查询已跟练，语义正确性解释、定位错误与修改预期练习待反馈；代码和代理指标不自动计掌握分 | 0/100 | **Agent 工程重点方向。** 下文调查支持对可观测性和评测的实际需求；平台品牌仍按需选择。 |

你已有实践证据的七个模块是：Python、结构化数据、FastAPI、模型 API/Prompt/LangChain、LangGraph、SQL 与持久化、时间；另有RAG概念入门25。切分决定检索片段，Embedding生成向量，查询用同一模型编码，程序调用Qdrant检索后再把证据交给回答模型。LangGraph75仅覆盖当前线程应用，时间50仅覆盖日期运算与消息基准；概念题通过不等于完成库调用、完整运行或独立排错。

模型调用、结构化输出、工具执行、状态/上下文管理、检索与评测构成 Agent 应用的重要能力；Python、接口、数据库和部署为这些能力提供工程基础。当前学习路线覆盖两者，不能只按 Agent 框架名称判断学习价值。

## 需要补充了解的当代 Agent 能力

以下是上表模块的后续细分或可选扩展，**不另外增加模块数量或掌握分**。列入看板不表示已经安装、实现或决定立刻全部接入。

| 能力或技术 | 为什么与本项目有关 | 你的当前情况 | 当前热度与采用建议 |
| --- | --- | --- | --- |
| 上下文工程与记忆 | 选择传给模型的原文、附件证据、历史与规则；控制 token；区分会话状态和长期业务事实 | 主服务已持久保存 thread 消息并验证线程隔离和服务重启后召回；压缩、检索式长期记忆和用户综合练习待验，归入模块5/7/12 | 是 Agent 工程的重要方向；先保证原消息与附件线索可追溯，再按实际需求增加记忆机制。[官方上下文工程说明](https://docs.langchain.com/oss/python/langchain/context-engineering) |
| Checkpoint、恢复与人工介入 | 进程重启后恢复处理；时间歧义或高影响操作需要用户确认 | 主服务已接异步 SQLite saver、失败线程 409 与显式 resume；业务写入不靠 checkpoint 回滚。用户综合练习和人工介入机制待验，归入模块7/8 | LangGraph 明确支持这些能力，值得后续学习；节点能执行不等于已具备恢复机制。[官方持久化说明](https://docs.langchain.com/oss/python/langgraph/persistence) |
| PostgreSQL 与向量检索工程 | 体验服务端连接、并发事务和数据迁移；RAG 阶段可评估 pgvector | 尚未专项实践，归入模块8/12 | 先完成 SQL 基础和统一存储，再补服务端数据库；向量方案按数据量和检索效果选择。 |
| MCP | 用统一协议暴露、发现和调用外部工具；与已有 Tool Calling 分层协作 | 上游 [github_mcp_agent.py](../agent-service-toolkit/src/agents/github_mcp_agent/github_mcp_agent.py) 有示例；本项目尚未接入，归入模块6/9 | **有跨厂商生态支持，值得了解。** 2025-12-09 Linux Foundation 宣布 MCP 为 AAIF 创始项目；Google 随后宣布实际服务集成。先掌握本地工具执行，再考虑 MCP，不推算市场份额。[基金会公告](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation)、[Google 公告](https://cloud.google.com/blog/products/ai-machine-learning/announcing-official-mcp-support-for-google-services) |
| 离线评测与端到端 Trace | 比较更换 Prompt、模型或检索方式后的效果，定位失败发生在哪一步 | 模块五已实现固定合成通知rules/live评测（当前10条，模块五交付时8条）与同库本地Trace，归入模块13；完整RAG语义集尚未建立 | 先学习分母、失败样例与节点定位，再按需接入LangSmith、Langfuse或OTEL；当前并未连接这些平台，品牌熟悉度不能代替评测设计。 |

LangChain 的公开自选调查有 1,340 份回答，采集于 2025-11-18 至 2025-12-02，网页显示发布于 2026-06-12；报告约 89% 使用某种可观测性、52.4% 开展离线评测。这是**厂商调查样本**，用于说明工程关注方向，不能当作全行业普及率；报告中的“研究与数据分析”用例比例也不能当作 RAG 使用率。[报告与方法](https://www.langchain.com/state-of-agent-engineering)

## 历史学习顺序与验收（当前暂停，交付后再安排）

[模块五：Trace与语义评测](MODULE_05_OBSERVABILITY.md)已实现：模块五交付时185项全量测试（含新增32项）及真实CLI/TCP/Trace持久化12项检查、17条断言通过。模块五交付时，真实DeepSeek的`live`模式在8条合成消息上得到类别8/8、事项数8/8、时间字段70/70、附件线索4/4、模型附件数量8/8；`rules`100%只属程序规则回归。数据集不覆盖全部RAG质量，引用合法与语义正确不等价；Trace只存白名单运行元数据，不保存业务原文或凭证。用户Trace查询已跟练，语义评测解释与修改预期练习待反馈，模块13仍0，项目53%及学习400/1300约31%不变。用户已解释提醒无需DeepSeek，只作为职责理解，异步操作未验，当前预计剩余2–4次。

[模块三：研究文件RAG](MODULE_03_DOCUMENT_RAG.md)已交付：115项测试与真实本地BGE512/磁盘Qdrant/DeepSeek、CLI/TCP的12项检查和11条断言见[模块三记录](results/module03_example.json)。原件、引用偏移、unknown、进程重启索引与第六工具search_documents均已验。用户概念题通过后RAG为25，总400/1300约31%；完整运行与独立实现仍待反馈。Qdrant local单进程、文件≤10MB且无OCR；出处检查不保证语义蕴含，主Agent自由回复仅Prompt约束。

[模块四：自动收件与持久提醒](MODULE_04_AUTOMATION.md)已交付，153项全量测试（根tests80＋lessons73；新增store13、mail12、runtime7、API6）通过；[真实验收](results/module04_example.json)的11项检查、11条断言覆盖正式CLI/TCP、真实DeepSeek、实际Windows submitted与重启补发。实际Uvicorn进程30512→2016，停机端口不可访问，临时目录已核验清理；用户已反馈桌面弹窗，同时报告历史邮件误提醒。项目53%=[5,5,19,7,13,0,4]；QQ已有实际登录与部分正文读取证据，真实附件、增量同步与延迟完整联调待验，M5保持0。用户已选桌面通知＋列表，原配置方式见[configure_mail.py](../configure_mail.py)，本轮先运行[无弹窗守卫演示](../reminder_guard_demo.py)核对修复。手工分析与提醒计划之间尚无共同事务内规划outbox，失败需显式补计划。异步题未答，仍0；提醒新代码不提升已有时间50，该批交付时预计剩余3–5次。

本轮实现与版本验收日期为2026-10-16，根依赖新增FastEmbed0.9.0、Qdrant Client1.19.1、pypdf6.20.1，锁文件77包；市场热度来源仍沿用2026-10-15核查，未因安装新库刷新行业热度结论。Embedding首次实际下载约95MB后缓存复用，回答仍调用远程DeepSeek。一个同义查询的0.557/0.287相似度仅是单例探针，完整语义评测待后续批次。

[模块二：消息业务闭环](MODULE_02_MESSAGE_PIPELINE.md)已交付，同一8002服务整合原文来源、精确角色规则、分类/事项证据、时间结果与业务事务，客户端支持JSON导入/查询。旧73项+模块一11项+模块二15项共99项通过；真实DeepSeek和实际客户端/TCP完成11项检查、12条断言，验证来源重复/冲突、完成状态关联、角色动态优先级和业务库重开持久性，见[模块二结果](results/module02_example.json)。新消息工作流按业务状态重试，不使用checkpoint；证据出处不等于语义全对。该批交付时项目40%、学习375/1300约29%、预计4–6次；用户现已核对processed/exists同ID及task_ids稳定等部分跟练，409/新消息/概念题与Tool Calling未验。

[加速模块一：主 Agent 与持久化记忆](MODULE_01_AGENT_MEMORY.md)已交付：`agent-service-toolkit/src/student_assistant/` 接入工具包 `service:app` 的个人助理 profile，根入口 `run_assistant.py`（8002）与 `assistant_client.py`。整合五项工具、thread_id、异步 SQLite checkpoint、每请求工具上下文与显式失败恢复；业务库与状态库分开，checkpoint 不代替业务幂等或回滚。84 项测试及真实 DeepSeek/TCP 验证完成，包括实际服务进程停止再启动后续聊与不同线程隔离、五工具调用；临时进程与双库已清理，见[模块一结果](results/module01_example.json)。该批交付时项目25%、学习350/1300约27%、预计5–7次；用户现已反馈线程应用，工具和重启操作未验；原全量demo、42项运行直接依赖和Streamlit未完整运行/安装。

1. **已完成 SQL 跟随运行与数量核对。** 用户确认 `pending_count` 与未完成列表一致，SQL 记 50/100；独立设计查询、解释参数绑定与异常处理尚待练习。
2. **第十课日期运算与原消息基准练习已完成，第九课先查后写仍待反馈。** 用户正确给出 `at=2026-10-17T15:00:00+08:00` 并解释消息基准，时间记 50；调度、持久提醒、全球时区和独立实现未验。第八、九课 Tool Calling 专项仍为 0。第十一课按[已确认时间规则](TIME_POLICY.md)扩展日期/范围精度及通用解析；旧有限 `/time/normalize` 保留，无模型调用不代表泛化中文时间理解。
   [第十一课](../lessons/11_README.md) 已实现 Pydantic 时间精度判别联合、模型结构化意图与 Python 通用日历计算；新 `/time/interpret` 表达分钟/日期/范围/待确认，旧 `/time/normalize` 保持原结构。9 项新测试与旧 64 项回归共 73 项通过，真实 DeepSeek 直接提取、临时 TCP 的 9 条表达与 CLI 经 HTTP 已验证；合成数据库未变、临时服务已停止，见 [11_example.json](../lessons/results/11_example.json)。用户已正确解释“星期三是一天而非时刻，所以不该带 at”；Pydantic 仍为 50，独立实现未验。预设意图的 `--demo` 不能证明模型理解，一般语义仍依赖模型；该课交付时13模块为350分、约27%。
3. **完成自动接入与提醒实证。** 按模块四观察提醒列表和Windows提交状态，补验QQ真实附件、增量去重与延迟；解释`to_thread`对阻塞收件的作用。异步应用与QQ完整闭环未验前不加学习或M5分。
4. **补齐检索与工程交付。** 处理真实论文并验证 RAG 引用；制作评测集、诊断失败、完成部署。PostgreSQL 服务端实践和 MCP 作为对应阶段的补充，不同时堆叠所有组件。

此前执行方式曾调整为[六个大交付批次](ACCELERATED_ROADMAP.md)，合并讲解与练习；这些历史课程安排当前暂停，不再更新学习对话次数。多平台全量接入仍是目标，按实际能力另验；完成课程不代表 13 模块全为 100 分，本轮路线调整不新增功能或分数。

本路线面向 Agent 应用工程。模型训练、微调、强化学习训练与大规模基础设施属于后续岗位分支；当前项目完成后应能展示和解释实际代码、运行结果、失败案例与取舍。具体职位要求继续以目标岗位为准，可参考 [求职能力与项目代码地图](JOB_SKILL_MAP.md)。

第七课默认数据库为 `local-data/assistant.sqlite3`、端口 8001，新增 `GET /tasks/pending/count`；第五课默认 JSON 和 8000 入口保留，旧客户端通过 `--base-url` 选择服务。仅在显式 `--import-json` 时迁移旧记录，默认不自动导入。业务待办持久化与图执行状态持久化分开验收。

第七课实现验收时新增 7 项集成测试与第三至第六课 29 项回归测试，共 36 项通过（模拟模型、真实图、临时 SQLite）。另用真实 DeepSeek、随机本地 TCP 端口与临时数据库验证两条 `created`、`model → tools → report → model`、总量/未完成数量均为 2；另一个 CLI 进程完成一项后未完成为 1、总量仍为 2，空白输入为 422。该次代理临时验证关闭服务后重开数据库记录仍在、旧 JSON SHA256 未变；当时未验证真实 TCP 服务重启，代理未创建默认数据库，不据此判断用户后来的文件状态。证据见 [07_example.json](../lessons/results/07_example.json)。这些是项目验收证据，未因此增加学习分；验收当时为 250/13、约 19%，用户解释概念后曾为 275/13、约 21%，完成新增函数练习后当时达到 300/13、约 23%。

第八课已实现 `list_tasks` 工具的 `pending`（默认）/`completed`/`all` 枚举、`QueryToolResult` 与 `tool_names`。工具名取自实际 `ToolMessage.name`，含失败请求，需结合结果状态判断成功；Prompt 引导纯查询选读工具，不是严格只读权限保证。复用第七课 8001 服务和 `05_client.py --message`，不新增服务；它是已有待办查询的工具层扩展，该课交付时项目为 21%，Tool Calling 未加分。

第八课新增 8 项测试与旧课 36 项回归，共 44 项通过（模拟模型、真实图、进程内 HTTP、临时 SQLite）。真实 DeepSeek 另经随机 TCP 端口的临时服务查询程序预置的 1 项 pending、1 项 completed，分别得到 `list_tasks`、`queried`、对应筛选与数量 1，ID 与库一致；用户新增 `/tasks/pending` 也返回 200 且与工具一致。两次查询后记录和完成状态未变，临时服务已停止，记录见 [08_example.json](../lessons/results/08_example.json)。这些代理验证不增加用户掌握分，该课交付时为 300/13、约 23%。

第九课于 2026-10-16 完成自然语言标完成工具：一次工具表创建的闭包保存成功查询返回的 ID，拒绝未查 ID 的写入；当前每请求重建图/工具表才实现隔离，以后缓存图需另处理生命周期。同批工具请求顺序执行，不强制两轮模型调用。工具重复完成的幂等性已离线验证；ID 检查不能保证语义目标正确，Prompt 歧义澄清也不是程序保证。HTTP 工具结果支持 `completed`，SQLite API 图上限为 12 步、旧 JSON 为 8，继续复用第七课服务与客户端。

第九课新增 8 项离线测试与旧课 44 项回归，共 52 项通过。真实 DeepSeek 经临时 TCP 两次返回 200：首次实际调用 `list_tasks → complete_task`、执行 7 节点，仅目标从 pending 变 completed、另一项仍 pending；第二次只调用 `list_tasks`，读到已完成后直接回复、未再次调用完成工具或修改数据，pending 仍为 1。两次待办列表与数量接口一致；记录见 [09_example.json](../lessons/results/09_example.json)，临时进程已停止，只使用合成数据，未修改用户数据库。该课交付时项目为 21%、学习约 23%、Tool Calling 0 分，未因此加分；用户专项练习和独立实现待验，完整上游和角色/来源/附件/提醒尚未实现。

第十课于 2026-10-16 实现 `time_parser.py` 与 `10_time_normalization.py` CLI，既有服务新增只读 `POST /time/normalize`。必须提供带 ISO 偏移的原消息 `reference_time`，保留原文与参考时间偏移，不取服务器当前时间；固定 UTC+08，不调用模型或新增 `zoneinfo` 依赖。仅支持今天/明天/后天的 24 小时时间或上午/下午中文整点 1—11，以及完整年月日时分；周五前、合法日期缺钟点、区间、否定混合等待确认，不补 23:59。非法日期时分为 422，完整日期即使缺钟点也先检查日期是否合法。

时间输出包含 `timezone/status/time_kind/at/confirmation_question` 等字段，尚不写入待办或创建提醒。第十课 12 项专测与旧 52 项回归共 64 项通过；真实第七课 TCP 临时服务在模型配置置空时验证 4 项合法请求 200（含 UTC 跨日、待确认、deadline）与 4 项非法请求 422（25:00、无钟点非法日期、无 offset、时区转换溢出）。CLI 经 HTTP 结果与直接函数一致，原 pending 接口保留、合成数据库前后不变，临时服务已停止；证据见 [10_example.json](../lessons/results/10_example.json)。

第十课独立时间预览验收后，当时 M4 为 2/20、项目为 23%；余 18% 用于持久化关联、时段标签与可恢复提醒。该课 API 交付时学习为 300/13、约 23%，时间与 Tool Calling 均为 0；随后日期运算与原消息基准练习后，时间为 50、学习为 350/1300、约 27%，Tool Calling 为 0。此段保留历史证据；当时模块四持久提醒验收后项目53%，市场资料核查仍为2026-10-15。

用户已确定裸“周三/周四”按原消息所在自然周（周一至周日）解释，即使已过去也不自动跳下周；“周三前”同样确定日期，未配置钟点仍不补 23:59。日期与周/月/年范围不必强行变成精确时刻。第十课 `/time/normalize` 保留原有限范围，第十一课以模型候选加程序日历计算扩展；通用方案与边界见[时间解释规则](TIME_POLICY.md)。

## 更新规则

- 用户实际运行、修改、解释或排错后，记录证据，再同步这里与学习记录的掌握分。
- 代理写好代码、依赖安装成功、离线测试通过，只增加对应的项目实现证据，不自动增加用户掌握分。
- “源码已有”“本机已运行”“用户已实践”“已能独立完成”分开记录。
- 热度判断注明核查日期；需要比较当前岗位或新框架时重新核实，保留调查分母和适用范围。

模块五交付时验收记录（历史8条，2026-10-16）：[真实记录](results/module05_example.json)包含12项检查、17条断言；实际Uvicorn进程12700停止后由新进程24528恢复同一Trace及Span，坏PDF定位到document.extract，临时服务和评测数据库已核验清理。模块五交付时185项测试全部通过；项目53%、学习400/1300约31%，当时主线预计2–4次。真实QQ实际登录与部分正文读取已有证据，真实附件、增量同步与延迟完整验收待补。
