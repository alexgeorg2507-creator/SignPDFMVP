"""Применение шаблона к новому документу с коррекцией координат.

v1.4.3

Шаблон содержит координаты мест подписи относительно оригинального документа.
При применении к новому похожему документу координаты могут слегка сдвинуться
(например, если добавлен водяной знак, изменился шрифт, или текст сдвинулся).

Стратегия коррекции:
1. Простая (v1.4.3): координаты применяются as-is без коррекции
2. Умная (v2.0): OCR + поиск якорного текста + смещение bbox
"""
from dataclasses import dataclass

from core.finder import SignMatch


@dataclass
class TemplatePosition:
    """Позиция места подписи в шаблоне."""
    id: str
    page: int
    x: float  # center_x
    y: float  # center_y
    width: float
    height: float
    party_hint: str | None = None  # "Арендатор", "Исполнитель" и т.д.


def apply_template_simple(doc, template: dict) -> list[SignMatch]:
    """Применить шаблон к документу (без коррекции координат).
    
    Параметры:
        doc — ParsedDocument
        template — шаблон из template_storage.read_template()
    
    Возвращает:
        list[SignMatch] с координатами из шаблона
    """
    positions = template.get("signature_positions", [])
    if not positions:
        return []
    
    matches = []
    for pos in positions:
        pos_id = pos.get("id", "template_pos")
        page = pos.get("page", 0)
        
        # Проверка что страница существует
        if page < 0 or page >= len(doc.pages):
            continue
        
        # Конвертируем center (x, y) + (width, height) → bbox (x0, y0, x1, y1)
        center_x = pos.get("x", 0)
        center_y = pos.get("y", 0)
        width = pos.get("width", 100)
        height = pos.get("height", 30)
        
        x0 = center_x - width / 2
        y0 = center_y - height / 2
        x1 = center_x + width / 2
        y1 = center_y + height / 2
        
        party_hint = pos.get("party_hint", "Шаблон")
        
        matches.append(SignMatch(
            id=pos_id,
            page=page,
            bbox=(x0, y0, x1, y1),
            context=f"Шаблон: {template.get('name', 'Unnamed')}",
            party=party_hint,
            pattern="template",
            confidence=0.9,  # Шаблон = высокая уверенность
        ))
    
    return matches


def apply_template_smart(doc, template: dict) -> list[SignMatch]:
    """Применить шаблон с умной коррекцией координат (OCR + якорный текст).
    
    НЕ РЕАЛИЗОВАНО в v1.4.3 — заглушка для v2.0.
    Возвращает результат apply_template_simple().
    """
    # TODO v2.0: OCR страницы, поиск якорного текста вокруг места подписи,
    # вычисление смещения, коррекция bbox
    return apply_template_simple(doc, template)


def convert_matches_to_template_positions(matches: list[SignMatch]) -> list[dict]:
    """Конвертировать SignMatch → TemplatePosition для сохранения в шаблоне.
    
    Параметры:
        matches — список SignMatch из ручной разметки или автопоиска
    
    Возвращает:
        list[dict] для поля signature_positions в шаблоне
    """
    positions = []
    for m in matches:
        x0, y0, x1, y1 = m.bbox
        center_x = (x0 + x1) / 2
        center_y = (y0 + y1) / 2
        width = x1 - x0
        height = y1 - y0
        
        positions.append({
            "id": m.id,
            "page": m.page,
            "x": center_x,
            "y": center_y,
            "width": width,
            "height": height,
            "party_hint": m.party,
        })
    
    return positions
