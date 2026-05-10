"""SignFinder MVP 1.0"""
import streamlit as st

from core.auth import check_access_code
from core.storage import read_md, write_md
from core.parser import parse_document
from core.finder import parse_parties_md, find_signatures
from core.corrector import apply_corrections
from core.validator import validate_with_llm
from core.preview import render_page_with_highlights
from core.overlay import apply_signature


st.set_page_config(page_title="SignFinder MVP", layout="wide")


# ---- AUTH GATE ----
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


st.title("📝 SignFinder MVP")
st.caption("Поиск мест подписи в договорах PDF / DOCX")


# ---- ШАГ 1: ПОДПИСЬ ----
st.subheader("1️⃣ Подпись")

sig_col1, sig_col2 = st.columns([2, 1])
with sig_col1:
    sig_upload = st.file_uploader(
        "Загрузить PNG подписи (прозрачный фон, обязательно)",
        type=["png"],
        key="sig_uploader",
    )
    if sig_upload is not None:
        st.session_state["signature_png"] = sig_upload.getvalue()

with sig_col2:
    if "signature_png" in st.session_state:
        st.image(st.session_state["signature_png"], width=200)
        st.success("✅ Подпись загружена")
    else:
        st.warning("⬅ Загрузите PNG подписи")


sig_ready = "signature_png" in st.session_state


# ---- НАСТРОЙКИ (в конце, свёрнуты) ----
# (рендерим позже, после основного контента)


# ---- ШАГ 2: ДОКУМЕНТ ----
st.divider()
st.subheader("2️⃣ Документ")

if not sig_ready:
    st.info("Сначала загрузите подпись (шаг 1).")
else:
    try:
        parties_md = read_md("parties.md")
        parties_list = parse_parties_md(parties_md)
    except Exception as e:
        st.error(f"Не удалось загрузить parties.md: {e}")
        parties_list = []

    col_a, col_b = st.columns([2, 1])
    with col_a:
        uploaded = st.file_uploader(
            "Загрузить договор",
            type=["pdf", "docx"],
            key="doc_uploader",
        )
    with col_b:
        party_names = [p["name"] for p in parties_list]
        selected_party = st.selectbox(
            "Сторона",
            options=party_names if party_names else ["(parties.md пуст)"],
            key="party_selector",
        )
        use_llm = st.checkbox("LLM-валидация", value=True)

    if uploaded is not None:
        if st.session_state.get("last_uploaded_name") != uploaded.name:
            with st.spinner(f"Парсинг {uploaded.name}..."):
                try:
                    parsed = parse_document(uploaded.getvalue(), uploaded.name)
                    st.session_state["parsed_doc"] = parsed
                    st.session_state["last_uploaded_name"] = uploaded.name
                    st.session_state.pop("matches", None)
                    st.session_state.pop("signed_pdf", None)
                except Exception as e:
                    st.error(f"Ошибка парсинга: {e}")
                    st.session_state.pop("parsed_doc", None)

    if "parsed_doc" in st.session_state:
        doc = st.session_state["parsed_doc"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Файл", doc.filename)
        c2.metric("Страниц", len(doc.pages))
        c3.metric("Язык", doc.language.upper())

        if st.button("🔍 Найти места подписи", type="primary"):
            party_obj = next((p for p in parties_list if p["name"] == selected_party), None)
            if not party_obj:
                st.error("Сторона не найдена в parties.md")
            else:
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


    # ---- РЕЗУЛЬТАТ ----
    if "matches" in st.session_state and "parsed_doc" in st.session_state:
        st.divider()
        st.subheader("✅ Результат поиска")
        doc = st.session_state["parsed_doc"]
        all_matches = st.session_state["matches"]
        confirmed = [m for m in all_matches if m.status not in ("rejected_by_llm", "decorative")]
        rejected = [m for m in all_matches if m.status in ("rejected_by_llm", "decorative")]

        if not all_matches:
            st.warning("Ничего не найдено по выбранным паттернам.")
        else:
            cc1, cc2 = st.columns(2)
            cc1.metric("Реальные места", len(confirmed))
            cc2.metric("Отклонены", len(rejected))

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
            pages_with_matches = sorted({m.page for m in confirmed})
            for page_num in pages_with_matches:
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

    # ---- ИТОГОВЫЙ PDF ----
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


# ---- НАСТРОЙКИ (в конце) ----
st.divider()
with st.expander("⚙️ Настройки", expanded=False):
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("parties.md")
        try:
            parties_content = read_md("parties.md")
        except Exception as e:
            st.error(f"Не удалось прочитать parties.md: {e}")
            parties_content = ""
        new_parties = st.text_area("Реестр сторон", value=parties_content, height=300,
                                   key="parties_editor", label_visibility="collapsed")
        if st.button("💾 Сохранить parties.md"):
            backup = write_md("parties.md", new_parties)
            st.success(f"Сохранено. Бэкап: {backup}")
    with col2:
        st.subheader("corrections.md")
        try:
            corrections_content = read_md("corrections.md")
        except Exception as e:
            st.error(f"Не удалось прочитать corrections.md: {e}")
            corrections_content = ""
        new_corrections = st.text_area("База корректировок", value=corrections_content, height=300,
                                       key="corrections_editor", label_visibility="collapsed")
        if st.button("💾 Сохранить corrections.md"):
            backup = write_md("corrections.md", new_corrections)
            st.success(f"Сохранено. Бэкап: {backup}")


st.divider()
st.markdown("[📋 ТЗ SignFinder MVP](https://github.com/alexgeorg2507-creator/SignPDFMVP)")
