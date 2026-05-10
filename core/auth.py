"""Аутентификация по промокоду из env var."""
import os


def check_access_code(input_code: str) -> bool:
    """Сравнить введённый код с ACCESS_CODE из env."""
    expected = os.environ.get("ACCESS_CODE", "")
    if not expected:
        return False
    return input_code == expected
