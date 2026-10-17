"""固定合成消息的规则回归与小样本模型评测，复用真实消息工作流并导出安全Trace。"""

import copy
import json
import math
import re
import tempfile
from contextlib import nullcontext
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from student_assistant.bridge import ROOT, TaskStore, create_model
from student_assistant.message_models import Category, MessageAnalysis, MessageInput
from student_assistant.message_store import MessageStore
from student_assistant.message_workflow import MessageProcessingError, MessageProcessor, validate_evidence
from student_assistant.trace_store import TraceStore, safe_diagnostic
from student_assistant.tracing import span, use_trace
from time_semantics import TimeInterpretation


DEFAULT_CASES = ROOT / "examples" / "module05_eval_cases.jsonl"
METRIC_DEFINITIONS = {
    "classification_accuracy": "类别完全相同的消息数 / 固定消息总数",
    "event_count_accuracy": "事项数量完全相同的消息数 / 固定消息总数",
    "time_field_accuracy": "按原时间表达一对一匹配后的正确字段与形状断言数 / 全部预期断言数；缺失事项计失败",
    "attachment_hint_retention": "原消息和详情均保留的附件线索数 / 输入附件线索总数，不代表已下载文件",
    "attachment_count_accuracy": "模型附件数量落在固定允许值内的消息数 / 固定消息总数",
}
_MISSING = "<missing>"


class EvaluationConfigurationError(ValueError):
    """表示评测配置或JSONL格式非法；公开错误不携带输入正文、路径或提供商响应。"""


class EvaluationInput(BaseModel):
    """为评测配置启用严格类型与未知字段拒绝，防止拼错预期字段后静默通过。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class ExpectedEvent(EvaluationInput):
    """用原时间表达匹配事项；预期时间保留精度和原始基准，不依赖模型改写的标题。"""

    time_text: str | None
    time: TimeInterpretation | None

    @model_validator(mode="after")
    def validate_time_pair(self) -> Self:
        """要求有时间时给出完整预期，无时间时明确为null，避免遗漏字段造成虚假通过。"""
        if (self.time_text is None) != (self.time is None):
            raise ValueError("预期时间原话与结果必须同时提供或同时为空")
        if self.time is not None and self.time.time_text != self.time_text:
            raise ValueError("预期时间结果必须保留相同的原话")
        return self


class ExpectedOutcome(EvaluationInput):
    """定义人工固定的类别、事项数、时间及附件数量允许值，修改预期后必须重新评测。"""

    category: Category
    event_count: int = Field(ge=0, le=20)
    events: list[ExpectedEvent] = Field(max_length=20)
    attachment_count_values: list[Annotated[int, Field(ge=0)] | None] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def validate_event_count(self) -> Self:
        """核对事项总数与逐项预期一致，不允许漏掉事项的时间断言。"""
        if self.event_count != len(self.events):
            raise ValueError("预期事项数与逐项预期不一致")
        if len(set(self.attachment_count_values)) != len(self.attachment_count_values):
            raise ValueError("附件数量允许值不能重复")
        return self


class EvaluationCase(EvaluationInput):
    """保存一条合成原文、独立预期和离线分析；离线分析不能替代真实模型能力证据。"""

    case_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    input: MessageInput
    expected: ExpectedOutcome
    rule_analysis: MessageAnalysis

    @model_validator(mode="after")
    def validate_case_evidence(self) -> Self:
        """检查离线证据与预期时间来自原消息，但不要求离线答案等于预期以支持修改练习。"""
        validate_evidence(self.input, self.rule_analysis)
        for event in self.expected.events:
            if event.time_text is not None and event.time_text not in self.input.text:
                raise ValueError("预期时间原话必须出现在输入消息中")
            if event.time is not None and event.time.reference_time != self.input.sent_at:
                raise ValueError("预期参考时间必须保留输入原串")
        return self


def validate_cases(cases: list[EvaluationCase | dict]) -> list[EvaluationCase]:
    """统一校验非空样例集与唯一编号，避免来源去重让后续样例跳过真实处理。"""
    try:
        if not isinstance(cases, list) or not cases:
            raise ValueError("样例集不能为空")
        validated = [EvaluationCase.model_validate(case) for case in cases]
        identifiers = [case.case_id for case in validated]
        sources = [(case.input.source, case.input.account_id, case.input.source_message_id) for case in validated]
        if len(set(identifiers)) != len(identifiers) or len(set(sources)) != len(sources):
            raise ValueError("样例编号和来源消息编号必须唯一")
        return validated
    except Exception:
        raise EvaluationConfigurationError("评测样例配置无效") from None


def load_cases(path: Path | str = DEFAULT_CASES) -> list[EvaluationCase]:
    """读取UTF-8或BOM的JSONL并严格校验；异常只返回配置错误类型，不回显坏行内容。"""
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
        raw = [json.loads(line) for line in lines if line.strip()]
        return validate_cases(raw)
    except Exception:
        raise EvaluationConfigurationError("无法读取合法的评测样例集") from None


class RuleAnalysisModel:
    """注入预设分析的模型替身，仅用于验证程序校验、日历计算、事务保存与判分。"""

    def __init__(self, analysis: MessageAnalysis):
        """复制固定分析数据，防止一次运行的可变状态污染下一条样例。"""
        self.analysis = analysis.model_dump()
        self.schema = None

    def with_structured_output(self, schema, *, method):
        """遵守真实工作流的结构化接口，拒绝绕开JSON模式或改变输出协议。"""
        if method != "json_mode" or schema is not MessageAnalysis:
            raise ValueError("离线模型只支持消息分析协议")
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        """返回通过真实Pydantic校验的预设分析，不访问网络或任何模型配置。"""
        if self.schema is None:
            raise ValueError("必须先绑定结构化输出协议")
        return self.schema.model_validate(copy.deepcopy(self.analysis))


def _add_check(checks: list[dict], metric: str, name: str, passed: bool, expected: Any, actual: Any) -> None:
    """追加一个可诊断断言；只保存有限的合成结果字段，不保存完整消息或模型响应。"""
    checks.append({"metric": metric, "name": name, "passed": bool(passed), "expected": expected, "actual": actual})


def score_case(case: EvaluationCase, detail: dict | None) -> list[dict]:
    """比较实际业务结果与独立预期，缺失事项不跳过，日期或范围禁止偷偷补at。"""
    checks = []
    actual = detail or {}
    analysis = actual.get("analysis") or {}
    events = actual.get("events") if detail is not None else None
    _add_check(checks, "classification_accuracy", "category", analysis.get("category") == case.expected.category,
               case.expected.category, analysis.get("category"))
    event_count = len(events) if isinstance(events, list) else None
    _add_check(checks, "event_count_accuracy", "event_count", event_count == case.expected.event_count,
               case.expected.event_count, event_count)
    _add_check(checks, "attachment_count_accuracy", "attachment_count",
               detail is not None and analysis.get("attachment_count") in case.expected.attachment_count_values,
               case.expected.attachment_count_values, analysis.get("attachment_count"))
    source_hints = (actual.get("source_message") or {}).get("attachments", [])
    shown_hints = actual.get("attachments", [])
    for index, hint in enumerate(case.input.attachments):
        expected_hint = hint.model_dump()
        preserved = (
            index < len(source_hints) and index < len(shown_hints)
            and all(source_hints[index].get(key, _MISSING) == value for key, value in expected_hint.items())
            and all(shown_hints[index].get(key, _MISSING) == value for key, value in expected_hint.items())
        )
        _add_check(checks, "attachment_hint_retention", f"attachment[{index}]", preserved,
                   hint.filename, shown_hints[index].get("filename") if index < len(shown_hints) else None)
    # 一对一消费事项，防止同一个模型结果被拿来满足多个预期；标题不参与匹配。
    remaining = list(events or [])
    for index, expected_event in enumerate(case.expected.events):
        matched_index = next((i for i, item in enumerate(remaining) if item.get("time_text") == expected_event.time_text), None)
        event = remaining.pop(matched_index) if matched_index is not None else None
        prefix = f"event[{index}]"
        _add_check(checks, "time_field_accuracy", prefix + ".matched", event is not None,
                   expected_event.time_text, event.get("time_text") if event else _MISSING)
        if expected_event.time is None:
            _add_check(checks, "time_field_accuracy", prefix + ".time_null",
                       event is not None and "time" in event and event["time"] is None,
                       None, event.get("time", _MISSING) if event else _MISSING)
            continue
        expected_time = expected_event.time.model_dump()
        actual_time = event.get("time") if event else None
        for key, value in expected_time.items():
            actual_value = actual_time.get(key, _MISSING) if isinstance(actual_time, dict) else _MISSING
            _add_check(checks, "time_field_accuracy", prefix + "." + key, actual_value == value, value, actual_value)
        # 字段相等还不够：date/date_range不能含at，minute也不能混入日期范围字段。
        for key in ("at", "date", "start_date", "end_date"):
            if key not in expected_time:
                absent = isinstance(actual_time, dict) and key not in actual_time
                _add_check(checks, "time_field_accuracy", prefix + ".forbid_" + key, absent,
                           "absent", "absent" if absent else "present_or_missing_result")
    return checks


def _safe_error(error: Exception) -> str:
    """解包业务异常后只保留安全异常类型，避免提供商原始响应或凭证进入报告。"""
    cause = error.cause if isinstance(error, MessageProcessingError) else error
    name = type(cause).__name__
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) else "Exception"


def _trace_call(method, *args, **kwargs):
    """隔离Trace开始、结束和读取故障，保证记录设施失败不会改变业务判分。"""
    try:
        return method(*args, **kwargs)
    except Exception as error:
        safe_diagnostic(error)
        return None


async def run_evaluation(
    cases: list[EvaluationCase | dict], mode: Literal["rules", "live"] = "rules", *,
    model_factory=None, work_dir: Path | str | None = None,
) -> dict:
    """在独立临时业务库评测全部样例，导出Trace后清理库；不创建提醒、收件或聊天队列。"""
    cases = validate_cases(cases)
    if mode not in ("rules", "live"):
        raise EvaluationConfigurationError("评测模式无效")
    parent = Path(work_dir) if work_dir is not None else ROOT / "local-data"
    parent.mkdir(parents=True, exist_ok=True)
    results, traces = [], []
    metrics = {name: {"passed": 0, "total": 0, "score": 0.0} for name in METRIC_DEFINITIONS}
    with tempfile.TemporaryDirectory(prefix="module05-eval-", dir=parent) as folder:
        tasks = TaskStore(Path(folder) / "evaluation.sqlite3")
        store = MessageStore(tasks)
        try:
            trace_store = TraceStore(tasks)
        except Exception as error:
            safe_diagnostic(error)
            trace_store = None
        for case in cases:
            factory = (partial(RuleAnalysisModel, case.rule_analysis) if mode == "rules"
                       else model_factory or create_model)
            # 不传after_process，因此不会隐式启用提醒；每条样例都走原文接收和真实事务。
            processor = MessageProcessor(store, model_factory=factory)
            trace_id = uuid4().hex
            if trace_store is not None:
                _trace_call(trace_store.start_trace, trace_id, "evaluation.run")
            detail, error_code = None, None
            context = use_trace(trace_store, trace_id) if trace_store is not None else nullcontext()
            with context, span("evaluation.run") as current:
                try:
                    response = await processor.process(case.input)
                    detail = response["message"]
                except Exception as error:
                    error_code = _safe_error(error)
                checks = score_case(case, detail)
                passed = error_code is None and all(check["passed"] for check in checks)
                if not passed:
                    current.fail(error_code or "EvaluationAssertionError")
            if trace_store is not None:
                _trace_call(trace_store.finish_trace, trace_id, "completed" if passed else "error",
                            error_code=error_code or (None if passed else "EvaluationAssertionError"))
                snapshot = _trace_call(trace_store.get_trace, trace_id)
                if snapshot is not None:
                    # Trace是独立JSON快照，临时SQLite清理后仍可在报告里检查父子节点与耗时。
                    traces.append(snapshot)
            analysis = (detail or {}).get("analysis") or {}
            results.append({
                "case_id": case.case_id, "passed": passed, "checks": checks,
                "trace_id": trace_id, "error_code": error_code,
                "actual": {
                    "category": analysis.get("category"),
                    "event_count": len(detail["events"]) if detail is not None else None,
                    "attachment_count": analysis.get("attachment_count"),
                    "events": [{"time_text": item["time_text"], "time": item["time"]}
                               for item in (detail or {}).get("events", [])],
                },
            })
            for check in checks:
                metric = metrics[check["metric"]]
                metric["total"] += 1
                metric["passed"] += int(check["passed"])
    for metric in metrics.values():
        metric["score"] = metric["passed"] / metric["total"] if metric["total"] else 1.0
    return {
        "mode": mode, "model_evaluation": mode == "live", "case_count": len(cases),
        "metrics": metrics, "metric_definitions": METRIC_DEFINITIONS,
        "cases": results, "traces": traces,
        "limitations": [
            "rules模式注入固定分析，只是程序规则回归，不能用其得分证明模型提取能力。",
            "live模式仅为这些固定合成样例的真实模型结果，不代表生产数据分布或全面质量。",
            "附件线索保留不等于已下载文件；模型附件数量与原文保存分别评分。",
            "标题不作硬匹配；时间按原话一对一匹配，缺失事项及多余at字段计失败。",
            "没有时间断言或附件线索时该项总数为0、score约定为1；应同时阅读分母。",
            "当前不评估RAG引用合法性、答案正确性、语义蕴含或检索Recall。",
            "Trace仅记录安全运行元数据，记录失败不改变判分；临时数据库在报告返回前清理。",
        ],
    }


def report_passes(report: dict, fail_under: float = 1.0) -> bool:
    """按每项指标分别判断阈值，不用平均数掩盖某一项较低的结果。"""
    if isinstance(fail_under, bool) or not isinstance(fail_under, (int, float)):
        raise EvaluationConfigurationError("阈值必须为0到1之间的有限数")
    if not math.isfinite(fail_under) or not 0 <= fail_under <= 1:
        raise EvaluationConfigurationError("阈值必须为0到1之间的有限数")
    return bool(report.get("metrics")) and all(item["score"] >= fail_under for item in report["metrics"].values())
