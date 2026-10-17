"""研究文件 RAG 离线集成测试：真实解析、受管原件和 Qdrant，外部模型与 embedding 使用替身。"""

import hashlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from openai import APIError


ROOT = Path(__file__).resolve().parents[1]
SOURCE = str(ROOT / "agent-service-toolkit" / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

service = importlib.import_module("student_assistant.service")
documents = importlib.import_module("student_assistant.rag_documents")
EmbeddingUnavailable = importlib.import_module("student_assistant.rag_embeddings").EmbeddingUnavailable


class FakeEmbeddings:
    """仅替换外部 embedding 请求，使用三个可区分方向验证真实向量索引和排序。"""

    model_id = "offline-semantic-v1"
    dimension = 3

    def __init__(self):
        """记录文档和查询调用，并允许指定一次外部服务故障。"""
        self.document_calls = []
        self.query_calls = []
        self.failure = None

    @staticmethod
    def vector(text):
        """为合成的天文、海洋及无关语料返回互相正交的三维向量。"""
        lowered = text.lower()
        if "stellar" in lowered:
            return [1.0, 0.0, 0.0]
        if "ocean" in lowered:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    def embed_documents(self, texts):
        """记录批量文本并返回向量；设定故障时模拟提供商失败，不替换向量数据库。"""
        self.document_calls.append(list(texts))
        if self.failure is not None:
            raise self.failure
        return [self.vector(text) for text in texts]

    def embed_query(self, text):
        """记录查询并返回同一向量空间中的向量。"""
        self.query_calls.append(text)
        if self.failure is not None:
            raise self.failure
        return self.vector(text)


class AnswerModel:
    """返回预设回答草稿或提供商异常，真实检索和引用校验仍由生产代码执行。"""

    def __init__(self, draft=None):
        """保存可在检索完成后填入的草稿，并初始化 schema 与输入记录。"""
        self.draft = draft
        self.schema = None
        self.schema_calls = []
        self.inputs = []

    def with_structured_output(self, schema, *, method):
        """记录回答使用的实际结构和 JSON 模式，返回模型自身。"""
        self.schema = schema
        self.schema_calls.append((schema, method))
        return self

    async def ainvoke(self, messages):
        """记录检索证据输入，返回校验后的预设草稿或抛出指定错误。"""
        self.inputs.append(list(messages))
        if isinstance(self.draft, Exception):
            raise self.draft
        if self.schema is None or self.draft is None:
            raise AssertionError("出现未配置的回答模型调用")
        return self.schema.model_validate(self.draft)


class RetrievalAgentModel:
    """让主 Agent 请求真实文件检索，记录其收到的工具证据，不模拟数据库或引用校验。"""

    def __init__(self):
        """准备一次检索请求和一次结语，并初始化消息与工具记录。"""
        self.responses = [
            AIMessage(content="", tool_calls=[{
                "name": "search_documents", "id": "agent-search", "type": "tool_call",
                "args": {"query": "stellar", "top_k": 5, "min_score": 0.35},
            }]),
            AIMessage(content="stellar.txt 第1页记录了测量结果。"),
        ]
        self.inputs = []
        self.tools = []

    def bind_tools(self, tools):
        """保存主图绑定的真实工具，让请求继续交由生产注册表执行。"""
        self.tools = list(tools)
        return self

    async def ainvoke(self, messages):
        """记录每轮实际历史并返回预设请求；额外调用立即使测试失败。"""
        self.inputs.append(list(messages))
        if not self.responses:
            raise AssertionError("出现未预期的 Agent 模型调用")
        return self.responses.pop(0)


def synthetic_pdf(page_texts):
    """生成带真实页面和文本绘制流的合成 PDF，无需写 PDF 的额外依赖。"""
    font_id = 3 + 2 * len(page_texts)
    page_ids = [3 + 2 * index for index in range(len(page_texts))]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{value} 0 R' for value in page_ids)}] /Count {len(page_ids)} >>".encode("ascii"),
    ]
    for page_id, text in zip(page_ids, page_texts, strict=True):
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
        objects.extend([
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {page_id + 1} 0 R >>".encode("ascii"),
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream",
        ])
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(output)


class DocumentRAGTests(unittest.TestCase):
    """使用临时业务库、检查点和文件目录检验文件到受证据约束回答的完整链路。"""

    def setUp(self):
        """隔离两份 SQLite、受管文件和 Qdrant，准备明确可检索的合成语料。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.business_db = self.folder / "business.sqlite3"
        self.checkpoint_db = self.folder / "checkpoints.sqlite3"
        self.rag_dir = self.folder / "managed-rag"
        self.embedding = FakeEmbeddings()
        self.embedding_factory = Mock(return_value=self.embedding)
        self.model_factory = Mock(side_effect=AssertionError("此操作不应创建回答模型"))
        self.stellar = b"Stellar research reports a measured value of forty-two."
        self.ocean = b"Ocean research reports a measured value of seven."

    def application(self, factory=None, embedding=None):
        """创建指向临时目录的应用，只注入模型和 embedding，不替换解析器或 Qdrant。"""
        return service.create_app(
            business_db=self.business_db, checkpoint_db=self.checkpoint_db,
            model_factory=self.model_factory if factory is None else factory,
            rag_dir=self.rag_dir,
            embedding_factory=self.embedding_factory if embedding is None else Mock(return_value=embedding),
        )

    def make_client(self, factory=None):
        """进入完整服务生命周期，确保向量客户端及检查点在测试结束时关闭。"""
        return self.enterContext(TestClient(self.application(factory)))

    def unconfigured_client(self):
        """让消息先以 503 保留附件线索，文件索引仍仅依赖注入的离线 embedding。"""
        self.enterContext(patch.dict(os.environ, {
            "COMPATIBLE_MODEL": "", "COMPATIBLE_BASE_URL": "", "COMPATIBLE_API_KEY": "",
        }))
        app = service.create_app(
            business_db=self.business_db, checkpoint_db=self.checkpoint_db,
            rag_dir=self.rag_dir, embedding_factory=self.embedding_factory,
        )
        return self.enterContext(TestClient(app))

    def upload(self, client, filename, raw, **binding):
        """按公开二进制上传协议发送实际文件内容，返回 HTTP 响应。"""
        return client.post("/documents", params={"filename": filename, **binding}, content=raw, headers={"Content-Type": "application/octet-stream"})

    def indexed(self, client, filename="stellar.txt", raw=None, **binding):
        """上传并确认索引成功，返回包含稳定文档编号的详情。"""
        response = self.upload(client, filename, self.stellar if raw is None else raw, **binding)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["document"]["status"], "indexed")
        return response.json()["document"]

    def search(self, client, query="stellar", document_id=None):
        """使用真实向量检索接口查找片段，可明确限制文档。"""
        response = client.post("/documents/search", json={
            "query": query, "top_k": 5, "min_score": 0.35, "document_id": document_id,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def receive_hints(self, client, source_id="mail-001"):
        """提交两条附件线索并确认缺模型配置仍保存完整消息，返回可关联的消息编号。"""
        payload = {
            "source": "qq_mail", "account_id": "research-mail", "source_message_id": source_id,
            "sender_id": "mentor@example.invalid", "sender_name": "导师",
            "sent_at": "2026-10-16T20:00:00+08:00", "text": "附件是两篇研究参考论文。",
            "attachments": [{"filename": "paper.txt"}, {"filename": "other-paper.pdf"}],
        }
        response = client.post("/messages", json=payload)
        self.assertEqual(response.status_code, 503)
        return response.json()["detail"]["message_id"]

    def test_text_markdown_parsing_and_managed_original_survive_source_removal(self):
        """文本和 Markdown 走真实解析，上传后保留字节一致的受管原件而非外部文件路径。"""
        text_pages = documents.parse_document("notes.txt", "\ufeff研究原文\n第二行".encode("utf-8"))
        markdown_pages = documents.parse_document("notes.md", b"# Stellar notes\n\nMeasured value: forty-two.")
        self.assertEqual(text_pages[0]["page"], 1)
        self.assertIn("研究原文", text_pages[0]["text"])
        self.assertNotIn("\ufeff", text_pages[0]["text"])
        self.assertIn("Measured value: forty-two.", markdown_pages[0]["text"])
        original = self.folder / "provided.txt"
        original.write_bytes(self.stellar)
        client = self.make_client()
        detail = self.indexed(client, filename=original.name, raw=original.read_bytes())
        original.unlink()

        downloaded = client.get(f"/documents/{detail['document_id']}/file")

        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, self.stellar)
        self.assertEqual(detail["sha256"], hashlib.sha256(self.stellar).hexdigest())
        managed = [path for path in self.rag_dir.rglob("*") if path.is_file() and path.stat().st_size == len(self.stellar)]
        self.assertTrue(any(path.read_bytes() == self.stellar for path in managed))
        self.model_factory.assert_not_called()

    def test_real_two_page_pdf_chunks_preserve_page_offsets_and_overlap(self):
        """合成 PDF 由真实解析器读取，片段能按页与偏移还原，并保留覆盖和相邻重叠。"""
        raw = synthetic_pdf([
            "Stellar:" + ":".join(f"{number:03d}" for number in range(160)),
            "Ocean:" + "-".join(f"{number:03d}" for number in range(80)),
        ])
        pages = documents.parse_document("two-pages.pdf", raw)
        self.assertEqual([page["page"] for page in pages], [1, 2])
        self.assertIn("Stellar:", pages[0]["text"])
        self.assertIn("Ocean:", pages[1]["text"])
        chunks = documents.chunk_pages(pages, chunk_size=100, overlap=20)
        self.assertEqual(len({chunk["chunk_index"] for chunk in chunks}), len(chunks))
        for page in pages:
            page_chunks = [chunk for chunk in chunks if chunk["page"] == page["page"]]
            self.assertEqual(page_chunks[0]["start"], 0)
            self.assertEqual(page_chunks[-1]["end"], len(page["text"]))
            for chunk in page_chunks:
                self.assertEqual(chunk["text"], page["text"][chunk["start"]:chunk["end"]])
            for earlier, later in zip(page_chunks, page_chunks[1:]):
                self.assertEqual(earlier["end"] - later["start"], 20)

    def test_real_vector_search_ranks_evidence_and_applies_document_filter(self):
        """只替换 embedding 服务，真实 Qdrant 用向量方向区分两类文档并执行文档筛选。"""
        client = self.make_client()
        stellar = self.indexed(client)
        ocean = self.indexed(client, "ocean.md", self.ocean)

        result = self.search(client, "ocean")

        self.assertEqual(result["model_id"], self.embedding.model_id)
        self.assertEqual(result["hits"][0]["document_id"], ocean["document_id"])
        self.assertIn("Ocean research", result["hits"][0]["text"])
        self.assertEqual(result["hits"][0]["page"], 1)
        self.assertGreater(result["hits"][0]["score"], 0.9)
        self.assertEqual(self.search(client, "stellar", ocean["document_id"])["hits"], [])
        first = self.search(client, "stellar", stellar["document_id"])["hits"][0]
        repeated = self.search(client, "stellar", stellar["document_id"])["hits"][0]
        self.assertEqual(first["chunk_id"], repeated["chunk_id"])
        self.assertEqual(first["text"], self.stellar.decode()[first["start"]:first["end"]])
        self.model_factory.assert_not_called()

    def test_content_hash_deduplicates_without_reembedding_and_keeps_first_filename(self):
        """相同字节换文件名复用首文档，内容变化另建索引，重复上传不再计算 embedding。"""
        client = self.make_client()
        first = self.indexed(client, "original.txt")

        duplicate = self.upload(client, "renamed.md", self.stellar)

        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.json()["status"], "exists")
        self.assertEqual(duplicate.json()["document"]["document_id"], first["document_id"])
        self.assertEqual(duplicate.json()["document"]["filename"], "original.txt")
        self.assertEqual(len(self.embedding.document_calls), 1)
        changed = self.indexed(client, "updated.txt", self.stellar + b" Additional evidence.")
        self.assertNotEqual(changed["document_id"], first["document_id"])
        self.assertEqual(client.get("/documents").json()["count"], 2)
        self.assertEqual(len(self.embedding.document_calls), 2)

    def test_app_restart_restores_original_and_vector_index_without_reembedding(self):
        """关闭真实 Qdrant 后重新创建应用，文档、原件和向量仍可查询且无需重新嵌入。"""
        with TestClient(self.application()) as first:
            detail = self.indexed(first)
            chunk_id = self.search(first)["hits"][0]["chunk_id"]
        second_embedding = FakeEmbeddings()

        with TestClient(self.application(embedding=second_embedding)) as reopened:
            self.assertEqual(reopened.get("/documents").json()["count"], 1)
            self.assertEqual(reopened.get(f"/documents/{detail['document_id']}/file").content, self.stellar)
            self.assertEqual(second_embedding.document_calls, [])
            self.assertEqual(second_embedding.query_calls, [])
            self.assertEqual(self.search(reopened)["hits"][0]["chunk_id"], chunk_id)
            self.assertEqual(second_embedding.document_calls, [])

    def test_unsafe_names_unsupported_types_and_oversize_uploads_leave_no_documents(self):
        """拒绝路径穿越、绝对路径、非法类型和超限上传，受管目录外不产生文件。"""
        client = self.make_client()
        for filename in ("../escape.txt", "..\\escape.txt", "C:\\escape.txt", "/escape.txt", "program.exe", ""):
            with self.subTest(filename=filename):
                self.assertEqual(self.upload(client, filename, self.stellar).status_code, 422)
        oversized = self.upload(client, "oversized.txt", b"x" * (10 * 1024 * 1024 + 1))
        self.assertEqual(oversized.status_code, 413)
        self.assertFalse((self.folder / "escape.txt").exists())
        self.assertEqual(client.get("/documents").json(), {"count": 0, "documents": []})
        self.assertEqual(self.embedding.document_calls, [])

    def test_blank_corrupt_and_encrypted_pdf_fail_but_keep_original_bytes(self):
        """无可提取正文、损坏及加密 PDF 明确失败，原始字节仍可查询而不伪造文本索引。"""
        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        writer.append_pages_from_reader(PdfReader(io.BytesIO(synthetic_pdf(["Confidential synthetic text."]))))
        writer.encrypt("test-password")
        encrypted = io.BytesIO()
        writer.write(encrypted)
        client = self.make_client()
        cases = [synthetic_pdf([""]), b"%PDF-1.4\ninvalid", encrypted.getvalue()]
        for index, raw in enumerate(cases):
            with self.subTest(index=index):
                response = self.upload(client, f"invalid-{index}.pdf", raw)
                self.assertEqual(response.status_code, 422)
                document_id = response.json()["detail"]["document_id"]
                detail = client.get(f"/documents/{document_id}").json()
                self.assertEqual(detail["status"], "failed")
                self.assertTrue(detail["error_code"])
                self.assertEqual(client.get(f"/documents/{document_id}/file").content, raw)
        self.assertEqual(self.embedding.document_calls, [])
        self.assertEqual(self.search(client)["hits"], [])

    def test_embedding_failure_keeps_original_and_retry_indexes_without_partial_hits(self):
        """embedding 失败不留下可检索片段，原件保留且同字节重传可恢复索引。"""
        # 替身遵守真实 LocalEmbedder 的公开异常协议，而不是抛出未知内部异常。
        self.embedding.failure = EmbeddingUnavailable("private-embedding-detail")
        client = self.make_client()

        failed = self.upload(client, "stellar.txt", self.stellar)

        self.assertEqual(failed.status_code, 503)
        self.assertNotIn("private-embedding-detail", failed.text)
        document_id = failed.json()["detail"]["document_id"]
        self.assertEqual(client.get(f"/documents/{document_id}").json()["status"], "failed")
        self.assertEqual(client.get(f"/documents/{document_id}/file").content, self.stellar)
        self.embedding.failure = None
        self.assertEqual(self.search(client)["hits"], [])
        retried = self.upload(client, "stellar.txt", self.stellar)
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["status"], "indexed")
        self.assertEqual(retried.json()["document"]["document_id"], document_id)
        self.assertEqual(len(self.search(client)["hits"]), 1)

    def test_uploaded_document_binds_multiple_sources_without_mutating_original_hints(self):
        """实际上传才改变派生附件可用状态；同文件关联多消息仍保留每条来源和原始 mentioned。"""
        client = self.unconfigured_client()
        first_message = self.receive_hints(client)
        second_message = self.receive_hints(client, "mail-002")
        self.assertEqual(self.search(client)["hits"], [])

        detail = self.indexed(client, "paper.txt", message_id=first_message, attachment_index=0)

        first = client.get(f"/messages/{first_message}").json()
        second = client.get(f"/messages/{second_message}").json()
        self.assertEqual(first["attachments"][0]["availability"], "indexed")
        self.assertEqual(first["attachments"][0]["document_id"], detail["document_id"])
        self.assertEqual(first["attachments"][1]["availability"], "mentioned")
        self.assertEqual(first["source_message"]["attachments"][0]["availability"], "mentioned")
        self.assertEqual(second["attachments"][0]["availability"], "mentioned")
        rebound = self.upload(client, "paper.txt", self.stellar, message_id=second_message, attachment_index=0)
        self.assertEqual(rebound.status_code, 200)
        self.assertEqual(rebound.json()["status"], "exists")
        sources = rebound.json()["document"]["sources"]
        self.assertEqual({source["message_id"] for source in sources}, {first_message, second_message})
        self.assertTrue(all(source["source"] == "qq_mail" and source["account_id"] == "research-mail" for source in sources))
        self.assertEqual(client.get(f"/messages/{second_message}").json()["attachments"][0]["availability"], "indexed")
        self.assertEqual(len(self.embedding.document_calls), 1)
        self.assertEqual({source["message_id"] for source in self.search(client)["hits"][0]["sources"]}, {first_message, second_message})

    def test_invalid_message_or_attachment_binding_does_not_create_orphan_document(self):
        """目标消息或附件序号无效时不创建原件文档、向量或关联。"""
        client = self.unconfigured_client()
        message_id = self.receive_hints(client)
        invalid_bindings = [
            {"message_id": "missing-message", "attachment_index": 0},
            {"message_id": message_id, "attachment_index": 9},
        ]
        for binding in invalid_bindings:
            with self.subTest(binding=binding):
                response = self.upload(client, "paper.txt", self.stellar, **binding)
                self.assertIn(response.status_code, (404, 422))
                self.assertEqual(client.get("/documents").json()["count"], 0)
        self.assertEqual(self.embedding.document_calls, [])
        self.assertEqual(client.get(f"/messages/{message_id}").json()["attachments"][0]["availability"], "mentioned")

    def test_ask_uses_real_search_and_returns_verified_quote_and_source_metadata(self):
        """回答模型收到当次检索片段，返回引用经 ID 与逐字片段校验并附文件和页码。"""
        model = AnswerModel()
        factory = Mock(return_value=model)
        client = self.make_client(factory)
        detail = self.indexed(client)
        hit = self.search(client)["hits"][0]
        model.draft = {"status": "answered", "answer": "The measured value is forty-two.", "citations": [{"chunk_id": hit["chunk_id"], "quote": hit["text"]}]}

        response = client.post("/documents/ask", json={"question": "What did stellar research measure?"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["model_id"], self.embedding.model_id)
        self.assertGreater(body["retrieved_count"], 0)
        citation = body["citations"][0]
        self.assertEqual(citation["chunk_id"], hit["chunk_id"])
        self.assertEqual(citation["quote"], hit["text"])
        self.assertEqual(citation["document_id"], detail["document_id"])
        self.assertEqual(citation["filename"], "stellar.txt")
        self.assertEqual(citation["page"], 1)
        self.assertEqual(citation["sources"], [])
        self.assertEqual(model.schema_calls[0][1], "json_mode")
        self.assertEqual(len(model.inputs), 1)
        model_input = "\n".join(message[1] if isinstance(message, tuple) else message.content for message in model.inputs[0])
        self.assertIn(hit["chunk_id"], model_input)
        self.assertIn(hit["text"], model_input)
        factory.assert_called_once_with()

    def test_empty_index_and_unrelated_query_return_unknown_without_answer_model(self):
        """空索引不请求 embedding，检索没有命中时直接 unknown 而不让回答模型猜测。"""
        client = self.make_client()
        empty = client.post("/documents/ask", json={"question": "What does stellar research say?"})
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["status"], "unknown")
        self.assertEqual(empty.json()["reason"], "no_hits")
        self.assertEqual(empty.json()["citations"], [])
        self.assertEqual(self.embedding.query_calls, [])
        self.indexed(client)

        unrelated = client.post("/documents/ask", json={"question": "What is the botany result?"})

        self.assertEqual(unrelated.status_code, 200)
        self.assertEqual(unrelated.json()["status"], "unknown")
        self.assertEqual(unrelated.json()["reason"], "no_hits")
        self.assertEqual(unrelated.json()["retrieved_count"], 0)
        self.model_factory.assert_not_called()

    def test_model_unknown_discards_unsupported_draft_answer(self):
        """即使检索有结果，模型判断不知道时也返回固定未知说明，丢弃其草稿断言。"""
        model = AnswerModel({"status": "unknown", "answer": "private-unsupported-answer", "citations": []})
        client = self.make_client(Mock(return_value=model))
        self.indexed(client)

        response = client.post("/documents/ask", json={"question": "What did stellar research measure?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")
        self.assertEqual(response.json()["reason"], "insufficient_evidence")
        self.assertEqual(response.json()["citations"], [])
        self.assertNotIn("private-unsupported-answer", response.text)

    def test_hallucinated_missing_or_cross_search_citations_are_rejected(self):
        """伪造 ID、错引原文、无引用及复用本次未命中的旧片段均降级为安全 unknown。"""
        model = AnswerModel()
        client = self.make_client(Mock(return_value=model))
        stellar = self.indexed(client)
        ocean = self.indexed(client, "ocean.txt", self.ocean)
        stellar_hit = self.search(client, "stellar")["hits"][0]
        ocean_hit = self.search(client, "ocean")["hits"][0]
        invalid_citations = [
            [{"chunk_id": "00000000-0000-0000-0000-000000000000", "quote": ocean_hit["text"]}],
            [{"chunk_id": ocean_hit["chunk_id"], "quote": "This quotation does not appear in the evidence."}],
            [{"chunk_id": ocean_hit["chunk_id"], "quote": stellar_hit["text"]}],
            [{"chunk_id": stellar_hit["chunk_id"], "quote": stellar_hit["text"]}],
            [],
        ]
        for citations in invalid_citations:
            with self.subTest(citations=citations):
                model.draft = {"status": "answered", "answer": "private-fabricated-answer", "citations": citations}
                response = client.post("/documents/ask", json={"question": "What did ocean research measure?", "document_id": ocean["document_id"]})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "unknown")
                self.assertEqual(response.json()["reason"], "invalid_citations")
                self.assertEqual(response.json()["citations"], [])
                self.assertNotIn("private-fabricated-answer", response.text)
        self.assertNotEqual(stellar["document_id"], ocean["document_id"])
        self.assertEqual(client.app.state.engine.store.list_tasks(), [])

    def test_answer_provider_failure_is_safe_and_does_not_modify_index(self):
        """回答模型失败返回安全状态，原件、检索结果和业务任务保持可用。"""
        request = httpx.Request("POST", "https://model.invalid/chat/completions")
        model = AnswerModel(APIError("private-provider-detail", request=request, body=None))
        client = self.make_client(Mock(return_value=model))
        detail = self.indexed(client)
        original_hit = self.search(client)["hits"][0]

        response = client.post("/documents/ask", json={"question": "What did stellar research measure?"})

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-provider-detail", response.text)
        self.assertEqual(self.search(client)["hits"][0]["chunk_id"], original_hit["chunk_id"])
        self.assertEqual(client.get(f"/documents/{detail['document_id']}/file").content, self.stellar)
        self.assertEqual(client.app.state.engine.store.list_tasks(), [])

    def test_main_agent_retrieval_tool_returns_real_index_hits_to_model(self):
        """主 Agent 的文件检索工具读取同一真实索引，并把带来源的结果回传模型。"""
        model = RetrievalAgentModel()
        client = self.make_client(Mock(return_value=model))
        detail = self.indexed(client)

        response = client.post("/invoke", json={"message": "请检索 stellar 研究文件。", "thread_id": "rag-agent"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["execution_status"], "completed")
        self.assertEqual(response.json()["tool_names"], ["search_documents"])
        self.assertEqual(response.json()["node_order"], ["model", "tools", "report", "model"])
        result = response.json()["tool_results"][0]
        self.assertEqual(result["status"], "retrieved")
        self.assertEqual(result["hits"][0]["document_id"], detail["document_id"])
        self.assertIn("Stellar research", result["hits"][0]["text"])
        self.assertIn("search_documents", {tool.name for tool in model.tools})
        messages = [message for message in model.inputs[-1] if isinstance(message, ToolMessage)]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].name, "search_documents")
        self.assertEqual(messages[0].tool_call_id, "agent-search")
        self.assertEqual(json.loads(messages[0].content), result)


if __name__ == "__main__":
    unittest.main()
