"""CRUD-редактор шаблонов сторон договора (parties.json).

Streamlit multi-page: pages/2_⚙️_Шаблоны.py
"""
import json

import streamlit as st

from core.storage import json_config_exists, read_json, write_json

st.set_page_config(page_title="Шаблоны — SignFinder", layout="wide")

# ── AUTH ─────────────────────────────────────────────────────────────────────
if not st.session_state.get("auth"):
    st.warning("Войдите через главную страницу.")
    st.stop()

st.title("⚙️ Шаблоны сторон")
st.caption("CRUD-редактор `parties.json` — стороны, языки, паттерны, aliases")

LANGUAGES = ["ru", "en", "pl"]

# ── Загрузка конфига ─────────────────────────────────────────────────────────
@st.cache_data(ttl=0)
def _load_json() -> dict:
    if json_config_exists("parties.json"):
        return read_json("parties.json")
    return {"version": "2.0", "parties": {}}


def _reload():
    st.cache_data.clear()
    st.rerun()


if "templates_data" not in st.session_state:
    try:
        st.session_state["templates_data"] = _load_json()
    except Exception as e:
        st.error(f"Ошибка загрузки parties.json: {e}")
        st.stop()

data: dict = st.session_state["templates_data"]
parties: dict = data.get("parties", {})

# ── Toolbar ──────────────────────────────────────────────────────────────────
col_reload, col_save, col_raw = st.columns([1, 1, 2])

with col_reload:
    if st.button("🔄 Перечитать из хранилища"):
        st.session_state.pop("templates_data", None)
        _reload()

with col_save:
    if st.button("💾 Сохранить все изменения", type="primary"):
        try:
            backup = write_json("parties.json", data)
            st.success(f"Сохранено. Бэкап: {backup}")
        except Exception as e:
            st.error(f"Ошибка сохранения: {e}")

with col_raw:
    with st.expander("📄 Raw JSON (только чтение)", expanded=False):
        st.code(json.dumps(data, ensure_ascii=False, indent=2), language="json")

st.divider()

# ── Список сторон ─────────────────────────────────────────────────────────────
st.subheader(f"Стороны ({len(parties)})")

party_names = list(parties.keys())

# Удаление стороны
del_col1, del_col2 = st.columns([3, 1])
with del_col1:
    party_to_delete = st.selectbox(
        "Удалить сторону",
        options=["— выбери —"] + party_names,
        key="delete_party_select",
        label_visibility="collapsed",
    )
with del_col2:
    if st.button("🗑 Удалить", disabled=(party_to_delete == "— выбери —")):
        del parties[party_to_delete]
        st.session_state["templates_data"] = data
        st.rerun()

st.divider()

# ── Редактор каждой стороны ──────────────────────────────────────────────────
for party_name, party_block in parties.items():
    with st.expander(f"**{party_name}** — {party_block.get('display', party_name)}", expanded=False):

        # display name
        new_display = st.text_input(
            "Отображаемое имя",
            value=party_block.get("display", party_name),
            key=f"display_{party_name}",
        )
        party_block["display"] = new_display

        # notes
        new_notes = st.text_input(
            "Заметки",
            value=party_block.get("notes", ""),
            key=f"notes_{party_name}",
        )
        party_block["notes"] = new_notes

        # Языковые блоки
        langs: dict = party_block.setdefault("languages", {})
        st.markdown("**Языки и паттерны**")

        for lang in LANGUAGES:
            lang_block = langs.get(lang, {"aliases": [], "patterns": []})
            with st.expander(f"Язык: **{lang.upper()}**", expanded=(lang in langs)):

                # Aliases
                aliases_str = ", ".join(lang_block.get("aliases", []))
                new_aliases_str = st.text_input(
                    "Aliases (через запятую)",
                    value=aliases_str,
                    key=f"aliases_{party_name}_{lang}",
                )
                new_aliases = [a.strip() for a in new_aliases_str.split(",") if a.strip()]

                # Patterns
                patterns_str = "\n".join(lang_block.get("patterns", []))
                new_patterns_str = st.text_area(
                    "Паттерны (по одному на строку)",
                    value=patterns_str,
                    height=120,
                    key=f"patterns_{party_name}_{lang}",
                )
                new_patterns = [p.strip() for p in new_patterns_str.split("\n") if p.strip()]

                # Применить изменения в памяти
                if new_aliases or new_patterns:
                    if lang not in langs:
                        langs[lang] = {}
                    langs[lang]["aliases"] = new_aliases
                    langs[lang]["patterns"] = new_patterns
                elif lang in langs and not new_aliases and not new_patterns:
                    # Блок стал пустым — убираем
                    langs.pop(lang, None)

                col_apply, _ = st.columns([1, 3])
                with col_apply:
                    if st.button(f"✅ Применить {lang.upper()}", key=f"apply_{party_name}_{lang}"):
                        # изменения уже применены выше в памяти
                        st.session_state["templates_data"] = data
                        st.success(f"Изменения {lang.upper()} применены (не забудь сохранить)")

        # Добавить новый языковой блок
        st.markdown("**Добавить языковой блок**")
        add_lang = st.selectbox(
            "Язык",
            options=[l for l in LANGUAGES if l not in langs] or ["—"],
            key=f"add_lang_{party_name}",
        )
        if st.button(f"+ Добавить {add_lang}", key=f"add_lang_btn_{party_name}",
                     disabled=(add_lang == "—")):
            langs[add_lang] = {"aliases": [], "patterns": []}
            st.session_state["templates_data"] = data
            st.rerun()

st.divider()

# ── Добавление новой стороны ──────────────────────────────────────────────────
st.subheader("➕ Добавить новую сторону")

new_name = st.text_input("Название стороны *", key="new_party_name_input")
new_display = st.text_input("Отображаемое имя", key="new_party_display_input")
new_lang = st.selectbox("Начальный язык", LANGUAGES, key="new_party_lang_input")

if st.button("➕ Создать сторону", disabled=not new_name.strip()):
    name = new_name.strip()
    if name in parties:
        st.error(f"Сторона '{name}' уже существует.")
    else:
        parties[name] = {
            "display": new_display.strip() or name,
            "languages": {
                new_lang: {"aliases": [], "patterns": []}
            },
            "notes": "",
        }
        st.session_state["templates_data"] = data
        st.success(f"Сторона '{name}' создана. Не забудь сохранить.")
        st.rerun()
