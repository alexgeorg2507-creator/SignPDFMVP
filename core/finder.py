"""Поиск мест подписи в распарсенном документе.

v1.1:
  - parse_parties_json()  — основной путь (parties.json)
  - parse_parties_md()    — обратная совместимость / редактор corrections
  - find_signatures()     — принимает нормализованный party-dict

v1.3:
  - умный bbox для матчей с подчёркиваниями: якорное слово + ближайшая линия
    (раньше fallback ставил красный бокс только на слово "Подпись", теперь
    bbox охватывает и слово, и реальную линию подписи)
"""
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
    status: str = "candidate"
    correction_applied: str | None = None
    operator_excluded: bool = False


# ── JSON (v1.1) ───────────────────────────────────────────────────────────────

def parse_parties_json(json_data: dict, language: str | None = None) -> list[dict]:
    """Парсит parties.json → [{name, aliases, patterns, notes, display}]."""
    result = []
    lang = (language or "").lower()[:2]

    for party_name, party_data in json_data.get("parties", {}).items():
        langs = party_data.get("languages", {})

        if lang and lang in langs:
            lang_block = langs[lang]
            aliases = lang_block.get("aliases", [])
            patterns = lang_block.get("patterns", [])
        else:
            aliases = []
            patterns = []
            for lb in langs.values():
                aliases.extend(lb.get("aliases", []))
                patterns.extend(lb.get("patterns", []))

        result.append({
            "name": party_name,
            "display": party_data.get("display", party_name),
            "aliases": aliases,
            "patterns": patterns,
            "notes": party_data.get("notes", ""),
        })

    return result


# ── MD (legacy) ──────────────────────────────────────────────────────────────

def parse_parties_md(md_text: str) -> list[dict]:
    """Парсит старый parties.md → [{name, aliases, patterns, notes}]."""
    parties = []
    current = None
    mode = None

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


# ── Bbox helpers (v1.3) ──────────────────────────────────────────────────────

# Два bbox считаются на одной строке если их вертикальные центры
# отличаются не больше чем на это число пунктов.
_SAME_LINE_TOLERANCE_PT = 4.0


def _extract_anchor_words(matched_text: str) -> list[str]:
    """Извлекает значимые слова (без подчёркиваний и пунктуации) из match-текста."""
    cleaned = re.sub(r"_{2,}", " ", matched_text)
    words = re.findall(r"[\w\u0400-\u04FF]+", cleaned, flags=re.UNICODE)
    seen = set()
    result = []
    for w in words:
        if len(w) >= 3 and w.lower() not in seen:
            seen.add(w.lower())
            result.append(w)
    return result


def _on_same_line(rect_a, rect_b) -> bool:
    """Два bbox на одной строке если их Y-центры близки."""
    center_a = (rect_a.y0 + rect_a.y1) / 2
    center_b = (rect_b.y0 + rect_b.y1) / 2
    return abs(center_a - center_b) <= _SAME_LINE_TOLERANCE_PT


def _merge_rects(rect_a, rect_b):
    """Объединяет два прямоугольника в охватывающий."""
    return fitz.Rect(
        min(rect_a.x0, rect_b.x0),
        min(rect_a.y0, rect_b.y0),
        max(rect_a.x1, rect_b.x1),
        max(rect_a.y1, rect_b.y1),
    )


def _find_signature_bbox(page, matched_text: str) -> list:
    """Возвращает список bbox для матча regex'а.

    Стратегия:
      1. Точный search_for(matched_text) — если работает, возвращаем как есть.
      2. Иначе разбиваем матч на якорное слово + линию подчёркиваний:
         - находим bbox якоря (например "Подпись" или "Арендатор")
         - находим bbox линий подчёркиваний на странице
         - объединяем якорь с ближайшей линией на той же строке
      3. Если ничего не получилось — fallback на первое слово.

    Решает кейс: regex матчит "Подпись_________________", но
    page.search_for() по этой строке возвращает пустой результат
    (из-за разницы в whitespace), а старый fallback подсвечивал только
    слово "Подпись" без самой линии.
    """
    # 1. Прямой поиск
    rects = page.search_for(matched_text)
    if rects:
        return rects

    has_underline = "___" in matched_text or "__" in matched_text
    anchor_words = _extract_anchor_words(matched_text)

    # 2a. Только подчёркивания, без слов
    if has_underline and not anchor_words:
        line_rects = page.search_for("___")
        return line_rects[:1] if line_rects else []

    # 2b. Только слова, без линии — старый fallback
    if not has_underline and anchor_words:
        return page.search_for(anchor_words[0])[:1]

    # 2c. И слова, и линия — самый интересный кейс
    if has_underline and anchor_words:
        anchor_rects = []
        for w in anchor_words:
            found = page.search_for(w)
            if found:
                anchor_rects.extend(found)

        line_rects = page.search_for("___")

        if not anchor_rects:
            return line_rects[:1] if line_rects else []

        if not line_rects:
            return anchor_rects[:1]

        # Для каждого якорного bbox — ближайшая линия на той же строке
        seen_keys = set()
        result = []
        for a in anchor_rects:
            same_line_lines = [u for u in line_rects if _on_same_line(a, u)]
            if not same_line_lines:
                continue

            def dist_to_anchor(u):
                if u.x0 >= a.x1:
                    return u.x0 - a.x1
                if u.x1 <= a.x0:
                    return a.x0 - u.x1
                return 0.0

            nearest = min(same_line_lines, key=dist_to_anchor)
            merged = _merge_rects(a, nearest)

            key = (round(merged.x0, 1), round(merged.y0, 1),
                   round(merged.x1, 1), round(merged.y1, 1))
            if key not in seen_keys:
                seen_keys.add(key)
                result.append(merged)

        if result:
            return result

    # 3. Финальный fallback
    if anchor_words:
        return page.search_for(anchor_words[0])[:1]
    return []


# ── Поиск ────────────────────────────────────────────────────────────────────

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
                    rects = _find_signature_bbox(page, matched_text)

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

# ── LLM-fallback (v1.4.2) ────────────────────────────────────────────────────

def find_signatures_smart(
    doc: ParsedDocument,
    party: dict,
    min_expected: int = 1,
    llm_fallback: bool = True,
) -> tuple[list[SignMatch], str]:
    """Умный поиск с LLM-fallback.

    Параметры:
        doc — ParsedDocument
        party — party dict из parse_parties_json()
        min_expected — минимум ожидаемых мест (если regex < min → LLM)
        llm_fallback — включён ли LLM-fallback

    Возвращает:
        (matches, source) где source = "regex" | "llm_fallback"
    """
    # Сначала пробуем regex
    matches = find_signatures(doc, party)

    if len(matches) >= min_expected:
        return matches, "regex"

    # Regex не нашёл достаточно → LLM-fallback
    if llm_fallback:
        from core.llm_finder import find_signatures_llm
        llm_matches = find_signatures_llm(doc, party["name"], doc.language)
        if llm_matches:
            return llm_matches, "llm_fallback"

    # Fallback не помог или выключен — возвращаем что есть
    return matches, "regex"