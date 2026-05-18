"""Извлечение regex-паттернов мест подписи из документа-образца через LLM.

v1.3: новый модуль для страницы "Обучение системы".
"""
import json
import os
import re

MODEL = "claude-sonnet-4-6"


def extract_patterns(
    doc,
    party_name: str,
    language: str,
    refinement: str | None = None,
    previous_result: dict | None = None,
) -> dict:
    """LLM анализирует документ-образец → возвращает regex-паттерны и места подписи.

    Параметры:
        doc           — ParsedDocument
        party_name    — название стороны (как в реестре или новая)
        language      — 'ru' / 'en' / 'pl'
        refinement    — уточнение оператора при повторном запросе
        previous_result — предыдущий ответ для контекста рефайнмента

    Возвращает dict:
        {
            "patterns":       ["regex1", ...],
            "found_locations": [{"page": 1, "line": "...", "context": "..."}, ...],
            "reasoning":      "...",
            "error":          None | "сообщение"
        }
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _error("ANTHROPIC_API_KEY не задан")

    doc_text = _get_doc_text(doc, max_chars=5000)
    if not doc_text.strip():
        return _error("Не удалось извлечь текст из документа")

    try:
        llm_result = _call_llm(doc_text, party_name, language, refinement, previous_result, doc)
        patterns = llm_result.get("patterns", [])
        prompt_used = llm_result.get("prompt", "")
        raw_response = llm_result.get("raw", "")

        if not patterns:
            err = _error("LLM не вернул паттерны")
            err["prompt"] = prompt_used
            err["raw_response"] = raw_response
            return err

        return {
            "patterns": patterns,
            "found_locations": [],
            "reasoning": "",
            "error": None,
            "prompt": prompt_used,
            "raw_response": raw_response,
        }
    except Exception as e:
        return _error(f"LLM error: {e}")


def merge_patterns_into_json(
    json_data: dict,
    party_name: str,
    language: str,
    new_patterns: list[str],
) -> int:
    """Добавляет паттерны в json_data in-place (без дублей).

    Если сторона или языковой блок отсутствуют — создаёт.
    Возвращает количество реально добавленных паттернов.
    """
    parties = json_data.setdefault("parties", {})

    if party_name not in parties:
        parties[party_name] = {
            "display": party_name,
            "languages": {
                language: {"aliases": [], "patterns": []}
            },
            "notes": "",
        }

    party_block = parties[party_name]
    langs = party_block.setdefault("languages", {})

    if language not in langs:
        langs[language] = {"aliases": [], "patterns": []}

    existing = set(langs[language].get("patterns", []))
    added = 0
    for p in new_patterns:
        if p not in existing:
            langs[language]["patterns"].append(p)
            existing.add(p)
            added += 1

    return added


# ── Внутренние ───────────────────────────────────────────────────────────────

def _get_doc_text(doc, max_chars: int) -> str:
    buf = []
    total = 0
    for i, page in enumerate(doc.pages):
        text = page.text or ""
        header = f"\n--- Страница {i + 1} ---\n"
        chunk = header + text
        if total + len(chunk) >= max_chars:
            buf.append(chunk[: max_chars - total])
            break
        buf.append(chunk)
        total += len(chunk)
    return "\n".join(buf)


def _call_llm(
    doc_text: str,
    party_name: str,
    language: str,
    refinement: str | None,
    previous_result: dict | None,
    doc=None,
) -> dict:
    """Возвращает dict: {"patterns": list[str], "prompt": str, "raw": str}."""
    from anthropic import Anthropic

    client = Anthropic()

    refinement_block = ""
    if refinement and previous_result:
        refinement_block = f"""
Предыдущий анализ:
Паттерны: {json.dumps(previous_result.get("patterns", []), ensure_ascii=False)}
Места: {json.dumps(previous_result.get("found_locations", []), ensure_ascii=False)}

Уточнение оператора: "{refinement}"
Скорректируй результат с учётом уточнения. ОБЯЗАТЕЛЬНО пересмотри паттерны под новые места — старые паттерны могут быть неактуальны.
"""
    elif refinement:
        refinement_block = f"""
Дополнительные инструкции оператора: "{refinement}"
Учти их при анализе документа.
"""

    from core.prompts import (
        get_sign_task_rules, get_pattern_quality_rules,
        get_pattern_from_lines_rules, get_pattern_narrow_strategies,
    )
    _task_rules     = get_sign_task_rules()
    _quality_rules  = get_pattern_quality_rules()

    lang_hint = {"ru": "русском", "en": "английском", "pl": "польском"}.get(language, language)

    prompt = f"""Анализируй договор на {lang_hint} языке. Найди все места где ПОДПИСЫВАЕТ сторона "{party_name}".

Договор (фрагмент):
---
{doc_text}
---
{refinement_block}
{_task_rules}

{_quality_rules}

Верни ТОЛЬКО JSON-массив строк без markdown:
["паттерн1", "паттерн2"]"""

    raw = ""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (resp.content[0].text or "").strip()
        raw_clean = re.sub(r"^```(?:json)?", "", raw).strip()
        raw_clean = re.sub(r"```$", "", raw_clean).strip()
        result = json.loads(raw_clean)
        if isinstance(result, list):
            patterns = [p for p in result if isinstance(p, str)]
            return {"patterns": patterns, "prompt": prompt, "raw": raw}
    except Exception as e:
        print(f"[pattern_extractor] _call_llm error: {e}, raw={raw[:200]}")
    return {"patterns": [], "prompt": prompt, "raw": raw}


def _patterns_match_anything(patterns: list[str], doc) -> bool:
    """Проверяет: находят ли паттерны хоть что-то в реальном тексте документа."""
    for p in patterns:
        try:
            rx = re.compile(p, re.IGNORECASE | re.UNICODE)
        except re.error:
            continue
        for page in doc.pages:
            if rx.search(page.text or ""):
                return True
    return False


def _regenerate_from_raw_text(
    client,
    party_name: str,
    locations: list[dict],
    doc,
) -> list[str]:
    """Перегенерирует паттерны с использованием СЫРОГО текста страниц.

    Применяется когда LLM-сгенерированные паттерны не находят ничего в реальном
    тексте — обычно из-за расхождения между "представлением" LLM о структуре
    документа и реальной экстракцией fitz (табы, переносы, неразрывные пробелы).
    """
    # Собираем страницы где LLM указал места (locations 1-indexed)
    page_indices = sorted({
        int(loc["page"]) - 1
        for loc in locations
        if isinstance(loc.get("page"), int) and loc["page"] > 0
    })
    if not page_indices:
        return []

    raw_blocks = []
    for idx in page_indices:
        if 0 <= idx < len(doc.pages):
            text = (doc.pages[idx].text or "")[:2500]
            raw_blocks.append(f"--- Страница {idx + 1} (сырой текст из PDF) ---\n{text}")
    raw_text = "\n\n".join(raw_blocks)

    if not raw_text.strip():
        return []

    locations_str = json.dumps(locations[:5], ensure_ascii=False, indent=2)

    from core.prompts import get_pattern_from_lines_rules, get_pattern_narrow_strategies
    _from_lines_rules = get_pattern_from_lines_rules()
    _narrow_strategies = get_pattern_narrow_strategies()

    prompt = f"""ВАЖНО: предыдущие сгенерированные паттерны не нашли ни одного места в реальном тексте документа.
Нужно создать паттерны на основе ТОЧНОГО сырого текста как он извлекается из PDF.

Места которые нужно найти (по описанию):
{locations_str}

РЕАЛЬНЫЙ СЫРОЙ ТЕКСТ страниц где должны быть эти места (как выдаёт fitz):
{raw_text}

Внимательно изучи РЕАЛЬНУЮ структуру текста:
- Между словом "{party_name}" и линией могут быть: пробелы, табы (\\t), переносы строк (\\n), двоеточия, скобки
- Линия может быть НЕ подчёркиваниями — а пробелами, точками, дефисами
- Слова могут быть разделены неожиданными способами
- Иногда роль и линия вообще на разных строках

Составь regex-паттерны которые ТОЧНО найдут места подписи в показанном выше реальном тексте.
Проверь мысленно: попадает ли каждый твой паттерн на конкретный фрагмент выше.

КРИТИЧЕСКИ для JSON:
- \\s, \\S, \\d, \\w, \\n внутри JSON-строк пишутся с ДВОЙНЫМ слэшем
- Например: "{party_name}[\\s_:]*_{{3,}}"
- Применяется флаг IGNORECASE | UNICODE
- Без якорей ^ и $

Верни ТОЛЬКО JSON-массив паттернов, без markdown и пояснений:
["паттерн1", "паттерн2"]"""

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (resp.content[0].text or "").strip()
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        result = json.loads(raw)
        if isinstance(result, list):
            return [p for p in result if isinstance(p, str)]
    except Exception as e:
        print(f"[pattern_extractor] regenerate_from_raw_text error: {e}")
    return []


def _count_matches(patterns: list[str], doc) -> int:
    """Суммарное количество матчей по всем паттернам и страницам."""
    total = 0
    for p in patterns:
        try:
            rx = re.compile(p, re.IGNORECASE | re.UNICODE)
        except re.error:
            continue
        for page in doc.pages:
            total += len(rx.findall(page.text or ""))
    return total


def _per_pattern_counts(patterns: list[str], doc) -> dict[str, int]:
    """Сколько матчей даёт каждый паттерн (для диагностики LLM)."""
    result = {}
    for p in patterns:
        try:
            rx = re.compile(p, re.IGNORECASE | re.UNICODE)
        except re.error:
            result[p] = -1  # маркер невалидного regex
            continue
        cnt = 0
        for page in doc.pages:
            cnt += len(rx.findall(page.text or ""))
        result[p] = cnt
    return result


def _narrow_patterns(
    client,
    party_name: str,
    patterns: list[str],
    locations: list[dict],
    doc,
    actual_count: int,
) -> list[str]:
    """Третий LLM-вызов: сузить паттерны если они находят слишком много мест.

    Передаём LLM диагностику (count матчей по каждому паттерну) + сырой текст
    страниц + предупреждение "слишком жадные". LLM должен переписать паттерны
    с большим контекстом.
    """
    expected = len(locations)
    by_pattern = _per_pattern_counts(patterns, doc)

    counts_str = "\n".join(
        f'  - "{p}" → {c} матчей' for p, c in by_pattern.items()
    )

    # Сырой текст страниц с локациями (для контекста)
    page_indices = sorted({
        int(loc["page"]) - 1
        for loc in locations
        if isinstance(loc.get("page"), int) and loc["page"] > 0
    })
    raw_blocks = []
    for idx in page_indices:
        if 0 <= idx < len(doc.pages):
            text = (doc.pages[idx].text or "")[:2000]
            raw_blocks.append(f"--- Страница {idx + 1} ---\n{text}")
    raw_text = "\n\n".join(raw_blocks)

    locations_str = json.dumps(locations[:5], ensure_ascii=False, indent=2)

    prompt = f"""ПРОБЛЕМА: паттерны слишком жадные.
Ожидалось мест подписи стороны "{party_name}": {expected}
Фактически находят: {actual_count} мест ({actual_count - expected} лишних)

Диагностика по каждому паттерну:
{counts_str}

Места которые ДОЛЖНЫ быть найдены (целевые):
{locations_str}

Сырой текст страниц:
{raw_text}

Задача: переписать паттерны так чтобы они находили РОВНО {expected} мест.

{_narrow_strategies}

КРИТИЧЕСКИ для JSON: \\s, \\S, \\d, \\w, \\n — с ДВОЙНЫМ слэшем.

Верни ТОЛЬКО JSON-массив без markdown:
["паттерн1", "паттерн2"]"""

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (resp.content[0].text or "").strip()
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        result = json.loads(raw)
        if isinstance(result, list):
            return [p for p in result if isinstance(p, str)]
    except Exception as e:
        print(f"[pattern_extractor] narrow_patterns error: {e}")
    return []


def _error(msg: str) -> dict:
    return {
        "patterns": [],
        "found_locations": [],
        "reasoning": "",
        "error": msg,
    }