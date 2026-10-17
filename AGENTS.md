# 协作与教学约定

## 最新执行方式：先完成项目，教学暂缓（2026-10-17）

- 最新任务已恢复项目收尾。真实QQ指定PDF附件链路9项、最近新信优先真实检查4项通过；新增13项防积压回归，本机与GitHub Actions均350项（根277＋lessons73）通过，pip check通过。GitHub Actions已实际构建和运行Linux Compose双服务并验收重启，M6容器1分计入，当前项目84%、阶段[10,10,20,15,20,0,9]；学习400冻结。手机0设备、真正新信延迟仍待，M5保持0。详情见`docs/results/closeout_status.json`。本次自动重启自有进程被审批策略拒绝（blocked by policy，无具体原因），未执行，不得换方式重试终止；请用户在桌面控制台停止再启动加载修复。用户早先已同意安装安卓、指定邮件样本，仍等待设备操作。

- 用户最新要求优先整理到本人GitHub供简历放链接，并在仓库新增与现有PDF段落结构一致的项目介绍。当前已发布公开仓库`https://github.com/notzjy/student-research-assistant`，公开首页、排除规则、署名、CI和`docs/RESUME_PROJECT.md`均已上传；原PDF未改。初版317文件的远端树哈希与本地完全一致，GitHub Actions实际337项测试及pip check已通过，发布与Actions状态见`docs/PUBLICATION.md`和`docs/results/github_publication.json`；手机与指定邮箱附件实机验收暂缓。不因本次文档整理和公开发布增加学习分。
- 用户明确要求现在停止教学、直接完成项目；后续再围绕完整项目安排技术栈学习。当前不布置练习、不以答题或解释作为推进前提，个人学习分400/1300保持，预计学习对话次数交付后重排。
- 继续按原产品目标补齐功能和真实验证，明确自动通知摘要与完整正文/文件原件的边界；需要设备系统授权或外部环境时说明实际阻塞，不能用模拟通过代替实机接入。
- 原代码注释、用户数据保护、中文沟通与两个进度条约定继续适用；下面教学节奏条目暂缓，直到用户恢复学习。

## 最新本机交付与待验边界（2026-10-17）

- 当前项目83%，阶段[10,10,20,15,20,0,8]；评分依据和验收条件调整见`docs/PROJECT_PLAN.md`。学习400/1300约31%冻结，预计学习对话次数交付后重新安排；代理开发和测试不增加用户掌握分。
- 双击`启动助理.cmd`可管理本次后端和七页Streamlit界面；沿用原双库，拒绝接管已有端口，只停止本控制台创建的进程。消息支持普通粘贴与附件上传、搜索分页、类别与多标签、版本化人工纠正；正式服务和Agent工具明确区分pending/completed/cancelled，取消不能混入已完成。
- 正式消息链路支持绝对日期、分钟时段和日期区间、友好时间编辑与修改后提醒重排；日期不补钟点。任意格式原件≤25MB可归档下载；txt/md/文本PDF≤10MB可解析、正文分类和RAG，无OCR。重点联系人到达收件箱与截止提醒独立，来源角色仍由用户明确配置。
- 安卓无线伴侣APK已实际构建、对齐和签名校验；同Wi-Fi下载配对、离线缓存和恢复上传不依赖USB。通知仅为应用展示的摘要，不等于完整聊天、真实稳定身份或文件原件；默认不把通知发布时间冒充原消息时间。尚无真机安装、通知授权和三应用实际采集证据，跨网络HTTPS服务没有部署。
- [最终验收](docs/results/final_delivery.json)29项真实HTTP/AppTest/DeepSeek/BGE512/Qdrant/独立Edge/自有启动器重启检查全部通过；264根tests＋73lessons共337项回归通过。实际Uvicorn16444→25688，专用进程与数据已清理；[界面截图](docs/results/final_dashboard.png)已目视核对。75份Python文件无缺失函数/类docstring，根锁97包且pip check通过；合成业务验收没有访问真实邮箱或发送Windows通知。
- [QQ只读验收](docs/results/qq_delivery_check.json)实际读取两封正文，断开重连后的无新信增量同步没有重复；真实附件和新信到弹窗延迟仍未验。同步请求耗时不等于通知延迟。M5完整闭环继续0/15；手机实机、真实附件和Docker运行等未验部分不据代码齐备标为通过。
- [交付说明](docs/DELIVERY.md)提供操作入口和边界，[后续学习地图](docs/POST_PROJECT_LEARNING.md)保存恢复教学后的路线。Docker只有静态配置，根Git仅初始化main、无暂存/提交/远程；上游原版全量demo未运行。以下模块数字均为历史节点。

## 历史：模块六交付与学习证据（2026-10-17）

- 当前项目60%，阶段[5,7,19,7,16,0,6]：M1最小界面启动+2、M4时间地点文字与色标+3、M6本地可演示交付+2；不计入尚未运行的Docker，也不计入QQ完整闭环。学习仍400/1300约31%，预计主线还需1–3次有效反馈，补QQ真实附件、增量同步与延迟、用户界面综合练习及机动；多平台目标仍未全部实现。
- `assistant_dashboard.py`、`dashboard_api.py`、`dashboard_views.py`交付独立Streamlit1.66界面，5页仅通过HTTP访问原8002后端，默认8501回环地址。首次打开仅GET消息，模型/写入/同步均明确按钮或表单提交；同界面会话复用thread_id，显式新建换号。session_state不代替SQLite/checkpoint；原上游界面仍未完整运行。
- 新POST `/tasks/{task_id}/complete`完成明确选中的16位hex事项，重复幂等；复用数据库触发器取消未投递提醒、保持submitted。固定按钮无需模型选工具，Agent工具原来的查询ID保护保持。
- 218项回归（根145＋lessons73）通过；新增20为adapter9、complete3、UI8。[模块六证据](docs/results/module06_example.json)有16项实际HTTP/AppTest/DeepSeek/BGE512/Qdrant/独立Edge检查，截图已目视检查；实际Uvicorn28216停止再由26300恢复双库、向量和Trace。临时进程与专用数据已清理；本轮没有实际QQ访问或Windows弹窗。
- 根锁96包，pip check通过；pywin32和win32_setctime加Windows平台标记。Git仅初始化main，无暂存/提交/远程；Dockerfile、compose与排除规则静态检查完成，本机无Docker CLI，不能声称构建、容器运行或部署成功。
- [模块六讲义](docs/MODULE_06_DELIVERY.md)提供两终端启动、关键代码与一次综合练习。用户已解释创建过滤不能覆盖旧队列，仅记有限理解、时间50保持；UI0、Git/部署0、Tool Calling0、异步0、评测0等待练习，不因代理代码/测试加学习分。下面2026-10-16数字是历史交付时快照。

## 旧邮件误提醒修复证据（2026-10-16，历史）

- 已确认QQ邮箱实际登录与部分正文读取；用户也已看到Windows弹窗，但历史邮件被误建提醒。真实附件、增量同步与延迟完整验收待补，M5完整闭环暂不计分。
- [本轮排错讲义](docs/LESSON_REMINDER_GUARD.md)与[离线演示](reminder_guard_demo.py)区分原消息时间和当前决策时间：2021年消息的“四天后”仍算2021年，不应据此在今天新建自动提醒。新自动计划跳过过期事项和已过提醒钟点；旧自动/legacy队列在投递前检查来源时间，显式manual计划保留补发。升级仅添加origin标记，不批量删除用户消息或事项。
- Prompt区分无需操作的商业自动续费告知与明确取消请求；不是按“续费”关键词一律封禁。固定语义评测当前为10条，新增两条对照样例；历史8条与70项时间字段结果仍是模块五交付时的记录，不能直接套到当前10条。
- 用户已完成消息导入和Trace查询跟练；“执行完成不等于语义正确”的解释与评测修改练习仍待反馈，评测掌握仍0。项目53%、学习400/1300约31%、主线预计2–4次保持；本轮198项全量测试（根tests125＋lessons73）通过；真实DeepSeek的10条live得到分类10/10、事项数10/10、时间字段81/81、附件线索4/4、模型附件数量10/10，rules同分只属工程回归。结果见[本轮修复验收](docs/results/reminder_guard_fix.json)。

本项目是用户基于 agent-service-toolkit 学习开发的个人研究与日程助理。

- 默认使用中文。每次面向用户的进展说明与最终答复都附两个进度条：项目完成进度、技术栈学习进度。
- 用户最新要求加快推进、聚焦核心智能体技术栈。每次在学习进度条下另列“预计主线还需约几次对话”；六批代码与本机演示已交付，QQ完整联调及用户综合练习待补，现估计1–3次有效提问/反馈，详情见 `docs/ACCELERATED_ROADMAP.md`。估计随实际剩余工作更新，不按收到消息次数机械递减；不表示13项技术已全部掌握或多平台项目已完成。
- 进度依据 `docs/PROJECT_PLAN.md` 和 `docs/LEARNING_PROGRESS.md`；说明已实现、已验证与尚待验证的区别。
- 用户要求核心技术栈、当前热度与个人学习掌握情况集中保存在 `docs/AGENT_TECH_STACK.md`；学习证据或分数变化时同步更新该看板，热度判断需注明核查日期与来源范围。
- 用户最新目标是通过项目学习智能体开发技术栈以求职，已明确反对缺少示例、与实际技术脱节的抽象问答。
- 教学采用“本次交付什么 → 技术名与项目代码位置 → 可运行示例和实际输出 → 解释关键行 → 改一处观察结果或排错”的方式。先给足示例，再安排关键技术练习。
- 按完整功能模块推进，每轮整合相关核心技术并交付可运行链路；字段、基础语法和小知识点放进模块解释，不再各占一轮。先给示例和关键代码，再安排一次综合运行/修改/排错练习；不把抽象答题作为推进前置关卡。
- 用户反馈与下一模块推进可合并在同一轮，前置条件满足时不再等待用户单独说“继续”；遇到用户澄清问题先回答并继续相关工作，除非用户要求只讨论或暂停。加快通过合并真实交付实现，项目与学习分仍依验收和用户证据更新。
- 阅读过介绍不等于掌握。学习掌握度以用户练习和迁移运用为证据；记录尚未答题的状态。
- 用户决定重点联系人、角色、分类与提醒规则。不能把模型对昵称或身份的猜测当成已确认配置。
- 用户已选 Windows 桌面通知＋提醒列表；QQ邮箱已有实际登录与部分正文读取证据，使用 `configure_mail.py` 本机配置，授权码不发聊天。日期级事项只有用户明确配置提醒钟点后才能自动计划，原事件精度不变；`submitted`仅代表交给通道，不代表已读或跨系统恰好显示一次。
- 时间解释按用户偏好：裸“周三/周四”等指原消息北京时间所在自然周（周一至周日），即使已过去也不自动顺延下周；能确定日期不代表已确定钟点。具体规则与当前实现边界见 `docs/TIME_POLICY.md`。
- 用户要求项目新增代码有注释：新增或修改的每个函数都添加中文 docstring，至少解释用途，适量说明输入和返回值；关键调用、状态合并和持久化步骤添加中文行注释。类也应说明职责。此要求优先于上游默认少注释约定。
- 保留用户自己完成的练习改动，例如第二课 summary 和第四课 report 节点；新增功能与验证须适配当前代码。
- 其他聊天中的最新用户指示优先于这里记录的默认节奏。

## 工作区

- `agent-service-toolkit/`：固定上游源码快照，现通过 `service:app` 条件分派运行个人助理 `student` profile，已完成真实服务验收。原全量 demo、Streamlit 和 `pyproject` 的 42 项运行直接依赖未完成安装运行，不能据此声称完整上游已启动。
- `agent-service-toolkit/src/student_assistant/`、根 `run_assistant.py` 与 `assistant_client.py`：模块一正式服务与客户端已交付，默认端口 8002；业务 SQLite 与 checkpoint SQLite 分库，复用根模型配置和教学基础依赖，补 SQLite checkpoint 3.1.0 / aiosqlite 0.22.1。84项测试和真实服务进程重启/线程隔离/五工具调用已验，用户线程复用/隔离已反馈，工具和重启操作未验；证据及边界见 `docs/MODULE_01_AGENT_MEMORY.md`、`docs/results/module01_example.json`。
- 模块二已交付同一8002服务的 `message_models.py`、`message_store.py`、`message_workflow.py`、消息/角色接口和JSON客户端；99项测试及真实DeepSeek/实际客户端TCP11项检查、12条断言通过。工作流按业务状态重试，不使用checkpoint；出处校验不等于语义全对，该模块交付时真实来源/下载/提醒未实现。当时用户线程应用75、总375/1300约29%，no_tools不计工具调用，用户重启无证据；用户仅核对processed/exists同ID及task_ids稳定等部分跟练；该批交付时项目40%、预计4–6次，见 `docs/MODULE_02_MESSAGE_PIPELINE.md`、`docs/results/module02_example.json`。
- 模块三已交付正式 `rag_documents/rag_embeddings/rag_store/rag_runtime/rag_tools`、bytes上传/文档检索/问答与CLI新模式，沿用8002及六工具主Agent；115项测试、真实CLI/TCP12项检查/11条断言已验，见 `docs/MODULE_03_DOCUMENT_RAG.md`、`docs/results/module03_example.json`。根依赖FastEmbed0.9.0/Qdrant Client1.19.1/pypdf6.20.1、锁文件77包；真实本地BGE中文512维约95MB缓存保留，Qdrant local先单进程、文件≤10MB无OCR。合成原创md不是取得真实参考论文，原消息mentioned不改、详情indexed、附件分类仍message_text；引用出处检查不保证语义蕴含，主Agent自由回复只Prompt约束。临时进程/双SQL/向量/原件均清理、未动用户数据。用户RAG概念答题通过记25：细化chunk切分与Embedding、查询同模型向量化、程序调用Qdrant；独立实现与综合运行待验。学习更新为400/1300约31%，其他模块不变，异步/Tool Calling仍0。
- 模块四 `reminder_store/reminder_runtime/notification` 与 `mail_ingest/mail_store/mail_runtime`、服务接口和客户端已交付，`docs/MODULE_04_AUTOMATION.md`、根 `configure_mail.py`、`module04_demo.py` 提供操作步骤。153项全量测试（根tests80＋lessons73）通过，其中新增38项为store13、mail12、runtime7、API6；`docs/results/module04_example.json`记录正式CLI/TCP、真实DeepSeek提取、实际Windows通道submitted与重启补发的11项检查、11条断言。实际Uvicorn进程30512→2016，停机端口不可访问，临时目录已核验清理；用户已反馈桌面弹窗，同时报告历史邮件误提醒。项目53%=[5,5,19,7,13,0,4]，持久提醒计入8%；M5仍0，模拟IMAP/MIME不算实际QQ接入。手工分析与提醒计划之间没有事务内规划outbox，异常需显式补计划；邮件队列重试补计划。日期/范围不补截止钟点；取消提醒不等于取消任务，旧tasks仅pending/completed。学习400/1300约31%，异步仍0，该批交付时预计剩余3–5次。
- 模块五`trace_store/tracing/trace_middleware/evaluation`、根`evaluate_assistant.py`及`--traces/--trace`已实现，讲义`docs/MODULE_05_OBSERVABILITY.md`；模块五交付时185项全量测试（新增32项为12+5+8+7）通过，真实CLI/TCP及Trace持久化12项检查、17条断言通过。模块五交付时的真实live对8条合成消息得到类别8/8、事项数8/8、时间字段70/70、附件线索4/4、模型附件数量8/8；rules100%仅为程序回归，不是模型质量指标。Trace只存白名单元数据、不存原文/Prompt/参数/凭证，未接OTEL SDK或LangSmith；本批不等于全部RAG语义质量已测。项目53%、学习400/1300约31%保持，用户只解释提醒无需DeepSeek，记职责理解，asyncio/to_thread应用与QQ完整联调仍待验，异步/ToolCalling/评测仍0。当前预计剩余2–4次。
- `lessons/` 与根目录 `.venv/`：教学脚本和 Python 3.13.9 环境保留，个人助理 profile 复用该环境；全部上游依赖仍未安装。预览和模拟测试不可宣称真实推理成功。
- `.upstream/`：上游下载存档与溯源材料，来源见 `docs/UPSTREAM.md`。
- 根目录文档：产品方案、学习记录与协作约定。
- 不把通知摘要等同完整消息，不把文件名等同已取得文件。各平台能力依据官方资料与实际验证。

模块五交付时验收记录（历史8条，2026-10-16）：[真实记录](docs/results/module05_example.json)包含12项检查、17条断言；实际Uvicorn进程12700停止后由新进程24528恢复同一Trace及Span，坏PDF定位到document.extract，临时服务和评测数据库已核验清理。模块五交付时185项测试全部通过；项目53%、学习400/1300约31%，当前主线预计2–4次。真实QQ实际登录与部分正文读取已有证据，真实附件、增量同步与延迟完整验收待补。
