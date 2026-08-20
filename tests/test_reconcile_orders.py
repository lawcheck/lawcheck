"""Сверка заказов с банком: находит оплату, которую мы проспали.

Вебхук может потеряться, а покупатель — не вернуться на /pay/success (СБП:
платит в приложении банка и вкладку не открывает). Тогда деньги у нас, а заказ
висит `pending`.
"""
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.models import Order, utcnow
from lawcheck.db.session import init_db, session_scope
from lawcheck.tools import reconcile_orders


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "reconcile.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    init_db()
    yield
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


@pytest.fixture(autouse=True)
def no_telegram(monkeypatch):
    sent = []
    monkeypatch.setattr(reconcile_orders.telegram, "notify_owner", sent.append)
    return sent


def _add(oid: str, *, status: str = "pending", operation_id: str = "op-1",
         days_ago: float = 1, email: str = "buyer@example.com") -> None:
    with session_scope() as s:
        s.add(Order(id=oid, plan="pro", amount=990, email=email, status=status,
                    operation_id=operation_id, payment_link="https://bank/link",
                    created_at=utcnow() - timedelta(days=days_ago)))


def _bank_says(monkeypatch, state: str) -> None:
    monkeypatch.setattr(reconcile_orders.tochka, "payment_state", lambda _op: state)


def test_oplachennyj_v_banke_zakaz_ischpravlyaetsya(monkeypatch, no_telegram):
    _add("a" * 32)
    _bank_says(monkeypatch, "paid")

    summary = reconcile_orders.run()

    assert summary["checked"] == 1 and summary["found"] == 1
    assert repo.get_order("a" * 32).status == "paid"
    assert len(no_telegram) == 1 and "Оплачен заказ" in no_telegram[0]


def test_dry_run_nichego_ne_menyaet(monkeypatch, no_telegram):
    _add("b" * 32)
    _bank_says(monkeypatch, "paid")

    summary = reconcile_orders.run(dry_run=True)

    assert summary["found"] == 1
    assert repo.get_order("b" * 32).status == "pending"
    assert no_telegram == []


@pytest.mark.parametrize("state", ["pending", "unknown"])
def test_neoplachennyj_ostayotsya_kak_byl(monkeypatch, no_telegram, state):
    """Банк не подтвердил — не трогаем. `unknown` это сбой связи, не отказ."""
    _add("c" * 32)
    _bank_says(monkeypatch, state)

    assert reconcile_orders.run()["found"] == 0
    assert repo.get_order("c" * 32).status == "pending"
    assert no_telegram == []


def test_zakaz_bez_operacii_ne_beryotsya(monkeypatch, no_telegram):
    """Без operation_id спрашивать банк не о чем — такой заказ вне отбора."""
    _add("d" * 32, operation_id="")
    _bank_says(monkeypatch, "paid")

    assert reconcile_orders.run()["checked"] == 0
    assert repo.get_order("d" * 32).status == "pending"


def test_uzhe_oplachennyj_ne_pereproveryaetsya(monkeypatch, no_telegram):
    """Иначе владелец получал бы алерт об одной оплате на каждом прогоне."""
    _add("e" * 32, status="paid")
    _bank_says(monkeypatch, "paid")

    assert reconcile_orders.run()["checked"] == 0
    assert no_telegram == []


def test_staryj_zakaz_za_oknom(monkeypatch, no_telegram):
    _add("f" * 32, days_ago=90)
    _bank_says(monkeypatch, "paid")

    assert reconcile_orders.run(max_age_days=60)["checked"] == 0
