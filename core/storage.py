"""Чтение и запись конфигов.

Режимы:
  - GCS (прод): env var GCS_BUCKET задан
  - local (dev): config/ рядом с проектом

v1.1: добавлена поддержка JSON (parties.json).
v1.3: добавлено хранение PNG подписи (signature.png).
"""
import json
import os
from datetime import datetime
from pathlib import Path

LOCAL_CONFIG_DIR = Path(__file__).parent.parent / "config"

_SIGNATURE_BLOB = "signature.png"


def _is_gcs_mode() -> bool:
    return bool(os.environ.get("GCS_BUCKET"))


def _gcs_client():
    from google.cloud import storage
    return storage.Client()


# ── MD (corrections.md) ───────────────────────────────────────────────────────

def read_md(filename: str) -> str:
    if _is_gcs_mode():
        bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
        return bucket.blob(filename).download_as_text()
    return (LOCAL_CONFIG_DIR / filename).read_text(encoding="utf-8")


def write_md(filename: str, content: str) -> str:
    backup_name = _backup_name(filename, "md")
    if _is_gcs_mode():
        bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
        blob = bucket.blob(filename)
        if blob.exists():
            bucket.blob(f"backups/{backup_name}").upload_from_string(blob.download_as_text())
        blob.upload_from_string(content)
    else:
        path = LOCAL_CONFIG_DIR / filename
        _local_backup(path, backup_name)
        path.write_text(content, encoding="utf-8")
    return backup_name


# ── JSON (parties.json) ───────────────────────────────────────────────────────

def read_json(filename: str) -> dict:
    if _is_gcs_mode():
        bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
        text = bucket.blob(filename).download_as_text()
    else:
        text = (LOCAL_CONFIG_DIR / filename).read_text(encoding="utf-8")
    return json.loads(text)


def write_json(filename: str, data: dict) -> str:
    backup_name = _backup_name(filename, "json")
    content = json.dumps(data, ensure_ascii=False, indent=2)
    if _is_gcs_mode():
        bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
        blob = bucket.blob(filename)
        if blob.exists():
            bucket.blob(f"backups/{backup_name}").upload_from_string(blob.download_as_text())
        blob.upload_from_string(content, content_type="application/json")
    else:
        path = LOCAL_CONFIG_DIR / filename
        _local_backup(path, backup_name)
        path.write_text(content, encoding="utf-8")
    return backup_name


# ── Подпись PNG (v1.3) ────────────────────────────────────────────────────────

def read_signature() -> bytes | None:
    """Загрузить сохранённую подпись PNG. Возвращает bytes или None если не сохранена."""
    try:
        if _is_gcs_mode():
            bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
            blob = bucket.blob(_SIGNATURE_BLOB)
            if blob.exists():
                return blob.download_as_bytes()
            return None
        path = LOCAL_CONFIG_DIR / _SIGNATURE_BLOB
        if path.exists():
            return path.read_bytes()
        return None
    except Exception:
        return None


def write_signature(png_bytes: bytes) -> None:
    """Сохранить PNG подписи. Перезаписывает предыдущую (без бэкапа — подпись меняется редко)."""
    if _is_gcs_mode():
        bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
        bucket.blob(_SIGNATURE_BLOB).upload_from_string(png_bytes, content_type="image/png")
    else:
        LOCAL_CONFIG_DIR.mkdir(exist_ok=True)
        (LOCAL_CONFIG_DIR / _SIGNATURE_BLOB).write_bytes(png_bytes)


def delete_signature() -> None:
    """Удалить сохранённую подпись."""
    try:
        if _is_gcs_mode():
            bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
            blob = bucket.blob(_SIGNATURE_BLOB)
            if blob.exists():
                blob.delete()
        else:
            path = LOCAL_CONFIG_DIR / _SIGNATURE_BLOB
            if path.exists():
                path.unlink()
    except Exception:
        pass


# ── Миграция md → json ────────────────────────────────────────────────────────

def json_config_exists(filename: str = "parties.json") -> bool:
    if _is_gcs_mode():
        bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
        return bucket.blob(filename).exists()
    return (LOCAL_CONFIG_DIR / filename).exists()


# ── Вспомогательные ───────────────────────────────────────────────────────────

def _backup_name(filename: str, ext: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(filename).stem
    return f"{stem}_{ts}.{ext}"


def _local_backup(path: Path, backup_name: str) -> None:
    if path.exists():
        backup_dir = LOCAL_CONFIG_DIR / "backups"
        backup_dir.mkdir(exist_ok=True)
        (backup_dir / backup_name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")