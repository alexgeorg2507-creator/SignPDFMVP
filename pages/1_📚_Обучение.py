"""Страница обучения — извлечение regex-паттернов мест подписи из образца договора.

Streamlit multi-page: pages/1_📚_Обучение.py
"""
import streamlit as st

from core.auth import check_access_code
from core.finder import find_signatures
from core.parser import parse_document
from core.pattern_extractor import extract_patterns, merge_patterns_into_json
from core.preview import render_page_with_highlights
from core.storage import json_config_exists, read_json, write_json

st.set_page_config(page_title="Обучение — SignFinder", layout="wide")

# ── AUTH ─────────────────────────────────────────────────────────────────────
if not st.session_state.get("auth"):
    st.warning("Войдите через главную страницу.")
    st.stop()

st.title("📚 Обучение системы")
st.caption("Загрузи образец договора — ИИ извлечёт regex-паттерны мест подписи для выбранной стороны")


def _reset_results():
    """Сброс состояния анализа (при смене документа/стороны/после сохранения)."""
    for k in (
        "train_result",
        "train_test_matches",
        "train_party_name",
        "train_lang",
        "train_patterns_textarea",
    ):
        st.session_state.pop(k, None)


# ── ШАГ 1: Загрузка образца ──────────────────────────────────────────────────
st.subheader("1️⃣ Образец договора")

uploaded = st.file_uploader("PDF или DOCX", type=["pdf", "docx"], key="train_upload")

if uploaded and st.session_state.get("train_doc_name") != uploaded.name:
    with st.spinner(f"Парсинг {uploaded.name}..."):
        try:
            doc = parse_document(uploaded.getvalue(), uploaded.name)
            st.session_state["train_doc"] = doc
            st.session_state["train_doc_name"] = uploaded.name
            _reset_results()
            st.rerun()
        except Exception as e:
            st.error(f"Ошибка парсинга: {e}")

if "train_doc" not in st.session_state:
    st.info("Загрузите образец договора для начала обучения.")
    st.stop()

doc = st.session_state["train_doc"]
c1, c2, c3 = st.columns(3)
c1.metric("Файл", doc.filename)
c2.metric("Страниц", len(doc.pages))
c3.metric("Язык", doc.language.upper())

with st.expander("👁 Превью первой и последней страницы", expanded=False):
    try:
        # Первая страница
        thumb_first = render_page_with_highlights(doc.pdf_bytes, 0, [])
        st.image(thumb_first, caption="Страница 1 (первая)", width=500)
        
        # Последняя страница (если есть)
        if len(doc.pages) > 1:
            thumb_last = render_page_with_highlights(doc.pdf_bytes, len(doc.pages) - 1, [])
            st.image(thumb_last, caption=f"Страница {len(doc.pages)} (последняя)", width=500)
    except Exception as e:
        st.caption(f"Не удалось отрисовать: {e}")

# ── ШАГ 2: Сторона и язык ────────────────────────────────────────────────────
st.subheader("2️⃣ Сторона и язык")

existing_parties: list[str] = []
try:
    if json_config_exists("parties.json"):
        existing_parties = list(read_json("parties.json").get("parties", {}).keys())
except Exception:
    pass

col_a, col_b = st.columns(2)

with col_a:
    party_options = existing_parties + ["[ + Новая сторона ]"]
    chosen = st.selectbox(
        "Сторона из реестра",
        options=party_options,
        key="train_party_select",
    )
    if chosen == "[ + Новая сторона ]":
        party_name = st.text_input(
            "Название новой стороны *",
            placeholder="напр. Wykonawca",
            key="train_new_party_input",
        ).strip()
    else:
        party_name = chosen

with col_b:
    lang_options = ["auto", "ru", "en", "pl"]
    auto_idx = 0
    if doc.language in ("ru", "en", "pl"):
        auto_idx = lang_options.index(doc.language)
    lang_raw = st.selectbox(
        "Язык договора",
        options=lang_options,
        index=auto_idx,
        key="train_language",
    )
    language = doc.language if lang_raw == "auto" else lang_raw
    if language not in ("ru", "en", "pl"):
        language = "ru"

# ── ШАГ 3: Анализ ИИ ─────────────────────────────────────────────────────────
st.subheader("3️⃣ Анализ ИИ")

has_result = "train_result" in st.session_state

# Textarea уточнения — ВСЕГДА доступна (и для первого вызова, и для повторного)
refinement = st.text_area(
    "✏ Уточнение / инструкции для ИИ (необязательно)",
    placeholder=(
        'напр. "На странице 1 подписи не нужны, искать только в конце документа" '
        'или "Подпись арендатора всегда после слова Подпись:"'
    ),
    height=80,
    key="train_refinement",
)

btn_label = "🔄 Повторить анализ с уточнением" if has_result else "▶ Проанализировать"
btn_disabled = not party_name

if st.button(btn_label, type="primary", disabled=btn_disabled):
    with st.spinner("LLM анализирует договор..."):
        prev = st.session_state.get("train_result")
        result = extract_patterns(
            doc,
            party_name,
            language,
            refinement=(refinement.strip() if refinement else None),
            previous_result=prev,
        )
    st.session_state["train_result"] = result
    st.session_state["train_party_name"] = party_name
    st.session_state["train_lang"] = language
    # Сбрасываем textarea редактирования паттернов — переинициализируется новыми
    st.session_state.pop("train_patterns_textarea", None)
    st.session_state.pop("train_test_matches", None)
    st.rerun()

# Показ результата: reasoning + locations + счётчики (БЕЗ редактируемых паттернов здесь)
if "train_result" in st.session_state:
    result = st.session_state["train_result"]

    if result.get("error"):
        st.error(f"❌ {result['error']}")
        st.stop()

    n_patterns = len(result.get("patterns", []))
    n_locations = len(result.get("found_locations", []))
    st.success(f"Найдено мест подписи: **{n_locations}** · Сгенерировано паттернов: **{n_patterns}**")

    if result.get("reasoning"):
        with st.expander("💬 Рассуждение ИИ", expanded=False):
            st.caption(result["reasoning"])

    locations = result.get("found_locations", [])
    if locations:
        with st.expander(f"📍 Найденные места ({len(locations)})", expanded=True):
            for loc in locations:
                pg = loc.get("page", "?")
                line = loc.get("line", "")
                ctx = loc.get("context", "")
                st.caption(f"стр. {pg} · `{line}` · {ctx}")
else:
    st.stop()

# ── Инициализация источника истины для паттернов ─────────────────────────────
# При новом результате train_patterns_textarea удалён выше → инициализируем заново.
# При редактировании в шаге 5 — session_state обновляется автоматически по ключу.
if "train_patterns_textarea" not in st.session_state:
    st.session_state["train_patterns_textarea"] = "\n".join(result.get("patterns", []))


def _current_patterns() -> list[str]:
    raw = st.session_state.get("train_patterns_textarea", "")
    return [p.strip() for p in raw.split("\n") if p.strip()]


# ── ШАГ 4: Предпросмотр ──────────────────────────────────────────────────────
st.subheader("4️⃣ Предпросмотр — применение паттернов к образцу")

col_btn, col_chk = st.columns([2, 3])
with col_btn:
    apply_btn = st.button("🔍 Применить паттерны к документу", key="train_preview_btn")
with col_chk:
    use_llm_in_preview = st.checkbox(
        "С LLM-валидацией (как в основном поиске)",
        value=True,
        key="train_preview_use_llm",
        help=(
            "Применяет ту же LLM-валидацию что и при подписании — отсеивает "
            "ложные срабатывания. Для табличных документов важно: regex "
            "найдёт все 'Подпись___', LLM отделит сторону Арендатор от Арендодателя."
        ),
    )

if apply_btn:
    patterns_now = _current_patterns()
    if not patterns_now:
        st.error("Нет паттернов для проверки. Отредактируй паттерны в шаге 5 или перезапусти анализ.")
    else:
        temp_party = {
            "name": st.session_state.get("train_party_name", "?"),
            "patterns": patterns_now,
        }
        with st.spinner("Ищу по паттернам..."):
            try:
                test_matches = find_signatures(doc, temp_party)
                if use_llm_in_preview and test_matches:
                    from core.validator import validate_with_llm
                    with st.spinner("LLM-валидация..."):
                        test_matches = validate_with_llm(
                            test_matches,
                            st.session_state.get("train_party_name", "?"),
                        )
                st.session_state["train_test_matches"] = test_matches
            except Exception as e:
                st.error(f"Ошибка поиска: {e}")
        st.rerun()

if "train_test_matches" in st.session_state:
    test_matches = st.session_state["train_test_matches"]
    active = [m for m in test_matches if m.status not in ("rejected_by_llm", "decorative")]
    rejected = [m for m in test_matches if m.status in ("rejected_by_llm", "decorative")]

    if not active and not rejected:
        st.warning("⚠ Паттерны ничего не нашли. Уточни описание в шаге 3 или отредактируй паттерны в шаге 5.")
    elif not active and rejected:
        st.warning(
            f"⚠ Паттерны нашли {len(rejected)} мест, но LLM-валидация отклонила все как не относящиеся "
            f"к стороне. Возможно паттерны слишком общие или сторона не та."
        )
    else:
        msg = f"✅ Паттерны находят **{len(active)}** мест подписи"
        if rejected:
            msg += f" · LLM отклонил {len(rejected)} ложных"
        st.success(msg)
        
        # Собираем страницы: первая, последняя, страницы с активными местами
        pages_with_matches = sorted({m.page for m in active})
        pages_to_show = set(pages_with_matches)
        pages_to_show.add(0)  # первая
        if len(doc.pages) > 1:
            pages_to_show.add(len(doc.pages) - 1)  # последняя
        
        for page_num in sorted(pages_to_show):
            page_matches = [m for m in active if m.page == page_num]
            try:
                png = render_page_with_highlights(doc.pdf_bytes, page_num, page_matches)
                caption = f"Страница {page_num + 1}"
                if page_num == 0:
                    caption += " (первая)"
                elif page_num == len(doc.pages) - 1 and page_num != 0:
                    caption += " (последняя)"
                st.image(png, caption=caption)
            except Exception as e:
                st.caption(f"Ошибка рендера стр. {page_num + 1}: {e}")

# ── ШАГ 5: Сохранение паттернов в реестр ─────────────────────────────────────
st.subheader("5️⃣ Сохранить паттерны в реестр")

# Редактируемый блок паттернов — теперь здесь
st.markdown("**Паттерны (можно редактировать, по одному на строку)**")
st.text_area(
    "patterns_editor",
    height=180,
    key="train_patterns_textarea",  # источник истины — используется шагами 4 и 5
    label_visibility="collapsed",
)

save_party = st.session_state.get("train_party_name", party_name)
save_lang = st.session_state.get("train_lang", language)

st.info(
    f"Паттерны будут добавлены к стороне **{save_party}** "
    f"(язык: **{save_lang}**) в `parties.json`. Дубли пропускаются. "
    f"Бэкап создаётся автоматически."
)

if st.button("💾 Сохранить паттерны", type="primary"):
    final_patterns = _current_patterns()
    if not final_patterns:
        st.error("Нет паттернов для сохранения.")
    elif not save_party:
        st.error("Не указана сторона.")
    else:
        try:
            if json_config_exists("parties.json"):
                json_data = read_json("parties.json")
            else:
                json_data = {"version": "2.0", "parties": {}}

            added = merge_patterns_into_json(
                json_data,
                party_name=save_party,
                language=save_lang,
                new_patterns=final_patterns,
            )
            backup_name = write_json("parties.json", json_data)

            if added > 0:
                st.success(f"✅ Добавлено новых паттернов: {added}. Бэкап: {backup_name}")
            else:
                st.info("Все паттерны уже есть в реестре — ничего нового не добавлено.")

            _reset_results()

        except Exception as e:
            st.error(f"Ошибка сохранения: {e}")