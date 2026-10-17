"""第一课：把完整通知交给模型，查看真实返回；--preview 只预览输入。"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


INSTRUCTION = (
    "请判断消息类别，只回答：研究、学生生活、求职、其他中的一个。"
    "不要解释，不要输出其他内容。"
)
NOTICE = "明天下午三点在实验楼302开组会；周五前交综述，附件是两篇参考论文。"


def analyze_notice(notice: str):
    """将通知原文 notice 交给模型分类，返回模型回复的正文。"""
    model = ChatOpenAI(
        model=os.environ["COMPATIBLE_MODEL"],
        base_url=os.environ["COMPATIBLE_BASE_URL"],
        api_key=os.environ["COMPATIBLE_API_KEY"],
        timeout=60,
        max_retries=0,
        http_socket_options=(),
    )
    # system 定义分类任务，human 保留用户提供的完整通知。
    response = model.invoke([
        ("system", INSTRUCTION),
        ("human", notice),
    ])
    return response.content


def main():
    """读取本机配置和命令行参数，执行输入预览或打印真实分类结果。"""
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="只显示模型输入，不发送请求")
    parser.add_argument("--message", default=NOTICE, help="替换示例通知")
    args = parser.parse_args()

    if args.preview:
        print("输入预览：本次没有调用模型，也没有生成分析结果。")
        print("system:", INSTRUCTION)
        print("human:", args.message)
        return

    required = ("COMPATIBLE_MODEL", "COMPATIBLE_BASE_URL", "COMPATIBLE_API_KEY")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        parser.exit(2, "尚未配置：" + ", ".join(missing) + "。请参照 lessons/README.md。\n")

    print(analyze_notice(args.message))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
