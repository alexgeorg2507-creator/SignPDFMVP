"""Централизованное хранилище статичных блоков промптов.

v1.4.4

Динамические переменные (doc_text, party_name, lang_hint и т.д.)
остаются в f-string внутри модулей — их нельзя редактировать в UI.

Статичные блоки (правила, инструкции, признаки) — здесь.
Редактируются через pages/4_⚙️_Настройки.py → таб Промпты.
Хранятся в GCS/локально как prompts.json.
"""

_PROMPTS_FILE = "prompts.json"

# ── Дефолты ────────────────────────────────────────────────────────────────────

DEFAULTS = {

    # ── validator.py ───────────────────────────────────────────────────────────
    "validator_rules": (
        "- Подпись в подвале каждой страницы (визирование) — реальное место\n"
        "- Строка вида «Клиент _________ ФИО» — реальное место\n"
        "- Упоминание «Клиент обязуется...» без подчёркиваний — НЕ место для подписи\n"
        "- Линия ___ + скобки с ФИО рядом — всегда реальное место\n"
        "- Слово «Подпись» без линии — НЕ место"
    ),

    # ── pattern_extractor.py + llm_finder.py ───────────────────────────────────
    "sign_task_rules": (
        "1. Найди строки/блоки с местами подписи стороны — НЕ упоминания в тексте.\n"
        "2. Признаки места подписи: подчёркивания (___), слово «Подпись», скобки с ФИО, роль + линия.\n"
        "3. Для каждого места составь regex-паттерн."
    ),

    "pattern_quality_rules": (
        "КРИТИЧЕСКИ ВАЖНО для паттернов:\n"
        "- Ты пишешь паттерны внутри JSON-строк — обратный слэш УДВАИВАТЬ\n"
        "- ПРАВИЛЬНО:  \"Арендатор[\\\\s_]*_{3,}\"\n"
        "- НЕПРАВИЛЬНО: \"Арендатор[\\s_]*_{3,}\"  (одинарный \\s сломает JSON)\n"
        "- ЗАПРЕЩЕНЫ жадные конструкции: НЕ пиши [\\\\s_]* — пиши [\\\\s_]{0,5}\n"
        "- ЗАПРЕЩЕНО .* без ограничения — пиши .{0,30}\n"
        "- Каждое место подписи = ОТДЕЛЬНЫЙ узкий паттерн, не один общий"
    ),

    "pattern_from_lines_rules": (
        "Составь regex-паттерны для поиска этих строк в тексте.\n\n"
        "КРИТИЧЕСКИ: паттерны внутри JSON-строк — обратный слэш УДВАИВАТЬ:\n"
        "- ПРАВИЛЬНО:  \"Арендатор[\\\\s_]*_{3,}\"\n"
        "- НЕПРАВИЛЬНО: \"Арендатор[\\s_]*_{3,}\"\n\n"
        "Верни ТОЛЬКО JSON-массив строк без markdown:\n"
        "[\"паттерн1\", \"паттерн2\"]"
    ),

    "pattern_narrow_strategies": (
        "Стратегии сужения паттернов:\n"
        "- Добавить уникальный контекст ПЕРЕД местом подписи (соседнее предложение, маркер блока)\n"
        "- Заменить жадные [\\\\s_]* на [\\\\s_]{0,5} или [\\\\s_]{1,10}\n"
        "- Использовать .{0,200} вместо .* для контекста\n"
        "- Добавить якоря начала строки ^ или конца $\n"
        "- Использовать lookahead/lookbehind для уникальной идентификации"
    ),

    # ── party_resolver.py ──────────────────────────────────────────────────────
    "party_resolver_rules": (
        "- Подписант и компания могут указывать на разные стороны — приоритет AND-совпадение.\n"
        "- Если совпадение только по одному критерию — допустимо, но снизь confidence.\n"
        "- Если ни одного совпадения в тексте — confidence = 0, party = null.\n"
        "- Поле «party» должно ТОЧНО совпадать с одним из ключей в списке."
    ),

}

# ── Метаданные для UI ──────────────────────────────────────────────────────────
# Описание каждого ключа: где используется, что влияет

PROMPT_META = {
    "validator_rules": {
        "label": "Правила LLM-валидатора",
        "module": "validator.py",
        "effect": "Что LLM считает реальным местом подписи vs ложным срабатыванием regex",
    },
    "sign_task_rules": {
        "label": "Задача поиска мест подписи",
        "module": "pattern_extractor.py · llm_finder.py",
        "effect": "Инструкция LLM — что именно искать и как, общая для обучения и fallback",
    },
    "pattern_quality_rules": {
        "label": "Правила качества regex-паттернов",
        "module": "pattern_extractor.py",
        "effect": "Ограничения на генерацию паттернов: экранирование, жадность, специфичность",
    },
    "pattern_from_lines_rules": {
        "label": "Инструкция паттернов из строк (шаг 2)",
        "module": "pattern_extractor.py",
        "effect": "Как LLM составляет паттерны когда места найдены вручную (страница Обучение)",
    },
    "pattern_narrow_strategies": {
        "label": "Стратегии сужения жадных паттернов",
        "module": "pattern_extractor.py",
        "effect": "Что делать когда паттерны находят слишком много мест (refinement loop)",
    },
    "party_resolver_rules": {
        "label": "Правила определения стороны по ФИО",
        "module": "party_resolver.py",
        "effect": "Как LLM решает на какой стороне договора выступает подписант",
    },
}


# ── Загрузка / сохранение ──────────────────────────────────────────────────────

def load_prompts() -> dict:
    """Загрузить промпты из GCS/локально. Fallback на DEFAULTS."""
    try:
        from core.storage import json_config_exists, read_json
        if json_config_exists(_PROMPTS_FILE):
            stored = read_json(_PROMPTS_FILE)
            merged = dict(DEFAULTS)
            merged.update(stored)
            return merged
    except Exception:
        pass
    return dict(DEFAULTS)


def save_prompts(prompts: dict) -> None:
    """Сохранить промпты в GCS/локально."""
    from core.storage import write_json
    write_json(_PROMPTS_FILE, prompts)


# ── Публичное API ──────────────────────────────────────────────────────────────

def get(key: str) -> str:
    """Получить промпт-блок по ключу."""
    return load_prompts().get(key, DEFAULTS.get(key, ""))


def get_validator_rules() -> str:
    return get("validator_rules")


def get_sign_task_rules() -> str:
    return get("sign_task_rules")


def get_pattern_quality_rules() -> str:
    return get("pattern_quality_rules")


def get_pattern_from_lines_rules() -> str:
    return get("pattern_from_lines_rules")


def get_pattern_narrow_strategies() -> str:
    return get("pattern_narrow_strategies")


def get_party_resolver_rules() -> str:
    return get("party_resolver_rules")


# ── v1.5: шаблоны для автоматического пайплайна ────────────────────────────────
# Не редактируются через UI — структурированные промпты с JSON-выводом.
# Форматируются через format_find_our_side() / format_generate_regex().

_PROMPT_FIND_OUR_SIDE = """Ты — юридический аналитик. Анализируй ШАПКУ договора и найди НАШУ СТОРОНУ.

Шапка договора:
{header_text}

Язык договора: {language}

Наши данные:
- Компания (алиасы): {company_aliases}
- Подписант (алиасы): {signer_aliases}

Универсальные маркеры подписи: {markers}

Задача:
1. Найди ВСЕ стороны договора в шапке (обычно две стороны)
2. Для каждой стороны определи: юрлицо, роль (Арендатор, Исполнитель, Заказчик и т.п.), ФИО подписанта
3. Определи КАКАЯ из сторон — НАША по совпадению с нашими алиасами (компания ИЛИ ФИО, фамилия первичнее)
4. Верни синонимы НАШЕЙ стороны как они написаны в этом договоре

Правила:
- Если найдено несколько потенциальных совпадений — снизь confidence
- Фамилия подписанта важнее имени и инициалов
- Если ни одного совпадения — our_side_index = null

Верни ТОЛЬКО JSON без markdown:
{{
  "all_parties": [
    {{"legal_entity": "...", "role": "...", "signer": "..."}},
    {{"legal_entity": "...", "role": "...", "signer": "..."}}
  ],
  "our_side_index": 0,
  "our_side_synonyms": {{
    "legal_entity": "...",
    "roles": ["...", "..."],
    "signer": "..."
  }},
  "confidence": 0.9,
  "match_reason": "company match | signer match | both | none",
  "evidence": "цитата из шапки где упомянута наша сторона"
}}"""


_PROMPT_GENERATE_REGEX = """Ты — инженер по регулярным выражениям. Создай regex-паттерны для поиска мест подписи.

Сторона, для которой ищем места подписи:
- Юрлицо: {legal_entity}
- Роли: {roles}
- Подписант: {signer}

Маркеры подписи для языка {language}:
- Подчёркивания (паттерны): {underline_patterns}
- Слова-маркеры: {marker_words}

Стратегические фрагменты документа:
{strategic_fragments}

Правила:
1. Паттерны должны ловить СТРУКТУРУ "синоним стороны + маркер места подписи", НЕ конкретные ФИО
2. Используй ВСЕ типы синонимов (роли, юрлицо, подписант — каждый отдельно)
3. Используй маркеры из переданного списка
4. НЕ создавай паттерны совпадающие со второй стороной
5. Приоритетные зоны: футер страниц, конец разделов, конец договора, приложения
6. Если в фрагментах виден явный паттерн (например "{role} _____") — обязательно включи

КРИТИЧЕСКИ — паттерны внутри JSON-строк: обратный слэш УДВАИВАТЬ
- ПРАВИЛЬНО:  "Арендатор[\\\\s_]{0,5}_{3,}"
- НЕПРАВИЛЬНО: "Арендатор[\\s_]*_{3,}"
- ЗАПРЕЩЕНЫ жадные .* — только .{0,50}

Верни ТОЛЬКО JSON без markdown:
{
  "patterns": [
    {"pattern": "...", "reason": "ловит подпись в футере страницы"},
    {"pattern": "...", "reason": "ловит подпись в конце раздела"}
  ]
}"""


def format_find_our_side(
    header_text: str,
    language: str,
    company_aliases: list,
    signer_aliases: list,
    markers: dict,
) -> str:
    """Форматировать промпт определения нашей стороны."""
    import json as _json
    return _PROMPT_FIND_OUR_SIDE.format(
        header_text=header_text,
        language=language,
        company_aliases=", ".join(company_aliases) if company_aliases else "(не указано)",
        signer_aliases=", ".join(signer_aliases) if signer_aliases else "(не указано)",
        markers=_json.dumps(markers, ensure_ascii=False),
    )


def format_generate_regex(
    legal_entity: str,
    roles: list,
    signer: str,
    language: str,
    markers_block: dict,
    strategic_fragments: str,
) -> str:
    """Форматировать промпт генерации regex-паттернов.

    Использует str.replace вместо .format() — промпт содержит {} в regex-примерах.
    """
    substitutions = {
        "{legal_entity}": legal_entity or "(не определено)",
        "{roles}": ", ".join(roles) if roles else "(не определено)",
        "{signer}": signer or "(не определено)",
        "{language}": language,
        "{underline_patterns}": ", ".join(markers_block.get("underline_patterns", [])),
        "{marker_words}": ", ".join(markers_block.get("marker_words", [])),
        "{strategic_fragments}": strategic_fragments,
    }
    result = _PROMPT_GENERATE_REGEX
    for placeholder, value in substitutions.items():
        result = result.replace(placeholder, value)
    return result
