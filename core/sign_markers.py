"""Централизованное хранилище маркеров мест подписи и статичных промпт-блоков.

v1.4.4

Все модули импортируют маркеры отсюда:
    from core.sign_markers import get_markers_re, get_settings

Хранится в GCS/локально как config/settings.json.
Редактируется через UI: pages/4_⚙️_Настройки.py
"""
import json
import os
import re
from pathlib import Path

_SETTINGS_FILE = "settings.json"

# ── Дефолтные значения ────────────────────────────────────────────────────────

DEFAULTS = {
    "sign_markers": [
        "_{3,}",
        "подпис",
        "signature",
        "sign here",
        "czytelny",
        "печать",
        "stamp",
        "paraf",
        "Unterschrift",
        "подпись",
        "место печати",
        "м\\.п\\.",
    ],
    "prompt_validator_rules": (
        "- Подпись в подвале каждой страницы (визирование) — реальное место\n"
        "- Строка вида \"Клиент _________ ФИО\" — реальное место\n"
        "- Упоминание \"Клиент обязуется...\" без подчёркиваний — НЕ место для подписи\n"
        "- Линия ___ + скобки с ФИО рядом — всегда реальное место\n"
        "- Слово «Подпись» без линии — НЕ место"
    ),
    "prompt_extractor_signs": (
        "Признаки места подписи: подчёркивания (___), слово \"Подпись\", "
        "скобки с ФИО, роль + линия."
    ),
    "prompt_extractor_rules": (
        "СПЕЦИФИЧНОСТЬ ПАТТЕРНОВ — главное требование:\n"
        "- ОТДЕЛЬНЫЙ узкий паттерн под КАЖДОЕ найденное место, а не один общий\n"
        "- ЗАПРЕЩЕНЫ жадные конструкции без ограничения длины: НЕ пиши [\\s_]* — пиши [\\s_]{0,5}\n"
        "- ЗАПРЕЩЕНО .* без ограничения — пиши .{0,30}\n"
        "- Добавляй якоря КОНТЕКСТА вокруг линии подписи, не только саму роль"
    ),
}


# ── Загрузка/сохранение ───────────────────────────────────────────────────────

def load_settings() -> dict:
    """Загрузить настройки из GCS/локально. Fallback на DEFAULTS."""
    try:
        from core.storage import json_config_exists, read_json
        if json_config_exists(_SETTINGS_FILE):
            stored = read_json(_SETTINGS_FILE)
            # Мержим с дефолтами — новые ключи появятся автоматически
            merged = dict(DEFAULTS)
            merged.update(stored)
            return merged
    except Exception:
        pass
    return dict(DEFAULTS)


def save_settings(settings: dict) -> None:
    """Сохранить настройки в GCS/локально."""
    from core.storage import write_json
    write_json(_SETTINGS_FILE, settings)


# ── Публичное API ─────────────────────────────────────────────────────────────

def get_settings() -> dict:
    """Получить актуальные настройки (с кешем на время сессии)."""
    return load_settings()


def get_markers_re() -> re.Pattern:
    """Скомпилированный regex из списка маркеров.

    Использование:
        from core.sign_markers import get_markers_re
        if get_markers_re().search(page_text): ...
    """
    settings = load_settings()
    markers = settings.get("sign_markers", DEFAULTS["sign_markers"])
    pattern = "|".join(markers)
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


def get_markers_list() -> list[str]:
    """Список маркеров как строки."""
    return load_settings().get("sign_markers", list(DEFAULTS["sign_markers"]))


def get_prompt_validator_rules() -> str:
    """Статичные правила для промпта validator.py."""
    return load_settings().get(
        "prompt_validator_rules", DEFAULTS["prompt_validator_rules"]
    )


def get_prompt_extractor_signs() -> str:
    """Признаки места подписи для промпта pattern_extractor.py."""
    return load_settings().get(
        "prompt_extractor_signs", DEFAULTS["prompt_extractor_signs"]
    )


def get_prompt_extractor_rules() -> str:
    """Правила специфичности паттернов для промпта pattern_extractor.py."""
    return load_settings().get(
        "prompt_extractor_rules", DEFAULTS["prompt_extractor_rules"]
    )
