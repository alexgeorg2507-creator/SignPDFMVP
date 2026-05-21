"""Сборка полного диагностического JSON для страницы Авто-подписания.

Назначение: собрать ВСЁ что нужно для диагностики регрессии v1.6→v1.7→v1.8
(пропуск мест подписи после внедрения текстовых якорей и светофора шаблонов).

Что входит в экспорт:
  - doc_info — имя, страницы, язык
  - fingerprint — page_count, simhash, section_titles, chars_per_page
  - matcher — traffic_light, best_match, все кандидаты со score_breakdown
  - applied_template — если шаблон был применён, грузим его целиком
  - pipeline — step3 (our_side+prompt+raw), step4 (patterns+prompt+raw),
               step5 (matches с полным контекстом)
  - matches_to_anchors_mapping — критично для диагностики regex_match_to_anchor:
    видно какой исходный SignMatch превратился в какой TextAnchor (или потерялся)
  - all_anchors — все якоря сессии с полными полями (включая enabled/disabled)
  - parties_json_for_language — секция реестра по текущему языку
                                (чтобы сравнить regex из реестра с тем что в шаблоне)

Модуль defensive: каждая секция в try/except. Если что-то сломалось,
в секцию пишется {"error": "..."}, остальное собирается.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any


def _safe(obj: Any) -> Any:
    """Defensive-сериализатор: dataclass → dict, объекты → __dict__, иначе str()."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _safe(v) for k, v in obj.items()}
    if is_dataclass(obj):
        try:
            return _safe(asdict(obj))
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return _safe({k: v for k, v in vars(obj).items() if not k.startswith("_")})
        except Exception:
            pass
    try:
        return str(obj)
    except Exception:
        return f"<unserializable {type(obj).__name__}>"


def _section(fn):
    """Декоратор для секций — оборачивает в try/except и возвращает {"error":...} при падении."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
    return wrapper


@_section
def _build_doc_info(ss: dict) -> dict:
    doc = ss.get("auto_doc")
    return {
        "filename": ss.get("auto_doc_name", ""),
        "pages": len(doc.pages) if doc else 0,
        "language": ss.get("auto_language", ""),
        "pdf_bytes_size": len(doc.pdf_bytes) if doc and hasattr(doc, "pdf_bytes") else None,
    }


@_section
def _build_fingerprint(ss: dict) -> dict:
    fp = ss.get("fingerprint")
    if fp is None:
        return {"present": False}
    safe_fp = _safe(fp)
    if isinstance(safe_fp, dict):
        return {"present": True, **safe_fp}
    return {"present": True, "value": safe_fp}


@_section
def _build_matcher(ss: dict) -> dict:
    mr = ss.get("matcher_result")
    if mr is None:
        return {"ran": False}

    candidates = getattr(mr, "all_candidates", []) or []
    best = getattr(mr, "best_match", None)

    return {
        "ran": True,
        "traffic_light": getattr(mr, "traffic_light", None),
        "internal_score": getattr(mr, "internal_score", None),
        "best_match": _safe(best) if best is not None else None,
        "all_candidates_count": len(candidates),
        "all_candidates": [_safe(c) for c in candidates],
        "explanation": getattr(mr, "explanation", None),
    }


@_section
def _build_applied_template(ss: dict) -> dict:
    tid = ss.get("applied_template_id")
    if not tid:
        return {"applied": False}

    result = {
        "applied": True,
        "template_id": tid,
    }

    # Пытаемся загрузить шаблон из реестра целиком
    try:
        from core.template_storage import load_template
        tpl = load_template(tid)
        result["template"] = _safe(tpl)
    except Exception as e:
        result["template_load_error"] = f"{type(e).__name__}: {e}"

    return result


@_section
def _build_pipeline(ss: dict) -> dict:
    """Полный пайплайн step3-step5 с промптами, raw ответами и matches."""
    has_step3 = bool(ss.get("debug_prompt_step3") or ss.get("auto_our_side"))
    has_step4 = bool(ss.get("debug_prompt_step4") or ss.get("auto_patterns"))
    has_step5 = bool(ss.get("auto_matches"))

    if not (has_step3 or has_step4 or has_step5):
        return {"ran": False, "reason": "template applied or pipeline not started"}

    our_side = ss.get("auto_our_side") or {}
    patterns = ss.get("auto_patterns") or []
    matches = ss.get("auto_matches") or []

    return {
        "ran": True,
        "step3": {
            "legal_entity": our_side.get("legal_entity", ""),
            "roles": our_side.get("roles", []),
            "signer": our_side.get("signer", ""),
            "confidence": our_side.get("confidence", 0),
            "match_reason": our_side.get("match_reason", ""),
            "evidence": our_side.get("evidence", ""),
            "all_parties": our_side.get("all_parties", []),
            "prompt": ss.get("debug_prompt_step3", ""),
            "raw_llm_response": ss.get("debug_raw_step3", ""),
        },
        "step4": {
            "patterns_count": len(patterns),
            "patterns": list(patterns),
            "prompt": ss.get("debug_prompt_step4", ""),
            "raw_llm_response": ss.get("debug_raw_step4", ""),
        },
        "step5": {
            "matches_count": len(matches),
            "matches": [
                {
                    "id": getattr(m, "id", None),
                    "page": getattr(m, "page", None),
                    "bbox": list(getattr(m, "bbox", []) or []),
                    "context": getattr(m, "context", None),
                    "party": getattr(m, "party", None),
                    "pattern": getattr(m, "pattern", None),
                    "confidence": getattr(m, "confidence", None),
                    "status": getattr(m, "status", None),
                    "operator_excluded": getattr(m, "operator_excluded", None),
                }
                for m in matches
            ],
        },
    }


@_section
def _build_anchors(ss: dict) -> dict:
    anchors = ss.get("all_anchors") or []
    items = []
    for a in anchors:
        safe = _safe(a)
        if isinstance(safe, dict):
            safe["enabled_in_session"] = ss.get(f"anchor_enabled_{getattr(a, 'id', '')}", True)
        items.append(safe)

    by_source = {"auto_regex": 0, "manual_click": 0, "other": 0}
    for a in anchors:
        src = getattr(a, "added_by", None) or "other"
        by_source[src] = by_source.get(src, 0) + 1

    return {
        "total": len(anchors),
        "by_source": by_source,
        "anchors": items,
    }


def _bbox_center(bbox) -> tuple:
    """(cx, cy) или None если bbox невалидный."""
    try:
        if not bbox or len(bbox) < 4:
            return None
        return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    except Exception:
        return None


def _anchor_page_int(anchor) -> int:
    """page_hint может быть строкой '0','1','first','last','any' — приводим к int.
    Возвращает -1 если нельзя определить точно (any/first/last без doc).
    """
    ph = getattr(anchor, "page_hint", None)
    if ph is None:
        return -1
    s = str(ph)
    if s.isdigit():
        return int(s)
    return -1  # any/first/last — точно не сопоставить


@_section
def _build_matches_to_anchors_mapping(ss: dict) -> dict:
    """Сопоставляет SignMatch → TextAnchor по (page, bbox center).

    ВАЖНО: id у match (sig_NNN) и anchor (uuid) разные — `regex_match_to_anchor`
    в core/finder.py создаёт новый uuid, не наследует match.id. Поэтому матчинг
    идёт ПО ПОЗИЦИИ, а не по id.

    Якорь считается соответствующим match'у если:
      - page совпадает (anchor.page_hint == match.page)
      - bbox центры расходятся не более чем на BBOX_TOLERANCE_PT
    """
    BBOX_TOLERANCE_PT = 3.0  # 3pt ≈ 1мм — близкие места считаем одним

    matches = ss.get("auto_matches") or []
    anchors = ss.get("all_anchors") or []

    # Индексируем якоря по странице
    from collections import defaultdict
    anchors_by_page = defaultdict(list)
    for a in anchors:
        anchors_by_page[_anchor_page_int(a)].append(a)

    mapping = []
    matched_anchor_ids = set()

    for m in matches:
        m_page = getattr(m, "page", -1)
        m_bbox = list(getattr(m, "bbox", []) or [])
        m_center = _bbox_center(m_bbox)

        found_anchor = None
        if m_center is not None:
            for a in anchors_by_page.get(m_page, []):
                a_center = _bbox_center(getattr(a, "bbox", []))
                if a_center is None:
                    continue
                if (abs(a_center[0] - m_center[0]) <= BBOX_TOLERANCE_PT and
                        abs(a_center[1] - m_center[1]) <= BBOX_TOLERANCE_PT):
                    found_anchor = a
                    break

        if found_anchor is not None:
            matched_anchor_ids.add(getattr(found_anchor, "id", None))

        mapping.append({
            "match_id": getattr(m, "id", None),
            "match_page": m_page,
            "match_pattern": getattr(m, "pattern", None),
            "match_context": getattr(m, "context", None),
            "match_bbox": m_bbox,
            "anchor_found": found_anchor is not None,
            "anchor_id": getattr(found_anchor, "id", None) if found_anchor else None,
            "anchor_added_by": getattr(found_anchor, "added_by", None) if found_anchor else None,
            "anchor_generated_pattern": getattr(found_anchor, "generated_pattern", None) if found_anchor else None,
            "anchor_text": getattr(found_anchor, "anchor_text", None) if found_anchor else None,
            "anchor_level": getattr(found_anchor, "anchor_level", None) if found_anchor else None,
            "anchor_context_before": getattr(found_anchor, "context_before", None) if found_anchor else None,
            "anchor_context_after": getattr(found_anchor, "context_after", None) if found_anchor else None,
            "pattern_changed": (
                getattr(m, "pattern", None) != getattr(found_anchor, "generated_pattern", None)
                if found_anchor else None
            ),
        })

    # Якоря не сопоставленные ни с одним match (ручная доразметка, шаблон, etc.)
    orphan_anchors = []
    for a in anchors:
        aid = getattr(a, "id", None)
        if aid not in matched_anchor_ids:
            orphan_anchors.append({
                "anchor_id": aid,
                "added_by": getattr(a, "added_by", None),
                "anchor_text": getattr(a, "anchor_text", None),
                "generated_pattern": getattr(a, "generated_pattern", None),
                "page_hint": str(getattr(a, "page_hint", "")),
            })

    return {
        "matches_count": len(matches),
        "anchors_count": len(anchors),
        "matched_pairs_count": len([x for x in mapping if x["anchor_found"]]),
        "lost_matches_count": len([x for x in mapping if not x["anchor_found"]]),
        "orphan_anchors_count": len(orphan_anchors),
        "mapping_method": "by_position (page + bbox center)",
        "bbox_tolerance_pt": BBOX_TOLERANCE_PT,
        "mapping": mapping,
        "orphan_anchors": orphan_anchors,
    }


@_section
def _build_parties_json_section(ss: dict) -> dict:
    """Содержимое parties.json для текущего языка — для сравнения regex
    из реестра с теми что генерируются и попадают в якоря.
    """
    lang = ss.get("auto_language", "")
    try:
        from core.storage import read_json, json_config_exists
        if not json_config_exists("parties.json"):
            return {"present": False, "reason": "parties.json not found"}
        data = read_json("parties.json")
    except Exception as e:
        return {"present": False, "error": f"{type(e).__name__}: {e}"}

    # Структура parties.json может быть разной — кладём всё что есть для языка,
    # плюс ключи верхнего уровня для понимания структуры
    return {
        "present": True,
        "language": lang,
        "top_level_keys": list(data.keys()) if isinstance(data, dict) else None,
        "raw": data,
    }


@_section
def _build_session_flags(ss: dict) -> dict:
    """Флаги управления флоу — какой путь прошёл документ."""
    return {
        "run_full_pipeline": ss.get("run_full_pipeline"),
        "apply_template_confirmed": ss.get("apply_template_confirmed"),
        "applied_template_id": ss.get("applied_template_id"),
        "traffic_light": ss.get("traffic_light"),
        "current_page": ss.get("current_page"),
    }


def build_debug_export(session_state) -> dict:
    """Главная функция: собирает полный диагностический JSON из st.session_state.

    Args:
        session_state: streamlit.session_state (или dict-like объект)

    Returns:
        dict для сериализации в JSON.
    """
    # session_state индексируется как dict, но это не настоящий dict
    ss = {}
    try:
        for k in session_state:
            ss[k] = session_state[k]
    except Exception:
        ss = dict(session_state) if hasattr(session_state, "items") else {}

    return {
        "export_version": "1.8-debug-v2",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "doc_info": _build_doc_info(ss),
        "session_flags": _build_session_flags(ss),
        "fingerprint": _build_fingerprint(ss),
        "matcher": _build_matcher(ss),
        "applied_template": _build_applied_template(ss),
        "pipeline": _build_pipeline(ss),
        "matches_to_anchors_mapping": _build_matches_to_anchors_mapping(ss),
        "all_anchors": _build_anchors(ss),
        "parties_json": _build_parties_json_section(ss),
    }
