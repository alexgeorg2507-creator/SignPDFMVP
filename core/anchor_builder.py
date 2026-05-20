"""Построение текстовых якорей из кликов и regex-матчей.

v1.7: TextAnchor — единица привязки подписи к тексту документа.
Клик → 4-уровневый поиск → TextAnchor или None.
"""
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

import fitz

logger = logging.getLogger(__name__)

_RADIUS_LEVEL1 = 30.0
_RADIUS_LEVEL2_Y = 10.0
_RADIUS_LEVEL3 = 200.0
_UNDERLINE_MAX_DIST = 50.0
_UNDERLINE_RE = re.compile(r"[_\.]{5,}")


@dataclass
class TextAnchor:
    id: str
    anchor_type: Literal["text_proximity", "underline_line"]
    anchor_level: int                                   # 1–4
    anchor_text: str                                    # точный текст из документа
    position: Literal["right", "left", "below", "above", "on"]
    offset_pt: float                                    # смещение от anchor_text до подписи
    generated_pattern: str                              # regex для finder.py
    context_before: str                                 # ~50 символов до якоря
    context_after: str                                  # ~50 символов после
    page_hint: str                                      # "first"|"last"|"any"|str(idx)
    added_by: Literal["auto_regex", "manual_click"]
    added_at: str                                       # ISO timestamp UTC
    bbox: tuple[float, float, float, float]             # (x0,y0,x1,y1) место подписи


# ── Публичный API ──────────────────────────────────────────────────────────────

def build_anchor_from_click(
    doc,
    page_idx: int,
    click_x: float,
    click_y: float,
    language: str,
    signature_height_pt: float = 30.0,
) -> Optional[TextAnchor]:
    """Клик → TextAnchor через 4-уровневый алгоритм. None если клик в пустоту."""
    try:
        page = doc[page_idx]

        blocks = _find_text_blocks_in_radius(page, click_x, click_y, _RADIUS_LEVEL1)
        if blocks:
            return _build_anchor(blocks, 1, "text_proximity",
                                 click_x, click_y, page_idx, doc, language, signature_height_pt)

        blocks = _find_text_blocks_same_row(page, click_x, click_y, _RADIUS_LEVEL2_Y)
        if blocks:
            return _build_anchor(blocks, 2, "text_proximity",
                                 click_x, click_y, page_idx, doc, language, signature_height_pt)

        blocks = _find_text_blocks_in_radius(page, click_x, click_y, _RADIUS_LEVEL3)
        if blocks:
            return _build_anchor(blocks, 3, "text_proximity",
                                 click_x, click_y, page_idx, doc, language, signature_height_pt)

        underline = _find_nearest_underline(page, click_x, click_y, _UNDERLINE_MAX_DIST)
        if underline:
            return _build_underline_anchor(underline, 4,
                                           click_x, click_y, page_idx, doc, language, signature_height_pt)

        return None
    except Exception as e:
        logger.error("build_anchor_from_click failed: %s", e)
        sys.stderr.write(f"[anchor_builder] build_anchor_from_click: {e}\n")
        return None


def build_anchor_from_regex_match(
    pattern: str,
    match_text: str,
    match_bbox: tuple[float, float, float, float],
    page_idx: int,
    language: str,
    context_before: str = "",
    context_after: str = "",
) -> TextAnchor:
    """Regex-матч → TextAnchor с added_by='auto_regex'."""
    anchor_text = match_text.strip()[:120]
    return TextAnchor(
        id=uuid4().hex,
        anchor_type="text_proximity",
        anchor_level=1,
        anchor_text=anchor_text,
        position="on",
        offset_pt=0.0,
        generated_pattern=pattern,
        context_before=context_before[-50:],
        context_after=context_after[:50],
        page_hint=str(page_idx),
        added_by="auto_regex",
        added_at=datetime.now(timezone.utc).isoformat(),
        bbox=match_bbox,
    )


def has_anchor_at(doc, page_idx: int, x: float, y: float) -> bool:
    """Быстрая проверка — есть ли текст в радиусе для построения якоря."""
    try:
        page = doc[page_idx]
        if _find_text_blocks_in_radius(page, x, y, _RADIUS_LEVEL1):
            return True
        if _find_nearest_underline(page, x, y, _UNDERLINE_MAX_DIST):
            return True
        return False
    except Exception as e:
        logger.error("has_anchor_at failed: %s", e)
        return False


# ── Внутренние хелперы ─────────────────────────────────────────────────────────

def _block_center(block) -> tuple[float, float]:
    x0, y0, x1, y1 = block[:4]
    return (x0 + x1) / 2, (y0 + y1) / 2


def _dist(ax, ay, bx, by) -> float:
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _find_text_blocks_in_radius(page, x: float, y: float, radius: float) -> list:
    """Текстовые блоки с непустым текстом в радиусе от точки."""
    result = []
    for b in page.get_text("blocks"):
        if b[6] != 0:  # пропускаем не-текстовые блоки
            continue
        text = b[4].strip()
        if not text:
            continue
        cx, cy = _block_center(b)
        if _dist(x, y, cx, cy) <= radius:
            result.append(b)
    return result


def _find_text_blocks_same_row(page, x: float, y: float, tolerance: float) -> list:
    """Блоки в горизонтальной полосе ±tolerance по Y."""
    result = []
    for b in page.get_text("blocks"):
        if b[6] != 0:
            continue
        text = b[4].strip()
        if not text:
            continue
        _, cy = _block_center(b)
        if abs(cy - y) <= tolerance:
            result.append(b)
    return result


def _find_nearest_underline(page, x: float, y: float, max_distance: float) -> Optional[dict]:
    """Ищет строку с подчёркиваниями рядом с кликом."""
    best = None
    best_dist = max_distance + 1

    for b in page.get_text("blocks"):
        if b[6] != 0:
            continue
        text = b[4]
        if not _UNDERLINE_RE.search(text):
            continue
        cx, cy = _block_center(b)
        d = _dist(x, y, cx, cy)
        if d < best_dist:
            best_dist = d
            best = {"block": b, "text": text.strip(), "dist": d}

    return best


def _closest_block(blocks, x: float, y: float):
    """Ближайший блок к точке."""
    return min(blocks, key=lambda b: _dist(x, y, *_block_center(b)))


def _determine_position(anchor_bbox, click_x: float, click_y: float
                        ) -> tuple[Literal["right", "left", "below", "above", "on"], float]:
    """Определяет положение клика относительно текста-якоря и смещение."""
    x0, y0, x1, y1 = anchor_bbox
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2

    dx = click_x - cx
    dy = click_y - cy

    if abs(dy) <= 15:
        if dx > 5:
            return "right", dx
        if dx < -5:
            return "left", abs(dx)
        return "on", 0.0
    if dy > 0:
        return "below", dy
    return "above", abs(dy)


def _generate_pattern_for_anchor(anchor_text: str, position: str, language: str) -> str:
    """Генерирует regex-паттерн по тексту якоря и его позиции относительно подписи."""
    escaped = re.escape(anchor_text[:60])
    line = r"[_\.]{5,}"

    if position in ("right", "on"):
        return rf"{escaped}\s*{line}"
    if position == "left":
        return rf"{line}\s*{escaped}"
    if position == "below":
        return rf"{escaped}[\s\S]{{0,60}}{line}"
    # above
    return rf"{line}[\s\S]{{0,60}}{escaped}"


def _extract_context(page, anchor_bbox, before: int = 50, after: int = 50) -> tuple[str, str]:
    """Контекст до и после bbox-якоря из полного текста страницы."""
    full_text = page.get_text("text")
    anchor_text_stripped = page.get_text("text", clip=fitz.Rect(anchor_bbox)).strip()
    if not anchor_text_stripped:
        return "", ""
    pos = full_text.find(anchor_text_stripped)
    if pos == -1:
        return "", ""
    ctx_before = full_text[max(0, pos - before):pos].replace("\n", " ")
    ctx_after = full_text[pos + len(anchor_text_stripped):
                          pos + len(anchor_text_stripped) + after].replace("\n", " ")
    return ctx_before, ctx_after


def _page_hint(page_idx: int, doc) -> str:
    n = len(doc)
    if page_idx == 0:
        return "first"
    if page_idx == n - 1:
        return "last"
    return str(page_idx)


def _signature_bbox_from_position(
    anchor_bbox, position: str, offset_pt: float, signature_height_pt: float
) -> tuple[float, float, float, float]:
    """bbox якоря должен соответствовать текстовой строке (~12pt),
    overlay сам подберёт высоту подписи через line_height × множитель.
    Ширину тоже ограничиваем разумным размером подписи.
    """
    x0, y0, x1, y1 = anchor_bbox
    text_w = x1 - x0
    # Ширина подписи: разумный максимум, не шире самого якорного текста × 1.5
    sig_w = min(150.0, max(80.0, text_w * 1.2))
    # Высота bbox = высота текстовой строки (для корректного множителя в overlay)
    line_h = min(15.0, max(8.0, y1 - y0))

    if position == "right":
        return (x1 + offset_pt, y0, x1 + offset_pt + sig_w, y0 + line_h)
    if position == "left":
        return (x0 - offset_pt - sig_w, y0, x0 - offset_pt, y0 + line_h)
    if position == "below":
        return (x0, y1 + offset_pt, x0 + sig_w, y1 + offset_pt + line_h)
    if position == "above":
        return (x0, y0 - offset_pt - line_h, x0 + sig_w, y0 - offset_pt)
    # on
    return (x0, y0, x0 + sig_w, y0 + line_h)


def _build_anchor(
    blocks, level: int, anchor_type: str,
    click_x, click_y, page_idx, doc, language, signature_height_pt
) -> TextAnchor:
    page = doc[page_idx]
    closest = _closest_block(blocks, click_x, click_y)
    anchor_bbox = closest[:4]
    anchor_text = closest[4].strip()[:120]

    position, offset_pt = _determine_position(anchor_bbox, click_x, click_y)
    pattern = _generate_pattern_for_anchor(anchor_text, position, language)
    ctx_before, ctx_after = _extract_context(page, anchor_bbox)
    sign_bbox = _signature_bbox_from_position(anchor_bbox, position, offset_pt, signature_height_pt)

    return TextAnchor(
        id=uuid4().hex,
        anchor_type=anchor_type,
        anchor_level=level,
        anchor_text=anchor_text,
        position=position,
        offset_pt=offset_pt,
        generated_pattern=pattern,
        context_before=ctx_before,
        context_after=ctx_after,
        page_hint=_page_hint(page_idx, doc),
        added_by="manual_click",
        added_at=datetime.now(timezone.utc).isoformat(),
        bbox=sign_bbox,
    )


def _build_underline_anchor(
    underline_info: dict, level: int,
    click_x, click_y, page_idx, doc, language, signature_height_pt
) -> TextAnchor:
    block = underline_info["block"]
    anchor_bbox = block[:4]
    anchor_text = underline_info["text"][:120]
    page = doc[page_idx]

    position, offset_pt = _determine_position(anchor_bbox, click_x, click_y)
    pattern = r"[_\.]{5,}"
    ctx_before, ctx_after = _extract_context(page, anchor_bbox)
    sign_bbox = _signature_bbox_from_position(anchor_bbox, position, offset_pt, signature_height_pt)

    return TextAnchor(
        id=uuid4().hex,
        anchor_type="underline_line",
        anchor_level=level,
        anchor_text=anchor_text,
        position=position,
        offset_pt=offset_pt,
        generated_pattern=pattern,
        context_before=ctx_before,
        context_after=ctx_after,
        page_hint=_page_hint(page_idx, doc),
        added_by="manual_click",
        added_at=datetime.now(timezone.utc).isoformat(),
        bbox=sign_bbox,
    )
