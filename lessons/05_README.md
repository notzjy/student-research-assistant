# 第五课：FastAPI，把 Agent 接成 HTTP 服务

学习记录：用户已新增 `/tasks/count` 并确认服务与客户端正常；当前进入 [第六课：SQL 与 SQLite](06_README.md)。下面保留第五课的运行步骤供复习。

## 本次交付与技术位置

现在通过客户端发送 JSON 通知，服务端调用第四课 LangGraph，再返回可核对的工具执行结果。保留用户已添加的 `report` 节点，继续复用第三课 JSON 待办文件。

| 文件 | 本课用途 |
| --- | --- |
| `05_fastapi.py` | FastAPI 服务、请求与响应模型、路由、错误处理和启动入口。 |
| `05_client.py` | HTTP 客户端，发送通知并查询待办。 |
| `04_langgraph.py` | 已有 Agent 流程，新增中文函数说明，保留用户节点改动。 |
| `03_tool_calling.py` | 已有工具、文件保存与模型配置。 |

本次新增代码的函数均有中文说明，关键步骤有行注释；前三课与既有测试也已补齐函数说明。

## 第一步：启动服务端

在 `D:\Student_Assistant` 的 PowerShell 终端中运行：

```powershell
.\.venv\Scripts\python.exe .\lessons\05_fastapi.py
```

脚本读取现有 `.env`，使用 Uvicorn 启动单进程服务，默认地址 `http://127.0.0.1:8000`。保持这个终端运行。

如果端口已占用，可指定其他端口，例如 `--port 8001`；客户端地址也相应改为该端口。

浏览器打开：

- `http://127.0.0.1:8000/health`：应看到 `{"status":"ok"}`。
- `http://127.0.0.1:8000/docs`：自动生成的接口文档，可点 Try it out / Execute 发起请求。

验证阶段的临时服务已停止；当前需要用上面的命令启动，不会有隐藏服务占用端口。

## 第二步：从客户端发送通知

另开一个终端，仍在项目目录：

```powershell
.\.venv\Scripts\python.exe .\lessons\05_client.py
```

客户端发送 `POST /messages`，正文是：

```json
{"text":"明天下午三点在实验楼302开组会；周五前交综述，附件是两篇参考论文。"}
```

之后自动发送 `GET /tasks`，查看本地记录。客户端只提交通知，模型凭证由服务端读取，不需要放到 HTTP 正文里。

2026-10-15 已真实启动 HTTP 服务并调用 DeepSeek，验证结果：

- `/health`、`/docs`、`/openapi.json` 返回 200。
- `POST /messages` 返回 200、`execution_status=completed`，路径为 model/tools/report/model。
- 两个工具结果都是 exists，复用了已保存的组会和综述待办；`GET /tasks` 返回200。
- 纯空白 text 返回422，在调用模型前被拒绝。

真实请求与响应保存在 [results/05_example.json](results/05_example.json)。

## 三个接口分别做什么

| 方法与路径 | 请求内容 | 返回内容 | 是否调用模型 |
| --- | --- | --- | --- |
| GET `/health` | 无需正文 | 服务存活状态 | 否 |
| POST `/messages` | `{"text":"通知原文"}` | 答复、节点顺序、实际工具结果 | 是 |
| GET `/tasks` | 无需正文 | 待办数量与列表 | 否 |

`GET` 用于这里的查询，`POST` 用于提交待处理通知。HTTP 状态200表示请求成功处理；422表示正文不符合要求。具体工具是否成功，继续查看响应中的 `tool_results`。

## 关键代码及其意义

```python
class MessageRequest(BaseModel):
    """声明接口接收的 JSON 正文，检查通知是否为非空文本。"""
    text: str = Field(min_length=1)
```

完整实现还会去掉首尾空白、限制最大长度和拒绝额外字段。FastAPI 根据参数类型把 JSON 解析为 Pydantic 对象，不用自己读取 HTTP 字节。

```python
@api.post("/messages", response_model=MessageResponse)
def process_message(payload: MessageRequest) -> MessageResponse:
    """接收校验后的通知，运行图并返回实际执行结果。"""
```

这里装饰器把方法、URL 与 Python 函数关联；`payload.text` 是通知原文，函数内部调用已有 `run_graph`。`response_model` 声明响应字段并生成接口文档。

响应中的 `execution_status` 有三种：completed表示本次工具结果均成功或已存在，tool_error表示有工具失败，no_tools表示没有执行工具。`tool_results` 从 `ToolMessage` 读取，不根据模型结语猜测。

客户端核心调用：

```python
response = client.post("/messages", json={"text": args.message})
```

这是一次真实 HTTP 请求。服务端的 print 会出现在启动服务的终端，客户端只能看到 HTTP 响应中的字段。

## 用户练习：新增一个查询接口

目标：访问 `/tasks/count` 时，只返回待办数量。

在 `create_app` 函数的 `return api` 前加入下面代码，与 `get_tasks` 等函数保持同级缩进：

```python
    @api.get("/tasks/count", summary="查询待办数量")
    def count_tasks() -> dict[str, int]:
        """复用本地待办查询，仅返回数量，不调用模型。"""
        return {"count": get_tasks().count}
```

保存，在服务端终端按 Ctrl+C 停止，再重新执行启动命令。浏览器访问 `http://127.0.0.1:8000/tasks/count`，或刷新 `/docs` 执行新接口。核对数量与 `/tasks` 一致，并观察服务器没有额外模型调用。

这次练习掌握的是：新增路由、定义处理函数、复用已有逻辑、返回 JSON；函数说明也会出现在接口文档中。

## 当前实现的边界

- 同步图使用普通 def 接口，由 FastAPI 放在线程池运行；请求共享锁，避免同一服务进程同时读改写 JSON。
- 本课运行一个进程；另一终端的第三/四课脚本或其他服务进程不共享锁。后续改用数据库事务处理持久化并发。
- 模型超时返回504，模型服务错误返回502，图步数上限返回409，缺模型配置返回503。失败前已经保存的记录仍可能存在，应通过 `/tasks` 核对。
- 本课仍是教学服务，尚未注册进完整上游服务，没有接入真实消息渠道或设置提醒。

## 验证与上游对应

第五课8项离线接口测试已通过，覆盖输入校验、保存和去重、无工具、工具失败、配置与错误映射，使用临时目录和模拟模型。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s lessons -p "test_05_fastapi.py" -v
```

上游 `src/service/service.py` 同样使用 FastAPI，`src/run_service.py` 启动服务；本课把最小调用链先展开。接下来将拆分业务模块，学习 SQL 与持久化，再逐步迁入工具包。

资料：[FastAPI 请求正文](https://fastapi.tiangolo.com/tutorial/body/)、[响应模型](https://fastapi.tiangolo.com/tutorial/response-model/)、[同步与异步接口](https://fastapi.tiangolo.com/async/)。
