"""Страница документации SignFinder v1.7."""
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Документация SignFinder", page_icon="📖", layout="wide")

if not st.session_state.get("auth", False):
    st.warning("Требуется авторизация. Перейдите на главную страницу.")
    st.stop()

st.title("📖 Документация SignFinder")

DOCS_DIR = Path(__file__).parent.parent / "docs"

AVAILABLE_DOCS = {
    "concept_v1_3": {
        "title": "Концепция проекта v1.3",
        "filename": "SignFinder_Concept_v1_3.md",
        "description": "Архитектура, методы парсинга, светофор, текстовые якоря",
    },
}

default_doc = st.session_state.pop("selected_doc", "concept_v1_3")
if default_doc not in AVAILABLE_DOCS:
    default_doc = "concept_v1_3"

selected = st.selectbox(
    "Выберите документ",
    list(AVAILABLE_DOCS.keys()),
    format_func=lambda k: AVAILABLE_DOCS[k]["title"],
    index=list(AVAILABLE_DOCS.keys()).index(default_doc),
)

doc_info = AVAILABLE_DOCS[selected]
st.caption(doc_info["description"])
st.divider()

doc_path = DOCS_DIR / doc_info["filename"]

if doc_path.exists():
    try:
        content = doc_path.read_text(encoding="utf-8")
        st.markdown(content)
    except Exception as e:
        st.error(f"Ошибка чтения документа: {e}")
else:
    st.warning(f"Документ ещё не создан: `{doc_info['filename']}`")
    st.info(f"Ожидается по пути: `{doc_path}`")
    st.caption("Положите файл в папку `docs/` в корне проекта.")
