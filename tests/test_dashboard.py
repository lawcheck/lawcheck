"""Фаза 4: дашборд «Мои отчёты», привязка сканов, claiming по verified email."""
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
    tmp = Path(tempfile.mkdtemp()) / "dash.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()
    from lawcheck.api.main import create_app
    sent: list[dict] = []
    monkeypatch.setattr("lawcheck.web.auth.mailer.send_email",
                        lambda to, subject, html_body, text_body=None: sent.append(
                            {"subject": subject, "html": html_body}) or True)
    # не запускать реальный краул при POST /scan
    monkeypatch.setattr("lawcheck.web.routes._run_scan", lambda *a, **k: None)
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c, sent
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _verify_token(sent):
    html = next(s["html"] for s in sent if "Подтверждение email" in s["subject"])
    return re.search(r"token=([\w\-]+)", html).group(1)


def test_dashboard_requires_login(env):
    client, _ = env
    r = client.get("/dashboard")
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_new_scan_tied_to_logged_in_user(env):
    client, _ = env
    client.post("/register", data={"email": "runner@x.ru", "password": "longenough1"})
    r = client.post("/scan", data={"url": "example.com", "max_pages": "5"})
    assert r.status_code == 303  # редирект на /report/{id}
    dash = client.get("/dashboard")
    assert "example.com" in dash.text  # скан попал в «Мои отчёты»


def test_claiming_gated_on_verification(env):
    client, sent = env
    # прошлые данные с этим email: оплаченный заказ + скан + лид на другой скан
    repo.create_scan("scanpaid", "https://paid-site.ru", 10)
    repo.create_order("ordX", "pro", 990, "claim@x.ru", "scanpaid")
    repo.mark_order_paid("ordX")
    repo.create_scan("scanlead", "https://lead-site.ru", 10)
    repo.create_lead("scanlead", "https://lead-site.ru", "claim@x.ru")

    client.post("/register", data={"email": "claim@x.ru", "password": "longenough1"})
    # до подтверждения — ничего чужого не подцеплено
    dash = client.get("/dashboard").text
    assert "paid-site.ru" not in dash and "lead-site.ru" not in dash
    assert "ordX"[:8] not in dash

    token = _verify_token(sent)
    client.get(f"/verify-email?token={token}")
    dash = client.get("/dashboard").text
    # после подтверждения — заказ и оба скана в кабинете
    assert "paid-site.ru" in dash and "lead-site.ru" in dash
    assert "оплачен" in dash


def test_claim_on_login_for_later_order(env):
    client, sent = env
    client.post("/register", data={"email": "late@x.ru", "password": "longenough1"})
    client.get(f"/verify-email?token={_verify_token(sent)}")
    # заказ появляется ПОЗЖЕ (например, оплата с того же email)
    repo.create_scan("scanlate", "https://late-site.ru", 10)
    repo.create_order("ordLate", "pro", 990, "late@x.ru", "scanlate")
    repo.mark_order_paid("ordLate")
    assert "late-site.ru" not in client.get("/dashboard").text
    # повторный вход подцепляет новый заказ (email уже подтверждён)
    client.cookies.clear()
    client.post("/login", data={"email": "late@x.ru", "password": "longenough1"})
    assert "late-site.ru" in client.get("/dashboard").text
