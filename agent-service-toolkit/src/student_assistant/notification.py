"""本地通知适配器：终端输出或 Windows 气泡；成功仅表示已提交显示请求。"""

import base64
import json
import os
import subprocess


class LocalNotifier:
    """把已验证的提醒交给本地输出通道，不调用模型，不发送邮件或聊天消息。"""

    def send(self, reminder: dict) -> None:
        """输出一条提醒；抛出的异常由持久化队列记录类型并安排重试。"""
        if reminder["channel"] == "console":
            print(json.dumps({
                "event": "reminder_submitted", "reminder_id": reminder["reminder_id"],
                "task_id": reminder["task_id"], "title": reminder["title"],
                "remind_at": reminder["remind_at"],
            }, ensure_ascii=False), flush=True)
            return
        if reminder["channel"] != "windows" or os.name != "nt":
            raise ValueError("当前系统不支持此本地通知通道")
        payload = base64.b64encode(json.dumps({
            "title": reminder.get("notice_title", "个人助理提醒"), "body": reminder["title"][:250],
            "important": reminder.get("priority") == "high",
        }, ensure_ascii=False).encode("utf-8")).decode("ascii")
        # 业务文本作为环境数据传递，绝不拼接到 PowerShell 代码中执行。
        script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:STUDENT_NOTICE_DATA))
$data = ConvertFrom-Json -InputObject $json
$notice = New-Object System.Windows.Forms.NotifyIcon
try {
    $notice.Icon = [System.Drawing.SystemIcons]::Information
    $notice.Visible = $true
    $icon = [System.Windows.Forms.ToolTipIcon]::Info
    if ($data.important) { $icon = [System.Windows.Forms.ToolTipIcon]::Warning; [System.Media.SystemSounds]::Exclamation.Play() }
    $notice.ShowBalloonTip(5000, $data.title, $data.body, $icon)
    Start-Sleep -Seconds 5
} finally { $notice.Dispose() }
"""
        environment = dict(os.environ)
        environment["STUDENT_NOTICE_DATA"] = payload
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
            env=environment, capture_output=True, timeout=12, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            # 不公开脚本 stderr；持久化记录只保留异常类型。
            raise RuntimeError("Windows 通知提交失败")
