"""本地助理启动控制台：同时管理后端与界面，仅停止本实例创建的进程。"""

import argparse
import importlib.util
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parent
BACKEND_PORT = 8002
DASHBOARD_PORT = 8501
DASHBOARD_URL = f"http://127.0.0.1:{DASHBOARD_PORT}"


class LauncherError(RuntimeError):
    """表示可以安全显示给用户的启动、端口或本实例进程管理错误。"""


def select_databases(root: Path) -> tuple[Path, Path]:
    """已有教学业务库时沿用对应双库，否则选择正式默认双库，避免打开空库误以为数据丢失。"""
    data = root / "local-data"
    if (data / "module01_tasks.sqlite3").is_file():
        return data / "module01_tasks.sqlite3", data / "module01_memory.sqlite3"
    return data / "assistant.sqlite3", data / "assistant_checkpoints.sqlite3"


def python_executable(root: Path) -> Path:
    """固定使用项目虚拟环境的控制台Python；子进程输出重定向到日志而不显示终端。"""
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return (root / ".venv" / relative).resolve()


def port_is_open(port: int) -> bool:
    """只检查本机指定TCP端口是否有人监听，不请求模型或读取业务消息。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def check_environment(root: Path = ROOT) -> dict:
    """只检查文件和模块可用性，不读取.env、启动服务、连接邮箱或请求模型。"""
    root = Path(root).resolve()
    executable = python_executable(root)
    missing_files = [name for name in ("run_assistant.py", "assistant_dashboard.py") if not (root / name).is_file()]
    if not executable.is_file():
        missing_files.append(".venv中的Python")
    missing_modules = [name for name in ("tkinter", "uvicorn", "streamlit", "httpx") if importlib.util.find_spec(name) is None]
    business, checkpoints = select_databases(root)
    return {
        "ready": not missing_files and not missing_modules,
        "python": str(executable), "business_db": str(business), "checkpoint_db": str(checkpoints),
        "missing_files": missing_files, "missing_modules": missing_modules,
        "backend_port_in_use": port_is_open(BACKEND_PORT),
        "dashboard_port_in_use": port_is_open(DASHBOARD_PORT),
    }


class AssistantLauncher:
    """管理一组独占的后端和Streamlit子进程，端口已有服务时拒绝接管。"""

    def __init__(self, root: Path = ROOT):
        """记录运行目录和本实例进程，构造阶段不创建日志或启动任何后台服务。"""
        self.root = Path(root).resolve()
        self.business_db, self.checkpoint_db = select_databases(self.root)
        self._lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._processes = []
        self._starting = False
        self._stopping = False
        self._instance = uuid4().hex[:8]

    def commands(self, allow_lan: bool = False) -> tuple[list[str], list[str]]:
        """生成绝对路径启动命令；局域网选项仅扩展后端监听，界面保持本机访问。"""
        executable = str(python_executable(self.root))
        backend = [
            executable, str(self.root / "run_assistant.py"), "--host", "0.0.0.0" if allow_lan else "127.0.0.1",
            "--port", str(BACKEND_PORT), "--db", str(self.business_db), "--checkpoints", str(self.checkpoint_db),
        ]
        dashboard = [
            executable, "-m", "streamlit", "run", str(self.root / "assistant_dashboard.py"),
            "--server.address=127.0.0.1", f"--server.port={DASHBOARD_PORT}",
            "--server.headless=true", "--browser.gatherUsageStats=false",
        ]
        return backend, dashboard

    def snapshot(self) -> dict:
        """返回线程安全的本实例状态；不查找或控制系统中其他Python进程。"""
        with self._lock:
            return {
                "starting": self._starting, "stopping": self._stopping,
                "owned": [{"name": name, "pid": process.pid, "exit_code": process.poll()} for name, process, _ in self._processes],
            }

    def _spawn(self, name: str, command: list[str], environment: dict) -> subprocess.Popen:
        """隐藏启动一个自有子进程并保存日志句柄，取消启动后不再创建新进程。"""
        with self._lock:
            if self._stop_requested.is_set():
                raise LauncherError("启动已取消。")
            log_dir = self.root / "local-data" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            log = (log_dir / f"launcher-{self._instance}-{stamp}-{name}.log").open("ab")
            try:
                # stdin为空且后台窗口隐藏，用户始终通过本控制台管理生命周期。
                process = subprocess.Popen(
                    command, cwd=self.root, env=environment, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
                )
            except Exception:
                log.close()
                raise LauncherError("无法创建服务进程，请检查虚拟环境与本机日志目录权限。") from None
            self._processes.append((name, process, log))
            return process

    def _wait_ready(self, process: subprocess.Popen, url: str, timeout: float = 90) -> None:
        """等待本实例服务健康检查，支持取消并拒绝将提前退出当作启动成功。"""
        deadline = time.monotonic() + timeout
        # 不继承系统代理，避免本地健康请求被送往外部代理服务器。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            if self._stop_requested.is_set():
                raise LauncherError("启动已取消。")
            if process.poll() is not None:
                raise LauncherError("服务提前退出，请查看local-data/logs中的本次启动日志。")
            try:
                with opener.open(url, timeout=1) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                pass
            self._stop_requested.wait(0.2)
        raise LauncherError("等待服务启动超时，请查看local-data/logs中的本次启动日志。")

    def start(self, allow_lan: bool = False) -> None:
        """按顺序启动后端和界面，任一步失败都清理本实例已创建的进程。"""
        with self._lock:
            if self._starting or self._stopping or self._processes:
                raise LauncherError("本控制台已有启动或运行中的服务，请先停止。")
            self._starting = True
            self._stop_requested.clear()
        try:
            report = check_environment(self.root)
            if not report["ready"]:
                raise LauncherError("运行环境不完整，请执行start_assistant.py --check检查缺少的文件或依赖。")
            occupied = [str(port) for port, key in ((BACKEND_PORT, "backend_port_in_use"), (DASHBOARD_PORT, "dashboard_port_in_use")) if report[key]]
            if occupied:
                raise LauncherError("端口" + "、".join(occupied) + "已被占用。请使用原服务，或先在原启动窗口停止；本控制台不会接管已有进程。")
            backend_command, dashboard_command = self.commands(allow_lan)
            environment = os.environ.copy()
            environment["PYTHONUTF8"] = "1"
            backend = self._spawn("backend", backend_command, environment)
            self._wait_ready(backend, f"http://127.0.0.1:{BACKEND_PORT}/health")
            dashboard_environment = environment.copy()
            dashboard_environment["ASSISTANT_API_URL"] = f"http://127.0.0.1:{BACKEND_PORT}"
            dashboard = self._spawn("dashboard", dashboard_command, dashboard_environment)
            self._wait_ready(dashboard, DASHBOARD_URL + "/_stcore/health")
        except Exception:
            self.stop()
            raise
        finally:
            with self._lock:
                self._starting = False

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        """仅终止保存的Popen对象；Windows按该PID停止其子树，覆盖虚拟环境Python转发进程。"""
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15, check=False,
            )
            process.wait(timeout=5)
        else:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def stop(self) -> None:
        """取消在途启动并按相反顺序停止自有进程，停止失败时保留控制权供用户重试。"""
        self._stop_requested.set()
        with self._lock:
            self._stopping = True
            owned = list(reversed(self._processes))
        failed = False
        try:
            for item in owned:
                _, process, log = item
                try:
                    self._stop_process(process)
                except Exception:
                    failed = True
                    continue
                log.close()
                with self._lock:
                    if item in self._processes:
                        self._processes.remove(item)
        finally:
            with self._lock:
                self._stopping = False
        if failed:
            raise LauncherError("部分本实例进程尚未停止，请保留控制台并再次点击停止。")


class LauncherWindow:
    """提供可见的Tk控制台，耗时启动/停止在工作线程执行，窗口主线程只更新界面。"""

    def __init__(self, tk_root, launcher: AssistantLauncher):
        """创建按钮、状态区与局域网选项，不在打开窗口时自动调用模型或邮箱。"""
        import tkinter as tk
        from tkinter import ttk

        self.root, self.launcher = tk_root, launcher
        self.events = queue.Queue()
        self.workers = 0
        self.exit_after_stop = False
        self.status = tk.StringVar(value="尚未启动。点击启动后，同时运行后端与网页界面。")
        self.allow_lan = tk.BooleanVar(value=False)
        tk_root.title("个人研究与日程助理 · 启动控制台")
        tk_root.geometry("650x310")
        tk_root.minsize(580, 290)
        panel = ttk.Frame(tk_root, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="个人研究与日程助理", font=("Microsoft YaHei", 16)).pack(anchor="w")
        ttk.Label(panel, text=f"界面：{DASHBOARD_URL}    后端端口：{BACKEND_PORT}").pack(anchor="w", pady=(8, 3))
        ttk.Label(panel, text="数据：" + launcher.business_db.name).pack(anchor="w")
        self.lan_option = ttk.Checkbutton(panel, text="允许安卓手机通过局域网接入后端（需要另行启用专用令牌）", variable=self.allow_lan)
        self.lan_option.pack(anchor="w", pady=12)
        buttons = ttk.Frame(panel)
        buttons.pack(anchor="w")
        self.start_button = ttk.Button(buttons, text="启动助理", command=self.start)
        self.start_button.pack(side="left", padx=(0, 8))
        self.open_button = ttk.Button(buttons, text="打开网页", command=self.open_dashboard)
        self.open_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(buttons, text="停止本次服务", command=self.stop)
        self.stop_button.pack(side="left")
        ttk.Label(panel, textvariable=self.status, wraplength=600).pack(anchor="w", pady=15)
        ttk.Label(panel, text="日志保存在local-data/logs。关闭窗口时可选择停止服务，或保留控制台并最小化。").pack(anchor="w")
        tk_root.protocol("WM_DELETE_WINDOW", self.close)
        tk_root.after(250, self.poll)

    def run_worker(self, operation: str, function, *args) -> None:
        """启动短生命周期工作线程，把结果放入队列以避免线程直接操作Tk控件。"""
        self.workers += 1

        def work():
            """执行已授权的本地生命周期操作，并仅把安全错误传回控制台。"""
            try:
                function(*args)
            except LauncherError as error:
                self.events.put((operation, str(error)))
            except Exception:
                self.events.put((operation, "本地启动操作失败，请检查虚拟环境和日志。"))
            else:
                self.events.put((operation, None))

        threading.Thread(target=work, name="assistant-launcher-" + operation, daemon=True).start()

    def start(self) -> None:
        """明确点击后启动两项服务；只把当前复选框值传给后台线程。"""
        self.status.set("正在启动后端和界面，首次加载可能需要一点时间……")
        self.run_worker("start", self.launcher.start, self.allow_lan.get())
        self.start_button.configure(state="disabled")

    def stop(self) -> None:
        """明确点击后取消启动或停止本实例进程，不触碰原有终端启动的服务。"""
        self.status.set("正在停止本控制台创建的服务……")
        self.run_worker("stop", self.launcher.stop)
        self.stop_button.configure(state="disabled")

    def open_dashboard(self) -> None:
        """在系统默认浏览器打开固定本机地址，不因此创建新后端进程。"""
        webbrowser.open(DASHBOARD_URL)

    def poll(self) -> None:
        """在Tk主线程处理工作结果并刷新按钮，服务意外退出时保留停止控制权。"""
        from tkinter import messagebox

        while True:
            try:
                operation, error = self.events.get_nowait()
            except queue.Empty:
                break
            self.workers -= 1
            if error:
                self.status.set(error)
                if not self.exit_after_stop:
                    messagebox.showerror("助理控制台", error, parent=self.root)
            elif operation == "start":
                self.status.set("助理已启动。可以打开网页；需要结束时点击停止本次服务。")
                if not self.exit_after_stop:
                    self.open_dashboard()
            else:
                self.status.set("本控制台创建的服务已停止，数据继续保存在原目录。")
        state = self.launcher.snapshot()
        busy = self.workers > 0
        owned = bool(state["owned"])
        self.start_button.configure(state="disabled" if busy or owned else "normal")
        self.stop_button.configure(state="normal" if (owned or state["starting"]) and not state["stopping"] else "disabled")
        self.lan_option.configure(state="disabled" if busy or owned else "normal")
        if owned and any(item["exit_code"] is not None for item in state["owned"]) and not busy:
            self.status.set("本次服务有进程退出。请先点击停止，再检查local-data/logs后重新启动。")
        if self.exit_after_stop and not busy and not owned and not state["starting"]:
            self.root.destroy()
            return
        self.root.after(250, self.poll)

    def close(self) -> None:
        """关闭时明确选择停止并退出，或保留可控制的最小化窗口，不静默遗留后台服务。"""
        from tkinter import messagebox

        state = self.launcher.snapshot()
        if not state["owned"] and not state["starting"] and not self.workers:
            self.root.destroy()
            return
        decision = messagebox.askyesnocancel(
            "关闭助理控制台", "是否停止本次服务并退出？\n\n是：停止服务并退出\n否：保持运行，将控制台最小化\n取消：返回控制台",
            parent=self.root,
        )
        if decision is True:
            self.exit_after_stop = True
            self.stop()
        elif decision is False:
            self.root.iconify()


def main(argv=None) -> int:
    """提供无需GUI的环境检查和默认Tk控制台；检查模式不会启动任何服务。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="仅检查环境与端口，不读取.env或启动服务")
    arguments = parser.parse_args(argv)
    if arguments.check:
        report = check_environment()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ready"] else 1
    import tkinter as tk

    root = tk.Tk()
    LauncherWindow(root, AssistantLauncher())
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
