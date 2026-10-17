"""复用用户已经完成的教学代码；业务模块后续迁移时由这一层统一调整引用。"""

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LESSONS = ROOT / "lessons"
# 保留既有练习文件和用户修改，正式Agent通过此适配层复用，而不复制业务实现。
if str(LESSONS) not in sys.path:
    sys.path.insert(0, str(LESSONS))

lesson02 = importlib.import_module("02_structured_output")
lesson03 = importlib.import_module("03_tool_calling")
TaskStore = importlib.import_module("task_store").TaskStore
create_task_tools = importlib.import_module("assistant_tools").create_task_tools
TimeNormalizationRequest = importlib.import_module("time_parser").TimeNormalizationRequest
parse_reference_time = importlib.import_module("time_parser").parse_reference_time
interpret_time = importlib.import_module("time_semantics").interpret_time
create_model = lesson03.create_model
execute_tool_call = lesson03.execute_tool_call


def model_is_configured() -> bool:
    """只判断兼容模型的三个环境变量是否齐全，不读取.env或返回密钥内容。"""
    import os

    return all(os.environ.get(name, "").strip() for name in (
        "COMPATIBLE_MODEL", "COMPATIBLE_BASE_URL", "COMPATIBLE_API_KEY",
    ))
