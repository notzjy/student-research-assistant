@echo off
chcp 65001 >nul
rem 使用项目虚拟环境的无终端Python打开控制台，不改系统启动项。
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo 未找到项目虚拟环境，请先按项目说明安装依赖。
    pause
    exit /b 1
)
rem 可见控制台负责启动和停止自有服务，关闭时由用户选择停止或最小化。
start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0start_assistant.py"
