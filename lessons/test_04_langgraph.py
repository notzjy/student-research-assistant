"""第四课离线测试：验证实际 LangGraph 流程，模型与文件均隔离。"""

import importlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError


LESSONS = str(Path(__file__).resolve().parent)
if LESSONS not in sys.path:
    sys.path.insert(0, LESSONS)
lesson = importlib.import_module("04_langgraph")


def make_call(call_id, title, time_text=None, location=None):
    """构造保存待办的工具请求，保留调用编号与原始时间、地点文本。"""
    return {
        "name": "save_task",
        "args": {"title": title, "time_text": time_text, "location": location},
        "id": call_id,
        "type": "tool_call",
    }


class FakeModel:
    def __init__(self, *responses):
        """准备按顺序返回的模型响应，并记录工具绑定与每轮消息输入。"""
        self.responses = list(responses)
        self.inputs = []
        self.bound_tools = []

    def bind_tools(self, tools):
        """记录绑定的工具并返回自身，以模拟图中可调用工具的模型。"""
        self.bound_tools.append(list(tools))
        return self

    def invoke(self, messages):
        """记录输入并取出下一条预设响应，额外调用会使测试失败。"""
        self.inputs.append(list(messages))
        if not self.responses:
            raise AssertionError("模型调用次数超过该场景预期")
        return self.responses.pop(0)


class RepeatingToolModel(FakeModel):
    def invoke(self, messages):
        """持续请求保存同一待办，用于检验循环上限和重复数据处理。"""
        self.inputs.append(list(messages))
        return AIMessage(content="", tool_calls=[
            make_call(f"loop_{len(self.inputs)}", "交综述", "周五前")
        ])


class LangGraphTests(unittest.TestCase):
    def setUp(self):
        """隔离待办文件与标准输出，并阻止测试创建真实远程模型。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.task_file = Path(temporary.name) / "local-data" / "lesson03_tasks.json"
        self.enterContext(patch.object(lesson.lesson03, "TASK_FILE", self.task_file))
        self.enterContext(patch.object(
            lesson.lesson03, "create_model",
            side_effect=AssertionError("离线测试不得创建真实模型"),
        ))
        self.enterContext(redirect_stdout(io.StringIO()))

    def read_tasks(self):
        """读取当前测试临时目录中的待办，供持久化结果断言使用。"""
        return json.loads(self.task_file.read_text(encoding="utf-8"))

    def test_tool_round_preserves_messages_and_two_saved_tasks(self):
        """验证工具后经过报告节点，保留完整消息并成功保存两项待办。"""
        calls = [
            make_call("meeting", "开组会", "明天下午三点", "实验楼302"),
            make_call("review", "交综述", "周五前"),
        ]
        proposal = AIMessage(content="", tool_calls=calls)
        final = AIMessage(content="已保存两项待办，未设置定时提醒。")
        model = FakeModel(proposal, final)

        state = lesson.run_graph(lesson.lesson03.NOTICE, model=model)

        self.assertEqual(state["node_history"], ["model", "tools", "report", "model"])
        messages = state["messages"]
        self.assertEqual([type(message) for message in messages], [
            SystemMessage, HumanMessage, AIMessage, ToolMessage, ToolMessage, AIMessage,
        ])
        self.assertEqual(messages[0].content, lesson.INSTRUCTION)
        self.assertEqual(messages[1].content, lesson.lesson03.NOTICE)
        self.assertEqual(messages[2].tool_calls, calls)
        self.assertEqual([message.tool_call_id for message in messages[3:5]], [
            "meeting", "review",
        ])
        self.assertEqual(messages[-1].content, final.content)
        self.assertEqual(model.bound_tools, [[lesson.lesson03.save_task]])
        self.assertEqual([len(history) for history in model.inputs], [2, 5])
        tasks = self.read_tasks()
        self.assertEqual(len(tasks), 2)
        self.assertEqual(len({task["id"] for task in tasks}), 2)
        for call, task, message in zip(calls, tasks, messages[3:5]):
            self.assertEqual({key: task[key] for key in call["args"]}, call["args"])
            self.assertEqual(json.loads(message.content), {"status": "created", "task": task})

    def test_no_tools_ends_after_one_model_node_without_writing(self):
        """验证无需工具时只执行模型节点，跳过工具和报告且不写文件。"""
        model = FakeModel(AIMessage(content="没有需要保存的事项。"))

        state = lesson.run_graph("谢谢，参考论文已收到。", model=model)

        self.assertEqual(state["node_history"], ["model"])
        self.assertEqual(len(model.inputs), 1)
        self.assertEqual(len(state["messages"]), 3)
        self.assertFalse(any(isinstance(message, ToolMessage) for message in state["messages"]))
        self.assertFalse(self.task_file.exists())

    def test_invalid_arguments_can_be_corrected_before_two_successive_tool_rounds(self):
        """验证无效参数可经报告返回模型修正，再连续执行两轮成功工具。"""
        model = FakeModel(
            AIMessage(content="", tool_calls=[make_call("invalid", "")]),
            AIMessage(content="", tool_calls=[
                make_call("corrected", "开组会", "明天下午三点", "实验楼302")
            ]),
            AIMessage(content="", tool_calls=[make_call("next_task", "交综述", "周五前")]),
            AIMessage(content="已保存组会和综述待办。"),
        )

        state = lesson.run_graph(lesson.lesson03.NOTICE, model=model, max_steps=12)

        self.assertEqual(state["node_history"], [
            "model", "tools", "report", "model", "tools", "report",
            "model", "tools", "report", "model",
        ])
        tool_messages = [message for message in state["messages"] if isinstance(message, ToolMessage)]
        self.assertEqual([message.tool_call_id for message in tool_messages], [
            "invalid", "corrected", "next_task",
        ])
        self.assertEqual([json.loads(message.content)["status"] for message in tool_messages], [
            "error", "created", "created",
        ])
        self.assertEqual([len(history) for history in model.inputs], [2, 4, 6, 8])
        self.assertEqual(model.inputs[1][-1].tool_call_id, "invalid")
        self.assertEqual(json.loads(model.inputs[1][-1].content)["status"], "error")
        self.assertEqual([task["title"] for task in self.read_tasks()], ["开组会", "交综述"])

    def test_repeated_tool_requests_stop_at_graph_limit_without_rolling_back(self):
        """验证含报告节点的重复工具循环会停止，已保存待办保留且去重。"""
        model = RepeatingToolModel()

        with self.assertRaises(GraphRecursionError):
            lesson.run_graph(lesson.lesson03.NOTICE, model=model, max_steps=7)

        self.assertGreater(len(model.inputs), 1)
        self.assertLessEqual(len(model.inputs), 7)
        tasks = self.read_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["title"], "交综述")

    def test_invalid_arguments_return_tool_error_without_writing(self):
        """验证工具参数错误仍经过报告节点并反馈模型，全程不写待办。"""
        model = FakeModel(
            AIMessage(content="", tool_calls=[make_call("bad_title", "")]),
            AIMessage(content="待办标题无效，未保存。"),
        )

        state = lesson.run_graph("保存一项待办。", model=model)

        self.assertEqual(state["node_history"], ["model", "tools", "report", "model"])
        tool_messages = [message for message in state["messages"] if isinstance(message, ToolMessage)]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0].tool_call_id, "bad_title")
        self.assertEqual(json.loads(tool_messages[0].content)["status"], "error")
        self.assertEqual(model.inputs[1][-1].tool_call_id, "bad_title")
        self.assertFalse(self.task_file.exists())


if __name__ == "__main__":
    unittest.main()
