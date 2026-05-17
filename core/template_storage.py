"""Хранение и управление шаблонами документов (DocumentTemplate).

v1.4.3

Шаблон = набор координат мест подписи + fingerprint для автоматчинга.
Хранится в GCS (прод) или локально (dev) как JSON-файлы.
Путь: gs://signfinder-config/templates/{template_id}.json
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

LOCAL_TEMPLATES_DIR = Path(__file__).parent.parent / "config" / "templates"


def _is_gcs_mode() -> bool:
    return bool(os.environ.get("GCS_BUCKET"))


def _gcs_client():
    from google.cloud import storage
    return storage.Client()


def _templates_prefix() -> str:
    """Префикс пути для шаблонов в GCS."""
    return "templates/"


# ── CRUD ─────────────────────────────────────────────────────────────────────

def list_templates() -> list[dict]:
    """Список всех шаблонов (метаданные: id, name, language, created_at).
    
    Возвращает: [{template_id, name, language, page_count, created_at}, ...]
    """
    if _is_gcs_mode():
        bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
        prefix = _templates_prefix()
        blobs = bucket.list_blobs(prefix=prefix)
        
        templates = []
        for blob in blobs:
            if blob.name.endswith(".json"):
                try:
                    content = blob.download_as_text()
                    data = json.loads(content)
                    templates.append({
                        "template_id": data.get("template_id"),
                        "name": data.get("name"),
                        "language": data.get("language"),
                        "page_count": data.get("fingerprint", {}).get("page_count"),
                        "created_at": data.get("created_at"),
                    })
                except Exception:
                    continue
        return templates
    else:
        LOCAL_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        templates = []
        for path in LOCAL_TEMPLATES_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                templates.append({
                    "template_id": data.get("template_id"),
                    "name": data.get("name"),
                    "language": data.get("language"),
                    "page_count": data.get("fingerprint", {}).get("page_count"),
                    "created_at": data.get("created_at"),
                })
            except Exception:
                continue
        return templates


def read_template(template_id: str) -> dict | None:
    """Загрузить шаблон по ID. Возвращает None если не найден."""
    if _is_gcs_mode():
        bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
        blob = bucket.blob(f"{_templates_prefix()}{template_id}.json")
        if not blob.exists():
            return None
        try:
            return json.loads(blob.download_as_text())
        except Exception:
            return None
    else:
        path = LOCAL_TEMPLATES_DIR / f"{template_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None


def write_template(template: dict) -> str:
    """Сохранить шаблон. Возвращает template_id.
    
    Если template_id отсутствует — генерируется новый UUID.
    Если уже существует — перезаписывается (без версионирования в v1.4.3).
    """
    if "template_id" not in template or not template["template_id"]:
        template["template_id"] = str(uuid.uuid4())
    
    if "created_at" not in template:
        template["created_at"] = datetime.now().isoformat()
    
    template_id = template["template_id"]
    content = json.dumps(template, ensure_ascii=False, indent=2)
    
    if _is_gcs_mode():
        bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
        blob = bucket.blob(f"{_templates_prefix()}{template_id}.json")
        blob.upload_from_string(content, content_type="application/json")
    else:
        LOCAL_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        path = LOCAL_TEMPLATES_DIR / f"{template_id}.json"
        path.write_text(content, encoding="utf-8")
    
    return template_id


def delete_template(template_id: str) -> bool:
    """Удалить шаблон. Возвращает True если удалён, False если не найден."""
    if _is_gcs_mode():
        bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
        blob = bucket.blob(f"{_templates_prefix()}{template_id}.json")
        if not blob.exists():
            return False
        blob.delete()
        return True
    else:
        path = LOCAL_TEMPLATES_DIR / f"{template_id}.json"
        if not path.exists():
            return False
        path.unlink()
        return True
