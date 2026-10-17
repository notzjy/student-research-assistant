# 完整项目交付后的技术栈学习地图

更新：2026-10-17。用户已明确暂停边开发边教学，先完成项目。当前不安排课程、不提问、不布置作业；本文件保存恢复学习时的路线，不作为使用项目的前置条件。

技术学习：`██████░░░░░░░░░░░░░░` **31%（400/1300）**。代码由代理完成和验收不会自动增加个人掌握分。预计还需几次学习对话：**目前暂停，不估计次数**；恢复后结合完整项目和实际反馈按模块安排。

## 从完整链路理解项目

恢复学习后先围绕已经可运行的产品观察整条链路：手机通知、QQ邮件或手工导入 → 原文与来源持久化 → 模型分类与结构化提取 → 时间计算 → 事项与提醒 → 文件正文检索 → 界面与Trace。再学习各环节的实现和失败恢复，避免技术名词与实际代码脱节。

每个模块将使用完整项目里的真实文件和可运行路径讲解。先演示结果、解释核心代码和技术用途，再在恢复学习时安排应用与排错；当前不要求用户先答题才能运行交付项目。

## 后续六个学习模块

| 顺序 | 核心技术与项目位置 | 恢复学习后的掌握目标 |
|---|---|---|
| 1. 模型调用与可信结构 | [模型适配](../agent-service-toolkit/src/student_assistant/bridge.py)、[消息协议](../agent-service-toolkit/src/student_assistant/message_models.py)、[提取工作流](../agent-service-toolkit/src/student_assistant/message_workflow.py)；Python、Prompt、LangChain、Pydantic | 看清模型到底收到哪些消息，如何约束输出、验证字段和逐字证据，为什么结构正确仍可能语义错误 |
| 2. Agent工具、图和上下文 | [工具注册](../agent-service-toolkit/src/student_assistant/tools.py)、[LangGraph图](../agent-service-toolkit/src/student_assistant/graph.py)、[运行引擎](../agent-service-toolkit/src/student_assistant/runtime.py)；Tool Calling、状态合并、thread_id、checkpoint | 区分模型选择工具与程序执行工具，解释查询ID保护、线程复用、历史和持久业务事实，理解失败后继续执行的边界 |
| 3. 接口、数据库与人工纠正 | [FastAPI服务](../agent-service-toolkit/src/student_assistant/service.py)、[消息存储](../agent-service-toolkit/src/student_assistant/message_store.py)、[修订存储](../agent-service-toolkit/src/student_assistant/review_store.py)、[HTTP适配器](../dashboard_api.py)；HTTP、SQL、事务、幂等与版本 | 能跟踪一次界面操作到数据库，解释来源去重、事务回滚、修订冲突、原文保留和用户确认 |
| 4. RAG与文件正文分类 | [解析与切片](../agent-service-toolkit/src/student_assistant/rag_documents.py)、[Embedding](../agent-service-toolkit/src/student_assistant/rag_embeddings.py)、[Qdrant存储](../agent-service-toolkit/src/student_assistant/rag_store.py)、[问答](../agent-service-toolkit/src/student_assistant/rag_runtime.py)、[正文分类](../agent-service-toolkit/src/student_assistant/document_classification.py) | 区分文件原件、片段、向量、检索候选和回答；理解引用、召回和语义支持的不同含义，以及正文节选覆盖范围 |
| 5. 异步接入、时间和可靠提醒 | [邮件运行器](../agent-service-toolkit/src/student_assistant/mail_runtime.py)、[通知队列](../agent-service-toolkit/src/student_assistant/notification_gateway.py)、[正式时间解析](../agent-service-toolkit/src/student_assistant/event_time.py)、[提醒运行器](../agent-service-toolkit/src/student_assistant/reminder_runtime.py)、[重点收件箱](../agent-service-toolkit/src/student_assistant/attention.py)；asyncio、to_thread、IMAP/MIME、重试、租约 | 能解释接收与分析分离、离线补传、原消息时间与当前决策时间、投递前校验、已读与submitted的区别，知道哪些平台字段不能保证完整 |
| 6. 界面、质量与本地交付 | [Streamlit入口](../assistant_dashboard.py)、[启动器](../start_assistant.py)、[评测](../evaluate_assistant.py)、[Trace](../agent-service-toolkit/src/student_assistant/tracing.py)、[Docker配置](../compose.yaml)；session_state、rerun、测试、Git和部署 | 能定位重复请求与上下文问题，读懂测试和评测分母，追踪失败节点，独立运行并讲清本地部署与容器配置的验证范围 |

安卓桥的Java、Android通知API和权限是数据源扩展部分，放在第5模块结合实际设备学习，不要求先成为安卓开发者才能掌握Python Agent核心链路。PostgreSQL、MCP、外部可观测性平台和公网部署可作为后续求职扩展；未接入的技术不计为当前项目已实现能力。

## 现有13项掌握记录保持

以下只保留已经有证据的历史分数；详细证据见[学习记录](LEARNING_PROGRESS.md)与[技术栈看板](AGENT_TECH_STACK.md)。上述六个模块整合这13项，不另设新总分。

| 原技术项 | 当前分数 | 后续归属 |
|---|---:|---|
| Python项目基础 | 50/100 | 1、3 |
| Git、环境与部署 | 0/100 | 6 |
| Pydantic与结构化数据 | 50/100 | 1、3 |
| FastAPI与HTTP | 50/100 | 3 |
| 模型API、Prompt与LangChain | 50/100 | 1 |
| Tool Calling | 0/100 | 2 |
| LangGraph与状态/上下文 | 75/100 | 2 |
| SQL与持久化 | 50/100 | 3 |
| 异步与数据源接入 | 0/100 | 5 |
| 时间、时区与提醒 | 50/100 | 5 |
| Streamlit与功能验证 | 0/100 | 6 |
| RAG、Embedding与向量检索 | 25/100 | 4 |
| 评测、Trace与可观测性 | 0/100 | 6 |

恢复后以实际运行、迁移运用、独立修改和排错作为掌握证据。阅读介绍、复制运行、代理完成测试、通过一个概念回答，都不直接等同于完整掌握某项技术。

当前使用入口和交付边界统一见[DELIVERY.md](DELIVERY.md)。历史课程和讲义继续保留，作为将来的示例材料；最新“先交付、后学习”要求覆盖其中按教学节奏推进的安排。
