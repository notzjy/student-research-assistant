# 求职能力与项目代码地图

## 本轮最终交付与当前权威状态（2026-10-17）

项目进度：`█████████████████░░░` **83%**，阶段 **[10,10,20,15,20,0,8]**。
技术学习：`██████░░░░░░░░░░░░░░` **31%（400/1300）**，分数冻结。

用户已明确要求**先停止学习，直接交付项目，后续再围绕完整项目安排技术栈学习**。当前不布置练习、不以答题阻塞交付，也不再估算学习对话次数；既有学习证据保留，代理实现和验收不增加个人掌握分。

[本轮交付证据](results/final_delivery.json)的 **29项检查全部通过**：实际启动器管理后端与Streamlit、真实DeepSeek/BGE512/Qdrant、人工分类与多标签、消息修订、文件正文分类、完整起止时间、编辑重排提醒、重点收件箱、无线通知入口、APK下载、Edge截图和真实重启后的双库/索引/修订/通知队列持久性。新增事项“已取消”与“已完成”分离，普通粘贴表单可直接收件；实际后端进程16444→25688验证重启。专用临时数据与进程已清理。最终回归 **337项通过（根tests264＋lessons73）**，已逐项核查最新日志[根测试](../local-data/final-verified-tests.log)的`Ran 264 tests / OK`与[教学脚本测试](../local-data/final-verified-lessons.log)的`Ran 73 tests / OK`，并核对[汇总](../local-data/final_verified_tests.json)；此前326项及旧240项失败日志均不是当前最终结论。

[QQ实际只读检查](results/qq_delivery_check.json)取得2封邮件正文（1015、2034字符），重新连接再同步0封重复；未取得真实附件，未测新邮件到达至提醒的延迟，故不满足原M5完整闭环规则，仍为0/15。[Android通知桥](CHANNEL_INTEGRATION.md)APK已编译验签、提供无线下载与配对；当前设备数0，尚未手机安装、授权或真实通知验收，不能将HTTP合成输入计为真机接入。Git已进行公开发布整理，当前提交与远端状态见[发布记录](PUBLICATION.md)；Docker仍未实际构建运行。

以下60%/218项、53%/198项以及原对话次数均为对应历史节点，不再作为当前状态或当前学习安排。市场与岗位热度资料仍以**2026-10-15**核查范围为准。

## 历史：模块六交付节点（2026-10-17，项目60%）

项目进度：`████████████░░░░░░░░` **60%**，阶段[5,7,19,7,16,0,6]。技术栈学习：`██████░░░░░░░░░░░░░░` **31%**，400/1300；预计主线还需约 **1–3次有效提问或反馈**，用于QQ真实附件、增量同步与延迟完整联调、本轮界面练习及补缺；原始多平台接入目标尚未完成。

[模块六](MODULE_06_DELIVERY.md)新增可演示的五页Streamlit客户端。代理完成[16项真实检查](results/module06_example.json)：实际HTTP、AppTest、DeepSeek、BGE512/Qdrant、独立Edge截图和实际进程重启持久性；[截图](results/module06_dashboard.png)已目视核对。Uvicorn进程28216→26300重启后双库、索引和Trace仍在；临时验收数据已清理，218项回归（根145＋lessons73）及96包pip check通过。

可展示的新增工程证据是HTTP界面、提交门控、thread_id复用、完成取消提醒与持久化；个人独立能力仍需用户综合练习。Git仅init main，无暂存/提交/远程，Docker无CLI、仅静态配置，不能在简历中写成已容器部署。用户关于旧队列的回答只补充守卫职责理解，时间50、Streamlit/Git/评测0及总400分保持。岗位和热度资料核查仍为2026-10-15，未重新声称更新。以下2026-10-16的53%、198项和2–4次为历史节点。

## 历史：旧邮件误提醒修复（2026-10-16）

- 已确认QQ邮箱实际登录与部分正文读取；用户也已看到Windows弹窗，但历史邮件被误建提醒。真实附件、增量同步与延迟完整验收待补，M5完整闭环暂不计分。
- [本轮排错讲义](LESSON_REMINDER_GUARD.md)与[离线演示](../reminder_guard_demo.py)区分原消息时间和当前决策时间：2021年消息的“四天后”仍算2021年，不应据此在今天新建自动提醒。新自动计划跳过过期事项和已过提醒钟点；旧自动/legacy队列在投递前检查来源时间，显式manual计划保留补发。升级仅添加origin标记，不批量删除用户消息或事项。
- Prompt区分无需操作的商业自动续费告知与明确取消请求；不是按“续费”关键词一律封禁。固定语义评测当前为10条，新增两条对照样例；历史8条与70项时间字段结果仍是模块五交付时的记录，不能直接套到当前10条。
- 用户已完成消息导入和Trace查询跟练；“执行完成不等于语义正确”的解释与评测修改练习仍待反馈，评测掌握仍0。项目53%、学习400/1300约31%、主线预计2–4次保持；本轮198项全量测试（根tests125＋lessons73）通过；真实DeepSeek的10条live得到分类10/10、事项数10/10、时间字段81/81、附件线索4/4、模型附件数量10/10，rules同分只属工程回归。结果见[本轮修复验收](results/reminder_guard_fix.json)。

如果要同时查看技术选型、当前热度与你的掌握情况，请先看 [智能体核心技术栈与学习看板](AGENT_TECH_STACK.md)。

课程与实现状态更新：2026-10-17；岗位资料核查：2026-10-15。学习目标是能独立开发、解释和交付一个 LLM / Agent 应用；用个人研究与日程助理积累可展示的工程证据。

## 官方岗位样本

| 岗位与来源 | 页面信息 | 与本项目有关的要求 |
| --- | --- | --- |
| [腾讯音乐：AI 全栈工程师](https://join.tencentmusic.com/campus/post-details/?id=15037&refer_code=) | 应届生；深圳、北京、上海、广州；未展示发布日期 | 一种后端语言及前端框架；LLM 基础、能力边界与部署；RAG；Tool Use、Planning、Memory；AI 编码与结果验证；工程质量。 |
| [腾讯：智能体套件全栈开发工程师—CodeBuddy / WorkBuddy](https://careers.tencent.com/zh-cn/jobdesc.html?postId=2074756587444158464) | 深圳；3 年以上；2026-10-08 更新 | 系统设计、前后端、模型服务接口、容器化、CI/CD、监控与稳定性；Agent、Prompt、RAG、工具集成为优先项。 |
| [腾讯：混元 Agent 强化学习框架工程师](https://careers.tencent.com/jobdesc.html?postId=2061654749727600640) | 深圳、北京、上海；1 年以上；2026-09-30 更新 | Python、Asyncio、并发；模型调用、工具调用、上下文、执行状态、Trace、评测；模块化与测试；容器及 Kubernetes。 |

这是三个具体样本，能说明应用开发与系统工程能力的重要性，不能据此认定所有公司要求相同。完成项目形成能力证据，不等于保证录用。

以下 FastAPI、Pydantic、LangChain、LangGraph、SQLite 和具体检索方案是本项目选型，不是三个岗位共同指定的品牌要求。混元岗位还涉及训练流程；强化学习训练、Kubernetes、复杂前端与多租户列为后续岗位分支，先完成应用开发主线。

## 先分清代码位置与状态

- 本轮工程交付新增`start_assistant.py`、`启动助理.cmd`、`dashboard_review.py`、`dashboard_connections.py`、正式`review_store/event_time/document_classification/attention/notification_gateway`与`android-notification-bridge/`。人工纠正、多标签、原文修订、正文分类、完整时段/编辑重排和重点收件箱已由实际服务验证；APK可无线下载配对，但设备数0，不能写成手机已实测。29项实际联调、337项回归通过；当前项目83%，学习400/1300约31%冻结。
- 历史模块六[交付记录](MODULE_06_DELIVERY.md)已交付根`assistant_dashboard.py/dashboard_api.py/dashboard_views.py`，界面只通过HTTP调用原8002服务；五页展示重点消息、分类、精度标签、研究问答、提醒角色、Trace及持久线程。表单提交门控和`session_state`用于页面交互，业务SQLite/checkpoint仍在后端。218项回归与16项真实检查通过，当时项目60%，学习400/1300约31%；对应综合练习现已暂停。根Git仅初始化main，Dockerfile/Compose及平台marker静态检查通过，容器运行未验。
- [模块五](MODULE_05_OBSERVABILITY.md)已实现正式`trace_store/tracing/trace_middleware/evaluation`与根[evaluate_assistant.py](../evaluate_assistant.py)：Trace只存节点、父子ID、耗时和安全错误类型，响应头`X-Trace-ID`关联只读查询；模块五交付时185项全量测试（含新增32项）通过，真实CLI/TCP及Trace持久化12项检查、17条断言通过。模块五交付时，真实live在8条合成通知上得到类别8/8、事项数8/8、时间字段70/70、附件线索4/4、模型附件数量8/8；rules只属程序回归，不是模型指标。未接OTEL SDK/LangSmith，不代表全域RAG已评测。项目53%、学习400/1300约31%不变，用户职责分工理解不等于asyncio实践，预计剩余2–4次完成QQ本机联调与可演示交付。
- [模块四](MODULE_04_AUTOMATION.md)已交付：正式`mail_ingest/mail_store/mail_runtime`实现只读IMAP/MIME、EML和UID队列，`reminder_store/reminder_runtime/notification`实现版本化计划、租约、退避与完成取消。153项测试（根tests80＋lessons73；新增store13、mail12、runtime7、API6）全部通过；[真实记录](results/module04_example.json)的11项检查、11条断言覆盖正式CLI/TCP、真实DeepSeek提取、实际Windows submitted与重启补发。实际Uvicorn进程30512→2016，停机端口不可访问，临时目录已核验清理；用户已反馈桌面弹窗，同时报告历史邮件误提醒。用户已选Windows桌面＋列表，QQ已有实际登录与部分正文读取证据，原本机配置入口为[configure_mail.py](../configure_mail.py)，本轮先用[无弹窗守卫演示](../reminder_guard_demo.py)核对修复；模拟邮箱不写成真实接入成果。项目53%=[5,5,19,7,13,0,4]，M5仍0，学习400/1300约31%、异步0；该批交付时预计3–5次，当前估计见顶部。
- [模块三](MODULE_03_DOCUMENT_RAG.md)已交付真实文件上传/解析、语义向量检索和引用回答，复用8002服务。正式`rag_documents/rag_embeddings/rag_store/rag_runtime/rag_tools`使用pypdf、FastEmbed/BGE-small-zh-v1.5中文512维与Qdrant local，主Agent新增第六工具search_documents。115项测试与真实CLI/TCP的12项检查、11条断言见[模块三结果](results/module03_example.json)；原件哈希、引用偏移与实际进程重启索引均已验。合成md不是真实论文，附件分类仍来自message_text。本批交付时项目45%、学习375/1300约29%；用户随后RAG概念题通过记25，总400/1300约31%，独立实现与完整运行/评测待验。
- [模块二](MODULE_02_MESSAGE_PIPELINE.md)已交付正式消息协议/存储/工作流及同一8002服务的消息导入、角色配置与追溯。99项测试通过，真实DeepSeek与实际客户端/TCP完成11项检查、12条断言，验证来源幂等/冲突、角色与分类、证据和时间关联、完成状态同步及重开持久性，见[模块二结果](results/module02_example.json)。示例仅声明人工来源和演示身份；该批交付时项目40%、学习375/1300约29%、预计4–6次；用户现仅核对processed/exists同ID及task_ids稳定等部分跟练，完整练习尚未完成。用户thread反馈只支持LangGraph当前应用75，no_tools不证明Tool Calling，用户重启无证据。
- [加速模块一](MODULE_01_AGENT_MEMORY.md)已将现有能力接入正式 `agent-service-toolkit/src/student_assistant/`，由工具包 `service:app` 的个人助理 `student` profile 分派；根 `run_assistant.py` 默认 8002、`assistant_client.py` 为客户端。五项工具与 thread_id/checkpoint 已验，84 项测试通过；真实 DeepSeek/TCP 验证实际服务重启后会话召回、线程隔离与五工具调用，临时服务和双库已清理，见[模块一结果](results/module01_example.json)。原全量 demo、Streamlit 与 42 项运行直接依赖尚未完整运行/安装；用户线程复用/隔离已反馈，工具和重启操作未验。
- 根目录 `.venv/` 由教学环境扩展，现由个人助理 profile 复用；根 `requirements.txt` 引入教学基础依赖及 SQLite checkpoint/aiosqlite。这不表示上游全部依赖、demo 或原界面已经安装运行。
- 首个教学示例是 `lessons/01_model_call.py`：通过 LangChain `ChatOpenAI` 调用兼容接口，从环境变量读取地址、模型和密钥，学习消息、响应与错误处理。真实调用是否通过以运行记录为准。
- 已有第二课 `lessons/02_structured_output.py` 与第三课 `lessons/03_tool_calling.py`，均已真实运行示例；第三课默认 JSON 待办工具保留，本轮通过显式工具表和工具工厂接入可注入的存储依赖。
- 已有第四课 `lessons/04_langgraph.py`，用状态与条件边编排同一模型/工具流程，默认路径已真实验证；还未注册进完整工具包服务。
- 已有第五课服务 `lessons/05_fastapi.py` 和客户端 `lessons/05_client.py`，HTTP链路与真实模型已验证；仍属本机教学服务。
- 已有第六课 `lessons/task_store.py` 与 `lessons/06_sqlite.py`，独立验证 SQLite 待办保存、查询、完成与旧 JSON 导入；用户已运行并确认 `pending_count` 与未完成列表一致，SQL 记跟随实践 50/100，独立设计未验。
- [第七课](../lessons/07_README.md) 已实现 `lessons/assistant_tools.py` 与 `lessons/07_sqlite_api.py`：通过工厂闭包和显式 `store` 参数，使工具、图中 report 与 HTTP 共用 `TaskStore`，集成验收已通过。这是依赖注入和模块拆分，不使用 FastAPI `Depends`，也未增加 checkpoint。
- 第七课默认 `local-data/assistant.sqlite3`、端口 8001；第五课默认 JSON 和 8000 保留，`05_client.py --base-url` 可选择服务。仅显式 `--import-json` 迁移旧记录；完整上游尚未运行，来源、角色、附件与提醒仍待实现。
- 第七课 7 项集成测试与旧课 29 项回归测试全部通过；另用真实 DeepSeek、随机 TCP 端口和临时 SQLite 验证两项待办保存、同库总量/未完成统计及跨 CLI 进程更新。当时代理临时验证关闭服务后重开数据库记录仍在、旧 JSON 未变；当时没有执行真实 TCP 服务重启，也未创建默认数据库，不据此判断用户后来实际文件的状态。证据为 `lessons/results/07_example.json`，该课交付时项目为 21%。
- 用户已理解存储对象参数传递，并在第七课新增带中文说明的 `GET /tasks/pending`、调用同一存储后校对成功。Python 模块记本次跟随实践 50/100，独立设计未验；该阶段 13 模块合计 300、学习约 23%，FastAPI 和 SQL 各保持 50。
- [第八课](../lessons/08_README.md) 已增加 Agent `list_tasks` 读工具（`pending` 默认、`completed`、`all`）、`QueryToolResult` 和实际执行工具名 `tool_names`。新增 8 项测试与旧课 36 项回归共 44 项通过；真实 DeepSeek 经临时 TCP 服务查询程序预置的 pending/completed 各 1 项，工具名、筛选、数量与库内 ID 一致，两次查询后数据和状态未变，用户新增路由也与工具一致。证据见 [08_example.json](../lessons/results/08_example.json)。
- 第八课复用第七课 8001 服务和 `05_client.py --message`，临时验证服务已停止；Prompt 引导与本次查询行为不构成严格只读权限保证，Tool Calling 待用户专项实践反馈后再计分，该课交付时学习约 23%。
- [第九课](../lessons/09_README.md) 已实现 `complete_task(task_id)`，复用 `TaskStore.mark_completed()`；一次工具表创建的闭包只接受成功查询返回的 ID，当前每请求重建图/工具表实现隔离，未来缓存图需另处理。工具同批顺序执行，允许同轮先查后完成，不强制两轮模型调用；重复完成幂等性已离线验证。HTTP 结果支持 `completed`，SQLite API 图上限为 12 步、旧 JSON 为 8；复用原服务/客户端，不新增第九课服务。ID 检查与歧义澄清 Prompt 均不保证语义目标选择正确。
- 第九课新增 8 项离线测试与旧课 44 项回归，共 52 项通过。真实 DeepSeek 经临时 TCP 首次调用 `list_tasks → complete_task`、执行 7 节点，仅目标变 completed；第二次仅调用 `list_tasks`，读到已完成后直接回复，无额外修改，pending 保持 1。两次 HTTP 200、待办列表与数量接口一致；临时进程已停止，只使用合成数据，未修改用户数据库。证据见 [09_example.json](../lessons/results/09_example.json)，项目与学习分均不增加，专项用户练习待反馈。
- 当前[第十课](../lessons/10_README.md) 已实现 `time_parser.py`、`10_time_normalization.py` CLI 和既有服务的只读 `POST /time/normalize`；用 `datetime/timedelta/timezone` 与 Pydantic，在显式原消息 `reference_time`（ISO 带偏移）下预览有限表达、保留原文，固定 UTC+08，不用服务器当前时间、不调用模型或新增时区依赖。
- 第十课支持今天/明天/后天搭配 24 小时时间或上午/下午中文整点 1—11、完整年月日时分；周五前、合法日期缺钟点、区间、否定混合等待确认，不补 23:59。非法日期时分为 422，完整日期即使缺钟点也检查合法性。输出保留参考偏移及 `status/time_kind/at/confirmation_question`，不等于泛化中文理解、持久化时间关联或提醒；原工具和表不变。
- 第十课 12 项专测与旧 52 项回归共 64 项通过；模型配置置空的第七课 TCP 临时服务验证 4 项合法 200 与 4 项非法 422，覆盖 UTC 跨日、歧义待确认、deadline、非法日期时分、无 offset 与转换溢出。CLI 经 HTTP 与直接函数一致，原 pending 接口保留、合成库前后不变，临时服务已停止；记录见 [10_example.json](../lessons/results/10_example.json)。独立 API 验收使 M4 为 2/20、项目为 23%；该课交付时学习为 300/13 约 23%，时间与 Tool Calling 均为 0。
- 用户随后正确给出 `at=2026-10-17T15:00:00+08:00` 并解释原消息基准，时间模块记50，仅限日期运算与消息基准，用户调度操作、全球时区和独立设计未验。当时合计350/1300约27%、项目23%；模块一25%、模块二40%、模块三45%为历史阶段。当前RAG概念题25后总400/1300约31%，模块四持久提醒验收后项目53%；Tool Calling与异步仍0。
- [第十一课](../lessons/11_README.md) 已实现 `time_semantics.py`、`11_time_precision.py` 与只读 `/time/interpret`：Pydantic 判别联合区分分钟、日期、日期范围和待确认，模型输出意图、Python 执行日历计算与精确裸星期政策；一般语义仍依赖模型。9 项新测试加旧 64 项共 73 项通过；真实 DeepSeek 直接提取、临时 TCP 的 9 条表达与 CLI 经 HTTP 已核对，旧接口保留、合成库未变、临时服务已停止，见 [11_example.json](../lessons/results/11_example.json)。离线 `--demo` 使用预设意图；用户已正确核对日期精度字段，独立实现待验，原分数保持。
- 下表“已有”指已存在的上游源码，可阅读和复用；不表示已经验证它适合本项目。“计划”指后续业务文件，尚不能当作已实现功能。
- 下表工具包内的相对路径均以 `agent-service-toolkit/` 为起点；`lessons/` 位于工作区根目录。

| 已有入口 | 现在可以读懂什么 |
| --- | --- |
| `src/core/llm.py` | `get_model()` 如何根据配置构造模型，包括 `ChatOpenAI` 兼容接口。 |
| `src/agents/research_assistant.py` | 消息、`bind_tools()`、`ToolNode`、`StateGraph` 如何组成执行流程。 |
| `src/agents/agents.py` | 已编译 Agent 如何注册并通过标识查找。 |
| `src/service/service.py`、`src/schema/schema.py` | FastAPI 服务、请求模型、流式响应与可选 Trace 回调。 |
| `src/agents/rag_assistant.py`、`src/agents/tools.py` | 检索作为工具接入 Agent 的示例；需要继续检查检索数据和实现。 |
| `src/memory/sqlite.py` | Agent 执行状态存储；业务联系人、消息和提醒另建数据表。 |
| `docker/Dockerfile.service`、`compose.yaml` | 工具包如何容器化运行；后续需要实际构建和验证。 |

## 交付后学习参考：技术、代码与能力证据（当前暂停）

| 顺序与技术 | 在个人助理里亲手完成的任务 | 对应代码位置 | 完成证据 |
| --- | --- | --- | --- |
| 1. 模型 API 与 Prompt | 把一条原始通知交给模型，提取摘要；传入消息时间和时区；能解释 system / user 消息及错误。 | 首课 `lessons/01_model_call.py`；已有 `src/core/llm.py` | 独立运行样例，解释输入输出；能诊断缺配置、鉴权失败和超时。 |
| 2. LangChain 结构化输出 | 定义类别、多标签、原文证据和完整起止，区分结构合法与语义正确 | 教学02保留；正式message_models.py/review_models.py/event_time.py已交付 | 结构校验、人工纠正与真实服务验收已有证据；用户schema独立设计仍待恢复学习后验证。 |
| 3. Tool Calling | 保存、查询、完成待办并回传真实结果 | 教学assistant_tools；正式tools.py/rag_tools.py整合六工具，查询ID保护保留 | 工具实际调用已由代理验证；个人专项掌握仍0，按钮明确动作无需再次让模型选工具。 |
| 4. LangGraph | 编排模型/工具循环，持久保存线程，显式恢复失败执行。 | 教学 `lessons/04_langgraph.py`；正式 `src/student_assistant/graph.py`、`runtime.py` 已接异步 saver；工具包 `service:app` 分派个人助理 profile | 实际服务进程停止再启动后仍可续聊、不同线程隔离已验；用户综合解释和独立修改待验，不能将 checkpoint 当业务写入回滚。 |
| 5. FastAPI / Pydantic | 会话、历史、纠正、角色与手机专用输入接口 | 正式service.py/review_models.py、根run_assistant.py/assistant_client.py与HTTP界面 | 实际HTTP、LAN通知专用鉴权与双服务启动/重启已验；用户原路由练习保留，个人分不变。 |
| 6. SQL 与持久化 | 原文、修订审计、角色、多标签、文件、事项与队列 | task_store/message_store/review_store/notification_gateway；工作流不以checkpoint代替事务 | 重复去重、原文保留/修订、修订及队列重启持久性已验；个人SQL仍50。 |
| 7. RAG 与向量检索 | 原件解析、400/80切分、BGE512/Qdrant、引用及文件正文分类 | rag_documents/embeddings/store/runtime/tools及document_classification.py | 实际文件bytes、正文分类逐字证据、问答引用及重启已验；用户RAG概念25保持。 |
| 8. 异步、邮件与持久提醒 | 阻塞IMAP移出事件循环，EML/UID、通知离线重试、提醒版本租约和编辑重排 | mail_*、notification_gateway/attention、event_time/review_store/reminder_*及Android源码 | 实际QQ2封正文与重连无重复；APK验签但未真机，QQ真实附件/新到达延迟未验；个人异步0。 |
| 9. 评测与 Trace | 当前10条人工标注合成通知分别验证类别、事项、时间和附件；以父子Span定位模型/工具与业务节点的耗时和错误。 | 已实现正式 `src/student_assistant/trace_store.py`、`tracing.py`、`trace_middleware.py`、`evaluation.py`；根[evaluate_assistant.py](../evaluate_assistant.py)、`examples/module05_eval_cases.jsonl`及32项专测，见[模块五](MODULE_05_OBSERVABILITY.md) | 能展示live固定样本的分子/分母及Trace错误定位；rules只作工程回归，非全域RAG评测、未接OTEL SDK/LangSmith；用户改预期和排错练习待反馈，掌握分仍0。 |
| 10. Streamlit / 功能验证 | HTTP界面表单门控、thread_id、修订与手机本机配对 | assistant_dashboard/dashboard_review/dashboard_connections，七页界面 | 实际AppTest/Edge/人工编辑/本机二维码已验；个人0保持，当前不布置练习。 |
| 11. Git / Docker / 部署 | 环境复现、进程管理、健康检查、持久卷和交付边界 | start_assistant.py、启动助理.cmd、97包锁文件、Dockerfile/Compose | 一键检查/启动/停止/真实重启已验；Git仅init，Docker未构建运行，个人0。 |

模型适合理解与提取；用户指定的重点身份通过配置匹配，提醒由已验收的SQLite outbox和后台循环执行。`version`防旧编辑覆盖，租约token防旧worker确认新任务；任务完成会取消未提交提醒。日期钟点只来自用户配置，实际Windows提交与重启补发已验，`submitted`不等于已读或跨OS恰好一次。手工分析提交至提醒计划之间尚无共同事务内规划outbox，失败需显式补计划，邮件持久队列会重试补计划。

时间工程的有限预览入口为`lessons/time_parser.py`及`POST /time/normalize`，第十一课扩展模型意图与日期/范围精度；旧接口保留。模块二已关联事项证据和时间，模块四持久提醒已通过真实集成验收；本轮已验完整起止、时间编辑和重排，用户独立调度设计仍未验。不能把纯规则预览当作任意中文时间理解，具体政策见[时间规则](TIME_POLICY.md)。

## 当前教学暂停，交付后再安排

用户要求先做完项目，后续再围绕完整项目学技术栈。当前工程83%，个人学习400/1300约31%冻结，不布置本轮作业、不估学习对话，也不把代理完成的代码计为用户独立能力。后续以实际交付代码逐项复盘；此前的示例、练习与掌握标准保留作参考。

本地Qdrant仍单进程，文本文件≤10MB、无OCR；引用ID/逐字quote校验不保证语义蕴含。正文分类与证据已补齐；QQ已实测正文与重连无重复，真实附件/到达延迟、Android手机和Docker运行继续待验。展示和简历需区分源码实现、离线测试、真实服务、真实账号/设备与个人能力。

技术状态按“未开始 → 读过 / 待练习 → 能在提示下完成 → 能独立完成并解释”记录。安装成功、看懂讲解、AI 生成了代码，都不能直接记为掌握；项目进度也单独区分准备、实现与验证。

## 做完后拿什么展示

- 一个可运行仓库：模型接入、结构化提取、工具调用、状态编排、HTTP 服务、业务数据库与提醒闭环。
- 一次可复现演示：导入通知 → 识别重点身份 → 分类与提取 → 确认歧义 → 查询事项 → 到期提醒；用脱敏样本展示。
- 一份可审查报告：样本、指标、失败案例、Trace，以及为何采用当前方案；RAG 同时展示检索引用。
- 一份运行说明与提交记录：环境、启动方式、配置项、验证方法、已验证和待验证的接入能力。

面试时应能解释：为何不用模型直接定闹钟；如何避免重复提醒；怎样处理“周五前”；检索没找到时怎么办；一次错误如何从日志定位到模型、工具、状态或数据库。

模块五交付时验收记录（历史8条，2026-10-16）：[真实记录](results/module05_example.json)包含12项检查、17条断言；实际Uvicorn进程12700停止后由新进程24528恢复同一Trace及Span，坏PDF定位到document.extract，临时服务和评测数据库已核验清理。模块五交付时185项测试全部通过；项目53%、学习400/1300约31%，当时主线预计2–4次。真实QQ实际登录与部分正文读取已有证据，真实附件、增量同步与延迟完整验收待补。
