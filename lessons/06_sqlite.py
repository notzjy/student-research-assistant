"""第六课：直接操作 SQLite，观察待办的保存、查询、完成与旧 JSON 导入。"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from task_store import DEFAULT_DB, ROOT, TaskStore


def print_json(value):
    """将操作结果以中文可读的 JSON 打印到终端，方便与数据库记录核对。"""
    print(json.dumps(value, ensure_ascii=False, indent=2))


def run_demo(store: TaskStore) -> dict:
    """保存两条教学待办、完成其中一条并查询结果；重复运行不会增加相同记录。"""
    review = store.save_task("[教学示例] 交综述", time_text="周五前")
    meeting = store.save_task("[教学示例] 参加组会", time_text="明天下午三点", location="实验楼302")
    # 后续 Agent 工具将调用同样的存储方法；此处先直接调用，观察 SQL 的作用。
    completed = store.mark_completed(review["task"]["id"])
    return {
        "save_results": [review, meeting],
        "completed_task": completed,
        "all_tasks": store.list_tasks(),
        "pending_tasks": store.list_tasks(status="pending"),
        # 与上面的未完成列表核对，观察 SQL 聚合查询如何返回一个数量。
        "pending_count": store.count_pending(),
    }


def main():
    """解析命令行并执行一种数据库操作；无参数时运行教学示例，不调用模型。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 文件路径")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--list", action="store_true", help="查询数据库中已保存的待办")
    modes.add_argument("--add", metavar="TITLE", help="直接保存一条待办标题")
    modes.add_argument("--complete", metavar="ID", help="将指定 ID 的待办标记为完成")
    modes.add_argument(
        "--import-json", nargs="?", type=Path, const=ROOT / "local-data" / "lesson03_tasks.json",
        metavar="PATH", help="导入旧 JSON；省略路径时导入第三课的文件，原文件保持不变",
    )
    parser.add_argument("--status", choices=("pending", "completed"), help="配合 --list 按状态查询")
    parser.add_argument("--time-text", help="配合 --add 保存时间原话")
    parser.add_argument("--location", help="配合 --add 保存地点")
    args = parser.parse_args()
    if args.status is not None and not args.list:
        parser.error("--status 需要与 --list 一起使用")
    if (args.time_text is not None or args.location is not None) and args.add is None:
        parser.error("--time-text 和 --location 需要与 --add 一起使用")

    try:
        store = TaskStore(args.db)
        print("数据库文件:", store.db_path)
        if args.list:
            print_json(store.list_tasks(status=args.status))
        elif args.add is not None:
            print_json(store.save_task(args.add, time_text=args.time_text, location=args.location))
        elif args.complete is not None:
            task = store.mark_completed(args.complete)
            if task is None:
                parser.exit(1, "没有找到这个待办 ID；请先用 --list 查询。\n")
            print_json(task)
        elif args.import_json is not None:
            print_json(store.import_json(args.import_json))
        else:
            print_json(run_demo(store))
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"数据库操作失败：{type(error).__name__}: {error}\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
