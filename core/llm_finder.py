"""LLM-first поиск мест подписи без regex (fallback для документов без паттернов).

v1.4.2
"""
import json
import os
import re

import fitz

from core.finder import SignMatch

MODEL = "claude-sonnet-4-6"


def find_signatures_llm(doc, party_name: str, language: str) -> list[SignMatch]:
    """LLM находит места подписи напрямую, без regex-паттернов.

    Используется как fallback когда regex находит 0 мест (новые типы договоров
    без паттернов в parties.json).

    Параметры:
        doc — ParsedDocument
        party_name — название стороны (напр. "Арендатор", "Wykonawca")
        language — 'ru' / 'en' / 'pl'

    Возвращает:
        list[SignMatch] с source="llm_fallback"
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return []

    # Реюз strategic text из pattern_extractor
    doc_text = _get_strategic_text(doc, max_chars=10000)
    if not doc_text.strip():
        return []

    try:
        locations = _call_llm(doc_text, party_name, language)
        if not locations:
            return []

        # Конвертируем locations в SignMatch с bbox
        matches = []
        counter = 0
        pdf_doc = fitz.open(stream=doc.pdf_bytes, filetype="pdf")

        try:
            for loc in locations:
                page_idx = int(loc.get("page", 1)) - 1  # LLM возвращает 1-indexed
                if page_idx < 0 or page_idx >= len(doc.pages):
                    continue

                text_marker = loc.get("text_marker", "")
                if not text_marker:
                    continue

                page = pdf_doc[page_idx]
                rects = _find_marker_bbox(page, text_marker)

                for rect in rects:
                    counter += 1
                    ctx = loc.get("context", text_marker)[:100]
                    matches.append(SignMatch(
                        id=f"llm_{counter:03d}",
                        page=page_idx,
                        bbox=tuple(rect),
                        context=ctx,
                        party=party_name,
                        pattern="llm_fallback",
                        confidence=0.7,  # LLM-fallback имеет среднюю уверенность
                    ))
        finally:
            pdf_doc.close()

        return matches

    except Exception as e:
        print(f"[llm_finder] error: {e}")
        return []


def _get_strategic_text(doc, max_chars: int) -> str:
    """Первая + последняя + страницы с маркерами подписи + остаток бюджета."""
    SIGN_MARKERS = ["подпись", "signature", "podpis", "___", "ФИО", "печать"]
    pages = doc.pages
    n = len(pages)

    priority = [0]
    if n > 1:
        priority.append(n - 1)
    for i, page in enumerate(pages):
        if i in priority:
            continue
        text = (page.text or "").lower()
        if any(m in text for m in SIGN_MARKERS):
            priority.append(i)
    rest = [i for i in range(n) if i not in priority]

    buf = []
    total = 0
    for i in (priority + rest):
        chunk = f"\n--- Страница {i + 1} ---\n{pages[i].text or ''}"
        if total + len(chunk) >= max_chars:
            buf.append(chunk[: max_chars - total])
            break
        buf.append(chunk)
        total += len(chunk)
    return "\n".join(buf)


def _call_llm(doc_text: str, party_name: str, language: str) -> list[dict]:
    """LLM анализирует документ → список мест подписи."""
    from anthropic import Anthropic

    client = Anthropic()
    from core.prompts import get_sign_task_rules
    _task_rules = get_sign_task_rules()

    lang_hint = {"ru": "русском", "en": "английском", "pl": "польском"}.get(language, language)

    prompt = f"""Анализируй договор на {lang_hint} языке. Найди все места где ПОДПИСЫВАЕТ сторона "{party_name}".

Договор (фрагмент):
---
{doc_text}
---

{_task_rules}
3. Для каждого места укажи: номер страницы, точный фрагмент текста (text_marker), контекст вокруг.

ВАЖНО:
- text_marker должен быть ТОЧНОЙ строкой из документа (10-30 символов), включая подчёркивания.
- НЕ обобщай — копируй точный текст как он есть в документе.
- Контекст — 2-3 слова до и после для уникальной идентификации.

Верни ТОЛЬКО JSON-массив без markdown:
[
  {{"page": 1, "text_marker": "Подпись_____________", "context": "Директор: Подпись_____________ /ФИО/"}},
  ...
]
"""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = (resp.content[0].text or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.MULTILINE).strip()
    raw = re.sub(r"```$", "", raw, flags=re.MULTILINE).strip()

    # Ищем JSON-массив
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        raw = m.group(0)

    try:
        locations = json.loads(raw)
        if isinstance(locations, list):
            return locations
    except json.JSONDecodeError:
        pass

    return []


def _find_marker_bbox(page, text_marker: str) -> list:
    """Найти bbox для text_marker на странице.

    Стратегия: прямой поиск → fallback на ключевые слова → линии подчёркиваний.
    """
    # 1. Прямой поиск
    rects = page.search_for(text_marker)
    if rects:
        return rects

    # 2. Извлечь ключевые слова (без подчёркиваний)
    cleaned = re.sub(r"_{2,}", " ", text_marker)
    words = re.findall(r"[\w\u0400-\u04FF]+", cleaned, flags=re.UNICODE)
    if words:
        for w in words:
            if len(w) >= 3:
                found = page.search_for(w)
                if found:
                    return found[:1]

    # 3. Fallback на линии подчёркиваний
    if "___" in text_marker or "__" in text_marker:
        lines = page.search_for("___")
        return lines[:1] if lines else []

    return []
