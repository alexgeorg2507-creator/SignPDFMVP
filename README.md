# Техническое задание: SignFinder MVP
## Система автоматического поиска мест подписи в договорах

**Версия:** 0.5
**Дата:** май 2025
**Статус:** Согласовано к реализации

---

## 1. Цель и контекст

Система позволяет оператору загрузить договор (PDF или DOCX), автоматически найти места для подписи для заданной стороны и наложить PNG-подпись. Результат — PDF с подписями, готовый к скачиванию.

Языки договоров: **RU, EN, PL**
Юрисдикции: РФ / ЕАЭС / EU (Польша)

---

## 2. Конфигурационные файлы

### 2.1 `parties.md` — реестр сторон

```markdown
# Реестр сторон договора

## СТОРОНА: Заказчик
aliases:
  - Заказчик
  - Customer
  - Zamawiający
sign_patterns:
  - "Заказчик[\\s_]*_{3,}"
  - "_{3,}[\\s]*\\(Лебедев"
  - "Customer[\\s_]*_{3,}"
notes: "Физлицо или ИП. Подпись + расшифровка ФИО в скобках."

---

## СТОРОНА: Клиент
aliases:
  - Клиент
  - Client
  - Klient
sign_patterns:
  - "Клиент[\\s_]*_{3,}"
  - "Client[\\s_]*_{3,}"
notes: "Подпись в таблице реквизитов и под таблицей (2 места)."

---

## СТОРОНА: Арендатор
aliases:
  - Арендатор
  - Tenant
  - Najemca
sign_patterns:
  - "Арендатор[:\\s]*_{3,}"
  - "Подпись_{3,}"
  - "Tenant[\\s_]*_{3,}"
notes: "Шаблонный договор — искать по роли, не по ФИО."
```

### 2.2 `corrections.md` — база корректировок ошибок

```markdown
# База корректировок SignFinder

## COR-001: Декоративный колонтитул-подпись
**Проблема:** Строка "Заказчик____" повторяется в колонтитуле каждой страницы.
**Признак:** паттерн совпадает на 4+ страницах подряд в одной позиции Y
**Действие:** flag=decorative — не предлагать оператору
**Пример:** Договор_Лебдев_А_П_.pdf, стр. 1–4

---

## COR-002: Дубли строки подписи — намеренные
**Проблема:** "Клиент ______" дважды подряд — два экземпляра договора.
**Признак:** идентичный паттерн на расстоянии < 3 строк
**Действие:** count=2, предложить обе позиции отдельно

---

## COR-003: Подпись внутри ячейки таблицы
**Проблема:** pdfplumber возвращает bbox всей ячейки.
**Признак:** in_table=true, высота bbox > 40px
**Действие:** сузить bbox до нижней трети ячейки

---

## COR-004: Место уже подписано
**Проблема:** В bbox уже есть изображение.
**Признак:** image-объект в bbox или отсутствие символов "_"
**Действие:** status=already_signed, предупредить оператора
```

---

## 3. UI: одностраничное приложение

```
┌────────────────────────────────────────────────────────┐
│  🔐  КОД ДОСТУПА  [____________]  [Войти]              │
│      (блокирует весь UI до ввода верного кода)         │
├────────────────────────────────────────────────────────┤
│  ⚙️  НАСТРОЙКИ  (expander, свёрнут по умолчанию)       │
│  ┌──────────────────┬───────────────────────────────┐  │
│  │ parties.md       │ corrections.md                │  │
│  │ [textarea]       │ [textarea]                    │  │
│  │ [💾 Сохранить]   │ [💾 Сохранить]                │  │
│  └──────────────────┴───────────────────────────────┘  │
│  Подпись: [Загрузить PNG]  [превью 80x40px]            │
├────────────────────────────────────────────────────────┤
│  📄  ДОКУМЕНТ                                          │
│  [Загрузить PDF / DOCX]                                │
│  Язык: 🇷🇺 RU (autodetect)   Сторона: [dropdown ▼]    │
│  [🔍 Найти места подписи]                              │
├────────────────────────────────────────────────────────┤
│  ✅  РЕЗУЛЬТАТ                                         │
│  Найдено: 3 места                                      │
│  ☑ стр.3 | "Заказчик____" | conf: 0.95                │
│  ☑ стр.5 | "(Лебедев А.П.)" | conf: 0.91              │
│  ☐ стр.1 | decorative (COR-001) — авто-исключено      │
│                                                        │
│  ☐ Защитить от редактирования (flatten PDF)           │
│  [▶ Применить подпись]                                 │
│                                                        │
│  [Предпросмотр — страницы с подписями]                 │
│  [⬇ Скачать PDF]                                       │
│                                                        │
│  ─────────────────────────────────────────────────     │
│  📋 [Техническое задание SignFinder MVP v0.5] (link)   │
└────────────────────────────────────────────────────────┘
```

---

## 4. Функциональные требования

### 4.1 Аутентификация

| ID | Требование |
|----|-----------|
| F-00 | Поле ввода промокода на странице до открытия UI |
| F-01 | Промокод сравнивается с env var `ACCESS_CODE` |
| F-02 | При совпадении: `session_state["auth"] = True`, UI разблокируется |
| F-03 | При несовпадении: сообщение об ошибке, UI остаётся закрытым |

### 4.2 Настройки

| ID | Требование |
|----|-----------|
| F-10 | Просмотр и редактирование `parties.md` в UI (textarea) |
| F-11 | Просмотр и редактирование `corrections.md` в UI |
| F-12 | Сохранение в GCS с бэкапом (`parties_20250510_143200.md`) |
| F-13 | Загрузка PNG подписи (RGBA), превью в UI |
| F-14 | PNG хранится в `session_state`; при перезагрузке — загрузить повторно |

### 4.3 Обработка документа

| ID | Требование |
|----|-----------|
| F-20 | Загрузка PDF или DOCX, до 50 МБ |
| F-21 | Автодетект языка (langdetect) |
| F-22 | Выбор стороны из `parties.md` (dropdown по заголовкам `## СТОРОНА:`) |
| F-23 | Поиск по regex-паттернам выбранной стороны |
| F-24 | Валидация через Claude API `claude-sonnet-4-6` |
| F-25 | Применение корректировок из `corrections.md` |
| F-26 | Список мест: страница, контекст, confidence, статус корректировки |

### 4.4 Предпросмотр и наложение

| ID | Требование |
|----|-----------|
| F-30 | Предпросмотр страниц с подсветкой bbox (красный прямоугольник) |
| F-31 | Чекбокс исключения каждого места оператором |
| F-32 | PNG накладывается as-is без масштабирования, левый край bbox |
| F-33 | Опция flatten PDF перед применением |
| F-34 | Предпросмотр результата inline |
| F-35 | Скачивание итогового PDF |
| F-36 | Документ не сохраняется на сервере |

---

## 5. Нефункциональные требования

### 5.1 Деплой
- **GCP Cloud Run** — контейнер Docker, serverless, привязка к текущему Google Account
- Конфиги `parties.md` / `corrections.md` — **GCP Cloud Storage** bucket
- Env vars в Cloud Run: `ACCESS_CODE`, `ANTHROPIC_API_KEY`, `GCS_BUCKET`
- Смена промокода: обновить env var в Cloud Run Console → restart (редеплой не нужен)

### 5.2 Производительность
- Обработка до 10 страниц: < 30 сек
- Cloud Run: `min-instances=0` (cold start ~5 сек), `max-instances=1`
- Memory: 2GB (LibreOffice + pymupdf)

---

## 6. Архитектура

### 6.1 Компонентная схема

```
┌─────────────────────────────────────────────────────┐
│                   GCP Cloud Run                     │
│                                                     │
│  ┌──────────┐    ┌─────────────────────────────┐   │
│  │ app.py   │───▶│         core/               │   │
│  │Streamlit │    │  auth.py      → env var      │   │
│  │  UI      │    │  storage.py   → GCS          │   │
│  │          │    │  parser.py    → fitz/docx    │   │
│  │          │    │  finder.py    → regex        │   │
│  │          │    │  validator.py → Claude API   │   │
│  │          │    │  corrector.py → corrections  │   │
│  │          │    │  overlay.py   → fitz PNG     │   │
│  └──────────┘    └─────────────────────────────┘   │
│                                                     │
└──────────────┬──────────────┬───────────────────────┘
               │              │
    ┌──────────▼──┐    ┌──────▼──────────┐
    │ GCS bucket  │    │  Anthropic API  │
    │ parties.md  │    │  claude-sonnet  │
    │corrections  │    └─────────────────┘
    └─────────────┘
```

### 6.2 Структура проекта

```
signfinder/
├── app.py                   # Streamlit entry point, UI логика
├── Dockerfile               # python:3.11-slim + LibreOffice + deps
├── requirements.txt
├── .env.example             # ACCESS_CODE, ANTHROPIC_API_KEY, GCS_BUCKET
├── config/                  # локально для разработки
│   ├── parties.md
│   └── corrections.md
└── core/
    ├── auth.py              # check_access_code(input) → bool
    ├── storage.py           # read/write MD: local (dev) / GCS (prod)
    ├── parser.py            # load_document() → pages[{text, bbox, images}]
    ├── finder.py            # find_signatures(pages, party) → [SignMatch]
    ├── validator.py         # validate_with_llm([SignMatch]) → [SignMatch+confidence]
    ├── corrector.py         # apply_corrections([SignMatch], corrections) → [SignMatch]
    └── overlay.py           # apply_signature(pdf_bytes, matches, png, flatten) → pdf_bytes
```

### 6.3 Ключевые модули

**`parser.py`**
```
load_document(file_bytes, mime_type)
  PDF  → fitz.open() → [{page, text, words+bbox, images}]
  DOCX → convert to PDF via LibreOffice → fitz.open()
```

**`finder.py`**
```
find_signatures(pages, party_config)
  1. parse party_config → aliases, patterns
  2. for each page: regex search over text
  3. map match position → bbox via word coordinates
  4. return [SignMatch(page, bbox, context, raw_pattern)]
```

**`validator.py`**
```
validate_with_llm(matches, document_text)
  prompt → Claude Sonnet:
    "Это место для подписи стороны {party}? Контекст: {context}"
  returns confidence 0..1, filtered list
```

**`corrector.py`**
```
apply_corrections(matches, corrections_md)
  parse corrections_md → [Correction(id, trigger, action)]
  for each match: check triggers → apply action
    decorative   → exclude
    in_table     → adjust bbox
    already_signed → flag warning
```

**`overlay.py`**
```
apply_signature(pdf_bytes, matches, png_bytes, flatten=False)
  doc = fitz.open(pdf_bytes)
  for match in matches:
    page = doc[match.page]
    rect = fitz.Rect(match.bbox)
    page.insert_image(rect, stream=png_bytes, keep_proportion=True)
  if flatten: doc.save(..., deflate=True, clean=True)
  return doc.tobytes()
```

---

## 7. Этапы реализации

### Этап 0 — Инфраструктура GCP (2–3 часа)

```
□ Создать GCS bucket: gs://signfinder-config/
□ Загрузить начальные parties.md и corrections.md в bucket
□ Создать service account с правами:
    roles/storage.objectAdmin (bucket)
□ Настроить Cloud Run env vars:
    ACCESS_CODE=<промокод>
    ANTHROPIC_API_KEY=<ключ>
    GCS_BUCKET=signfinder-config
□ Написать Dockerfile (base: python:3.11-slim)
    + apt: libreoffice-writer, fonts-liberation
    + pip: -r requirements.txt
□ Тест: docker build + docker run локально
```

### Этап 1 — Аутентификация (1–2 часа)

```
□ app.py: если не authenticated → показать только форму кода
□ core/auth.py: compare(input, os.environ["ACCESS_CODE"])
□ session_state["auth"] = True при успехе
□ Тест: верный код → UI, неверный → ошибка
```

### Этап 2 — Storage и конфиги (2–3 часа)

```
□ core/storage.py:
    read_md(filename) → str
      dev: open("config/{filename}")
      prod: gcs_client.bucket.blob(filename).download_as_text()
    write_md(filename, content) → None
      + backup: filename_YYYYMMDD_HHMMSS.md
□ app.py: панель настроек — textarea + кнопка сохранить
□ parse_parties(md_text) → [{name, aliases, patterns, notes}]
□ parse_corrections(md_text) → [{id, trigger, action, notes}]
□ Тест: редактировать parties.md в UI → сохранить → перечитать
```

### Этап 3 — Парсинг документа (3–4 часа)

```
□ core/parser.py:
    PDF: fitz.open(stream=bytes) → extract words+bbox per page
    DOCX: subprocess LibreOffice --headless --convert-to pdf
          → result PDF → fitz
□ app.py: загрузка файла, детект языка (langdetect)
□ Тест: загрузить Договор_Лебдев_А_П_.pdf
         → получить список слов с координатами
```

### Этап 4 — Поиск мест подписи (3–4 часа)

```
□ core/finder.py: regex по паттернам из parties.md
□ Маппинг позиции в тексте → bbox через word-координаты fitz
□ app.py: dropdown сторон → кнопка поиска → список результатов
□ Тест на 4 загруженных договорах:
    Договор_Лебдев_А_П_.pdf     → Заказчик → 7 мест (стр.1-5,6,7)
    Договор_аренды.pdf          → Арендатор → 2 места (стр.4, стр.5)
    BSS_ДОГОВОР.pdf             → Заказчик → 1 место (стр.3)
    ТАМОЖЕННЫЙ_ПРЕДСТАВИТЕЛЬ.docx → Клиент → 3 места
```

### Этап 5 — LLM валидация и корректировки (3–4 часа)

```
□ core/validator.py: батчевый промпт в Claude API
    input: список мест с контекстом
    output: confidence + is_signature (bool)
□ core/corrector.py: применить corrections.md
    COR-001: исключить декоративные колонтитулы
    COR-002: разделить дубли
    COR-003: скорректировать bbox таблиц
    COR-004: пометить уже подписанные
□ Тест: COR-001 должен убрать стр.1-4 из Договора ЦИС
```

### Этап 6 — Предпросмотр и наложение подписи (4–5 часов)

```
□ Предпросмотр: рендер страниц PDF как изображений (fitz → PNG)
    нарисовать красный bbox на найденных местах
□ Чекбоксы исключения для каждого места
□ Загрузка PNG подписи → session_state
□ core/overlay.py: insert_image в bbox, as-is
□ Опция flatten: fitz save с флагом
□ Предпросмотр результата: рендер страниц после наложения
□ Кнопка скачать → st.download_button(pdf_bytes)
□ Тест end-to-end: договор → поиск → подпись → скачать PDF
```

### Этап 7 — Деплой на Cloud Run (2–3 часа)

```
□ gcloud builds submit --tag gcr.io/{project}/signfinder
□ gcloud run deploy signfinder \
    --image gcr.io/{project}/signfinder \
    --platform managed \
    --region europe-west1 \
    --memory 2Gi \
    --set-env-vars ACCESS_CODE=xxx,ANTHROPIC_API_KEY=xxx,GCS_BUCKET=xxx \
    --min-instances 0 \
    --max-instances 1 \
    --allow-unauthenticated
□ Тест: открыть URL → ввести промокод → прогнать тестовый договор
```

---

## 8. Зависимости (requirements.txt)

```
streamlit==1.35.0
pymupdf==1.24.0
python-docx==1.1.0
langdetect==1.0.9
anthropic==0.28.0
google-cloud-storage==2.17.0
Pillow==10.3.0
```

---

## 9. Внутренний формат результата (SignMatch)

```python
@dataclass
class SignMatch:
    id: str               # "sig_001"
    page: int             # 0-indexed
    bbox: list            # [x0, y0, x1, y1] в pt
    context: str          # текст вокруг места подписи
    raw_pattern: str      # какой regex сработал
    confidence: float     # 0..1 от LLM
    status: str           # confirmed / decorative / already_signed
    correction_applied: str | None  # "COR-001" или None
    operator_excluded: bool         # оператор снял чекбокс
```

---

## 10. Ограничения MVP (out of scope)

- Несколько PNG-подписей за одну операцию
- Масштабирование подписи
- Ручное добавление мест кликом по странице
- Хранение документов на сервере
- Аудит-лог
- Многопользовательский режим
- Ролевая модель доступа
