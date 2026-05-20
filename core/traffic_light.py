"""Светофор шаблонов — классификация green/yellow по score и синонимам.

v1.8: новый модуль.
Конфиг хранится в gs://signfinder-config/traffic_light_config.json (или local).
"""
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from typing import Literal, Optional

logger = logging.getLogger(__name__)

_CONFIG_BLOB = "traffic_light_config.json"
_CONFIG_LOCAL = "config/traffic_light_config.json"


@dataclass
class TrafficLightConfig:
    green_threshold: float = 0.95
    synonym_match_required: bool = True
    collision_delta: float = 0.05  # разница score < delta → коллизия


def classify(
    score: float,
    synonyms_match: bool,
    has_collision: bool = False,
    config: Optional[TrafficLightConfig] = None,
) -> Literal["green", "yellow"]:
    """Классифицирует по светофору.

    Зелёный требует ВСЕ условия:
      - score >= green_threshold
      - synonyms_match (если synonym_match_required)
      - has_collision == False
    Иначе → жёлтый.
    """
    if config is None:
        config = TrafficLightConfig()

    if score < config.green_threshold:
        return "yellow"
    if config.synonym_match_required and not synonyms_match:
        return "yellow"
    if has_collision:
        return "yellow"
    return "green"


def load_config() -> TrafficLightConfig:
    """Загружает конфиг из GCS/local. При отсутствии — дефолт."""
    try:
        if _is_gcs():
            from core.storage import _gcs_client
            bucket = _gcs_client().bucket(os.environ["GCS_BUCKET"])
            blob = bucket.blob(_CONFIG_BLOB)
            if blob.exists():
                data = json.loads(blob.download_as_text())
                return TrafficLightConfig(**data)
        else:
            from pathlib import Path
            path = Path(_CONFIG_LOCAL)
            if path.exists():
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
            bucket.blob(_CONFIG_BLOB).upload_from_string(
                content, content_type="application/json"
            )
        else:
            from pathlib import Path
            path = Path(_CONFIG_LOCAL)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.error("save_config failed: %s", e)
        sys.stderr.write(f"[traffic_light] save_config: {e}\n")
        raise


def _is_gcs() -> bool:
    return bool(os.environ.get("GCS_BUCKET"))
