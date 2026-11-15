"""解析子进程入口；通过文件交付结果，避免在日志中输出资料内容。"""

import hashlib
import json
import sys
from pathlib import Path

from travel_planner.domain.errors import AppError

MAX_TEXT_CHARS = 2_000_000


def set_resource_limits():
    if sys.platform == "linux":
        import resource

        memory = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))


def paragraphs(text: str) -> list[dict]:
    return [
        {"text": part.strip(), "paragraph": index}
        for index, part in enumerate(text.splitlines(), 1)
        if part.strip()
    ]


def extract(kind: str, raw: bytes) -> list[dict]:
    if kind == "pdf":
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(raw))
        if reader.is_encrypted:
            raise AppError("encrypted_pdf", "PDF 有密码保护，请上传解除保护的文字版本")
        if len(reader.pages) > 200:
            raise AppError("pdf_page_limit", "PDF 最多 200 页，请拆分后上传")
        segments, total = [], 0
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            total += len(text)
            if total > MAX_TEXT_CHARS:
                raise AppError("extracted_text_limit", "提取文字超过 200 万字符，请拆分资料")
            if text.strip():
                segments.append({"text": text, "page": number})
        if not segments:
            raise AppError("pdf_no_text", "PDF 没有可提取文字，请提供文字版本或粘贴正文")
        return segments
    if kind == "text":
        try:
            return paragraphs(raw.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise AppError("text_encoding", "文本需要 UTF-8 编码，请转换编码或粘贴正文") from exc
    from trafilatura import extract as extract_html

    text = extract_html(raw, include_comments=False, include_tables=True)
    if not text:
        raise AppError("web_no_text", "网页没有可提取正文，请粘贴文本或上传文件")
    return paragraphs(text)


def run(kind: str, input_path: Path, output_path: Path, raw_output: Path):
    set_resource_limits()
    try:
        raw = input_path.read_bytes()
        actual_kind = kind
        if kind in {"url", "bookmark"}:
            from travel_planner.knowledge.public_web import fetch_public

            raw, content_type = fetch_public(raw.decode("utf-8"))
            raw_output.write_bytes(raw)
            actual_kind = "text" if content_type == "text/plain" else "html"
        segments = extract(actual_kind, raw)
        if sum(len(segment["text"]) for segment in segments) > MAX_TEXT_CHARS:
            raise AppError("extracted_text_limit", "提取文字超过 200 万字符，请拆分资料")
        if not segments:
            raise AppError("empty_source", "资料没有可检索正文，请补充文本")
        result = {"segments": segments, "sha256": hashlib.sha256(raw).hexdigest()}
    except AppError as exc:
        result = {"error": exc.payload()}
    except MemoryError:
        result = {"error": AppError("parse_memory_limit", "解析超出内存上限，请拆分资料").payload()}
    except Exception:
        result = {
            "error": AppError("parse_failed", "资料解析失败，请粘贴正文或上传文字版本").payload()
        }
    output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    run(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
