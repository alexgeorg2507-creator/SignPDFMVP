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

# Максимальная высота bbox места подписи — отсекает жадные [\s\S]{0,N} паттерны
# которые тянут от синонима до линии через несколько строк.
# Реальное место подписи: 1-2 строки текста ≈ 30-50pt.
MAX_BBOX_HEIGHT_PT = 60.0

# Дедупликация по строке: bbox-ы с центрами по Y ближе этого порога
# и пересечением по X считаются одним местом подписи.
SAME_ROW_Y_TOLERANCE_PT = 6.0


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

def _has_real_signature_line(text: str) -> bool:
    """Матч содержит реальную линию подписи (подчёркивания или точки), не просто слово."""
    return bool(re.search(r"_{3,}|\.{5,}", text))


def _bbox_key(rect, precision: int = 4) -> tuple:
    """Ключ для дедупликации — bbox с округлением."""
    return (round(rect.x0, precision), round(rect.y0, precision),
            round(rect.x1, precision), round(rect.y1, precision))


def _bbox_overlap_ratio(a, b) -> float:
    """Доля пересечения меньшего bbox с большим (0..1)."""
    ix0 = max(a.x0, b.x0); iy0 = max(a.y0, b.y0)
    ix1 = min(a.x1, b.x1); iy1 = min(a.y1, b.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (a.x1 - a.x0) * (a.y1 - a.y0)
    area_b = (b.x1 - b.x0) * (b.y1 - b.y0)
    smaller = min(area_a, area_b)
    return inter / smaller if smaller > 0 else 0.0


def _bbox_contains_signature_line(page, match_rect) -> bool:
    """СТРОГИЙ критерий валидного места подписи: bbox матча геометрически
    пересекается с линией подчёркиваний (___) или точек (....) на странице.

    Убивает ложные срабатывания на одиночных словах (М.П., названия сторон,
    "Адреса и реквизиты") где bbox охватывает только текст без линии.

    Returns True если хоть одна линия пересекается с match_rect.
    """
    line_rects = list(page.search_for("___"))
    line_rects.extend(page.search_for("....."))

    if not line_rects:
        return False

    for line in line_rects:
        # Геометрическое пересечение прямоугольников
        if (line.y0 <= match_rect.y1 and line.y1 >= match_rect.y0 and
                line.x0 <= match_rect.x1 and line.x1 >= match_rect.x0):
            return True
    return False


def _filter_by_dominant_patterns(matches: list[SignMatch], min_pages: int = 2) -> list[SignMatch]:
    """Базовая директива: подписант один → паттерн места подписи единообразен по всему договору.

    Доминирующий паттерн = покрытие ≥ 50% от максимального покрытия среди всех паттернов
    (и не менее min_pages). На каждой странице где есть матч доминирующего паттерна,
    матчи остальных паттернов отбрасываются.
    """
    if not matches:
        return matches

    from collections import defaultdict
    pattern_pages: dict[str, set] = defaultdict(set)
    for m in matches:
        pattern_pages[m.pattern].add(m.page)

    if not pattern_pages:
        return matches

    max_coverage = max(len(pages) for pages in pattern_pages.values())
    threshold = max(min_pages, int(max_coverage * 0.5))

    dominant = {p for p, pages in pattern_pages.items() if len(pages) >= threshold}
    if not dominant:
        return matches

    # Страницы где сработал хотя бы один доминирующий паттерн
    pages_with_dominant: set = set()
    for m in matches:
        if m.pattern in dominant:
            pages_with_dominant.add(m.page)

    filtered = []
    for m in matches:
        if m.page in pages_with_dominant and m.pattern not in dominant:
            continue
        filtered.append(m)
    return filtered


def find_signatures(doc: ParsedDocument, party: dict) -> list[SignMatch]:
    """Поиск мест подписи для заданной стороны."""
    raw_matches: list[SignMatch] = []
    counter = 0

    compiled = []
    for pat in party.get("patterns", []):
        try:
            # Фильтр 0: отсекаем "реверсные" паттерны — начинаются с линии подписи
            # (_{3,}...Наше_имя). Такой паттерн садится bbox-ом на ЧУЖУЮ линию подписи
            # и тянет до нашего имени → ложное срабатывание.
            pat_stripped = re.sub(r'^\(\?:', '', pat)  # убираем (?:
            if pat_stripped.startswith('_') or pat_stripped.startswith('\\.') or pat_stripped.startswith('.'):
                continue
            compiled.append((pat, re.compile(pat, re.IGNORECASE | re.UNICODE)))
        except re.error:
            continue

    # Чужие алиасы — паттерны не должны цеплять обе стороны одновременно.
    # Универсальный признак: matched_text содержит и нашу, и чужую сторону → дроп.
    other_aliases: list[str] = [
        a.strip() for a in party.get("other_aliases", []) if a and len(a.strip()) >= 3
    ]

    pdf_doc = fitz.open(stream=doc.pdf_bytes, filetype="pdf")

    try:
        for page_idx, parsed_page in enumerate(doc.pages):
            text = parsed_page.text
            page = pdf_doc[page_idx]

            # Собираем сырые матчи со всех паттернов
            page_raw: list[SignMatch] = []
            seen_text_spans: set[tuple] = set()  # (start, end) → первый паттерн выиграл

            for pattern_str, regex in compiled:
                for m in regex.finditer(text):
                    matched_text = m.group(0)

                    # Фильтр 1: матч должен содержать реальную линию подписи
                    if not _has_real_signature_line(matched_text):
                        continue

                    # Фильтр 2: одинаковые текстовые позиции — дубль паттернов
                    span_key = (m.start(), m.end())
                    if span_key in seen_text_spans:
                        continue
                    seen_text_spans.add(span_key)

                    # Фильтр 3: чужая сторона в matched_text — паттерн ловит не нас
                    if other_aliases:
                        if any(alias.lower() in matched_text.lower() for alias in other_aliases):
                            continue

                    rects = _find_signature_bbox(page, matched_text)
                    for rect in rects:
                        # Фильтр 4: высота bbox — реальное место подписи компактное.
                        # Отсекает жадные [\s\S]{0,N} паттерны тянущие пол-страницы.
                        if (rect.y1 - rect.y0) > MAX_BBOX_HEIGHT_PT:
                            continue

                        # Фильтр 5: bbox должен СОДЕРЖАТЬ линию подписи (___/....) геометрически.
                        # Универсальный критерий: место подписи = слово-якорь + линия в bbox.
                        # Убивает ложные срабатывания на одиночных словах без линии.
                        if not _bbox_contains_signature_line(page, rect):
                            continue

                        counter += 1
                        start = max(0, m.start() - 40)
                        end = min(len(text), m.end() + 40)
                        ctx = text[start:end].replace("\n", " ").strip()

                        page_raw.append(SignMatch(
                            id=f"sig_{counter:03d}",
                            page=page_idx,
                            bbox=tuple(rect),
                            context=ctx,
                            party=party["name"],
                            pattern=pattern_str,
                        ))

            # Фильтр 6: дедупликация по bbox-overlap (>70%).
            # Для близких bbox с любым уровнем overlap.
            deduped: list[SignMatch] = []
            for candidate in page_raw:
                c_rect = fitz.Rect(candidate.bbox)
                is_dup = False
                for kept in deduped:
                    k_rect = fitz.Rect(kept.bbox)
                    if _bbox_overlap_ratio(c_rect, k_rect) > 0.70:
                        is_dup = True
                        break
                if not is_dup:
                    deduped.append(candidate)

            # Фильтр 7: дедупликация по строке.
            # bbox-ы с близкими Y-центрами + любое X-пересечение = одна строка подписи.
            # Оставляем самый компактный (минимальная площадь).
            def _area(b):
                return (b[2] - b[0]) * (b[3] - b[1])

            def _y_center(b):
                return (b[1] + b[3]) / 2

            def _x_overlap(a, b):
                return min(a[2], b[2]) > max(a[0], b[0])

            row_deduped: list[SignMatch] = []
            for candidate in sorted(deduped, key=lambda m: _area(m.bbox)):
                c_yc = _y_center(candidate.bbox)
                is_dup = False
                for kept in row_deduped:
                    if (abs(c_yc - _y_center(kept.bbox)) <= SAME_ROW_Y_TOLERANCE_PT and
                            _x_overlap(candidate.bbox, kept.bbox)):
                        is_dup = True
                        break
                if not is_dup:
                    row_deduped.append(candidate)

            raw_matches.extend(row_deduped)
    finally:
        pdf_doc.close()

    # Фильтр 8: доминирующий паттерн выигрывает.
    # Если один паттерн = footer-шаблон документа (сработал на 2+ страницах),
    # отбрасываем матчи "слабых" паттернов на тех же страницах.
    raw_matches = _filter_by_dominant_patterns(raw_matches, min_pages=2)

    return raw_matches

def find_signatures_smart(
    doc: ParsedDocument,
    party: dict,
    min_expected: int = 1,
    llm_fallback: bool = False,
) -> tuple[list[SignMatch], str]:
    """find_signatures + source label для совместимости с app.py.

    Returns:
        (matches, source) где source ∈ {"regex", "llm_fallback"}
    """
    matches = find_signatures(doc, party)

    if matches or not llm_fallback:
        return matches, "regex"

    # LLM-fallback: если regex не нашёл — пробуем сгенерировать паттерны на лету
    try:
        from core.pattern_extractor import extract_patterns
        result = extract_patterns(doc, party["name"], getattr(doc, "language", "ru"))
        if result.get("patterns"):
            fallback_party = dict(party)
            existing = fallback_party.get("patterns", [])
            fallback_party["patterns"] = list(dict.fromkeys(
                existing + result["patterns"]
            ))
            matches = find_signatures(doc, fallback_party)
            return matches, "llm_fallback"
    except Exception as e:
        print(f"[finder] llm_fallback error: {e}")

    return [], "regex"
