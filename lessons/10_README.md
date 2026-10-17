# 第十课：把原文时间转换成可计算的时刻

## 本次学什么、在项目里做什么

现在的待办只保存 `time_text`，例如“明天下午三点”“周五前”。本课学习 Python 标准库 **`datetime`、`timedelta`、`timezone`**，把有明确信息的时间表达转换为带偏移的时间，缺信息或超出规则时返回待确认结果。

后续的提醒调度器需要明确时刻，才能判断什么时候执行提醒。本课交付解析函数、命令行示例和现有服务里的只读预览接口；时间结果尚未写回待办，也没有发送提醒。你的第九课练习尚未反馈，继续学习不自动记为已掌握。

| 技术或代码 | 位置 | 本课作用 |
| --- | --- | --- |
| `datetime` / `timedelta` / `timezone` | [time_parser.py](time_parser.py) | 表示消息时间，换算北京时间，进行日期加减和钟点设置。 |
| Pydantic | 同文件 `TimeNormalizationRequest` / `NormalizedTime` | 检查输入字段、必填时区和输出状态之间的关系。 |
| FastAPI | [05_fastapi.py](05_fastapi.py) 的 `preview_time` | 新增 `POST /time/normalize`，第七课服务直接复用。 |
| CLI | [10_time_normalization.py](10_time_normalization.py) | 直接调用解析函数，也能作为 HTTP 客户端验证相同结果。 |

没有新增依赖。本课使用固定 `UTC+08:00` 作为业务时间，适用于当前北京时间教学样例；它没有实现全球地区时区、历史夏令时或日历系统。Python 对带时区与不带时区的对象、日期时间加减有明确区分，见 [Python 3.13 官方 datetime 文档](https://docs.python.org/3.13/library/datetime.html)。

## 第一步：不启动服务，先运行最小例子

在 `D:\Student_Assistant` 的 PowerShell 中执行：

```powershell
.\.venv\Scripts\python.exe .\lessons\10_time_normalization.py --reference-time "2026-10-15T20:00:00+08:00"
```

`--time-text` 默认是“明天下午三点”。上面的参考时间明确表示：这条样例消息在 **2026年10月15日20:00，北京时间** 发出。实际得到：

```json
{
  "time_text": "明天下午三点",
  "reference_time": "2026-10-15T20:00:00+08:00",
  "timezone": "UTC+08:00",
  "status": "resolved",
  "time_kind": "point",
  "at": "2026-10-16T15:00:00+08:00",
  "confirmation_question": null
}
```

`at` 是用于后续计算的时刻，`+08:00` 表示相对 UTC 快8小时。输出保留时间原话和参考时刻，方便回溯。原话的首尾空格也会保留；内部匹配时可以忽略首尾空格。

这条命令直接运行 Python 规则，无需 API Key。换一天运行同样的命令，结果仍然一样，因为参考时间已经明确给出。

## 第二步：看日期加减和钟点设置

解析函数的关键代码是：

```python
# 所有相对日期都先在本课规定的北京时间下计算。
local_reference = reference_time.astimezone(BEIJING)

# day是“今天”“明天”或“后天”；映射得到0、1、2天。
target_day = local_reference + timedelta(days=DAY_OFFSETS[day])

# 设置目标时、分；本课支持的表达精确到分钟，用00秒表示。
target = target_day.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
```

用“明天15:00”理解：

1. `reference_time` 是原消息发送时刻，必须带时区；`astimezone(BEIJING)` 将同一时刻转换到北京时间。
2. `timedelta(days=1)` 表示一天，Python负责处理跨月、跨年、闰年。
3. `replace(hour=15, minute=0, ...)` 将目标钟点设为15:00；单纯加一天还会保留参考时间里的20:00。

“明天下午三点”分支会先把“下午三点”换成15点，再执行同样的日期计算。

`reference_time` 应来自真实来源的消息发送时间。当前旧待办表没有来源时间字段，所以预览命令显式提供教学参考值；后续接入消息元数据后由程序传入。延迟处理、补同步或重放消息时，不能用服务处理时的 `datetime.now()` 重新定义“明天”。

### 时区如何影响日期

```powershell
.\.venv\Scripts\python.exe .\lessons\10_time_normalization.py --time-text "明天15:00" --reference-time "2026-10-15T23:30:00Z"
```

`Z` 代表 UTC；该参考时刻对应北京时间10月16日07:30，因此这里的“明天”是 **10月17日**，结果是 `2026-10-17T15:00:00+08:00`。输出中的 `reference_time` 保留原偏移，`Z` 会规范化为 `+00:00`，仍表示同一时刻。

## 第三步：处理不够明确的截止时间

```powershell
.\.venv\Scripts\python.exe .\lessons\10_time_normalization.py --time-text "周五前" --reference-time "2026-10-15T20:00:00+08:00"
```

关键结果是：

```json
{
  "status": "needs_confirmation",
  "time_kind": "deadline",
  "at": null,
  "confirmation_question": "请确认具体哪一天、几点截止，例如2026-10-23 18:00前；不会自动补成当天结束。"
}
```

这个表达带有截止性质，但没有明确截止时刻。因此可以标记 `deadline`，同时让 `at` 为 `null`，等待确认。确认问题里的日期是说明格式的例子，不是程序替用户选定的日期。

得到明确日期和钟点后，可以验证：

```powershell
.\.venv\Scripts\python.exe .\lessons\10_time_normalization.py --time-text "2026-10-20 18:00前" --reference-time "2026-10-15T20:00:00+08:00"
```

此时 `status="resolved"`、`time_kind="deadline"`，`at="2026-10-20T18:00:00+08:00"`。这里的 `at` 是截止边界，什么时候提醒仍由后续提醒策略决定。

## 当前支持范围与失败含义

| 输入表达 | 处理方式 |
| --- | --- |
| 今天 / 明天 / 后天 + 24小时制 `HH:MM`，可带“前” | 转换为明确时刻；带“前”记为deadline，否则point。 |
| 今天 / 明天 / 后天 + 上午 / 下午 + 一至十一点，可用阿拉伯数字，可带“前” | 转换为明确时刻；支持“两点”。十二点请求用24小时制确认。 |
| 完整 `YYYY-MM-DD HH:MM` 或 `YYYY-MM-DDTHH:MM`，可带“前” | 按本课北京时间转换，过去的日期也如实保留，不自动顺延。 |
| “周五前”“2026-10-20前”等缺钟点表达 | `needs_confirmation`，不补造23:59或其他钟点。 |
| 否定句、时间区间、混合文本或其他不支持表达 | `needs_confirmation`，不从局部截取一个看似有效的时刻。 |
| 已识别语法中的非法日期/钟点，如2月30日、25:00 | 拒绝；CLI退出码2，HTTP422。 |
| 缺少参考时间、参考时间无偏移、字段类型错误或额外字段 | 输入校验拒绝；CLI退出码2，HTTP422。 |

`re.fullmatch(...)` 要求整段表达符合规则。比如“不是明天下午三点”“明天下午三点到四点”都不会被截成一个确定的15:00。它是有限规则解析器，不具备通用中文语义理解；`needs_confirmation` 也可能意味着该表达超出当前支持范围，而不代表人类一定无法理解。

本课输入 `time_text` 对应前课已经提取并保存的时间原话，预期流程是“完整消息及来源时间 → 模型提取事项 → 程序校验时间候选”。此时没有把模型接回新的预览接口，也没有新增完整原消息存储。

## 第四步：通过现有 HTTP 服务运行相同能力

先按原数据库路径重启第七课服务。例如继续使用第九课练习库：

```powershell
.\.venv\Scripts\python.exe .\lessons\07_sqlite_api.py --db .\local-data\lesson09_practice.sqlite3
```

另一个终端执行：

```powershell
.\.venv\Scripts\python.exe .\lessons\10_time_normalization.py --reference-time "2026-10-15T20:00:00+08:00" --base-url http://127.0.0.1:8001
```

这次客户端向已有服务的 `POST /time/normalize` 发送：

```json
{
  "time_text": "明天下午三点",
  "reference_time": "2026-10-15T20:00:00+08:00"
}
```

服务调用同一个 `normalize_time(...)`，应得到相同结果。也可在 `http://127.0.0.1:8001/docs` 中操作该接口。它是只读预览，不调用模型、不访问待办存储，也不会创建提醒；服务本身启动时仍会初始化它绑定的待办库。

## 你的练习：预测、运行、解释一处代码

先保留 `--time-text "明天15:00"`，把参考时间从 `2026-10-15T20:00:00+08:00` 改为 `2026-10-16T20:00:00+08:00`，预测再运行，反馈 `at`。

然后看这个改法是否适用于本项目：

```python
# 练习中的待判断写法：按程序运行当天计算。
target_day = datetime.now(BEIJING) + timedelta(days=1)
```

结合“10月15日收到消息、10月18日才补处理”的具体情景，解释应该继续使用 `local_reference` 还是使用 `datetime.now(BEIJING)`。不必实际改坏项目代码；对照正在运行的例子说明结果差异即可。

## 验证与进度

2026-10-16，第十课12项专项测试与第三至九课52项回归，共 **64项全部通过**。覆盖原时区偏移、跨月年与闰日、否定句/范围的完整匹配、歧义确认、严格输入和输出关系、非法日历/时分及极端年份溢出。测试还确认时间接口不调用模型或存储，用户新增的未完成接口保留。

另启动真实第七课TCP服务，使用临时数据库和合成输入，临时子进程模型配置为空。4项正常请求均HTTP200：中文相对整点、UTC换日、周五前待确认、明确日期钟点截止；4项非法请求均HTTP422：25:00、无钟点非法日期、无偏移基准、参考年份换时区溢出。真实CLI经HTTP与直接函数结果一致，待办列表和完成状态前后未变。记录见 [results/10_example.json](results/10_example.json)，临时服务已停止。本课没有进行模型推理，也未修改你的业务数据库。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s lessons -p "test_10_time_parser.py" -v
```

M4从20%中分配2%给有限时间表达归一化与歧义预览的独立验收，当前已通过，项目从21%更新为 **23%**；18%继续用于关联持久化、时段与标签、可恢复且不重复的提醒。学习分保持13模块合计300、约23%；时间模块尚待用户练习，不因代码交付自动加分。

## 练习反馈与后续方向

用户随后正确给出 `at="2026-10-17T15:00:00+08:00"`，并解释应以原消息发送时间为基准。本次跟随实践记时间模块50/100，总学习分从300更新为350，约27%；上述约23%是课程刚交付、尚未收到反馈时的历史状态，不表示整个时间与调度模块已经学完。

用户提出不应逐种时间表达堆叠代码，并规定裸“周三”按原消息所在周的周三解释。本项目采用周一至周日的自然周，即使该星期已过去也不自动跳下周；日期可以先确定，精确提醒钟点以后再补。通用星期计算示例、日期/范围精度和解析器复用方向见 [时间解释规则](../docs/TIME_POLICY.md)。当时的问答轮没有扩展API；随后[第十一课](11_README.md)新增 `/time/interpret`，实现相对日/周/月/年、裸星期政策和不同精度。第十课原接口与脚本保持原行为。
