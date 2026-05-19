"""SignFinder MVP v1.7 — Dashboard"""
import streamlit as st

from core.auth import check_access_code
from core.storage import read_signature, read_json, json_config_exists

st.set_page_config(page_title="SignFinder MVP", page_icon="🤖", layout="wide")


# ── AUTH GATE ─────────────────────────────────────────────────────────────────
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


# ── Автозагрузка подписи (один раз за сессию) ─────────────────────────────────
if "signature_autoloaded" not in st.session_state:
    st.session_state["signature_autoloaded"] = True
    if "signature_png" not in st.session_state:
        saved = read_signature()
        if saved:
            st.session_state["signature_png"] = saved


# ── Загрузка данных подписанта ────────────────────────────────────────────────
def _load_signer() -> dict:
    """Читает signer_profile.json или берёт первую сторону из parties.json."""
    try:
        if json_config_exists("signer_profile.json"):
            return read_json("signer_profile.json")
    except Exception:
        pass
    try:
        if json_config_exists("parties.json"):
            data = read_json("parties.json")
            parties = data.get("parties", {})
            if parties:
                first_key = next(iter(parties))
                party = parties[first_key]
                langs = party.get("languages", {})
                ru = langs.get("ru", {})
                aliases = ru.get("aliases", [])
                return {
                    "name": aliases[0] if aliases else first_key,
                    "company": first_key,
                }
    except Exception:
        pass
    return {}


# ── DASHBOARD ─────────────────────────────────────────────────────────────────
st.title("🤖 SignFinder MVP")

with st.sidebar:
    with st.expander("🔧 Debug", expanded=False):
        for k in ["last_uploaded_name", "search_source", "search_elapsed"]:
            st.caption(f"`{k}`: {st.session_state.get(k, '—')}")
        if st.button("🗑 Очистить session", key="debug_clear"):
            for k in list(st.session_state.keys()):
                if k not in ("auth", "signature_png", "signature_autoloaded"):
                    st.session_state.pop(k)
            st.rerun()

st.divider()

# ── Карточка подписанта ───────────────────────────────────────────────────────
st.subheader("👤 Подписант")

signer = _load_signer()
sig_bytes = st.session_state.get("signature_png")

col_info, col_sig, col_btn = st.columns([2, 2, 1])

with col_info:
    fio = signer.get("name") or signer.get("full_name", "—")
    company = signer.get("company") or signer.get("legal_entity", "—")
    st.markdown(f"**ФИО:** {fio}")
    st.markdown(f"**Компания:** {company}")

with col_sig:
    if sig_bytes:
        st.image(sig_bytes, width=200, caption="Подпись")
    else:
        st.caption("Подпись не загружена")

with col_btn:
    st.write("")
    st.write("")
    try:
        if st.button("⚙ Изменить данные"):
            st.switch_page("pages/4_⚙️_Настройки.py")
    except AttributeError:
        st.info("Раздел: Настройки")

st.divider()

# ── Что делаем ────────────────────────────────────────────────────────────────
st.subheader("📋 Что делаем")

card_col1, card_col2 = st.columns(2)

with card_col1:
    st.markdown("""
**🤖 Автоподписание договоров**

Загрузить договор → автопоиск мест подписи →
доразметка кликом если нужно → скачать подписанный PDF
""")
    try:
        if st.button("→ Открыть", key="go_auto", type="primary", use_container_width=True):
            st.switch_page("pages/5_🤖_Авто_подписание.py")
    except AttributeError:
        st.markdown("[→ Авто-подписание](/5___Авто_подписание)")

with card_col2:
    st.markdown("""
**⚙ Шаблоны сторон (parties.json)**

Реестр сторон с regex-паттернами
для базового поиска мест подписи
""")
    try:
        if st.button("→ Открыть", key="go_settings", use_container_width=True):
            st.switch_page("pages/4_⚙️_Настройки.py")
    except AttributeError:
        st.markdown("[→ Настройки](/4____Настройки)")

st.divider()

# ── Документация ──────────────────────────────────────────────────────────────
st.subheader("📖 Документация")

docs = {
    "concept_v1_3": "📄 Концепция проекта v1.3",
    "tz_v1_7": "📄 Техническое задание v1.7",
}

doc_col1, doc_col2 = st.columns(2)

with doc_col1:
    try:
        if st.button(docs["concept_v1_3"], use_container_width=True):
            st.session_state["selected_doc"] = "concept_v1_3"
            st.switch_page("pages/6___Документация.py")
    except AttributeError:
        st.markdown(f"[{docs['concept_v1_3']}](/6___Документация)")

with doc_col2:
    try:
        if st.button(docs["tz_v1_7"], use_container_width=True):
            st.session_state["selected_doc"] = "tz_v1_7"
            st.switch_page("pages/6___Документация.py")
    except AttributeError:
        st.markdown(f"[{docs['tz_v1_7']}](/6___Документация)")

st.divider()
st.caption("SignFinder MVP v1.7")
