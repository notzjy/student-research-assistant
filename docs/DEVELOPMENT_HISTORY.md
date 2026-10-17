# 开发与教学历史记录

本页保留公开整理前的README内容；当前运行方式和验证边界以[仓库首页](../README.md)及[交付说明](DELIVERY.md)为准。

# 个人研究与日程助理

## 当前执行模式：先完成项目，后学习完整技术栈（2026-10-17）

按用户最新要求，**现在暂停教学，直接完成功能和交付验收**；当前不布置作业，也不要求答题才能推进。学习仍为400/1300，恢复学习时再围绕完整项目安排。这里的最新执行模式覆盖下方历史课程中“边开发边教学”的推进安排。

**使用入口：双击根目录[启动助理.cmd](../启动助理.cmd)，在控制台点击“启动助理”，准备完成后自动打开本机网页。** 默认沿用已有模块一双库；已有8002/8501服务时拒绝接管。关闭控制台可选择停止或最小化，局域网接入由明确选项开启。

- [交付与使用说明](../docs/DELIVERY.md)：七个页面、角色/多标签与人工纠正、文件正文分类和RAG、区间时间、截止提醒与重点收件箱、QQ邮箱、手机无线安装配对和验证边界。
- [手机通知接入](../docs/CHANNEL_INTEGRATION.md)与[已构建APK](../android-notification-bridge/dist/student-assistant-bridge.apk)：同Wi-Fi可扫码下载与配对；日常不依赖USB，断连先缓存。通知摘要不保证聊天全文、原消息时间、真实唯一身份或附件原件；尚无真机安装/授权/真实通知验收证据。
- [交付后的技术栈学习地图](../docs/POST_PROJECT_LEARNING.md)：只保存未来学习安排，当前暂停教学，个人学习分不因代理新增代码而变化。

项目进度：`█████████████████░░░` **83%**；技术学习：`██████░░░░░░░░░░░░░░` **31%（400/1300，暂缓）**。预计学习对话次数：项目交付后重新安排。评分依据见[项目计划](../docs/PROJECT_PLAN.md)。

[最终验收报告](../docs/results/final_delivery.json)：**337项回归通过（根264＋lessons73），29项实际联调检查通过**，覆盖真实DeepSeek、本地BGE/Qdrant、文件与人工纠正、无线接收入口、七页界面、自有启动器和重启持久性；[实际界面截图](../docs/results/final_dashboard.png)已目视检查。消息可直接粘贴录入，已取消事项与已完成事项独立查询。

[QQ只读验收](../docs/results/qq_delivery_check.json)已读取真实正文，并验证断开重连后无新信时不重复导入；真实附件和新信到弹窗的延迟仍待验。安卓APK已构建签名，但尚无真机安装授权和实际通知证据。Docker只有静态配置，尚未构建运行；根Git仅初始化，无提交和远程。不能据代码齐备宣称多平台已经全部实机验证。

以下保留历史交付与教学记录，其中百分比、测试数量和剩余对话估计属于相应历史节点，当前实现与验收以最新交付说明和报告为准。

## 历史：模块六本地操作界面（2026-10-17）

项目进度：`████████████░░░░░░░░` **60%**，阶段[5,7,19,7,16,0,6]。技术学习：`██████░░░░░░░░░░░░░░` **31%**（400/1300），预计主线还需约 **1–3次有效提问或反馈**，用于QQ真实附件、增量同步与延迟联调，以及界面综合练习和补缺。多平台原始目标仍未全部完成，代理验收不等于用户已掌握。

- [模块六操作与教学](../docs/MODULE_06_DELIVERY.md)：新建本地[Streamlit界面](../assistant_dashboard.py)通过[HTTP适配器](../dashboard_api.py)访问已有8002服务，提供消息与日程、研究资料、提醒与角色、运行追踪、对话助理五页。颜色与文字区分截止、日期/时刻/范围及地点，保留原文和两份附件线索；写入与模型请求只在明确提交后执行。
- [真实验收](../docs/results/module06_example.json)：16项实际HTTP、Streamlit脚本交互、DeepSeek、本地BGE512/Qdrant、独立Edge浏览器与重启检查通过；[实际界面截图](../docs/results/module06_dashboard.png)已人工目视核对。Uvicorn进程28216停止后由26300恢复业务、线程、向量文件和Trace；专用验收数据与进程已清理。本次没有连接真实QQ或发送Windows桌面通知。
- 回归 **218项通过**（根tests145＋lessons73），模块六新增20项为HTTP适配器9、完成接口3、界面8；`pip check`通过。根依赖锁定96包，Windows辅助包带平台标记。
- 根Git已初始化main，尚未暂存、提交或配置远程；[Dockerfile](../Dockerfile)与[Compose](../compose.yaml)已准备并静态检查，本机没有Docker CLI，**镜像构建与容器运行待验证**。新界面独立于上游原始Streamlit；上游全量demo及42项运行直接依赖仍未完整验证。
- 学习看板已更新实现位置，个人分仍保持400：UI、Git/Docker、Tool Calling、异步、评测等待用户专项练习。用户已理解“只在创建时过滤无法覆盖旧队列”的要点，时间模块仍50，不据此增加未练习技术的分数。

启动已有8002主服务后，在第二个终端运行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run .\assistant_dashboard.py --server.address=127.0.0.1 --server.port=8501 --browser.gatherUsageStats=false
```

浏览器访问`http://127.0.0.1:8501`。后端重启时沿用原来的双数据库路径；当时的完整命令和综合练习保留在模块六讲义。下方各模块数字均为交付时历史记录；当前执行模式与交付入口以上方最新版为准。

## 旧邮件误提醒修复记录（2026-10-16，历史）

- 已确认QQ邮箱实际登录与部分正文读取；用户也已看到Windows弹窗，但历史邮件被误建提醒。真实附件、增量同步与延迟完整验收待补，M5完整闭环暂不计分。
- [本轮排错讲义](../docs/LESSON_REMINDER_GUARD.md)与[离线演示](../reminder_guard_demo.py)区分原消息时间和当前决策时间：2021年消息的“四天后”仍算2021年，不应据此在今天新建自动提醒。新自动计划跳过过期事项和已过提醒钟点；旧自动/legacy队列在投递前检查来源时间，显式manual计划保留补发。升级仅添加origin标记，不批量删除用户消息或事项。
- Prompt区分无需操作的商业自动续费告知与明确取消请求；不是按“续费”关键词一律封禁。固定语义评测当前为10条，新增两条对照样例；历史8条与70项时间字段结果仍是模块五交付时的记录，不能直接套到当前10条。
- 用户已完成消息导入和Trace查询跟练；“执行完成不等于语义正确”的解释与评测修改练习仍待反馈，评测掌握仍0。项目53%、学习400/1300约31%、主线预计2–4次保持；本轮198项全量测试（根tests125＋lessons73）通过；真实DeepSeek的10条live得到分类10/10、事项数10/10、时间字段81/81、附件线索4/4、模型附件数量10/10，rules同分只属工程回归。结果见[本轮修复验收](../docs/results/reminder_guard_fix.json)。

基于 [agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit) 开发，同时用于学习 Agent 技术。

目标：汇总多应用的重要消息与文件，按用户定义的角色提高提醒级别，区分研究、学生生活等类别，提取截止时间、活动时段和地点，提供可追溯的提醒。

## 当前状态（2026-10-16）

- 当前采用[核心技术栈加速路线](../docs/ACCELERATED_ROADMAP.md)：前五批已交付，模块五Trace与语义评测已验收；QQ邮箱实际登录与部分正文读取已有证据，完整联调待补，预计主线还需2–4次有效提问/反馈，完成QQ联调、可演示交付并保留机动。每次学习进度条下更新估计，用户反馈与下一模块推进合并。
- 加速[模块一：主 Agent 与持久化记忆](../docs/MODULE_01_AGENT_MEMORY.md) 已通过工具包 `service:app` 的个人助理 `student` profile 运行：正式包位于 `agent-service-toolkit/src/student_assistant/`，根入口 `run_assistant.py` 默认端口 8002，客户端为 `assistant_client.py`。整合五项工具、thread_id 与独立 checkpoint 库；73 项旧测试与 11 项新测试共 84 项通过，真实 DeepSeek/TCP 验证实际服务进程停止后重新启动仍可续聊、不同线程隔离及五工具调用，见[模块一记录](../docs/results/module01_example.json)。用户线程复用与隔离已反馈，工具和重启操作未验；原全量 demo 和 Streamlit 未运行，原 `pyproject` 的 42 项运行直接依赖未全部安装。
- 项目进度：53%，阶段[5,5,19,7,13,0,4]；模块五质量工具不新增权重。模块五交付时185项全量测试（根tests112＋lessons73，其中模块五新增32）全部通过；[模块四真实验收](../docs/results/module04_example.json)已验证Windows提交与服务重启补发。人工消息、原件与带来源检索已验；QQ邮箱实际登录与部分正文读取已有证据，完整验收待补，M5保持0，文件正文分类与界面尚待完成。
- 学习进度：约 31%（13 个模块合计 400/1300 分，约 30.77%）。用户 RAG 概念答题通过，记25分；已细化文本切分与Embedding的区别、查询用同一Embedding模型、程序调用Qdrant后再把片段交给回答模型。LangGraph当前线程应用仍75，时间仍50，Tool Calling和异步接入仍0；用户独立实现、重启排错及新模块综合操作待验证。
- [模块五：Trace与语义评测](../docs/MODULE_05_OBSERVABILITY.md)已实现：同库本地Trace、ContextVar父子Span、响应头`X-Trace-ID`及只读查询，`evaluate_assistant.py`对当前10条固定合成通知评分（模块五交付时为8条）。模块五交付时185项全量通过，含新增32项；真实CLI/TCP及Trace持久化12项检查、17条断言通过。模块五交付时的真实`--mode live`得到类别8/8、事项数8/8、时间字段70/70、附件线索4/4、模型附件数量8/8；`rules`100%仅为程序规则回归。反例将首条预期类别改为求职后，类别降至7/8=87.5%，其他指标不变，证明评分会检出预期差异。该集不覆盖全部RAG质量；本地Trace未接OpenTelemetry SDK或LangSmith。项目仍53%、学习仍400分；用户正确理解提醒执行不需DeepSeek，仅记职责分工证据，异步实际应用未验。
- [模块二：消息业务闭环](../docs/MODULE_02_MESSAGE_PIPELINE.md)已接入同一8002服务，99项测试与真实客户端/TCP的11项检查、12条断言见[模块二记录](../docs/results/module02_example.json)。用户已核对processed/exists同一message_id、task_ids稳定及研究分类/角色/时间/两附件保留；409、新消息实验和概念题尚未反馈，无Tool Calling证据，学习分不变。[消息示例](../examples/module02_notice.json)与[角色示例](../examples/module02_role.json)仅用于演示，完整综合练习尚未完成。
- [模块三：研究文件RAG](../docs/MODULE_03_DOCUMENT_RAG.md)已复用8002服务，客户端支持`--index-file/--documents/--search/--ask`。115项测试、真实本地BGE512维/磁盘Qdrant/DeepSeek与CLI/TCP的12项检查、11条断言见[模块三记录](../docs/results/module03_example.json)；已验证原件下载哈希、引用偏移和实际服务进程重启后的索引持久性。验证材料为[合成研究笔记](../examples/module03_research_note.md)、[合成学生指南](../examples/module03_student_life.md)及[消息](../examples/module03_notice.json)。用户概念答题已通过，综合运行和独立修改待反馈。Qdrant local先按单进程运行，支持≤10MB的txt/md/文本PDF，无OCR；本地Embedding首次需下载，回答仍实际调用DeepSeek。原消息mentioned不改、详情叠加indexed，附件分类仍来自message_text；`/documents/ask`校验引用ID和逐字出处，不保证回答语义全部被证据支持，主Agent自由回复引用仅受Prompt约束。
- [模块四：自动收件与持久提醒](../docs/MODULE_04_AUTOMATION.md)已交付，用户已选择 Windows 桌面通知＋提醒列表。新增38项测试（提醒存储13、邮件12、runtime7、API6）纳入153项全量回归并全部通过；11项真实检查、11条断言已验，实际Uvicorn进程30512停止、端口不可访问后，由新进程2016补发持久提醒，临时目录已核验清理。模拟邮件不代表QQ账号已接通。原模块四入口为 [module04_demo.py](../module04_demo.py) 和本机授权码配置 [configure_mail.py](../configure_mail.py)；QQ邮箱已有实际登录与部分正文读取证据，真实附件、增量同步与延迟完整验证待补。本轮先运行 [无弹窗守卫演示](../reminder_guard_demo.py) 核对修复。提醒仅在用户明确启用规则后自动计划，日期缺钟点不补造截止时刻；`submitted`表示交给通道，用户已反馈桌面弹窗，同时报告历史邮件误提醒，不表示已读或严格只显示一次。手工分析提交与提醒规划之间尚无共同事务内规划outbox，失败需显式补计划。
- 用户会一点 Python，尚无完整项目经验；第一版以 Windows 为主，安卓手机可配合后续通知采集验证。
- 上游源码位于 `agent-service-toolkit/`，为固定提交的源码快照，未包含 Git 历史。
- 当前默认 Python 为 3.10.6；已用 Python 3.13.9 创建根目录 `.venv` 并安装首课教学依赖。
- 用户已有 DeepSeek API Key，已在本机配置；第二课默认通知已真实调用模型并通过校验，结果见 `lessons/results/02_example.json`。
- 第三课已实际完成两次 `save_task` 调用、JSON 文件持久化和工具结果回传；记录见 `lessons/results/03_example.json`，本机待办位于 `local-data/lesson03_tasks.json`。
- 第四课保留用户新增的 report 节点；第五课 HTTP 请求已实际运行 `model → tools → report → model`，请求与响应见 `lessons/results/05_example.json`。
- 第三、四课共11项离线测试及第五课8项接口测试通过；新增课程与正式模块函数均有中文说明。完整上游工具包和真实消息账户仍未全面接通；持久提醒已通过模块四真实集成验收。
- 第六课 `lessons/task_store.py` 提供 SQLite 保存、查询、完成与旧 JSON 导入；10 项数据库测试通过。命令行直接使用该模块，默认数据库是 `local-data/lesson06_tasks.sqlite3`；第三至第五课独立运行时默认仍使用 JSON。
- 第七课已实现 `lessons/assistant_tools.py` 工具工厂与 `lessons/07_sqlite_api.py`：显式传入 `TaskStore`，让工具保存、report 数量与接口查询共用存储。这是依赖注入与模块拆分；本课不使用 FastAPI `Depends`，也未接入图 checkpoint。
- 第七课默认数据库为 `local-data/assistant.sqlite3`、端口为 8001；新增 `GET /tasks/pending/count`，旧 `05_client.py` 可通过 `--base-url` 调用。第五课仍默认端口 8000 和 JSON；只有显式 `--import-json` 才迁移旧记录，启动时不自动导入。
- 第七课 7 项集成测试及第三至第六课 29 项回归测试全部通过；模拟模型、真实图与临时 SQLite 用于离线测试。另以本地随机 TCP 端口、临时数据库和真实 DeepSeek 验证两项待办保存、同库统计、另一个 CLI 进程完成一项后的数量变化，以及空白输入 422；记录见 `lessons/results/07_example.json`。
- 第七课当时的代理临时验证在关闭服务后重开数据库，记录仍在、旧 JSON 哈希未变；当时未执行真实 TCP 服务重启，也未创建默认 `assistant.sqlite3`，不据此判断用户后来实际数据库的状态。当时完整上游、消息来源、角色、附件与提醒仍未实现。
- 第七课用户已理解新数据库且未导入时查询为 0、工具无需知道或修改数据库路径；已在 `07_sqlite_api.py` 的 `create_app` 内新增 `GET /tasks/pending`，含中文函数说明，调用同一 `store.list_tasks(status='pending')` 返回 `count/tasks`，并反馈校对成功。
- 第八课已把查询能力接入 Agent：`assistant_tools.py` 增加 `list_tasks` 工具，支持 `pending`（默认）、`completed`、`all`；接口增加查询结果结构与实际工具名 `tool_names`。继续使用第七课 8001 服务和 `05_client.py --message`，不新增服务。
- 第八课 8 项新测试与旧课 36 项回归共 44 项通过（模拟模型、真实图、进程内 HTTP、临时 SQLite）。真实 DeepSeek 另通过临时 TCP 服务查询程序预置的 1 项未完成、1 项已完成待办：两次均仅调用 `list_tasks`，状态筛选与数量各 1、ID 与库一致，查询后记录和完成状态未变；用户新增 `/tasks/pending` 也与工具结果一致。临时服务已停止，证据见 `lessons/results/08_example.json`；该课交付时项目为 21%，学习约 23%。
- 第八、九课尚未收集用户专项 Tool Calling 答题证据，教学新增不自动增加掌握分。第九课已复用 `TaskStore.mark_completed()` 实现 `complete_task(task_id)`；一次工具表创建的闭包记录成功查询返回的 ID，未查过的 ID 拒绝写入。当前每次 `run_graph` 重建图与工具表，实现请求间隔离；以后缓存图时需重新处理。同批工具请求按顺序执行，允许同轮先查后完成，不强制两轮模型调用。工具本身重复完成的幂等性由离线测试验证。
- 第九课继续使用第七课 8001 服务和 `05_client.py --message`，不新增服务入口；SQLite API 图步数上限调整为 12，旧 JSON 入口仍为 8。Prompt 引导歧义时澄清，不等于程序已经保证语义选择正确。该课交付时项目为 21%，学习 300/13、约 23%，用户独立实现仍待验证。
- 第九课新增 8 项离线测试与旧课 44 项回归共 52 项通过。真实 DeepSeek 经临时 TCP 服务两次返回 200：首次实际工具为 `list_tasks → complete_task`、执行 7 个节点，仅目标由 pending 变 completed、另一项仍 pending；再次请求只调用 `list_tasks`，读到已完成后直接回复，没有再次调用完成工具或修改数据，pending 仍为 1。待办列表与数量接口一致，记录见 `lessons/results/09_example.json`；临时进程已停止，仅使用合成数据，未修改用户数据库。
- 第十课已实现有限时间表达归一化：`lessons/time_parser.py` 使用 `datetime`、`timedelta`、固定 UTC+08 `timezone` 和 Pydantic；`lessons/10_time_normalization.py` 提供 CLI，既有服务新增只读预览 `POST /time/normalize`，第七课入口沿用。原待办表、保存协议和工具流程保持原有职责。
- 必须显式提供原消息发送时间 `reference_time`（带 ISO 偏移），不使用服务器当前时间代替；保留 `time_text` 原文与原参考时间的 ISO 偏移信息。仅支持今天/明天/后天的 24 小时时间或上午/下午中文整点 1—11，以及完整年月日时分；不增加 `zoneinfo` 依赖，也不调用模型。
- “周五前”、合法日期缺钟点、区间、否定混合或其他不支持表达返回 `needs_confirmation`，不擅自补 23:59；非法日期、时分返回 422，完整日期即使缺钟点也要检查日期是否合法。输出包含时区、状态、时间性质、`at` 和确认问题；有限规则预览不等于泛化中文时间理解，时间仍不落库或调度。
- 第十课新增 12 项专测与旧 52 项回归，共 64 项通过。另用模型配置置空的真实第七课 TCP 临时服务验证 4 项合法请求 200（含 UTC 跨日、待确认与 deadline）和 4 项非法请求 422；CLI 经 HTTP 结果与直接函数一致，原 pending 接口保留、数据库前后不变，临时服务已停止。记录见 `lessons/results/10_example.json`。
- 第十课独立 API 验收计入 M4 的 2/20，该课交付时项目为 23%；余 18% 继续完成持久关联、时段标签与可恢复提醒。当时学习为 300/13、约 23%，时间与 Tool Calling 均为 0；随后用户参考时间练习后学习更新为 350/1300、约 27%，第九课 Tool Calling 专项仍待反馈。
- 用户已确定裸“周三/周四”按原消息所在自然周（周一至周日）定位，即使已过去也不自动跳下周；“周三前”沿用该日期，未配置钟点时不补 23:59。日期、一周、月份或年份可以有自己的日期/范围精度，缺钟点不等于日期无法确定。详见[时间解释规则与后续解析方案](../docs/TIME_POLICY.md)；第十课 `/time/normalize` 不支持这些扩展，第十一课另以模型结构化候选和程序日历计算实现。
- [第十一课：时间精度与模型意图](../lessons/11_README.md) 通过 Pydantic 判别联合区分分钟、日期、日期范围和待确认，模型提取结构化意图，Python 计算日/周/月/年；新增只读 `POST /time/interpret`。9 项新测试加旧 64 项共 73 项通过；真实 DeepSeek、临时 TCP 的 9 条表达及真实 HTTP CLI 已核对，旧接口保留、合成库未变，记录见 [11_example.json](../lessons/results/11_example.json)。`--demo` 是预设意图，不能证明模型理解；一般语义仍依赖模型；用户已正确核对日期与时刻字段，独立实现待验，项目和学习分不变。

## 开始阅读

集中查看：[智能体开发核心技术栈、当前热度与个人学习看板](../docs/AGENT_TECH_STACK.md)。包含 SQLite 的定位、13 个学习模块、你的实践证据及后续求职工程补充。

1. [求职技术栈、项目代码位置与完成证据](../docs/JOB_SKILL_MAP.md)
2. [加速模块一：主 Agent 与持久化记忆](../docs/MODULE_01_AGENT_MEMORY.md)
3. [第一课：运行模型调用](../lessons/README.md)
4. [第二课：Pydantic 与结构化输出](../lessons/02_README.md)
5. [第三课：Tool Calling 与本地保存](../lessons/03_README.md)
6. [第四课：LangGraph 状态与流程](../lessons/04_README.md)
7. [第五课：FastAPI 与 HTTP 客户端](../lessons/05_README.md)
8. [第六课：SQL、SQLite 与待办持久化](../lessons/06_README.md)
9. [第七课：将 SQLite 接回工具、图与 HTTP](../lessons/07_README.md)
10. [第八课：让 Agent 查询待办](../lessons/08_README.md)
11. [第九课：通过自然语言完成待办](../lessons/09_README.md)
12. [第十课：时间归一化与歧义预览](../lessons/10_README.md)
13. [第十一课：时间精度与模型意图](../lessons/11_README.md)
14. [项目方案与验收进度](../docs/PROJECT_PLAN.md)
15. [技术学习记录](../docs/LEARNING_PROGRESS.md)
16. [上游来源与环境核查](../docs/UPSTREAM.md)

自动收件与提醒操作见[模块四讲义](../docs/MODULE_04_AUTOMATION.md)、[邮箱本机配置脚本](../configure_mail.py)与[提醒演示脚本](../module04_demo.py)。

质量核对见[模块五讲义](../docs/MODULE_05_OBSERVABILITY.md)：用[评测入口](../evaluate_assistant.py)分别运行`--mode rules`和`--mode live`，通过`assistant_client.py --traces`或`--trace TRACE_ID`定位节点；Trace只保存运行元数据，不保存原文、Prompt或凭证。

## 第一版路径

第一版从粘贴消息和上传文件的“分类 → 重点角色 → 时间地点 → 保存 → 提醒”扩展到QQ邮箱接入。提醒呈现已确定为 Windows 桌面通知＋提醒列表；QQ IMAP 已有实际登录与部分正文读取证据，真实附件、增量同步与延迟完整验收仍待补。微信、QQ、BOSS 直聘的自动接入单独验证；如有安卓设备，可实验系统通知采集。

QQ 邮箱使用 IMAP 授权码连接；个人微信、QQ、BOSS 直聘的全部聊天与附件不能预先承诺自动获取。通知显示“[文件]”时只记录待补充附件，不能标记为已下载或已分析。

开发按完整模块加速推进：同批交付可运行链路、核心技术用途与关键代码，配一次综合运行/修改/排错练习；用户反馈与下一批工作可在同轮推进。

模块五交付时验收记录（历史8条，2026-10-16）：[真实记录](../docs/results/module05_example.json)包含12项检查、17条断言；实际Uvicorn进程12700停止后由新进程24528恢复同一Trace及Span，坏PDF定位到document.extract，临时服务和评测数据库已核验清理。模块五交付时185项测试全部通过；项目53%、学习400/1300约31%，当前主线预计2–4次。真实QQ实际登录与部分正文读取已有证据，真实附件、增量同步与延迟完整验收待补。
