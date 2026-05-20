"""Авто-пайплайн подписания договора.

pages/5_🤖_Авто_подписание.py

Шаги:
  1. Парсинг документа
  2. Определение языка
  3. Поиск нашей стороны в шапке через LLM
  4. Генерация regex-паттернов через LLM
  5. Поиск мест подписи через finder.py
  6. Превью с чекбоксами
  7. Скачивание + сохранение паттернов

v1.5
"""
import json
import os
import re
import sys
from datetime import datetime
from typing import List, Optional

import streamlit as st
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
SUPPORTED_LANGUAGES = ("ru", "en", "pl")

st.set_page_config(page_title="Авто-подписание — SignFinder", layout="wide")

# ── Auth ─────────────────────────────────────────────────────────────────────
if not st.session_state.get("auth"):
    st.warning("Войдите через главную страницу.")
    st.stop()

st.title("🤖 Авто-подписание")
st.caption("Определи компанию или подписанта в Настройках, после чего загрузи договор — система определит нашу сторону, найдёт места подписи и подпишет.")

# ── Проверка подписи ─────────────────────────────────────────────────────────
from core.storage import read_signature, json_config_exists, read_json, write_json

if "signature_png" not in st.session_state:
    saved = read_signature()
    if saved:
        st.session_state["signature_png"] = saved

sig_png: Optional[bytes] = st.session_state.get("signature_png")
if not sig_png:
    st.error("⚠️ Подпись не загружена. Загрузите PNG подписи в Настройках.")
    st.page_link("pages/4_⚙️_Настройки.py", label="Открыть Настройки", icon="⚙️")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Вспомогательные функции пайплайна
# ══════════════════════════════════════════════════════════════════════════════

def _get_header_text(doc) -> str:
    """Первые 1-3 страницы для шапки (шаг 3)."""
    parts = []
    total = 0
    for i, page in enumerate(doc.pages):
        text = page.text or ""
        parts.append(f"--- Страница {i + 1} ---\n{text}")
        total += len(text)
        if i >= 2 or total >= 3000:
            break
    return "\n".join(parts)[:4000]


def _get_strategic_fragments(doc, markers_block: dict) -> str:
    """Стратегический срез: первая, последняя, якорные страницы, футеры."""
    pages = doc.pages
    n = len(pages)
    anchors = [a.lower() for a in markers_block.get("section_anchors", [])]
    fragments = []

    if pages:
        fragments.append(f"=== ПЕРВАЯ СТРАНИЦА ===\n{pages[0].text or ''}")

    if n > 1:
        fragments.append(f"=== ПОСЛЕДНЯЯ СТРАНИЦА ===\n{pages[-1].text or ''}")

    # Страницы с anchors (кроме первой и последней)
    for i in range(1, n - 1):
        text = (pages[i].text or "").lower()
        if any(a in text for a in anchors):
            fragments.append(f"=== СТРАНИЦА {i + 1} (секция) ===\n{pages[i].text[:2000]}")

    # Футеры всех страниц
    footer_parts = []
    for i, page in enumerate(pages):
        text = (page.text or "").strip()
        footer = text[-200:] if len(text) > 200 else text
        if footer.strip():
            footer_parts.append(f"[стр.{i + 1}] {footer}")
    if footer_parts:
        fragments.append("=== ФУТЕРЫ СТРАНИЦ ===\n" + "\n---\n".join(footer_parts[:20]))

    return "\n\n".join(fragments)[:8000]


def _call_llm_json(prompt: str, max_tokens: int = 1500, capture_key: str | None = None) -> Optional[dict]:
    """Вызвать LLM, распарсить JSON-ответ. Возвращает None при ошибке.
    capture_key: если задан — сохраняет промпт и raw-ответ в session_state под ключами
    debug_prompt_<key> и debug_raw_<key>.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.error("ANTHROPIC_API_KEY не задан.")
        return None
    if capture_key:
        st.session_state[f"debug_prompt_{capture_key}"] = prompt
    client = Anthropic()
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (resp.content[0].text or "").strip()
        if capture_key:
            st.session_state[f"debug_raw_{capture_key}"] = raw
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.MULTILINE).strip()
        raw = re.sub(r"```$", "", raw, flags=re.MULTILINE).strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            raw = m.group(0)
        return json.loads(raw)
    except Exception as e:
        print(f"[auto_pipeline] LLM error: {e}", file=sys.stderr)
        return None


def _run_step3(doc, lang: str) -> Optional[dict]:
    """Шаг 3: определение нашей стороны через LLM."""
    from core.signer_profile import get_aliases_for_language
    from core.markers import get_markers_for_language
    from core.prompts import format_find_our_side

    aliases = get_aliases_for_language(lang)
    markers_block = get_markers_for_language(lang)

    if not aliases["signer"]:
        st.error(
            "❌ Шаг 3: Не задан ни один алиас ФИО подписанта. "
            "Заполните данные в Настройках → Подписант."
        )
        return None

    header = _get_header_text(doc)
    prompt = format_find_our_side(
        header_text=header,
        language=lang,
        company_aliases=aliases["company"],
        signer_aliases=aliases["signer"],
        markers=markers_block,
    )

    result = _call_llm_json(prompt, max_tokens=1500, capture_key="step3")
    if result is None:
        st.error("❌ Шаг 3: LLM не ответил или вернул невалидный JSON.")
        return None

    confidence = float(result.get("confidence", 0))
    our_index = result.get("our_side_index")
    synonyms = result.get("our_side_synonyms") or {}

    if our_index is None or confidence < 0.5:
        if result.get("match_reason") == "none" or confidence < 0.3:
            st.error(
                "❌ Шаг 3: Наша компания/подписант не найдены в шапке договора. "
                "Проверьте Данные подписанта или используйте ручной режим."
            )
        else:
            st.error(
                "❌ Шаг 3: Не удалось однозначно определить нашу сторону. "
                "Найдено несколько совпадений. Используйте ручной режим."
            )
        return None

    return {
        "legal_entity": synonyms.get("legal_entity", ""),
        "roles": synonyms.get("roles", []),
        "signer": synonyms.get("signer", ""),
        "confidence": confidence,
        "match_reason": result.get("match_reason", ""),
        "evidence": result.get("evidence", ""),
        "all_parties": result.get("all_parties", []),
    }


def _run_step4(doc, lang: str, our_side: dict) -> Optional[List[str]]:
    """Шаг 4: генерация regex-паттернов через LLM."""
    from core.markers import get_markers_for_language
    from core.prompts import format_generate_regex

    markers_block = get_markers_for_language(lang)
    fragments = _get_strategic_fragments(doc, markers_block)

    prompt = format_generate_regex(
        legal_entity=our_side["legal_entity"],
        roles=our_side["roles"],
        signer=our_side["signer"],
        language=lang,
        markers_block=markers_block,
        strategic_fragments=fragments,
    )

    result = _call_llm_json(prompt, max_tokens=3000, capture_key="step4")
    if result is None:
        st.error("❌ Шаг 4: LLM не вернул паттерны.")
        _show_step4_debug()
        return None

    raw_patterns = result.get("patterns", [])
    patterns = []
    for item in raw_patterns:
        pat = item.get("pattern", "") if isinstance(item, dict) else str(item)
        pat = pat.strip()
        if pat:
            try:
                re.compile(pat, re.IGNORECASE | re.UNICODE)
                patterns.append(pat)
            except re.error as e:
                print(f"[auto_pipeline] bad pattern '{pat}': {e}", file=sys.stderr)

    if not patterns:
        st.error(
            "❌ Шаг 4: Не удалось сгенерировать паттерны. "
            "Используйте ручной режим."
        )
        _show_step4_debug()
        return None

    return patterns


def _extract_distinctive_tokens(s: str) -> list[str]:
    """Извлекает distinctive токены из строки: имена в кавычках, фамилии.

    Примеры:
      "Общество с ограниченной ответственностью «Стэп интегратор»"
        → ["Стэп интегратор", "Стэп", "интегратор"]
      "Ткачев Сергей Леонидович" → ["Ткачев", "Сергей", "Леонидович"]
      "ИСПОЛНИТЕЛЬ" → ["ИСПОЛНИТЕЛЬ"]
      "не указан" → []
    """
    if not s:
        return []
    sl = s.lower().strip()
    if sl in ("не указан", "не указана", "не указано", "—", "-", "n/a", "na", ""):
        return []

    tokens = []
    # 1. Содержимое кавычек: «Стэп интегратор» / "Acme Inc"
    for m in re.finditer(r'[«"\']([^»"\']+)[»"\']', s):
        inner = m.group(1).strip()
        if len(inner) >= 3:
            tokens.append(inner)
            for w in inner.split():
                if len(w) >= 4:
                    tokens.append(w)

    # 2. Слова с заглавной (имена собственные, фамилии, ROLE-as-UPPER)
    stop = {
        "общество", "ограниченной", "ответственностью", "компания",
        "корпорация", "генеральный", "директор", "лице", "именуем",
        "именуемая", "именуемое", "именуемый", "далее", "стороны",
        "стороне", "договор", "договору", "паспорт", "выдан", "адрес",
    }
    for w in re.findall(r"[А-ЯA-ZЁ][а-яa-zА-ЯA-ZёЁ\-]{3,}", s):
        if w.lower() in stop:
            continue
        tokens.append(w)

    # Дедуп с сохранением порядка
    seen, result = set(), []
    for t in tokens:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            result.append(t)
    return result


def _run_step5(doc, our_side: dict, patterns: List[str]):
    """Шаг 5: поиск мест подписи через finder.py с кастомными паттернами."""
    from core.finder import find_signatures

    # Собираем distinctive синонимы ЧУЖИХ сторон (для отсечения паттернов
    # которые случайно цепляют другую сторону).
    our_entity = (our_side.get("legal_entity") or "").strip()
    our_roles = set(r.strip().lower() for r in our_side.get("roles", []) if r)
    our_signer = (our_side.get("signer") or "").strip()
    our_signer_tokens = set(t.lower() for t in _extract_distinctive_tokens(our_signer))
    our_entity_tokens = set(t.lower() for t in _extract_distinctive_tokens(our_entity))

    other_aliases: list[str] = []
    for p in our_side.get("all_parties", []):
        if not isinstance(p, dict):
            continue
        le = (p.get("legal_entity") or "").strip()
        role = (p.get("role") or "").strip()
        signer = (p.get("signer") or "").strip()

        # пропускаем нашу сторону
        if le and le == our_entity:
            continue

        # роль чужой стороны
        if role and role.lower() not in our_roles:
            other_aliases.append(role)
        # distinctive токены юрлица
        for t in _extract_distinctive_tokens(le):
            if t.lower() not in our_entity_tokens:
                other_aliases.append(t)
        # distinctive токены подписанта
        for t in _extract_distinctive_tokens(signer):
            if t.lower() not in our_signer_tokens:
                other_aliases.append(t)

    # дедуп, выбрасываем слишком короткие
    seen = set()
    other_aliases_clean = []
    for a in other_aliases:
        al = a.lower().strip()
        if len(al) >= 3 and al not in seen:
            seen.add(al)
            other_aliases_clean.append(a)
    other_aliases = other_aliases_clean

    party_dict = {
        "name": our_side["legal_entity"] or "auto",
        "display": our_side["legal_entity"] or "auto",
        "aliases": (
            [our_side["legal_entity"]]
            + our_side.get("roles", [])
            + [our_side["signer"]]
        ),
        "signer": our_side.get("signer", ""),
        "other_aliases": other_aliases,
        "patterns": patterns,
        "notes": "",
    }
    matches = find_signatures(doc, party_dict)

    if not matches:
        st.error(
            "❌ Шаг 5: Паттерны сгенерированы, но не нашли мест подписи. "
            "Используйте ручной режим."
        )
        return None

    return matches


def _show_step4_debug():
    """Показывает debug-блок шага 4 (промпт + raw ответ LLM)."""
    prompt = st.session_state.get("debug_prompt_step4", "")
    raw = st.session_state.get("debug_raw_step4", "")
    if prompt or raw:
        with st.expander("🔍 Debug шаг 4 — промпт и ответ LLM", expanded=True):
            if prompt:
                st.markdown("**Промпт ушедший в LLM:**")
                st.code(prompt, language="text")
            if raw:
                st.markdown("**Raw ответ LLM:**")
                st.code(raw, language="text")


def _build_debug_export() -> dict:
    """Собирает полный debug-трейс из session_state. Работает при любом результате pipeline."""
    from datetime import timezone
    doc = st.session_state.get("auto_doc")
    our_side = st.session_state.get("auto_our_side") or {}
    patterns = st.session_state.get("auto_patterns") or []
    matches = st.session_state.get("auto_matches") or []

    return {
        "version": "1.6",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "doc_info": {
            "filename": st.session_state.get("auto_doc_name", ""),
            "pages": len(doc.pages) if doc else 0,
            "language": st.session_state.get("auto_language", ""),
        },
        "step3_party_detection": {
            "legal_entity": our_side.get("legal_entity", ""),
            "roles": our_side.get("roles", []),
            "signer": our_side.get("signer", ""),
            "confidence": our_side.get("confidence", 0),
            "match_reason": our_side.get("match_reason", ""),
            "evidence": our_side.get("evidence", ""),
            "all_parties": our_side.get("all_parties", []),
            "prompt": st.session_state.get("debug_prompt_step3", ""),
            "raw_llm_response": st.session_state.get("debug_raw_step3", ""),
        },
        "step4_pattern_generation": {
            "patterns": patterns,
            "prompt": st.session_state.get("debug_prompt_step4", ""),
            "raw_llm_response": st.session_state.get("debug_raw_step4", ""),
            "raw_length_chars": len(st.session_state.get("debug_raw_step4", "")),
        },
        "step5_signature_search": {
            "total_found": len(matches),
            "matches": [
                {
                    "id": m.id,
                    "page": m.page,
                    "bbox": list(m.bbox),
                    "pattern": m.pattern,
                    "context": m.context,
                    "confidence": m.confidence,
                }
                for m in matches
            ],
        },
    }


def _apply_template_to_session(template_id: str, doc) -> bool:
    """
    Загружает шаблон, применяет якоря к doc, пишет в session_state.
    Возвращает True если нашли хотя бы 1 место.
    """
    from core.template_storage import load_template, update_usage_stats
    from core.finder import apply_template_anchors

    tpl = load_template(template_id)
    if tpl is None:
        st.error(f"Шаблон {template_id} не найден в реестре.")
        return False

    matches = apply_template_anchors(doc, tpl)
    if not matches:
        st.warning("Шаблон применён, но места подписи не найдены — возможно документ изменился.")
        return False

    st.session_state["auto_matches"] = matches
    st.session_state["auto_active_ids"] = {m.id for m in matches}
    st.session_state["auto_our_side"] = {
        "legal_entity": tpl.synonyms_used.get("legal_entity", ""),
        "roles": tpl.synonyms_used.get("roles", []),
        "signer": tpl.synonyms_used.get("signer", ""),
        "confidence": 1.0,
        "match_reason": "template_match",
        "evidence": f"Применён шаблон: {tpl.name}",
        "all_parties": [],
    }
    st.session_state["auto_patterns"] = [
        a.get("generated_pattern", "") if isinstance(a, dict) else getattr(a, "generated_pattern", "")
        for a in tpl.anchors
    ]
    st.session_state["applied_template_id"] = template_id
    st.session_state["applied_template_name"] = tpl.name

    update_usage_stats(template_id, "applied")
    return True


def _show_template_save_dialog():
    """
    Диалог Часть Д: обновить/версионировать шаблон после ручной доразметки.
    Показывается если пайплайн применил шаблон и оператор мог добавить якоря.
    """
    tpl_id = st.session_state.get("applied_template_id")
    tpl_name = st.session_state.get("applied_template_name", "")
    new_anchors = st.session_state.get("manual_anchors_added", [])

    if not tpl_id or not new_anchors:
        return

    st.info(
        f'Шаблон **"{tpl_name}"** был расширен {len(new_anchors)} новыми якорями. '
        f'Что делать?'
    )
    col_upd, col_new, col_skip = st.columns([1, 1, 1])
    with col_upd:
        if st.button("💾 Обновить шаблон", key="tpl_update_btn"):
            from core.template_storage import add_anchors_to_template
            from dataclasses import asdict
            raw = [asdict(a) if hasattr(a, '__dataclass_fields__') else a for a in new_anchors]
            add_anchors_to_template(tpl_id, raw, increment_version=False)
            st.success(f"Шаблон «{tpl_name}» обновлён.")
            st.session_state.pop("manual_anchors_added", None)
            st.rerun()
    with col_new:
        if st.button("🆕 Создать v2", key="tpl_newver_btn"):
            from core.template_storage import add_anchors_to_template
            from dataclasses import asdict
            raw = [asdict(a) if hasattr(a, '__dataclass_fields__') else a for a in new_anchors]
            new_id = add_anchors_to_template(tpl_id, raw, increment_version=True)
            st.success(f"Создана новая версия шаблона. ID: {new_id}")
            st.session_state.pop("manual_anchors_added", None)
            st.rerun()
    with col_skip:
        if st.button("✗ Не сохранять", key="tpl_skip_btn"):
            st.session_state.pop("manual_anchors_added", None)
            st.rerun()


def _reset_pipeline():
    """Сброс всего состояния пайплайна."""
    for k in [
        "auto_language", "auto_our_side", "auto_patterns",
        "auto_matches", "auto_active_ids", "auto_signed_pdf",
        "debug_prompt_step3", "debug_raw_step3",
        "debug_prompt_step4", "debug_raw_step4",
        "matcher_result", "auto_template_pending",
        "applied_template_id", "applied_template_name",
        "manual_anchors_added",
    ]:
        st.session_state.pop(k, None)


# ══════════════════════════════════════════════════════════════════════════════
# Загрузка файла + запуск пайплайна
# ══════════════════════════════════════════════════════════════════════════════

uploaded = st.file_uploader(
    "Загрузить договор",
    type=["pdf", "docx"],
    key="auto_uploader",
)

if uploaded is not None and st.session_state.get("auto_doc_name") != uploaded.name:
    _reset_pipeline()
    st.session_state["auto_doc_name"] = uploaded.name

    with st.status("Запускаю пайплайн...", expanded=True) as status:

        # ШАГ 1: Парсинг
        st.write("📄 Шаг 1: Парсинг документа...")
        from core.parser import parse_document
        try:
            doc = parse_document(uploaded.getvalue(), uploaded.name)
            st.session_state["auto_doc"] = doc
            st.write(f"✅ Распознано страниц: {len(doc.pages)}")
        except Exception as e:
            status.update(label="❌ Ошибка парсинга", state="error")
            st.error(f"Не удалось распарсить файл: {e}")
            st.stop()

        # ШАГ 2: Язык
        st.write("🌐 Шаг 2: Определение языка...")
        from core.language_detector import detect_language
        lang = detect_language(doc)
        if lang not in SUPPORTED_LANGUAGES:
            status.update(label="❌ Язык не поддерживается", state="error")
            lang_name = lang if lang != "unknown" else "не определён"
            st.error(
                f"Обрабатываем только русские, английские и польские договоры. "
                f"Определён язык: {lang_name}"
            )
            st.stop()
        st.session_state["auto_language"] = lang
        st.write(f"✅ Язык: **{lang}**")

        # ШАГ 0 (v1.8): Поиск похожего шаблона
        st.write("🔎 Поиск похожего шаблона в реестре...")
        try:
            from core.template_matcher import find_matching_templates, log_matching_decision
            matcher_result = find_matching_templates(doc, lang)
            log_matching_decision(matcher_result, uploaded.name)
            st.session_state["matcher_result"] = matcher_result
        except Exception as _me:
            st.warning(f"Матчер недоступен, продолжаю полный пайплайн: {_me}")
            st.session_state["matcher_result"] = None
            matcher_result = None

        # ── Зелёный: пропускаем шаги 3-5 ─────────────────────────────────────
        _run_full = True
        if matcher_result and matcher_result.traffic_light == "green":
            bm = matcher_result.best_match
            st.write(
                f"🟢 Найден шаблон **{bm.template_name}** "
                f"({bm.score:.0%}) — ожидаю подтверждения оператора"
            )
            st.session_state["auto_template_pending"] = True
            _run_full = False
            status.update(
                label=f"🟢 Шаблон «{bm.template_name}» найден — подтвердите ниже",
                state="complete",
            )
        elif matcher_result and matcher_result.best_match:
            bm = matcher_result.best_match
            st.write(
                f"🟡 Близкий шаблон: **{bm.template_name}** ({bm.score:.0%}) — "
                f"запускаю полный анализ"
            )
        else:
            st.write("📄 Шаблонов не найдено — запускаю полный анализ")

        if _run_full:
            # ШАГ 3: Наша сторона
            st.write("🔍 Шаг 3: Определение нашей стороны...")
            our_side = _run_step3(doc, lang)
            if our_side is None:
                status.update(label="❌ Не удалось определить нашу сторону", state="error")
                st.stop()
            st.session_state["auto_our_side"] = our_side
            st.write(
                f"✅ Наша сторона: **{our_side['legal_entity']}** "
                f"/ {our_side['signer']} "
                f"(уверенность: {our_side['confidence']:.0%})"
            )
            with st.expander("🔍 Debug шаг 3 — синонимы из шапки", expanded=False):
                st.markdown(f"**Юрлицо:** `{our_side['legal_entity']}`")
                roles_str = ", ".join(our_side["roles"]) if our_side.get("roles") else "—"
                st.markdown(f"**Роли в договоре:** {roles_str}")
                st.markdown(f"**Подписант:** `{our_side['signer']}`")
                st.markdown(f"**Уверенность:** {our_side['confidence']:.0%} · причина: `{our_side.get('match_reason', '—')}`")
                if our_side.get("evidence"):
                    st.caption(f"Подтверждение: «{our_side['evidence'][:300]}»")
                if our_side.get("all_parties"):
                    st.markdown("**Все стороны найдены в договоре:**")
                    for p in our_side["all_parties"]:
                        st.markdown(f"- `{p}`")
                with st.expander("📋 Промпт шага 3", expanded=False):
                    st.code(st.session_state.get("debug_prompt_step3", "—"), language="text")

            # ШАГ 4: Паттерны
            st.write("⚙️ Шаг 4: Генерация regex-паттернов...")
            patterns = _run_step4(doc, lang, our_side)
            if patterns is None:
                status.update(label="❌ Генерация паттернов не удалась", state="error")
                st.stop()
            st.session_state["auto_patterns"] = patterns
            st.write(f"✅ Сгенерировано паттернов: **{len(patterns)}**")
            with st.expander("🔍 Debug шаг 4 — паттерны и промпт", expanded=False):
                st.markdown("**Сгенерированные паттерны:**")
                for i, p in enumerate(patterns, 1):
                    st.code(p, language="")
                with st.expander("📋 Промпт шага 4", expanded=False):
                    st.code(st.session_state.get("debug_prompt_step4", "—"), language="text")

            # ШАГ 5: Поиск
            st.write("🔎 Шаг 5: Поиск мест подписи...")
            matches = _run_step5(doc, our_side, patterns)
            if matches is None:
                status.update(label="❌ Места подписи не найдены", state="error")
                st.stop()
            st.session_state["auto_matches"] = matches
            st.session_state["auto_active_ids"] = {m.id for m in matches}
            st.write(f"✅ Найдено мест: **{len(matches)}**")
            with st.expander("🔍 Debug шаг 5 — найденные места подписи", expanded=False):
                for m in matches:
                    st.markdown(
                        f"- стр. **{m.page + 1}** · conf `{m.confidence:.2f}` · `{m.context[:100]}`"
                    )
                    st.caption(f"  паттерн: `{m.pattern}` · bbox: `{[round(x, 1) for x in m.bbox]}`")

            status.update(label="✅ Пайплайн завершён — выберите места и скачайте", state="complete")



# ══════════════════════════════════════════════════════════════════════════════
# v1.8: UI светофора — применение шаблона (показывается пока auto_matches нет)
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.get("auto_template_pending") and "auto_matches" not in st.session_state:
    matcher_result = st.session_state.get("matcher_result")
    doc = st.session_state.get("auto_doc")

    if matcher_result and matcher_result.best_match and doc:
        bm = matcher_result.best_match
        candidates = matcher_result.all_candidates or []

        st.divider()
        st.markdown("### 🟢 Найден похожий шаблон")

        with st.container(border=True):
            st.markdown(f"**{bm.template_name}** — совпадение **{bm.score:.0%}**")
            # Детали explanation
            for line in bm.explanation.split(". "):
                if line.strip():
                    icon = "⚠" if "⚠" in line else "✓"
                    st.caption(f"{icon} {line.strip().lstrip('⚠').strip()}")

            # Статистика применений из шаблона
            from core.template_storage import load_template
            tpl_meta = load_template(bm.template_id)
            if tpl_meta:
                stats = tpl_meta.usage_stats or {}
                applied = stats.get("times_applied", 0)
                confirmed = stats.get("times_confirmed", 0)
                if applied > 0:
                    st.caption(f"ℹ Применялся {applied} раз, подтверждён {confirmed} раз")

        # Показать всех кандидатов если их больше 1
        if len(candidates) > 1:
            with st.expander(f"Показать все кандидаты ({len(candidates)})", expanded=False):
                for c in candidates:
                    st.markdown(f"- **{c.template_name}** — {c.score:.0%} | {c.explanation}")

        col_apply, col_check, col_full = st.columns([1, 1, 1])
        with col_apply:
            if st.button("▶ Применить шаблон", type="primary", key="tpl_apply_btn"):
                with st.spinner("Применяю якоря шаблона..."):
                    ok = _apply_template_to_session(bm.template_id, doc)
                if ok:
                    st.session_state.pop("auto_template_pending", None)
                    st.success(f"Шаблон «{bm.template_name}» применён. Результаты ниже.")
                    st.rerun()
        with col_check:
            if st.button("▶ Применить с проверкой", key="tpl_apply_check_btn"):
                with st.spinner("Применяю якоря шаблона..."):
                    ok = _apply_template_to_session(bm.template_id, doc)
                if ok:
                    st.session_state["require_review"] = True
                    st.session_state.pop("auto_template_pending", None)
                    st.rerun()
        with col_full:
            if st.button("✗ Полный анализ", key="tpl_skip_full_btn"):
                st.session_state.pop("auto_template_pending", None)
                # Форсируем запуск полного пайплайна сбросом doc_name
                st.session_state["force_full_pipeline"] = True
                st.rerun()

elif "matcher_result" in st.session_state and not st.session_state.get("auto_template_pending"):
    matcher_result = st.session_state.get("matcher_result")
    # Жёлтый с кандидатом — информационная плашка (пайплайн уже отработал)
    if (matcher_result and matcher_result.best_match
            and matcher_result.traffic_light == "yellow"
            and "auto_matches" not in st.session_state):
        bm = matcher_result.best_match
        candidates = matcher_result.all_candidates or []
        collision = len(candidates) >= 2 and abs(candidates[0].score - candidates[1].score) <= 0.05

        st.divider()
        if collision:
            st.markdown("### 🟡 Найдено несколько похожих шаблонов")
            for c in candidates[:3]:
                st.caption(f"• {c.template_name} ({c.score:.0%})")
            st.info("Рекомендуется запустить полный анализ.")
        else:
            st.markdown(f"### 🟡 Близкий шаблон: «{bm.template_name}» ({bm.score:.0%})")
            st.caption(bm.explanation)


# ── Принудительный запуск полного пайплайна (нажали «Полный анализ» на зелёном) ──
if st.session_state.pop("force_full_pipeline", False):
    doc = st.session_state.get("auto_doc")
    lang = st.session_state.get("auto_language")
    if doc and lang:
        with st.status("Запускаю полный анализ...", expanded=True) as status2:
            st.write("🔍 Шаг 3: Определение нашей стороны...")
            our_side = _run_step3(doc, lang)
            if our_side is None:
                status2.update(label="❌ Не удалось определить сторону", state="error")
                st.stop()
            st.session_state["auto_our_side"] = our_side
            st.write(f"✅ {our_side['legal_entity']} / {our_side['signer']}")

            st.write("⚙️ Шаг 4: Генерация паттернов...")
            patterns = _run_step4(doc, lang, our_side)
            if patterns is None:
                status2.update(label="❌ Генерация паттернов не удалась", state="error")
                st.stop()
            st.session_state["auto_patterns"] = patterns
            st.write(f"✅ Паттернов: {len(patterns)}")

            st.write("🔎 Шаг 5: Поиск мест подписи...")
            matches = _run_step5(doc, our_side, patterns)
            if matches is None:
                status2.update(label="❌ Места подписи не найдены", state="error")
                st.stop()
            st.session_state["auto_matches"] = matches
            st.session_state["auto_active_ids"] = {m.id for m in matches}
            st.write(f"✅ Найдено мест: {len(matches)}")
            status2.update(label="✅ Анализ завершён", state="complete")
        st.rerun()


# ── Кнопка экспорта debug JSON — всегда доступна после запуска пайплайна ──────
if any(st.session_state.get(k) for k in (
    "debug_prompt_step3", "debug_prompt_step4", "auto_our_side", "auto_patterns"
)):
    st.divider()
    col_exp, _ = st.columns([2, 4])
    with col_exp:
        export = _build_debug_export()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = st.session_state.get("auto_doc_name", "doc").rsplit(".", 1)[0]
        st.download_button(
            "📥 Экспорт debug JSON (для анализа)",
            data=json.dumps(export, ensure_ascii=False, indent=2),
            file_name=f"signfinder_debug_{base}_{ts}.json",
            mime="application/json",
            key="dl_debug_always",
            help="Полный трейс: промпты, raw LLM-ответы, паттерны, найденные места",
        )


# ══════════════════════════════════════════════════════════════════════════════
# Шаг 6: Превью с чекбоксами
# ══════════════════════════════════════════════════════════════════════════════

if "auto_matches" in st.session_state:
    matches = st.session_state["auto_matches"]
    active_ids: set = st.session_state.get("auto_active_ids", {m.id for m in matches})
    doc = st.session_state["auto_doc"]
    our_side = st.session_state["auto_our_side"]
    patterns = st.session_state["auto_patterns"]
    lang = st.session_state["auto_language"]

    st.divider()
    st.subheader(f"6️⃣ Найдено мест подписи: {len(matches)}")

    col_all, col_none, _ = st.columns([1, 1, 4])
    with col_all:
        if st.button("✅ Все", key="check_all"):
            st.session_state["auto_active_ids"] = {m.id for m in matches}
            st.rerun()
    with col_none:
        if st.button("☐ Снять все", key="uncheck_all"):
            st.session_state["auto_active_ids"] = set()
            st.rerun()

    st.caption("Снимите чекбокс чтобы исключить место из подписания.")

    # Группируем по страницам
    pages_with_matches: dict = {}
    for m in matches:
        pages_with_matches.setdefault(m.page, []).append(m)

    new_active_ids = set(active_ids)
    changed = False

    from core.preview import render_page_with_highlights

    for page_num in sorted(pages_with_matches.keys()):
        page_matches = pages_with_matches[page_num]
        st.markdown(f"**Страница {page_num + 1}**")

        # Чекбоксы
        for m in page_matches:
            is_active = m.id in active_ids
            checked = st.checkbox(
                f"Место {m.id} — `{m.context[:60]}...`" if len(m.context) > 60 else f"Место {m.id} — `{m.context}`",
                value=is_active,
                key=f"cb_{m.id}",
            )
            if checked != is_active:
                changed = True
            if checked:
                new_active_ids.add(m.id)
            else:
                new_active_ids.discard(m.id)

        # Превью страницы — помечаем неактивные как excluded
        preview_matches = []
        for m in page_matches:
            import copy
            pm = copy.copy(m)
            pm.operator_excluded = (m.id not in new_active_ids)
            preview_matches.append(pm)

        try:
            img_bytes = render_page_with_highlights(
                doc.pdf_bytes, page_num, preview_matches, scale=1.2
            )
            st.image(img_bytes, use_container_width=True)
        except Exception as e:
            st.warning(f"Превью недоступно: {e}")

    if changed:
        st.session_state["auto_active_ids"] = new_active_ids
        st.rerun()

    # ── Детали: что нашли ─────────────────────────────────────────────────────
    with st.expander("📋 Детали: найденная сторона и паттерны", expanded=False):
        st.markdown(f"""
**Юрлицо:** {our_side['legal_entity']}  
**Роли:** {', '.join(our_side['roles'])}  
**Подписант:** {our_side['signer']}  
**Уверенность:** {our_side['confidence']:.0%} ({our_side['match_reason']})  
**Язык:** {lang}
        """)
        if our_side.get("evidence"):
            st.caption(f"Подтверждение: «{our_side['evidence'][:200]}»")
        st.markdown("**Паттерны:**")
        for i, p in enumerate(patterns, 1):
            st.code(p, language="")

    # ── Шаг 7: Скачивание ────────────────────────────────────────────────────
    st.divider()
    st.subheader("7️⃣ Скачать и сохранить")

    active_count = len(new_active_ids)
    if active_count == 0:
        st.warning("Нет выбранных мест подписи.")
    else:
        st.caption(f"Будет подписано: {active_count} из {len(matches)} мест.")

        if st.button("⬇ Скачать подписанный PDF", type="primary", disabled=(active_count == 0)):
            from core.overlay import apply_signature
            import copy

            final_matches = []
            for m in matches:
                cm = copy.copy(m)
                cm.operator_excluded = (m.id not in new_active_ids)
                final_matches.append(cm)

            try:
                signed_pdf = apply_signature(doc.pdf_bytes, final_matches, sig_png)
                st.session_state["auto_signed_pdf"] = signed_pdf
            except Exception as e:
                st.error(f"Ошибка наложения подписи: {e}")

        if "auto_signed_pdf" in st.session_state:
            fname = uploaded.name if uploaded else "signed.pdf"
            stem = fname.rsplit(".", 1)[0]
            st.download_button(
                label="💾 Сохранить PDF",
                data=st.session_state["auto_signed_pdf"],
                file_name=f"{stem}_signed.pdf",
                mime="application/pdf",
                key="dl_signed",
            )

        # ── Сохранение паттернов в parties.json ──────────────────────────────
        st.divider()
        auto_party_name = (
            f"pipelineAuto1_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{lang}"
        )
        st.caption(
            f"Сохранить сгенерированные паттерны в реестр сторон?\n\n"
            f"Имя: `{auto_party_name}`"
        )

        col_save_p, col_skip_p, _ = st.columns([1, 1, 3])
        with col_save_p:
            if st.button("💾 Сохранить паттерны", key="save_patterns_btn"):
                try:
                    from core.pattern_extractor import merge_patterns_into_json

                    parties_data = (
                        read_json("parties.json")
                        if json_config_exists("parties.json")
                        else {"version": "2.0", "parties": {}}
                    )
                    added = merge_patterns_into_json(parties_data, auto_party_name, lang, patterns)

                    # Добавляем aliases
                    aliases_list = list(filter(None, [
                        our_side.get("legal_entity"),
                        *our_side.get("roles", []),
                        our_side.get("signer"),
                    ]))
                    parties_data["parties"][auto_party_name]["languages"][lang]["aliases"] = aliases_list

                    backup = write_json("parties.json", parties_data)
                    st.success(f"Сохранено {added} паттернов. Бэкап: {backup}")
                except Exception as e:
                    st.error(f"Ошибка: {e}")
        with col_skip_p:
            if st.button("Пропустить", key="skip_patterns_btn"):
                st.info("Паттерны не сохранены.")

        # ── v1.8: подтверждение/отклонение шаблона + диалог обновления ─────────
        tpl_id = st.session_state.get("applied_template_id")
        if tpl_id:
            st.divider()
            st.caption(f"Шаблон «{st.session_state.get('applied_template_name', tpl_id)}» был применён автоматически.")
            col_conf, col_rej = st.columns([1, 1])
            with col_conf:
                if st.button("👍 Подтвердить шаблон", key="tpl_confirm_btn"):
                    from core.template_storage import update_usage_stats
                    update_usage_stats(tpl_id, "confirmed")
                    st.success("Шаблон помечен как успешный.")
            with col_rej:
                if st.button("👎 Отклонить шаблон", key="tpl_reject_btn"):
                    from core.template_storage import update_usage_stats
                    update_usage_stats(tpl_id, "rejected")
                    st.warning("Шаблон помечен как неподходящий.")

        # Диалог обновления шаблона если были ручные якоря
        _show_template_save_dialog()
