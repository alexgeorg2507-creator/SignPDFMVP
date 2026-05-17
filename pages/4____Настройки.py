"""Страница настроек SignFinder.

Streamlit multi-page: pages/4_⚙️_Настройки.py

Содержит:
- parties.json — CRUD-редактор сторон договора
- corrections.md — база корректировок
- Промпты — редактирование статичных блоков
- Подписант — signer_profile.json (v1.5)
- Маркеры подписи — markers.json (v1.5)
"""
import json

import streamlit as st

from core.storage import json_config_exists, read_json, write_json, read_md, write_md
from core.prompts import load_prompts, save_prompts, PROMPT_META, DEFAULTS as PROMPT_DEFAULTS

st.set_page_config(page_title="Настройки — SignFinder", layout="wide")

if not st.session_state.get("auth"):
    st.warning("Войдите через главную страницу.")
    st.stop()

st.title("⚙️ Настройки SignFinder")

LANGUAGES = ["ru", "en", "pl"]

tab_parties, tab_corrections, tab_prompts, tab_signer, tab_markers = st.tabs([
    "📋 Стороны (parties.json)",
    "🔧 Корректировки (corrections.md)",
    "🤖 Промпты",
    "👤 Подписант",
    "🔖 Маркеры подписи",
])

# ══════════════════════════════════════════════════════════════════════════════
# ТАБ 1: parties.json
# ══════════════════════════════════════════════════════════════════════════════
with tab_parties:
    st.caption("CRUD-редактор `parties.json` — стороны, языки, паттерны, aliases")

    @st.cache_data(ttl=0)
    def _load_parties_json() -> dict:
        if json_config_exists("parties.json"):
            return read_json("parties.json")
        return {"version": "2.0", "parties": {}}

    if "settings_parties_data" not in st.session_state:
        try:
            st.session_state["settings_parties_data"] = _load_parties_json()
        except Exception as e:
            st.error(f"Ошибка загрузки parties.json: {e}")
            st.stop()

    data: dict = st.session_state["settings_parties_data"]
    parties: dict = data.get("parties", {})

    col_reload, col_save, col_raw = st.columns([1, 1, 2])

    with col_reload:
        if st.button("🔄 Перечитать", key="parties_reload"):
            st.session_state.pop("settings_parties_data", None)
            st.cache_data.clear()
            st.rerun()

    with col_save:
        if st.button("💾 Сохранить все", type="primary", key="parties_save"):
            try:
                backup = write_json("parties.json", data)
                st.success(f"Сохранено. Бэкап: {backup}")
            except Exception as e:
                st.error(f"Ошибка сохранения: {e}")

    with col_raw:
        with st.expander("📄 Raw JSON (только чтение)", expanded=False):
            st.code(json.dumps(data, ensure_ascii=False, indent=2), language="json")

    st.divider()

    party_names = list(parties.keys())
    st.subheader(f"Стороны ({len(parties)})")

    del_col1, del_col2 = st.columns([3, 1])
    with del_col1:
        party_to_delete = st.selectbox(
            "Удалить сторону",
            options=["— выбери —"] + party_names,
            key="delete_party_select",
            label_visibility="collapsed",
        )
    with del_col2:
        if st.button("🗑 Удалить", key="delete_party_btn",
                     disabled=(party_to_delete == "— выбери —")):
            del parties[party_to_delete]
            st.session_state["settings_parties_data"] = data
            st.rerun()

    st.divider()

    for party_name, party_block in parties.items():
        with st.expander(
            f"**{party_name}** — {party_block.get('display', party_name)}",
            expanded=False,
        ):
            new_display = st.text_input(
                "Отображаемое имя",
                value=party_block.get("display", party_name),
                key=f"display_{party_name}",
            )
            party_block["display"] = new_display

            new_notes = st.text_input(
                "Заметки",
                value=party_block.get("notes", ""),
                key=f"notes_{party_name}",
            )
            party_block["notes"] = new_notes

            langs: dict = party_block.setdefault("languages", {})
            st.markdown("**Языки и паттерны**")

            for lang in LANGUAGES:
                lang_block = langs.get(lang, {"aliases": [], "patterns": []})
                with st.expander(f"Язык: **{lang.upper()}**", expanded=(lang in langs)):

                    aliases_str = ", ".join(lang_block.get("aliases", []))
                    new_aliases_str = st.text_input(
                        "Aliases (через запятую)",
                        value=aliases_str,
                        key=f"aliases_{party_name}_{lang}",
                    )
                    new_aliases = [a.strip() for a in new_aliases_str.split(",") if a.strip()]

                    patterns_str = "\n".join(lang_block.get("patterns", []))
                    new_patterns_str = st.text_area(
                        "Паттерны (по одному на строку)",
                        value=patterns_str,
                        height=120,
                        key=f"patterns_{party_name}_{lang}",
                    )
                    new_patterns = [p.strip() for p in new_patterns_str.split("\n") if p.strip()]

                    if new_aliases or new_patterns:
                        if lang not in langs:
                            langs[lang] = {}
                        langs[lang]["aliases"] = new_aliases
                        langs[lang]["patterns"] = new_patterns
                    elif lang in langs and not new_aliases and not new_patterns:
                        langs.pop(lang, None)

                    col_apply, _ = st.columns([1, 3])
                    with col_apply:
                        if st.button(f"✅ Применить {lang.upper()}",
                                     key=f"apply_{party_name}_{lang}"):
                            st.session_state["settings_parties_data"] = data
                            st.success(f"Изменения {lang.upper()} применены (не забудь сохранить)")

            st.markdown("**Добавить языковой блок**")
            add_lang = st.selectbox(
                "Язык",
                options=[la for la in LANGUAGES if la not in langs] or ["—"],
                key=f"add_lang_{party_name}",
            )
            if st.button(f"+ Добавить {add_lang}", key=f"add_lang_btn_{party_name}",
                         disabled=(add_lang == "—")):
                langs[add_lang] = {"aliases": [], "patterns": []}
                st.session_state["settings_parties_data"] = data
                st.rerun()

    st.divider()
    st.subheader("➕ Добавить новую сторону")

    new_name = st.text_input("Название стороны *", key="new_party_name_input")
    new_display_val = st.text_input("Отображаемое имя", key="new_party_display_input")
    new_lang_sel = st.selectbox("Начальный язык", LANGUAGES, key="new_party_lang_input")

    if st.button("➕ Создать сторону", disabled=not new_name.strip()):
        name = new_name.strip()
        if name in parties:
            st.error(f"Сторона '{name}' уже существует.")
        else:
            parties[name] = {
                "display": new_display_val.strip() or name,
                "languages": {new_lang_sel: {"aliases": [], "patterns": []}},
                "notes": "",
            }
            st.session_state["settings_parties_data"] = data
            st.success(f"Сторона '{name}' создана. Не забудь сохранить.")
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# ТАБ 2: corrections.md
# ══════════════════════════════════════════════════════════════════════════════
with tab_corrections:
    st.caption("База корректировок — правила постобработки найденных мест подписи")

    try:
        corrections_content = read_md("corrections.md")
    except Exception as e:
        st.error(f"Не удалось прочитать corrections.md: {e}")
        corrections_content = ""

    new_corrections = st.text_area(
        "corrections.md",
        value=corrections_content,
        height=600,
        key="corrections_editor",
        label_visibility="collapsed",
    )

    col_s, col_r, _ = st.columns([1, 1, 3])
    with col_s:
        if st.button("💾 Сохранить corrections.md", type="primary"):
            try:
                backup = write_md("corrections.md", new_corrections)
                st.success(f"Сохранено. Бэкап: {backup}")
            except Exception as e:
                st.error(f"Ошибка сохранения: {e}")
    with col_r:
        if st.button("🔄 Перечитать", key="corrections_reload"):
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# ТАБ 3: Промпты
# ══════════════════════════════════════════════════════════════════════════════
with tab_prompts:
    st.caption(
        "Статичные блоки промптов — правила и инструкции для LLM. "
        "Динамические переменные (doc_text, party_name и т.д.) здесь не отображаются. "
        "Изменения применяются на следующем вызове LLM — без деплоя."
    )

    if "prompts_data" not in st.session_state:
        st.session_state["prompts_data"] = load_prompts()

    prompts_data = st.session_state["prompts_data"]

    col_p_save, col_p_reset, col_p_reload = st.columns([1, 1, 2])

    with col_p_save:
        if st.button("💾 Сохранить промпты", type="primary", key="prompts_save"):
            try:
                save_prompts(st.session_state["prompts_data"])
                st.success("✅ Промпты сохранены")
            except Exception as e:
                st.error(f"Ошибка: {e}")

    with col_p_reset:
        if st.button("↩ Сбросить к дефолтам", key="prompts_reset"):
            st.session_state["prompts_data"] = dict(PROMPT_DEFAULTS)
            try:
                save_prompts(dict(PROMPT_DEFAULTS))
                st.success("✅ Сброшено")
                st.rerun()
            except Exception as e:
                st.error(f"Ошибка: {e}")

    with col_p_reload:
        if st.button("🔄 Перечитать", key="prompts_reload"):
            st.session_state.pop("prompts_data", None)
            st.rerun()

    st.divider()

    for key, meta in PROMPT_META.items():
        with st.expander(
            f"**{meta['label']}** · `{meta['module']}`",
            expanded=False,
        ):
            st.caption(f"Влияет на: {meta['effect']}")

            current_val = prompts_data.get(key, PROMPT_DEFAULTS.get(key, ""))
            new_val = st.text_area(
                "Текст промпта",
                value=current_val,
                height=200,
                key=f"prompt_{key}",
                label_visibility="collapsed",
            )
            prompts_data[key] = new_val

            col_diff, _ = st.columns([1, 3])
            with col_diff:
                if current_val != PROMPT_DEFAULTS.get(key, ""):
                    st.warning("⚠️ Отличается от дефолта")
                else:
                    st.success("✓ Дефолтное значение")


# ══════════════════════════════════════════════════════════════════════════════
# ТАБ 4: Подписант (signer_profile.json)
# ══════════════════════════════════════════════════════════════════════════════
with tab_signer:
    st.caption("Данные подписанта: компания и ФИО на разных языках. Используются в авто-пайплайне для определения нашей стороны в договоре.")

    from core.signer_profile import load_signer_profile, save_signer_profile

    if "signer_profile_data" not in st.session_state:
        st.session_state["signer_profile_data"] = load_signer_profile()

    sp = st.session_state["signer_profile_data"]

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _render_alias_table(alias_list: list, prefix: str) -> list:
        """Рисует строки алиасов, возвращает обновлённый список."""
        to_delete = []
        for i, alias in enumerate(alias_list):
            c1, c2, c3 = st.columns([2, 6, 1])
            with c1:
                opts = LANGUAGES
                cur_lang = alias.get("language", "ru")
                idx = opts.index(cur_lang) if cur_lang in opts else 0
                alias["language"] = st.selectbox(
                    "Язык",
                    options=opts,
                    index=idx,
                    key=f"{prefix}_lang_{i}",
                    label_visibility="collapsed",
                )
            with c2:
                alias["value"] = st.text_input(
                    "Значение",
                    value=alias.get("value", ""),
                    key=f"{prefix}_val_{i}",
                    label_visibility="collapsed",
                )
            with c3:
                if st.button("🗑", key=f"{prefix}_del_{i}"):
                    to_delete.append(i)

        for idx in sorted(to_delete, reverse=True):
            alias_list.pop(idx)

        if to_delete:
            st.rerun()

        return alias_list

    # ── Компания ──────────────────────────────────────────────────────────────
    st.subheader("Алиасы компании")
    st.caption("Юридическое название на разных языках. Опционально — можно оставить пустым.")

    if not sp.get("company_aliases"):
        st.info("Нет алиасов. Добавьте хотя бы один если хотите искать по названию компании.")

    sp["company_aliases"] = _render_alias_table(sp.setdefault("company_aliases", []), "company")

    if st.button("+ Добавить алиас компании", key="company_add"):
        sp["company_aliases"].append({"language": "ru", "value": ""})
        st.rerun()

    st.divider()

    # ── ФИО подписанта ────────────────────────────────────────────────────────
    st.subheader("Алиасы ФИО подписанта")
    st.caption("Фамилия, Фамилия И.И., Фамилия Имя Отчество — на разных языках. Минимум одно значение.")

    if not sp.get("signer_aliases"):
        st.warning("⚠️ Не задано ни одного алиаса ФИО. Авто-пайплайн не сможет определить нашу сторону.")

    sp["signer_aliases"] = _render_alias_table(sp.setdefault("signer_aliases", []), "signer")

    if st.button("+ Добавить алиас ФИО", key="signer_add"):
        sp["signer_aliases"].append({"language": "ru", "value": ""})
        st.rerun()

    st.divider()

    col_sp_save, col_sp_reload, col_sp_raw = st.columns([1, 1, 2])
    with col_sp_save:
        if st.button("💾 Сохранить подписанта", type="primary", key="sp_save"):
            if not any(a.get("value", "").strip() for a in sp.get("signer_aliases", [])):
                st.error("Нужен хотя бы один алиас ФИО.")
            else:
                try:
                    backup = save_signer_profile(sp)
                    st.success(f"Сохранено. Бэкап: {backup}")
                except Exception as e:
                    st.error(f"Ошибка: {e}")
    with col_sp_reload:
        if st.button("🔄 Перечитать", key="sp_reload"):
            st.session_state.pop("signer_profile_data", None)
            st.rerun()
    with col_sp_raw:
        with st.expander("Raw JSON", expanded=False):
            st.code(json.dumps(sp, ensure_ascii=False, indent=2), language="json")


# ══════════════════════════════════════════════════════════════════════════════
# ТАБ 5: Маркеры подписи (markers.json)
# ══════════════════════════════════════════════════════════════════════════════
with tab_markers:
    st.caption("Универсальные маркеры для поиска мест подписи. Служебная настройка — редактируется редко. JSON напрямую.")

    from core.markers import load_markers, save_markers, DEFAULTS as MARKERS_DEFAULTS

    if "markers_data" not in st.session_state:
        raw = load_markers()
        st.session_state["markers_raw"] = json.dumps(raw, ensure_ascii=False, indent=2)

    col_m_save, col_m_reset, col_m_reload = st.columns([1, 1, 2])

    with col_m_save:
        if st.button("💾 Сохранить маркеры", type="primary", key="markers_save"):
            raw_text = st.session_state.get("markers_editor", "")
            try:
                parsed = json.loads(raw_text)
                backup = save_markers(parsed)
                st.success(f"Сохранено. Бэкап: {backup}")
                st.session_state["markers_raw"] = raw_text
            except json.JSONDecodeError as e:
                st.error(f"Невалидный JSON: {e}")
            except Exception as e:
                st.error(f"Ошибка: {e}")

    with col_m_reset:
        if st.button("↩ Сбросить к дефолтам", key="markers_reset"):
            default_text = json.dumps(MARKERS_DEFAULTS, ensure_ascii=False, indent=2)
            st.session_state["markers_raw"] = default_text
            try:
                save_markers(MARKERS_DEFAULTS)
                st.success("Сброшено к дефолтам")
                st.rerun()
            except Exception as e:
                st.error(f"Ошибка: {e}")

    with col_m_reload:
        if st.button("🔄 Перечитать", key="markers_reload"):
            st.session_state.pop("markers_raw", None)
            st.session_state.pop("markers_data", None)
            st.rerun()

    st.divider()

    markers_text = st.text_area(
        "markers.json",
        value=st.session_state.get("markers_raw", "{}"),
        height=500,
        key="markers_editor",
        label_visibility="collapsed",
    )
