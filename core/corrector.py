"""Применение корректировок из corrections.md.

На текущем этапе MVP — пустой проход. Корректировки COR-002/003/004
будут реализованы по мере появления реальных кейсов в логах работы.
LLM-валидация (validator.py) сейчас выполняет основную работу
по фильтрации ложных срабатываний.
"""
from core.finder import SignMatch


def apply_corrections(matches: list[SignMatch]) -> list[SignMatch]:
    """Заглушка. Возвращает matches без изменений."""
    return matches
