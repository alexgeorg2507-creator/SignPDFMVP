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
        return _call_llm(doc_text, party_name, language, refinement, previous_result, doc)
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

    lang_hint = {"ru": "русском", "en": "английском", "pl": "польском"}.get(language, language)

    prompt = f"""Анализируй договор на {lang_hint} языке. Найди все места где ПОДПИСЫВАЕТ сторона "{party_name}".

Договор (фрагмент):
---
{doc_text}
---
{refinement_block}
Задача:
1. Найди строки/блоки с местами подписи стороны "{party_name}" — НЕ упоминания в тексте договора.
2. Признаки места подписи: подчёркивания (___), слово "Подпись", скобки с ФИО, роль + линия.
3. Для каждого места составь regex-паттерн.

КРИТИЧЕСКИ ВАЖНО для паттернов:
- Ты пишешь паттерны внутри JSON-строк, поэтому обратный слэш НУЖНО удваивать
- ПРАВИЛЬНО:  "Арендатор[\\s_]*_{3,}"   (в JSON \\s = реальный \s в regex)
- НЕПРАВИЛЬНО: "Арендатор[\s_]*_{3,}"   (в JSON \s — невалидный escape, сломает парсинг)
- Аналогично: \\s  \\S  \\d  \\w  \\n — всегда с двойным слэшем внутри JSON-строки
- _{3,} — три и более подчёркивания (слэш не нужен, это литерал)
- Без привязки к конкретным ФИО — только роли и структура строки
- Без якорей ^ и $

СПЕЦИФИЧНОСТЬ ПАТТЕРНОВ — главное требование:
- ОТДЕЛЬНЫЙ узкий паттерн под КАЖДОЕ найденное место, а не один общий
- ЗАПРЕЩЕНЫ жадные конструкции без ограничения длины: НЕ пиши [\\s_]* — пиши [\\s_]{{0,5}}
- ЗАПРЕЩЕНО .* без ограничения — пиши .{{0,30}}
- Добавляй якоря КОНТЕКСТА вокруг линии подписи, не только саму роль:
  - плохо:  "Подпись_{3,}"   (поймает любую подпись на странице)
  - хорошо: "адресу[\\s\\S]{{0,200}}Подпись_{3,}"   (привязка к ближайшему якорю)
- Количество паттернов должно примерно соответствовать количеству найденных мест

Примеры правильных паттернов в JSON:
- "Арендатор:[\\s]{{0,3}}_{3,}[\\s]{{0,5}}_{3,}"   (узкий: двоеточие + 2 линии)
- "_{3,}[\\s\\n]{{0,5}}\\(Арендатор"             (узкий: линия + скобка-аннотация)
- "адресу[\\s\\S]{{0,300}}Подпись_{3,}"          (с контекстом до якоря Подпись)

Верни ТОЛЬКО валидный JSON без markdown и пояснений:
{{
  "patterns": ["паттерн1", "паттерн2"],
  "found_locations": [
    {{"page": 1, "line": "точная строка из документа", "context": "несколько слов вокруг"}}
  ],
  "reasoning": "кратко: что нашёл и почему такие паттерны"
}}

ОБЯЗАТЕЛЬНО: массив "patterns" не должен быть пустым если нашёл места подписи."""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    if not resp.content:
        raise ValueError("LLM вернул пустой ответ (content пустой)")

    raw = (resp.content[0].text or "").strip()
    print(f"[pattern_extractor] raw LLM response ({len(raw)} chars): {raw[:300]}")

    # Убираем markdown-обёртки — поддержка многострочных блоков
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"\s*```$", "", raw, flags=re.DOTALL).strip()

    if not raw:
        raise ValueError("LLM вернул пустой ответ после стрипки markdown")

    # Ищем JSON-объект даже если LLM добавил пояснения до/после
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    data = json.loads(raw)
    data["error"] = None

    # При refinement ВСЕГДА перегенерируем паттерны под актуальные locations.
    # LLM имеет привычку копировать старые patterns не пересматривая их под новые места.
    # Иначе fallback только для пустых patterns.
    if data.get("found_locations"):
        if refinement:
            data["patterns"] = _extract_patterns_from_locations(
                client, party_name, data["found_locations"]
            )
        elif not data.get("patterns"):
            data["patterns"] = _extract_patterns_from_locations(
                client, party_name, data["found_locations"]
            )

    # Самопроверка №1: паттерны находят хоть что-то?
    # Если нет — перегенерация с сырым текстом страниц.
    if doc is not None and data.get("patterns") and data.get("found_locations"):
        if not _patterns_match_anything(data["patterns"], doc):
            regen = _regenerate_from_raw_text(
                client, party_name, data["found_locations"], doc
            )
            if regen:
                data["patterns"] = regen

    # Самопроверка №2: паттерны не слишком жадные?
    # Если суммарный count матчей > 1.5 * count(locations) — паттерны ловят шум, нужно сузить.
    if doc is not None and data.get("patterns") and data.get("found_locations"):
        expected = len(data["found_locations"])
        actual = _count_matches(data["patterns"], doc)
        if expected > 0 and actual > expected * 1.5:
            narrowed = _narrow_patterns(
                client, party_name, data["patterns"], data["found_locations"], doc, actual
            )
            if narrowed:
                # Применяем только если новые паттерны находят разумное количество
                new_count = _count_matches(narrowed, doc)
                if new_count > 0 and new_count <= actual:
                    data["patterns"] = narrowed

    return data


def _extract_patterns_from_locations(client, party_name: str, locations: list[dict]) -> list[str]:
    """Fallback: генерирует паттерны на основе найденных строк."""
    lines_str = "\n".join(
        f'- стр.{loc.get("page","?")}: {loc.get("line", loc.get("context", ""))}'
        for loc in locations[:10]
    )

    prompt = f"""Вот строки из договора — места подписи стороны "{party_name}":
{lines_str}

Составь regex-паттерны для поиска этих строк в тексте.

КРИТИЧЕСКИ: паттерны внутри JSON-строк — обратный слэш УДВАИВАТЬ:
- ПРАВИЛЬНО:  "Арендатор[\\s_]*_{{3,}}"
- НЕПРАВИЛЬНО: "Арендатор[\\s_]*_{{3,}}"  (одинарный \s сломает JSON)

Верни ТОЛЬКО JSON-массив строк без markdown:
["паттерн1", "паттерн2"]"""

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (resp.content[0].text or "").strip()
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        result = json.loads(raw)
        if isinstance(result, list):
            return [p for p in result if isinstance(p, str)]
    except Exception:
        pass
    return []


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

Стратегии сужения:
- Добавить уникальный контекст ПЕРЕД местом подписи (соседнее предложение, маркер блока)
- Заменить жадные [\\s_]* на [\\s_]{{0,5}} или [\\s_]{{1,10}}
- Использовать .{{0,200}} вместо .* для контекстных якорей через несколько строк
- Если два паттерна дублируются по матчам — оставить один более специфичный
- НЕ использовать конкретные ФИО (паттерны должны работать на других документах)

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