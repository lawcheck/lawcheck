"""Фаза 3: подтверждение email и сброс пароля (web/auth.py + токены repo)."""
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.session import init_db


@pytest.fixture()
def env(monkeypatch):
    """Файловая sqlite + сессии + перехват отправленных писем."""
    tmp = Path(tempfile.mkdtemp()) / "authmail.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()
    from lawcheck.api.main import create_app
    sent: list[dict] = []

    def _capture(to, subject, html_body, text_body=None):
        sent.append({"to": to, "subject": subject, "html": html_body})
        return True

    # mailer импортируется в auth как модуль — патчим там, где вызывается.
    monkeypatch.setattr("lawcheck.web.auth.mailer.send_email", _capture)
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c, sent
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _token_from(sent: list[dict], needle: str) -> str:
    html = next(s["html"] for s in sent if needle in s["subject"])
    return re.search(r"token=([\w\-]+)", html).group(1)


def test_verification_email_on_register_then_verify(env):
    client, sent = env
    client.post("/register", data={"email": "v@x.ru", "password": "longenough1"})
    assert any("Подтверждение email" in s["subject"] for s in sent)
    # до подтверждения — баннер виден
    assert "Подтвердите email" in client.get("/").text
    token = _token_from(sent, "Подтверждение email")
    r = client.get(f"/verify-email?token={token}")
    assert r.status_code == 200 and "подтверждён" in r.text.lower()
    user = repo.get_user_by_email("v@x.ru")
    assert user.email_verified_at is not None
    # баннер пропал
    assert "Подтвердите email" not in client.get("/").text


def test_verify_token_single_use_and_bad_token(env):
    client, sent = env
    client.post("/register", data={"email": "v2@x.ru", "password": "longenough1"})
    token = _token_from(sent, "Подтверждение email")
    assert client.get(f"/verify-email?token={token}").status_code == 200
    # повторное использование — отказ
    assert client.get(f"/verify-email?token={token}").status_code == 400
    assert client.get("/verify-email?token=garbage").status_code == 400


def test_forgot_password_no_enumeration(env):
    client, sent = env
    client.post("/register", data={"email": "real@x.ru", "password": "longenough1"})
    sent.clear()
    # несуществующий email — тот же ответ, письма нет
    r1 = client.post("/forgot-password", data={"email": "ghost@x.ru"})
    assert r1.status_code == 200 and "Если аккаунт" in r1.text
    assert sent == []
    # существующий — тот же ответ, но письмо ушло
    r2 = client.post("/forgot-password", data={"email": "real@x.ru"})
    assert r2.status_code == 200 and "Если аккаунт" in r2.text
    assert any("Сброс пароля" in s["subject"] for s in sent)


def test_reset_password_flow(env):
    client, sent = env
    client.post("/register", data={"email": "r@x.ru", "password": "oldpassword1"})
    client.cookies.clear()
    client.post("/forgot-password", data={"email": "r@x.ru"})
    token = _token_from(sent, "Сброс пароля")
    # слишком короткий — 422, токен не потрачен
    assert client.post("/reset-password",
                       data={"token": token, "password": "short"}).status_code == 422
    ok = client.post("/reset-password", data={"token": token, "password": "brandnewpass1"})
    assert ok.status_code == 200 and "обновлён" in ok.text.lower()
    # старый пароль больше не подходит, новый — да
    assert client.post("/login", data={"email": "r@x.ru", "password": "oldpassword1"}).status_code == 401
    assert client.post("/login", data={"email": "r@x.ru", "password": "brandnewpass1"}).status_code == 303
    # токен сброса одноразовый
    assert client.post("/reset-password",
                       data={"token": token, "password": "anotherpass1"}).status_code == 400
