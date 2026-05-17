"""SignFinder MVP 1.4.2 — LLM-fallback для документов без паттернов"""
import json
import time

import streamlit as st

from core.auth import check_access_code
from core.storage import (
    read_md, write_md, read_json, write_json, json_config_exists,
    read_signature, write_signature, delete_signature,
)
from core.parser import parse_document
from core.finder import parse_parties_json, parse_parties_md, find_signatures, find_signatures_smart
from core.corrector import apply_corrections
from core.validator import validate_with_llm
from core.preview import render_page_with_highlights
from core.overlay import apply_signature
from core.party_resolver import resolve_party
from core.language_detector import detect_language


st.set_page_config(page_title="SignFinder MVP", layout="wide")


# ── AUTH GATE ────────────────────────────────────────────────────────────────
if "auth" not in st.session_state:
    st.session_state["auth"] = False

if not st.session_state["auth"]:
    st.title("🔐 SignFinder MVP")
    code = st.text_input("Код доступа", type="password")
    if st.button("Войти"):
        if check_access_code(code):
            st.session_state["auth"] = True
            st.rerun()
        else:
            st.error("Неверный код")
    st.stop()

# ── Автозагрузка сохранённой подписи (один раз за сессию) ────────────────────
if "signature_autoloaded" not in st.session_state:
    st.session_state["signature_autoloaded"] = True
    if "signature_png" not in st.session_state:
        saved = read_signature()
        if saved:
            st.session_state["signature_png"] = saved


def _run_search(doc, parties_list, selected_party, use_llm, use_llm_fallback=False):
    party_obj = next((p for p in parties_list if p["name"] == selected_party), None)
    if not party_obj:
        st.error("Сторона не найдена в конфиге")
        return
    with st.spinner(f"Поиск для стороны '{selected_party}'..."):
        try:
            t0 = time.time()
            
            # v1.4.2: умный поиск с LLM-fallback
            if use_llm_fallback:
                matches, source = find_signatures_smart(doc, party_obj, min_expected=1, llm_fallback=True)
                st.session_state["search_source"] = source
            else:
                matches = find_signatures(doc, party_obj)
                st.session_state["search_source"] = "regex"
            
            matches = apply_corrections(matches)
            if use_llm:
                with st.spinner("LLM-валидация через Claude..."):
                    matches = validate_with_llm(matches, selected_party)
            elapsed = round(time.time() - t0, 1)
            st.session_state["matches"] = matches
            st.session_state["search_elapsed"] = elapsed
            st.session_state["search_config"] = {
                "mode": "По роли",
                "party": selected_party,
                "use_llm_validation": use_llm,
                "use_llm_fallback": use_llm_fallback,
            }
            st.session_state.pop("signed_pdf", None)
        except Exception as e:
            st.error(f"Ошибка поиска: {e}")


def _handle_resolved_party(doc, parties_list, result, use_llm, use_llm_fallback):
    if result.get("error") or not result.get("party"):
        return
    if result["confidence"] < 0.5:
        return
    _run_search(doc, parties_list, result["party"], use_llm, use_llm_fallback)

st.title("📝 SignFinder MVP")
st.caption("Поиск мест подписи в договорах PDF / DOCX")

# ── ДИАГНОСТИКА (sidebar) ─────────────────────────────────────────────────────
with st.sidebar:
    with st.expander("🔧 Debug: session_state", expanded=False):
        diag_keys = ["last_uploaded_name", "resolved_party", "search_triggered_for",
                     "search_source", "search_elapsed", "similar_templates"]
        for k in diag_keys:
            v = st.session_state.get(k, "—")
            st.caption(f"`{k}`: {v}")
        if st.button("🗑 Очистить session", key="debug_clear"):
            for k in list(st.session_state.keys()):
                if k not in ("auth", "signature_png", "signature_autoloaded"):
                    st.session_state.pop(k)
            st.rerun()


# ── ПОДПИСЬ ──────────────────────────────────────────────────────────────────
sig_ready = "signature_png" in st.session_state

if not sig_ready:
    col1, col2 = st.columns([3, 1], vertical_alignment="center")
    with col1:
        st.warning("⚠️ Подпись не загружена.")
    with col2:
        st.page_link("pages/4____Настройки.py", label="Загрузить", icon="⚙️")
else:
    col1, col2 = st.columns([3, 1], vertical_alignment="center")
    with col1:
        st.success("✅ Подпись загружена")
    with col2:
        st.page_link("pages/4____Настройки.py", label="Изменить", icon="⚙️")


# ── ШАГ 1: ДОКУМЕНТ ──────────────────────────────────────────────────────────
st.divider()
st.subheader("1️⃣ Документ")

if not sig_ready:
    st.info("Сначала загрузите подпись (шаг 1).")
else:
    def _load_parties(doc_language: str | None = None) -> list[dict]:
        try:
            if json_config_exists("parties.json"):
                data = read_json("parties.json")
                return parse_parties_json(data, doc_language)
            else:
                md = read_md("parties.md")
                return parse_parties_md(md)
        except Exception as e:
            st.error(f"Ошибка загрузки конфига сторон: {e}")
            return []

    uploaded = st.file_uploader(
        "Загрузить договор",
        type=["pdf", "docx"],
        key="doc_uploader",
    )

    if uploaded is not None:
        if st.session_state.get("last_uploaded_name") != uploaded.name:
            with st.spinner(f"Парсинг {uploaded.name}..."):
                try:
                    parsed = parse_document(uploaded.getvalue(), uploaded.name)
                    st.session_state["parsed_doc"] = parsed
                    st.session_state["parsed_doc_language"] = parsed.language
                    st.session_state["last_uploaded_name"] = uploaded.name
                    st.session_state.pop("matches", None)
                    st.session_state.pop("signed_pdf", None)
                    st.session_state.pop("resolved_party", None)
                    
                    # v1.4.3: Поиск похожих шаблонов
                    from core.template_matcher import compute_fingerprint, find_similar_templates
                    try:
                        fingerprint = compute_fingerprint(parsed)
                        similar = find_similar_templates(fingerprint, parsed.language, threshold=0.85)
                        st.session_state["similar_templates"] = similar
                    except Exception:
                        st.session_state["similar_templates"] = []
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка парсинга: {e}")
                    st.session_state.pop("parsed_doc", None)

    if "parsed_doc" in st.session_state:
        doc = st.session_state["parsed_doc"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Файл", doc.filename)
        c2.metric("Страниц", len(doc.pages))
        c3.metric("Язык", doc.language.upper())

        # v1.4.3: Баннер похожего шаблона
        similar = st.session_state.get("similar_templates", [])
        if similar:
            template, similarity = similar[0]
            st.info(
                f"📋 Найден похожий шаблон: **{template.get('name')}** "
                f"(совпадение {similarity*100:.0f}%). "
                f"Применить сохранённые места подписи?"
            )
            col_apply, col_skip = st.columns([1, 3])
            with col_apply:
                if st.button("✅ Применить шаблон", key="apply_template_btn"):
                    from core.template_applier import apply_template_simple
                    matches = apply_template_simple(doc, template)
                    st.session_state["matches"] = matches
                    st.session_state["search_source"] = "template"
                    st.session_state.pop("similar_templates", None)
                    st.rerun()
            with col_skip:
                if st.button("⏭ Пропустить", key="skip_template_btn"):
                    st.session_state.pop("similar_templates", None)
                    st.rerun()
            st.divider()

        with st.expander("👁 Превью первой страницы", expanded=False):
            try:
                thumb = render_page_with_highlights(doc.pdf_bytes, 0, [])
                st.image(thumb, caption="Страница 1", width=500)
            except Exception as e:
                st.caption(f"Не удалось отрисовать стр. 1: {e}")

        st.markdown("##### Режим поиска")
        mode = st.radio(
            "Как искать сторону договора",
            options=["По роли", "По подписанту"],
            horizontal=True,
            key="search_mode",
        )

        parties_list = _load_parties(doc.language)

        # ───────────────────────────────────────────────────────────────────────
        # Режим: По роли
        # ───────────────────────────────────────────────────────────────────────
        if mode == "По роли":
            if not parties_list:
                st.error("parties.json пуст или не загрузился.")
                st.stop()

            col_party, col_llm, col_fallback = st.columns([2, 1, 1])
            with col_party:
                selected = st.selectbox(
                    "Сторона договора",
                    options=[p["name"] for p in parties_list],
                    key="role_party_select",
                )
            with col_llm:
                use_llm = st.checkbox(
                    "LLM-валидация",
                    value=True,
                    key="role_use_llm",
                    help="Отсеивает ложные срабатывания через Claude",
                )
            with col_fallback:
                use_llm_fallback = st.checkbox(
                    "🤖 LLM-fallback",
                    value=False,
                    key="role_llm_fallback",
                    help="Если regex нашёл <N мест → LLM ищет напрямую",
                )

            if st.button("🔍 Найти места подписи", type="primary"):
                _run_search(doc, parties_list, selected, use_llm, use_llm_fallback)

        # ───────────────────────────────────────────────────────────────────────
        # Режим: По подписанту
        # ───────────────────────────────────────────────────────────────────────
        else:
            st.markdown("**Кто подписывает?**")
            col_name, col_company = st.columns(2)
            with col_name:
                signer_name = st.text_input(
                    "ФИО подписанта *",
                    placeholder="напр. Иван Петров",
                    key="signer_name",
                )
            with col_company:
                signer_company = st.text_input(
                    "Компания (необязательно)",
                    placeholder="напр. ООО Ромашка",
                    key="signer_company",
                )

            col_llm2, col_fallback2 = st.columns([1, 1])
            with col_llm2:
                use_llm2 = st.checkbox(
                    "LLM-валидация",
                    value=True,
                    key="signer_use_llm",
                    help="Отсеивает ложные срабатывания",
                )
            with col_fallback2:
                use_llm_fallback2 = st.checkbox(
                    "🤖 LLM-fallback",
                    value=False,
                    key="signer_llm_fallback",
                    help="Если regex нашёл <N мест → LLM ищет напрямую",
                )

            resolve_btn_disabled = not signer_name.strip()

            if st.button(
                "🧠 Определить сторону через Claude",
                type="primary",
                disabled=resolve_btn_disabled,
            ):
                with st.spinner("LLM определяет сторону подписанта..."):
                    try:
                        result = resolve_party(
                            doc,
                            signer_name=signer_name.strip(),
                            company=signer_company.strip() if signer_company else None,
                            parties=parties_list,
                        )
                        st.session_state["resolved_party"] = result
                    except Exception as e:
                        st.session_state["resolved_party"] = {"error": str(e)}
                st.rerun()

            if "resolved_party" in st.session_state:
                result = st.session_state["resolved_party"]
                if result.get("error"):
                    st.error(f"Ошибка резолвинга: {result['error']}")
                elif not result.get("party"):
                    st.warning("LLM не смог определить сторону из реестра.")
                else:
                    party_name = result["party"]
                    confidence = result.get("confidence", 0.0)
                    reasoning = result.get("reasoning", "")

                    if confidence >= 0.5:
                        st.success(
                            f"✅ Сторона: **{party_name}** (confidence {confidence:.2f})"
                        )
                        if reasoning:
                            with st.expander("💬 Reasoning", expanded=False):
                                st.caption(reasoning)

                        # Защита от повторного входа
                        already_triggered = st.session_state.get("search_triggered_for") == party_name
                        if not already_triggered:
                            if st.button(
                                f"🔍 Найти места подписи для '{party_name}'",
                                type="primary",
                                key="auto_search_btn",
                            ):
                                st.session_state["search_triggered_for"] = party_name
                                _run_search(doc, parties_list, party_name, use_llm2, use_llm_fallback2)
                                st.session_state.pop("resolved_party", None)
                                st.session_state.pop("search_triggered_for", None)
                                st.rerun()
                        else:
                            st.info("Поиск запущен...")
                    else:
                        st.warning(
                            f"⚠ LLM предполагает сторону '{party_name}' "
                            f"(confidence {confidence:.2f}), но уверенности мало."
                        )
                        if reasoning:
                            with st.expander("💬 Reasoning", expanded=False):
                                st.caption(reasoning)
                        if st.button("▶ Искать принудительно"):
                            _run_search(doc, parties_list, party_name, use_llm2, use_llm_fallback2)


# ── РЕЗУЛЬТАТЫ ПОИСКА ────────────────────────────────────────────────────────
if "matches" in st.session_state and "parsed_doc" in st.session_state:
    st.divider()
    st.subheader("📍 Результаты поиска")
    doc = st.session_state["parsed_doc"]
    all_matches = st.session_state["matches"]
    confirmed = [m for m in all_matches if m.status == "candidate"]
    rejected = [m for m in all_matches if m.status in ("rejected_by_llm", "decorative")]

    if not confirmed and not rejected:
        st.warning("⚠ Места подписи не найдены.")
    else:
        cc1, cc2, cc3, cc4 = st.columns(4)
        cc1.metric("Найдено", len(confirmed))
        cc2.metric("Отклонено LLM", len(rejected))
        cc3.metric("Время поиска", f"{st.session_state.get('search_elapsed', 0)} сек")
        source = st.session_state.get("search_source", "regex")
        source_label = {
            "regex": "Regex",
            "llm_fallback": "🤖 LLM-fallback",
            "template": "📋 Шаблон",
            "manual": "🖱 Ручная разметка",
            "regex_empty": "Regex (0)",
        }.get(source, source)
        cc4.metric("Источник", source_label)

        st.markdown("##### Места для подписи")
        st.caption("✅ — подтверждённые · ⚠ — отклонены LLM (снимите галку чтобы исключить, поставьте чтобы переопределить LLM)")

        # ── Подтверждённые места ────────────────────────────────────────────
        for m in confirmed:
            cb_key = f"include_{m.id}"
            if cb_key not in st.session_state:
                st.session_state[cb_key] = True
            include = st.checkbox(
                f"**{m.id}** · стр. {m.page + 1} · conf {m.confidence:.2f} · `{m.context[:80]}...`",
                value=st.session_state[cb_key],
                key=cb_key,
            )
            m.operator_excluded = not include

        # ── Отклонённые LLM — показываем с чекбоксом default=False (BUG-02) ─
        if rejected:
            st.markdown("---")
            st.caption("Ниже — места отклонённые LLM. Включите чекбокс чтобы переопределить.")
            for m in rejected:
                cb_key = f"include_{m.id}"
                if cb_key not in st.session_state:
                    st.session_state[cb_key] = False  # по умолчанию выключено
                include = st.checkbox(
                    f"~~{m.id}~~ · стр. {m.page + 1} · ⚠ отклонено LLM · `{m.context[:80]}...`",
                    value=st.session_state[cb_key],
                    key=cb_key,
                )
                if include:
                    m.operator_excluded = False
                    m.status = "candidate"  # оператор переопределяет LLM
                else:
                    m.operator_excluded = True

        # ── Все активные = подтверждённые + переопределённые ─────────────────
        active_matches = [m for m in all_matches if not m.operator_excluded]

        st.markdown("##### Предпросмотр (красные рамки = активные места подписи)")
        
        # Собираем страницы для показа: первая, последняя, страницы с активными местами
        pages_with_matches = sorted({m.page for m in active_matches})
        pages_to_show = set(pages_with_matches)
        pages_to_show.add(0)  # первая
        if len(doc.pages) > 1:
            pages_to_show.add(len(doc.pages) - 1)  # последняя
        
        for page_num in sorted(pages_to_show):
            page_matches = [m for m in active_matches if m.page == page_num]
            try:
                png = render_page_with_highlights(doc.pdf_bytes, page_num, page_matches)
                caption = f"Страница {page_num + 1}"
                if page_num == 0:
                    caption += " (первая)"
                elif page_num == len(doc.pages) - 1 and page_num != 0:
                    caption += " (последняя)"
                st.image(png, caption=caption)
            except Exception as e:
                st.error(f"Ошибка рендера стр. {page_num + 1}: {e}")

        # ── JSON экспорт ──────────────────────────────────────────────────────
        from datetime import datetime, timezone
        export_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "doc_info": {
                "filename": doc.filename,
                "pages": len(doc.pages),
                "language": doc.language,
                "size_bytes": len(doc.pdf_bytes),
            },
            "search_config": st.session_state.get("search_config", {}),
            "results": {
                "total_found": len(confirmed),
                "after_validation": len(confirmed),
                "rejected_by_llm": len(rejected),
                "processing_time_seconds": st.session_state.get("search_elapsed", 0),
            },
            "matches": [
                {
                    "id": m.id,
                    "page": m.page,
                    "bbox": list(m.bbox),
                    "pattern": m.pattern,
                    "context": m.context,
                    "confidence": m.confidence,
                    "status": m.status,
                    "operator_excluded": m.operator_excluded,
                }
                for m in confirmed
            ],
            "rejected": [
                {
                    "id": m.id,
                    "page": m.page,
                    "reason": m.status,
                    "pattern": m.pattern,
                    "context": m.context,
                }
                for m in rejected
            ],
        }
        st.download_button(
            "📥 Экспорт JSON (диагностика)",
            data=json.dumps(export_data, ensure_ascii=False, indent=2),
            file_name=f"signfinder_{doc.filename.rsplit('.', 1)[0]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
        )

        st.divider()
        st.markdown("##### Наложение подписи")
        flatten = st.checkbox("🔒 Защитить от редактирования (flatten PDF)", value=False)

        if st.button("▶ Применить подпись", type="primary"):
            active = [m for m in all_matches if not m.operator_excluded]
            if not active:
                st.error("Все места исключены.")
            else:
                with st.spinner(f"Накладываю подпись в {len(active)} местах..."):
                    try:
                        signed = apply_signature(
                            doc.pdf_bytes,
                            active,
                            st.session_state["signature_png"],
                            flatten=flatten,
                        )
                        st.session_state["signed_pdf"] = signed
                    except Exception as e:
                        st.error(f"Ошибка наложения: {e}")


# ── ИТОГОВЫЙ PDF ─────────────────────────────────────────────────────────────
if "signed_pdf" in st.session_state and "parsed_doc" in st.session_state:
    st.divider()
    st.subheader("📥 Подписанный документ")
    signed = st.session_state["signed_pdf"]
    doc = st.session_state["parsed_doc"]
    st.download_button(
        "⬇ Скачать подписанный PDF",
        data=signed,
        file_name=f"signed_{doc.filename.rsplit('.', 1)[0]}.pdf",
        mime="application/pdf",
    )
    st.markdown("##### Превью результата")
    active_matches = [m for m in st.session_state["matches"] if not m.operator_excluded]
    
    # Собираем страницы: первая, последняя, страницы с активными местами
    pages_with_matches = sorted({m.page for m in active_matches})
    pages_to_show = set(pages_with_matches)
    pages_to_show.add(0)  # первая
    if len(doc.pages) > 1:
        pages_to_show.add(len(doc.pages) - 1)  # последняя
    
    for page_num in sorted(pages_to_show):
        try:
            png = render_page_with_highlights(signed, page_num, [])
            caption = f"Страница {page_num + 1} (подписанная)"
            if page_num == 0:
                caption = f"Страница {page_num + 1} (подписанная, первая)"
            elif page_num == len(doc.pages) - 1 and page_num != 0:
                caption = f"Страница {page_num + 1} (подписанная, последняя)"
            st.image(png, caption=caption)
        except Exception as e:
            st.error(f"Ошибка рендера стр. {page_num + 1}: {e}")


st.divider()
st.caption("SignFinder MVP v1.4.2")
st.markdown("[📋 ТЗ SignFinder MVP Requirements](https://github.com/alexgeorg2507-creator/SignPDFMVP/blob/main/SignPDFMVP)")
