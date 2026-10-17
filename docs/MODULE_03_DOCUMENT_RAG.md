# 模块三：研究文件RAG、Embedding与向量检索

更新：2026-10-16。代码与代理验收已完成，用户已解释三者分工，记概念25分；实际上传、检索与引用核对仍待用户反馈。沿用8002主服务、原DeepSeek配置和业务库；本轮不需要第二个API Key。

本轮交付：真实文件上传与原件保存 → 正文解析 → 保留出处的分块 → 本地语义Embedding → Qdrant持久向量检索 → DeepSeek基于证据回答 → 引用检查。主Agent也已新增 `search_documents` 工具，查询同一个索引。

## 先做一次完整实验

工作目录为 `D:\Student_Assistant`。根 `.venv` 已装本模块依赖并实际下载中文模型。换机器时使用根 `requirements.lock.txt` 安装依赖；首次索引会下载约95MB的Embedding模型，后续使用 `local-data/embedding_cache` 缓存。启动或查询文档列表不下载模型。

先停止原8002服务，再用原命令重启以加载新增接口：

```powershell
.\.venv\Scripts\python.exe .\run_assistant.py --db .\local-data\module01_tasks.sqlite3 --checkpoints .\local-data\module01_memory.sqlite3
```

另开终端，上传两份实际文件并建立索引：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --index-file .\examples\module03_research_note.md
.\.venv\Scripts\python.exe .\assistant_client.py --index-file .\examples\module03_student_life.md
```

这两份是本项目原创的**合成教学材料**，不是用户真实论文。[研究材料](../examples/module03_research_note.md)含检索实验指标；[学生生活材料](../examples/module03_student_life.md)用于观察检索能否区分主题。客户端读取本地文件字节并上传，不把文件名当作已经取得正文。

首次返回 `status="indexed"`，相同字节重复上传返回 `exists`。本次实际研究文件生成3个片段、生活文件生成2个片段。相同字节改文件名重传也复用首份文档，不重复生成向量。

先只检索，观察系统找到什么：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --search "研究实验对检索覆盖率设定了什么最低要求？" --top-k 3
```

本次真实结果的第一名来自研究文件，余弦相似度约0.5345，包含检索验收指标；问题使用“覆盖率”，材料写的是“Recall@5”。查看 `hits[].text`、`filename`、`page`、`start/end`，它们是真实索引中的正文与位置。

再让模型依据检索片段回答：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --ask "检索验收要求Recall@5至少达到多少？"
```

本次实际回复：

```json
{
  "status": "answered",
  "answer": "检索验收要求Recall@5不得低于0.85。",
  "citations": [
    {
      "filename": "module03_research_note.md",
      "page": 1,
      "quote": "检索验收要求：Recall@5不得低于0.85"
    }
  ]
}
```

上面只摘录关键字段；真实响应还含文档/片段ID、引用位置和来源绑定。**0.85是合成材料里设定的实验目标，不是当前系统测出的召回率。** 相似度分数也不是回答正确的概率。

问一个资料没有提供答案的问题：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --ask "项目负责人的银行卡密码是多少？"
```

本次得到 `status="unknown"`、空引用，明确说明现有片段不足。检索可能找到提到银行卡的片段，是否足以回答仍需要判断；不能把“有相关词”当成“文件提供了答案”。

查看已保存文档：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --documents
```

停止并用原命令重启服务，再执行相同检索，索引仍应可用。本次代理验收实际更换了服务进程，并核对命中同一个稳定 `chunk_id`，不是只保留内存状态。

## 这次究竟在学哪些技术

| 技术 | 项目里的作用 | 实际代码 |
| --- | --- | --- |
| 文档解析 | 从txt/md/PDF取得真实正文，保留PDF页码 | [rag_documents.py](../agent-service-toolkit/src/student_assistant/rag_documents.py) 的 `parse_document()` |
| Chunk与Overlap | 将文件变成可定位、可检索的片段，降低边界切断上下文的影响 | 同文件 `chunk_pages()` |
| Embedding | 将片段和问题映射成同一语义空间里的512维向量 | [rag_embeddings.py](../agent-service-toolkit/src/student_assistant/rag_embeddings.py) 的 `LocalEmbedder` |
| 向量数据库 | 用余弦相似度查候选，持久保存文档向量 | [rag_store.py](../agent-service-toolkit/src/student_assistant/rag_store.py) 的 `complete_index()` / `search()` |
| RAG | 回答前检索证据，把选中的原文交给模型生成答案 | [rag_runtime.py](../agent-service-toolkit/src/student_assistant/rag_runtime.py) 的 `_build_answer_graph()` |
| 引用检查 | 接受本次真实命中ID及逐字quote，由程序补齐文件、页码、位置与来源 | 同文件 `_checked_answer()` |
| 检索工具 | 让主Agent把文档检索作为可以选择的真实能力 | [rag_tools.py](../agent-service-toolkit/src/student_assistant/rag_tools.py) 的 `create_retrieval_tools()` |

本次选用 `FastEmbed==0.9.0`、CPU版ONNX Runtime、`BAAI/bge-small-zh-v1.5` 和 `qdrant-client==1.19.1` 的磁盘local模式。BGE中文模型输出512维，查询端按模型说明加中文检索前缀；文档端直接编码正文。这里使用内置约95MB模型，不把它称作int8版本。[FastEmbed官方介绍](https://qdrant.tech/documentation/fastembed/)、[BGE模型说明](https://huggingface.co/BAAI/bge-small-zh-v1.5)

## 看懂索引与回答两条路径

索引路径里的关键步骤：

```python
# 解析后的每页仍保存完整正文。
pages = parse_document(filename, data)

# 每个片段包含page/start/end，能够从页面正文逐字复现。
chunks = chunk_pages(pages, chunk_size=400, overlap=80)

# 真实本地模型推理，把每个片段编码为512个浮点数。
vectors = embedder.embed_documents([part["text"] for part in chunks])

# 先写向量，再提交片段与indexed状态；失败时清理并保留原件。
store.complete_index(document_id, pages, chunks, vectors)
```

这是关键流程摘录，实际异步代码在 `RagEngine.index()`。窗口为400字符，步长为320字符，相邻窗口重叠80字符。索引保留原件和每页全文；分块用于检索。字符窗口是本轮便于观察的实现，仍受Embedding模型512 tokens上限约束，不保证所有特殊文本都能完整编码；后续可以比较token/段落切分。

通知分析继续完整发送消息原文。研究文件先建立片段索引，提问时找到相关片段，再把候选正文和ID发给回答模型。这是[RAG的检索增强路径](https://docs.langchain.com/oss/python/deepagents/retrieval)，不在此过程中更新DeepSeek的模型参数。

回答路径：

```text
问题 → 本地问题向量 → Qdrant查候选 → DeepSeek回答草稿 → 代码检查引用
                           ↓没有候选                  ↓引用不合法
                         unknown                    unknown
```

用户概念题反馈已核对：切片由 `chunk_pages()` 完成，Embedding 将已有片段和问题转换为向量；Qdrant保存向量并检索候选，程序取回对应正文。`--ask` 的检索由固定流程先执行；主Agent使用 `--message` 时可以选择 `search_documents` 工具，再由程序调用 Qdrant。DeepSeek不会自动看到数据库或直接运行项目代码。

`top_k` 是候选数量上限；相似度阈值可能让结果少于这个数量。多取候选可以增加覆盖，也可能增加无关内容和模型输入量。当前阈值0.35是起始设置，不能代替后续标注集评测。

## SQLite和Qdrant分别存什么

| 存储 | 默认位置（本次练习） | 内容 |
| --- | --- | --- |
| 业务SQLite | `local-data/module01_tasks.sqlite3` | 原消息、身份规则、事项、文档/片段元数据、来源关联 |
| 会话checkpoint SQLite | `local-data/module01_memory.sqlite3` | 主Agent聊天历史及执行状态 |
| 原件目录 | `local-data/module01_tasks_rag/files/` | 真正上传的文件字节，按SHA256保存 |
| Qdrant向量索引 | `local-data/module01_tasks_rag/vectors/` | 真实向量点，用于相似度检索 |
| Embedding模型缓存 | `local-data/embedding_cache/` | 已下载的本地模型权重与tokenizer |

Qdrant和业务SQLite没有共享事务。当前实现先确保SQL文档状态为 `indexed` 才允许命中，失败清理残余向量和片段；不能把它描述成跨库原子事务。当前是单进程local模式，不能同时启动两个进程写同一向量目录。更换模型、查询前缀、维度或索引目录时，会拒绝混用已有配置，需要独立索引或迁移流程。

## 把真实原件绑定到消息里的附件

先导入[模块三来源消息](../examples/module03_notice.json)：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --ingest .\examples\module03_notice.json
```

保持示例来源字段不变时，返回消息ID `f992ddd69f3f075dfbd1d937`。如果修改过来源字段，使用实际返回的ID。附件序号从0开始，随后显式绑定两份原件：

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --index-file .\examples\module03_research_note.md --message-id f992ddd69f3f075dfbd1d937 --attachment-index 0
.\.venv\Scripts\python.exe .\assistant_client.py --index-file .\examples\module03_student_life.md --message-id f992ddd69f3f075dfbd1d937 --attachment-index 1
.\.venv\Scripts\python.exe .\assistant_client.py --message-id f992ddd69f3f075dfbd1d937
```

原始 `source_message.attachments` 仍保留当时消息声明的 `mentioned`，原始内容与去重指纹不变。当前详情的 `attachments` 叠加真实文件状态，成功索引后为 `indexed`，带 `document_id`。文件实际取得但解析/索引失败时是 `available`，文档状态为 `failed`，允许重试。未上传的附件继续是 `mentioned`。

已有相同原件可以增加来源绑定，上传顺序先索引后绑定也可复用。绑定必须由你提供消息ID和附件序号，程序不按文件名猜来源。同一附件已绑定另一份原件时拒绝覆盖，消息修订仍留后续处理。

消息按来源消息ID独立保存，文件按字节SHA256复用原件和向量。同一文件由两个人分别发来时，两条消息都保留，各自关联同一份文件索引，因此既节省重复向量化，也保留“谁在什么时候发来”的来源信息。

## 主Agent怎样使用检索

```powershell
.\.venv\Scripts\python.exe .\assistant_client.py --thread-id research-chat --message "请检索已上传的星河计划实验方案，告诉我Recall@5的最低要求，不要新增待办。"
```

本次实际 `tool_names` 包含 `search_documents`，真实检索结果通过 `ToolMessage` 回传，主Agent回答0.85。它与 `--search` / `--ask` 使用同一个索引。

**验证边界需要区分：** `--ask` 对应 `/documents/ask`，执行当前命中ID与逐字quote校验；主Agent的自由文字回复目前依靠Prompt要求引用，尚未经过相同的机械检查。即使引用位置正确，也不能保证整段答案的推理一定得到引用支持，后续语义评测继续检查这一点。

## 一项综合练习

1. 索引两份材料，先 `--search`，再 `--ask`，核对答案里的0.85确实出现在引用原文中。
2. 同一个检索问题分别加 `--top-k 1` 和 `--top-k 5`，比较候选数量与内容。数量是上限，不保证总有5个。
3. 问一个材料未提供答案的问题，观察 `unknown` 与空引用。
4. 用自己的话解释：Embedding、Qdrant、DeepSeek在这条链路中各负责哪一步？为什么相似度0.53不代表答案有53%的正确概率？

提交关键结果和解释即可，无需粘贴全部片段。核对后推进自动接入与提醒，已学过的模型、工作流、存储和幂等继续复用。

## 实际验收与当前范围

- 新增[16项专测](../tests/test_document_rag.py)与旧99项共115项通过；`pip check`无冲突。专测使用假Embedding/回答模型、真实Qdrant/SQLite/ASGI和真实PDF解析，验证两页PDF、offset/overlap、原件持久化、失败重试、引用幻觉拒绝及主Agent检索工具。
- [真实模型验收](results/module03_example.json)有12项检查、11条断言：真实CPU BGE512、本地磁盘Qdrant、真实DeepSeek及客户端/TCP；两份合成材料、同义检索、带出处回答、资料未知、来源绑定、重复字节复用、原件哈希及真实进程停止再启动后恢复均通过。
- 真实Embedding曾实测单位范数约1，同义研究query与指标片段得分0.557、与宿舍片段0.287；这只是小样例，尚非完整检索评测。缓存下载后，真实服务验收使用HF离线模式读缓存，DeepSeek仍实际远程调用。
- 验收只使用合成数据与专用临时存储，进程与临时库/向量/原件已清理，未改用户数据；本地Embedding缓存保留复用。
- 支持UTF-8 txt/md与可提取文本的PDF，单文件≤10MB、≤200页、提取正文≤200000字符。扫描PDF尚无OCR，加密PDF和不支持类型会明确失败并保留可接收的原件状态。
- Embedding在本机推理；回答所需候选正文会发给已配置的DeepSeek。真实消息渠道、自动附件下载、文件正文分类、持久提醒和界面仍按后续范围推进，当前附件类别仍由消息分类继承。
- 项目45%：M2附件真实取得/索引状态流转新增1%，消息修订1%仍待；M6研究文件入口与持久问答/主Agent集成新增4%。学习375/1300≈29%保持，本轮用户尚未提供RAG实践证据；核心主线预计剩余3–5次有效提问/反馈。
