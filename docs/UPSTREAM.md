# 上游来源与环境

核查日期：2026-10-15。

## 固定源码

- 仓库：https://github.com/JoshuaC215/agent-service-toolkit
- 提交：`88ebfa1fadd1a207aa92115635abf7ed20d66a88`
- 固定源码：https://github.com/JoshuaC215/agent-service-toolkit/tree/88ebfa1fadd1a207aa92115635abf7ed20d66a88
- 下载入口：https://codeload.github.com/JoshuaC215/agent-service-toolkit/zip/88ebfa1fadd1a207aa92115635abf7ed20d66a88
- 原始存档：`.upstream/agent-service-toolkit-88ebfa1fadd1a207aa92115635abf7ed20d66a88.zip`
- 存档 SHA256：`190DF9C5350A83D638B711F76B53AF46985CD556DB27D25449AC5160E205C40D`
- 解压位置：`agent-service-toolkit/`
- 上游许可证：MIT，原有 `LICENSE` 已保留。

最初 `git clone` 遇到 GitHub 443 连接超时，改用 GitHub 官方 codeload 下载固定提交。当前是源码快照，不包含 `.git` 与提交历史。哈希用于记录本次下载内容，不是独立的发布签名验证。

## 已核查入口

- `pyproject.toml`：Python `>=3.12,<3.15`，依赖与锁文件配置。
- `src/agents/agents.py`：Agent 注册与选择。
- `src/agents/chatbot.py`：简单模型调用示例。
- `src/agents/interrupt_agent.py`：Pydantic 与结构化输出、人工中断示例。
- `src/service/service.py`：FastAPI 服务、启动生命周期、调用与健康检查。
- `src/schema/schema.py`：现有聊天请求与响应协议；业务消息/事项需要另行设计。
- `src/memory/sqlite.py`：checkpoint 使用 SQLite，但长期 store 为 InMemoryStore。
- `src/streamlit_app.py`：现有聊天界面，需要扩展消息和事项视图。
- `src/core/settings.py`：提供 `USE_FAKE_MODEL`；可用于基础连通验证，不能代替真实抽取质量验证。

## 本机环境

- 工作区：`D:\Student_Assistant`，Windows PowerShell。
- 默认 Python：`C:\Python310\python.exe`，3.10.6，不符合上游版本要求。
- 另有 Python：`D:\anaconda\python.exe`，版本为 3.13.9；已用于创建根目录独立教学 `.venv`。
- Git 已安装；未发现 `uv` 命令。
- 已安装首课 `langchain-openai==1.3.3`、`python-dotenv==1.2.2`；完整教学环境版本见 `lessons/requirements.lock.txt`。这不是工具包完整安装；后续完整服务使用工具包自身锁文件与环境。
- 用户已在本机配置 DeepSeek 凭证，第二课已使用本机配置完成真实推理；仅核查配置是否存在，不展示凭证值。尚未初始化本项目 Git 仓库。
- 已验证首课预览、缺配置退出、SDK 模拟请求/响应、第二课 schema 校验和真实推理；完整工具包、上游测试套件和消息接入尚未运行。
- 第三课已验证真实工具调用与本地保存；6 项离线测试使用临时目录与模拟模型，真实运行产物位于 `local-data/`，固定示例位于 `lessons/results/03_example.json`。
- 第四课教学环境已增加 `langgraph==1.2.11`，主包版本与上游锁文件一致，其他教学依赖以 `lessons/requirements.lock.txt` 为准；当前共46个安装包，通过 pip check。
- 安装时原教学锁的 `websockets==17.2` 与 LangGraph SDK 要求的 `<17` 冲突，仅放开该传递依赖后解析为16.1.1，其余既有冻结版本保留；前三课原锁存档位于 `.upstream/lesson03-requirements.lock.txt`。
- 第四课已真实验证 LangGraph 模型/工具流程，记录位于 `lessons/results/04_example.json`。完整工具包与其上游测试仍未安装运行。
- 第五课增加fastapi0.141.0与uvicorn0.52.0，教学环境共51包并通过依赖检查；完整版本见教学锁文件。真实HTTP+DeepSeek验证通过，临时验证服务已停止。
- 已按用户要求给课程函数及测试补中文说明，保留用户summary与report。测试预期同步到report路径，不改上游源码。
- 已核对存档 SHA256、全部 129 个源码文件与存档内容一致，并检查 5 份项目文档的本地相对链接。
