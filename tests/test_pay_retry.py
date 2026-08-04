"""Перевыпуск платёжной ссылки по брошенному заказу — цель ссылки из письма
о незавершённой оплате (`reporting/order_reminder.py`)."""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.models import Order
from lawcheck.db.session import init_db, session_scope
from lawcheck.payments.tochka import PaymentLink


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "retry.db"
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


def _add_order(oid: str = "o1", *, status: str = "pending", amount: int = 990) -> None:
    with session_scope() as s:
        s.add(Order(id=oid, plan="pro", amount=amount, email="a@x.ru", status=status,
                    operation_id="op-old", payment_link="https://bank/old"))


def _stub_tochka(monkeypatch, seen: dict):
    from lawcheck.web import routes

    def fake_create(*, amount_rub: int, purpose: str, order_id: str) -> PaymentLink:
        seen.update(amount=amount_rub, purpose=purpose, order_id=order_id)
        return PaymentLink(operation_id="op-new", url="https://bank/new")

    monkeypatch.setattr(routes.tochka, "is_configured", lambda: True)
    monkeypatch.setattr(routes.tochka, "create_payment", fake_create)


def test_unknown_order_404(client):
    assert client.get("/pay/retry/nope").status_code == 404


def test_paid_order_goes_to_account(client):
    _add_order(status="paid")
    r = client.get("/pay/retry/o1")
    assert r.status_code == 303
    assert r.headers["location"] == "/account/o1"


def test_pending_order_gets_fresh_link(client, monkeypatch):
    _add_order()
    seen: dict = {}
    _stub_tochka(monkeypatch, seen)
    r = client.get("/pay/retry/o1")
    assert r.status_code == 303
    assert r.headers["location"] == "https://bank/new"
    # Заказ переиспользован, а не продублирован — иначе не отличить отработку
    # напоминания от свежего спроса.
    assert seen["order_id"] == "o1"
    order = repo.get_order("o1")
    assert (order.operation_id, order.payment_link) == ("op-new", "https://bank/new")


def test_price_taken_from_order_not_pricelist(client, monkeypatch):
    """Человек платит цену, которую видел при оформлении."""
    _add_order(amount=490)
    seen: dict = {}
    _stub_tochka(monkeypatch, seen)
    client.get("/pay/retry/o1")
    assert seen["amount"] == 490


def test_acquiring_down_shows_fallback(client, monkeypatch):
    _add_order()
    from lawcheck.web import routes
    monkeypatch.setattr(routes.tochka, "is_configured", lambda: False)
    r = client.get("/pay/retry/o1")
    assert r.status_code == 200
    assert "op-old" not in r.text


def test_bank_error_does_not_break_order(client, monkeypatch):
    _add_order()
    from lawcheck.web import routes

    def boom(**kwargs):
        raise RuntimeError("bank down")

    monkeypatch.setattr(routes.tochka, "is_configured", lambda: True)
    monkeypatch.setattr(routes.tochka, "create_payment", boom)
    r = client.get("/pay/retry/o1")
    assert r.status_code == 200
    order = repo.get_order("o1")
    assert (order.operation_id, order.status) == ("op-old", "pending")
