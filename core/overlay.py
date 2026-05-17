"""Наложение PNG-подписи на PDF и опциональный flatten."""
import io

import fitz
from PIL import Image


# Высота подписи: max(MIN_PT, line_height × MULTIPLIER)
MIN_SIGNATURE_HEIGHT_PT = 30
LINE_HEIGHT_MULTIPLIER = 3


def apply_signature(pdf_bytes: bytes, matches: list, png_bytes: bytes, flatten: bool = False) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    img = Image.open(io.BytesIO(png_bytes))
    png_w, png_h = img.size
    aspect = png_w / png_h if png_h else 1.0

    for m in matches:
        if m.operator_excluded or m.status == "rejected_by_llm":
            continue

        page = doc[m.page]
        page_rect = page.rect  # границы страницы
        anchor_x, anchor_y_bottom, line_height = _find_underscore_anchor(page, m.bbox, m.pattern)

        sig_h = max(MIN_SIGNATURE_HEIGHT_PT, line_height * LINE_HEIGHT_MULTIPLIER)
        sig_w = sig_h * aspect

        sig_rect = fitz.Rect(
            anchor_x,
            anchor_y_bottom - sig_h,
            anchor_x + sig_w,
            anchor_y_bottom,
        )

        # Клипаем к границам страницы — иначе fitz растягивает изображение на весь лист
        sig_rect = sig_rect & page_rect

        # Пропускаем если rect невалидный (нулевая площадь или инвертированный)
        if sig_rect.is_empty or sig_rect.is_infinite or sig_rect.width < 5 or sig_rect.height < 5:
            print(f"[overlay] skip match {m.id}: invalid sig_rect {sig_rect}", flush=True)
            continue

        page.insert_image(sig_rect, stream=png_bytes, keep_proportion=True)

    out_bytes = doc.tobytes(deflate=True)
    doc.close()

    if flatten:
        out_bytes = _flatten_pdf(out_bytes)

    return out_bytes


def _find_underscore_anchor(page, bbox, pattern: str):
    """Найти позицию подчёркиваний.
    Стратегия:
    1. Если pattern начинается с '_' — underscores в начале bbox, anchor=bbox.x0
    2. Иначе ищем '___' через page.search_for и фильтруем по y и x
    3. Fallback: anchor=bbox.x0 + 30% ширины (примерно после слова роли)
    """
    x0, y0, x1, y1 = bbox
    line_height = y1 - y0

    if pattern.startswith("_"):
        return x0, y1, line_height

    # ищем underscore-серии на странице
    underscore_rects = page.search_for("___")

    best = None
    best_dist = float("inf")
    for r in underscore_rects:
        # вертикальное пересечение с bbox
        if r.y1 < y0 - 2 or r.y0 > y1 + 2:
            continue
        # x в пределах bbox (с допуском)
        if r.x0 < x0 - 10 or r.x0 > x1:
            continue
        # выбираем ближайший по центру
        rc = (r.y0 + r.y1) / 2
        bc = (y0 + y1) / 2
        d = abs(rc - bc)
        if d < best_dist:
            best_dist = d
            best = r

    if best:
        return best.x0, best.y1, max(line_height, best.height)

    # fallback — сместить вправо от начала bbox (после слова роли)
    bbox_width = x1 - x0
    return x0 + bbox_width * 0.3, y1, line_height


def _flatten_pdf(pdf_bytes: bytes) -> bytes:
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    dst = fitz.open()
    for page in src:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        new_page = dst.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(page.rect, pixmap=pix)
    out = dst.tobytes(deflate=True)
    src.close()
    dst.close()
    return out
