"""Лид-магниты: открытый текст образца на странице статьи + копия на почту."""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.session import init_db
from lawcheck.web import magnets


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "magnets.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret-please-ignore")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    monkeypatch.setattr(settings, "seo_enabled", True)
    init_db()
    from lawcheck.api.main import create_app
    app = create_app()
    # routes.py решает, подключать ли роутер блога, ОДИН раз — на импорте модуля.
    # `monkeypatch.setattr(settings, ...)` к тому моменту уже опоздал: на машине
    # с SEO_ENABLED=true в .env роутер есть и тесты проходят, на чистом раннере
    # CI его нет и страницы отдают 404. Подключаем явно, если его не оказалось.
    if not any(getattr(r, "path", "").startswith("/blog/") for r in app.routes):
        from lawcheck.web import blog as blog_web
        app.include_router(blog_web.router)
    with TestClient(app, follow_redirects=False) as c:
        yield c


def test_magnity_privyazany_k_sushchestvuyushchim_statyam():
    """Магнит без своей статьи не отрендерится нигде — слаги должны совпадать."""
    from lawcheck.web import blog
    slugs = {a.slug for a in blog.list_articles()}
    for slug in magnets.MAGNETS:
        assert slug in slugs, f"магнит {slug} ссылается на несуществующую статью"


def test_tekst_obrazca_otdaetsya_otkryto(client):
    """Смысл магнита — текст индексируется. Под формой его быть не должно."""
    r = client.get("/blog/soglasie-na-obrabotku-personalnyh-dannyh")
    assert r.status_code == 200
    assert "СОГЛАСИЕ" in r.text
    assert "статьёй 9 Федерального закона" in r.text
    assert 'action="/obrazec/soglasie-na-obrabotku-personalnyh-dannyh"' in r.text


def test_statya_bez_magnita_ne_pokazyvaet_formu(client):
    r = client.get("/blog/cookie-banner-po-zakonu")
    assert r.status_code == 200
    assert 'class="magnet-form"' not in r.text


def test_zapros_obrazca_sozdaet_lid(client, monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr("lawcheck.notify.mailer.send_email",
                        lambda to, subj, html, text=None: sent.append((to, subj)) or True)
    slug = "soglasie-na-obrabotku-personalnyh-dannyh"
    r = client.post(f"/obrazec/{slug}", data={"email": "Chelovek@Example.RU"})
    assert r.status_code == 303
    assert r.headers["location"] == f"/blog/{slug}?msent=1#obrazec"
    leads = repo.list_leads(10)
    assert len(leads) == 1
    assert leads[0].email == "chelovek@example.ru"  # приведён к нижнему регистру
    assert leads[0].scan_id == f"magnet:{slug}"
    assert sent and sent[0][0] == "chelovek@example.ru"


def test_krivoy_email_ne_sozdaet_lid(client):
    slug = "soglasie-na-obrabotku-personalnyh-dannyh"
    r = client.post(f"/obrazec/{slug}", data={"email": "не-почта"})
    assert r.status_code == 303
    assert "mfail=1" in r.headers["location"]
    assert repo.list_leads(10) == []


def test_neizvestnyy_obrazec_404(client):
    r = client.post("/obrazec/vydumannyy", data={"email": "a@b.ru"})
    assert r.status_code == 404


def test_lid_s_magnita_ne_lomaet_rassylku_dogonyalok(client, monkeypatch):
    """У такого лида нет скана. followup.run обязан его пропустить, а не упасть."""
    monkeypatch.setattr("lawcheck.notify.mailer.send_email",
                        lambda *a, **kw: True)
    slug = "soglasie-na-obrabotku-personalnyh-dannyh"
    client.post(f"/obrazec/{slug}", data={"email": "lead@example.ru"})
    from lawcheck.reporting import followup
    summary = followup.run(limit=10, delay_hours=0, max_age_days=14, dry_run=False)
    assert summary["sent"] == 0
