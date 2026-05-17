"""Страница ручной разметки мест подписи.

v1.4.3-fix2:
- FIX: дубликаты при рендере (last_coords guard)
- FIX: режим редактирования (✏️ → следующий клик перемещает)
- FIX: кнопка Применить с явной реакцией
- REMOVED: JSON экспорт из дефолтного вида
"""
import json
import io

import streamlit as st
from PIL import Image
from streamlit_image_coordinates import streamlit_image_coordinates

from core.parser import parse_document
from core.preview import render_page_with_highlights
from core.template_storage import write_template
from core.template_matcher import compute_fingerprint
from core.finder import SignMatch

st.set_page_config(page_title="Ручная разметка — SignFinder", layout="wide")

if not st.session_state.get("auth"):
    st.warning("Войдите через главную страницу.")
    st.stop()

st.title("🖱 Ручная разметка")
st.caption("Кликни по местам подписи на PDF → сохрани как шаблон для повторных документов")

# ── Инициализация session_state ───────────────────────────────────────────────
if "markup_positions" not in st.session_state:
    st.session_state["markup_positions"] = []
if "markup_last_coords" not in st.session_state:
    st.session_state["markup_last_coords"] = None
if "markup_editing_idx" not in st.session_state:
    st.session_state["markup_editing_idx"] = None  # None = режим добавления

# ── ШАГ 1: Загрузка документа ────────────────────────────────────────────────
st.subheader("1️⃣ Загрузить документ")

uploaded = st.file_uploader("PDF или DOCX", type=["pdf", "docx"], key="markup_upload")

if uploaded and st.session_state.get("markup_doc_name") != uploaded.name:
    with st.spinner(f"Парсинг {uploaded.name}..."):
        try:
            doc = parse_document(uploaded.getvalue(), uploaded.name)
            st.session_state.pop("markup_positions", None)
            st.session_state["markup_last_coords"] = None
            st.session_state["markup_editing_idx"] = None
            st.session_state["markup_doc"] = doc
            st.session_state["markup_doc_name"] = uploaded.name
            st.rerun()
        except Exception as e:
            import traceback
            st.error(f"Ошибка парсинга: {e}")
            st.code(traceback.format_exc())

if "markup_doc" not in st.session_state:
    st.info("Загрузите документ для начала разметки.")
    st.stop()

doc = st.session_state["markup_doc"]
c1, c2, c3 = st.columns(3)
c1.metric("Файл", doc.filename)
c2.metric("Страниц", len(doc.pages))
c3.metric("Язык", doc.language.upper())

# ── ШАГ 2: Клик по странице ──────────────────────────────────────────────────
st.subheader("2️⃣ Кликнуть по месту подписи")

page_num = st.number_input(
    "Номер страницы",
    min_value=1, max_value=len(doc.pages), value=1, step=1,
    key="markup_page_num",
) - 1

# Определяем режим
editing_idx = st.session_state["markup_editing_idx"]
if editing_idx is not None:
    st.info(f"✏️ Режим редактирования — кликни по новому месту для позиции {editing_idx + 1}. Нажми Отмена чтобы выйти.")
    if st.button("✖ Отмена редактирования"):
        st.session_state["markup_editing_idx"] = None
        st.rerun()

# Рендер страницы с текущими позициями
positions = st.session_state["markup_positions"]
marks_on_page = [p for p in positions if p["page"] == page_num]

temp_matches = []
for i, pos in enumerate(positions):
    if pos["page"] != page_num:
        continue
    x, y = pos["x"], pos["y"]
    w, h = pos.get("width", 100), pos.get("height", 30)
    # Редактируемая позиция — другой цвет через party-hack
    party = f"[ред.] {pos.get('party_hint','')}" if positions.index(pos) == editing_idx else pos.get("party_hint", "")
    temp_matches.append(SignMatch(
        id=pos["id"], page=page_num,
        bbox=(x - w/2, y - h/2, x + w/2, y + h/2),
        context="", party=party, pattern="manual",
    ))

try:
    png_bytes = render_page_with_highlights(doc.pdf_bytes, page_num, temp_matches, scale=2.0)
    pil_image = Image.open(io.BytesIO(png_bytes))
except Exception as e:
    st.error(f"Ошибка рендера: {e}")
    st.stop()

coords = streamlit_image_coordinates(pil_image, key=f"page_{page_num}_coords")

# Обработка клика — защита от дублирования через last_coords
if coords is not None:
    coords_key = (coords["x"], coords["y"], page_num)
    if coords_key != st.session_state["markup_last_coords"]:
        st.session_state["markup_last_coords"] = coords_key
        scale = 2.0
        x_pt = coords["x"] / scale
        y_pt = coords["y"] / scale

        if editing_idx is not None:
            # Режим редактирования — перемещаем существующую позицию
            st.session_state["markup_positions"][editing_idx]["x"] = x_pt
            st.session_state["markup_positions"][editing_idx]["y"] = y_pt
            st.session_state["markup_positions"][editing_idx]["page"] = page_num
            st.session_state["markup_editing_idx"] = None
        else:
            # Режим добавления — новая позиция
            pos_id = f"manual_{len(st.session_state['markup_positions']) + 1:03d}"
            st.session_state["markup_positions"].append({
                "id": pos_id,
                "page": page_num,
                "x": x_pt,
                "y": y_pt,
                "width": 100,
                "height": 30,
                "party_hint": "",
            })
        st.rerun()

# ── ШАГ 3: Список позиций ────────────────────────────────────────────────────
st.subheader("3️⃣ Размеченные позиции")

if not positions:
    st.info("Нет позиций. Кликните по изображению выше.")
else:
    st.markdown(f"**Всего позиций:** {len(positions)}")

    for idx, pos in enumerate(positions):
        col_info, col_party, col_edit, col_del = st.columns([2, 3, 1, 1])

        with col_info:
            st.caption(f"**{idx+1}.** стр.{pos['page']+1} · x={pos['x']:.0f} y={pos['y']:.0f}")

        with col_party:
            party_val = st.text_input(
                "Роль",
                value=pos.get("party_hint", ""),
                key=f"party_{idx}",
                placeholder="Арендатор",
                label_visibility="collapsed",
            )
            pos["party_hint"] = party_val

        with col_edit:
            if st.button("✏️", key=f"edit_{idx}", help="Переместить — кликни по новому месту"):
                st.session_state["markup_editing_idx"] = idx
                st.rerun()

        with col_del:
            if st.button("🗑", key=f"del_{idx}", help="Удалить позицию"):
                st.session_state["markup_positions"].pop(idx)
                if st.session_state["markup_editing_idx"] == idx:
                    st.session_state["markup_editing_idx"] = None
                st.rerun()

# ── ШАГ 4: Сохранить / Применить ─────────────────────────────────────────────
st.subheader("4️⃣ Сохранить как шаблон или применить")

template_name = st.text_input(
    "Название шаблона",
    placeholder="напр. Договор аренды Ромашка v1",
    key="template_name_input",
)

col_save, col_apply = st.columns(2)

with col_save:
    if st.button("💾 Сохранить шаблон", type="primary", disabled=not template_name.strip()):
        if not positions:
            st.error("Нет позиций.")
        else:
            key_phrases = list({p.get("party_hint", "") for p in positions if p.get("party_hint")})
            fingerprint = compute_fingerprint(doc, key_phrases=key_phrases or None)
            template = {
                "template_id": None,
                "name": template_name.strip(),
                "language": doc.language,
                "fingerprint": fingerprint,
                "signature_positions": positions,
                "created_at": None,
            }
            try:
                template_id = write_template(template)
                st.success(f"✅ Шаблон сохранён · ID: `{template_id}`")
            except Exception as e:
                st.error(f"Ошибка сохранения: {e}")

with col_apply:
    if st.button("▶ Применить к документу (без сохранения)"):
        if not positions:
            st.error("Нет позиций.")
        else:
            matches = []
            for pos in positions:
                x, y = pos["x"], pos["y"]
                w, h = pos.get("width", 100), pos.get("height", 30)
                matches.append(SignMatch(
                    id=pos["id"], page=pos["page"],
                    bbox=(x - w/2, y - h/2, x + w/2, y + h/2),
                    context=f"Ручная разметка: {pos.get('party_hint', '')}",
                    party=pos.get("party_hint", "Ручная"),
                    pattern="manual", confidence=1.0,
                ))
            st.session_state["matches"] = matches
            st.session_state["parsed_doc"] = doc
            st.session_state["search_source"] = "manual"
            st.switch_page("Подписание.py")

            