"""Аккаунты: регистрация, вход, выход, навигация (web/auth.py + deps + main)."""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.session import init_db


@pytest.fixture()
def client(monkeypatch):
    """Свежая файловая sqlite (шарится между потоками to_thread) + сессии вкл."""
    tmp = Path(tempfile.mkdtemp()) / "auth.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret-please-ignore")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")  # https_only=False
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def test_register_creates_user_and_session(client):
    r = client.post("/register", data={"email": "New@X.ru", "password": "longenough1"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert "lc_session" in r.cookies  # сессия выставлена
    user = repo.get_user_by_email("new@x.ru")  # email нормализован в нижний регистр
    assert user is not None and user.password_hash.startswith("$argon2")


def test_register_duplicate_email(client):
    client.post("/register", data={"email": "dup@x.ru", "password": "longenough1"})
    r = client.post("/register", data={"email": "dup@x.ru", "password": "otherpass1"})
    assert r.status_code == 409
    assert "уже есть аккаунт" in r.text


def test_register_short_password(client):
    r = client.post("/register", data={"email": "a@x.ru", "password": "short"})
    assert r.status_code == 422
    assert repo.get_user_by_email("a@x.ru") is None


def test_login_success_and_wrong_password(client):
    client.post("/register", data={"email": "log@x.ru", "password": "correcthorse1"})
    client.cookies.clear()  # выходим, проверяем чистый вход
    bad = client.post("/login", data={"email": "log@x.ru", "password": "nope"})
    assert bad.status_code == 401
    ok = client.post("/login", data={"email": "log@x.ru", "password": "correcthorse1"})
    assert ok.status_code == 303 and "lc_session" in ok.cookies


def test_nav_shows_login_then_email(client):
    anon = client.get("/")
    assert "Войти" in anon.text and "Выйти" not in anon.text
    client.post("/register", data={"email": "nav@x.ru", "password": "longenough1"})
    home = client.get("/")
    assert "nav@x.ru" in home.text and "Выйти" in home.text


def test_logout_clears_session(client):
    client.post("/register", data={"email": "out@x.ru", "password": "longenough1"})
    client.post("/logout")
    home = client.get("/")
    assert "Войти" in home.text and "out@x.ru" not in home.text
