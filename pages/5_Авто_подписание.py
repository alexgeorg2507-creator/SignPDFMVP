"""Авто подписание — автоматический pipeline поиска мест подписи.

Streamlit multi-page: pages/5_✍️_Авто_подписание.py
v1.6: debug-трейс пайплайна, экспорт JSON для анализа.
"""
import copy
import json
import os
import re
import time
from datetime import datetime, timezone

import streamlit as st

from core.auth import check_access_code
from core.finder import parse_parties_json, find_signatures
from core.parser import parse_document
from core.pattern_extractor import extract_patterns
from core.preview import render_page_with_highlights
from core.storage import json_config_exists, read_json

st.set_page_config(page_title="Авто подписание — SignFinder", layout="wide")

if not st.session_state.get("auth"):
    st.warning("Войдите через главную страницу.")
    st.stop()

st.title("✍️ Авто подписание")
st.caption("Pipeline: парсинг → язык → определение стороны → паттерны → места подписи")
st.caption("v1.6 · с debug-трейсом и экспортом для анализа")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_parties(lang: str | None = None) -> list[dict]:
    try:
        if json_config_exists("parties.json"):
            return parse_parties_json(read_json("parties.json"), lang)
        return []
    except Exception as e:
        st.error(f"Ошибка загрузки parties.json: {e}")
        return []


def _auto_detect_our_party(doc, parties_list: list[dict], language: str) -> dict:
    """Автоопределение нашей стороны из шапки договора через LLM."""
    _empty = {"party": None, "confidence": 0.0, "synonyms": [],
              "reasoning": "", "prompt": "", "raw_response": ""}

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {**_empty, "error": "ANTHROPIC_API_KEY не задан"}

    from anthropic import Anthropic

    # Шапка = первые 3000 символов из первых 2 страниц
    header = "\n".join(
        (p.text or "") for p in doc.pages[:2]
    )[:3000]

    if not header.strip():
        return {**_empty, "error": "Пустой текст документа"}

    lines = []
    for p in parties_list:
        aliases = p.get("aliases", [])
        display = p.get("display") or p["name"]
        if aliases:
            lines.append(
                f'- "{p["name"]}" (алиасы: {", ".join(aliases)}; отображение: {display})'
            )
        else:
            lines.append(f'- "{p["name"]}" ({display})')
    parties_str = "\n".join(lines) if lines else "(список сторон пуст)"

    lang_hint = {"ru": "русском", "en": "английском", "pl": "польском"}.get(language, language)

    prompt = f"""Анализируй шапку договора на {lang_hint} языке. Найди НАШУ сторону — ту которая подписывает договор.

Шапка договора (первые символы):
---
{header}
---

Реестр сторон системы:
{parties_str}

Задачи:
1. Определи которая из сторон реестра является НАШЕЙ стороной в этом договоре
2. Найди ВСЕ варианты названия нашей стороны в тексте (полное наименование, сокращения, ФИО, должность)
3. Верни ТОЛЬКО JSON без markdown и пояснений:
{{"party": "<точное имя из реестра или null>", "confidence": <число 0..1>, "synonyms": ["вариант1", "вариант2"], "reasoning": "<краткое объяснение до 200 символов>"}}"""

    client = Anthropic()
    raw = ""
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (resp.content[0].text or "").strip()
        raw_clean = re.sub(r"^```(?:json)?", "", raw).strip()
        raw_clean = re.sub(r"```$", "", raw_clean).strip()
        data = json.loads(raw_clean)
        if data.get("party") in (None, "null", ""):
            data["party"] = None
        return {
            "party": data.get("party"),
            "confidence": float(data.get("confidence", 0.0)),
            "synonyms": data.get("synonyms", []),
            "reasoning": data.get("reasoning", ""),
            "prompt": prompt,
            "raw_response": raw,
            "error": None,
        }
    except Exception as e:
        return {**_empty, "prompt": prompt, "raw_response": raw,
                "error": f"LLM error: {e}"}


def _reset_pipeline():
    for k in ("pipe_doc", "pipe_doc_name", "pipe_party_result",
              "pipe_pattern_result", "pipe_matches", "pipe_matches_elapsed"):
        st.session_state.pop(k, None)


# ── ШАГ 1: Документ ───────────────────────────────────────────────────────────
st.divider()
st.subheader("📄 Шаг 1: Парсинг документа...")

uploaded = st.file_uploader(
    "Загрузить договор", type=["pdf", "docx"], key="auto_sign_uploader"
)

if uploaded:
    if st.session_state.get("pipe_doc_name") != uploaded.name:
        _reset_pipeline()
        with st.spinner(f"Парсинг {uploaded.name}..."):
            try:
                doc = parse_document(uploaded.getvalue(), uploaded.name)
                st.session_state["pipe_doc"] = doc
                st.session_state["pipe_doc_name"] = uploaded.name
                st.rerun()
            except Exception as e:
                st.error(f"Ошибка парсинга: {e}")
                st.stop()

if "pipe_doc" not in st.session_state:
    st.stop()

doc = st.session_state["pipe_doc"]
st.success(f"✅ Распознано страниц: {len(doc.pages)}")

# ── ШАГ 2: Язык ───────────────────────────────────────────────────────────────
st.divider()
st.subheader("🌐 Шаг 2: Определение языка...")
st.success(f"✅ Язык: **{doc.language}**")

# ── ШАГ 3: Определение нашей стороны ─────────────────────────────────────────
st.divider()
st.subheader("🔵 Шаг 3: Определение нашей стороны...")

parties_list = _load_parties(doc.language)
if not parties_list:
    st.error("parties.json пуст. Настройте стороны в разделе Шаблоны.")
    st.stop()

if "pipe_party_result" not in st.session_state:
    with st.spinner("LLM анализирует шапку договора..."):
        t0 = time.time()
        r = _auto_detect_our_party(doc, parties_list, doc.language)
        r["elapsed"] = round(time.time() - t0, 1)
        st.session_state["pipe_party_result"] = r
    st.rerun()

party_result = st.session_state["pipe_party_result"]

# ── Debug: синонимы и промпт шага 3 ──────────────────────────────────────────
synonyms = party_result.get("synonyms", [])
with st.expander(
    f"🔍 Debug шаг 3 — синонимы из шапки ({len(synonyms)})", expanded=False
):
    st.markdown("**Синонимы найдены в шапке:**")
    if synonyms:
        for s in synonyms:
            st.markdown(f"- `{s}`")
    else:
        st.caption("Синонимы не найдены")
    if party_result.get("reasoning"):
        st.caption(f"**Reasoning LLM:** {party_result['reasoning']}")
    with st.expander("📋 Промпт для определения стороны", expanded=False):
        st.code(party_result.get("prompt", "—"), language="text")

if party_result.get("error"):
    st.error(f"❌ {party_result['error']}")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Повторить автоопределение"):
            st.session_state.pop("pipe_party_result", None)
            st.session_state.pop("pipe_pattern_result", None)
            st.session_state.pop("pipe_matches", None)
            st.rerun()
    with col2:
        party_manual = st.selectbox(
            "Или выберите сторону вручную",
            [p["name"] for p in parties_list],
            key="party_manual_err",
        )
    if st.button("▶ Продолжить с выбранной стороной", key="party_manual_btn_err"):
        party_result.update({"party": party_manual, "confidence": 0.7, "error": None})
        st.session_state["pipe_party_result"] = party_result
        st.rerun()
    st.stop()

party_name = party_result.get("party")
confidence = party_result.get("confidence", 0.0)

if not party_name or confidence < 0.5:
    st.warning(f"⚠ Не удалось определить сторону уверенно (confidence: {confidence:.0%})")
    party_manual = st.selectbox(
        "Выберите сторону вручную", [p["name"] for p in parties_list], key="party_manual_low"
    )
    if st.button("▶ Продолжить с выбранной стороной", key="party_manual_btn_low"):
        party_result.update({"party": party_manual, "confidence": 0.7})
        st.session_state["pipe_party_result"] = party_result
        st.rerun()
    st.stop()

synonyms_str = " / ".join(synonyms[:2]) if synonyms else "—"
st.success(
    f"✅ Наша сторона: **{party_name}** / {synonyms_str} (уверенность: {confidence:.0%})"
)

col_retry, _ = st.columns([1, 3])
with col_retry:
    if st.button("🔄 Переопределить сторону"):
        for k in ("pipe_party_result", "pipe_pattern_result", "pipe_matches"):
            st.session_state.pop(k, None)
        st.rerun()

# ── ШАГ 4: Генерация паттернов ────────────────────────────────────────────────
st.divider()
st.subheader("⚙️ Шаг 4: Генерация regex-паттернов...")

if "pipe_pattern_result" not in st.session_state:
    with st.spinner("LLM генерирует паттерны..."):
        t0 = time.time()
        pr = extract_patterns(doc, party_name, doc.language)
        pr["elapsed"] = round(time.time() - t0, 1)
        st.session_state["pipe_pattern_result"] = pr
    st.rerun()

pattern_result = st.session_state["pipe_pattern_result"]
patterns = pattern_result.get("patterns", [])

# ── Debug: промпт и паттерны шага 4 ──────────────────────────────────────────
with st.expander(
    f"🔍 Debug шаг 4 — промпт для генерации паттернов", expanded=False
):
    prompt_used = pattern_result.get("prompt", "")
    if prompt_used:
        st.code(prompt_used, language="text")
    else:
        st.caption("Промпт не захвачен")

if patterns:
    with st.expander(f"🔍 Debug шаг 4 — сгенерированные паттерны ({len(patterns)})", expanded=False):
        for i, p in enumerate(patterns, 1):
            st.markdown(f"`{i}.` `{p}`")

if pattern_result.get("error"):
    st.error(f"❌ Шаг 4: {pattern_result['error']}")
    if st.button("🔄 Повторить генерацию паттернов"):
        st.session_state.pop("pipe_pattern_result", None)
        st.session_state.pop("pipe_matches", None)
        st.rerun()
    st.stop()
else:
    st.success(f"✅ Сгенерировано паттернов: {len(patterns)}")

# ── ШАГ 5: Поиск мест подписи ────────────────────────────────────────────────
st.divider()
st.subheader("🔍 Шаг 5: Поиск мест подписи...")

if "pipe_matches" not in st.session_state:
    party_obj = next((p for p in parties_list if p["name"] == party_name), None)
    # Мержим сгенерированные паттерны к существующим
    if party_obj:
        party_obj_run = copy.deepcopy(party_obj)
        existing_patterns = party_obj_run.get("patterns", [])
        combined = list(dict.fromkeys(existing_patterns + patterns))  # порядок + дедупликация
        party_obj_run["patterns"] = combined
    else:
        party_obj_run = {"name": party_name, "patterns": patterns}

    with st.spinner("Ищу по паттернам..."):
        t0 = time.time()
        try:
            matches = find_signatures(doc, party_obj_run)
            st.session_state["pipe_matches"] = matches
            st.session_state["pipe_matches_elapsed"] = round(time.time() - t0, 1)
            st.session_state["pipe_patterns_used"] = party_obj_run.get("patterns", [])
        except Exception as e:
            st.error(f"Ошибка поиска: {e}")
            st.stop()
    st.rerun()

matches = st.session_state["pipe_matches"]
confirmed = [m for m in matches if m.status == "candidate"]
rejected = [m for m in matches if m.status in ("rejected_by_llm", "decorative")]
elapsed = st.session_state.get("pipe_matches_elapsed", 0)

# ── Debug: найденные места шага 5 ────────────────────────────────────────────
with st.expander(
    f"🔍 Debug шаг 5 — найденные места ({len(confirmed)} подтверждено, {len(rejected)} отклонено)",
    expanded=len(confirmed) > 0,
):
    if confirmed:
        st.markdown("**Подтверждённые:**")
        for m in confirmed:
            st.markdown(
                f"- стр. **{m.page + 1}** · conf `{m.confidence:.2f}` · `{m.context[:100]}`"
            )
            st.caption(f"  паттерн: `{m.pattern}` · bbox: `{[round(x, 1) for x in m.bbox]}`")
    if rejected:
        st.markdown("**Отклонённые:**")
        for m in rejected:
            st.markdown(f"- стр. **{m.page + 1}** · статус: `{m.status}` · `{m.context[:80]}`")

if not confirmed:
    st.warning("⚠ Места подписи не найдены.")
else:
    col1, col2, col3 = st.columns(3)
    col1.metric("Найдено", len(confirmed))
    col2.metric("Отклонено", len(rejected))
    col3.metric("Время", f"{elapsed} с")

    st.markdown("##### Предпросмотр")
    for page_num in sorted({m.page for m in confirmed}):
        page_matches = [m for m in confirmed if m.page == page_num]
        try:
            png = render_page_with_highlights(doc.pdf_bytes, page_num, page_matches)
            st.image(png, caption=f"Страница {page_num + 1}")
        except Exception as e:
            st.caption(f"Ошибка рендера стр. {page_num + 1}: {e}")

if st.button("🔄 Повторить поиск мест подписи"):
    st.session_state.pop("pipe_matches", None)
    st.session_state.pop("pipe_matches_elapsed", None)
    st.rerun()

# ── ЭКСПОРТ DEBUG JSON ────────────────────────────────────────────────────────
st.divider()
st.subheader("📥 Экспорт для анализа")

export_data = {
    "version": "1.6",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "doc_info": {
        "filename": doc.filename,
        "pages": len(doc.pages),
        "language": doc.language,
    },
    "step3_party_detection": {
        "party": party_result.get("party"),
        "confidence": party_result.get("confidence"),
        "synonyms_found": party_result.get("synonyms", []),
        "reasoning": party_result.get("reasoning", ""),
        "prompt": party_result.get("prompt", ""),
        "raw_llm_response": party_result.get("raw_response", ""),
        "elapsed_sec": party_result.get("elapsed", 0),
    },
    "step4_pattern_generation": {
        "patterns_generated": pattern_result.get("patterns", []),
        "patterns_combined": st.session_state.get("pipe_patterns_used", []),
        "prompt": pattern_result.get("prompt", ""),
        "raw_llm_response": pattern_result.get("raw_response", ""),
        "error": pattern_result.get("error"),
        "elapsed_sec": pattern_result.get("elapsed", 0),
    },
    "step5_signature_search": {
        "total_confirmed": len(confirmed),
        "total_rejected": len(rejected),
        "elapsed_sec": elapsed,
        "confirmed_matches": [
            {
                "id": m.id,
                "page": m.page,
                "bbox": list(m.bbox),
                "pattern": m.pattern,
                "context": m.context,
                "confidence": m.confidence,
                "status": m.status,
            }
            for m in confirmed
        ],
        "rejected_matches": [
            {
                "id": m.id,
                "page": m.page,
                "pattern": m.pattern,
                "context": m.context[:200],
                "status": m.status,
            }
            for m in rejected
        ],
    },
}

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
base_name = doc.filename.rsplit(".", 1)[0]

st.download_button(
    "📥 Скачать debug JSON",
    data=json.dumps(export_data, ensure_ascii=False, indent=2),
    file_name=f"signfinder_debug_{base_name}_{ts}.json",
    mime="application/json",
    help="Полный трейс пайплайна: промпты, ответы LLM, паттерны, места подписи",
)

st.divider()
st.caption("SignFinder MVP v1.6 — Авто подписание")
