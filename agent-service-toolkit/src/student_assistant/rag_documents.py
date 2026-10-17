"""真实文档解析与可复现分块：保留页码和页内字符偏移，不把附件线索当作正文。"""

from io import BytesIO
from pathlib import PureWindowsPath


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PAGES = 200
MAX_TEXT_CHARACTERS = 200_000
SUPPORTED_SUFFIXES = frozenset({".txt", ".md", ".pdf"})


def validate_file(filename: str, data: bytes) -> str:
    """校验安全文件名、类型和原件大小，返回小写扩展名；此步骤不解析正文。"""
    if not isinstance(filename, str) or not filename.strip() or filename != filename.strip():
        raise ValueError("文件名不能为空或带首尾空白")
    path = PureWindowsPath(filename)
    if "/" in filename or "\\" in filename or path.is_absolute() or path.drive:
        raise ValueError("文件名只能是名称，不能包含路径")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("目前只支持 txt、md 和 pdf 文档")
    if not isinstance(data, bytes) or not data:
        raise ValueError("原件必须是非空 bytes")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("原件大小不能超过 10 MB")
    return suffix


def validate_pages(pages: list[dict]) -> None:
    """检查页码、正文类型和总字符预算，允许有空白页，但整份文档必须存在正文。"""
    if not isinstance(pages, list) or not pages or len(pages) > MAX_PAGES:
        raise ValueError("文档页数必须在 1 到 200 之间")
    seen = set()
    total = 0
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("页面必须是包含 page 和 text 的对象")
        number, text = page.get("page"), page.get("text")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number in seen:
            raise ValueError("页码必须是从 1 开始且不重复的正整数")
        if not isinstance(text, str):
            raise ValueError("页面正文必须是字符串")
        seen.add(number)
        total += len(text)
    if total > MAX_TEXT_CHARACTERS:
        raise ValueError("文档正文不能超过 200000 字符")
    if not any(page["text"].strip() for page in pages):
        raise ValueError("未提取到正文；扫描 PDF 本模块尚不支持 OCR")


def parse_document(filename: str, data: bytes) -> list[dict]:
    """解析真实 txt/md/PDF 原件为 page、text 列表；坏文件、加密或无正文时抛 ValueError。"""
    suffix = validate_file(filename, data)
    if suffix in {".txt", ".md"}:
        try:
            pages = [{"page": 1, "text": data.decode("utf-8-sig")}]
        except UnicodeDecodeError:
            raise ValueError("txt 和 md 文档必须使用 UTF-8 编码") from None
    else:
        # 只在处理 PDF 时导入解析器；不会为了解析文档加载 Embedding 模型。
        from pypdf import PdfReader

        try:
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("暂不支持加密 PDF")
            if not 1 <= len(reader.pages) <= MAX_PAGES:
                raise ValueError("PDF 页数必须在 1 到 200 之间")
            pages = []
            total = 0
            for number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                total += len(text)
                if total > MAX_TEXT_CHARACTERS:
                    raise ValueError("文档正文不能超过 200000 字符")
                pages.append({"page": number, "text": text})
        except ValueError:
            raise
        except Exception:
            raise ValueError("PDF 已损坏或无法读取正文") from None
    validate_pages(pages)
    return pages


def chunk_pages(pages: list[dict], chunk_size: int = 400, overlap: int = 80) -> list[dict]:
    """在每一页内做滑窗分块；start/end 是原页面字符串的切片边界，绝不混页。"""
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size < 1:
        raise ValueError("chunk_size 必须是正整数")
    if not isinstance(overlap, int) or isinstance(overlap, bool) or not 0 <= overlap < chunk_size:
        raise ValueError("overlap 必须是非负整数且小于 chunk_size")
    validate_pages(pages)
    chunks = []
    step = chunk_size - overlap
    for page in pages:
        text = page["text"]
        for start in range(0, len(text), step):
            end = min(start + chunk_size, len(text))
            fragment = text[start:end]
            if fragment.strip():
                chunks.append({
                    "page": page["page"], "start": start, "end": end,
                    "text": fragment, "chunk_index": len(chunks),
                })
            if end == len(text):
                break
    return chunks
