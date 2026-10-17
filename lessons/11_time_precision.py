"""第十一课：比较日期、精确时刻和日期范围，支持离线演示与真实模型/HTTP解释。"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from langchain_core.exceptions import OutputParserException
from openai import APIError
from pydantic import TypeAdapter

from time_parser import TimeNormalizationRequest
from time_semantics import (
    RelativeTimeIntent, TimeExtraction, TimeInterpretation, build_time_messages, interpret_time, resolve_intent,
)


DEMO_REFERENCE = "2026-10-16T20:00:00+08:00"


def show_demo(reference_time: str):
    """用三份明确标注的预设意图展示不同结果精度，不调用真实模型。"""
    examples = [
        ("周三", RelativeTimeIntent(unit="week", weekday=2)),
        ("周三15:00", RelativeTimeIntent(unit="week", weekday=2, hour=15, minute=0)),
        ("下个月", RelativeTimeIntent(unit="month", offset=1)),
    ]
    records = []
    for text, intent in examples:
        request = TimeNormalizationRequest(time_text=text, reference_time=reference_time)
        result = resolve_intent(request, TimeExtraction(intent=intent))
        records.append({"preset_intent": intent.model_dump(), "result": result.model_dump()})
    print("本地预设意图演示：不调用模型；不能据此证明模型识别正确。")
    print(json.dumps(records, ensure_ascii=False, indent=2))


def main():
    """运行本地精度演示、预览模型输入，或通过模型/现有HTTP服务解释时间。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--time-text", default="周三", help="待解释的原文时间表达")
    parser.add_argument("--reference-time", help="原消息发送时间，必须携带时区偏移")
    parser.add_argument("--base-url", help="可选：使用现有服务的/time/interpret接口")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--demo", action="store_true", help="用预设意图演示，不调用模型")
    modes.add_argument("--preview", action="store_true", help="预览模型输入和schema，不调用模型")
    args = parser.parse_args()
    if args.base_url and (args.demo or args.preview):
        parser.error("--base-url 不与 --demo 或 --preview 同时使用")
    if not args.demo and args.reference_time is None:
        parser.error("真实解释或预览时必须显式提供 --reference-time")

    try:
        if args.demo:
            show_demo(args.reference_time or DEMO_REFERENCE)
            return
        request = TimeNormalizationRequest(time_text=args.time_text, reference_time=args.reference_time)
        if args.preview:
            for role, content in build_time_messages(request):
                print(role + ":", content)
            return
        if args.base_url:
            with httpx.Client(base_url=args.base_url, timeout=90, trust_env=False) as client:
                response = client.post("/time/interpret", json=request.model_dump())
            print("POST /time/interpret HTTP 状态:", response.status_code)
            if not response.is_success:
                print(json.dumps(response.json(), ensure_ascii=False, indent=2))
                raise SystemExit(1)
            result = TypeAdapter(TimeInterpretation).validate_python(response.json())
        else:
            load_dotenv(Path(__file__).resolve().parents[1] / ".env")
            names = ("COMPATIBLE_MODEL", "COMPATIBLE_BASE_URL", "COMPATIBLE_API_KEY")
            missing = [name for name in names if not os.environ.get(name, "").strip()]
            if missing:
                parser.exit(2, "尚未配置：" + ", ".join(missing) + "。沿用已有.env。\n")
            result = interpret_time(request)
    except APIError as error:
        parser.exit(1, f"模型请求失败：{type(error).__name__}；HTTP状态{getattr(error, 'status_code', None)}。\n")
    except httpx.HTTPError:
        parser.exit(1, "无法访问时间解释接口，请检查服务地址和端口。\n")
    except (OutputParserException, ValueError):
        parser.exit(2, "输入、模型时间结构或计算结果未通过校验；请检查日期范围和字段。\n")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
