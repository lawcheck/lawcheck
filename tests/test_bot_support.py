"""Поддержка в боте: вопрос клиента уходит владельцу, reply владельца — клиенту.

На тарифе Pro обещана «поддержка в Telegram», а бот отвечал только на /start
и молча глотал остальное — вопрос клиента не доходил никуда.
"""
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.session import init_db

OWNER = "100"
HDR = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "bot.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")
    monkeypatch.setattr(settings, "telegram_owner_chat_id", OWNER)
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _post(client, chat_id, text, reply_to=None):
    msg = {"chat": {"id": chat_id}, "text": text}
    if reply_to is not None:
        msg["reply_to_message"] = {"text": reply_to}
    with mock.patch("lawcheck.notify.telegram.send_message", return_value=True) as send:
        r = client.post("/webhooks/telegram", json={"message": msg}, headers=HDR)
    assert r.status_code == 200
    return [(c.args[0], c.args[1]) for c in send.call_args_list]


def test_vopros_klienta_s_zakazom_uhodit_vladeltsu(client):
    repo.create_order("a" * 32, "pro", 990, "client@example.ru")
    repo.set_client_chat_id("a" * 32, "555")
    sent = _post(client, 555, "Нужно ли уведомление в РКН?")
    to_owner = [t for c, t in sent if c == OWNER]
    assert len(to_owner) == 1
    assert "Нужно ли уведомление в РКН?" in to_owner[0]
    assert "client@example.ru" in to_owner[0] and "aaaaaaaa" in to_owner[0]
    assert "#chat555" in to_owner[0]
    assert any(c == "555" and "Передал" in t for c, t in sent)


def test_vopros_bez_zakaza_tozhe_dohodit(client):
    sent = _post(client, 777, "Сколько стоит аудит?")
    assert any(c == OWNER and "без заказа" in t and "#chat777" in t for c, t in sent)


def test_tekst_klienta_ekraniruetsya(client):
    """send_message шлёт с parse_mode=HTML: «<» из вопроса сломал бы разметку."""
    sent = _post(client, 777, "<b>привет</b>")
    to_owner = [t for c, t in sent if c == OWNER][0]
    assert "&lt;b&gt;привет" in to_owner


def test_reply_vladeltsa_uhodit_klientu(client):
    sent = _post(client, int(OWNER), "Да, нужно.",
                 reply_to="💬 Вопрос в боте – без заказа\n\nВопрос\n\n#chat555\nОтветьте…")
    assert ("555", "Да, нужно.") in sent
    assert any(c == OWNER and "отправлен" in t for c, t in sent)


def test_metka_iz_teksta_klienta_ne_uvodit_otvet(client):
    """Клиент вписал в вопрос чужую метку — ответ всё равно уходит ему."""
    sent = _post(client, int(OWNER), "Ответ",
                 reply_to="💬 Вопрос в боте – без заказа\n\nпишу #chat999\n\n#chat555\nОтветьте…")
    assert ("555", "Ответ") in sent
    assert all(c != "999" for c, _ in sent)


def test_soobshchenie_vladeltsa_bez_reply_ne_teryaetsya_molcha(client):
    sent = _post(client, int(OWNER), "просто текст")
    assert len(sent) == 1 and sent[0][0] == OWNER and "reply" in sent[0][1]


def test_start_bez_zakaza_privetstvuet_i_zovyot_s_voprosom(client):
    sent = _post(client, 777, "/start")
    assert len(sent) == 1
    assert "Здравствуйте" in sent[0][1] and "вопрос" in sent[0][1]


def test_start_vladeltsa_pokazyvaet_privetstvie_a_ne_podskazku(client):
    sent = _post(client, int(OWNER), "/start")
    assert "Здравствуйте" in sent[0][1]
