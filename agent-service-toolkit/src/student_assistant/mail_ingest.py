"""QQ 邮箱只读收件：短连接收取真实 EML，再将 MIME 解析为现有消息和附件协议。"""

import imaplib
import math
import mimetypes
import os
import re
import ssl
import threading
import time
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from student_assistant.message_models import AttachmentHint, MessageInput


@dataclass
class MailSettings:
    """记录本机邮箱连接选项；授权码不进入对象 repr，enabled 仅控制后台自动收取。"""

    enabled: bool = False
    account: str = ""
    auth_code: str = field(default="", repr=False)
    host: str = "imap.qq.com"
    port: int = 993
    poll_seconds: int = 60
    batch_size: int = 10
    initial_limit: int = 5
    max_message_bytes: int = 25 * 1024 * 1024
    timeout: float = 10

    def __post_init__(self):
        """校验超时和批量参数，避免无界收取；空凭证仍允许服务以未配置状态启动。"""
        if not isinstance(self.enabled, bool):
            raise ValueError("QQ_MAIL_ENABLED 必须是布尔值")
        if not all(isinstance(value, str) for value in (self.account, self.auth_code, self.host)) or not self.host.strip():
            raise ValueError("邮箱连接配置不合法")
        for name in ("port", "poll_seconds", "batch_size", "max_message_bytes"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("邮箱端口、轮询间隔、批量和大小必须是正整数")
        if self.port > 65535 or self.batch_size > 100:
            raise ValueError("邮箱端口或批量超出上限")
        if not isinstance(self.initial_limit, int) or isinstance(self.initial_limit, bool) or not 0 <= self.initial_limit <= 100:
            raise ValueError("首次收取数量必须在 0 到 100 之间")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("邮箱超时必须是有限正数")

    @classmethod
    def from_env(cls):
        """只读取明确的 QQ_MAIL 环境变量；本模块不读取 .env 文件或输出凭证。"""
        value = os.environ.get("QQ_MAIL_ENABLED", "false").strip().lower()
        if value not in {"", "0", "false", "no", "off", "1", "true", "yes", "on"}:
            raise ValueError("QQ_MAIL_ENABLED 取值不合法")
        try:
            return cls(
                enabled=value in {"1", "true", "yes", "on"},
                account=os.environ.get("QQ_MAIL_ACCOUNT", "").strip(),
                auth_code=os.environ.get("QQ_MAIL_AUTH_CODE", "").strip(),
                poll_seconds=int(os.environ.get("QQ_MAIL_POLL_SECONDS", "").strip() or "60"),
                initial_limit=int(os.environ.get("QQ_MAIL_INITIAL_LIMIT", "").strip() or "5"),
            )
        except (TypeError, ValueError):
            raise ValueError("QQ 邮箱环境配置不合法，请在本机检查配置项") from None

    @property
    def configured(self) -> bool:
        """只表示账号和授权码是否齐全，不把配置完整误报为已经成功连接邮箱。"""
        return bool(self.account.strip() and self.auth_code.strip())


@dataclass
class MailAttachment:
    """保存一个真实 MIME 附件的解码字节，index 对应消息协议中的附件序号。"""

    index: int
    filename: str
    data: bytes = field(repr=False)
    media_type: str


@dataclass
class ParsedMail:
    """把经过校验的消息协议、真实附件字节和溯源元数据交给现有业务工作流。"""

    envelope: MessageInput
    attachments: list[MailAttachment]
    metadata: dict


class _MailHTMLText(HTMLParser):
    """仅提取 HTML 中可阅读的文字，忽略脚本样式，不渲染页面或访问外部资源。"""

    def __init__(self):
        """建立字符引用解码和屏蔽状态；兼容本机 Python 3.13.9。"""
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        """屏蔽脚本与样式，并在常见块元素前保留换行。"""
        if tag in {"script", "style", "head"}:
            self.ignored += 1
        if not self.ignored and tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        """结束屏蔽区域，并用换行隔开相邻段落。"""
        if tag in {"script", "style", "head"} and self.ignored:
            self.ignored -= 1
        if not self.ignored and tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        """只接收正文文字，避免脚本或 CSS 成为给模型的通知内容。"""
        if not self.ignored:
            self.parts.append(data)


def _decode_text(part) -> str:
    """按 MIME 声明的字符集严格解码正文，未知编码或坏字节明确失败而不悄悄替换。"""
    data = part.get_payload(decode=True)
    if not isinstance(data, bytes):
        raise ValueError("邮件正文无法解码")
    try:
        return data.decode(part.get_content_charset() or "utf-8")
    except (LookupError, UnicodeError):
        raise ValueError("邮件正文字符集或字节不合法") from None


def _safe_filename(value, part_index: int, media_type: str) -> str:
    """丢弃不可信路径和控制字符，保留安全 basename；缺失名称时按 MIME 类型生成名称。"""
    filename = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    filename = re.sub(r'[\x00-\x1f\x7f<>:"|?*]', "_", filename).strip(" .\t")
    if not filename:
        suffix = {"application/pdf": ".pdf", "text/plain": ".txt", "text/markdown": ".md"}.get(media_type)
        suffix = suffix or mimetypes.guess_extension(media_type) or ".bin"
        filename = f"attachment-{part_index}{suffix}"
    if len(filename) > 255:
        dot = filename.rfind(".")
        suffix = filename[dot:] if dot > 0 and len(filename) - dot <= 20 else ""
        filename = filename[:255 - len(suffix)] + suffix
    return filename


def parse_mail(raw: bytes, account_id: str, mailbox: str, uidvalidity: str, uid: int) -> ParsedMail:
    """解析原始 EML，保留完整正文和真实 Date；超限或缺少可信解析边界时抛 ValueError。"""
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("邮件原文必须是非空 bytes")
    if not isinstance(uid, int) or isinstance(uid, bool) or uid < 1:
        raise ValueError("邮件 UID 不合法")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    from_headers = message.get_all("From", [])
    if len(from_headers) != 1:
        raise ValueError("邮件必须有唯一 From 声明")
    addresses = getaddresses([str(from_headers[0])], strict=True)
    if len(addresses) != 1 or not addresses[0][1] or "@" not in addresses[0][1]:
        raise ValueError("邮件 From 必须包含一个有效邮箱地址")
    sender_name, address = addresses[0]
    if any(character.isspace() or ord(character) < 32 for character in address):
        raise ValueError("发件人邮箱地址不合法")
    local, domain = address.rsplit("@", 1)
    sender_id = local + "@" + domain.lower()
    dates = message.get_all("Date", [])
    try:
        sent_at = parsedate_to_datetime(str(dates[0])) if len(dates) == 1 else None
        if sent_at is None or sent_at.tzinfo is None or sent_at.utcoffset() is None:
            raise ValueError("邮件 Date 必须有有效的时区偏移")
    except (TypeError, ValueError, OverflowError):
        raise ValueError("邮件 Date 缺失、损坏或没有有效时区偏移") from None
    subject = str(message.get("Subject", ""))
    body_part = message.get_body(preferencelist=("plain", "html"))
    body = _decode_text(body_part) if body_part is not None else ""
    if body_part is not None and body_part.get_content_type() == "text/html":
        parser = _MailHTMLText()
        parser.feed(body)
        parser.close()
        body = "".join(parser.parts).strip()
    if not subject.strip() and not body.strip():
        raise ValueError("邮件没有可供分析的主题或正文")
    text = "主题：" + (subject or "（无主题）") + "\n\n" + body
    if len(text) > 5000:
        raise ValueError("邮件主题与正文合计超过现有 5000 字符上限，原始 EML 保留供处理")
    attachments, hints, attachment_metadata = [], [], []
    stack = [message]
    part_index = -1
    while stack:
        part = stack.pop()
        part_index += 1
        original_filename = part.get_filename()
        is_attachment = part.get_content_disposition() == "attachment" or original_filename is not None
        if is_attachment:
            data = part.get_payload(decode=True)
            if not isinstance(data, bytes):
                raise ValueError("该 MIME 附件无法提取为原始字节，完整 EML 已保留")
            if len(attachments) >= 20:
                raise ValueError("邮件附件超过现有 20 项上限，不能静默丢弃")
            media_type = part.get_content_type()
            filename = _safe_filename(original_filename, part_index, media_type)
            index = len(attachments)
            attachments.append(MailAttachment(index=index, filename=filename, data=data, media_type=media_type))
            hints.append(AttachmentHint(
                filename=filename, media_type=media_type, source_attachment_id=f"part-{part_index}",
            ))
            attachment_metadata.append({
                "index": index, "mime_part_index": part_index, "original_filename": original_filename,
                "filename": filename, "media_type": media_type, "byte_count": len(data),
            })
        elif part.is_multipart():
            # 不把附带的另一封邮件展开成当前发送人的正文或附件。
            if part.get_content_type().startswith("message/"):
                raise ValueError("嵌套邮件 MIME 暂需人工处理，完整 EML 已保留")
            stack.extend(reversed(list(part.iter_parts())))
    envelope = MessageInput(
        source="qq_mail", account_id=account_id, source_message_id=f"{mailbox}:{uidvalidity}:{uid}",
        sender_id=sender_id, sender_name=sender_name or None, sent_at=sent_at.isoformat(),
        text=text, attachments=hints,
    )
    return ParsedMail(envelope=envelope, attachments=attachments, metadata={
        "subject": subject, "message_id_header": str(message["Message-ID"]) if message["Message-ID"] else None,
        "sender_identity_basis": "declared_from", "attachments": attachment_metadata,
    })


class MailCollectionError(Exception):
    """公开安全的收取失败类型，不携带账号、授权码或 IMAP 服务器原始错误。"""

    def __init__(self, error_code: str):
        """只保留异常类名，供后台状态和测试区分配置、网络与协议问题。"""
        super().__init__("邮箱只读收取未完成")
        self.error_code = error_code


class ImapCollector:
    """以短 SSL 连接只读收取 INBOX，收取高水位与后续模型处理进度分开。"""

    def __init__(self, settings: MailSettings, connection_factory=None):
        """注入连接工厂供离线测试；构造时不登录或检查真实邮箱。"""
        self.settings = settings
        self.connection_factory = imaplib.IMAP4_SSL if connection_factory is None else connection_factory
        self._lock = threading.Lock()

    def _command(self, connection, deadline, name, *args, **kwargs):
        """每条命令使用剩余轮次预算设置 socket 超时，避免单轮持续阻塞。"""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("邮箱收取达到本轮时间预算")
        socket = getattr(connection, "sock", None)
        if socket is not None:
            socket.settimeout(min(self.settings.timeout, remaining))
        return getattr(connection, name)(*args, **kwargs)

    @staticmethod
    def _metadata(data, expected_uid: int) -> dict | None:
        """检查 FETCH 中的真实 UID 和大小，不把响应前的邮件序号误当 UID。"""
        for item in data or []:
            header = item[0] if isinstance(item, tuple) else item
            if not isinstance(header, bytes):
                continue
            uid_match = re.search(rb"\bUID\s+(\d+)\b", header)
            size_match = re.search(rb"\bRFC822\.SIZE\s+(\d+)\b", header)
            if uid_match is not None and int(uid_match.group(1)) == expected_uid and size_match is not None:
                internal_date = re.search(rb'\bINTERNALDATE\s+"([^"]+)"', header)
                return {
                    "rfc822_size": int(size_match.group(1)),
                    "internal_date": internal_date.group(1).decode("ascii") if internal_date else None,
                }
        return None

    @staticmethod
    def _raw(data, expected_uid: int) -> bytes:
        """遍历 FETCH 全部响应，提取 UID 相符的完整正文 literal，忽略异步附加数据。"""
        header_parts = []
        literal = None
        for item in data or []:
            header = item[0] if isinstance(item, tuple) and len(item) == 2 else item
            if not isinstance(header, bytes):
                continue
            if re.match(rb"\d+\s+\(", header) and header_parts:
                uid_match = re.search(rb"\bUID\s+(\d+)\b", b" ".join(header_parts))
                if uid_match is not None and int(uid_match.group(1)) == expected_uid and literal is not None:
                    return literal
                header_parts, literal = [], None
            header_parts.append(header)
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes) and b"BODY[]" in header:
                literal = item[1]
        # 服务器可以把 UID 放在正文 literal 后面，不能只检查 tuple 的首段。
        uid_match = re.search(rb"\bUID\s+(\d+)\b", b" ".join(header_parts))
        if uid_match is not None and int(uid_match.group(1)) == expected_uid and literal is not None:
            return literal
        raise ValueError("没有收到指定 UID 的完整邮件正文")

    def fetch_recent(self, store) -> dict:
        """积压时先保存最新一批新信，不越过历史游标；首次接入仍遵守initial_limit。"""
        result = {'received': 0, 'priority_job_ids': []}
        if not self.settings.configured:
            raise MailCollectionError('MailConfigurationMissing')
        connection = None
        deadline = time.monotonic() + 15
        with self._lock:
            try:
                connection = self.connection_factory(self.settings.host, self.settings.port, ssl_context=ssl.create_default_context(), timeout=min(self.settings.timeout, 15))
                status, _ = self._command(connection, deadline, 'login', self.settings.account, self.settings.auth_code)
                if status != 'OK':
                    raise ValueError('IMAP登录未成功')
                status, _ = self._command(connection, deadline, 'select', 'INBOX', readonly=True)
                if status != 'OK':
                    raise ValueError('IMAP只读选择未成功')
                _, validity = self._command(connection, deadline, 'response', 'UIDVALIDITY')
                if not validity or not isinstance(validity[0], bytes) or not validity[0].strip().isdigit():
                    raise ValueError('无效UIDVALIDITY')
                uidvalidity = str(int(validity[0]))
                if not 1 <= int(uidvalidity) <= 4_294_967_295:
                    raise ValueError('UIDVALIDITY超出范围')
                account = self.settings.account
                cursor = store.get_cursor(account, 'INBOX', uidvalidity)
                if cursor is None or cursor >= 4_294_967_295:
                    return result
                status, data = self._command(connection, deadline, 'uid', 'SEARCH', None, 'UID', f'{cursor + 1}:*')
                if status != 'OK':
                    raise ValueError('UID搜索失败')
                uids = sorted({int(value) for chunk in data or [] if isinstance(chunk, bytes) for value in chunk.split()})
                if any(not 1 <= uid <= 4_294_967_295 for uid in uids):
                    raise ValueError('无效UID')
                # 排除普通收取本轮即将处理的一批；没有积压时不重复下载同一封信。
                remaining = [uid for uid in uids if uid > cursor][self.settings.batch_size:]
                for uid in remaining[-self.settings.batch_size:]:
                    existing = store.get_job(account, 'INBOX', uidvalidity, uid)
                    if existing and existing['has_raw']:
                        if existing['status'] in ('pending', 'retry'):
                            result['priority_job_ids'].append(existing['job_id'])
                        continue
                    status, data = self._command(connection, deadline, 'uid', 'FETCH', str(uid), '(UID RFC822.SIZE INTERNALDATE)')
                    metadata = self._metadata(data, uid) if status == 'OK' else None
                    if metadata is None or metadata['rfc822_size'] > self.settings.max_message_bytes:
                        continue
                    status, data = self._command(connection, deadline, 'uid', 'FETCH', str(uid), '(UID BODY.PEEK[])')
                    if status != 'OK':
                        raise ValueError('邮件正文收取失败')
                    raw = self._raw(data, uid)
                    if len(raw) > self.settings.max_message_bytes:
                        continue
                    saved = store.save_raw(account, 'INBOX', uidvalidity, uid, raw, metadata, advance_cursor=False)
                    result['received'] += 1
                    result['priority_job_ids'].append(saved['job_id'])
                return result
            except Exception as error:
                raise MailCollectionError(type(error).__name__) from None
            finally:
                if connection is not None:
                    try:
                        socket = getattr(connection, 'sock', None)
                        if socket is not None:
                            socket.settimeout(min(self.settings.timeout, 1))
                        connection.logout()
                    except Exception:
                        pass

    def fetch_new(self, store) -> dict:
        """收取首轮最近邮件或游标后的新 UID，原文落库后推进，单封获取失败不越过该 UID。"""
        if not self.settings.configured:
            raise MailCollectionError("MailConfigurationMissing")
        result = {"received": 0, "skipped": 0, "initialized": False, "uidvalidity_reset": False, "received_job_ids": []}
        connection = None
        deadline = time.monotonic() + 40
        with self._lock:
            try:
                connection = self.connection_factory(
                    self.settings.host, self.settings.port, ssl_context=ssl.create_default_context(),
                    timeout=min(self.settings.timeout, 40),
                )
                status, _ = self._command(connection, deadline, "login", self.settings.account, self.settings.auth_code)
                if status != "OK":
                    raise ValueError("IMAP 登录未成功")
                status, _ = self._command(connection, deadline, "select", "INBOX", readonly=True)
                if status != "OK":
                    raise ValueError("IMAP 只读选择收件箱未成功")
                _, validity_data = self._command(connection, deadline, "response", "UIDVALIDITY")
                if not validity_data or not isinstance(validity_data[0], bytes) or not validity_data[0].strip().isdigit():
                    raise ValueError("服务器未返回有效 UIDVALIDITY")
                uidvalidity = str(int(validity_data[0]))
                if not 1 <= int(uidvalidity) <= 4_294_967_295:
                    raise ValueError("UIDVALIDITY 超出范围")
                account, mailbox = self.settings.account, "INBOX"
                cursor = store.get_cursor(account, mailbox, uidvalidity)
                initialized = cursor is None
                result["initialized"] = initialized
                if initialized:
                    result["uidvalidity_reset"] = store.has_other_uidvalidity(account, mailbox, uidvalidity)
                    status, data = self._command(connection, deadline, "uid", "SEARCH", None, "ALL")
                elif cursor >= 4_294_967_295:
                    return result
                else:
                    status, data = self._command(connection, deadline, "uid", "SEARCH", None, "UID", f"{cursor + 1}:*")
                if status != "OK":
                    raise ValueError("UID 搜索未成功")
                uids = sorted({int(value) for item in data or [] if isinstance(item, bytes) for value in item.split()})
                if any(not 1 <= uid <= 4_294_967_295 for uid in uids):
                    raise ValueError("服务器返回了无效 UID")
                if initialized:
                    selected = uids[-self.settings.initial_limit:] if self.settings.initial_limit else []
                    cursor = selected[0] - 1 if selected else max(uids, default=0)
                    # 首次只跳过用户配置之外的历史信，选中的最新邮件仍须逐封持久化。
                    store.set_cursor(account, mailbox, uidvalidity, cursor)
                    uids = selected
                uids = [uid for uid in uids if uid > cursor][:self.settings.batch_size]
                for uid in uids:
                    try:
                        status, metadata_response = self._command(
                            connection, deadline, "uid", "FETCH", str(uid), "(UID RFC822.SIZE INTERNALDATE)",
                        )
                        if status != "OK":
                            raise ValueError("邮件元数据收取未成功")
                        metadata = self._metadata(metadata_response, uid)
                        if metadata is None:
                            # 搜索后邮件可能被其他客户端删除；记录原因后才允许越过该 UID。
                            store.record_skipped(account, mailbox, uidvalidity, uid, "MessageUnavailable")
                            result["skipped"] += 1
                            continue
                        if metadata["rfc822_size"] > self.settings.max_message_bytes:
                            store.record_skipped(account, mailbox, uidvalidity, uid, "MessageTooLarge", metadata)
                            result["skipped"] += 1
                            continue
                        status, body_response = self._command(
                            connection, deadline, "uid", "FETCH", str(uid), "(UID BODY.PEEK[])",
                        )
                        if status != "OK":
                            raise ValueError("邮件正文收取未成功")
                        raw = self._raw(body_response, uid)
                        if len(raw) > self.settings.max_message_bytes:
                            store.record_skipped(account, mailbox, uidvalidity, uid, "MessageTooLarge", metadata)
                            result["skipped"] += 1
                            continue
                        saved = store.save_raw(account, mailbox, uidvalidity, uid, raw, metadata)
                        result['received_job_ids'].append(saved['job_id'])
                        result["received"] += 1
                    except Exception as error:
                        # 本轮已保存的邮件仍可处理；失败 UID 没有推进，下轮短连接会重新取得。
                        result["error_code"] = type(error).__name__
                        break
                return result
            except Exception as error:
                raise MailCollectionError(type(error).__name__) from None
            finally:
                if connection is not None:
                    try:
                        socket = getattr(connection, "sock", None)
                        if socket is not None:
                            socket.settimeout(min(self.settings.timeout, 1))
                        connection.logout()
                    except Exception:
                        pass
