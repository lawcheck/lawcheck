"""Напоминание о брошенной оплате: отбор заказов и сборка письма."""
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.models import Order, utcnow
from lawcheck.db.session import init_db, session_scope
from lawcheck.reporting import order_reminder


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "reminder.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "site_base_url", "https://lawchek.ru")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    init_db()
    yield
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _add_order(oid: str, email: str, *, status: str = "pending", days_ago: float = 2,
               reminded: bool = False, scan_id: str = "", amount: int = 990) -> None:
    with session_scope() as s:
        order = Order(id=oid, plan="pro", amount=amount, email=email, status=status,
                      scan_id=scan_id, payment_link="https://bank/old",
                      created_at=utcnow() - timedelta(days=days_ago))
        if reminded:
            order.reminded_at = utcnow()
        s.add(order)


# --- отбор orders_to_remind ---

def test_abandoned_order_selected():
    _add_order("o1", "a@x.ru")
    assert [o.id for o in repo.orders_to_remind()] == ["o1"]


def test_paid_order_skipped():
    _add_order("o1", "a@x.ru", status="paid")
    assert repo.orders_to_remind() == []


def test_already_reminded_skipped():
    _add_order("o1", "a@x.ru", reminded=True)
    assert repo.orders_to_remind() == []


def test_odin_email_odno_pismo():
    """Сломанная кнопка оплаты плодит по десятку заказов на адрес. Письмо
    должно уйти одно — про самый свежий заказ, у него живее ссылка."""
    _add_order("o1", "a@x.ru", days_ago=3)
    _add_order("o2", "a@x.ru", days_ago=2)
    _add_order("o3", "a@x.ru", days_ago=1)
    assert [o.id for o in repo.orders_to_remind()] == ["o3"]


def test_email_s_otpravlennym_napominaniem_bolshe_ne_beryotsya():
    """Иначе адрес получал бы по письму за каждый свой брошенный заказ —
    по одному на прогон, растянуто на дни."""
    _add_order("o1", "a@x.ru", reminded=True)
    _add_order("o2", "a@x.ru")
    assert repo.orders_to_remind() == []


def test_raznye_email_ne_meshayut_drug_drugu():
    _add_order("o1", "a@x.ru")
    _add_order("o2", "b@x.ru")
    assert {o.id for o in repo.orders_to_remind()} == {"o1", "o2"}


def test_too_recent_skipped():
    _add_order("o1", "a@x.ru", days_ago=0.1)  # моложе delay_hours=6
    assert repo.orders_to_remind() == []


def test_too_old_skipped():
    _add_order("o1", "a@x.ru", days_ago=30)  # старше max_age_days=14
    assert repo.orders_to_remind() == []


def test_order_without_email_skipped():
    _add_order("o1", "")
    assert repo.orders_to_remind() == []


def test_buyer_who_paid_another_order_skipped():
    """Человек бросил один заказ, но оплатил другой — напоминать нечего."""
    _add_order("o1", "a@x.ru")
    _add_order("o2", "a@x.ru", status="paid", days_ago=1)
    assert repo.orders_to_remind() == []


def test_limit_respected():
    for i in range(5):
        _add_order(f"o{i}", f"a{i}@x.ru")
    assert len(repo.orders_to_remind(limit=3)) == 3


# --- отметка об отправке ---

def test_mark_order_reminded_is_idempotent():
    _add_order("o1", "a@x.ru")
    repo.mark_order_reminded("o1")
    first = repo.get_order("o1").reminded_at
    repo.mark_order_reminded("o1")
    assert repo.get_order("o1").reminded_at == first
    assert repo.orders_to_remind() == []


# --- письмо ---

def test_letter_links_to_retry_not_stored_bank_link():
    """Ссылка ведёт на перевыпуск, а не на протухшую ссылку банка из заказа."""
    _add_order("o1", "a@x.ru")
    order = repo.orders_to_remind()[0]
    subject, html_body, text_body = order_reminder.render(
        order_reminder.build_context(order))
    assert "/pay/retry/o1" in html_body
    assert "bank/old" not in html_body
    assert "990" in subject or "990" in html_body
    assert "utm_campaign=order_reminder" in text_body


def test_letter_stays_transactional():
    """Сервисное письмо по ст. 18 ФЗ «О рекламе»: без апселла на другие тарифы."""
    _add_order("o1", "a@x.ru")
    order = repo.orders_to_remind()[0]
    _, html_body, text_body = order_reminder.render(
        order_reminder.build_context(order))
    for pitch in ("Персональный аудит", "35 000", "аудит"):
        assert pitch not in html_body
        assert pitch not in text_body


def test_report_link_only_when_scan_known():
    _add_order("o1", "a@x.ru", scan_id="s1")
    _add_order("o2", "b@x.ru")
    by_id = {o.id: o for o in repo.orders_to_remind()}
    with_scan = order_reminder.render(order_reminder.build_context(by_id["o1"]))[1]
    without = order_reminder.render(order_reminder.build_context(by_id["o2"]))[1]
    assert "/report/s1" in with_scan
    assert "/report/" not in without


# --- батч ---

def test_dry_run_sends_nothing_and_marks_nothing():
    _add_order("o1", "a@x.ru")
    summary = order_reminder.run(dry_run=True)
    assert summary == {"candidates": 1, "sent": 0, "skipped": 0, "dry_run": True}
    assert repo.get_order("o1").reminded_at is None
