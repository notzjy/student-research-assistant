"""文档检索与带引用回答的协议，将检索参数、模型草稿和经过校验的出处分开。"""

from typing import Literal

from pydantic import Field

from student_assistant.message_models import StrictInput


class RetrievalOptions(StrictInput):
    """限制检索候选数量和相似度阈值，可选择只查询一个真实文档。"""

    top_k: int = Field(default=5, ge=1, le=10)
    min_score: float = Field(default=0.35, ge=-1.0, le=1.0)
    document_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-f0-9]+$")


class SearchRequest(RetrievalOptions):
    """接收需要语义检索的查询，返回候选片段，不调用文本生成模型。"""

    query: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class AskRequest(RetrievalOptions):
    """接收研究问题，检索之后根据本次真实候选片段生成有出处的回答。"""

    question: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class CitationDraft(StrictInput):
    """模型只选择当前候选的片段ID并逐字引用，文档名和页码由程序补充。"""

    chunk_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class AnswerDraft(StrictInput):
    """表达可回答或证据不足，并允许程序拒绝无引用或错误引用的模型草稿。"""

    status: Literal["answered", "unknown"]
    answer: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    citations: list[CitationDraft] = Field(max_length=10)
