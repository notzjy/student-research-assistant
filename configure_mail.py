"""本机交互配置 QQ 只读邮箱接入；授权码隐藏输入，不在终端或对话中展示。"""

from getpass import getpass
from pathlib import Path

from dotenv import set_key


def main() -> None:
    """将用户本机输入追加或更新到现有 .env，保留已配置的 DeepSeek 参数。"""
    account = input("QQ 邮箱完整地址（例如 123456@qq.com）：").strip()
    if not account or "@" not in account or any(char in account for char in "\r\n"):
        raise SystemExit("邮箱地址格式不正确，未保存。")
    authorization = getpass("QQ 邮箱 IMAP 授权码（隐藏输入，不是登录密码）：").strip()
    if not authorization or any(char in authorization for char in "\r\n"):
        raise SystemExit("授权码不能为空或包含换行，未保存。")
    config = Path(__file__).resolve().parent / ".env"
    # set_key 逐键更新，不能用 .env.example 覆盖用户已有模型配置。
    for key, value in {
        "QQ_MAIL_ACCOUNT": account, "QQ_MAIL_AUTH_CODE": authorization,
        "QQ_MAIL_ENABLED": "true", "QQ_MAIL_POLL_SECONDS": "60", "QQ_MAIL_INITIAL_LIMIT": "5",
    }.items():
        set_key(config, key, value, quote_mode="always", encoding="utf-8")
    print("配置已写入本机 .env；重启服务后启用读取。首次仅接收最新 5 封，授权码未回显。")


if __name__ == "__main__":
    main()
