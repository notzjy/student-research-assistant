# syntax=docker/dockerfile:1
# 同一镜像分别启动API和界面；GitHub Linux运行器已完成实际构建与Compose验收。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ONNX本地Embedding运行时可能依赖OpenMP；仅安装所需共享库。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 先安装锁定依赖，代码变化时可复用依赖安装层。
COPY requirements.lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements.lock.txt

# 明确复制运行文件，模型密钥、用户邮件和数据库由构建上下文排除。
COPY run_assistant.py assistant_dashboard.py dashboard_api.py dashboard_views.py dashboard_review.py dashboard_connections.py dashboard_intake.py ./
COPY android-notification-bridge/dist/student-assistant-bridge.apk ./android-notification-bridge/dist/student-assistant-bridge.apk
COPY lessons/*.py ./lessons/
COPY agent-service-toolkit/src/student_assistant/ ./agent-service-toolkit/src/student_assistant/
COPY agent-service-toolkit/src/service/__init__.py ./agent-service-toolkit/src/service/__init__.py
COPY agent-service-toolkit/LICENSE ./THIRD_PARTY_AGENT_SERVICE_TOOLKIT_LICENSE

EXPOSE 8002 8501

# Compose为两个服务分别覆盖命令；此默认值仅启动单进程API。
CMD ["python", "run_assistant.py", "--host", "0.0.0.0", "--port", "8002", "--db", "/data/assistant.sqlite3", "--checkpoints", "/data/assistant_checkpoints.sqlite3"]
