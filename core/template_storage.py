"""CRUD для DocumentTemplate в GCS/local.

Хранилище: gs://signfinder-config/templates/{template_id}.json
Локально: config/templates/{template_id}.json
Бэкапы: config/templates/_archive/
"""
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

_TEMPLATES_PREFIX = "templates"
_ARCHIVE_PREFIX = "templates/_archive"


@dataclass
class DocumentTemplate:
    template_id: str
    name: str
    language: str
    created_at: str
    created_by: str                  # "pipeline_auto_1" | "manual_enrichment"

    fingerprint: dict
    anchors: list                    # list[dict] — сериализованные TextAnchor
    synonyms_used: dict              # legal_entity, roles, signer

    usage_stats: dict = field(default_factory=lambda: {
        "times_applied": 0,
        "times_confirmed": 0,
        "times_rejected": 0,
        "last_used": None,
    })


# ── Storage helpers ────────────────────────────────────────────────────────────

def _is_gcs() -> bool:
    return bool(os.environ.get("GCS_BUCKET"))


def _local_templates_dir() -> Path:
    from core.storage import LOCAL_CONFIG_DIR
    p = LOCAL_CONFIG_DIR / _TEMPLATES_PREFIX
    p.mkdir(parents=True, exist_ok=True)
    return p


def _local_archive_dir() -> Path:
    from core.storage import LOCAL_CONFIG_DIR
    p = LOCAL_CONFIG_DIR / "_archive" / _TEMPLATES_PREFIX
    p.mkdir(parents=True, exist_ok=True)
    return p


def _blob_path(template_id: str) -> str:
    return f"{_TEMPLATES_PREFIX}/{template_id}.json"


def _archive_blob_path(template_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{_ARCHIVE_PREFIX}/{template_id}_{ts}.json"


# ── Публичный API ──────────────────────────────────────────────────────────────

def save_template(template: DocumentTemplate) -> str:
    """Сохраняет шаблон в GCS/local. Возвращает template_id."""
    content = json.dumps(asdict(template), ensure_ascii=False, indent=2)
    try:
        if _is_gcs():
            from core.storage import _gcs_client
            bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
            bucket.blob(_blob_path(template.template_id)).upload_from_string(
                content, content_type="application/json"
            )
        else:
            path = _local_templates_dir() / f"{template.template_id}.json"
            path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.error("save_template failed: %s", e)
        sys.stderr.write(f"[template_storage] save_template: {e}\n")
        raise
    return template.template_id


def load_template(template_id: str) -> Optional[DocumentTemplate]:
    """Читает шаблон по ID. None если не найден."""
    try:
        if _is_gcs():
            from core.storage import _gcs_client
            bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
            blob = bucket.blob(_blob_path(template_id))
            if not blob.exists():
                return None
            data = json.loads(blob.download_as_text())
        else:
            path = _local_templates_dir() / f"{template_id}.json"
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
        return DocumentTemplate(**data)
    except Exception as e:
        logger.error("load_template %s failed: %s", template_id, e)
        sys.stderr.write(f"[template_storage] load_template {template_id}: {e}\n")
        return None


def list_templates(language: Optional[str] = None) -> list[DocumentTemplate]:
    """Список всех шаблонов. Опционально с фильтром по языку."""
    templates = []
    try:
        if _is_gcs():
            from core.storage import _gcs_client
            bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
            blobs = bucket.list_blobs(prefix=f"{_TEMPLATES_PREFIX}/")
            for blob in blobs:
                if not blob.name.endswith(".json"):
                    continue
                if "/_archive/" in blob.name:
                    continue
                try:
                    data = json.loads(blob.download_as_text())
                    t = DocumentTemplate(**data)
                    if language is None or t.language == language:
                        templates.append(t)
                except Exception as e:
                    sys.stderr.write(f"[template_storage] list skip {blob.name}: {e}\n")
        else:
            for path in _local_templates_dir().glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    t = DocumentTemplate(**data)
                    if language is None or t.language == language:
                        templates.append(t)
                except Exception as e:
                    sys.stderr.write(f"[template_storage] list skip {path.name}: {e}\n")
    except Exception as e:
        logger.error("list_templates failed: %s", e)
        sys.stderr.write(f"[template_storage] list_templates: {e}\n")
    return templates


def delete_template(template_id: str) -> bool:
    """Удаляет шаблон. Бэкап в _archive/ перед удалением."""
    try:
        if _is_gcs():
            from core.storage import _gcs_client
            bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
            blob = bucket.blob(_blob_path(template_id))
            if not blob.exists():
                return False
            content = blob.download_as_text()
            bucket.blob(_archive_blob_path(template_id)).upload_from_string(
                content, content_type="application/json"
            )
            blob.delete()
        else:
            path = _local_templates_dir() / f"{template_id}.json"
            if not path.exists():
                return False
            archive_path = _local_archive_dir() / f"{template_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
            archive_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            path.unlink()
        return True
    except Exception as e:
        logger.error("delete_template %s failed: %s", template_id, e)
        sys.stderr.write(f"[template_storage] delete_template {template_id}: {e}\n")
        return False


def generate_template_name(language: str, synonyms: Optional[dict] = None) -> str:
    """Имя по схеме: pipelineAuto1_YYYY-MM-DD_HHMM_<lang>[_<тип>]"""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    name = f"pipelineAuto1_{ts}_{language}"
    if synonyms:
        doc_type = synonyms.get("doc_type") or synonyms.get("legal_entity", "")
        if doc_type:
            safe = str(doc_type)[:20].replace(" ", "_")
            name = f"{name}_{safe}"
    return name


def new_template(
    language: str,
    anchors: list,
    fingerprint: dict,
    synonyms_used: Optional[dict] = None,
    created_by: str = "pipeline_auto_1",
) -> DocumentTemplate:
    """Фабрика — создаёт новый DocumentTemplate с uuid и текущим timestamp."""
    synonyms_used = synonyms_used or {}
    return DocumentTemplate(
        template_id=uuid4().hex,
        name=generate_template_name(language, synonyms_used),
        language=language,
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by=created_by,
        fingerprint=fingerprint,
        anchors=anchors,
        synonyms_used=synonyms_used,
    )


# ── v1.8: статистика и расширение якорей ─────────────────────────────────────

def update_usage_stats(
    template_id: str,
    event: str,  # "applied" | "confirmed" | "rejected"
) -> None:
    """Обновляет статистику использования шаблона.

    applied:   times_applied++, last_used = now
    confirmed: times_confirmed++
    rejected:  times_rejected++
    """
    template = load_template(template_id)
    if template is None:
        sys.stderr.write(f"[template_storage] update_usage_stats: {template_id} not found\n")
        return

    stats = template.usage_stats or {
        "times_applied": 0,
        "times_confirmed": 0,
        "times_rejected": 0,
        "last_used": None,
    }

    if event == "applied":
        stats["times_applied"] = stats.get("times_applied", 0) + 1
        stats["last_used"] = datetime.now(timezone.utc).isoformat()
    elif event == "confirmed":
        stats["times_confirmed"] = stats.get("times_confirmed", 0) + 1
    elif event == "rejected":
        stats["times_rejected"] = stats.get("times_rejected", 0) + 1
    else:
        sys.stderr.write(f"[template_storage] update_usage_stats: unknown event '{event}'\n")
        return

    template.usage_stats = stats
    try:
        save_template(template)
    except Exception as e:
        sys.stderr.write(f"[template_storage] update_usage_stats save: {e}\n")


def add_anchors_to_template(
    template_id: str,
    new_anchors: list,
    increment_version: bool = False,
) -> Optional[str]:
    """Добавляет якоря к существующему шаблону.

    increment_version=False: обновляет шаблон на месте.
    increment_version=True:  создаёт новую запись с суффиксом _v2, _v3 и т.д.
    Возвращает template_id (старый или новый).
    """
    import re as _re

    template = load_template(template_id)
    if template is None:
        sys.stderr.write(f"[template_storage] add_anchors_to_template: {template_id} not found\n")
        return None

    def _anchor_id(a):
        return a.get("id") if isinstance(a, dict) else getattr(a, "id", None)

    def _to_dict(a):
        return a if isinstance(a, dict) else asdict(a)

    if not increment_version:
        existing_ids = {_anchor_id(a) for a in template.anchors}
        for anchor in new_anchors:
            d = _to_dict(anchor)
            if _anchor_id(d) not in existing_ids:
                template.anchors.append(d)
                existing_ids.add(_anchor_id(d))
        try:
            save_template(template)
        except Exception as e:
            sys.stderr.write(f"[template_storage] add_anchors_to_template save: {e}\n")
            return None
        return template_id

    # Новая версия: ищем незанятый суффикс _vN
    base_name = _re.sub(r"_v\d+$", "", template.name)
    existing_names = {t.name for t in list_templates(template.language)}
    version = 2
    while f"{base_name}_v{version}" in existing_names:
        version += 1

    all_anchors = list(template.anchors)
    existing_ids = {_anchor_id(a) for a in all_anchors}
    for anchor in new_anchors:
        d = _to_dict(anchor)
        if _anchor_id(d) not in existing_ids:
            all_anchors.append(d)
            existing_ids.add(_anchor_id(d))

    new_tpl = DocumentTemplate(
        template_id=uuid4().hex,
        name=f"{base_name}_v{version}",
        language=template.language,
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by="manual_enrichment",
        fingerprint=template.fingerprint,
        anchors=all_anchors,
        synonyms_used=template.synonyms_used,
    )
    try:
        return save_template(new_tpl)
    except Exception as e:
        sys.stderr.write(f"[template_storage] add_anchors_to_template new version: {e}\n")
        return None
