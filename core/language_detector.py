"""Определение языка документа.

Стратегия:
  1. Если parser уже определил язык (doc.language) и это известный код — используем.
  2. Иначе — LLM fallback (Claude) по первым 2000 символам.
  3. Если LLM недоступен или вернул мусор — возвращаем 'unknown'.

Поддерживаемые языки: ru, en, pl. Остальные → 'unknown' (fallback в finder
склеит паттерны всех языков).
"""
import json
import os

SUPPORTED = ("ru", "en", "pl")


def detect_language(doc) -> str:
    """Возвращает 'ru' / 'en' / 'pl' / 'unknown'.

    Принимает ParsedDocument с полями .language (опц.) и .pages[].text.
    """
    # 1. Доверяем parser.py если у него уже есть нормальный код
    parser_lang = (getattr(doc, "language", "") or "").lower()[:2]
    if parser_lang in SUPPORTED:
        return parser_lang

    # 2. LLM fallback
    try:
        sample = _get_sample(doc, max_chars=2000)
        if not sample.strip():
            return "unknown"
        return _llm_detect(sample)
    except Exception:
        return "unknown"


def _get_sample(doc, max_chars: int) -> str:
    buf = []
    total = 0
    for page in doc.pages:
        text = page.text or ""
        if total + len(text) >= max_chars:
            buf.append(text[: max_chars - total])
            break
        buf.append(text)
        total += len(text)
    return "\n".join(buf)


def _llm_detect(sample: str) -> str:
    """Вернёт 'ru' / 'en' / 'pl' / 'unknown' через Claude."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "unknown"

    from anthropic import Anthropic

    client = Anthropic()
    prompt = (
        "Определи язык фрагмента договора. Ответь ОДНИМ кодом из списка: "
        "ru, en, pl, unknown. Никаких пояснений, только код.\n\n"
        f"Фрагмент:\n{sample}"
    )

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )
    answer = (resp.content[0].text or "").strip().lower()
    if answer in SUPPORTED:
        return answer
    return "unknown"
