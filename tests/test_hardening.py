"""Харденинг августа-2026 (хвост прошлого ревью).

Пять защит: подметальщик зависших сканов, запрет затирать операцию
оплаченного заказа, стриминговый лимит PDF, алерт при падении SMTP,
потолок на размер страницы из браузера.
"""
import tempfile
from pathlib import Path

import pytest

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.models import Order, Scan
from lawcheck.db.session import init_db, session_scope


@pytest.fixture()
def db(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "hardening.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    init_db()
    yield
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


# === подметальщик зависших сканов ===

def _scan(scan_id: str, status: str, hours_ago: float) -> None:
    from datetime import timedelta

    from lawcheck.db.models import utcnow
    with session_scope() as s:
        s.add(Scan(id=scan_id, url="https://example.com/", status=status,
                   created_at=utcnow() - timedelta(hours=hours_ago)))


def test_reap_zakryvaet_staryy_running(db):
    _scan("old1", "running", hours_ago=2)
    stale = repo.reap_stale_scans()
    assert [s.id for s in stale] == ["old1"]
    with session_scope() as s:
        scan = s.get(Scan, "old1")
        assert scan.status == "error"
        assert scan.finished_at is not None


def test_reap_ne_trogaet_svezhiy_i_prochie_statusy(db):
    _scan("fresh", "running", hours_ago=0.1)
    _scan("done1", "done", hours_ago=5)
    _scan("err1", "error", hours_ago=5)
    assert repo.reap_stale_scans() == []
    with session_scope() as s:
        assert s.get(Scan, "fresh").status == "running"
        assert s.get(Scan, "done1").status == "done"
        assert s.get(Scan, "err1").status == "error"


# === операция оплаты не затирает оплаченный заказ ===

def _order(order_id: str, status: str = "pending",
           operation_id: str = "op-old") -> None:
    with session_scope() as s:
        s.add(Order(id=order_id, plan="pro", amount=990, email="b@x.ru",
                    status=status, operation_id=operation_id,
                    payment_link=f"https://bank/pay/{operation_id}"))


def test_set_order_payment_menyaet_pending(db):
    _order("o1")
    assert repo.set_order_payment("o1", "op-new", "https://bank/new") is True
    with session_scope() as s:
        order = s.get(Order, "o1")
        assert order.operation_id == "op-new"
        assert order.payment_link == "https://bank/new"


def test_set_order_payment_boitsya_oplachennogo(db):
    """Гонка /pay/retry и вебхука: оплата пришла между проверкой статуса и
    выпиской новой ссылки. Затирать operation_id оплаченного заказа нельзя —
    reconcile спрашивал бы банк про новую пустую операцию, а деньги по старой
    становились невидимы."""
    _order("o2", status="paid")
    assert repo.set_order_payment("o2", "op-race", "https://bank/race") is False
    with session_scope() as s:
        order = s.get(Order, "o2")
        assert order.operation_id == "op-old"
        assert order.status == "paid"


def test_pay_retry_u_oplachennogo_vedet_v_kabinet(db, client_factory=None):
    from fastapi.testclient import TestClient

    from lawcheck.api.main import create_app

    _order("o3", status="paid")
    with TestClient(create_app(), follow_redirects=False) as c:
        r = c.get("/pay/retry/o3")
    assert r.status_code == 303
    assert r.headers["location"].endswith("/account/o3")
    with session_scope() as s:
        assert s.get(Order, "o3").operation_id == "op-old"


# === потолок размера страницы ===

def test_truncate_rezhet_monstra():
    from lawcheck.crawler.browser import _truncate

    assert _truncate(None) == ""
    assert _truncate("abc") == "abc"
    big = "x" * (5 * 1024 * 1024 + 10)
    assert len(_truncate(big)) == 5 * 1024 * 1024


# === алерт при падении SMTP ===

def _smtp_setup(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_user", "")
    monkeypatch.setattr(settings, "smtp_password", "")
    monkeypatch.setattr(settings, "smtp_from", "LawCheck <noreply@lawchek.ru>")
    monkeypatch.setattr(settings, "telegram_bot_token", "tok")
    monkeypatch.setattr(settings, "telegram_owner_chat_id", "42")


def test_smtp_alert_posle_pyati_padniy(monkeypatch):
    from unittest import mock

    from lawcheck.notify import mailer

    _smtp_setup(monkeypatch)
    alerts = []
    monkeypatch.setattr(mailer, "_smtp_failures", 0)
    with mock.patch("smtplib.SMTP", side_effect=ConnectionRefusedError("down")), \
         mock.patch("lawcheck.notify.telegram.notify_owner",
                    side_effect=lambda t: alerts.append(t)):
        for i in range(5):
            assert mailer.send_email("u@x.ru", "t", "<p>x</p>") is False
        # Пятая падение — ровно один алерт, шестое и дальше молчат.
        assert mailer.send_email("u@x.ru", "t", "<p>x</p>") is False
    assert len(alerts) == 1
    assert "SMTP" in alerts[0]
    # Успех сбрасывает счётчик — новая серия снова начнёт копиться с нуля.
    with mock.patch("smtplib.SMTP"):
        assert mailer.send_email("u@x.ru", "t", "<p>x</p>") is True
    assert mailer._smtp_failures == 0
