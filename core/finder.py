"""Поиск мест подписи в распарсенном документе по паттернам из parties.md."""
import re
from dataclasses import dataclass

import fitz

from core.parser import ParsedDocument


@dataclass
class SignMatch:
    id: str
    page: int               # 0-indexed
    bbox: tuple             # (x0, y0, x1, y1) в пунктах
    context: str            # текст вокруг найденного места
    party: str              # имя стороны
    pattern: str            # какой паттерн сработал
    confidence: float = 0.0
    status: str = "candidate"  # candidate / confirmed / decorative / already_signed
    correction_applied: str | None = None
    operator_excluded: bool = False


def parse_parties_md(md_text: str) -> list[dict]:
    """Парсит parties.md → [{name, aliases, patterns, notes}]."""
    parties = []
    current = None
    mode = None  # 'aliases' / 'patterns' / None

    for raw in md_text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("## СТОРОНА:"):
            if current:
                parties.append(current)
            name = stripped.replace("## СТОРОНА:", "").strip()
            current = {"name": name, "aliases": [], "patterns": [], "notes": ""}
            mode = None
        elif stripped == "aliases:":
            mode = "aliases"
        elif stripped == "sign_patterns:":
            mode = "patterns"
        elif stripped.startswith("notes:"):
            mode = None
            note = stripped[len("notes:"):].strip().strip('"').strip("'")
            if current:
                current["notes"] = note
        elif stripped.startswith("-") and mode and current:
            value = stripped[1:].strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            current[mode].append(value)
        elif stripped == "---":
            if current:
                parties.append(current)
                current = None
            mode = None

    if current:
        parties.append(current)
    return parties


def find_signatures(doc: ParsedDocument, party: dict) -> list[SignMatch]:
    """Поиск мест подписи для заданной стороны."""
    matches = []
    counter = 0

    compiled = []
    for pat in party.get("patterns", []):
        try:
            compiled.append((pat, re.compile(pat, re.IGNORECASE | re.UNICODE)))
        except re.error:
            continue

    pdf_doc = fitz.open(stream=doc.pdf_bytes, filetype="pdf")

    try:
        for page_idx, parsed_page in enumerate(doc.pages):
            text = parsed_page.text
            page = pdf_doc[page_idx]

            for pattern_str, regex in compiled:
                for m in regex.finditer(text):
                    matched_text = m.group(0)

                    # bbox через fitz.search_for — ищет визуальное расположение
                    rects = page.search_for(matched_text)

                    if not rects:
                        # fallback: попробуем найти первое слово из matched
                        first_word = matched_text.split()[0] if matched_text.split() else matched_text
                        rects = page.search_for(first_word)[:1]

                    for rect in rects:
                        counter += 1
                        start = max(0, m.start() - 40)
                        end = min(len(text), m.end() + 40)
                        ctx = text[start:end].replace("\n", " ").strip()

                        matches.append(SignMatch(
                            id=f"sig_{counter:03d}",
                            page=page_idx,
                            bbox=tuple(rect),
                            context=ctx,
                            party=party["name"],
                            pattern=pattern_str,
                        ))
    finally:
        pdf_doc.close()

    return matches
