"""Fingerprint документов и поиск похожих шаблонов.

v1.4.3

Fingerprint = {page_count, first_page_text_hash (SimHash), section_titles, key_phrases}.
Similarity: SimHash distance + совпадение section_titles.
"""
import re
from simhash import Simhash

from core.template_storage import list_templates, read_template


def compute_fingerprint(doc, key_phrases: list[str] | None = None) -> dict:
    """Вычислить fingerprint документа.
    
    Параметры:
        doc — ParsedDocument
        key_phrases — опционально: ключевые фразы для поиска (роли сторон и т.д.)
    
    Возвращает:
        {
            "page_count": int,
            "first_page_text_hash": int (SimHash),
            "section_titles": [str, ...],
            "key_phrases": [str, ...]
        }
    """
    page_count = len(doc.pages)
    
    # SimHash первой страницы
    first_page_text = doc.pages[0].text if doc.pages else ""
    first_page_hash = Simhash(first_page_text).value
    
    # Детект заголовков разделов (regex: "1. ", "РАЗДЕЛ", "ПРИЛОЖЕНИЕ")
    section_titles = _extract_section_titles(doc)
    
    # Ключевые фразы (если не переданы — автодетект частых слов)
    if key_phrases is None:
        key_phrases = _extract_key_phrases(doc)
    
    return {
        "page_count": page_count,
        "first_page_text_hash": first_page_hash,
        "section_titles": section_titles,
        "key_phrases": key_phrases,
    }


def find_similar_templates(
    fingerprint: dict,
    language: str,
    threshold: float = 0.85,
) -> list[tuple[dict, float]]:
    """Найти похожие шаблоны по fingerprint.
    
    Параметры:
        fingerprint — fingerprint документа (результат compute_fingerprint)
        language — язык документа (ru/en/pl)
        threshold — минимальный similarity (0..1) для включения в результат
    
    Возвращает:
        [(template_data, similarity), ...] отсортированный по убыванию similarity
    """
    all_templates = list_templates()
    results = []
    
    for meta in all_templates:
        # Фильтр по языку
        if meta.get("language") != language:
            continue
        
        template_id = meta.get("template_id")
        if not template_id:
            continue
        
        template = read_template(template_id)
        if not template:
            continue
        
        # Сравниваем fingerprint
        similarity = _compute_similarity(fingerprint, template.get("fingerprint", {}))
        if similarity >= threshold:
            results.append((template, similarity))
    
    # Сортируем по убыванию similarity
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def _extract_section_titles(doc) -> list[str]:
    """Извлечь заголовки разделов из документа.
    
    Ищет строки вида: "1. Название", "РАЗДЕЛ I", "ПРИЛОЖЕНИЕ", "АКТ".
    Возвращает до 10 первых найденных заголовков.
    """
    titles = []
    patterns = [
        re.compile(r"^\d+\.\s+([А-ЯЁA-Z][^\n]{5,50})", re.MULTILINE | re.UNICODE),
        re.compile(r"^(РАЗДЕЛ|SECTION|ROZDZIAŁ)\s+[IVX\d]+[:\.]?\s*([^\n]{5,50})", re.MULTILINE | re.IGNORECASE | re.UNICODE),
        re.compile(r"^(ПРИЛОЖЕНИЕ|APPENDIX|ZAŁĄCZNIK)\s*\d*[:\.]?\s*([^\n]{5,50})", re.MULTILINE | re.IGNORECASE | re.UNICODE),
        re.compile(r"^(АКТ|ACT|AKT)\s+([^\n]{5,50})", re.MULTILINE | re.IGNORECASE | re.UNICODE),
    ]
    
    for page in doc.pages[:5]:  # Смотрим только первые 5 страниц
        text = page.text or ""
        for pattern in patterns:
            for match in pattern.finditer(text):
                title = match.group(1) if len(match.groups()) == 1 else match.group(2)
                title = title.strip()
                if title and len(title) > 5:
                    titles.append(title)
                    if len(titles) >= 10:
                        return titles
    
    return titles


def _extract_key_phrases(doc, top_n: int = 5) -> list[str]:
    """Автоматическое извлечение ключевых фраз (частые слова).
    
    Возвращает top_n наиболее частых значимых слов (длина >= 5).
    """
    from collections import Counter
    
    words = []
    for page in doc.pages[:3]:  # Первые 3 страницы
        text = page.text or ""
        words.extend(re.findall(r'[А-ЯЁA-Z][а-яёa-z]{4,}', text, re.UNICODE))
    
    counter = Counter(words)
    return [word for word, _ in counter.most_common(top_n)]


def _compute_similarity(fp1: dict, fp2: dict) -> float:
    """Вычислить similarity между двумя fingerprint.
    
    Формула:
    - SimHash distance (нормализованный в 0..1): вес 0.6
    - Совпадение section_titles (Jaccard): вес 0.3
    - Совпадение page_count (exact match): вес 0.1
    """
    # SimHash similarity
    hash1 = fp1.get("first_page_text_hash", 0)
    hash2 = fp2.get("first_page_text_hash", 0)
    simhash_sim = _simhash_similarity(hash1, hash2)
    
    # Section titles Jaccard
    titles1 = set(fp1.get("section_titles", []))
    titles2 = set(fp2.get("section_titles", []))
    if titles1 or titles2:
        intersection = len(titles1 & titles2)
        union = len(titles1 | titles2)
        titles_sim = intersection / union if union > 0 else 0.0
    else:
        titles_sim = 0.0
    
    # Page count exact match
    pc1 = fp1.get("page_count", 0)
    pc2 = fp2.get("page_count", 0)
    page_count_sim = 1.0 if pc1 == pc2 else 0.0
    
    # Взвешенная сумма
    return (
        simhash_sim * 0.6 +
        titles_sim * 0.3 +
        page_count_sim * 0.1
    )


def _simhash_similarity(hash1: int, hash2: int) -> float:
    """SimHash similarity (нормализованный Hamming distance в 0..1).
    
    SimHash хеши — 64-битные. Hamming distance = количество различающихся битов.
    Similarity = 1 - (hamming_distance / 64).
    """
    xor = hash1 ^ hash2
    hamming = bin(xor).count('1')
    return 1.0 - (hamming / 64.0)
