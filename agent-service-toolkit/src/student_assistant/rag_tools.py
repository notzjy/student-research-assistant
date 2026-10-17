"""将真实文档检索注册为主Agent工具，问答接口另行执行更严格的引用校验。"""

from langchain_core.tools import tool

from student_assistant.rag_models import SearchRequest


def create_retrieval_tools(search_function) -> dict:
    """为当前服务的主Agent绑定同一个向量索引，只读检索且不修改业务待办。"""

    @tool("search_documents", args_schema=SearchRequest)
    def search_documents(query: str, top_k: int = 5, min_score: float = 0.35, document_id: str | None = None) -> dict:
        """从已上传并建立向量索引的文件检索相关原文片段，返回真实文件名、页码和来源。"""
        try:
            result = search_function(SearchRequest(
                query=query, top_k=top_k, min_score=min_score, document_id=document_id,
            ))
            return {"status": "retrieved", **result}
        except Exception as error:
            return {"status": "error", "error": f"文档检索未完成：{type(error).__name__}"}

    return {search_documents.name: search_documents}
