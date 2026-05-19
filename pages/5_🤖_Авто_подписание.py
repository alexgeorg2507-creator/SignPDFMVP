"""Авто-подписание договоров — SignFinder v1.7.

Флоу:
  1-5. pipelineAuto1 (без изменений)
  6.   Конвертация matches → TextAnchor, пагинация, canvas, ручная доразметка
  7.   Наложение подписи + скачивание
  8.   Сохранение шаблона
"""
import copy
import io
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

import streamlit as st
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
SUPPORTED_LANGUAGES = ("ru", "en", "pl")
CANVAS_SCALE = 2.0   # px per pt при рендере страниц

st.set_page_config(page_title="Авто-подписание — SignFinder", layout="wide")

# ── Auth ─────────────────────────────────────────────────────────────────────
if not st.session_state.get("auth"):
    st.warning("Войдите через главную страницу.")
    st.stop()

from core.storage import read_signature, json_config_exists, read_json, write_json

if "signature_png" not in st.session_state:
    saved = read_signature()
    if saved:
        st.session_state["signature_png"] = saved

sig_png: Optional[bytes] = st.session_state.get("signature_png")
if not sig_png:
    st.error("Подпись не загружена. Загрузите PNG подписи в Настройках.")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline helpers (без изменений от v1.5/v1.6)
# ══════════════════════════════════════════════════════════════════════════════

def _get_header_text(doc) -> str:
    parts = []
    total = 0
    for i, page in enumerate(doc.pages):
        text = page.text or ""
        parts.append(f"--- Страница {i + 1} ---\n{text}")
        total += len(text)
        if i >= 2 or total >= 3000:
            break
    return "\n".join(parts)[:4000]


def _get_strategic_fragments(doc, markers_block: dict) -> str:
    pages = doc.pages
    n = len(pages)
    anchors_kw = [a.lower() for a in markers_block.get("section_anchors", [])]
    fragments = []
    if pages:
        fragments.append(f"=== ПЕРВАЯ СТРАНИЦА ===\n{pages[0].text or ''}")
    if n > 1:
        fragments.append(f"=== ПОСЛЕДНЯЯ СТРАНИЦА ===\n{pages[-1].text or ''}")
    for i in range(1, n - 1):
        text = (pages[i].text or "").lower()
        if any(a in text for a in anchors_kw):
            fragments.append(f"=== СТРАНИЦА {i+1} ===\n{pages[i].text[:2000]}")
    footer_parts = []
    for i, page in enumerate(pages):
        text = (page.text or "").strip()
        footer = text[-200:] if len(text) > 200 else text
        if footer.strip():
            footer_parts.append(f"[стр.{i+1}] {footer}")
    if footer_parts:
        fragments.append("=== ФУТЕРЫ ===\n" + "\n---\n".join(footer_parts[:20]))
    return "\n\n".join(fragments)[:8000]


def _call_llm_json(prompt: str, max_tokens: int = 1500,
                   capture_key: Optional[str] = None) -> Optional[dict]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.error("ANTHROPIC_API_KEY не задан.")
        return None
    if capture_key:
        st.session_state[f"debug_prompt_{capture_key}"] = prompt
    client = Anthropic()
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (resp.content[0].text or "").strip()
        if capture_key:
            st.session_state[f"debug_raw_{capture_key}"] = raw
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.MULTILINE).strip()
        raw = re.sub(r"```$", "", raw, flags=re.MULTILINE).strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            raw = m.group(0)
        return json.loads(raw)
    except Exception as e:
        sys.stderr.write(f"[auto_pipeline] LLM error: {e}\n")
        return None


def _run_step3(doc, lang: str) -> Optional[dict]:
    from core.signer_profile import get_aliases_for_language
    from core.markers import get_markers_for_language
    from core.prompts import format_find_our_side

    aliases = get_aliases_for_language(lang)
    markers_block = get_markers_for_language(lang)

    if not aliases["signer"]:
        st.error("Шаг 3: Не задан алиас ФИО подписанта. Заполните Настройки.")
        return None

    header = _get_header_text(doc)
    prompt = format_find_our_side(
        header_text=header, language=lang,
        company_aliases=aliases["company"], signer_aliases=aliases["signer"],
        markers=markers_block,
    )
    result = _call_llm_json(prompt, max_tokens=1500, capture_key="step3")
    if result is None:
        st.error("Шаг 3: LLM не ответил или невалидный JSON.")
        return None

    confidence = float(result.get("confidence", 0))
    our_index = result.get("our_side_index")
    synonyms = result.get("our_side_synonyms") or {}

    if our_index is None or confidence < 0.5:
        st.error("Шаг 3: Наша сторона не найдена в шапке договора.")
        return None

    return {
        "legal_entity": synonyms.get("legal_entity", ""),
        "roles": synonyms.get("roles", []),
        "signer": synonyms.get("signer", ""),
        "confidence": confidence,
        "match_reason": result.get("match_reason", ""),
        "evidence": result.get("evidence", ""),
        "all_parties": result.get("all_parties", []),
    }


def _run_step4(doc, lang: str, our_side: dict) -> Optional[List[str]]:
    from core.markers import get_markers_for_language
    from core.prompts import format_generate_regex

    markers_block = get_markers_for_language(lang)
    fragments = _get_strategic_fragments(doc, markers_block)
    prompt = format_generate_regex(
        legal_entity=our_side["legal_entity"], roles=our_side["roles"],
        signer=our_side["signer"], language=lang,
        markers_block=markers_block, strategic_fragments=fragments,
    )
    result = _call_llm_json(prompt, max_tokens=3000, capture_key="step4")
    if result is None:
        st.error("Шаг 4: LLM не вернул паттерны.")
        return None

    raw_patterns = result.get("patterns", [])
    patterns = []
    for item in raw_patterns:
        pat = item.get("pattern", "") if isinstance(item, dict) else str(item)
        pat = pat.strip()
        if pat:
            try:
                re.compile(pat, re.IGNORECASE | re.UNICODE)
                patterns.append(pat)
            except re.error as e:
                sys.stderr.write(f"[auto_pipeline] bad pattern '{pat}': {e}\n")
    if not patterns:
        st.error("Шаг 4: Не удалось сгенерировать паттерны.")
        return None
    return patterns


def _extract_distinctive_tokens(s: str) -> list:
    if not s:
        return []
    sl = s.lower().strip()
    if sl in ("не указан", "не указана", "не указано", "—", "-", "n/a", "na", ""):
        return []
    tokens = []
    for m in re.finditer(r'[«"\']([^»"\']+)[»"\']', s):
        inner = m.group(1).strip()
        if len(inner) >= 3:
            tokens.append(inner)
            for w in inner.split():
                if len(w) >= 4:
                    tokens.append(w)
    stop = {
        "общество", "ограниченной", "ответственностью", "компания",
        "корпорация", "генеральный", "директор", "лице", "именуем",
        "именуемая", "именуемое", "именуемый", "далее", "стороны",
        "стороне", "договор", "договору",
    }
    for w in re.findall(r"[А-ЯA-ZЁ][а-яa-zА-ЯA-ZёЁ\-]{3,}", s):
        if w.lower() not in stop:
            tokens.append(w)
    seen, result = set(), []
    for t in tokens:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            result.append(t)
    return result


def _run_step5(doc, our_side: dict, patterns: List[str]):
    from core.finder import find_signatures

    our_entity = (our_side.get("legal_entity") or "").strip()
    our_roles = set(r.strip().lower() for r in our_side.get("roles", []) if r)
    our_signer = (our_side.get("signer") or "").strip()
    our_signer_tokens = set(t.lower() for t in _extract_distinctive_tokens(our_signer))
    our_entity_tokens = set(t.lower() for t in _extract_distinctive_tokens(our_entity))

    other_aliases: list = []
    for p in our_side.get("all_parties", []):
        if not isinstance(p, dict):
            continue
        le = (p.get("legal_entity") or "").strip()
        role = (p.get("role") or "").strip()
        signer_p = (p.get("signer") or "").strip()
        if le and le == our_entity:
            continue
        if role and role.lower() not in our_roles:
            other_aliases.append(role)
        for t in _extract_distinctive_tokens(le):
            if t.lower() not in our_entity_tokens:
                other_aliases.append(t)
        for t in _extract_distinctive_tokens(signer_p):
            if t.lower() not in our_signer_tokens:
                other_aliases.append(t)

    seen = set()
    other_aliases_clean = []
    for a in other_aliases:
        al = a.lower().strip()
        if len(al) >= 3 and al not in seen:
            seen.add(al)
            other_aliases_clean.append(a)

    party_dict = {
        "name": our_side["legal_entity"] or "auto",
        "display": our_side["legal_entity"] or "auto",
        "aliases": ([our_side["legal_entity"]] + our_side.get("roles", []) + [our_side["signer"]]),
        "signer": our_side.get("signer", ""),
        "other_aliases": other_aliases_clean,
        "patterns": patterns,
        "notes": "",
    }
    matches = find_signatures(doc, party_dict)
    if not matches:
        st.error("Шаг 5: Паттерны сгенерированы, но мест подписи не найдено.")
        return None
    return matches


def _build_debug_export() -> dict:
    doc = st.session_state.get("auto_doc")
    our_side = st.session_state.get("auto_our_side") or {}
    patterns = st.session_state.get("auto_patterns") or []
    matches = st.session_state.get("auto_matches") or []
    return {
        "version": "1.7",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "doc_info": {
            "filename": st.session_state.get("auto_doc_name", ""),
            "pages": len(doc.pages) if doc else 0,
            "language": st.session_state.get("auto_language", ""),
        },
        "step3": {
            **{k: our_side.get(k, "") for k in ("legal_entity", "roles", "signer", "confidence", "match_reason")},
            "prompt": st.session_state.get("debug_prompt_step3", ""),
            "raw": st.session_state.get("debug_raw_step3", ""),
        },
        "step4": {
            "patterns": patterns,
            "prompt": st.session_state.get("debug_prompt_step4", ""),
            "raw": st.session_state.get("debug_raw_step4", ""),
        },
        "step5": {
            "total": len(matches),
            "matches": [{"id": m.id, "page": m.page, "bbox": list(m.bbox), "pattern": m.pattern} for m in matches],
        },
    }


def _reset_pipeline():
    for k in [
        "auto_language", "auto_our_side", "auto_patterns", "auto_matches",
        "auto_signed_pdf", "all_anchors", "fingerprint", "current_page",
        "debug_prompt_step3", "debug_raw_step3", "debug_prompt_step4", "debug_raw_step4",
    ]:
        st.session_state.pop(k, None)


# ══════════════════════════════════════════════════════════════════════════════
# v1.7: Anchor + canvas helpers
# ══════════════════════════════════════════════════════════════════════════════

def _get_anchor_page_idx(anchor, total_pages: int) -> Optional[int]:
    hint = str(anchor.page_hint)
    if hint == "first":
        return 0
    if hint == "last":
        return total_pages - 1
    if hint.isdigit():
        return int(hint)
    return None  # "any"


def _anchors_for_page(anchors: list, page_idx: int, total_pages: int) -> list:
    result = []
    for a in anchors:
        pi = _get_anchor_page_idx(a, total_pages)
        if pi is None or pi == page_idx:
            result.append(a)
    return result


def _render_page_pil(fitz_doc, page_idx: int, scale: float = CANVAS_SCALE):
    from PIL import Image
    page = fitz_doc[page_idx]
    import fitz as _fitz
    pix = page.get_pixmap(matrix=_fitz.Matrix(scale, scale))
    return Image.open(io.BytesIO(pix.tobytes("png")))


def _anchor_to_canvas_obj(anchor, scale: float, total_pages: int) -> dict:
    x0, y0, x1, y1 = [c * scale for c in anchor.bbox]
    is_auto = anchor.added_by == "auto_regex"
    return {
        "type": "rect",
        "left": x0, "top": y0,
        "width": max(x1 - x0, 10), "height": max(y1 - y0, 10),
        "fill": "rgba(0,180,0,0.25)" if is_auto else "rgba(255,140,0,0.25)",
        "stroke": "#009900" if is_auto else "#cc6600",
        "strokeWidth": 2,
        "id": anchor.id,
    }


def _handle_canvas_changes(new_objects: list, current_anchors: list,
                            page_idx: int, scale: float, fitz_doc) -> bool:
    from core.anchor_builder import build_anchor_from_click
    lang = st.session_state.get("auto_language", "ru")

    current_by_id = {a.id: a for a in current_anchors}
    new_by_id = {obj["id"]: obj for obj in new_objects if obj.get("id")}
    changed = False

    # Удалённые
    deleted = set(current_by_id.keys()) - set(new_by_id.keys())
    if deleted:
        st.session_state["all_anchors"] = [
            a for a in st.session_state["all_anchors"] if a.id not in deleted
        ]
        changed = True

    # Перемещённые
    for aid, new_obj in new_by_id.items():
        if aid not in current_by_id:
            continue
        old = current_by_id[aid]
        new_x = new_obj.get("left", 0) / scale
        new_y = new_obj.get("top", 0) / scale
        if abs(new_x - old.bbox[0]) < 2 and abs(new_y - old.bbox[1]) < 2:
            continue
        cx = new_x + (new_obj.get("width", 0) / scale) / 2
        cy = new_y + (new_obj.get("height", 0) / scale) / 2
        new_anchor = build_anchor_from_click(fitz_doc, page_idx, cx, cy, lang)
        if new_anchor:
            new_anchor.id = aid
            st.session_state["all_anchors"] = [
                new_anchor if a.id == aid else a
                for a in st.session_state["all_anchors"]
            ]
            changed = True
        else:
            st.warning("Нет якоря в этой точке — позиция не изменена.")

    # Новые (rect без id — в режиме "add")
    for obj in new_objects:
        if obj.get("id"):
            continue
        cx = (obj.get("left", 0) + obj.get("width", 0) / 2) / scale
        cy = (obj.get("top", 0) + obj.get("height", 0) / 2) / scale
        new_anchor = build_anchor_from_click(fitz_doc, page_idx, cx, cy, lang)
        if new_anchor:
            st.session_state["all_anchors"].append(new_anchor)
            changed = True
        else:
            st.warning("Не удалось построить якорь. Кликните над текстом или подчёркиваниями.")

    return changed


def _build_signed_pdf() -> bytes:
    from core.finder import SignMatch
    from core.overlay import apply_signature

    doc = st.session_state["auto_doc"]
    total = len(doc.pages)
    all_anchors = st.session_state.get("all_anchors", [])

    fake_matches = []
    for anchor in all_anchors:
        if not st.session_state.get(f"anchor_enabled_{anchor.id}", True):
            continue
        pi = _get_anchor_page_idx(anchor, total)
        if pi is None:
            pi = 0
        fake_matches.append(SignMatch(
            id=anchor.id, page=pi, bbox=anchor.bbox,
            context=anchor.anchor_text, party="",
            pattern=anchor.generated_pattern,
            confidence=1.0, status="candidate", operator_excluded=False,
        ))

    return apply_signature(doc.pdf_bytes, fake_matches, sig_png)


def _save_template():
    try:
        import fitz as _fitz
        from core.template_storage import DocumentTemplate, save_template
        from core.fingerprint import compute_fingerprint

        doc = st.session_state["auto_doc"]
        lang = st.session_state.get("auto_language", "ru")
        our_side = st.session_state.get("auto_our_side", {})
        all_anchors = st.session_state.get("all_anchors", [])
        has_manual = any(a.added_by == "manual_click" for a in all_anchors)

        template_name = st.session_state.get("template_name_input") or \
            f"pipelineAuto1_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M')}_{lang}"

        fitz_doc = _fitz.open(stream=doc.pdf_bytes, filetype="pdf")
        try:
            fp = compute_fingerprint(fitz_doc, lang)
        finally:
            fitz_doc.close()

        template = DocumentTemplate(
            template_id=uuid4().hex,
            name=template_name,
            language=lang,
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by="manual_enrichment" if has_manual else "pipeline_auto_1",
            fingerprint=fp,
            anchors=[asdict(a) for a in all_anchors],
            synonyms_used={
                "legal_entity": our_side.get("legal_entity", ""),
                "roles": our_side.get("roles", []),
                "signer": our_side.get("signer", ""),
            },
        )
        tid = save_template(template)
        st.success(f"Шаблон сохранён: `{template_name}` (id: {tid[:8]}…)")
    except Exception as e:
        st.error(f"Ошибка сохранения шаблона: {e}")
        sys.stderr.write(f"[auto_sign] _save_template: {e}\n")


# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════

st.title("🤖 Авто-подписание")
st.caption("Загрузи договор — система найдёт места подписи. Дорисуй если нужно → скачай PDF.")

# ── Шаги 1-5: загрузка + пайплайн ────────────────────────────────────────────
uploaded = st.file_uploader("Загрузить договор", type=["pdf", "docx"], key="auto_uploader")

if uploaded is not None and st.session_state.get("auto_doc_name") != uploaded.name:
    _reset_pipeline()
    st.session_state["auto_doc_name"] = uploaded.name

    with st.status("Запускаю пайплайн...", expanded=True) as status:

        st.write("📄 Шаг 1: Парсинг...")
        from core.parser import parse_document
        try:
            doc = parse_document(uploaded.getvalue(), uploaded.name)
            st.session_state["auto_doc"] = doc
            st.write(f"✅ Страниц: {len(doc.pages)}")
        except Exception as e:
            status.update(label="❌ Ошибка парсинга", state="error")
            st.error(f"{e}")
            st.stop()

        st.write("🌐 Шаг 2: Язык...")
        from core.language_detector import detect_language
        lang = detect_language(doc)
        if lang not in SUPPORTED_LANGUAGES:
            status.update(label="❌ Язык не поддерживается", state="error")
            st.error(f"Поддерживаем ru/en/pl. Определён: {lang or '?'}")
            st.stop()
        st.session_state["auto_language"] = lang
        st.write(f"✅ Язык: **{lang}**")

        st.write("🔍 Шаг 3: Наша сторона...")
        our_side = _run_step3(doc, lang)
        if our_side is None:
            status.update(label="❌ Сторона не определена", state="error")
            st.stop()
        st.session_state["auto_our_side"] = our_side
        st.write(f"✅ {our_side['legal_entity']} / {our_side['signer']} ({our_side['confidence']:.0%})")

        with st.expander("Debug шаг 3", expanded=False):
            st.json({k: our_side[k] for k in ("legal_entity", "roles", "signer", "confidence")})

        st.write("⚙️ Шаг 4: Паттерны...")
        patterns = _run_step4(doc, lang, our_side)
        if patterns is None:
            status.update(label="❌ Паттерны не сгенерированы", state="error")
            st.stop()
        st.session_state["auto_patterns"] = patterns
        st.write(f"✅ Паттернов: {len(patterns)}")

        st.write("🔎 Шаг 5: Поиск мест...")
        matches = _run_step5(doc, our_side, patterns)
        if matches is None:
            status.update(label="❌ Места не найдены", state="error")
            st.stop()
        st.session_state["auto_matches"] = matches

        st.write("🔗 Конвертация в якоря...")
        from core.finder import regex_match_to_anchor
        anchors = []
        for m in matches:
            try:
                anchors.append(regex_match_to_anchor(m, m.page, lang))
            except Exception as e:
                sys.stderr.write(f"[auto_sign] anchor conv: {e}\n")
        st.session_state["all_anchors"] = anchors
        st.session_state["current_page"] = 0
        st.write(f"✅ Якорей: {len(anchors)}")
        status.update(label="✅ Готово", state="complete")


if any(st.session_state.get(k) for k in ("debug_prompt_step3", "auto_our_side")):
    with st.expander("Debug JSON", expanded=False):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = st.session_state.get("auto_doc_name", "doc").rsplit(".", 1)[0]
        st.download_button(
            "📥 Экспорт debug JSON",
            data=json.dumps(_build_debug_export(), ensure_ascii=False, indent=2),
            file_name=f"debug_{base}_{ts}.json",
            mime="application/json",
            key="dl_debug",
        )

if "all_anchors" not in st.session_state:
    st.stop()

# ── Шаг 6: Пагинация + Canvas + Доразметка ───────────────────────────────────
doc = st.session_state["auto_doc"]
total_pages = len(doc.pages)
all_anchors: list = st.session_state["all_anchors"]
current_page: int = st.session_state.get("current_page", 0)

st.divider()
st.subheader("2️⃣ Превью и доразметка")

# Якоря текущей страницы + чекбоксы
page_anchors = _anchors_for_page(all_anchors, current_page, total_pages)

if page_anchors:
    st.write(f"Места подписи на стр. {current_page + 1}:")
    ca, cn, _ = st.columns([1, 1, 4])
    with ca:
        if st.button("✅ Все", key="en_all"):
            for a in page_anchors:
                st.session_state[f"anchor_enabled_{a.id}"] = True
            st.rerun()
    with cn:
        if st.button("☐ Снять", key="dis_all"):
            for a in page_anchors:
                st.session_state[f"anchor_enabled_{a.id}"] = False
            st.rerun()

    for i, anchor in enumerate(page_anchors):
        c1, c2, c3 = st.columns([1, 9, 1])
        with c1:
            enabled = st.checkbox(
                "", value=st.session_state.get(f"anchor_enabled_{anchor.id}", True),
                key=f"cb_{anchor.id}",
            )
            st.session_state[f"anchor_enabled_{anchor.id}"] = enabled
        with c2:
            src = "auto" if anchor.added_by == "auto_regex" else "✏️ manual"
            st.caption(f"#{i+1} {src} · Ур.{anchor.anchor_level} · «{anchor.anchor_text[:40]}»")
        with c3:
            if st.button("✕", key=f"del_{anchor.id}"):
                st.session_state["all_anchors"] = [a for a in all_anchors if a.id != anchor.id]
                st.rerun()
else:
    st.caption(f"На стр. {current_page + 1} мест подписи нет.")

# Режим канваса
canvas_mode_label = st.radio(
    "Режим", ["👁 Просмотр / drag", "✏️ Добавить место подписи"],
    horizontal=True, key="canvas_mode_radio",
)
mode_key = "add" if "Добавить" in canvas_mode_label else "view"

# Пагинация (над превью)
nav1, nav2, nav3, nav4 = st.columns([1, 2, 2, 1])
with nav1:
    if st.button("◀", key="pg_prev", disabled=(current_page == 0)):
        st.session_state["current_page"] = current_page - 1
        st.rerun()
with nav2:
    st.markdown(f"**Стр. {current_page + 1}** из {total_pages}")
with nav3:
    jump = st.number_input(
        "Перейти на стр.", min_value=1, max_value=total_pages,
        value=current_page + 1, label_visibility="collapsed", key="pg_jump",
    )
    if jump - 1 != current_page:
        st.session_state["current_page"] = jump - 1
        st.rerun()
with nav4:
    if st.button("▶", key="pg_next", disabled=(current_page >= total_pages - 1)):
        st.session_state["current_page"] = current_page + 1
        st.rerun()

# Превью страницы с подсветкой якорей
_preview_rendered = False
try:
    from core.preview import render_page_with_highlights
    from core.finder import SignMatch as _SM

    pm = []
    for a in page_anchors:
        pi = _get_anchor_page_idx(a, total_pages) or current_page
        if pi == current_page:
            pm.append(_SM(
                id=a.id, page=pi, bbox=a.bbox, context=a.anchor_text,
                party="", pattern=a.generated_pattern,
                operator_excluded=not st.session_state.get(f"anchor_enabled_{a.id}", True),
            ))

    img_bytes = render_page_with_highlights(doc.pdf_bytes, current_page, pm, scale=1.5)

    if mode_key == "add":
        # Кликабельное превью — клик добавляет якорь
        from streamlit_image_coordinates import streamlit_image_coordinates
        from PIL import Image

        pil_img = Image.open(io.BytesIO(img_bytes))
        img_w, img_h = pil_img.size
        click_scale = 1.5  # scale при рендере превью

        coords = streamlit_image_coordinates(
            pil_img,
            key=f"click_{current_page}_{len(page_anchors)}",
        )

        if coords is not None:
            click_x_pt = coords["x"] / click_scale
            click_y_pt = coords["y"] / click_scale

            # Проверка: не обработали ли мы этот клик уже
            last_click = st.session_state.get("_last_click")
            this_click = (current_page, round(click_x_pt, 1), round(click_y_pt, 1))
            if last_click != this_click:
                st.session_state["_last_click"] = this_click
                try:
                    import fitz as _fitz
                    from core.anchor_builder import build_anchor_from_click
                    _fd = _fitz.open(stream=doc.pdf_bytes, filetype="pdf")
                    new_a = build_anchor_from_click(
                        _fd, current_page, click_x_pt, click_y_pt,
                        st.session_state.get("auto_language", "ru"),
                    )
                    _fd.close()
                    if new_a:
                        st.session_state["all_anchors"].append(new_a)
                        st.rerun()
                    else:
                        st.warning("Нет текста в этой точке. Кликните ближе к тексту или подчёркиваниям.")
                except Exception as e:
                    st.error(f"Ошибка добавления: {e}")
    else:
        # Режим просмотра — статичное превью
        st.image(img_bytes, use_container_width=True)

    _preview_rendered = True
except Exception as e:
    st.warning(f"Превью недоступно: {e}")

if mode_key == "add" and not _preview_rendered:
    st.caption("Превью недоступно — ручной ввод координат:")
    mx_col, my_col, madd_col = st.columns([2, 2, 1])
    with mx_col:
        mx = st.number_input("X (pt)", min_value=0.0, value=100.0, key="man_x")
    with my_col:
        my = st.number_input("Y (pt)", min_value=0.0, value=200.0, key="man_y")
    with madd_col:
        st.write(""); st.write("")
        if st.button("➕", key="btn_add_manual"):
            try:
                import fitz as _fitz
                from core.anchor_builder import build_anchor_from_click
                _fd = _fitz.open(stream=doc.pdf_bytes, filetype="pdf")
                a = build_anchor_from_click(_fd, current_page, mx, my,
                                            st.session_state.get("auto_language", "ru"))
                _fd.close()
                if a:
                    st.session_state["all_anchors"].append(a)
                    st.rerun()
                else:
                    st.warning("Нет текста в этой точке.")
            except Exception as e:
                st.error(f"{e}")


# ── Шаг 7: Скачивание ────────────────────────────────────────────────────────
st.divider()
st.subheader("7️⃣ Скачать подписанный PDF")

auto_n = sum(1 for a in all_anchors if a.added_by == "auto_regex")
manual_n = sum(1 for a in all_anchors if a.added_by == "manual_click")
enabled_n = sum(1 for a in all_anchors if st.session_state.get(f"anchor_enabled_{a.id}", True))

st.caption(f"{auto_n} auto + {manual_n} manual = {len(all_anchors)} якорей · включено: {enabled_n}")

if enabled_n == 0:
    st.warning("Нет включённых мест подписи.")
else:
    if st.button("⬇ Подписать и скачать", type="primary", key="btn_sign"):
        try:
            signed = _build_signed_pdf()
            st.session_state["auto_signed_pdf"] = signed
        except Exception as e:
            st.error(f"Ошибка: {e}")
            sys.stderr.write(f"[auto_sign] _build_signed_pdf: {e}\n")

    if "auto_signed_pdf" in st.session_state:
        fname = st.session_state.get("auto_doc_name", "doc").rsplit(".", 1)[0]
        st.download_button(
            "💾 Сохранить PDF",
            data=st.session_state["auto_signed_pdf"],
            file_name=f"{fname}_signed.pdf",
            mime="application/pdf",
            key="dl_signed",
        )


# ── Шаг 8: Сохранение шаблона ────────────────────────────────────────────────
st.divider()
st.subheader("💾 Сохранение шаблона")

has_manual = any(a.added_by == "manual_click" for a in all_anchors)
lang_h = st.session_state.get("auto_language", "ru")

try:
    from core.template_storage import generate_template_name
    def_name = generate_template_name(lang_h, st.session_state.get("auto_our_side"))
except Exception:
    def_name = f"pipelineAuto1_{datetime.now().strftime('%Y-%m-%d_%H%M')}_{lang_h}"

st.text_input("Имя шаблона", value=def_name, key="template_name_input")

_lbl = "💾 Сохранить шаблон (рекомендуется — есть ручные якоря)" if has_manual else "💾 Сохранить шаблон"
_typ = "primary" if has_manual else "secondary"

if st.button(_lbl, type=_typ, key="btn_save_tpl"):
    _save_template()

st.caption("Matching по шаблонам — v1.8.")
st.divider()
st.caption("SignFinder MVP v1.7")
