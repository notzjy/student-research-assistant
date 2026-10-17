"""界面HTTP适配器测试：隔离网络，核对目的地址、真实文件字节和安全错误边界。"""

import json
import unittest
from unittest.mock import patch

import httpx

from dashboard_api import DashboardAPI, DashboardError


class DashboardAPITests(unittest.TestCase):
    """用MockTransport检查实际HTTP请求，不连接外网、邮箱或模型。"""

    def setUp(self):
        """保存真实客户端构造器，以便在替换连接边界后仍运行HTTP编码。"""
        self.original_client = httpx.Client
        self.requests = []
        self.client_options = []

    def transport(self, handler):
        """替换适配器的客户端并捕获配置，所有请求只进入合成处理器。"""
        def factory(**kwargs):
            """创建使用内存传输的真实客户端，保留被测超时与代理设置。"""
            self.client_options.append(dict(kwargs))
            return self.original_client(**kwargs, transport=httpx.MockTransport(handler))

        return patch("dashboard_api.httpx.Client", side_effect=factory)

    def test_base_url_accepts_local_service_and_rejects_other_destinations(self):
        """目标仅可为本机或compose后端，拒绝远程主机、凭证及多余URL部分。"""
        for value in ("http://127.0.0.1:8002", "https://localhost:8002/", "http://[::1]:8002", "http://backend:8002"):
            with self.subTest(value=value):
                self.assertEqual(DashboardAPI(value).base_url, value.rstrip("/"))
        for value in (
            "https://example.com", "http://127.0.0.1.example.com", "ftp://localhost",
            "http://user:password@localhost:8002", "http://localhost:8002/api",
            "http://localhost:8002?token=secret", "http://localhost:8002#fragment",
            "http://localhost:70000", "//localhost:8002", "http://", None,
        ):
            with self.subTest(value=value), self.assertRaises(DashboardError):
                DashboardAPI(value)

    def test_invalid_method_or_path_fails_before_any_network_call(self):
        """拒绝绝对地址和会切换目的主机的路径，且不创建HTTP客户端。"""
        api = DashboardAPI("http://127.0.0.1:8002")
        with patch("dashboard_api.httpx.Client") as constructor:
            for method, path in (
                ("DELETE", "/tasks"), ("POST", "http://example.com/messages"),
                ("GET", "//example.com/tasks"), ("GET", "/\\example.com/tasks"),
                ("GET", "tasks"), ("GET", None),
            ):
                with self.subTest(method=method, path=path), self.assertRaises(DashboardError):
                    api.request(method, path)
        constructor.assert_not_called()

    def test_json_request_uses_local_client_and_preserves_trace_header(self):
        """真实编码查询和JSON正文，关闭环境代理、重定向并保存本次追踪编号。"""
        def handler(request):
            """核对提交消息的URL和正文，仅返回合成成功对象。"""
            self.requests.append(request)
            self.assertEqual(str(request.url), "http://127.0.0.1:8002/messages?limit=3")
            self.assertEqual(json.loads(request.content), {"text": "合成通知"})
            return httpx.Response(200, json={"status": "processed"}, headers={"X-Trace-ID": "a" * 32})

        api = DashboardAPI("http://127.0.0.1:8002")
        with self.transport(handler):
            result = api.request("POST", "/messages", params={"limit": 3}, json={"text": "合成通知"})
        self.assertEqual(result, {"status": "processed"})
        self.assertEqual(api.last_trace_id, "a" * 32)
        self.assertFalse(self.client_options[0]["trust_env"])
        self.assertFalse(self.client_options[0]["follow_redirects"])

    def test_http_failures_only_expose_safe_message_and_trace(self):
        """后端安全message可展示，其他HTML、请求回显和供应商信息不进入错误文本。"""
        responses = [
            httpx.Response(422, json={"detail": {"message": "文件参数不符合要求。", "raw": "do-not-expose-provider"}}),
            httpx.Response(502, text="<html>do-not-expose-provider</html>"),
            httpx.Response(422, json={"detail": [{"input": "do-not-expose-provider"}]}),
        ]
        for response in responses:
            with self.subTest(status=response.status_code, content=response.content):
                response.headers["X-Trace-ID"] = "b" * 32
                api = DashboardAPI("http://localhost:8002")
                with self.transport(lambda request: response), self.assertRaises(DashboardError) as raised:
                    api.request("POST", "/messages", json={"text": "private-request-body"})
                self.assertEqual(raised.exception.status_code, response.status_code)
                self.assertEqual(raised.exception.trace_id, "b" * 32)
                self.assertNotIn("do-not-expose-provider", str(raised.exception))
                self.assertNotIn("private-request-body", str(raised.exception))
        self.assertEqual(str(raised.exception), "主服务未能完成操作，请查看运行状态或追踪记录。")

    def test_invalid_success_json_and_network_error_hide_internal_details(self):
        """成功响应也必须为JSON对象，网络异常不泄露异常里的地址和私密正文。"""
        for response in (httpx.Response(200, text="private-invalid-json"), httpx.Response(200, json=["private-array"])):
            api = DashboardAPI("http://localhost:8002")
            with self.transport(lambda request: response), self.assertRaises(DashboardError) as raised:
                api.request("GET", "/tasks")
            self.assertNotIn("private", str(raised.exception))
            self.assertEqual(raised.exception.status_code, 200)

        def failed(request):
            """在内存边界制造连接异常，验证底层异常内容不会展示给界面。"""
            raise httpx.ConnectError("private-network-url-and-credential", request=request)

        with self.transport(failed), self.assertRaises(DashboardError) as raised:
            DashboardAPI("http://localhost:8002").request("POST", "/messages", json={"text": "private-body"})
        self.assertNotIn("private", str(raised.exception))
        self.assertIsNone(raised.exception.status_code)

    def test_upload_sends_real_bytes_with_explicit_paired_binding(self):
        """文件通过octet-stream上传，查询参数只带文件名和绑定，不把本地路径当内容。"""
        data = "真实附件内容".encode("utf-8")

        def handler(request):
            """检查上传的字节和附件来源，避免JSON文件路径冒充原件。"""
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/documents")
            self.assertEqual(request.url.params["filename"], "研究资料.md")
            self.assertEqual(request.url.params["message_id"], "message-id")
            self.assertEqual(request.url.params["attachment_index"], "1")
            self.assertEqual(request.headers["Content-Type"], "application/octet-stream")
            self.assertEqual(request.content, data)
            return httpx.Response(200, json={"status": "indexed"})

        api = DashboardAPI("http://localhost:8002")
        with self.transport(handler):
            self.assertEqual(api.upload("研究资料.md", data, "message-id", 1), {"status": "indexed"})
        with patch("dashboard_api.httpx.Client") as constructor:
            for message_id, index, value in (("id", None, data), (None, 0, data), ("id", -1, data), ("id", True, data), (None, None, b""), (None, None, "D:\\private\\paper.pdf")):
                with self.subTest(message_id=message_id, index=index), self.assertRaises(DashboardError):
                    api.upload("paper.pdf", value, message_id, index)
            constructor.assert_not_called()

    def test_download_quotes_identifier_and_returns_file_bytes(self):
        """文档编号整体转义为单个路径段，并原样返回后端管理的文件字节。"""
        def handler(request):
            """检查潜在路径分隔符已编码，下载没有读取界面本地路径。"""
            self.assertEqual(request.url.raw_path, b"/documents/id%2Fwith%20space%3F/file")
            return httpx.Response(200, content=b"%PDF-synthetic-original")

        with self.transport(handler):
            self.assertEqual(DashboardAPI("http://localhost:8002").download("id/with space?"), b"%PDF-synthetic-original")

    def test_upload_size_boundary_rejects_oversized_bytes_before_http(self):
        """允许恰好10MB的原件，超过限制时在请求之前拒绝，避免界面转发超大文件。"""
        limit = 10 * 1024 * 1024
        data = b"a" * limit

        def handler(request):
            """只在内存接收边界大小的合成原件，不连接模型或实际文件存储。"""
            self.assertEqual(len(request.content), limit)
            return httpx.Response(200, json={"status": "indexed"})

        api = DashboardAPI("http://localhost:8002")
        with self.transport(handler):
            self.assertEqual(api.upload("boundary.txt", data), {"status": "indexed"})
        with patch("dashboard_api.httpx.Client") as constructor:
            with self.assertRaises(DashboardError):
                api.upload("oversized.txt", data + b"a")
            constructor.assert_not_called()

    def test_redirect_cannot_forward_private_body_to_another_host(self):
        """即便本机返回外部重定向，也只发送原始一次请求并展示安全错误。"""
        visited = []

        def handler(request):
            """模拟本机跳转到其他主机，若适配器跟随跳转便记录第二次访问。"""
            visited.append(str(request.url))
            return httpx.Response(
                307,
                headers={"Location": "https://example.com/collect", "X-Trace-ID": "c" * 32},
            )

        api = DashboardAPI("http://localhost:8002")
        with self.transport(handler), self.assertRaises(DashboardError) as raised:
            api.request("POST", "/messages", json={"text": "private-synthetic-body"})
        self.assertEqual(visited, ["http://localhost:8002/messages"])
        self.assertEqual(raised.exception.status_code, 307)
        self.assertEqual(raised.exception.trace_id, "c" * 32)
        self.assertNotIn("private-synthetic-body", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
