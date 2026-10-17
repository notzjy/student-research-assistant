"""第五课客户端：通过 HTTP 把通知发给本机服务，并查询保存结果。"""

import argparse
import json
import sys

import httpx


NOTICE = "明天下午三点在实验楼302开组会；周五前交综述，附件是两篇参考论文。"


def main():
    """读取服务地址和通知，发送 POST 请求后打印响应，再用 GET 查询待办。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="本机 FastAPI 服务地址")
    parser.add_argument("--message", default=NOTICE, help="要处理的通知原文")
    args = parser.parse_args()

    # 只向本机服务发送通知，模型密钥继续由服务端的 .env 读取。
    with httpx.Client(base_url=args.base_url, timeout=180, trust_env=False) as client:
        response = client.post("/messages", json={"text": args.message})
        print("POST /messages HTTP 状态:", response.status_code)
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
        if not response.is_success:
            raise SystemExit(1)

        # GET 查询复用已经保存的数据，不再次调用模型。
        tasks = client.get("/tasks")
        tasks.raise_for_status()
        print("\nGET /tasks：")
        print(json.dumps(tasks.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
