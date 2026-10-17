# 模块六：可操作界面与交付方式

更新：2026-10-17。模块六把已有的消息、研究资料、提醒、追踪和对话接口接到本地 Streamlit 界面。界面沿用8002主服务，不另外运行一份Agent，不自己打开业务库，也不自己建立邮箱或提醒后台循环。

## 最新验收与学习状态（2026-10-17）

项目进度：`████████████░░░░░░░░` **60%**，阶段[5,7,19,7,16,0,6]。技术栈学习：`██████░░░░░░░░░░░░░░` **31%**，400/1300。

预计主线还需约 **1–3次有效提问或反馈**，用于QQ真实附件、增量同步与延迟完整联调、本轮界面综合练习及补缺；原始多平台接入目标尚未完成。项目与学习计分详见[项目记录](PROJECT_PLAN.md)、[学习记录](LEARNING_PROGRESS.md)和[加速路线](ACCELERATED_ROADMAP.md)。代码交付与代理测试不直接增加个人掌握分；Streamlit、Git/部署和评测仍0，用户关于旧队列的正确解释仅补充守卫职责理解，时间50保持。

[本轮实际记录](results/module06_example.json)的16项检查全部通过，覆盖实际HTTP、Streamlit AppTest、DeepSeek提取与问答、缓存BGE512/Qdrant及[独立Edge截图](results/module06_dashboard.png)。截图中文、重点角色、具体时刻和地点已目视核对。实际Uvicorn进程28216停止后由26300启动，双库、文件索引、Trace及4条线程历史仍在，查询返回同一研究片段。临时验收数据已清理，未连接真实QQ或新发Windows弹窗。

218项回归（根145＋lessons73）通过，新增20项为HTTP适配器9、完成接口3、界面8；96包锁定依赖的pip check通过。M1界面启动+2%、M4时间地点标签+3%、M6本地演示/Git初始化+2%，项目从53%升60%。Git仅初始化main，无暂存/提交/远程；Docker无CLI，仅静态检查，镜像构建与容器运行未验。用户按下文综合练习提供运行和解释证据后，再更新掌握分。

## 本次能做什么

| 导航页 | 可直接操作的功能 | 会改变数据或调用模型的入口 |
| --- | --- | --- |
| 消息与日程 | 按类别、重点联系人筛选，查看原文、角色、附件线索和事项；用颜色与文字区分截止、具体时刻、日期、日期范围和地点 | “导入并分析消息”调用消息工作流；“标记完成”完成选中事项 |
| 研究资料 | 查看已取得的文件，准备下载原件，上传txt/md/文本PDF，检索和有引用的问答 | 上传建立索引；“仅检索片段”生成查询向量；“基于文件回答”还调用DeepSeek |
| 提醒与角色 | 查看提醒与邮箱状态，保存用户确认的精确角色和提醒规则 | 保存表单、取消提醒、明确点击“同步邮箱并分析邮件” |
| 运行追踪 | 查看最近请求及单次请求的Span、耗时、错误类型 | 查询不调用模型，也不建立额外Trace查询记录 |
| 对话助理 | 连续对话复用同一thread_id，查询线程，显式恢复未完成流程 | “发送”或“恢复未完成流程”调用Agent；“新建对话”更换会话上下文 |

橙色“截止”、蓝色“具体时刻”、紫色“地点”等标签同时有文字说明；只有日期的“周三”显示日期和“钟点未知”，不会为了画界面补出一个`at`。日期范围保留起止日期和原精度。文件名`reference-a.pdf`仍只是附件线索，只有真实上传成功才可下载和检索正文。角色通过来源、账号和发送者精确标识匹配，显示昵称不能代替身份确认。

## 本机先跑起来：两个终端

工作目录为`D:\Student_Assistant`。已有8002服务时，在它原来的终端按Ctrl+C，再用同一组数据库启动；不需要同时启动两个占用同一端口、业务库或Qdrant目录的后端。

终端一运行主服务：

```powershell
.\.venv\Scripts\python.exe .\run_assistant.py --db .\local-data\module01_tasks.sqlite3 --checkpoints .\local-data\module01_memory.sqlite3
```

它默认监听`127.0.0.1:8002`。保留现有DeepSeek本机配置；无需把密钥填到界面或聊天里。后端是否自动收邮件仍由现有本机配置决定，打开界面本身不会额外启动收件循环。

终端二运行界面：

```powershell
$env:ASSISTANT_API_URL = "http://127.0.0.1:8002"
.\.venv\Scripts\python.exe -m streamlit run .\assistant_dashboard.py --server.address=127.0.0.1 --server.port=8501 --browser.gatherUsageStats=false
```

然后用浏览器打开`http://127.0.0.1:8501`。两个终端保持运行。首次进入默认页只通过HTTP读取消息，切换导航仅执行选中的页面；不会因为绘制五个页面而自动做五批操作。关闭界面不关闭8002服务，也不停止后端已经配置好的后台提醒。

如果虚拟环境尚未安装本次新增依赖，再在项目目录执行一次：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.lock.txt
```

这是本项目新建的最小界面，复用上游的Streamlit技术选择；不能据此声称上游原始Streamlit、全量demo和42项运行直接依赖均已启动。

## 这次学习的技术体现在哪里

| 技术与代码位置 | 实际职责 | 与已学技术如何连接 |
| --- | --- | --- |
| [assistant_dashboard.py](../assistant_dashboard.py)的`main()`与各页面函数 | 根据当前导航绘制界面，只在明确提交后执行对应操作 | 把原来命令行的HTTP请求变成按钮和表单 |
| 同文件`chat_page()`中的`st.session_state` | 同一浏览器会话连续发送时保留thread_id和当前展示结果 | 后端仍使用LangGraph checkpoint保存线程；两者生命周期不同 |
| [dashboard_api.py](../dashboard_api.py)的`DashboardAPI` | 统一调用现有FastAPI接口，处理文件字节、错误和Trace编号 | JSON/Pydantic验证、业务事务和Agent执行仍在后端 |
| [dashboard_views.py](../dashboard_views.py)的`event_badges()` | 把后端时间精度和地点变成颜色与文字标签 | 展示已有时间计算结果，不重新让模型猜日期 |
| [service.py](../agent-service-toolkit/src/student_assistant/service.py)的`POST /tasks/{task_id}/complete` | 完成明确选中的事项，并沿用存储层的未投递提醒取消机制 | 固定按钮直接调用业务接口，无需DeepSeek选择工具 |
| [Dockerfile](../Dockerfile)、[compose.yaml](../compose.yaml) | 定义运行环境及后端、界面的启动方式 | 给已有程序提供可复现启动约定；本机尚未执行容器验证 |
| [.gitignore](../.gitignore)与本地Git仓库 | 区分源代码、配置模板与运行数据，便于检查版本差异 | 先检查变更，再决定哪些内容进入一次提交 |

界面请求链为：浏览器操作 → Streamlit中的HTTP适配器 → FastAPI → 原业务存储或Agent → JSON结果 → 页面。界面进程不加载`.env`，不导入业务存储或模型模块。模型密钥继续由后端使用；上传的文件通过HTTP发送给后端处理。

### 为什么模型调用一定放在提交判断里

Streamlit会在用户交互后重新运行页面脚本，`st.form`则把一组输入集中在一次提交中，参见[官方表单说明](https://docs.streamlit.io/develop/concepts/architecture/forms)。下面是项目中实际使用方式的简化摘录：

```python
with st.form("document_question"):
    question = st.text_area("检索词或研究问题")
    ask = st.form_submit_button("基于文件回答（调用模型）")

if ask and question.strip():
    # 只有本次确实提交表单，才向后端请求一次研究问答。
    result = api.request("POST", "/documents/ask", json={"question": question.strip()})
    st.session_state["research_result"] = result

if "research_result" in st.session_state:
    # 页面重新绘制时展示上一次结果，不重新请求模型。
    render_research_result(st.session_state["research_result"])
```

关键是区分“显示上一次结果”与“再执行一次操作”。如果把`api.request(...)`直接放在提交判断外，改变筛选条件、切换导航或点击其他组件时都可能再次请求模型。HTTP超时也不代表后端一定没写成功，因此界面错误会提示先查询实际状态再重试；表单并不代替后端的幂等、版本检查和队列状态。

### 为什么session_state不能替代checkpoint和数据库

`chat_page()`中保留线程的关键代码是：

```python
st.session_state.setdefault("thread_id", uuid4().hex)

if st.button("新建对话"):
    # 只有用户明确新建会话，才切换到另一个线程编号。
    st.session_state["thread_id"] = uuid4().hex

thread_id = st.session_state["thread_id"]
```

同一个界面会话连续发送时复用该编号，后端依此读取对应checkpoint。新建对话会清空本地聊天展示并换号，但不会删除事项、原文或文件。浏览器会话结束或界面进程重启后，`session_state`可能消失；已经写入的SQLite、checkpoint和原件仍由后端保存。长期事实查询继续使用业务接口或工具，不能只依赖聊天窗口里的文字。Streamlit会话状态的连接边界见[官方Session State说明](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state)。

## Git：先会查看，再决定提交什么

本项目根目录已执行`git init -b main`，已有本地`main`分支；尚未暂存、提交或设置远程仓库。不会替用户编造Git姓名、邮箱，也不需要把项目公开到GitHub才能学习版本管理。

先运行只读检查：

```powershell
git status --short
git check-ignore .env local-data/module01_tasks.sqlite3
git diff -- assistant_dashboard.py dashboard_api.py dashboard_views.py
```

没有首次提交时，新文件显示为`??`，普通`git diff`不会显示未跟踪文件内容；这不意味着代码为空。可以先打开文件阅读，选择一个具体变更暂存后，再通过`git diff --cached`查看将进入提交的内容。[Git官方文档](https://git-scm.com/docs/git-diff)说明了工作区、暂存区和提交之间的比较范围。例如下面三条命令需要在自己检查代码后执行，代理没有代为执行：

```powershell
git add -- assistant_dashboard.py dashboard_api.py dashboard_views.py
git diff --cached
git commit -m "Add local assistant dashboard"
```

`add`把选中版本放进暂存区，`commit`记录暂存区快照。上面的提交只演示三个界面文件，尚不包含整个项目运行所需文件；它不能单独作为完整项目交付。Git要求身份时，在本机配置自己的真实信息。`.env`、业务数据库、文件原件和缓存目录由忽略规则排除；`.env.example`保留为字段说明模板。

## Docker：配置已准备，实际运行待验证

当前机器未找到Docker CLI，因此本轮只检查配置和运行路径，**尚未执行镜像构建、Compose启动或容器内端到端验证**。以下命令是安装并启动Docker Desktop之后可执行的步骤，不是本次已成功运行的记录：

```powershell
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail 50 backend
```

运行前停止本机占用8002和8501端口的两个进程，保留现有本机`.env`。构建上下文通过`.dockerignore`排除凭证和运行数据；Dockerfile只复制所需源码、锁文件及上游许可证，不复制`.env`。Compose的`env_file`在运行时仅向后端注入配置；UI容器只接收后端地址，不接收密钥。锁文件中Windows专用辅助依赖使用平台marker，Linux安装时跳过；实际Linux依赖可安装性仍要由构建验收。

| 概念 | 在本项目中的对应物 |
| --- | --- |
| 镜像image | Dockerfile定义的Python、依赖及程序文件 |
| 容器container | 同一镜像分别运行一个后端进程和一个Streamlit进程 |
| Compose服务 | `backend`和`ui`两个服务名称；UI用`http://backend:8002`访问后端 |
| 主机端口映射 | 浏览器仍访问`127.0.0.1:8501`，后端映射`127.0.0.1:8002` |
| 数据卷volume | `assistant-data`挂载到后端`/data`，保存业务库、checkpoint、原件、向量索引及模型缓存 |

容器中的`127.0.0.1`指容器自身，所以UI不能继续用它寻找另一个容器；Compose服务名`backend`提供内部寻址，见[Docker官方网络说明](https://docs.docker.com/compose/how-tos/networking/)。UI不挂载数据卷，业务写入统一通过后端。两个服务在容器内监听`0.0.0.0`，主机发布端口仍限制到本机回环。后端健康检查只确认`/health`可用，不证明模型配置、邮箱或每个功能都正确。

Compose默认关闭QQ邮箱自动收取。Linux容器不支持本项目的Windows桌面弹窗，需要在“提醒与角色”页选择`console`通道；日志里的`submitted`仍只代表已交给通道。若需要实际Windows桌面通知，继续采用前面的本机运行方式。首次在容器中做向量检索还需准备模型缓存或完成模型下载，主机缓存不会因同名路径自动出现在卷中。

可用`docker compose stop`停止本组容器；再次`docker compose up -d`沿用同一数据卷。不要把删除数据卷当成日常重启步骤。本机数据库不会自动迁移进容器卷；使用空卷初次启动时看到空列表是正常现象，不能据此认为Windows本机数据被删除。

## 一次综合练习：从界面走通一条业务链

1. 按前面的两个终端命令启动，进入“消息与日程”，筛选研究消息，展开一条原文。核对两篇附件仍然在，截止、时刻与地点分别显示；没有钟点的日期没有被补成具体时刻。可以使用已有教学消息，不必为了练习重复导入真实邮件。
2. 进入“研究资料”，选择本地已有的[研究笔记示例](../examples/module03_research_note.md)并点击上传，然后填写“星河计划要求Recall@5至少达到多少，平均查询耗时不得高于多少？”并点击“基于文件回答”。查看回答及引用，再改变候选片段数但先不提交，观察没有因此生成另一次问答。示例中设定的答案是0.85和120毫秒，这只是原创教学实验的指标。
3. 进入“对话助理”，发送“这个演示项目叫星河计划，只记住名称，不创建待办”，再发送“刚才的项目叫什么”。核对thread_id没有变化；点“新建对话”后确认编号变化。保留之前的编号，可用`assistant_client.py --thread-id 旧编号 --history`查询后端旧线程，比较浏览器状态与checkpoint保存的范围。
4. 回到消息页，选一个确实已完成的教学事项点击“标记完成”，再切换到其他页并返回。核对完成状态保留；未投递关联提醒由后端取消，已投递记录保留。

反馈时只需要贴这条综合运行链的结果，并解释两句：为什么研究问答请求要放在`if ask`内；为什么`session_state`不能替代业务SQLite或checkpoint。如果某步失败，给页面显示的安全错误及Trace ID即可，账号、原文隐私和授权码继续留在本机。

这一轮聚焦可演示交付；QQ真实附件、增量同步和延迟的完整验收，以及微信、个人QQ、BOSS等其他来源能力，仍按[项目方案](PROJECT_PLAN.md)记录待验证，不因为有界面而计为已接通。
