"""Средний продукт лестницы «Документы под сайт» (развилка 2026-08-20).

Чек 990 ₽ не окупает платный трафик (CAC 11 607 ₽ при 1,9% конверсии), средним
продуктом стал разовый пакет за 8 000 ₽: проверка + PDF + готовые документы
с ручной проверкой юриста. Здесь проверяется, что покупка пакета проходит по
той же кассовой механике, что и Pro, и что разблокировка отчёта не смотрит план:
оплаченный заказ любого тарифа с этим scan_id открывает рецепты и драфты.
"""
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.models import Finding, Order, Scan
from lawcheck.db.session import init_db, session_scope
from lawcheck.payments.tochka import PaymentLink


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "docsplan.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()

    from lawcheck.api.main import create_app
    from lawcheck.web import payments

    def fake_create(*, amount_rub: int, purpose: str, order_id: str, email: str) -> PaymentLink:
        return PaymentLink(operation_id=f"op-{order_id[:8]}",
                           url=f"https://bank/pay/{order_id}")

    monkeypatch.setattr(payments.tochka, "is_configured", lambda: True)
    monkeypatch.setattr(payments.tochka, "create_payment", fake_create)

    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _scan_with_problems(scan_id: str):
    with session_scope() as s:
        s.add(Scan(id=scan_id, url="https://mysite.ru", status="done", pages_crawled=3))
        for i in range(4):
            s.add(Finding(scan_id=scan_id, check_id=f"A{i}", severity="critical",
                          title=f"Проблема {i}", evidence="x",
                          location="https://mysite.ru/p", law_reference="ст.1",
                          recommendation=f"Исправление {i}"))


def _locks(html: str) -> int:
    return html.count("🔒 Как исправить")


def test_pricing_pokazyvaet_paket_i_knopku_docs(client):
    r = client.get("/pricing")
    assert r.status_code == 200
    assert "Документы под сайт" in r.text
    assert "/buy/docs" in r.text
    assert "8 000 ₽" in r.text


def test_buy_docs_zavodit_zakaz_na_vosem_tysyach(client):
    r = client.post("/buy/docs", data={"email": "buyer@example.com",
                                       "pd_consent": "on"})
    assert r.status_code == 303
    with session_scope() as s:
        [order] = s.query(Order).all()
        assert order.plan == "docs"
        assert order.amount == 8000
        assert r.headers["location"] == order.payment_link


def test_oplachennyy_docs_otkryvaet_otchyot_kak_pro(client):
    """Разблокировка отчёта не смотрит план заказа — только факт оплаты и scan_id."""
    sid = "sdocsplan00000000000000000docs01"
    _scan_with_problems(sid)
    oid = uuid.uuid4().hex
    repo.create_order(oid, "docs", 8000, "buyer@x.ru", sid)
    repo.mark_order_paid(oid)

    client.get(f"/report/{sid}?order={oid}")  # запоминает заказ в сессии
    html = client.get(f"/report/{sid}").text
    assert _locks(html) == 0
    # драфты документов тоже открыты оплаченному пакету
    assert client.get(f"/report/{sid}/documents").status_code == 200
    assert client.get(f"/report/{sid}/rkn-notification").status_code == 200


def test_neoplachennyy_docs_nichego_ne_otkryvaet(client):
    sid = "sdocsplan00000000000000000docs02"
    _scan_with_problems(sid)
    oid = uuid.uuid4().hex
    repo.create_order(oid, "docs", 8000, "buyer@x.ru", sid)

    client.get(f"/report/{sid}?order={oid}")
    assert _locks(client.get(f"/report/{sid}").text) >= 1
    r = client.get(f"/report/{sid}/documents")
    assert r.status_code == 303 and "/pricing" in r.headers["location"]
