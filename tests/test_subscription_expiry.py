"""Срок действия Pro: подписка живёт период тарифа, а не вечно.

До появления `Order.paid_until` доступ определялся одним `status == "paid"`,
поэтому разовая оплата 990 ₽ открывала Pro навсегда.
"""
import tempfile
import uuid
from datetime import timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.models import Order, utcnow
from lawcheck.db.session import init_db, session_scope


@pytest.fixture()
def db(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "expiry.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    init_db()
    yield
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _order(paid_until, status="paid", user_id=None) -> str:
    oid = uuid.uuid4().hex
    with session_scope() as sess:
        sess.add(Order(id=oid, plan="pro", amount=990, status=status,
                       paid_at=utcnow(), paid_until=paid_until, user_id=user_id))
    return oid


def test_oplata_otkryvaet_podpisku_na_period_tarifa(db):
    oid = uuid.uuid4().hex
    repo.create_order(oid, "pro", 990)
    assert repo.mark_order_paid(oid) is True

    order = repo.get_order(oid)
    assert order.paid_until is not None
    assert repo.subscription_active(order) is True

    # Срок — ровно период тарифа от момента оплаты (допуск на время теста).
    delta = order.paid_until - order.paid_at
    assert abs(delta - timedelta(days=repo.PRO_PERIOD_DAYS)) < timedelta(minutes=1)


def test_istekshaya_podpiska_ne_aktivna(db):
    oid = _order(utcnow() - timedelta(days=1))
    assert repo.subscription_active(repo.get_order(oid)) is False


def test_zakaz_bez_sroka_ne_aktiven(db):
    """Оплачен, но paid_until не проставлен — доступа нет.

    Это защита от возврата старого поведения: раньше такой заказ давал Pro вечно.
    """
    oid = _order(None)
    assert repo.subscription_active(repo.get_order(oid)) is False


def test_neoplachennyy_zakaz_so_srokom_ne_aktiven(db):
    """paid_until в будущем сам по себе доступа не даёт — статус тоже проверяется."""
    oid = _order(utcnow() + timedelta(days=30), status="pending")
    assert repo.subscription_active(repo.get_order(oid)) is False


def test_pro_podpiska_polzovatelya_istekaet(db):
    _order(utcnow() - timedelta(days=1), user_id=42)
    assert repo.user_has_paid_order(42) is False
    assert repo.latest_paid_order_id_for_user(42) is None

    active_id = _order(utcnow() + timedelta(days=5), user_id=42)
    assert repo.user_has_paid_order(42) is True
    assert repo.latest_paid_order_id_for_user(42) == active_id


def test_monitoring_ne_beret_istekshie_zakazy(db):
    with session_scope() as sess:
        sess.add(Order(id=uuid.uuid4().hex, plan="pro", amount=990, status="paid",
                       paid_at=utcnow(), paid_until=utcnow() - timedelta(days=1),
                       monitored_url="https://example.ru", verified_at=utcnow()))
        live = uuid.uuid4().hex
        sess.add(Order(id=live, plan="pro", amount=990, status="paid",
                       paid_at=utcnow(), paid_until=utcnow() + timedelta(days=10),
                       monitored_url="https://active.ru", verified_at=utcnow()))
    orders = repo.list_monitored_orders()
    assert [o.monitored_url for o in orders] == ["https://active.ru"]


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    from fastapi.testclient import TestClient

    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def test_kabinet_pokazyvaet_srok_i_predlagaet_prodlit(client):
    """Кабинет рендерится во всех трёх состояниях и не молчит об истечении."""
    active_id = _order(utcnow() + timedelta(days=12))
    expired_id = _order(utcnow() - timedelta(days=3))
    unpaid_id = _order(None, status="pending")

    body = client.get(f"/account/{active_id}").text
    assert "активен до" in body
    assert "Еженедельный мониторинг" in body

    body = client.get(f"/account/{expired_id}").text
    assert "Доступ по этому заказу закончился" in body
    assert "Продлить Pro" in body
    # Платные функции скрыты, но страница жива.
    assert "Еженедельный мониторинг" not in body

    body = client.get(f"/account/{unpaid_id}").text
    assert "Заказ не оплачен" in body


def test_istekshiy_zakaz_ne_puskaet_k_platnym_funkciyam(client):
    expired_id = _order(utcnow() - timedelta(days=3))

    assert client.post(f"/account/{expired_id}/monitor",
                       data={"url": "https://example.ru"}).status_code == 403
    assert client.post(f"/account/{expired_id}/verify").status_code == 403
    # Шаблоны — редирект обратно в кабинет, а не 403.
    r = client.get(f"/account/{expired_id}/templates")
    assert r.status_code == 303
    assert r.headers["location"] == f"/account/{expired_id}"


def test_migraciya_daet_mesyac_s_daty_vykata(db):
    """Бэкфилл: заказ, оплаченный до появления срока, получает месяц с момента
    миграции, а не с даты оплаты — иначе доступ пропал бы в секунду выката."""
    oid = uuid.uuid4().hex
    davno = utcnow() - timedelta(days=200)
    with session_scope() as sess:
        sess.add(Order(id=oid, plan="pro", amount=990, status="paid",
                       paid_at=davno, paid_until=None))
    # Эмулируем состояние старой БД: колонка есть, но значение не заполнено.
    with session.get_engine().begin() as conn:
        conn.execute(text("UPDATE orders SET paid_until = NULL"))
        conn.execute(text("ALTER TABLE orders RENAME COLUMN paid_until TO paid_until_tmp"))

    session._migrate_orders_paid_until()  # колонки paid_until нет → должна досоздать

    order = repo.get_order(oid)
    assert order.paid_until is not None
    assert repo.subscription_active(order) is True
    # Срок считается от миграции, а не от давней оплаты.
    # sqlite отдаёт naive datetime — нормализуем, как это делает subscription_active.
    until = order.paid_until.replace(tzinfo=timezone.utc)
    assert until > utcnow() + timedelta(days=repo.PRO_PERIOD_DAYS - 1)
