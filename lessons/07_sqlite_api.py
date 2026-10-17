"""第七课：通过依赖注入，把 SQLite 待办存储接入现有 Agent 和 HTTP 服务。"""

import argparse
import importlib
import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

from task_store import ROOT, TaskStore


lesson05 = importlib.import_module("05_fastapi")
DEFAULT_DB = ROOT / "local-data" / "assistant.sqlite3"


def create_app(
    db_path: Path | str = DEFAULT_DB,
    model_factory: Callable | None = None,
    *,
    import_json: Path | None = None,
) -> FastAPI:
    """创建一个共享指定数据库的应用；可选导入旧 JSON，导入失败时不启动应用。"""
    store = TaskStore(db_path)
    if import_json is not None:
        # 迁移必须由启动参数明确指定；整批导入失败时异常会阻止继续启动。
        counts = store.import_json(import_json)
        print("旧 JSON 导入结果:", json.dumps(counts, ensure_ascii=False))

    # 只创建一次存储，将它传给 API；API 再传给图和保存工具。
    api = lesson05.create_app(model_factory=model_factory, store=store)

    @api.get("/tasks/pending/count", summary="查询 SQLite 未完成待办数量")
    def count_pending_tasks() -> dict[str, int]:
        """使用注入的同一存储统计未完成数量，不创建模型或读取旧 JSON。"""
        try:
            return {"count": store.count_pending()}
        except (OSError, ValueError, sqlite3.Error):
            raise HTTPException(status_code=500, detail="待办数量查询失败，请检查服务端数据。") from None
    
    @api.get("/tasks/pending", summary="查询未完成待办列表")
    def get_pending_tasks() -> dict:
        """使用已有存储查询未完成列表，不调用模型。"""
        try:
            # 复用外层创建的 store，由存储层负责 SQL 查询。
            tasks = store.list_tasks(status="pending")
            return {"count": len(tasks), "tasks": tasks}
        except (OSError, ValueError, sqlite3.Error):
            raise HTTPException(
                status_code=500,
                detail="未完成待办查询失败，请检查服务端数据。",
            ) from None
    return api


def main():
    """读取本机配置与数据库参数，启动仅本机可访问的第七课服务，默认端口 8001。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001, help="服务端口，默认 8001")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 数据库文件路径")
    parser.add_argument(
        "--import-json", nargs="?", type=Path, const=ROOT / "local-data" / "lesson03_tasks.json",
        metavar="PATH", help="启动前导入旧 JSON；省略路径时导入第三课文件",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 到 65535 之间")

    # 导入本模块不读配置、不创建业务数据库；只有运行入口负责初始化。
    load_dotenv(ROOT / ".env")
    try:
        api = create_app(args.db, import_json=args.import_json)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"存储初始化或导入失败：{type(error).__name__}；服务未启动。\n")
    print("本次 SQLite 数据库:", args.db.resolve())
    uvicorn.run(api, host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
