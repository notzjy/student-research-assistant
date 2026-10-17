"""评测固定合成消息：rules只检查程序规则，live才调用本机配置的真实模型。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from student_assistant.bridge import model_is_configured
from student_assistant.evaluation import DEFAULT_CASES, EvaluationConfigurationError, load_cases, report_passes, run_evaluation


def main(argv: list[str] | None = None) -> int:
    """解析样例和阈值，生成报告；通过返回0，低于阈值返回1，配置或文件错误返回2。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("rules", "live"), default="rules", help="rules不调用模型，live使用真实模型")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="固定JSONL；可复制后修改预期进行练习")
    parser.add_argument("--output", type=Path, help="报告JSON位置；默认写入local-data")
    parser.add_argument("--fail-under", type=float, default=1.0, help="每项指标的最低分数，0到1，默认1")
    args = parser.parse_args(argv)
    try:
        # 先校验阈值和样例；离线模式不加载.env，不启动服务，也不访问默认业务库。
        report_passes({"metrics": {"validation": {"score": 1.0}}}, args.fail_under)
        cases = load_cases(args.cases)
        if args.mode == "live":
            from dotenv import load_dotenv

            load_dotenv(ROOT / ".env")
            if not model_is_configured():
                print("评测配置无效：ModelConfigurationMissing", file=sys.stderr)
                return 2
        report = asyncio.run(run_evaluation(cases, args.mode, work_dir=ROOT / "local-data"))
        passed = report_passes(report, args.fail_under)
        report["fail_under"] = args.fail_under
        report["passed"] = passed
        output = args.output or ROOT / "local-data" / f"module05_{args.mode}_report.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    except Exception as error:
        # 配置、I/O和初始化异常只打印类型；不回显样例正文、模型响应或本机凭证。
        error_code = "EvaluationConfigurationError" if isinstance(error, EvaluationConfigurationError) else type(error).__name__
        print("评测未运行完成：" + error_code, file=sys.stderr)
        return 2
    print("真实模型小样本评测" if report["model_evaluation"] else "程序规则回归（非模型能力指标）")
    for name, metric in report["metrics"].items():
        print(f"{name}: {metric['passed']}/{metric['total']} = {metric['score']:.2%}")
    print("报告已保存：", output)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
