"""一键启动控制台的环境、命令、进程所有权和关闭行为离线测试。"""

import importlib
import io
import json
import socket
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch


launcher_module = importlib.import_module("start_assistant")


class LauncherTests(unittest.TestCase):
    """仅用临时目录和进程替身测试，不启动后端、模型、邮箱或桌面通知。"""

    def setUp(self):
        """准备具有空占位脚本的项目目录，Popen在所有启动场景中由Mock替代。"""
        temporary = tempfile.TemporaryDirectory(prefix="assistant-launcher-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "local-data").mkdir()
        (self.root / "run_assistant.py").touch()
        (self.root / "assistant_dashboard.py").touch()
        executable = launcher_module.python_executable(self.root)
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.touch()
        self.manager = launcher_module.AssistantLauncher(self.root)
        self.addCleanup(self.release_fake_processes)

    def release_fake_processes(self):
        """关闭测试日志并清空假进程引用，绝不向测试伪造PID执行系统终止操作。"""
        for _, _, log in self.manager._processes:
            log.close()
        self.manager._processes.clear()

    @staticmethod
    def fake_process(pid):
        """创建仍在运行的进程替身，禁止任何实际子进程创建。"""
        process = Mock()
        process.pid = pid
        process.poll.return_value = None
        process.wait.return_value = 0
        return process

    def ready_report(self, **updates):
        """为生命周期用例提供独立的可用环境报告，不检查真实用户端口。"""
        report = {"ready": True, "backend_port_in_use": False, "dashboard_port_in_use": False}
        report.update(updates)
        return report

    def test_database_selection_preserves_existing_teaching_pair_or_uses_default(self):
        """教学业务库存在时沿用对应检查点，否则使用正式双库默认文件。"""
        business, checkpoint = launcher_module.select_databases(self.root)
        self.assertEqual(business.name, "assistant.sqlite3")
        self.assertEqual(checkpoint.name, "assistant_checkpoints.sqlite3")
        (self.root / "local-data" / "module01_tasks.sqlite3").touch()
        business, checkpoint = launcher_module.select_databases(self.root)
        self.assertEqual(business.name, "module01_tasks.sqlite3")
        self.assertEqual(checkpoint.name, "module01_memory.sqlite3")

    def test_commands_use_project_python_absolute_paths_and_explicit_lan_choice(self):
        """默认后端和界面仅本机，勾选局域网只改变后端host，不改变数据库。"""
        backend, dashboard = self.manager.commands()
        self.assertEqual(Path(backend[0]), launcher_module.python_executable(self.root))
        self.assertEqual(Path(backend[1]), self.root / "run_assistant.py")
        self.assertEqual(backend[backend.index("--host") + 1], "127.0.0.1")
        self.assertIn("--server.address=127.0.0.1", dashboard)
        self.assertIn("--server.headless=true", dashboard)
        self.assertIn("--browser.gatherUsageStats=false", dashboard)
        lan_backend, lan_dashboard = self.manager.commands(True)
        self.assertEqual(lan_backend[lan_backend.index("--host") + 1], "0.0.0.0")
        self.assertEqual(dashboard, lan_dashboard)
        self.assertEqual(backend[backend.index("--db") + 1], lan_backend[lan_backend.index("--db") + 1])

    def test_environment_check_is_read_only_and_never_reads_dotenv_or_starts_process(self):
        """环境检查仅看文件与模块，私密配置文件的内容从未被读取。"""
        with (
            patch.object(launcher_module.importlib.util, "find_spec", return_value=object()),
            patch.object(launcher_module, "port_is_open", return_value=False),
            patch.object(Path, "read_text", side_effect=AssertionError("不能读取配置")),
            patch.object(launcher_module.subprocess, "Popen") as popen,
        ):
            report = launcher_module.check_environment(self.root)
        self.assertTrue(report["ready"])
        self.assertEqual(report["missing_files"], [])
        popen.assert_not_called()
        (self.root / "assistant_dashboard.py").unlink()
        with patch.object(launcher_module, "port_is_open", return_value=False):
            missing = launcher_module.check_environment(self.root)
        self.assertFalse(missing["ready"])
        self.assertIn("assistant_dashboard.py", missing["missing_files"])

    def test_occupied_port_does_not_take_over_or_stop_external_service(self):
        """发现已有服务时在创建进程前退出，不调用taskkill或接管旧服务。"""
        with (
            patch.object(launcher_module, "check_environment", return_value=self.ready_report(backend_port_in_use=True)),
            patch.object(launcher_module.subprocess, "Popen") as popen,
            patch.object(launcher_module.subprocess, "run") as terminate,
        ):
            with self.assertRaisesRegex(launcher_module.LauncherError, "8002.*已被占用"):
                self.manager.start()
        popen.assert_not_called()
        terminate.assert_not_called()
        self.assertEqual(self.manager.snapshot()["owned"], [])

    def test_start_runs_owned_backend_then_dashboard_and_redirects_hidden_logs(self):
        """后台先健康再启动界面，两个进程保存独立日志且所有路径绝对。"""
        backend, dashboard = self.fake_process(987001), self.fake_process(987002)
        with (
            patch.object(launcher_module, "check_environment", return_value=self.ready_report()),
            patch.object(launcher_module.subprocess, "Popen", side_effect=[backend, dashboard]) as popen,
            patch.object(self.manager, "_wait_ready") as wait,
        ):
            self.manager.start()
        self.assertEqual(popen.call_count, 2)
        self.assertEqual([call.args[0] for call in wait.call_args_list], [backend, dashboard])
        self.assertEqual([item["pid"] for item in self.manager.snapshot()["owned"]], [987001, 987002])
        self.assertFalse(self.manager.snapshot()["starting"])
        self.assertEqual(popen.call_args_list[0].kwargs["cwd"], self.root)
        self.assertEqual(popen.call_args_list[1].kwargs["env"]["ASSISTANT_API_URL"], "http://127.0.0.1:8002")
        self.assertEqual(popen.call_args_list[0].kwargs["stdin"], launcher_module.subprocess.DEVNULL)
        self.assertEqual(len(list((self.root / "local-data" / "logs").glob("*.log"))), 2)
        if launcher_module.os.name == "nt":
            self.assertEqual(popen.call_args_list[0].kwargs["creationflags"], launcher_module.subprocess.CREATE_NO_WINDOW)
        with self.assertRaises(launcher_module.LauncherError):
            self.manager.start()

    def test_dashboard_start_failure_stops_only_owned_processes_in_reverse_order(self):
        """界面未就绪时自动撤回本次两个进程，不留下失控的半套服务。"""
        backend, dashboard = self.fake_process(987011), self.fake_process(987012)
        with (
            patch.object(launcher_module, "check_environment", return_value=self.ready_report()),
            patch.object(launcher_module.subprocess, "Popen", side_effect=[backend, dashboard]),
            patch.object(self.manager, "_wait_ready", side_effect=[None, launcher_module.LauncherError("合成启动失败")]),
            patch.object(self.manager, "_stop_process") as stop,
        ):
            with self.assertRaisesRegex(launcher_module.LauncherError, "合成启动失败"):
                self.manager.start()
        self.assertEqual([call.args[0] for call in stop.call_args_list], [dashboard, backend])
        self.assertEqual(self.manager.snapshot()["owned"], [])
        self.assertFalse(self.manager.snapshot()["starting"])

    def test_failed_stop_retains_process_ownership_for_retry(self):
        """停止失败时保持自有进程记录和日志句柄，用户可以再次停止。"""
        process = self.fake_process(987021)
        log = Mock()
        self.manager._processes = [("backend", process, log)]
        with patch.object(self.manager, "_stop_process", side_effect=OSError("synthetic")):
            with self.assertRaises(launcher_module.LauncherError):
                self.manager.stop()
        self.assertEqual(len(self.manager.snapshot()["owned"]), 1)
        log.close.assert_not_called()
        with patch.object(self.manager, "_stop_process"):
            self.manager.stop()
            self.manager.stop()
        self.assertEqual(self.manager.snapshot()["owned"], [])
        log.close.assert_called_once()

    def test_windows_tree_stop_targets_only_owned_pid_without_global_process_matching(self):
        """Windows只对持有的Popen PID执行taskkill树停止，不使用进程名批量结束。"""
        if launcher_module.os.name != "nt":
            self.skipTest("Windows专用进程树调用")
        process = self.fake_process(987031)
        with patch.object(launcher_module.subprocess, "run") as terminate:
            self.manager._stop_process(process)
        self.assertEqual(terminate.call_args.args[0], ["taskkill", "/PID", "987031", "/T", "/F"])
        self.assertEqual(terminate.call_args.kwargs["creationflags"], launcher_module.subprocess.CREATE_NO_WINDOW)
        process.wait.assert_called_once()

    def test_readiness_checks_reject_exited_or_cancelled_process_without_network(self):
        """进程提前退出或用户取消启动时立即失败，不把其他服务的健康响应当成功。"""
        process = self.fake_process(987041)
        process.poll.return_value = 1
        with self.assertRaisesRegex(launcher_module.LauncherError, "提前退出"):
            self.manager._wait_ready(process, "http://127.0.0.1:1/health")
        process.poll.return_value = None
        self.manager._stop_requested.set()
        with self.assertRaisesRegex(launcher_module.LauncherError, "取消"):
            self.manager._wait_ready(process, "http://127.0.0.1:1/health")

    def test_check_cli_returns_json_without_creating_gui_or_service(self):
        """--check提供无GUI诊断并按环境完整性返回退出码，不进入Tk主循环。"""
        output = io.StringIO()
        with (
            patch.object(launcher_module, "check_environment", return_value={"ready": True}),
            patch.object(launcher_module, "LauncherWindow") as window,
            patch.object(launcher_module.subprocess, "Popen") as popen,
            redirect_stdout(output),
        ):
            self.assertEqual(launcher_module.main(["--check"]), 0)
        self.assertTrue(json.loads(output.getvalue())["ready"])
        window.assert_not_called()
        popen.assert_not_called()

    def test_visible_controller_close_minimizes_or_explicitly_stops(self):
        """关闭运行窗口由用户选择，保持运行时仅最小化，退出时明确停止。"""
        window = launcher_module.LauncherWindow.__new__(launcher_module.LauncherWindow)
        window.root = Mock()
        window.launcher = Mock()
        window.launcher.snapshot.return_value = {"owned": [{"pid": 987051}], "starting": False}
        window.workers = 0
        window.stop = Mock()
        with patch("tkinter.messagebox.askyesnocancel", return_value=False):
            window.close()
        window.root.iconify.assert_called_once()
        window.root.destroy.assert_not_called()
        window.stop.assert_not_called()
        with patch("tkinter.messagebox.askyesnocancel", return_value=True):
            window.close()
        self.assertTrue(window.exit_after_stop)
        window.stop.assert_called_once()

    def test_port_probe_uses_only_local_tcp_connection(self):
        """临时回环监听可被检测，关闭后返回未占用，不读取应用数据。"""
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            self.assertTrue(launcher_module.port_is_open(port))
        self.assertFalse(launcher_module.port_is_open(port))


if __name__ == "__main__":
    unittest.main()
