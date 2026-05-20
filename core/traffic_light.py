"""Классификация светофора шаблонов (v1.8).

Загружает/сохраняет конфиг в GCS или локально.
"""
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

_CONFIG_KEY = "traffic_light_config.json"


@dataclass
class TrafficLightConfig:
    green_threshold: float = 0.95
    synonym_match_required: bool = True  # требовать совпадения синонимов для зелёного


def classify(
    score: float,
    synonyms_match: bool,
    has_collision: bool = False,
    config: Optional[TrafficLightConfig] = None,
) -> Literal["green", "yellow"]:
    """Зелёный только если все условия выполнены, иначе жёлтый."""
    cfg = config or TrafficLightConfig()
    if score < cfg.green_threshold:
        return "yellow"
    if cfg.synonym_match_required and not synonyms_match:
        return "yellow"
    if has_collision:
        return "yellow"
    return "green"


def load_config() -> TrafficLightConfig:
    """Читает конфиг из GCS/local. Возвращает дефолт если файла нет."""
    try:
        if _is_gcs():
            from core.storage import _gcs_client
            bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
            blob = bucket.blob(_CONFIG_KEY)
            if not blob.exists():
                return TrafficLightConfig()
            data = json.loads(blob.download_as_text())
        else:
            path = _local_config_path()
            if not path.exists():
                return TrafficLightConfig()
            data = json.loads(path.read_text(encoding="utf-8"))
        return TrafficLightConfig(**data)
    except Exception as e:
        logger.warning("load_config failed, using defaults: %s", e)
        sys.stderr.write(f"[traffic_light] load_config: {e}\n")
        return TrafficLightConfig()


def save_config(config: TrafficLightConfig) -> None:
    """Сохраняет конфиг в GCS/local."""
    content = json.dumps(asdict(config), ensure_ascii=False, indent=2)
    try:
        if _is_gcs():
            from core.storage import _gcs_client
            bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
            bucket.blob(_CONFIG_KEY).upload_from_string(content, content_type="application/json")
        else:
            _local_config_path().write_text(content, encoding="utf-8")
    except Exception as e:
        logger.error("save_config failed: %s", e)
        sys.stderr.write(f"[traffic_light] save_config: {e}\n")
        raise


# ── helpers ───────────────────────────────────────────────────────────────────

def _is_gcs() -> bool:
    return bool(os.environ.get("GCS_BUCKET"))


def _local_config_path() -> Path:
    try:
        from core.storage import LOCAL_CONFIG_DIR
        p = LOCAL_CONFIG_DIR
    except ImportError:
        p = Path("config")
    p.mkdir(parents=True, exist_ok=True)
    return p / _CONFIG_KEY
