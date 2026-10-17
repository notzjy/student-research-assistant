"""工具包服务入口：显式选择个人助理配置档，默认保留上游服务行为。"""

import os


# 独立配置档避免个人助理启动时导入所有示例 Agent 和供应商依赖。
if os.environ.get("AGENT_SERVICE_PROFILE") == "student":
    from student_assistant.service import app
else:
    from service.service import app

__all__ = ["app"]
