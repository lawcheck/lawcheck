"""Цель Метрики «Оплата прошла» на странице успешной оплаты.

До 2026-08 факт оплаты нигде не событился: приоритетной целью кампании был
запуск бесплатной проверки, поэтому Директ оптимизировался не под продажу.
Событие lcGoal("pay_success") допустимо только в состоянии ok — оно
рендерится лишь после подтверждения статуса запросом к API банка
(payments.pay_success), а не по возвращению покупателя из кассы.
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
    tmp = Path(tempfile.mkdtemp()) / "paygoal.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()

    from lawcheck.api.main import create_app
    from lawcheck.web import payments

    def fake_create(*, amount_rub: int, purpose: str, order_id: str, email: str) -> PaymentLink:
        return PaymentLink(operation_id="op-1", url="https://bank/pay")

    monkeypatch.setattr(payments.tochka, "is_configured", lambda: True)
    monkeypatch.setattr(payments.tochka, "create_payment", fake_create)

    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _buy(client) -> Order:
    r = client.post("/buy/pro", data={"email": "buyer@example.com", "pd_consent": "on"})
    assert r.status_code == 303, r.text
    with session_scope() as s:
        order = s.query(Order).one()
        s.expunge(order)
    return order


def test_oplachennyi_zakaz_shlet_celu_pay_success(client, monkeypatch):
    from lawcheck.web import payments

    order = _buy(client)
    monkeypatch.setattr(payments.tochka, "payment_state", lambda op_id: "paid")
    r = client.get(f"/pay/success?order={order.id}")
    assert r.status_code == 200
    assert 'lcGoal("pay_success")' in r.text


def test_neoplahachennyi_i_panding_bez_tseli(client, monkeypatch):
    from lawcheck.web import payments

    order = _buy(client)
    # unknown: банк не подтвердил оплату
    monkeypatch.setattr(payments.tochka, "payment_state", lambda op_id: "unknown")
    r = client.get(f"/pay/success?order={order.id}")
    assert r.status_code == 200
    assert 'lcGoal("pay_success")' not in r.text

    # pending: холд без списания — целью продажи быть не должно
    monkeypatch.setattr(payments.tochka, "payment_state", lambda op_id: "pending")
    r = client.get(f"/pay/success?order={order.id}")
    assert r.status_code == 200
    assert 'lcGoal("pay_success")' not in r.text
