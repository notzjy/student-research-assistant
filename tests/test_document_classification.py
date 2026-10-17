"""文件正文分类测试：显式调用、逐字引用、防文件名猜测与可重试失败。"""

import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)
TaskStore = importlib.import_module("student_assistant.bridge").TaskStore
ReviewStore = importlib.import_module("student_assistant.review_store").ReviewStore
classification_module = importlib.import_module("student_assistant.document_classification")
DocumentClassifier = classification_module.DocumentClassifier
DocumentClassificationError = classification_module.DocumentClassificationError


class FakeClassificationModel:
    """只记录结构化调用并返回测试指定结果，不访问外部模型。"""

    def __init__(self, response):
        """保存可替换响应及调用记录，便于验证失败后的显式重试。"""
        self.response = response
        self.calls = []

    def with_structured_output(self, schema, method):
        """模拟项目已有 JSON 模式接口并返回自身。"""
        return self

    async def ainvoke(self, messages):
        """记录本次消息，返回合成分类或抛出指定异常。"""
        self.calls.append(messages)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class DocumentClassificationTests(unittest.IsolatedAsyncioTestCase):
    """使用真实临时文档表与假模型，不读取真实文件、邮箱或凭证。"""

    def setUp(self):
        """准备与误导文件名相反的正文，避免文件名分类也能碰巧通过测试。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tasks = TaskStore(Path(temporary.name) / "business.sqlite3")
        self.pages = [{"page": 1, "text": "毕业生招聘通知：请提交简历和作品集。"}]
        with closing(sqlite3.connect(self.tasks.db_path)) as connection, connection:
            connection.execute("CREATE TABLE rag_documents(document_id TEXT PRIMARY KEY,filename TEXT,status TEXT,pages_json TEXT)")
            connection.execute("INSERT INTO rag_documents VALUES ('doc-1','研究论文.pdf','indexed',?)", (json.dumps(self.pages, ensure_ascii=False),))
        self.store = ReviewStore(self.tasks)
        self.result = {"category": "求职", "labels": ["招聘", "简历"], "summary": "招聘材料提交要求", "evidence": [{"page": 1, "quote": "请提交简历和作品集"}]}
        self.model = FakeClassificationModel(self.result)
        self.engine = DocumentClassifier(self.store, lambda: self.model)

    async def test_only_explicit_call_classifies_text_without_filename(self):
        """初始化和查询不调用模型，显式分类只发送真实正文与覆盖范围。"""
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.store.get_classification("doc-1")["status"], "unclassified")
        saved = await self.engine.classify("doc-1")
        content = json.loads(self.model.calls[0][1][1])
        self.assertEqual(content["pages"], self.pages)
        self.assertNotIn("filename", content)
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["result"]["category"], "求职")
        self.assertEqual(saved["result"]["coverage"]["scope"], "full_text")
        self.assertEqual(ReviewStore(self.tasks).get_classification("doc-1"), saved)

    async def test_fabricated_quote_fails_and_can_be_retried(self):
        """伪造引用不能成为有效分类，修复模型响应后同一文件能显式重试。"""
        self.model.response = {**self.result, "evidence": [{"page": 1, "quote": "不存在的原文"}]}
        with self.assertRaises(DocumentClassificationError):
            await self.engine.classify("doc-1")
        self.assertEqual(self.store.get_classification("doc-1")["status"], "failed")
        self.model.response = self.result
        self.assertEqual((await self.engine.classify("doc-1"))["status"], "completed")
        self.assertEqual(len(self.model.calls), 2)

    async def test_provider_error_details_never_enter_persistent_result(self):
        """提供商异常文字可能包含秘密；公开与落盘仅记录异常类型。"""
        self.model.response = RuntimeError("secret-provider-key and raw text")
        with self.assertRaises(DocumentClassificationError) as caught:
            await self.engine.classify("doc-1")
        self.assertNotIn("secret", str(caught.exception))
        saved = self.store.get_classification("doc-1")
        self.assertEqual(saved["error_code"], "RuntimeError")
        self.assertNotIn("secret", json.dumps(saved))

    async def test_unindexed_or_missing_document_never_calls_model(self):
        """不存在和提取失败的文件在模型调用前拒绝。"""
        with self.assertRaises(KeyError):
            await self.engine.classify("missing")
        with closing(sqlite3.connect(self.tasks.db_path)) as connection, connection:
            connection.execute("UPDATE rag_documents SET status='failed'")
        with self.assertRaises(ValueError):
            await self.engine.classify("doc-1")
        self.assertEqual(self.model.calls, [])

    async def test_large_document_sampling_records_actual_read_scope(self):
        """超预算文件保留多页节选，并明确披露未读完全部正文。"""
        engine = DocumentClassifier(self.store, lambda: self.model, max_characters=100)
        selected, coverage = engine._sample([{"page": 1, "text": "甲" * 100}, {"page": 2, "text": "乙" * 100}])
        self.assertEqual([page["page"] for page in selected], [1, 2])
        self.assertEqual(coverage["scope"], "sampled_text")
        self.assertEqual(coverage["read_characters"], 100)
        self.assertEqual(coverage["total_characters"], 200)

    async def test_full_budget_reads_uneven_pages_without_unnecessary_truncation(self):
        """页长差别很大但总量未超预算时，应完整读取每页而非提前丢弃长页。"""
        engine = DocumentClassifier(self.store, lambda: self.model, max_characters=100)
        pages = [{"page": 1, "text": "甲" * 80}, {"page": 2, "text": "乙" * 10}]
        selected, coverage = engine._sample(pages)
        self.assertEqual(selected, pages)
        self.assertEqual(coverage["scope"], "full_text")


if __name__ == "__main__":
    unittest.main()
