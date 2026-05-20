\# SignFinder MVP — контекст для Claude



\## Стек

Python, Streamlit, PyMuPDF (fitz), Claude API (claude-sonnet-4-6), GCP Cloud Run, GCS.



\## Структура

\- app.py — dashboard

\- core/ — бизнес-логика, без UI

\- pages/ — Streamlit страницы

\- docs/ — markdown документация



\## Текущая версия

v1.7 в продакшене, разработка v1.8.



\## Правила работы со мной

1\. Перед изменением файла всегда читай его текущее содержимое

2\. При расширении функционала — сохраняй существующие функции

3\. Формат коммитов: "v1.X: краткое описание"

4\. Не трогай: Dockerfile, cloudbuild.yaml, GCS schema

5\. Зависимости меняй только по запросу



\## Ссылки на документацию

\- docs/SignFinder\_Concept\_v1\_3.md — концепция архитектуры

\- docs/TZ\_SignFinder\_MVP\_v1\_7.md — текущее ТЗ

