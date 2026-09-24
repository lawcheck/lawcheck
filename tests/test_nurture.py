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


def test_cta_vedyot_tuda_chto_obeshchaet_knopka():
    """Кнопки «Как мы находим скрытые трекеры» вели на главную. Теперь каждый
    CTA — на свою страницу, и эта страница существует."""
    blog_dir = Path(nurture.__file__).resolve().parents[1] / "content" / "blog"
    for data in nurture._EMAILS:
        _, html_body, _ = nurture._render_email(data["step"], "tok")
        path = data["cta_path"]
        assert f'href="https://lawchek.ru{path}?utm' in html_body
        if path.startswith("/blog/"):
            slug = path.removeprefix("/blog/")
            assert (blog_dir / f"{slug}.md").exists(), f"шаг {data['step']}: нет статьи {slug}"
        else:
            assert path in ("/", "/pricing", "/politika-obrabotki-pd")


def test_net_obeshchaniy_kotoryh_net_v_skanere():
    """Ревью 24.09: письма обещали то, чего сканер не делает."""
    for step in range(1, 9):
        subject, html_body, text_body = nurture._render_email(step, "tok")
        blob = f"{subject} {html_body} {text_body}".lower()
        for zapret in ["fingerprint", "localstorage", "договорах с третьими",
                       "email-рассыл", "мгновенн", "при каждом изменении",
                       "оплата разовая"]:
            assert zapret not in blob, f"шаг {step}: «{zapret}»"


def test_pismo_8_nazyvaet_oba_tarifa():
    subject, html_body, text_body = nurture._render_email(8, "tok")
    assert "990 ₽ за месяц" in text_body and "без автопродления" in text_body
    assert "8 000 ₽" in subject and "8 000 ₽ разово" in text_body


def test_tekstovaya_versiya_po_abzacam():
    """Текстовая версия склеивала все абзацы в одну строку."""
    _, _, text_body = nurture._render_email(1, "tok")
    assert "\n\nНачните с пяти пунктов:" in text_body
    assert "\n– Галочку согласия" in text_body



def _scan_with_lead(email: str, sid: str, *, magnet: bool = False) -> None:
    from lawcheck.db.models import Finding, Scan
    with session_scope() as s:
        s.add(Scan(id=sid, url="https://www.mysite.ru", status="done", pages_crawled=2))
        for cid, sev in [("B2", "critical"), ("D2", "warning"), ("A1", "ok")]:
            s.add(Finding(scan_id=sid, check_id=cid, severity=sev, title=cid,
                          evidence="e", recommendation="r"))
    repo.create_lead("magnet:obrazec" if magnet else sid, "https://www.mysite.ru", email)


def test_latest_report_scan_id_propuskaet_magnity():
    _scan_with_lead("a@example.ru", "s1")
    _scan_with_lead("a@example.ru", "s2", magnet=True)
    assert repo.latest_report_scan_id("a@example.ru") == "s1"
    assert repo.latest_report_scan_id("net@example.ru") is None


def test_pismo_8_pro_sayt_podpischika(monkeypatch):
    """Письмо-оффер называет сайт и число нарушений, CTA — на /pricing?scan=,
    где страница сама выберет главный вариант под этот отчёт."""
    letters: list[tuple[str, str]] = []
    monkeypatch.setattr("lawcheck.notify.mailer.send_email",
                        lambda to, subj, html_body, text_body: letters.append((html_body, text_body)) or True)
    _scan_with_lead("a@example.ru", "s1")
    _sub("a@example.ru", step=8, token="tok")
    with session_scope() as s:
        from sqlalchemy import select
        sub = s.execute(select(NurtureSubscriber)).scalars().one()
        s.expunge(sub)
    assert nurture.send_one(sub)
    html_body, text_body = letters[0]
    assert "На mysite.ru проверка нашла 2 нарушения." in text_body
    assert "/pricing?scan=s1&utm_source=email" in html_body


def test_pismo_8_bez_otcheta_obshchee():
    """Подписчик с магнита: сайта нет — письмо общее, CTA на /pricing без scan."""
    _, html_body, text_body = nurture._render_email(8, "tok", None)
    assert "проверка нашла" not in text_body and "?scan=" not in html_body

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
