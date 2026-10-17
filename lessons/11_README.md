# 第十一课：用 Pydantic 区分日期、时刻和范围

## 本次交付与主知识点

本课主知识点是 **Pydantic 判别联合（discriminated union）**：根据 `precision` 字段选择对应的数据模型。你会看到“周三”“周三15:00”“下个月”使用不同字段，程序可以保留实际精度，日期级结果不再被迫补成零点。

本课同时复用第二课的结构化输出：模型识别时间意图，Python执行通用日历运算，Pydantic校验输入与输出。没有新增解析库，也没有为每种中文说法增加一个计算函数。

| 代码位置 | 本课职责 |
| --- | --- |
| [time_semantics.py](time_semantics.py) 的 `RelativeTimeIntent`、`TimeExtraction` | 限制模型提取的单位、偏移、星期、钟点；允许明确表示无法识别。 |
| 同文件 `resolve_intent` | 根据原消息时间计算日、周、月、年；应用裸星期默认本周的项目规则。 |
| 同文件四种结果模型及 `TimeInterpretation` | 表达精确分钟、日期、日期范围与未确定结果，拒绝混用字段。 |
| [11_time_precision.py](11_time_precision.py) | 离线演示、Prompt预览、真实DeepSeek解释和HTTP客户端。 |
| [05_fastapi.py](05_fastapi.py) 的 `interpret_time_message` | 新增 `POST /time/interpret`，第七课服务直接复用。 |

旧的 `POST /time/normalize` 与第十课CLI继续提供有限规则预览，本课使用新的 `/time/interpret` 返回更丰富的结构。旧待办表、工具、LangGraph和你新增的路由没有改动。

## 第一步：先比较三个可观察结果

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe .\lessons\11_time_precision.py --demo
```

这里使用固定原消息时间 `2026-10-16T20:00:00+08:00`，它是星期五。演示中的意图是程序预设的，**不调用模型**；它验证日历计算与结果结构，不证明模型识别能力。

| 输入 | precision | 实际结果字段 |
| --- | --- | --- |
| 周三 | `date` | `date="2026-10-14"` |
| 周三15:00 | `minute` | `at="2026-10-14T15:00:00+08:00"` |
| 下个月 | `date_range` | `start_date="2026-11-01"`、`end_date="2026-11-30"` |

“周三”现在是已确定日期，`status="resolved"`，无需为了记录日期询问钟点，也不会自动移到下周。`status` 表示该精度的结果已确定，不表示提醒已设置。

完整日期结果如下：

```json
{
  "time_text": "周三",
  "reference_time": "2026-10-16T20:00:00+08:00",
  "timezone": "UTC+08:00",
  "precision": "date",
  "status": "resolved",
  "time_kind": "point",
  "date": "2026-10-14"
}
```

这份结果没有 `at` 字段。日期范围的两端是包含在范围内的日期，不表示凌晨或任何具体钟点。

## 第二步：看 Pydantic 怎样表达这些区别

下面是实际结果模型的关键部分，公共字段由 `TimeResultBase` 提供：

```python
class DateOnlyResult(TimeResultBase):
    """表达已确定的日期；没有 at 字段，因此不能把缺少钟点误写成零点。"""

    precision: Literal["date"] = "date"
    status: Literal["resolved"] = "resolved"
    time_kind: Literal["point", "deadline"]
    date: str
```

四种模型通过下面的类型声明连接：

```python
# Pydantic 按 precision 选择唯一模型；日期、时刻和范围不能混用字段。
TimeInterpretation = Annotated[
    ExactTimeResult | DateOnlyResult | DateRangeResult | UnresolvedTimeResult,
    Field(discriminator="precision"),
]
```

- `Literal["date"]` 规定这类结果的标签固定为date。
- `|` 表示结果可以是这些类型中的一种。
- `Field(discriminator="precision")` 让Pydantic先看precision，再选择对应模型验证。
- 公共配置 `extra="forbid"` 拒绝该类型没有声明的字段。例如date结果带上at会失败，不能偷偷混入假设钟点。

这种建模方式也适用于其他具有不同字段的业务结果，例如不同工具的输出类型。这里必须掌握的是“数据类型如何表达业务含义”，而不是记住类型名。[Pydantic 官方判别联合说明](https://pydantic.dev/docs/validation/latest/concepts/unions/#discriminated-unions-with-str-discriminators)

## 第三步：让真实模型识别意图

```powershell
.\.venv\Scripts\python.exe .\lessons\11_time_precision.py --time-text "下个月" --reference-time "2026-10-16T20:00:00+08:00"
```

此命令加载已有本机 `.env`，调用真实模型。模型的目标输出类似：

```json
{
  "intent": {
    "unit": "month",
    "offset": 1,
    "weekday": null,
    "hour": null,
    "minute": null,
    "is_deadline": false
  },
  "reason": null
}
```

这只是说明模型意图格式，真实结果以本次调用为准。`unit="month"` 和 `offset=1` 就表示相对原消息所在月份的下一个自然月；最终范围由Python计算。

本课真实验证已取得同一次“下个月”的提取对象与计算结果：实际 `intent.unit="month"`、`intent.offset=1`，随后计算为 `2026-11-01` 至 `2026-11-30`。两份实际对象并列保存在 [11_example.json](results/11_example.json) 的 `direct_model_extraction_and_result` 中；可以对照模型字段与程序输出，区别于demo里的预设值。

模型调用复用你学过的写法：

```python
# JSON模式需要显式把schema放进Prompt；结构校验不代表语义一定正确。
structured_model = model.with_structured_output(TimeExtraction, method="json_mode")
extracted = structured_model.invoke(build_time_messages(request))
```

`build_time_messages` 同时提供原文、原消息发送时间、北京时间规则、裸星期政策和JSON Schema。模型只识别意图，`resolve_intent` 才计算日期。

想查看实际Prompt与schema，可以使用 `--preview`；这时不调用模型：

```powershell
.\.venv\Scripts\python.exe .\lessons\11_time_precision.py --time-text "下个月" --reference-time "2026-10-16T20:00:00+08:00" --preview
```

## 通用日历计算怎样覆盖多种表达

| 模型意图 | 计算方式 | 例子 |
| --- | --- | --- |
| day + offset | 基准日期加减天数 | 昨天−1、明天+1、后天+2 |
| week + offset + weekday | 原消息所在自然周的周一，加周偏移和星期索引 | 周三、周四、下周一 |
| week + offset，没有weekday | 返回自然周的周一至周日 | 这周、下周 |
| month + offset | 移动日历月份，查询该月天数 | 这个月、下个月、上个月 |
| year + offset | 返回目标自然年的1月1日至12月31日 | 今年、明年、去年 |

月份采用月份索引运算处理跨年，并使用标准库 `calendar.monthrange` 查询月末；不会把下个月算成加30天。日偏移、周偏移和时区换算仍使用原消息时间，不依赖服务运行当天。[Python calendar 官方说明](https://docs.python.org/3.13/library/calendar.html#calendar.monthrange)

`hour` 与 `minute` 都有明确值时才产生精确时刻；它们必须成对提供。年、月或没有定位到某天的一整周不能带钟点，避免从范围中凭空选一天。

## 星期政策和不确定性

程序对精确匹配的裸 `周三`、`星期三`、`周三前` 等应用你已经确定的政策：属于原消息本周、保留日期精度。即使模型误提取下周或额外钟点，这些裸表达也会按项目规则纠正。

这项小范围的程序约束保护用户设置；其他表达仍由模型识别意图，因此不能把结构通过校验当作语义必定正确。例如模型把复杂句理解成错误的day偏移，日期仍可能合法；后续需要真实样本评估和纠正入口。

“周三前”可得到date精度的deadline，但没有精确截止钟点；它不等于周三00:00或23:59，也没有安排提醒。全球地区时区、完整绝对日期和具体会议起止区间仍不是本课接口的范围。完整绝对日期可继续用第十课有限规则接口验证。

无法唯一识别时，模型应返回 `intent=null` 和非空reason，例如“不是周三”没有给出实际日期。程序会返回precision为unresolved、status为needs_confirmation的结果，不强迫生成日期。日历越界返回422；模型返回的结构校验失败为502，模型服务失败/超时分别为502/504。

## 第四步：通过现有HTTP服务运行

按原数据库路径重启第七课服务。例如：

```powershell
.\.venv\Scripts\python.exe .\lessons\07_sqlite_api.py --db .\local-data\lesson09_practice.sqlite3
```

另一个终端使用新客户端：

```powershell
.\.venv\Scripts\python.exe .\lessons\11_time_precision.py --time-text "周三" --reference-time "2026-10-16T20:00:00+08:00" --base-url http://127.0.0.1:8001
```

它调用 `POST /time/interpret`。请求字段与第十课相同，结果使用本课的精度协议。新接口会调用模型；旧 `/time/normalize` 不调用模型。两者当前都不写入待办或设置提醒，现有用户路由保留。

## 你的练习：判断一个字段错误

先运行demo，然后尝试真实解释“这周”与“昨天”，反馈各自的precision和日期字段。接着看下面这个结果片段：

```json
{
  "precision": "date",
  "date": "2026-10-14",
  "at": "2026-10-14T00:00:00+08:00"
}
```

它能不能通过本课的DateOnlyResult校验？应该删掉或改动什么，才能忠实保留“周三”原文给出的信息？请结合demo中实际输出回答。

## 验证记录与进度

2026-10-16，本课9项专项测试与第三至十课64项回归共 **73项全部通过**。覆盖意图字段关系、精度联合字段隔离、日历边界、裸星期纠正、无法识别的原因、结构化模型调用与状态隔离，以及HTTP输入/配置/模型/计算错误边界。离线demo使用预设意图，单元测试使用模拟模型，不能据此证明真实模型识别成功。

另验证了真实DeepSeek：一次直接调用保存“下个月”的实际意图和计算结果；随机本地TCP端口上的第七课临时服务解释9条合成表达，均HTTP200且结果符合预期，分别为周三、周三15:00、下个月、今年、这周、周四、昨天、周三前、不是周三。前三类精度与上表一致，周三前只得到日期级deadline，否定句得到unresolved。这些样本通过不代表所有自然语言表达都能正确识别。

新CLI通过真实HTTP解释周三，与接口结果一致；非法无时区参考时间为422，旧归一化接口仍返回原结构，用户pending接口保留，临时数据库记录前后未变。记录见 [results/11_example.json](results/11_example.json)，临时服务已停止，未修改用户业务数据库。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s lessons -p "test_11_time_semantics.py" -v
```

本课扩展既有M4时间预览能力，项目保持23%；学习仍13模块合计350、约27%，时间模块50仅覆盖已完成的第十课练习。第十一课用户已正确核对日期精度字段，独立修改待验；不因代码交付增加掌握分。业务时间持久化、提醒调度、完整上游、来源与附件仍待实现。

用户随后正确回答：at不应该出现，因为星期三是一天而不是某一时刻，精度概念核对已完成；独立修改schema和排错尚未验证，Pydantic保持50。用户同时要求加快推进，后续按[核心技术栈加速路线](../docs/ACCELERATED_ROADMAP.md)合并项目交付与综合练习，不再为本字段单独安排下一轮。
