"""界面专用HTTP适配器：访问本机主服务，不持有模型配置、业务库或后台任务。"""

import ipaddress
from urllib.parse import quote, urlsplit

import httpx


class DashboardError(Exception):
    """向界面提供有限的错误说明、HTTP状态和追踪编号，不暴露底层请求数据。"""

    def __init__(self, message: str, status_code: int | None = None, trace_id: str | None = None):
        """保存可展示的错误元数据，供用户在需要时查询运行追踪。"""
        super().__init__(message)
        self.status_code = status_code
        self.trace_id = trace_id


def validate_base_url(value: str) -> str:
    """仅接受本机或compose内部backend地址，避免把私人消息和附件发到任意服务器。"""
    if not isinstance(value, str):
        raise DashboardError("服务地址应为本机HTTP地址，例如 http://127.0.0.1:8002。")
    try:
        parsed = urlsplit(value)
        allowed = parsed.hostname in ("localhost", "backend")
        if parsed.hostname and not allowed:
            try:
                allowed = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                allowed = False
        if (
            parsed.scheme not in ("http", "https") or not allowed
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise DashboardError("服务地址应为本机HTTP地址，例如 http://127.0.0.1:8002。") from None
    return value.rstrip("/")


class DashboardAPI:
    """通过已有HTTP协议执行界面操作，所有模型调用和写入由后端统一处理。"""

    def __init__(self, base_url: str):
        """校验目标地址并初始化追踪编号，本身不发出网络请求。"""
        self.base_url = validate_base_url(base_url)
        self.last_trace_id = None

    def _response(self, method: str, path: str, *, json=None, params=None, content=None, headers=None):
        """发出一次显式请求；业务正文仅交给指定后端，不进入错误字符串。"""
        if method not in ("GET", "POST", "PUT") or not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "://" in path or "\\" in path:
            raise DashboardError("界面请求路径或方法不符合要求。")
        try:
            with httpx.Client(base_url=self.base_url, timeout=180, trust_env=False, follow_redirects=False) as client:
                response = client.request(method, path, json=json, params=params, content=content, headers=headers)
        except httpx.HTTPError:
            raise DashboardError("无法完成主服务请求，请确认8002服务正在运行；已提交操作请查询后再重试。") from None
        self.last_trace_id = response.headers.get("X-Trace-ID")
        if not response.is_success:
            # 只采用本项目约定的安全message，忽略请求回显、原始HTML和提供商错误。
            message = "主服务未能完成操作，请查看运行状态或追踪记录。"
            try:
                detail = response.json().get("detail")
                if isinstance(detail, dict) and isinstance(detail.get("message"), str):
                    message = detail["message"][:300]
            except (AttributeError, TypeError, ValueError):
                pass
            raise DashboardError(message, response.status_code, self.last_trace_id)
        return response

    def request(self, method: str, path: str, *, json=None, params=None, content=None) -> dict:
        """调用JSON接口并核对对象结构，保持界面与后端业务逻辑分离。"""
        response = self._response(method, path, json=json, params=params, content=content)
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
        except ValueError:
            raise DashboardError("主服务返回了无法识别的JSON结果。", response.status_code, self.last_trace_id) from None
        return result

    def upload(self, filename: str, data: bytes, message_id: str | None = None, attachment_index: int | None = None) -> dict:
        """上传真实文件字节，可显式绑定消息附件序号，文件名不作为服务器路径。"""
        if (message_id is None) != (attachment_index is None):
            raise DashboardError("绑定附件时，请同时填写消息编号和附件序号。")
        if not isinstance(data, bytes) or not data or len(data) > 10 * 1024 * 1024:
            raise DashboardError("请选择非空且不超过10MB的文件。")
        params = {"filename": filename}
        if message_id is not None:
            if not isinstance(message_id, str) or not message_id.strip() or type(attachment_index) is not int or attachment_index < 0:
                raise DashboardError("消息编号和附件序号不符合要求。")
            params.update({"message_id": message_id, "attachment_index": attachment_index})
        response = self._response("POST", "/documents", params=params, content=data, headers={"Content-Type": "application/octet-stream"})
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
        except ValueError:
            raise DashboardError("上传结果不是有效的JSON对象。", response.status_code, self.last_trace_id) from None
        return result

    def download(self, document_id: str) -> bytes:
        """按后端文档编号取回受管原件，不读取界面进程本机文件路径。"""
        if not isinstance(document_id, str) or not document_id.strip():
            raise DashboardError("请选择有效的文档编号。")
        return self._response("GET", "/documents/" + quote(document_id, safe="") + "/file").content

    def upload_attachment(self, message_id: str, index: int, filename: str, data: bytes) -> dict:
        """将任意格式的真实附件原件绑定已有线索，分类和正文提取不等同原件取得。"""
        if type(index) is not int or index < 0 or not isinstance(data, bytes) or len(data) > 25 * 1024 * 1024:
            raise DashboardError('附件序号或字节大小不符合要求。')
        response = self._response('POST', '/messages/' + quote(message_id, safe='') + '/attachments/' + str(index), params={'filename': filename}, content=data, headers={'Content-Type': 'application/octet-stream'})
        return response.json()

    def download_attachment(self, message_id: str, index: int) -> bytes:
        """按消息编号和附件序号下载后端已实际取得的原件。"""
        if type(index) is not int or index < 0:
            raise DashboardError('附件序号不符合要求。')
        return self._response('GET', '/messages/' + quote(message_id, safe='') + '/attachments/' + str(index) + '/file').content

    def download_phone_app(self) -> bytes:
        """从已有后端取得本项目已构建的安卓APK，界面进程不读取后端本地文件。"""
        return self._response('GET', '/notifications/app.apk').content
