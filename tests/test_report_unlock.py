"""Разблокировка отчёта Pro-подпиской: владелец скана с оплаченным заказом
видит свои отчёты открытыми целиком; чужие — нет."""
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.models import Finding, Order, Scan
from lawcheck.db.session import init_db, session_scope


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "unlock.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    monkeypatch.setattr("lawcheck.web.auth.mailer.send_email",
                        lambda *a, **k: True)
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _scan_with_problems(scan_id: str, user_id=None):
    with session_scope() as s:
        s.add(Scan(id=scan_id, url="https://mysite.ru", status="done",
                   pages_crawled=3, user_id=user_id))
        for cid, t, rec in [
            ("B2", "Нет согласия", "Добавьте чекбокс"),
            ("A1", "Нет политики", "Разместите политику"),
            ("G1", "Реклама без пометки", "Добавьте пометку"),
            ("D1", "Cookie без баннера", "Добавьте баннер"),
        ]:
            s.add(Finding(scan_id=scan_id, check_id=cid, severity="critical",
                          title=t, evidence="x", location="https://mysite.ru/p",
                          law_reference="ст.1", recommendation=rec))


def _locks(html: str) -> int:
    return html.count("🔒 Как исправить")


def _register(client, email):
    client.post("/register", data={"email": email, "password": "longenough1"})
    return repo.get_user_by_email(email)


def _give_paid_order(user_id):
    oid = uuid.uuid4().hex
    repo.create_order(oid, "pro", 990, "")
    repo.mark_order_paid(oid)
    with session_scope() as s:
        s.get(Order, oid).user_id = user_id
    return oid


def test_pro_owner_sees_own_report_unlocked(client):
    user = _register(client, "owner@x.ru")     # logged in
    _give_paid_order(user.id)
    sid = "sownedbypro00000000000000000pro1"
    _scan_with_problems(sid, user_id=user.id)  # скан принадлежит владельцу
    html = client.get(f"/report/{sid}").text
    assert _locks(html) == 0
    assert "Доступ Pro активен" in html
    # связка «находка → готовый текст»: у A1/B2 есть ссылка на раздел шаблонов
    assert "Готовый текст" in html
    assert "/templates#tpl-" in html


def test_logged_in_without_paid_order_stays_locked(client):
    user = _register(client, "free@x.ru")
    sid = "sfreeuser000000000000000000free1"
    _scan_with_problems(sid, user_id=user.id)  # его скан, но заказа нет
    html = client.get(f"/report/{sid}").text
    assert _locks(html) >= 1
    assert "Доступ Pro активен" not in html


def test_pro_user_does_not_unlock_foreign_scan(client):
    pro = _register(client, "pro@x.ru")
    _give_paid_order(pro.id)
    sid = "sforeign0000000000000000000frgn1"
    _scan_with_problems(sid, user_id=None)     # ничей (чужой) скан
    html = client.get(f"/report/{sid}").text
    assert _locks(html) >= 1                    # Pro не открывает чужое


def test_documents_route_gated_by_payment(client):
    # оплаченный владелец → черновик отдаётся (200, содержит Политику)
    user = _register(client, "docs@x.ru")
    _give_paid_order(user.id)
    sid = "sdocsowner00000000000000000docs1"
    _scan_with_problems(sid, user_id=user.id)
    r = client.get(f"/report/{sid}/documents")
    assert r.status_code == 200
    assert "Политика обработки персональных данных" in r.text
    assert "☐ Я даю согласие" in r.text
    # аноним → редирект на оплату
    client.cookies.clear()
    r2 = client.get(f"/report/{sid}/documents")
    assert r2.status_code == 303 and "/pricing" in r2.headers["location"]


def test_per_scan_purchase_still_unlocks_for_anyone(client):
    sid = "sperscan00000000000000000000buy1"
    _scan_with_problems(sid, user_id=None)
    oid = uuid.uuid4().hex
    repo.create_order(oid, "pro", 990, "buyer@x.ru", sid)  # покупка С ЭТОГО скана
    repo.mark_order_paid(oid)
    html = client.get(f"/report/{sid}").text   # аноним, без входа
    assert _locks(html) == 0
