# 从旧邮件误提醒学习：策略门控与回归测试

更新：2026-10-16。本轮围绕用户实际发现的旧续费邮件误提醒，修复程序行为并继续模块五的排错学习。所有演示使用合成消息、固定时钟和临时数据库，不读取真实邮箱，不调用Windows通知系统。

## 为什么已有测试通过，仍出现了问题

此前验证了“已经建立的提醒，到期后会补发”，但没有同时验证“今天刚导入一封2021年的邮件，不应立即为它创建提醒”。两者都满足 `remind_at <= now`，业务含义却不同。需要把实际失败场景增加到回归集，不能只让原有测试继续通过。

Trace中的 `completed` 表示代码步骤正常结束，不代表业务决策正确。用户贴出的请求中，`message.analyze`、`message.resolve_times`、`message.persist`、`reminder.plan`全部完成；后台另一条邮件流程仍可能将历史通知误当待办。一个请求的Trace也不会自动覆盖所有独立后台任务。

本轮增加三个可独立观察的行为：

| 情形 | 修复后的行为 |
| --- | --- |
| 今天新处理2021年邮件，事项日期已过 | 原文、分析和已有事项保留，自动计划返回 `source_event_expired` |
| 事项尚未过期，但计划提醒钟点已经过去 | 新自动计划返回 `reminder_time_passed`；需要时由用户显式设置新提醒 |
| 升级前已经进入队列的旧自动提醒，原事项已过期 | 投递前抑制，记录 `cancelled` / `SourceEventExpired`，不弹窗 |
| 已提前安排的自动提醒在停机期间错过，但原事项尚未到 | 仍允许补发 |
| 用户明确手工安排的一条到期提醒 | 记录 `origin=manual`，保留显式计划的补发行为 |

“自动续费”关键词本身不作为硬拦截条件。纯“服务将自动续费，无需操作”应保留在摘要、不创建用户待办；“请在明天15点前取消自动续费”仍是有效待办。两类语义分别加入模型评测，Prompt改进不能保证所有措辞永远正确，未来的语义错误仍需要标注和回归。

## 两种时间，各司其职

**原消息发送时间 `sent_at`** 用于理解“明天”“四天后”“这周三”。例如2021年10月5日的“四天后”仍是2021年10月9日，不能为了避免过期而改成今天加四天。

**当前处理时间 `now`** 用于判断提醒是否还应该自动建立或发送。它不会回写或替换原消息时间。

```python
# 先按原消息的时间计算事项日期。
normalized_date = "2021-10-09"

# 再用当前时间判断是否已经过去。
reason = event_expiration_reason(event["time"], clock())
```

日期比较按北京时间自然日进行。只有日期的事项在当天仍保留日期精度，不补零点、09:00或23:59作为事项时刻。`date_only_time`是独立的提醒钟点；若它今天已经过去，新自动计划会跳过，原事项日期不变。

## 跑一个不会弹窗的完整复现

在 `D:\Student_Assistant` 运行：

```powershell
.\.venv\Scripts\python.exe .\reminder_guard_demo.py
```

演示的当前时钟固定为北京时间2026年10月16日20:00，不随电脑实际日期改变。预期输出：

```json
{
  "new_old_message_plan": "source_event_expired",
  "old_automatic_queue_suppressed": 1,
  "explicit_manual_simulated_submission": 1,
  "temporary_database_cleaned": true
}
```

其中第三行只表示**记录通知器被调用一次**，没有系统气泡。演示先故意注入一条把自动续费告知提取成事项的旧模型结果，验证即使上游提取错了，日期门控仍能挡住历史弹窗；它不是模型能力评测。

完整报告保存在 `local-data/reminder_guard_report.json`，内含原始时间精度、跳过原因、旧队列取消状态及Trace快照。临时SQLite已删除，用户的 `module01_tasks.sqlite3` 不受此演示影响。

## 关键代码与本轮技术

| 技术 | 本轮实际作用 | 代码 |
| --- | --- | --- |
| 策略门控 | 自动建计划前、真正投递前分别判断是否允许 | [reminder_guards.py](../agent-service-toolkit/src/student_assistant/reminder_guards.py)、[reminder_runtime.py](../agent-service-toolkit/src/student_assistant/reminder_runtime.py) |
| 依赖注入 / 固定时钟 | 用测试指定的时刻复现2021年旧消息，不依赖测试当天 | `ReminderRuntime(..., clock=fixed_now)` |
| 兼容迁移 | 旧提醒标为legacy，新手工/自动计划标明origin，旧记录内容不重写 | [reminder_store.py](../agent-service-toolkit/src/student_assistant/reminder_store.py) |
| 租约下的状态更新 | 只有仍持有当前版本与token的worker才能抑制该次投递 | 同文件 `suppress_claim()` |
| 回归测试 | 把本次失败作为以后每次改动都要通过的检查 | [test_reminder_guards.py](../tests/test_reminder_guards.py) |
| 对照语义评测 | 同时检查无需操作的告知和明确取消请求，避免一刀切 | [module05_eval_cases.jsonl](../examples/module05_eval_cases.jsonl) |

关键流程摘录：

```python
# 消息日期已经过去时，记录原因并跳过自动计划。
if event_expiration_reason(value, now):
    reason = "source_event_expired"

# 日期可能还没过，但提醒钟点本身也必须在未来。
if target <= now:
    skipped.append({"task_id": task_id, "reason": "reminder_time_passed"})
    continue
```

投递前还会检查已存在的自动和legacy队列。只在创建处加判断无法挡住修复前已经存在的记录，因此必须覆盖两个入口。显式手工计划代表用户再次选择，不受自动来源的过期规则限制。

本轮只改代码和演示文件；已有业务消息、事项和已submitted通知记录没有批量删除。用户重启服务后才会加载新逻辑；旧schema会增加origin列，未投递且原事项已过期的旧自动来源记录，在worker认领时记为cancelled。已提交给Windows的通知无法撤回。

## 用回归结果判断修复是否完整

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_reminder_guards -v
.\.venv\Scripts\python.exe .\evaluate_assistant.py --mode rules
```

如需验证模型对新加入两种措辞的判断，再运行：

```powershell
.\.venv\Scripts\python.exe .\evaluate_assistant.py --mode live
```

`rules`验证预设分析经过真实程序的行为；`live`才使用配置的模型提取这组合成消息。两者的报告分别保存，不用规则满分代替模型准确率。时间字段检查数也不等于独立时间表达数量。

这轮练习：运行离线演示后，结合 `source_event_time` 和 `new_automatic_plan` 解释：**2021年邮件的“四天后”算成2021年10月9日是否正确？如果正确，为什么还不能自动弹提醒？**

## 本轮实际验证

[修复验收记录](results/reminder_guard_fix.json)记录12项检查及198项全量测试通过（根tests125＋lessons73）。新增13项为提醒存储4、时间门控8、语义误提/漏提对照1；原自动化测试注入固定时钟，避免测试日期变化导致结果漂移。

真实DeepSeek对当前10条合成消息本次得到分类10/10、事项数10/10、时间字段81/81、附件线索4/4、模型附件数量10/10。新增“服务将自动续费，无需手动操作”返回0事项，“请在明天下午三点前取消自动续费”返回1事项；不能由两例正确推断所有商业通知都不会误判。rules模式同分只属于程序规则回归。

本轮演示、评测的临时SQLite已清理。两次早期存储单测的临时目录因自动审批拒绝清理而保留，仅含测试数据，不影响用户业务库；没有为清理继续变更权限。

项目功能进度暂保持53%，本轮修复不新增功能权重。技术学习保持400/1300约31%；用户已运行导入和Trace查询，但评测解释、固定时钟修改和独立排错仍待反馈。预计核心主线还需约2–4次有效反馈；真实QQ正文读取已有排查证据，完整附件、增量与延迟验收仍待核对。
