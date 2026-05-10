"""Парсинг PDF и DOCX в структурированный вид."""
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


@dataclass
class Word:
    text: str
    bbox: tuple  # (x0, y0, x1, y1) в пунктах


@dataclass
class ParsedPage:
    page_num: int  # 0-indexed
    text: str
    words: list = field(default_factory=list)


@dataclass
class ParsedDocument:
    filename: str
    language: str
    pages: list = field(default_factory=list)
    pdf_bytes: bytes = b""


def docx_to_pdf(docx_bytes: bytes) -> bytes:
    """Конвертация DOCX в PDF через LibreOffice headless."""
    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = Path(tmpdir) / "input.docx"
        docx_path.write_bytes(docx_bytes)
        subprocess.run(
            [
                "soffice", "--headless", "--convert-to", "pdf",
                "--outdir", tmpdir, str(docx_path),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        pdf_path = Path(tmpdir) / "input.pdf"
        if not pdf_path.exists():
            raise RuntimeError("LibreOffice не создал PDF из DOCX")
        return pdf_path.read_bytes()


def parse_pdf_bytes(pdf_bytes: bytes, filename: str) -> ParsedDocument:
    """Парсинг PDF — текст и слова с координатами."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    full_text_parts = []

    for page_num, page in enumerate(doc):
        text = page.get_text()
        full_text_parts.append(text)

        words_raw = page.get_text("words")
        words = [Word(text=w[4], bbox=(w[0], w[1], w[2], w[3])) for w in words_raw]

        pages.append(ParsedPage(page_num=page_num, text=text, words=words))

    doc.close()

    full_text = "\n".join(full_text_parts)[:5000]
    try:
        from langdetect import detect
        language = detect(full_text) if full_text.strip() else "unknown"
    except Exception:
        language = "unknown"

    return ParsedDocument(
        filename=filename,
        language=language,
        pages=pages,
        pdf_bytes=pdf_bytes,
    )


def parse_document(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Универсальный парсер — PDF или DOCX по расширению."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return parse_pdf_bytes(file_bytes, filename)
    elif ext == ".docx":
        pdf_bytes = docx_to_pdf(file_bytes)
        return parse_pdf_bytes(pdf_bytes, filename)
    else:
        raise ValueError(f"Неподдерживаемый формат: {ext}")
