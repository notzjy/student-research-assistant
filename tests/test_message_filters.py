"""消息检索回归：人工覆盖先筛选再分页，旧消息不会被最近100条窗口永久隐藏。"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)
TaskStore = importlib.import_module("student_assistant.bridge").TaskStore
MessageStore = importlib.import_module("student_assistant.message_store").MessageStore
models = importlib.import_module("student_assistant.message_models")
ReviewStore = importlib.import_module("student_assistant.review_store").ReviewStore


class MessageFilterTests(unittest.TestCase):
    """使用110条真实持久化消息验收容量、累计人工覆盖、总数和分页一致性。"""

    def setUp(self):
        """准备超过旧窗口大小的合成消息，最早消息保留独有文本以暴露截断错误。"""
        temporary = tempfile.TemporaryDirectory(prefix="student_message_filters_")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.tasks = TaskStore(self.folder / "business.sqlite3")
        self.messages = MessageStore(self.tasks)
        self.reviews = ReviewStore(self.tasks)
        self.ids = []
        for index in range(110):
            text = "最早通知包含100%_完成率。" if index == 0 else "第" + str(index) + "条普通通知。"
            source = models.MessageInput(
                source="manual", account_id="synthetic", source_message_id="filter-" + str(index),
                sender_id="teacher-1" if index in (0, 1) else "student-" + str(index),
                sender_name="Original Sender" if index == 0 else "普通同学",
                sent_at="2026-10-17T10:00:00+08:00", text=text,
            )
            message_id = self.messages.receive(source)["message_id"]
            self.messages.complete_analysis(message_id, {
                "category": "研究", "summary": "原始摘要" + str(index),
                "labels": ["模型旧标签"] if index in (0, 1, 2) else [],
            }, [])
            self.ids.append(message_id)

    def ids_of(self, messages):
        """从持久详情提取编号，避免用模型摘要文字判断分页是否完整。"""
        return [message["message_id"] for message in messages]

    def test_old_manual_category_and_label_are_filtered_before_limit(self):
        """最早消息人工纠正后即使位于第110条，也能用类别或标签检索出来。"""
        self.reviews.apply_message(self.ids[0], {"category": "求职", "labels": ["人工归档", "待读"]})
        selected = self.messages.list_reviewed_messages(category="求职", label="人工归档", limit=1)
        self.assertEqual(self.ids_of(selected), [self.ids[0]])
        self.assertEqual(self.messages.count_reviewed_messages(category="求职", label="人工归档"), 1)
        self.assertEqual(self.messages.count_reviewed_messages(category="研究"), 109)
        viewed = self.reviews.detail_overlay(selected[0])
        self.assertEqual(viewed["analysis"]["category"], "求职")
        self.assertEqual(viewed["original_analysis"]["category"], "研究")

    def test_manual_empty_labels_override_original_model_labels(self):
        """显式清空标签不会重新匹配模型旧标签；未纠正消息仍可按旧标签查到。"""
        self.reviews.apply_message(self.ids[0], {"labels": []})
        self.reviews.apply_message(self.ids[1], {"labels": ["新标签"]})
        selected = self.messages.list_reviewed_messages(label="模型旧标签")
        self.assertEqual(self.ids_of(selected), [self.ids[2]])
        self.assertEqual(self.messages.count_reviewed_messages(label="模型旧标签"), 1)
        self.assertEqual(self.ids_of(self.messages.list_reviewed_messages(label="新标签")), [self.ids[1]])

    def test_all_pages_have_no_duplicates_or_hidden_messages(self):
        """稳定rowid倒序分页覆盖全部110条，页外offset返回空且总数不随页变化。"""
        collected = []
        for offset in (0, 40, 80):
            collected.extend(self.ids_of(self.messages.list_reviewed_messages(limit=40, offset=offset)))
        self.assertEqual(collected, list(reversed(self.ids)))
        self.assertEqual(len(set(collected)), 110)
        self.assertEqual(self.messages.list_reviewed_messages(offset=110), [])
        self.assertEqual(self.messages.count_reviewed_messages(), 110)

    def test_search_covers_old_original_text_sender_and_reviewed_summary(self):
        """搜索原文、发送人和人工摘要都能定位旧消息，旧摘要在被纠正后不再误匹配。"""
        self.reviews.apply_message(self.ids[0], {"summary": "纠正后的摘要 AlphaProject"})
        for query in ("100%_", "original sender", "ALPHAPROJECT"):
            with self.subTest(query=query):
                self.assertEqual(self.ids_of(self.messages.list_reviewed_messages(query=query)), [self.ids[0]])
                self.assertEqual(self.messages.count_reviewed_messages(query=query), 1)
        self.assertEqual(self.messages.count_reviewed_messages(query="原始摘要0"), 0)
        self.assertEqual(self.messages.count_reviewed_messages(query="' OR 1=1 --"), 0)
        self.assertEqual(self.messages.count_reviewed_messages(query="  "), 110)

    def test_role_category_label_and_search_filters_share_count_logic(self):
        """重点角色精确匹配并与人工类别、标签和搜索组合，列表及总数保持一致。"""
        self.messages.upsert_role(models.RoleRule(
            source="manual", account_id="synthetic", sender_id="teacher-1", role="导师", important=True,
        ))
        self.reviews.apply_message(self.ids[0], {"category": "其他", "labels": ["重要材料"], "summary": "迁移记录"})
        conditions = {"important_only": True, "category": "其他", "label": "重要材料", "query": "迁移"}
        self.assertEqual(self.ids_of(self.messages.list_reviewed_messages(**conditions)), [self.ids[0]])
        self.assertEqual(self.messages.count_reviewed_messages(**conditions), 1)
        self.assertEqual(self.messages.count_reviewed_messages(important_only=True), 2)

    def test_no_review_table_remains_compatible_and_invalid_pagination_is_rejected(self):
        """未启用修订表的旧库也可检索；无效页码和筛选类型在SQL前拒绝。"""
        old = MessageStore(TaskStore(self.folder / "old.sqlite3"))
        source = models.MessageInput(source="manual", source_message_id="old", sender_id="old", sent_at="2026-10-17T10:00:00+08:00", text="旧库通知")
        message_id = old.receive(source)["message_id"]
        old.complete_analysis(message_id, {"category": "学生生活", "labels": ["生活"], "summary": "旧摘要"}, [])
        self.assertEqual(self.ids_of(old.list_reviewed_messages(category="学生生活", label="生活", query="旧库")), [message_id])
        self.assertEqual(old.count_reviewed_messages(category="学生生活", label="生活"), 1)
        for options in ({"limit": 0}, {"limit": True}, {"offset": -1}, {"offset": True}, {"query": 3}, {"label": []}, {"important_only": 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                old.list_reviewed_messages(**options)


if __name__ == "__main__":
    unittest.main()
