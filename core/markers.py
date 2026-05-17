"""CRUD для markers.json — универсальные маркеры подписи по языкам.

v1.5
"""
import sys
from typing import Dict

_FILE = "markers.json"

DEFAULTS: Dict = {
    "version": "1.0",
    "languages": {
        "ru": {
            "underline_patterns": ["_{3,}", "\\.{5,}"],
            "marker_words": ["Подпись", "М.П.", "Место подписи", "Подп.", "/Подпись/"],
            "section_anchors": ["раздел", "приложение", "акт", "часть"],
        },
        "en": {
            "underline_patterns": ["_{3,}", "\\.{5,}"],
            "marker_words": ["Signature", "Sign", "/Signature/", "Authorized Signatory"],
            "section_anchors": ["section", "annex", "appendix", "schedule"],
        },
        "pl": {
            "underline_patterns": ["_{3,}", "\\.{5,}"],
            "marker_words": ["Podpis", "Czytelny podpis", "Pieczęć"],
            "section_anchors": ["część", "załącznik", "rozdział"],
        },
    },
}


def load_markers() -> Dict:
    """Загрузить маркеры из хранилища. Fallback на DEFAULTS."""
    try:
        from core.storage import json_config_exists, read_json
        if json_config_exists(_FILE):
            return read_json(_FILE)
    except Exception as e:
        print(f"[markers] load error: {e}", file=sys.stderr)
    return dict(DEFAULTS)


def save_markers(markers: Dict) -> str:
    """Сохранить маркеры с бэкапом. Возвращает имя бэкапа."""
    from core.storage import write_json
    return write_json(_FILE, markers)


def get_markers_for_language(language: str) -> Dict:
    """Вернуть блок маркеров для языка. Возвращает {} если нет."""
    markers = load_markers()
    lang = (language or "").lower()[:2]
    return markers.get("languages", {}).get(lang, {})
