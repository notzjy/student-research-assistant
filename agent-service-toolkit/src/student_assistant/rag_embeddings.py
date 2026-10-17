"""本地中文Embedding适配器：延迟加载真实ONNX模型，统一文档与查询向量配置。"""

import math
import threading
from pathlib import Path

from student_assistant.bridge import ROOT


class EmbeddingUnavailable(Exception):
    """表示模型下载、加载或本地向量推理未完成，API应报告服务暂不可用。"""


class LocalEmbedder:
    """用CPU上的FastEmbed运行BGE中文模型，不需要向量API密钥或将文档发送给Embedding服务。"""

    model_name = "BAAI/bge-small-zh-v1.5"
    model_id = "BAAI/bge-small-zh-v1.5|query=zh-v1"
    dimension = 512
    query_prefix = "为这个句子生成表示以用于检索相关文章："

    def __init__(self, cache_dir: Path | str | None = None):
        """只记录缓存位置；服务启动和只读查询不触发Embedding模型下载。"""
        self.cache_dir = Path(cache_dir) if cache_dir is not None else ROOT / "local-data" / "embedding_cache"
        self._model = None
        self._lock = threading.RLock()

    def _get_model(self):
        """首次推理时下载并加载内置中文模型，后续调用复用CPU推理会话。"""
        if self._model is None:
            from fastembed import TextEmbedding

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._model = TextEmbedding(
                model_name=self.model_name, cache_dir=str(self.cache_dir), threads=2,
                providers=["CPUExecutionProvider"],
            )
        return self._model

    def _validated(self, vectors) -> list[list[float]]:
        """转为普通浮点列表并检查维度与有限值，避免损坏向量污染持久索引。"""
        result = [[float(value) for value in vector] for vector in vectors]
        if any(len(vector) != self.dimension or not all(math.isfinite(value) for value in vector) for vector in result):
            raise ValueError("Embedding返回的向量维度或数值不合法")
        return result

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """为真实文档片段生成语义向量，文档端不添加查询前缀。"""
        if not texts:
            return []
        try:
            with self._lock:
                result = self._validated(self._get_model().embed(texts, batch_size=16))
                if len(result) != len(texts):
                    raise ValueError("Embedding输出数量与片段数量不一致")
                return result
        except Exception as error:
            raise EmbeddingUnavailable(f"本地Embedding未完成：{type(error).__name__}") from None

    def embed_query(self, query: str) -> list[float]:
        """为中文问题加模型规定的检索前缀，再使用同一个模型生成查询向量。"""
        try:
            with self._lock:
                return self._validated(self._get_model().embed([self.query_prefix + query]))[0]
        except Exception as error:
            raise EmbeddingUnavailable(f"本地Embedding未完成：{type(error).__name__}") from None
