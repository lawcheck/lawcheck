"""Секреты вебхука Telegram и внутренних cron-ручек.

Раньше пустой `telegram_webhook_secret` выключал проверку целиком: эндпойнт
оставался открыт всему интернету, а `chat_id` берётся из присланного тела —
то есть посторонний рассылал сообщения нашим ботом кому угодно.
"""
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.session import init_db


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "hooks.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


_UPDATE = {"message": {"chat": {"id": "999"}, "text": "/start"}}


def test_pustoy_sekret_zakryvaet_vebhuk(client, monkeypatch):
    """Незаданный секрет закрывает ручку, а не открывает."""
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")
    with mock.patch("lawcheck.notify.telegram.send_message") as send:
        r = client.post("/webhooks/telegram", json=_UPDATE)
    assert r.status_code == 403
    send.assert_not_called()


def test_bez_zagolovka_ne_puskaet(client, monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")
    with mock.patch("lawcheck.notify.telegram.send_message") as send:
        r = client.post("/webhooks/telegram", json=_UPDATE)
    assert r.status_code == 403
    send.assert_not_called()


def test_nevernyy_sekret_ne_puskaet(client, monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")
    r = client.post("/webhooks/telegram", json=_UPDATE,
                    headers={"X-Telegram-Bot-Api-Secret-Token": "s3cre"})
    assert r.status_code == 403


def test_vernyy_sekret_puskaet(client, monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")
    with mock.patch("lawcheck.notify.telegram.send_message", return_value=True) as send:
        r = client.post("/webhooks/telegram", json=_UPDATE,
                        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
    assert r.status_code == 200
    send.assert_called_once()


def test_ne_ascii_sekret_daet_403_a_ne_500(client, monkeypatch):
    """compare_digest на строках с символом >127 бросает TypeError.
    Заголовки Starlette декодирует как latin-1, так что прислать такой можно."""
    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")
    r = client.post("/webhooks/telegram", json=_UPDATE,
                    headers={"X-Telegram-Bot-Api-Secret-Token": "пароль".encode()})
    assert r.status_code == 403


@pytest.mark.parametrize("path", ["/internal/monitoring/run", "/internal/followups/run"])
def test_vnutrennie_ruchki_ne_padayut_na_ne_ascii(client, monkeypatch, path):
    monkeypatch.setattr(settings, "internal_key", "s3cret")
    r = client.post(path, headers={"X-Internal-Key": "ключ".encode()})
    assert r.status_code == 403
