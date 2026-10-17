# 第六课：SQL、SQLite 与待办持久化

学习记录：用户已确认 `pending_count` 与未完成列表一致；当前进入 [第七课：依赖注入与SQLite接入](07_README.md)。本课保留独立存储示例，供观察SQL与复习。

## 这次学什么，用在哪里

第五课已经通过 HTTP 调用 Agent 并查询 JSON 待办。本课把数据库操作提取到可复用模块：保存一条事项、筛选未完成事项、完成指定事项，以及导入旧 JSON。

**SQL 是操作关系型数据库的语言，SQLite 是本课使用的数据库。** SQLite 将数据存入本地文件，不需要额外启动数据库服务，Python 自带 `sqlite3` 驱动，本课无需安装新依赖。后续提醒程序可以查询未完成事项，Agent 保存工具也将调用这层模块。[Python 官方说明](https://docs.python.org/3.13/library/sqlite3.html)

| 代码或产物 | 用途 |
| --- | --- |
| [task_store.py](task_store.py) | `TaskStore` 集中处理数据库操作；每个函数都有中文说明。 |
| [06_sqlite.py](06_sqlite.py) | 命令行入口，直接调用存储模块，方便观察每一步。 |
| `local-data/lesson06_tasks.sqlite3` | 默认教学数据库；程序退出后数据仍保存在文件里。 |
| [test_06_sqlite.py](test_06_sqlite.py) | 在临时数据库中验证持久化、筛选、更新、重复输入及批量导入。 |

本课专注数据库层，调用链是：`命令行 → TaskStore → SQLite`。第三至第五课仍使用原来的 JSON 文件；下一课将通过明确的存储依赖把 `TaskStore` 接入 Agent 工具与 HTTP。后续再迁入完整工具包；当前不包含提醒调度。

## 第一步：运行数据库示例

在项目目录 `D:\Student_Assistant` 运行：

```powershell
.\.venv\Scripts\python.exe .\lessons\06_sqlite.py
```

它会依次执行：

1. 创建不存在的数据库和 `tasks` 表。
2. 保存“[教学示例] 交综述”和“[教学示例] 参加组会”。
3. 将交综述标成 `completed`。
4. 查询全部事项，再只查询 `pending` 事项。

本机已运行过这个示例，因此再次运行通常返回 `exists`，表示沿用相同记录。`save_results` 是执行保存时的返回，后面的 `completed_task`、`all_tasks` 才反映完成更新后的状态；按输出先后顺序阅读。

仅含教学记录时，最终的关键字段如下，完整实际输出见 [results/06_example.json](results/06_example.json)：

| title | status |
| --- | --- |
| [教学示例] 交综述 | completed |
| [教学示例] 参加组会 | pending |

`pending_tasks` 只应包含参加组会。若你随后完成了组会，筛选结果会相应减少；再次运行演示也不会把已完成任务重新变成未完成。

## 第二步：退出后再查询

```powershell
.\.venv\Scripts\python.exe .\lessons\06_sqlite.py --list
.\.venv\Scripts\python.exe .\lessons\06_sqlite.py --list --status pending
.\.venv\Scripts\python.exe .\lessons\06_sqlite.py --list --status completed
```

每条命令都是一个新 Python 进程，仍能查到前一次的记录，这就是持久化。前面的 JSON 文件也能持久化；这次数据库新增的是 SQL 条件查询、主键约束与事务等能力。

保存一条自己的测试事项：

```powershell
.\.venv\Scripts\python.exe .\lessons\06_sqlite.py --add "投递实习简历" --time-text "周六前"
```

复制返回的 `id`，替换下面的 `实际ID`：

```powershell
.\.venv\Scripts\python.exe .\lessons\06_sqlite.py --complete 实际ID
```

再查询 `--list --status pending` 和 `--list --status completed`，核对这条记录移动到了完成列表。未知 ID 会提示未找到，不会修改其他事项。

## 第三步：对应 SQL 看代码

关系型数据表可以先理解成有固定列的表格：一行是一项待办，`id`、`title`、`time_text`、`location`、`status` 是列。

| SQL 关键词 | 本项目中的作用 | 对应函数 |
| --- | --- | --- |
| `CREATE TABLE IF NOT EXISTS` | 建立不存在的待办表 | `TaskStore.__init__` |
| `INSERT INTO` | 写入待办 | `_insert_task`，由 `save_task` 调用 |
| `SELECT ... FROM` | 查询待办 | `list_tasks` |
| `WHERE` | 限定需要查询或修改的行 | `list_tasks`、`mark_completed` |
| `UPDATE ... SET` | 把某一项改成已完成 | `mark_completed` |

先看 `list_tasks` 中按状态查询的关键代码：

```python
rows = connection.execute(
    "SELECT * FROM tasks WHERE status = ? ORDER BY rowid", (status,)
).fetchall()
```

- `SELECT * FROM tasks`：取出待办表各列。
- `WHERE status = ?`：只取状态符合条件的行。
- `(status,)`：将 Python 的状态值绑定到 `?`，末尾逗号表示单元素元组。
- `ORDER BY rowid`：这个简单表按插入顺序展示。
- `fetchall()`：取回查询得到的全部行；后面用 `dict(row)` 转成前几课熟悉的字典。

传入 `status="pending"`，结果就只有未完成事项。标题、ID、状态等变量都通过参数绑定传入，避免字符串拼接改变 SQL 结构。[官方参数绑定说明](https://docs.python.org/3.13/library/sqlite3.html#how-to-use-placeholders-to-bind-values-in-sql-queries)

完成事项的关键代码是：

```python
connection.execute(
    "UPDATE tasks SET status = 'completed' WHERE id = ?", (task_id,)
)
```

`SET` 表示改成什么，`WHERE` 表示改哪一行。省略这个 `WHERE` 会把所有待办都标成完成；后续提醒程序因此会跳过本来还没做的事情。

## 保存、主键和事务各有什么用

`id TEXT PRIMARY KEY` 声明唯一的记录标识，数据库阻止相同 ID 重复插入。`save_task` 沿用第三课按完整参数生成 ID 的办法；重复保存返回 `exists`，并保留数据库中的完成状态。参数内容稍有变化就可能生成另一条记录，因此它仍不是基于真实消息来源 ID 的业务去重。

连接用 `autocommit=False` 开启事务管理，写操作位于以下结构中：

```python
with closing(self._connect()) as connection, connection:
    # 在这里执行本次操作的 SQL；正常结束提交，异常时回滚。
    ...
```

外层 `closing` 关闭连接，内层 `connection` 在正常结束时提交、异常时回滚；单独 `with connection` 不会关闭连接。本课每次操作创建自己的连接，以后给接口使用时也不会把同一个连接跨线程共享。[Python 事务与连接上下文说明](https://docs.python.org/3.13/library/sqlite3.html#how-to-use-the-connection-context-manager)

事务在批量导入时的意义很直接：一批中发生内容冲突，就不会留下只导入了一部分的结果。SQLite 提供事务机制保障提交与回滚；完成标记这类业务状态也由程序写入数据库。[SQLite 官方说明](https://www.sqlite.org/transactional.html)

## 可选：导入第三课旧待办

```powershell
.\.venv\Scripts\python.exe .\lessons\06_sqlite.py --import-json
```

省略路径时读取 `local-data/lesson03_tasks.json`。也可以在 `--import-json` 后指定其他同结构 JSON 的路径。

导入保留原 ID，返回 `created` 与 `exists` 数量；再次导入不新增相同记录，且不会把数据库中已经完成的事项重置成未完成。字段类型错误或同 ID 对应不同内容时，整批拒绝，不修改旧 JSON。

这是一次导入，不是双向同步。此后通过第五课 HTTP 新增的 JSON 记录，仍需重新导入才能出现在教学数据库中；下一课接入同一存储后统一写入。

## 你的练习：统计未完成数量

当前进展：你已能补齐 `FROM tasks` 并把筛选条件换成 `completed`。如果反引号只是聊天排版，不视为 SQL 错误；真正执行时写成 `status = 'completed'`，不要把反引号包含在单引号内。

当前源码已加入带中文说明的 `count_pending()` 和演示中的 `pending_count`。下面先对比“统计全部”与“统计未完成”，再理解返回值并实际运行；代理提供函数实现不等于你已经掌握完整 SQL 模块。

先运行示例和两种状态查询，确认 `pending` 与 `completed` 的区别。然后阅读 `TaskStore` 类中的 `count_pending()`，观察它怎样用 SQL 直接返回未完成数量。

先给一个已写好的“统计所有记录”的例子：

```python
    def count_all(self) -> int:
        """直接由数据库统计全部待办数量，避免把完整列表读回 Python。"""
        with closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()
            return row[0]
```

`COUNT(*)` 统计查询范围内有多少行，`fetchone()` 取回唯一的统计结果，`row[0]` 读取该结果的第一列。这个示例仅在文档中，尚未加入源码。

对应的 `count_pending()` 已加入 `task_store.py`，核心部分如下：

```python
    def count_pending(self) -> int:
        """直接由数据库统计未完成待办数量，并返回整数。"""
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = ?", ("pending",)
            ).fetchone()
            # 一行统计结果的第一列就是数量；COUNT(*) 在没有匹配项时返回 0。
            return row[0]
```

`?` 接收参数 `("pending",)` 中的值；不要把 `?` 放进单引号。`fetchone()` 取得一行统计结果，`row[0]` 再取得这一行第一列的整数，而不是返回整行对象。

`run_demo` 返回的字典现在已经包含：

```python
"pending_count": store.count_pending(),
```

重新运行示例，数量应与 `pending_tasks` 的条目数一致。再完成或新增一个待办，核对数量跟着变化；告诉我你看到的 `pending_count`，以及它是否与列表一致。

进一步练习 Python 中如何使用这个整数：取得 `pending = store.count_pending()`，大于 0 时打印“还有 X 项待办”，否则打印“当前没有待办”。函数实现由代理提供，本轮先记录 SQL 条件概念入门；用户运行、解释和迁移应用后再更新跟随实践掌握分。

## 验证与当前范围

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s lessons -p "test_06_sqlite.py" -v
```

测试使用临时数据库，不调用模型。当前只保存待办及完成状态：完整原消息、附件、重点角色、精确截止时间、提醒与跨运行 Agent checkpoint 都是后续任务；时间字段仍保留原文。
