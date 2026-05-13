"""SignFinder MVP 1.4.0 — JSON экспорт результатов"""
import json
import time
from datetime import datetime

import streamlit as st

from core.auth import check_access_code
from core.storage import (
    read_md, write_md, read_json, write_json, json_config_exists,
    read_signature, write_signature, delete_signature,
)
from core.parser import parse_document
from core.finder import parse_parties_json, parse_parties_md, find_signatures
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


def _run_search(doc, parties_list, selected_party, use_llm, mode="По роли"):
    party_obj = next((p for p in parties_list if p["name"] == selected_party), None)
    if not party_obj:
        st.error("Сторона не найдена в конфиге")
        return
    
    start_time = time.time()
    with st.spinner(f"Поиск для стороны '{selected_party}'..."):
        try:
            matches = find_signatures(doc, party_obj)
            matches = apply_corrections(matches)
            if use_llm:
                with st.spinner("LLM-валидация через Claude..."):
                    matches = validate_with_llm(matches, selected_party)
            st.session_state["matches"] = matches
            st.session_state.pop("signed_pdf", None)
        except Exception as e:
            st.error(f"Ошибка поиска: {e}")
            return
    
    elapsed = time.time() - start_time
    st.session_state["last_search_time"] = elapsed
    st.session_state["search_config"] = {
        "mode": mode,
        "party": selected_party,
        "use_llm_validation": use_llm,
        "use_llm_fallback": False  # пока нет в v1.4.0
    }


def _handle_resolved_party(doc, parties_list, result, use_llm):
    if result.get("error") or not result.get("party"):
        return
    if result["confidence"] < 0.5:
        return
    _run_search(doc, parties_list, result["party"], use_llm, mode="По подписанту")


def _export_results_json() -> str:
    """Формирует JSON-экспорт результатов поиска."""
    doc = st.session_state["parsed_doc"]
    all_matches = st.session_state["matches"]
    config = st.session_state.get("search_config", {})
    elapsed = st.session_state.get("last_search_time", 0)
    
    confirmed = [m for m in all_matches if m.status not in ("rejected_by_llm", "decorative")]
    rejected = [m for m in all_matches if m.status in ("rejected_by_llm", "decorative")]
    
    export = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "doc_info": {
            "filename": doc.filename,
            "pages": len(doc.pages),
            "language": doc.language,
            "size_bytes": len(doc.pdf_bytes)
        },
        "search_config": config,
        "results": {
            "total_found": len(all_matches),
            "after_validation": len(confirmed),
            "rejected_by_llm": len(rejected),
            "processing_time_seconds": round(elapsed, 1)
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
                "operator_excluded": m.operator_excluded
            }
            for m in confirmed
        ],
        "rejected": [
            {
                "id": m.id,
                "page": m.page,
                "reason": m.status,
                "pattern": m.pattern,
                "context": m.context
            }
            for m in rejected
        ]
    }
    return json.dumps(export, ensure_ascii=False, indent=2)

st.title("📝 SignFinder MVP")
st.caption("Поиск мест подписи в договорах PDF / DOCX")


# ── ШАГ 1: ПОДПИСЬ ───────────────────────────────────────────────────────────
st.subheader("1️⃣ Подпись")

sig_col1, sig_col2 = st.columns([2, 1])
with sig_col1:
    sig_upload = st.file_uploader(
        "Загрузить PNG подписи (прозрачный фон, обязательно)",
        type=["png"],
        key="sig_uploader",
    )
    if sig_upload is not None:
        png_bytes = sig_upload.getvalue()
        st.session_state["signature_png"] = png_bytes
        # Сохраняем в GCS/локально для следующих сессий
        try:
            write_signature(png_bytes)
        except Exception:
            pass  # не блокируем основной флоу

with sig_col2:
    if "signature_png" in st.session_state:
        st.image(st.session_state["signature_png"], width=200)
        st.success("✅ Подпись загружена")
        if st.button("🗑 Забыть подпись", help="Удалить сохранённую подпись из хранилища"):
            st.session_state.pop("signature_png", None)
            try:
                delete_signature()
            except Exception:
                pass
            st.rerun()
    else:
        st.warning("⬅ Загрузите PNG подписи")


sig_ready = "signature_png" in st.session_state


# ── ШАГ 2: ДОКУМЕНТ ──────────────────────────────────────────────────────────
st.divider()
st.subheader("2️⃣ Документ")

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
            label_visibility="collapsed",
        )

        doc_lang = st.session_state.get("parsed_doc_language")
        parties_list = _load_parties(doc_lang)
        use_llm = st.checkbox("LLM-валидация мест подписи", value=True)

        def _party_label(name: str) -> str:
            for p in parties_list:
                if p["name"] == name:
                    return p.get("display") or name
            return name

        if mode == "По роли":
            party_names = [p["name"] for p in parties_list]
            if party_names:
                selected_party = st.selectbox(
                    "Сторона",
                    options=party_names,
                    format_func=_party_label,
                    key="party_selector",
                )
            else:
                st.warning("Конфиг сторон пуст")
                selected_party = None

            if st.button("🔍 Найти места подписи", type="primary"):
                _run_search(doc, parties_list, selected_party, use_llm)

        else:
            col_a, col_b = st.columns(2)
            with col_a:
                signer_name = st.text_input(
                    "ФИО подписанта *",
                    placeholder="напр. Лебедев Александр Петрович",
                    key="signer_name_input",
                )
            with col_b:
                company = st.text_input(
                    "Компания (опционально)",
                    placeholder="напр. ООО Ромашка",
                    key="company_input",
                )

            if st.button("🎯 Определить сторону и найти", type="primary"):
                if not signer_name.strip():
                    st.error("Укажите ФИО подписанта")
                else:
                    if doc_lang not in ("ru", "en", "pl"):
                        with st.spinner("Определяю язык документа..."):
                            lang = detect_language(doc)
                            st.session_state["parsed_doc_language"] = lang
                            parties_list = _load_parties(lang)

                    with st.spinner("Определяю сторону подписанта через LLM..."):
                        result = resolve_party(doc, signer_name, company, parties_list)

                    st.session_state["resolved_party"] = result
                    _handle_resolved_party(doc, parties_list, result, use_llm)

            if "resolved_party" in st.session_state:
                r = st.session_state["resolved_party"]
                if r.get("error"):
                    st.error(f"❌ {r['error']}")
                elif r.get("party"):
                    conf = r["confidence"]
                    if conf >= 0.7:
                        st.success(f"✅ Сторона: **{_party_label(r['party'])}** (confidence {conf:.2f})")
                    elif conf >= 0.5:
                        st.warning(f"⚠️ Сторона определена со средней уверенностью: "
                                   f"**{_party_label(r['party'])}** (confidence {conf:.2f})")
                    else:
                        st.error(f"❌ Сторона не определена однозначно (confidence {conf:.2f}). "
                                 "Уточните ФИО или добавьте компанию, или используйте режим 'По роли'.")
                    if r.get("evidence"):
                        st.caption(f"💬 Цитата: «{r['evidence']}»")


# ── РЕЗУЛЬТАТ ────────────────────────────────────────────────────────────────
if "matches" in st.session_state and "parsed_doc" in st.session_state:
    st.divider()
    st.subheader("✅ Результат поиска")
    doc = st.session_state["parsed_doc"]
    all_matches = st.session_state["matches"]
    confirmed = [m for m in all_matches if m.status not in ("rejected_by_llm", "decorative")]
    rejected  = [m for m in all_matches if m.status in  ("rejected_by_llm", "decorative")]

    if not all_matches:
        st.warning("Ничего не найдено по выбранным паттернам.")
    else:
        cc1, cc2 = st.columns(2)
        cc1.metric("Реальные места", len(confirmed))
        cc2.metric("Отклонены", len(rejected))

        # Кнопка экспорта JSON (v1.4.0)
        export_json = _export_results_json()
        st.download_button(
            "📥 Экспорт JSON",
            data=export_json,
            file_name=f"signfinder_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
        )

        st.markdown("##### Места для подписи (снимите галку чтобы исключить)")
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

        st.markdown("##### Предпросмотр (красные рамки = места подписи)")
        for page_num in sorted({m.page for m in confirmed}):
            page_matches = [m for m in confirmed if m.page == page_num]
            try:
                png = render_page_with_highlights(doc.pdf_bytes, page_num, page_matches)
                st.image(png, caption=f"Страница {page_num + 1}")
            except Exception as e:
                st.error(f"Ошибка рендера стр. {page_num + 1}: {e}")

        st.divider()
        st.markdown("##### Наложение подписи")
        flatten = st.checkbox("🔒 Защитить от редактирования (flatten PDF)", value=False)

        if st.button("▶ Применить подпись", type="primary"):
            active = [m for m in confirmed if not m.operator_excluded]
            if not active:
                st.error("Все места исключены.")
            else:
                with st.spinner(f"Накладываю подпись в {len(active)} местах..."):
                    try:
                        signed = apply_signature(
                            doc.pdf_bytes,
                            confirmed,
                            st.session_state["signature_png"],
                            flatten=flatten,
                        )
                        st.session_state["signed_pdf"] = signed
                    except Exception as e:
                        st.error(f"Ошибка наложения: {e}")

        if rejected:
            with st.expander(f"🚫 Отклонены ({len(rejected)})"):
                for m in rejected:
                    st.caption(f"{m.id} · стр. {m.page + 1} · {m.correction_applied or m.status} · `{m.context[:80]}`")

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
    confirmed = [m for m in st.session_state["matches"] if m.status not in ("rejected_by_llm", "decorative")]
    for page_num in sorted({m.page for m in confirmed}):
        try:
            png = render_page_with_highlights(signed, page_num, [])
            st.image(png, caption=f"Страница {page_num + 1} (подписанная)")
        except Exception as e:
            st.error(f"Ошибка рендера стр. {page_num + 1}: {e}")


# ── НАСТРОЙКИ ────────────────────────────────────────────────────────────────
st.divider()
with st.expander("⚙️ Настройки", expanded=False):
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("parties.json")
        try:
            if json_config_exists("parties.json"):
                import json as _json
                raw_json = _json.dumps(read_json("parties.json"), ensure_ascii=False, indent=2)
            else:
                raw_json = "{}"
        except Exception as e:
            st.error(f"Ошибка чтения parties.json: {e}")
            raw_json = "{}"

        new_json_str = st.text_area(
            "Реестр сторон (JSON)",
            value=raw_json,
            height=400,
            key="parties_json_editor",
            label_visibility="collapsed",
        )
        if st.button("💾 Сохранить parties.json"):
            try:
                import json as _json
                parsed_json = _json.loads(new_json_str)
                backup = write_json("parties.json", parsed_json)
                st.success(f"Сохранено. Бэкап: {backup}")
            except Exception as e:
                st.error(f"Невалидный JSON: {e}")

    with col2:
        st.subheader("corrections.md")
        try:
            corrections_content = read_md("corrections.md")
        except Exception as e:
            st.error(f"Не удалось прочитать corrections.md: {e}")
            corrections_content = ""
        new_corrections = st.text_area(
            "База корректировок",
            value=corrections_content,
            height=400,
            key="corrections_editor",
            label_visibility="collapsed",
        )
        if st.button("💾 Сохранить corrections.md"):
            backup = write_md("corrections.md", new_corrections)
            st.success(f"Сохранено. Бэкап: {backup}")


st.divider()
st.markdown("[📋 ТЗ SignFinder MVP Requirements](https://github.com/alexgeorg2507-creator/SignPDFMVP/blob/main/SignPDFMVP)")
st.caption("SignFinder MVP v1.4.0 — май 2026")