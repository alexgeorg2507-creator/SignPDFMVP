"""Чтение и запись конфигов parties.md / corrections.md.

Автоматически переключается между локальным режимом (для разработки)
и GCS (для прода) по наличию env var GCS_BUCKET.
"""
import os
from datetime import datetime
from pathlib import Path

LOCAL_CONFIG_DIR = Path(__file__).parent.parent / "config"


def _is_gcs_mode() -> bool:
    return bool(os.environ.get("GCS_BUCKET"))


def _gcs_client():
    from google.cloud import storage
    return storage.Client()


def read_md(filename: str) -> str:
    """Прочитать MD файл из GCS или локально."""
    if _is_gcs_mode():
        bucket_name = os.environ["GCS_BUCKET"]
        bucket = _gcs_client().bucket(bucket_name)
        blob = bucket.blob(filename)
        return blob.download_as_text()
    else:
        path = LOCAL_CONFIG_DIR / filename
        return path.read_text(encoding="utf-8")


def write_md(filename: str, content: str) -> str:
    """Записать MD файл, предварительно сохранив бэкап.

    Возвращает имя бэкап-файла.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{Path(filename).stem}_{timestamp}.md"

    if _is_gcs_mode():
        bucket_name = os.environ["GCS_BUCKET"]
        bucket = _gcs_client().bucket(bucket_name)
        # бэкап текущей версии
        current = bucket.blob(filename)
        if current.exists():
            current_text = current.download_as_text()
            bucket.blob(f"backups/{backup_name}").upload_from_string(current_text)
        # записать новый
        bucket.blob(filename).upload_from_string(content)
    else:
        path = LOCAL_CONFIG_DIR / filename
        if path.exists():
            backup_dir = LOCAL_CONFIG_DIR / "backups"
            backup_dir.mkdir(exist_ok=True)
            (backup_dir / backup_name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(content, encoding="utf-8")

    return backup_name
