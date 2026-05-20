"""Каскадный матчер шаблонов по fingerprint (v1.8).

Публичный API:
  find_matching_templates(doc, language, our_synonyms, fingerprint) -> MatcherResult
"""
import json
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

logger = logging.getLogger(__name__)

_GREEN_THRESHOLD = 0.95
_COLLISION_DELTA = 0.05  # разница score, при которой считаем коллизию


# ── DTO ───────────────────────────────────────────────────────────────────────

@dataclass
class MatchResult:
    template_id: str
    template_name: str
    score: float
    score_breakdown: dict        # {"simhash", "jaccard", "cosine_chars_per_page", "page_count_similarity"}
    explanation: str
    synonyms_match: bool         # синонимы нашей стороны пересеклись


@dataclass
class MatcherResult:
    traffic_light: Literal["green", "yellow"]
    best_match: Optional[MatchResult]
    all_candidates: list         # топ-5 MatchResult
    explanation: str


# ── Публичный API ─────────────────────────────────────────────────────────────

def find_matching_templates(
    doc,
    language: str,
    our_synonyms: Optional[dict] = None,
    fingerprint: Optional[dict] = None,
) -> MatcherResult:
    """
    Главная функция.
    doc — fitz.Document (или объект с .pages для совместимости со страницами парсера).
    Возвращает MatcherResult с traffic_light и топ-кандидатами.
    """
    try:
        # 1. Fingerprint
        if fingerprint is None:
            fingerprint = _compute_fp(doc, language)

        # 2. Загрузить шаблоны
        from core.template_storage import list_templates
        templates = list_templates(language=language)

        if not templates:
            return MatcherResult(
                traffic_light="yellow",
                best_match=None,
                all_candidates=[],
                explanation="Шаблонов в реестре пока нет",
            )

        # 3. Каскадный матчинг
        candidates: list[MatchResult] = []
        for tpl in templates:
            tpl_fp = tpl.fingerprint or {}
            if not passes_quick_filter(fingerprint, tpl_fp):
                continue
            score, breakdown = compute_composite_score(fingerprint, tpl_fp)
            synonyms_match = _check_synonyms(our_synonyms, tpl.synonyms_used)
            mr = MatchResult(
                template_id=tpl.template_id,
                template_name=tpl.name,
                score=score,
                score_breakdown=breakdown,
                explanation="",
                synonyms_match=synonyms_match,
            )
            candidates.append(mr)

        if not candidates:
            return MatcherResult(
                traffic_light="yellow",
                best_match=None,
                all_candidates=[],
                explanation="Ни один шаблон не прошёл быструю отсечку",
            )

        # 4. Сортируем по score desc
        candidates.sort(key=lambda x: x.score, reverse=True)
        top5 = candidates[:5]
        best = top5[0]

        # 5. Проверяем синонимы — снижаем score
        if not best.synonyms_match:
            best = MatchResult(
                template_id=best.template_id,
                template_name=best.template_name,
                score=best.score * 0.85,   # штраф 15%
                score_breakdown=best.score_breakdown,
                explanation="",
                synonyms_match=False,
            )
            top5[0] = best

        # 6. Коллизия: два кандидата с близким score
        has_collision = (
            len(top5) >= 2
            and abs(top5[0].score - top5[1].score) <= _COLLISION_DELTA
        )

        # 7. Светофор
        from core.traffic_light import classify, load_config
        cfg = load_config()
        light = classify(
            score=best.score,
            synonyms_match=best.synonyms_match,
            has_collision=has_collision,
            config=cfg,
        )

        # 8. Explanation
        for i, mr in enumerate(top5):
            top5[i] = MatchResult(
                template_id=mr.template_id,
                template_name=mr.template_name,
                score=mr.score,
                score_breakdown=mr.score_breakdown,
                explanation=build_explanation(mr, light if i == 0 else "yellow"),
                synonyms_match=mr.synonyms_match,
            )

        best = top5[0]
        summary = best.explanation
        if has_collision:
            summary += (
                f"\n⚠ Найдено несколько похожих шаблонов "
                f"({top5[0].template_name} {top5[0].score:.0%} vs "
                f"{top5[1].template_name} {top5[1].score:.0%})."
            )

        log_matching_decision(
            MatcherResult(light, best, top5, summary),
            doc_filename="",
        )

        return MatcherResult(
            traffic_light=light,
            best_match=best,
            all_candidates=top5,
            explanation=summary,
        )

    except Exception as e:
        logger.error("find_matching_templates failed: %s", e)
        sys.stderr.write(f"[template_matcher] find_matching_templates: {e}\n")
        return MatcherResult(
            traffic_light="yellow",
            best_match=None,
            all_candidates=[],
            explanation=f"Ошибка матчинга: {e}",
        )


# ── Фильтры и метрики ─────────────────────────────────────────────────────────

def passes_quick_filter(new_fp: dict, tpl_fp: dict) -> bool:
    """
    Быстрая отсечка:
    - языки совпадают
    - разница page_count <= 2
    - total_chars в пределах ±20%
    """
    if new_fp.get("language") != tpl_fp.get("language"):
        return False

    n_pages = new_fp.get("page_count", 0) or 0
    t_pages = tpl_fp.get("page_count", 0) or 0
    if abs(n_pages - t_pages) > 2:
        return False

    n_chars = new_fp.get("total_chars", 0) or 0
    t_chars = tpl_fp.get("total_chars", 0) or 0
    if t_chars == 0:
        return True  # нет данных — пропускаем
    ratio = n_chars / t_chars
    if not (0.8 <= ratio <= 1.2):
        return False

    return True


def compute_composite_score(new_fp: dict, tpl_fp: dict) -> tuple[float, dict]:
    """
    score = 0.4*simhash + 0.3*jaccard + 0.2*cosine_chars + 0.1*page_count_sim
    """
    sh = _simhash_similarity(
        new_fp.get("header_simhash", ""),
        tpl_fp.get("header_simhash", ""),
    )
    jac = _jaccard(
        new_fp.get("section_titles", []),
        tpl_fp.get("section_titles", []),
    )
    cos = _cosine_chars_per_page(
        new_fp.get("chars_per_page", []),
        tpl_fp.get("chars_per_page", []),
    )
    n_p = new_fp.get("page_count", 0) or 0
    t_p = tpl_fp.get("page_count", 0) or 0
    denom = max(n_p, t_p, 1)
    pc_sim = 1.0 - abs(n_p - t_p) / denom

    score = 0.4 * sh + 0.3 * jac + 0.2 * cos + 0.1 * pc_sim
    breakdown = {
        "simhash": round(sh, 4),
        "jaccard": round(jac, 4),
        "cosine_chars_per_page": round(cos, 4),
        "page_count_similarity": round(pc_sim, 4),
    }
    return round(score, 4), breakdown


def build_explanation(match: MatchResult, traffic_light: str) -> str:
    bd = match.score_breakdown
    parts = []

    sh = bd.get("simhash", 0)
    if sh >= 0.95:
        parts.append("Шапка договора почти идентична")
    elif sh >= 0.85:
        parts.append(f"Шапка договора очень похожа ({int(sh * 100)}%)")
    elif sh >= 0.70:
        parts.append(f"Шапка договора частично совпадает ({int(sh * 100)}%)")
    else:
        parts.append(f"Шапка договора отличается (совпадение {int(sh * 100)}%)")

    j = bd.get("jaccard", 0)
    if j >= 0.9:
        parts.append("структура разделов идентична")
    elif j >= 0.7:
        parts.append(f"структура разделов совпадает на {int(j * 100)}%")
    else:
        parts.append(f"структура разделов отличается ({int(j * 100)}% совпадения)")

    pc = bd.get("page_count_similarity", 0)
    if pc >= 0.95:
        parts.append("количество страниц совпадает")
    else:
        parts.append("количество страниц немного отличается")

    if not match.synonyms_match:
        parts.append("⚠ синонимы стороны в этом документе отличаются от шаблона")

    return ". ".join(parts) + "."


# ── helpers ───────────────────────────────────────────────────────────────────

def _compute_fp(doc, language: str) -> dict:
    """Вычисляет fingerprint через core.fingerprint."""
    try:
        import fitz
        if hasattr(doc, "pdf_bytes"):
            fitz_doc = fitz.open(stream=doc.pdf_bytes, filetype="pdf")
        else:
            fitz_doc = doc
        from core.fingerprint import compute_fingerprint
        return compute_fingerprint(fitz_doc, language)
    except Exception as e:
        sys.stderr.write(f"[template_matcher] _compute_fp: {e}\n")
        return {"page_count": 0, "total_chars": 0, "chars_per_page": [],
                "header_simhash": "", "section_titles": [], "language": language}


def _simhash_similarity(h1: str, h2: str) -> float:
    """Simhash similarity по Хэммингову расстоянию. 0.0 если нет данных."""
    if not h1 or not h2:
        return 0.5  # нейтральное значение если нет данных
    try:
        from simhash import Simhash
        v1 = int(h1)
        v2 = int(h2)
        # Хэмминговое расстояние через XOR
        xor = v1 ^ v2
        dist = bin(xor).count("1")
        return max(0.0, 1.0 - dist / 64.0)
    except Exception:
        return 0.5


def _jaccard(a: list, b: list) -> float:
    """Jaccard similarity двух списков строк."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    sa = set(s.strip().lower() for s in a)
    sb = set(s.strip().lower() for s in b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _cosine_chars_per_page(v1: list, v2: list) -> float:
    """Косинусное сходство двух векторов длины страниц (нормализованных)."""
    if not v1 or not v2:
        return 0.5
    # Выравниваем длину
    n = max(len(v1), len(v2))
    a = list(v1) + [0] * (n - len(v1))
    b = list(v2) + [0] * (n - len(v2))
    # Нормализуем
    a = _normalize(a)
    b = _normalize(b)
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(1.0, dot))


def _normalize(v: list) -> list:
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0:
        return v
    return [x / norm for x in v]


def _check_synonyms(our_synonyms: Optional[dict], tpl_synonyms: Optional[dict]) -> bool:
    """Проверяет пересечение синонимов нашей стороны с шаблоном."""
    if not our_synonyms or not tpl_synonyms:
        return True  # нет данных — не блокируем
    our_tokens = set()
    for v in our_synonyms.values():
        if isinstance(v, str) and v.strip():
            our_tokens.add(v.strip().lower())
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str) and x.strip():
                    our_tokens.add(x.strip().lower())

    tpl_tokens = set()
    for v in tpl_synonyms.values():
        if isinstance(v, str) and v.strip():
            tpl_tokens.add(v.strip().lower())
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str) and x.strip():
                    tpl_tokens.add(x.strip().lower())

    if not our_tokens or not tpl_tokens:
        return True
    return bool(our_tokens & tpl_tokens)


# ── Логирование ───────────────────────────────────────────────────────────────

def log_matching_decision(matcher_result: MatcherResult, doc_filename: str) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "doc_filename": doc_filename,
        "traffic_light": matcher_result.traffic_light,
        "best_score": matcher_result.best_match.score if matcher_result.best_match else None,
        "best_template_id": matcher_result.best_match.template_id if matcher_result.best_match else None,
        "candidates_count": len(matcher_result.all_candidates),
        "explanation": matcher_result.explanation,
    }
    sys.stderr.write(f"[TRAFFIC_LIGHT] {json.dumps(record, ensure_ascii=False)}\n")
