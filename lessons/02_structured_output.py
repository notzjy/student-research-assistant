"""第二课：用 Pydantic 定义字段，让 LangChain 返回经过校验的对象。"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from langchain_core.exceptions import OutputParserException
from langchain_openai import ChatOpenAI
from openai import APIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError


NOTICE = "明天下午三点在实验楼302开组会；周五前交综述，附件是两篇参考论文。"


class Event(BaseModel):
    """定义单项事项的标题、原文时间和地点，供模型输出与本地校验共用。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, description="事项名称")
    time_text: str | None = Field(default=None, description="原文时间表达，未说明时为 null")
    location: str | None = Field(default=None, description="该事项的地点，未说明时为 null")


class NoticeAnalysis(BaseModel):
    """定义整条通知的类别、摘要、事项与附件信息，作为结构化输出格式。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    category: Literal["研究", "学生生活", "求职", "其他"] = Field(description="消息主要类别")
    summary: str = Field(description="用一句话概括原通知")
    events: list[Event] = Field(description="需要参加、处理或提交的事项；没有则为空列表")
    attachment_description: str | None = Field(default=None, description="原文中的附件描述")
    attachment_count: int | None = Field(
        default=None, ge=0, description="消息明确提到的附件数量；未明确数量则为 null，不代表实际取得文件"
    )


def build_messages(notice: str):
    """将通知原文与字段约束组装成模型输入，返回 system、human 消息列表。"""
    # JSON 模式只要求输出 JSON，具体字段约束还需通过 Prompt 告诉模型。
    schema = json.dumps(NoticeAnalysis.model_json_schema(), ensure_ascii=False)
    instruction = (
        "你是个人研究与日程助理。完整阅读通知，提取类别、事项及附件信息，只输出 JSON。"
        "一条消息中的多个事项分别记录。时间保留原话，不换算日期，不补造具体截止钟点。"
        "每个地点只关联它实际对应的事项。未知信息使用 null，别虚构文件名、内容或下载状态。"
        "例如：输入‘周三前登记宿舍信息’，可以输出 "
        '{"category":"学生生活","summary":"周三前登记宿舍信息","events":[{"title":"登记宿舍信息",'
        '"time_text":"周三前","location":null}],"attachment_description":null,'
        '"attachment_count":null}。示例只用于说明格式，结果以当前通知为准。'
        "\n输出须符合下面的 JSON Schema：\n" + schema
    )
    return [("system", instruction), ("human", notice)]


def analyze_notice(notice: str) -> NoticeAnalysis:
    """分析通知原文，返回通过 Pydantic 字段校验的 NoticeAnalysis 对象。"""
    model = ChatOpenAI(
        model=os.environ["COMPATIBLE_MODEL"],
        base_url=os.environ["COMPATIBLE_BASE_URL"],
        api_key=os.environ["COMPATIBLE_API_KEY"],
        timeout=60,
        max_retries=0,
        http_socket_options=(),
    )
    # LangChain 解析模型返回的 JSON，并用 NoticeAnalysis 检查字段和类型。
    structured_model = model.with_structured_output(NoticeAnalysis, method="json_mode")
    return structured_model.invoke(build_messages(notice))


def show_validation_demo():
    """使用本地非法数据演示字段校验，把具体错误打印到终端。"""
    print("本地校验实验：不调用模型。故意传入非法类别和负数附件数量。")
    try:
        NoticeAnalysis.model_validate({
            "category": "娱乐", "summary": "本地校验示例", "events": [], "attachment_count": -1
        })
    except ValidationError as error:
        for item in error.errors():
            print("字段:", ".".join(map(str, item["loc"])), "错误:", item["msg"])


def main():
    """解析命令行参数，执行本地校验、输入预览或真实结构化分析并显示结果。"""
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", default=NOTICE, help="替换输入通知")
    parser.add_argument("--preview", action="store_true", help="只预览 schema 和模型输入")
    parser.add_argument("--validate-demo", action="store_true", help="本地演示校验错误，不调用模型")
    args = parser.parse_args()

    if args.validate_demo:
        show_validation_demo()
        return
    if args.preview:
        print("输入与 schema 预览：本次不调用模型。")
        for role, content in build_messages(args.message):
            print(role + ":", content)
        return

    required = ("COMPATIBLE_MODEL", "COMPATIBLE_BASE_URL", "COMPATIBLE_API_KEY")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        parser.exit(2, "尚未配置：" + ", ".join(missing) + "。沿用第一课的本机 .env。\n")

    try:
        result = analyze_notice(args.message)
    except OutputParserException:
        parser.exit(1, "模型返回未通过结构校验。请重新运行；类型校验不代表内容事实正确。\n")
    except APIError as error:
        parser.exit(1, f"模型请求失败：{type(error).__name__}，HTTP 状态 {getattr(error, 'status_code', None)}。\n")

    print("已通过 Pydantic 结构校验的结果：")
    print(result.model_dump_json(indent=2))
    print("\n用 Python 读取对象字段：")
    print("类别:", result.category)
    print("事项数:", len(result.events))
    for event in result.events:
        print("事项:", event.title, "| 时间原文:", event.time_text, "| 地点:", event.location)
    print("消息提到的附件数量:", result.attachment_count)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
