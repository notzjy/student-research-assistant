"""评测模块的离线验收：真实工作流、SQLite、Trace、判分分母和CLI退出码。"""

import copy
import importlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from functools import partial
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import APIError


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

evaluation = importlib.import_module("student_assistant.evaluation")
cli = importlib.import_module("evaluate_assistant")


class EvaluationModel:
    """按完整输入中的来源编号返回预设分析，以离线验证live分支而不冒充真实模型推理。"""

    def __init__(self, responses, calls):
        """共享测试响应和调用记录，每个真实处理请求仍获得独立模型对象。"""
        self.responses = responses
        self.calls = calls
        self.schema = None

    def with_structured_output(self, schema, *, method):
        """核对正式工作流使用真实消息协议和JSON模式，并保存输出校验器。"""
        if schema is not evaluation.MessageAnalysis or method != "json_mode":
            raise AssertionError("消息评测绕过了真实结构化协议")
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        """记录实际收到的完整原文，按来源ID返回分析或抛出合成提供商异常。"""
        payload = json.loads(messages[-1][1])
        self.calls.append(payload)
        response = self.responses[payload["source_message_id"]]
        if isinstance(response, Exception):
            raise response
        return self.schema.model_validate(copy.deepcopy(response))


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    """只使用固定合成样例和临时数据库，不联网、加载.env或访问用户数据。"""

    def setUp(self):
        """读取合成基准，为每项测试建立独立目录和可控模型分析。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.work = self.folder / "work"
        self.cases = evaluation.load_cases()
        self.assertEqual(len(self.cases), 10)
        self.responses = {case.input.source_message_id: case.rule_analysis.model_dump() for case in self.cases}
        self.calls = []

    def fake_factory(self):
        """返回每次新建分析模型的工厂，替换live模型调用但不替换业务图或存储。"""
        return partial(EvaluationModel, self.responses, self.calls)

    async def test_rules_all_cases_real_calendar_persistence_and_exported_traces(self):
        """全部规则回归通过，真实业务图生成时间、附件和可独立读取的Trace快照。"""
        with patch.object(evaluation, "create_model", side_effect=AssertionError("不能调用真实模型")):
            report = await evaluation.run_evaluation(self.cases, work_dir=self.work)

        self.assertEqual(report["mode"], "rules")
        self.assertFalse(report["model_evaluation"])
        self.assertEqual(report["case_count"], len(self.cases))
        self.assertTrue(all(case["passed"] for case in report["cases"]))
        self.assertTrue(all(item["score"] == 1.0 for item in report["metrics"].values()))
        self.assertEqual(report["metrics"]["classification_accuracy"]["total"], len(self.cases))
        self.assertEqual(report["metrics"]["event_count_accuracy"]["total"], len(self.cases))
        self.assertEqual(report["metrics"]["time_field_accuracy"]["total"], 81)
        self.assertEqual(report["metrics"]["attachment_hint_retention"]["total"], 4)
        self.assertEqual(report["cases"][1]["actual"]["events"][0]["time"]["date"], "2026-10-14")
        self.assertNotIn("at", report["cases"][1]["actual"]["events"][0]["time"])
        self.assertEqual(report["cases"][2]["actual"]["events"][0]["time"]["at"], "2026-02-01T09:00:00+08:00")
        self.assertEqual(report["cases"][3]["actual"]["events"][0]["time"]["end_date"], "2027-01-31")
        self.assertEqual(len(report["traces"]), len(self.cases))
        for trace, result in zip(report["traces"], report["cases"]):
            self.assertEqual(trace["trace_id"], result["trace_id"])
            self.assertEqual(trace["operation"], "evaluation.run")
            self.assertEqual(trace["status"], "completed")
            self.assertIn("evaluation.run", [item["name"] for item in trace["spans"]])
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertNotIn("sqlite3", json.dumps(report["traces"], ensure_ascii=False))

    async def test_live_branch_uses_full_original_and_counts_wrong_or_missing_results(self):
        """live调用路径收到完整原文，类别、缺失事项与错误时间各自降低指标且分母不缩小。"""
        baseline = await evaluation.run_evaluation(self.cases, work_dir=self.work)
        first = self.responses["eval-001"]
        first["category"] = "其他"
        first["events"].pop()
        first["events"][0]["time_extraction"]["intent"]["offset"] = 2
        for response in self.responses.values():
            for event in response["events"]:
                event["title"] = "模型合理改写的事项标题"

        report = await evaluation.run_evaluation(self.cases, "live", model_factory=self.fake_factory(), work_dir=self.work)

        self.assertTrue(report["model_evaluation"])
        self.assertEqual(len(self.calls), len(self.cases))
        self.assertEqual(self.calls[0], self.cases[0].input.model_dump())
        self.assertEqual(report["metrics"]["classification_accuracy"]["passed"], len(self.cases) - 1)
        self.assertEqual(report["metrics"]["event_count_accuracy"]["passed"], len(self.cases) - 1)
        self.assertLess(report["metrics"]["time_field_accuracy"]["score"], 1)
        self.assertEqual(report["metrics"]["time_field_accuracy"]["total"], baseline["metrics"]["time_field_accuracy"]["total"])
        self.assertTrue(all(case["passed"] for case in report["cases"][1:]))
        self.assertFalse(evaluation.report_passes(report))

    async def test_model_attachment_count_is_distinct_from_retained_source_hints(self):
        """模型漏算附件不会伪装成原始线索丢失，两个指标保持独立且都展示真实分母。"""
        self.responses["eval-001"]["attachment_count"] = 0
        report = await evaluation.run_evaluation(self.cases, "live", model_factory=self.fake_factory(), work_dir=self.work)

        self.assertEqual(report["metrics"]["attachment_hint_retention"], {"passed": 4, "total": 4, "score": 1.0})
        self.assertEqual(report["metrics"]["attachment_count_accuracy"]["passed"], len(self.cases) - 1)
        self.assertEqual(report["metrics"]["attachment_count_accuracy"]["total"], len(self.cases))
        self.assertFalse(report["cases"][0]["passed"])

    async def test_renewal_notice_false_positive_and_cancel_action_false_negative_are_scored(self):
        """未来自动续费告知不要求行动，而取消要求是行动；评测能分别识别多提和漏提，非证明模型语义保证。"""
        cases = [case for case in self.cases if case.case_id in ("eval-009", "eval-010")]
        baseline = await evaluation.run_evaluation(cases, work_dir=self.work)
        self.assertTrue(evaluation.report_passes(baseline))
        self.assertEqual([case["actual"]["event_count"] for case in baseline["cases"]], [0, 1])
        deadline = baseline["cases"][1]["actual"]["events"][0]["time"]
        self.assertEqual(deadline["time_kind"], "deadline")
        self.assertEqual(deadline["at"], "2026-10-17T15:00:00+08:00")

        for wrong_id in ("eval-009", "eval-010"):
            with self.subTest(wrong_id=wrong_id):
                responses = copy.deepcopy(self.responses)
                if wrong_id == "eval-009":
                    # 伪造“把自动服务事实当待办”的合法结构化输出，以验证误提能被独立预期发现。
                    event = copy.deepcopy(responses["eval-010"]["events"][0])
                    event.update({
                        "title": "腾讯视频VIP自动续费",
                        "time_text": "明天下午三点",
                        "evidence": "腾讯视频VIP将在明天下午三点自动续费，无需手动操作",
                    })
                    event["time_extraction"]["intent"]["is_deadline"] = False
                    responses[wrong_id]["events"] = [event]
                else:
                    # 伪造“看见续费关键词就全丢弃”的输出，避免修误报时掩盖明确操作的漏报。
                    responses[wrong_id]["events"] = []
                report = await evaluation.run_evaluation(
                    cases, "live", model_factory=partial(EvaluationModel, responses, []), work_dir=self.work,
                )
                self.assertEqual(report["metrics"]["classification_accuracy"]["score"], 1.0)
                self.assertEqual(report["metrics"]["event_count_accuracy"], {"passed": 1, "total": 2, "score": 0.5})
                failed = next(case for case in report["cases"] if case["case_id"] == wrong_id)
                self.assertIsNone(failed["error_code"])
                self.assertFalse(failed["passed"])
                self.assertFalse(evaluation.report_passes(report))

    async def test_provider_and_evidence_failures_keep_cases_without_leaking_raw_errors(self):
        """提供商失败与伪造证据均保留在分母中，报告只记异常类型并继续后续样例。"""
        secret = "SYNTHETIC_PROVIDER_SECRET_DO_NOT_COPY"
        self.responses["eval-001"] = APIError(secret, request=httpx.Request("POST", "https://invalid.test"), body={"secret": secret})
        self.responses["eval-002"]["events"][0]["evidence"] = "原文中不存在的伪造证据"

        report = await evaluation.run_evaluation(self.cases, "live", model_factory=self.fake_factory(), work_dir=self.work)

        self.assertEqual(len(report["cases"]), len(self.cases))
        self.assertEqual(report["cases"][0]["error_code"], "APIError")
        self.assertEqual(report["cases"][1]["error_code"], "ValueError")
        self.assertEqual(report["metrics"]["classification_accuracy"]["total"], len(self.cases))
        self.assertEqual(report["metrics"]["classification_accuracy"]["passed"], len(self.cases) - 2)
        self.assertTrue(all(case["passed"] for case in report["cases"][2:]))
        self.assertNotIn(secret, json.dumps(report, ensure_ascii=False))
        self.assertEqual(report["traces"][0]["status"], "error")
        self.assertEqual(list(self.work.iterdir()), [])

    async def test_time_scoring_checks_reference_timezone_and_forbids_fake_midnight(self):
        """判分同时核对原始时区基准并拒绝日期多出at，缺失事项的全部预期仍参与计分。"""
        report = await evaluation.run_evaluation(self.cases, work_dir=self.work)
        actual = copy.deepcopy(report["cases"][1]["actual"])
        detail = {
            "analysis": {"category": actual["category"], "attachment_count": actual["attachment_count"]},
            "events": actual["events"], "source_message": self.cases[1].input.model_dump(), "attachments": [],
        }
        time_result = detail["events"][0]["time"]
        time_result["at"] = "2026-10-14T00:00:00+08:00"
        time_result["reference_time"] = "2026-10-17T10:00:00+08:00"
        time_result["timezone"] = "UTC"

        checks = evaluation.score_case(self.cases[1], detail)
        failures = {check["name"] for check in checks if not check["passed"]}

        self.assertIn("event[0].forbid_at", failures)
        self.assertIn("event[0].reference_time", failures)
        self.assertIn("event[0].timezone", failures)
        missing = evaluation.score_case(self.cases[1], None)
        expected_time_checks = [check for check in checks if check["metric"] == "time_field_accuracy"]
        missing_time_checks = [check for check in missing if check["metric"] == "time_field_accuracy"]
        self.assertEqual(len(missing_time_checks), len(expected_time_checks))
        self.assertTrue(all(not check["passed"] for check in missing_time_checks))

    async def test_bad_cases_rejected_before_database_or_model_and_errors_are_safe(self):
        """重复来源、非法日期、坏字段及非JSON配置提前拒绝，不创建业务目录或输出坏行正文。"""
        raw = [case.model_dump() for case in self.cases]
        invalid_sets = []
        invalid_sets.append([raw[0], copy.deepcopy(raw[0])])
        wrong_date = copy.deepcopy(raw)
        wrong_date[1]["expected"]["events"][0]["time"]["date"] = "2026-02-30"
        invalid_sets.append(wrong_date)
        wrong_field = copy.deepcopy(raw)
        wrong_field[0]["expected"]["misspelled_category"] = "研究"
        invalid_sets.append(wrong_field)
        for invalid in invalid_sets:
            with self.subTest(kind=len(invalid)):
                with self.assertRaises(evaluation.EvaluationConfigurationError):
                    await evaluation.run_evaluation(invalid, model_factory=self.fake_factory(), work_dir=self.work)
        self.assertFalse(self.work.exists())
        self.assertEqual(self.calls, [])
        path = self.folder / "bad.jsonl"
        path.write_text("SYNTHETIC_SECRET_INVALID_JSON", encoding="utf-8")
        with self.assertRaises(evaluation.EvaluationConfigurationError) as captured:
            evaluation.load_cases(path)
        self.assertNotIn("SYNTHETIC_SECRET", str(captured.exception))
        for threshold in (float("nan"), float("inf"), -0.1, 1.1, True):
            with self.assertRaises(evaluation.EvaluationConfigurationError):
                evaluation.report_passes({"metrics": {}}, threshold)

    async def test_trace_failure_does_not_change_scores_or_business_execution(self):
        """Trace开始、Span、结束和读取同时故障仍完成规则回归，不把可观测性故障计成语义错误。"""
        failure = sqlite3.OperationalError("SYNTHETIC_PRIVATE_TRACE_PATH")
        with (
            patch.object(evaluation.TraceStore, "start_trace", side_effect=failure),
            patch.object(evaluation.TraceStore, "write_span", side_effect=failure),
            patch.object(evaluation.TraceStore, "finish_trace", side_effect=failure),
            patch.object(evaluation.TraceStore, "get_trace", side_effect=failure),
            patch.object(evaluation, "safe_diagnostic"),
            patch("student_assistant.tracing.safe_diagnostic"),
        ):
            report = await evaluation.run_evaluation(self.cases, work_dir=self.work)

        self.assertTrue(all(case["passed"] for case in report["cases"]))
        self.assertTrue(evaluation.report_passes(report))
        self.assertEqual(report["traces"], [])
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(report))

    def test_cli_threshold_exit_codes_reports_and_rules_never_load_environment(self):
        """CLI默认通过返回0，改错预期返回1且保留报告，非法配置返回2，rules不读取.env。"""
        cases = self.folder / "cases.jsonl"
        raw = [case.model_dump() for case in self.cases]
        cases.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in raw), encoding="utf-8")
        output = self.folder / "report.json"
        arguments = ["--mode", "rules", "--cases", str(cases), "--output", str(output)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(cli, "ROOT", self.folder), patch("dotenv.load_dotenv") as load_environment, redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(cli.main(arguments), 0)
            self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["passed"])
            raw[0]["expected"]["category"] = "其他"
            cases.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in raw), encoding="utf-8")
            self.assertEqual(cli.main(arguments), 1)
            negative_control = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(negative_control["passed"])
            self.assertEqual(negative_control["metrics"]["classification_accuracy"], {"passed": 9, "total": 10, "score": 0.9})
            self.assertEqual(cli.main(arguments + ["--fail-under", "0.8"]), 0)
            cases.write_text("SYNTHETIC_SECRET_INVALID_JSON", encoding="utf-8")
            self.assertEqual(cli.main(arguments), 2)
            self.assertEqual(cli.main(arguments + ["--fail-under", "nan"]), 2)
            load_environment.assert_not_called()
        self.assertNotIn("SYNTHETIC_SECRET", stderr.getvalue())
        self.assertIn("EvaluationConfigurationError", stderr.getvalue())
        self.assertFalse(any(path.name.startswith("module05-eval-") for path in (self.folder / "local-data").iterdir()))


if __name__ == "__main__":
    unittest.main()
