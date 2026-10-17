"""第三课的离线测试：假模型验证工具执行，临时目录隔离真实待办。"""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage


SCRIPT = Path(__file__).with_name("03_tool_calling.py")
SPEC = importlib.util.spec_from_file_location("lesson03_tool_calling", SCRIPT)
lesson = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lesson
SPEC.loader.exec_module(lesson)


class FakeBoundModel:
    def __init__(self, parent):
        """保存父模型，以便记录绑定工具后的调用并返回预设提议。"""
        self.parent = parent

    def invoke(self, messages):
        """记录本轮输入，模拟模型提出工具调用或直接答复。"""
        self.parent.proposal_inputs.append(list(messages))
        return self.parent.proposal


class FakeModel:
    def __init__(self, proposal, reply=None):
        """预设模型提议与最终答复，并初始化各阶段调用记录。"""
        self.proposal = proposal
        self.reply = reply
        self.bound_tools = []
        self.proposal_inputs = []
        self.final_inputs = []

    def bind_tools(self, tools):
        """记录待绑定工具，返回只负责产生首轮提议的假模型。"""
        self.bound_tools.append(list(tools))
        return FakeBoundModel(self)

    def invoke(self, messages):
        """模拟收到工具结果后的答复，拒绝场景外的第二次调用。"""
        self.final_inputs.append(list(messages))
        if self.reply is None:
            raise AssertionError("此场景不应发起第二次模型调用")
        return self.reply


def make_call(call_id, args, name="save_task"):
    """构造带唯一调用编号的工具请求，便于检查结果是否对应原请求。"""
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class ToolCallingTests(unittest.TestCase):
    def setUp(self):
        """用临时文件、禁止真实模型的补丁和输出捕获隔离每个测试。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.task_file = Path(temporary.name) / "local-data" / "lesson03_tasks.json"
        self.enterContext(patch.object(lesson, "TASK_FILE", self.task_file))
        self.enterContext(patch.object(
            lesson, "create_model", side_effect=AssertionError("离线测试不得创建真实模型")
        ))
        self.enterContext(redirect_stdout(io.StringIO()))

    def test_two_calls_save_two_tasks_and_return_matching_tool_messages(self):
        """验证两次工具调用分别保存待办，并把对应结果交给模型答复。"""
        calls = [
            make_call("call_meeting", {
                "title": "开组会", "time_text": "明天下午三点", "location": "实验楼302"
            }),
            make_call("call_review", {
                "title": "交综述", "time_text": "周五前", "location": None
            }),
        ]
        proposal = AIMessage(content="", tool_calls=calls)
        reply = AIMessage(content="已保存两项待办，尚未设置定时提醒。")
        model = FakeModel(proposal, reply)

        result = lesson.run_assistant(lesson.NOTICE, model=model)

        tasks = json.loads(self.task_file.read_text(encoding="utf-8"))
        self.assertEqual(len(tasks), 2)
        self.assertEqual(len({task["id"] for task in tasks}), 2)
        for task, call in zip(tasks, calls):
            for field, value in call["args"].items():
                self.assertEqual(task[field], value)
            self.assertEqual(task["status"], "pending")
        self.assertEqual(model.bound_tools, [[lesson.save_task]])
        self.assertEqual(len(model.proposal_inputs), 1)
        self.assertEqual(len(model.proposal_inputs[0]), 2)
        self.assertEqual(len(model.final_inputs), 1)
        self.assertIs(model.final_inputs[0][2], proposal)
        tool_messages = model.final_inputs[0][3:]
        self.assertTrue(all(isinstance(message, ToolMessage) for message in tool_messages))
        self.assertEqual([message.tool_call_id for message in tool_messages], [
            "call_meeting", "call_review"
        ])
        for message, task in zip(tool_messages, tasks):
            self.assertEqual(message.name, "save_task")
            self.assertEqual(json.loads(message.content), {"status": "created", "task": task})
        self.assertEqual(result["final_reply"], reply.content)
        self.assertEqual([item["status"] for item in result["tool_results"]], [
            "created", "created"
        ])

    def test_identical_arguments_do_not_create_another_task(self):
        """验证重复参数返回已有待办，文件内容和记录数量均保持不变。"""
        args = {"title": "交综述", "time_text": "周五前", "location": None}
        first = lesson.save_task.invoke(args)
        original = self.task_file.read_bytes()

        second = lesson.save_task.invoke(args)

        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "exists")
        self.assertEqual(first["task"], second["task"])
        self.assertEqual(self.task_file.read_bytes(), original)
        self.assertEqual(len(json.loads(original)), 1)

    def test_unknown_tool_returns_error_without_writing(self):
        """验证未知工具返回关联调用编号的错误，且不会创建待办文件。"""
        response = lesson.execute_tool_call(make_call(
            "call_unknown", {"title": "不应保存"}, name="unknown_tool"
        ))

        self.assertIsInstance(response, ToolMessage)
        self.assertEqual(response.tool_call_id, "call_unknown")
        self.assertEqual(json.loads(response.content)["status"], "error")
        self.assertFalse(self.task_file.exists())

    def test_invalid_arguments_return_error_without_writing(self):
        """验证空标题、错误类型及多余字段均被拒绝且不写入数据。"""
        for args in ({"title": ""}, {"title": 123}, {"title": "交综述", "unexpected": True}):
            with self.subTest(args=args):
                response = lesson.execute_tool_call(make_call("call_invalid", args))

                self.assertEqual(response.tool_call_id, "call_invalid")
                self.assertEqual(json.loads(response.content)["status"], "error")
                self.assertFalse(self.task_file.exists())

    def test_corrupt_json_and_invalid_file_structure_are_not_overwritten(self):
        """验证损坏或结构无效的原文件得到保留，不产生临时覆盖文件。"""
        self.task_file.parent.mkdir(parents=True)
        for original in ('{"unfinished": [', '{}', '[{}]'):
            with self.subTest(original=original):
                self.task_file.write_text(original, encoding="utf-8")

                response = lesson.execute_tool_call(make_call(
                    "call_corrupt", {"title": "交综述"}
                ))

                self.assertEqual(json.loads(response.content)["status"], "error")
                self.assertEqual(self.task_file.read_text(encoding="utf-8"), original)
                self.assertFalse(self.task_file.with_suffix(".json.tmp").exists())

    def test_no_tool_calls_skips_second_model_call_and_file_write(self):
        """验证无需工具时直接返回首轮答复，不再调用模型或写入文件。"""
        proposal = AIMessage(content="这条通知没有明确待办。")
        model = FakeModel(proposal)

        result = lesson.run_assistant("收到参考资料，谢谢。", model=model)

        self.assertEqual(len(model.proposal_inputs), 1)
        self.assertEqual(model.final_inputs, [])
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["tool_results"], [])
        self.assertEqual(result["final_reply"], proposal.content)
        self.assertFalse(self.task_file.exists())


if __name__ == "__main__":
    unittest.main()
