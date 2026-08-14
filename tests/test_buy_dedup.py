"""Повторный клик «Оплатить» не должен плодить дубль заказа.

Авария 05.08.2026: зависший переход на кассу банка — человек кликнул
«Оплатить» 14 раз за 30 секунд, получил 14 заказов и 14 операций в Точке.
Фикс — repo.recent_pending_order в web/payments.py::buy.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.models import Order
from lawcheck.db.session import init_db, session_scope
from lawcheck.payments.tochka import PaymentLink


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "buydedup.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()

    from lawcheck.api.main import create_app
    from lawcheck.web import payments

    calls = {"n": 0}

    def fake_create(*, amount_rub: int, purpose: str, order_id: str, email: str) -> PaymentLink:
        calls["n"] += 1
        return PaymentLink(operation_id=f"op-{calls['n']}", url=f"https://bank/pay/{order_id}")

    monkeypatch.setattr(payments.tochka, "is_configured", lambda: True)
    monkeypatch.setattr(payments.tochka, "create_payment", fake_create)

    with TestClient(create_app(), follow_redirects=False) as c:
        c.tochka_calls = calls
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _buy(client, **extra):
    data = {"email": "buyer@example.com", "pd_consent": "on", **extra}
    return client.post("/buy/pro", data=data)


def test_povtornyy_klik_ne_plodit_dubl_zakaza(client):
    r1 = _buy(client)
    r2 = _buy(client)
    assert r1.status_code == r2.status_code == 303
    with session_scope() as s:
        orders = s.query(Order).all()
        assert len(orders) == 1
    assert r1.headers["location"] == r2.headers["location"]
    assert client.tochka_calls["n"] == 1


def test_povtornyy_klik_s_drugogo_skana_zavodit_svoy_zakaz(client):
    """Дедуп по (email, scan_id, plan) — разные сканы не должны схлопываться,
    иначе покупка второго отчёта потеряется."""
    _buy(client, scan_id="scan-a")
    _buy(client, scan_id="scan-b")
    with session_scope() as s:
        assert s.query(Order).count() == 2


def test_drugoy_email_zavodit_svoy_zakaz(client):
    _buy(client, email="one@example.com")
    _buy(client, email="two@example.com")
    with session_scope() as s:
        assert s.query(Order).count() == 2


def test_posle_oplaty_povtornyy_klik_zavodit_novyy_zakaz(client):
    """Дедуп смотрит только на status='pending' — оплаченный заказ не должен
    мешать оформить новую подписку (например, на новый месяц)."""
    r1 = _buy(client)
    with session_scope() as s:
        order = s.query(Order).one()
        order.status = "paid"

    r2 = _buy(client)
    assert r2.status_code == 303
    assert r1.headers["location"] != r2.headers["location"]
    with session_scope() as s:
        assert s.query(Order).count() == 2
    assert client.tochka_calls["n"] == 2
