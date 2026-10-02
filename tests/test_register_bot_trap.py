"""Защита /register от ботов: скрытое поле и метка времени выдачи формы."""
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.session import init_db
from lawcheck.notify import mailer
from lawcheck.web import auth

_REAL_TOKEN_OK = auth._form_token_ok
_DATA = {"pd_consent": "1", "email": "bot@x.ru", "password": "longenough1"}


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "trap.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret-please-ignore")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    monkeypatch.setattr(auth, "_form_token_ok", _REAL_TOKEN_OK)  # conftest её выключает
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


@pytest.fixture()
def sent(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(mailer, "send_email", lambda to, *a, **kw: out.append(to) or True)
    return out


def _token(client) -> str:
    html = client.get("/register").text
    return re.search(r'name="form_token" value="([^"]+)"', html).group(1)


def test_post_without_form_token_creates_nothing(client, sent):
    r = client.post("/register", data=_DATA)
    assert r.status_code == 422
    assert repo.get_user_by_email("bot@x.ru") is None
    assert sent == []


def test_instant_submit_is_rejected(client, sent):
    r = client.post("/register", data={**_DATA, "form_token": _token(client)})
    assert r.status_code == 422
    assert "Попробуйте ещё раз" in r.text
    assert repo.get_user_by_email("bot@x.ru") is None
    assert sent == []


def test_forged_token_is_rejected(client, sent):
    r = client.post("/register", data={**_DATA, "form_token": "r.AAAAAA.forged"})
    assert r.status_code == 422
    assert repo.get_user_by_email("bot@x.ru") is None


def test_human_pace_registers(client, sent, monkeypatch):
    token = _token(client)
    monkeypatch.setattr(auth, "_FORM_MIN_FILL_SEC", 0)
    r = client.post("/register", data={**_DATA, "form_token": token})
    assert r.status_code == 303
    assert repo.get_user_by_email("bot@x.ru") is not None
    assert sent == ["bot@x.ru"]


def test_stale_token_is_rejected(client, sent, monkeypatch):
    token = _token(client)
    monkeypatch.setattr(auth, "_FORM_MIN_FILL_SEC", 0)
    monkeypatch.setattr(auth, "_FORM_MAX_AGE_SEC", -1)
    r = client.post("/register", data={**_DATA, "form_token": token})
    assert r.status_code == 422
    assert repo.get_user_by_email("bot@x.ru") is None


def test_honeypot_silently_drops(client, sent, monkeypatch):
    token = _token(client)
    monkeypatch.setattr(auth, "_FORM_MIN_FILL_SEC", 0)
    r = client.post("/register", data={**_DATA, "form_token": token, "website": "http://spam"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "lc_session" not in r.cookies
    assert repo.get_user_by_email("bot@x.ru") is None
    assert sent == []


def test_error_page_carries_fresh_token(client):
    r = client.post("/register", data={**_DATA, "password": "short", "form_token": _token(client)})
    assert 'name="form_token" value="' in r.text
