"""第十课：直接调用时间解析函数，或通过已有 HTTP 服务预览相同的解析结果。"""

import argparse
import json
import sys

import httpx

from time_parser import NormalizedTime, TimeNormalizationRequest, normalize_time, parse_reference_time


def main():
    """读取原文和原消息时间，运行本地解析或 HTTP 预览，打印结构化时间结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--time-text", default="明天下午三点", help="已提取的原文时间表达，不是整条通知")
    parser.add_argument("--reference-time", required=True, help="原消息发送时间，例如2026-10-15T20:00:00+08:00")
    parser.add_argument("--base-url", help="可选：改用现有服务的/time/normalize接口，例如http://127.0.0.1:8001")
    args = parser.parse_args()
    try:
        request = TimeNormalizationRequest(time_text=args.time_text, reference_time=args.reference_time)
        if args.base_url:
            # HTTP 入口与本地模式使用相同字段；这次请求不调用模型或修改待办。
            with httpx.Client(base_url=args.base_url, timeout=10, trust_env=False) as client:
                response = client.post("/time/normalize", json=request.model_dump())
            print("POST /time/normalize HTTP 状态:", response.status_code)
            if not response.is_success:
                print(json.dumps(response.json(), ensure_ascii=False, indent=2))
                raise SystemExit(1)
            result = NormalizedTime.model_validate(response.json())
        else:
            result = normalize_time(request.time_text, parse_reference_time(request.reference_time))
    except httpx.HTTPError:
        parser.exit(1, "无法访问时间预览接口，请检查服务地址和端口。\n")
    except ValueError as error:
        parser.exit(2, f"时间输入或结果校验失败：{error}\n")

    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
