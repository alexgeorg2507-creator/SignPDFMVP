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