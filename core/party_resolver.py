"""Идентификация стороны договора по ФИО подписанта и/или компании.

Использует Claude API. На вход — фрагмент документа, имя подписанта,
опционально название компании, список доступных сторон.

Возвращает: {"party": <name|None>, "confidence": 0..1, "evidence": "..."}.

Логика confidence по ТЗ:
  >= 0.7  — уверенное определение, используем
  0.5-0.7 — слабое, разрешаем но показываем предупреждение
  < 0.5   — STOP, сообщаем пользователю
"""
import json
import os
import re


def resolve_party(
    doc,
    signer_name: str,
    company: str | None,
    parties: list[dict],
) -> dict:
    """Определить сторону договора.

    Параметры:
      doc — ParsedDocument
      signer_name — обязательное ФИО подписанта
      company — опциональное название компании
      parties — список сторон из parse_parties_json() (name, aliases, ...)

    Возвращает dict с полями: party (str|None), confidence (float),
    evidence (str), error (str|None).
    """
    if not signer_name or not signer_name.strip():
        return {"party": None, "confidence": 0.0, "evidence": "",
                "error": "ФИО подписанта не указано"}

    if not parties:
        return {"party": None, "confidence": 0.0, "evidence": "",
                "error": "Список сторон пуст"}

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"party": None, "confidence": 0.0, "evidence": "",
                "error": "ANTHROPIC_API_KEY не задан"}

    doc_text = _get_doc_text(doc, max_chars=3000)
    if not doc_text.strip():
        return {"party": None, "confidence": 0.0, "evidence": "",
                "error": "Не удалось извлечь текст из документа"}

    try:
        result = _call_llm(doc_text, signer_name.strip(),
                           (company or "").strip() or None, parties)

        llm_party = result.get("party")

        # Кейс 1: LLM явно вернул null/None — подписант не найден в документе
        if llm_party is None:
            hint = ""
            if len(signer_name.strip().split()) == 1:
                hint = " Введено только одно слово — попробуйте указать полное ФИО."
            if not company:
                hint += " Можно добавить название компании для уточнения."
            return {
                "party": None,
                "confidence": 0.0,
                "evidence": result.get("evidence", ""),
                "error": (
                    f"Подписант «{signer_name}» не найден ни на одной стороне договора.{hint} "
                    "Используйте режим «По роли» для ручного выбора."
                ),
            }

        valid_names = {p["name"] for p in parties}

        # Кейс 2: LLM вернул название — проверяем что оно валидное
        if llm_party not in valid_names:
            # Попытка fuzzy-match через aliases
            mapped = _map_to_known_party(llm_party, parties)
            if mapped:
                result["party"] = mapped
            else:
                # LLM придумал название которого нет в реестре — мягкая ошибка
                return {
                    "party": None,
                    "confidence": 0.0,
                    "evidence": result.get("evidence", ""),
                    "error": (
                        f"Сторона «{llm_party}» не найдена в реестре. "
                        "Добавьте её в parties.json или используйте режим «По роли»."
                    ),
                }

        return {
            "party": result["party"],
            "confidence": float(result.get("confidence", 0.0)),
            "evidence": result.get("evidence", ""),
            "error": None,
        }
    except Exception as e:
        return {"party": None, "confidence": 0.0, "evidence": "",
                "error": f"LLM error: {e}"}


def _get_doc_text(doc, max_chars: int) -> str:
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


def _build_parties_list_str(parties: list[dict]) -> str:
    """Список сторон с aliases для промпта."""
    lines = []
    for p in parties:
        aliases = p.get("aliases", [])
        display = p.get("display") or p["name"]
        if aliases:
            lines.append(f'- "{p["name"]}" (синонимы/языковые варианты: {", ".join(aliases)}; отображение: {display})')
        else:
            lines.append(f'- "{p["name"]}" ({display})')
    return "\n".join(lines)


def _call_llm(doc_text: str, signer_name: str, company: str | None,
              parties: list[dict]) -> dict:
    """Сделать запрос к Claude и распарсить JSON-ответ."""
    from anthropic import Anthropic

    client = Anthropic()
    parties_str = _build_parties_list_str(parties)
    company_line = f'Компания: "{company}"' if company else "Компания: (не указана)"

    prompt = f"""Фрагмент договора (первые символы):
---
{doc_text}
---

Подписант: "{signer_name}"
{company_line}

Доступные стороны договора (выбери ОДНУ из этого списка):
{parties_str}

Задача: определи, на какой стороне договора выступает указанный подписант или компания.

Правила:
- Подписант и компания могут указывать на разные стороны — приоритет AND-совпадение (оба указывают на одну сторону).
- Если совпадение только по одному критерию — допустимо, но снизь confidence.
- Если ни одного совпадения в тексте — confidence = 0, party = null.
- Поле "party" должно ТОЧНО совпадать с одним из ключей в списке выше (русским названием).

Верни ТОЛЬКО JSON без обрамления markdown, без пояснений:
{{"party": "<точное имя из списка или null>", "confidence": <число 0..1>, "evidence": "<цитата из договора, до 200 символов>"}}
"""

    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = (resp.content[0].text or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    data = json.loads(raw)
    if data.get("party") in (None, "null", ""):
        data["party"] = None
    return data


def _map_to_known_party(llm_name: str, parties: list[dict]) -> str | None:
    """Сопоставить ответ LLM с известным ключом через aliases."""
    if not llm_name:
        return None
    needle = llm_name.strip().lower()
    for p in parties:
        candidates = [p["name"]] + p.get("aliases", [])
        if any(c.lower() == needle for c in candidates):
            return p["name"]
        if any(needle in c.lower() or c.lower() in needle for c in candidates):
            return p["name"]
    return None