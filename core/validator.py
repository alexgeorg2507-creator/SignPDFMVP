"""LLM-валидация найденных мест подписи через Claude API."""
import json
import os
import re

from core.finder import SignMatch

MODEL = "claude-sonnet-4-6"


def validate_with_llm(matches: list[SignMatch], party_name: str) -> list[SignMatch]:
    """Прогон через Claude — определить реальные места подписи и шум.

    Если ANTHROPIC_API_KEY не задан — возвращает matches без изменений.
    При ошибке API — graceful degradation, confidence=0.5.
    """
    if not matches:
        return matches

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        for m in matches:
            m.confidence = 0.5
        return matches

    try:
        import anthropic
    except ImportError:
        return matches

    items = [
        {
            "id": m.id,
            "page": m.page + 1,
            "context": m.context,
            "pattern": m.pattern,
        }
        for m in matches
    ]

    prompt = f"""Ты валидируешь места для подписи стороны "{party_name}" в договоре.
Для каждого найденного места определи: это реальное место для подписи или ложное срабатывание (например, упоминание роли в тексте без линии для подписи)?

Учти распространённые конвенции:
- Подпись в подвале каждой страницы (визирование) — реальное место
- Строка вида "Клиент _________ ФИО" — реальное место
- Упоминание "Клиент обязуется..." без подчёркиваний — НЕ место для подписи

Найденные места:
{json.dumps(items, ensure_ascii=False, indent=2)}

Ответь ТОЛЬКО JSON-массивом, без пояснений и markdown:
[{{"id":"sig_001","is_signature":true,"confidence":0.95,"reason":"короткое пояснение"}},...]
"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()

        # извлечь JSON если завёрнут в markdown-блок
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            text = m.group(0)

        validations = json.loads(text)
        val_map = {v["id"]: v for v in validations}

        for match in matches:
            v = val_map.get(match.id)
            if v:
                match.confidence = float(v.get("confidence", 0.5))
                if not v.get("is_signature", True):
                    match.status = "rejected_by_llm"
                    match.correction_applied = "LLM"
            else:
                match.confidence = 0.5

    except Exception as e:
        for match in matches:
            match.confidence = 0.5
        # ошибку не показываем в UI, только в лог
        print(f"[validator] LLM error: {e}")

    return matches
