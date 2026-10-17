"""启动工具包的个人助理主服务，使用独立业务数据库与持久线程检查点。"""

import argparse
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent


def main():
    """解析端口和数据库路径，加载本机配置，以单进程方式启动 student 服务配置档。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1", help="本机默认仅监听回环；容器内部可指定0.0.0.0")
    parser.add_argument("--port", type=int, default=8002, help="本机服务端口，默认 8002")
    parser.add_argument("--db", type=Path, default=ROOT / "local-data" / "assistant.sqlite3", help="业务数据库文件")
    parser.add_argument(
        "--checkpoints", type=Path, default=ROOT / "local-data" / "assistant_checkpoints.sqlite3",
        help="保存线程消息与图执行状态的独立 SQLite 文件",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 到 65535 之间")
    business_db = args.db.resolve()
    checkpoint_db = args.checkpoints.resolve()
    if business_db == checkpoint_db:
        parser.error("业务数据库与 checkpoint 数据库必须使用不同文件")

    # 显式参数通过环境传给 service:app；不调用会导入多供应商模块的上游启动器。
    os.environ["AGENT_SERVICE_PROFILE"] = "student"
    os.environ["STUDENT_ASSISTANT_DB"] = str(business_db)
    os.environ["STUDENT_ASSISTANT_CHECKPOINT_DB"] = str(checkpoint_db)
    os.environ['STUDENT_ASSISTANT_BIND_HOST'] = args.host
    source_path = str(ROOT / "agent-service-toolkit" / "src")
    if source_path not in sys.path:
        sys.path.insert(0, source_path)
    load_dotenv(ROOT / ".env")

    print("正在启动个人助理主服务:", f"http://{args.host}:{args.port}")
    print("业务数据库:", business_db)
    print("线程检查点:", checkpoint_db)
    # 当前仅运行一个本机服务进程；线程内并发控制由引擎负责。
    uvicorn.run("service:app", host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
