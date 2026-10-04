"""Общие фикстуры тестов."""
import pytest

from lawcheck.web import auth, ratelimit


@pytest.fixture(autouse=True)
def _reset_ratelimit():
    """Счётчик лимитов живёт в памяти процесса и общий для всех тестов.

    Без сброса тесты начинают влиять друг на друга: десяток регистраций подряд
    в разных файлах упирается в лимит «5 в час с адреса», и падает не тот тест,
    который что-то сломал, а тот, которому не повезло идти последним.
    """
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture(autouse=True)
def _skip_register_form_token(monkeypatch):
    """Десятки тестов заводят пользователя прямым POST /register, не открывая
    форму: метки времени у них нет, и ждать три секунды им незачем. Сама метка
    проверяется в test_register_bot_trap.py — там она включена обратно."""
    monkeypatch.setattr(auth, "_form_token_ok", lambda token: True)
