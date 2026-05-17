"""CRUD для signer_profile.json — данные подписанта (один на продукт).

v1.5
"""
import sys
from datetime import datetime, timezone
from typing import Dict, List

_FILE = "signer_profile.json"

DEFAULTS: Dict = {
    "version": "1.0",
    "company_aliases": [],   # [{"language": "ru", "value": "ООО Ромашка"}, ...]
    "signer_aliases": [],    # [{"language": "ru", "value": "Иванов И.И."}, ...]
    "updated_at": "",
}


def load_signer_profile() -> Dict:
    """Загрузить профиль подписанта. Fallback на пустой профиль."""
    try:
        from core.storage import json_config_exists, read_json
        if json_config_exists(_FILE):
            stored = read_json(_FILE)
            result = dict(DEFAULTS)
            result.update(stored)
            return result
    except Exception as e:
        print(f"[signer_profile] load error: {e}", file=sys.stderr)
    return dict(DEFAULTS)


def save_signer_profile(profile: Dict) -> str:
    """Сохранить профиль с бэкапом. Возвращает имя бэкапа."""
    from core.storage import write_json
    profile = dict(profile)
    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    return write_json(_FILE, profile)


def get_aliases_for_language(language: str) -> Dict[str, List[str]]:
    """Вернуть {company: [...], signer: [...]} алиасов для языка.

    Fallback: если алиасов для языка нет — возвращает все без фильтрации.
    """
    profile = load_signer_profile()
    lang = (language or "").lower()[:2]

    def _filter(key: str) -> List[str]:
        all_aliases = profile.get(key, [])
        by_lang = [a["value"] for a in all_aliases
                   if a.get("language") == lang and a.get("value", "").strip()]
        if by_lang:
            return by_lang
        # fallback — все языки
        return [a["value"] for a in all_aliases if a.get("value", "").strip()]

    return {
        "company": _filter("company_aliases"),
        "signer": _filter("signer_aliases"),
    }
