"""CSRF-защита проверкой Origin и содержимое cookie-сессии.

Cookie подписана, но не зашифрована: всё, что в неё положено, читается base64
в браузере и в любом логе, куда cookie попадает. Поэтому в ней только id
пользователя и версия сессий, а email берётся из БД.
"""
import base64
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.session import init_db


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "csrf.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _session_payload(client) -> dict:
    raw = client.cookies["lc_session"]
    body = raw.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_chuzhoy_origin_otklonen(client):
    r = client.post("/inquiry", data={"message": "привет", "contact": "a@x.ru",
                                      "pd_consent": "1"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_svoy_origin_prohodit(client):
    r = client.post("/inquiry", data={"message": "привет", "contact": "a@x.ru",
                                      "pd_consent": "1"},
                    headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_bez_origin_prohodit(client):
    """Вебхук банка, cron и curl браузерного Origin не шлют."""
    r = client.post("/inquiry", data={"message": "привет", "contact": "a@x.ru",
                                      "pd_consent": "1"})
    assert r.status_code == 200


def test_get_ne_proveryaetsya(client):
    r = client.get("/", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200


def test_v_cookie_net_email(client):
    r = client.post("/register", data={"email": "klient@x.ru", "password": "parol123"},
                    headers={"Origin": "http://testserver"})
    assert r.status_code == 303
    payload = _session_payload(client)
    assert payload.get("uid")
    assert "klient@x.ru" not in json.dumps(payload, ensure_ascii=False)


def test_navigaciya_vsyo_ravno_znaet_email(client):
    client.post("/register", data={"email": "klient@x.ru", "password": "parol123"},
                headers={"Origin": "http://testserver"})
    # Email в cookie нет, но шапка его показывает — значит, читается из БД.
    assert "klient@x.ru" in client.get("/").text
