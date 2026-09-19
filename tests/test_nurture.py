"""Nurture-цепочка: отписка, исключение оплативших, содержимое писем.

Ревью 19.09.2026 нашло: отписка по nurture-токену не работала (роут искал
токен только в leads/inquiries), `nurture_remove_paid` не вызывалась нигде,
письмо 8 продавало несуществующий тариф «1 ₽ вместо 2 990 ₽/мес», письмо 7 —
выдуманный кейс. Каждый фикс закреплён тестом.
"""
import tempfile
from pathlib import Path

import pytest

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.models import NurtureSubscriber, utcnow
from lawcheck.db.session import init_db, session_scope
from lawcheck.reporting import nurture


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "nurture.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "site_base_url", "https://lawchek.ru")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    init_db()
    yield
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


@pytest.fixture()
def client(isolated_db):
    from fastapi.testclient import TestClient
    from lawcheck.api.main import create_app
    app = create_app()
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _sub(email: str, *, step: int = 1, token: str = "", paid_order: bool = False,
         unsub: bool = False) -> int:
    repo.nurture_subscribe(email)
    if token:
        with session_scope() as s:
            from sqlalchemy import select
            row = s.execute(select(NurtureSubscriber).where(
                NurtureSubscriber.email == email)).scalars().first()
            row.unsub_token = token
            row.step = step
            if unsub:
                row.unsubscribed_at = utcnow()
    if paid_order:
        import uuid
        with session_scope() as s:
            from lawcheck.db.models import Order
            s.add(Order(id=uuid.uuid4().hex, plan="pro", amount=990,
                        email=email, status="paid"))
    with session_scope() as s:
        from sqlalchemy import select
        row = s.execute(select(NurtureSubscriber).where(
            NurtureSubscriber.email == email)).scalars().first()
        return row.id


# --- отписка по nurture-токену ---

def test_otpiska_po_nurture_tokenu():
    _sub("chelovek@example.ru", token="nur-tok")
    email = repo.nurture_unsubscribe_by_token("nur-tok")
    assert email == "chelovek@example.ru"
    # повторная отписка идемпотентна и всё равно возвращает email
    assert repo.nurture_unsubscribe_by_token("nur-tok") == "chelovek@example.ru"
    assert repo.nurture_to_send(10) == []


def test_otpiska_neizvestnyy_i_pustoy_token():
    assert repo.nurture_unsubscribe_by_token("nope") is None
    assert repo.nurture_unsubscribe_by_token("") is None


def test_otpiska_marshrut_nurture_token(client):
    """Роут /unsubscribe/{token}: nurture-токен обязан приниматься."""
    _sub("chelovek@example.ru", token="nur-tok")
    r = client.get("/unsubscribe/nur-tok")
    assert r.status_code == 200
    assert "Вы отписаны" in r.text
    assert repo.nurture_to_send(10) == []


def test_otpiska_po_nurture_glushit_i_lidov(client):
    """Ссылка в письме одна и обещает «больше не пишем»: отписка по
    nurture-токену отписывает и лидов с тем же email (иначе завтра придёт
    догонялка по отчёту)."""
    repo.create_lead("scan-1", "https://site.ru", "chelovek@example.ru")
    _sub("chelovek@example.ru", token="nur-tok")
    client.get("/unsubscribe/nur-tok")
    assert repo.leads_to_followup(delay_hours=0, max_age_days=14) == []


def test_neizvestnyy_token_marshrut(client):
    r = client.get("/unsubscribe/bogus")
    assert r.status_code == 200
    assert "недействительна" in r.text


# --- оплатившие не получают продажи ---

def test_oplativshiy_isklyuchaetsya_v_run(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr("lawcheck.notify.mailer.send_email",
                        lambda to, *a, **kw: sent.append(to) or True)
    _sub("platil@example.ru", paid_order=True)
    _sub("ne-platil@example.ru")
    summary = nurture.run(limit=10)
    assert summary["sent"] == 1
    assert summary["paid_skipped"] == 1
    assert sent == ["ne-platil@example.ru"]
    # оплативший отписан — следующий прогон его не видит
    assert repo.nurture_to_send(10) == [] or all(
        s.email != "platil@example.ru" for s in repo.nurture_to_send(10))


def test_nurture_remove_paid_idempotenten():
    _sub("platil@example.ru", paid_order=True)
    assert repo.nurture_remove_paid("platil@example.ru") == 1
    assert repo.nurture_remove_paid("platil@example.ru") == 0
    assert repo.nurture_remove_paid("ne-platili@example.ru") == 0


# --- содержимое писем ---

def test_vse_8_shagov_renderyatsya():
    for step in range(1, 9):
        subject, html_body, text_body = nurture._render_email(step, "tok")
        assert subject and "LawCheck" in html_body
        assert "/unsubscribe/tok" in html_body
        assert "Отписаться: https://lawchek.ru/unsubscribe/tok" in text_body
        assert "utm_content=email" in html_body and f"email{step}" in html_body


def test_net_ofera_prizraka_i_vydumannogo_keysa():
    """Письмо 8 продавало «1 ₽ вместо 2 990 ₽/мес» (нет такого тарифа),
    письмо 7 — выдуманного клиента с оборотом 500 млн. Запретные строки."""
    for step in range(1, 9):
        subject, html_body, text_body = nurture._render_email(step, "tok")
        blob = f"{subject} {html_body} {text_body}"
        for zapret in ["1 рубль", "за 1 ₽", "2 990", "47 точек",
                       "500 млн", "1,2 млн", "73%", "63 дня", "4% выручки"]:
            assert zapret not in blob, f"шаг {step}: призрак «{zapret}»"


def test_offer_soopadaet_s_tarifami():
    """Письма 7–8 ведут на /pricing и называют реальную цену Pro."""
    for step in (7, 8):
        subject, html_body, _ = nurture._render_email(step, "tok")
        assert "/pricing" in html_body, f"шаг {step}: CTA мимо тарифов"
    subject8, html8, _ = nurture._render_email(8, "tok")
    assert "990" in html8 and "990" in subject8


def test_educational_shagi_vedut_na_glavnuyu():
    for step in range(1, 7):
        _, html_body, _ = nurture._render_email(step, "tok")
        cta = [h for h in html_body.split('href="')[1:] if "utm_content" in h][0]
        assert cta.startswith("https://lawchek.ru/?utm"), f"шаг {step}: {cta[:60]}"


# --- шаг и интервал ---

def test_advance_sdvigaet_shag_i_datu():
    sid = _sub("chelovek@example.ru")
    repo.nurture_advance(sid)
    subs = repo.nurture_to_send(10)
    assert subs == []  # следующее письмо через 7 дней
    with session_scope() as s:
        row = s.get(NurtureSubscriber, sid)
        assert row.step == 2


def test_podpiska_dedup_po_email():
    assert repo.nurture_subscribe("chelovek@example.ru") is True
    assert repo.nurture_subscribe("chelovek@example.ru") is False
