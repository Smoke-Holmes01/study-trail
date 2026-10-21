from pathlib import Path

from docx import Document
from pypdf import PdfReader

from .core import Problem


def decode_text(data):
    if data.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    elif data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encoding = "utf-32"
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    else:
        try:
            return data.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            encoding = "gb18030"
    try:
        return data.decode(encoding), encoding
    except UnicodeDecodeError:
        raise Problem("FILE_PARSE_FAILED", "无法识别文件文字编码", 409)


def extract(path, extension):
    segments = []
    encoding = None
    try:
        if extension == ".pdf":
            pdf = PdfReader(path)
            if pdf.is_encrypted:
                raise Problem("FILE_PARSE_FAILED", "不支持加密 PDF", 409)
            for i, page in enumerate(pdf.pages):
                segments.append((page.extract_text() or "", {"page": i + 1}))
        elif extension == ".docx":
            doc = Document(path)
            # iter_inner_content preserves paragraph/table order.
            from docx.text.paragraph import Paragraph

            for i, block in enumerate(doc.iter_inner_content()):
                text = (
                    block.text
                    if isinstance(block, Paragraph)
                    else "\n".join("\t".join(cell.text for cell in row.cells) for row in block.rows)
                )
                segments.append(
                    (text, {"block": i + 1, "kind": "paragraph" if isinstance(block, Paragraph) else "table"})
                )
        else:
            text, encoding = decode_text(Path(path).read_bytes())
            if "\x00" in text:
                raise Problem("FILE_PARSE_FAILED", "TXT 包含不可读二进制内容", 409)
            segments = [(line, {"line": i + 1}) for i, line in enumerate(text.splitlines())]
    except Problem:
        raise
    except Exception:
        raise Problem("FILE_PARSE_FAILED", "文件损坏或无法提取文字", 409)
    text = ""
    boundaries = []
    for content, locator in segments:
        start = len(text)
        text += content + "\n"
        boundaries.append({"start": start, "end": len(text), **locator})
    if not text.strip():
        raise Problem("NO_READABLE_TEXT", "未提取到文字；扫描件不支持 OCR", 409)
    return text, {"encoding": encoding, "boundaries": boundaries, "warning": "仅提取可读文字，不还原排版。"}


def chunks(text, metadata):
    result = []
    start = 0
    boundaries = metadata["boundaries"]
    while start < len(text):
        end = min(start + 1200, len(text))
        if end < len(text):
            cut = max(
                text.rfind("\n", start + 600, end),
                text.rfind("。", start + 600, end),
                text.rfind(". ", start + 600, end),
            )
            if cut >= start + 600:
                end = cut + 1
        if text[start:end].strip():
            touched = [b for b in boundaries if b["end"] > start and b["start"] < end]
            loc = {"char_start": start, "char_end": end}
            for key in ("page", "block", "line"):
                values = [b[key] for b in touched if key in b]
                if values:
                    loc[key + "_start"] = min(values)
                    loc[key + "_end"] = max(values)
            result.append(
                {
                    "chunk_index": len(result),
                    "content_text": text[start:end],
                    "char_start": start,
                    "char_end": end,
                    "locator": loc,
                }
            )
        if end >= len(text):
            break
        start = max(start + 1, end - 200)
    return result
