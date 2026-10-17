"""手机配对与重点收件箱交互验收，全部使用合成数据，不连接手机、邮箱或通知通道。"""

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from streamlit.testing.v1 import AppTest

from dashboard_api import DashboardAPI
import dashboard_connections


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_TOKEN = "synthetic-unit-test-token-not-a-real-credential"


class PhoneDashboardUITests(unittest.TestCase):
    """验证配对、停用和已读都由明确按钮触发，二维码只在本机生成。"""

    def setUp(self):
        """替换HTTP边界和外网请求，保留真实Streamlit页面、控件与二维码生成。"""
        self.calls = []
        self.bridge_enabled = False
        self.policy = {"enabled": False, "max_age_minutes": 60, "channel": "console"}
        self.alerts = [{"alert_id": "a" * 32, "message_id": "demo-important-message", "title": "合成重点消息", "status": "pending"}]
        self.api = Mock(spec=DashboardAPI)
        self.api.base_url = "http://127.0.0.1:8002"
        self.api.last_trace_id = None
        self.api.request.side_effect = self.respond
        self.api.download_phone_app.return_value = b"synthetic-apk-bytes"
        self.enterContext(patch("dashboard_api.DashboardAPI", return_value=self.api))
        # 此库在本地把文本变为PNG；保留真实实现以验证没有第三方二维码服务。
        self.make_qr = self.enterContext(patch("dashboard_connections.qrcode.make", wraps=dashboard_connections.qrcode.make))
        self.enterContext(patch("httpx.Client.request", side_effect=AssertionError("界面测试不得发起真实HTTP")))
        self.app = AppTest.from_file(ROOT / "assistant_dashboard.py", default_timeout=10)

    def respond(self, method, path, **kwargs):
        """只允许本轮约定的合成HTTP操作，意外写入或模型调用立即失败。"""
        self.calls.append((method, path, deepcopy(kwargs)))
        if (method, path) == ("GET", "/messages"):
            return {"messages": []}
        if (method, path) == ("GET", "/notifications/status"):
            return {"enabled": self.bridge_enabled, "configured": True, "listen_host": "0.0.0.0", "counts": {}, "devices": []}
        if (method, path) == ("POST", "/notifications/config"):
            self.bridge_enabled = kwargs["json"]["enabled"]
            return {"enabled": self.bridge_enabled, "token": SYNTHETIC_TOKEN, "upload_urls": ["http://192.168.1.8:8002/notifications"]}
        if (method, path) == ("GET", "/attention/policy"):
            return deepcopy(self.policy)
        if (method, path) == ("PUT", "/attention/policy"):
            self.policy = deepcopy(kwargs["json"])
            return deepcopy(self.policy)
        if (method, path) == ("GET", "/attention"):
            return {"alerts": deepcopy(self.alerts)}
        if method == "POST" and path == "/attention/" + "a" * 32 + "/read":
            self.alerts[0]["status"] = "read"
            return deepcopy(self.alerts[0])
        raise AssertionError(f"非预期HTTP操作：{method} {path}")

    def run_app(self):
        """执行真实脚本重运行，并把界面异常提升为测试失败。"""
        self.app.run()
        self.assertFalse(list(self.app.exception), list(self.app.exception))

    def navigate(self, page):
        """从默认消息页进入一个指定页面，并清空初始只读请求。"""
        self.run_app()
        self.calls.clear()
        self.app.sidebar.radio[0].set_value(page)
        self.run_app()

    def click(self, label):
        """按可见按钮文字提交一次明确用户操作。"""
        next(button for button in self.app.button if button.label == label).click()
        self.run_app()

    def checkbox(self, label):
        """按文字定位复选框，避免依赖界面控件顺序。"""
        return next(item for item in self.app.checkbox if item.label == label)

    def writes(self):
        """只提取写入请求，便于验证rerun和刷新不会重复执行操作。"""
        return [call for call in self.calls if call[0] != "GET"]

    def enable_pairing(self):
        """完成一轮显式启用操作，供停用与会话保留场景复用。"""
        self.navigate("手机接入")
        self.checkbox("启用手机通知接入").check()
        self.click("保存并显示本机配对信息")

    def test_phone_page_default_only_reads_status(self):
        """首次进入手机页只读安全状态，不配对、不下载、不生成二维码。"""
        self.navigate("手机接入")
        self.assertEqual(self.calls, [("GET", "/notifications/status", {})])
        self.assertFalse(self.writes())
        self.make_qr.assert_not_called()
        self.api.download_phone_app.assert_not_called()
        self.assertNotIn("phone_pairing", self.app.session_state)

    def test_explicit_pairing_builds_local_qr_with_encoded_deep_link(self):
        """用户主动保存后才生成本机二维码，完整POST地址与合成令牌经过URL编码。"""
        self.enable_pairing()
        self.assertEqual(self.writes(), [("POST", "/notifications/config", {"json": {"enabled": True, "rotate": False, "trust_posted_time": False}})])
        encoded = [call.args[0] for call in self.make_qr.call_args_list]
        self.assertEqual(encoded[0], "http://192.168.1.8:8002/notifications/app.apk")
        link = urlsplit(encoded[1])
        self.assertEqual((link.scheme, link.netloc), ("student-assistant", "pair"))
        self.assertEqual(parse_qs(link.query), {"server": ["http://192.168.1.8:8002/notifications"], "token": [SYNTHETIC_TOKEN]})
        self.assertEqual(self.app.session_state["phone_pairing"]["token"], SYNTHETIC_TOKEN)
        self.api.download_phone_app.assert_not_called()
        self.run_app()
        self.assertEqual(len(self.writes()), 1)

    def test_disable_removes_cached_pairing_qr_and_token(self):
        """停用时立即清除会话配对信息，不继续展示可扫描的旧令牌。"""
        self.enable_pairing()
        self.make_qr.reset_mock()
        self.checkbox("启用手机通知接入").uncheck()
        self.click("保存并显示本机配对信息")
        self.assertEqual(self.writes()[-1][2]["json"], {"enabled": False, "rotate": False, "trust_posted_time": False})
        self.assertNotIn("phone_pairing", self.app.session_state)
        self.make_qr.assert_not_called()
        self.assertFalse(any(item.label == "专用接入令牌" for item in self.app.text_input))

    def test_external_disable_clears_old_session_pairing_without_writing(self):
        """另一个本机窗口停用后，本页刷新读取状态并丢弃旧配对，而非继续展示缓存码。"""
        self.enable_pairing()
        self.bridge_enabled = False
        self.make_qr.reset_mock()
        writes = deepcopy(self.writes())
        self.run_app()
        self.assertEqual(self.writes(), writes)
        self.assertNotIn("phone_pairing", self.app.session_state)
        self.make_qr.assert_not_called()

    def test_apk_download_is_explicit_and_does_not_repeat_on_rerun(self):
        """安装包只有在点击准备下载时读取，页面刷新复用已取回字节。"""
        self.enable_pairing()
        self.click("准备下载安卓安装包")
        self.api.download_phone_app.assert_called_once_with()
        self.assertEqual(self.app.session_state["phone_apk"], b"synthetic-apk-bytes")
        self.run_app()
        self.api.download_phone_app.assert_called_once_with()
        self.assertEqual(len(self.writes()), 1)

    def test_attention_default_only_reads_policy_and_alerts(self):
        """打开重点收件箱不保存规则、不标记已读、不投递实际通知。"""
        self.navigate("重点收件箱")
        self.assertEqual(self.calls, [("GET", "/attention/policy", {}), ("GET", "/attention", {})])
        self.assertFalse(self.writes())
        self.run_app()
        self.assertFalse(self.writes())

    def test_attention_policy_changes_only_on_explicit_submit(self):
        """编辑规则不会自动写入，显式提交后只保存一次，不调用模型或通知。"""
        self.navigate("重点收件箱")
        self.checkbox("重点联系人新消息弹窗与提示音").check()
        next(item for item in self.app.number_input if item.label == "超过多少分钟前发出的消息不弹到达提醒").set_value(30)
        self.assertFalse(self.writes())
        self.click("保存重点消息提醒规则")
        self.assertEqual(self.writes(), [("PUT", "/attention/policy", {"json": {"enabled": True, "max_age_minutes": 30, "channel": "console"}})])
        self.run_app()
        self.assertEqual(len(self.writes()), 1)

    def test_attention_read_changes_only_selected_alert_once(self):
        """仅点击指定重点消息才标记已读，重运行不重复写入。"""
        self.navigate("重点收件箱")
        self.app.button(key="attention_read_" + "a" * 32).click()
        self.run_app()
        self.assertEqual(self.writes(), [("POST", "/attention/" + "a" * 32 + "/read", {})])
        self.run_app()
        self.assertEqual(len(self.writes()), 1)
        self.assertFalse(any(item.key == "attention_read_" + "a" * 32 for item in self.app.button))


if __name__ == "__main__":
    unittest.main()
