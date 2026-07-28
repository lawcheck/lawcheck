"""Директивы для поисковых роботов: robots.txt, sitemap.xml, индексация отчётов."""
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.session import init_db


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "seo.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret-please-ignore")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def test_robots_skleivaet_reklamnye_metki(client):
    """Переход из Директа несёт yclid — без Clean-param это дубль главной."""
    r = client.get("/robots.txt")
    assert r.status_code == 200
    clean = [ln for ln in r.text.splitlines() if ln.startswith("Clean-param:")]
    assert len(clean) == 1
    for param in ("utm_source", "utm_medium", "utm_campaign", "yclid", "_openstat"):
        assert param in clean[0]


def test_robots_ne_zakryvaet_sait(client):
    r = client.get("/robots.txt")
    assert "Allow: /" in r.text
    assert "Disallow: /" not in r.text
    assert "Sitemap: http://testserver/sitemap.xml" in r.text


def test_kazhdaya_stranica_so_svoim_description(client):
    """Шаблон без своего meta_description наследует текст главной.

    Четыре страницы делили одно описание, пока это не всплыло в обходе
    всего sitemap: Lighthouse проверяет одну страницу и такое не видит.
    """
    import re

    paths = ["/", "/pricing", "/privacy", "/oferta", "/uvedomlenie-rkn"]
    seen: dict[str, str] = {}
    for path in paths:
        html = client.get(path).text
        m = re.search(r'<meta name="description" content="(.*?)"', html, re.S)
        assert m, f"{path}: нет meta description"
        desc = m.group(1)
        assert desc not in seen, f"{path} дублирует описание {seen[desc]}"
        seen[desc] = path


def test_chuzhoy_otchet_zakryt_ot_poiska(client):
    """Отчёты о чужих сайтах не должны попадать в индекс.

    К 28.07.2026 Яндекс держал в поиске 105 страниц, почти все — отчёты, и
    70% показов домена приходилось на запросы вида «hentasis5» — по отчёту
    об adult-домене, который кто-то прогнал через сканер.
    """
    from lawcheck.db import repo
    scan_id = uuid.uuid4().hex
    repo.create_scan(scan_id, "https://example.ru", max_pages=5)
    r = client.get(f"/report/{scan_id}")
    assert r.status_code == 200
    assert r.headers["x-robots-tag"] == "noindex, follow"


def test_vitrina_otchetov_ostaetsya_otkrytoy(client, monkeypatch):
    """Фиксированный набор — единственное исключение, у него noindex нет."""
    from lawcheck.web import routes
    from lawcheck.db import repo
    scan_id = uuid.uuid4().hex
    repo.create_scan(scan_id, "https://example.ru", max_pages=5)
    monkeypatch.setattr(routes, "_INDEXABLE_REPORTS", frozenset({scan_id}))
    r = client.get(f"/report/{scan_id}")
    assert r.status_code == 200
    assert "x-robots-tag" not in r.headers


def test_v_sitemap_tolko_vitrina_otchetov(client, monkeypatch):
    from lawcheck.web import routes
    r = client.get("/sitemap.xml")
    for scan_id in routes._INDEXABLE_REPORTS:
        assert f"<loc>http://testserver/report/{scan_id}</loc>" in r.text
    # Ровно столько, сколько в наборе — случайные отчёты в карту не попадают.
    assert r.text.count("/report/") == len(routes._INDEXABLE_REPORTS)


def test_sitemap_datiruet_listing_bloga_svezhei_statei(client, monkeypatch):
    """У /blog нет своей даты — берём её у самой свежей статьи на листинге."""
    monkeypatch.setattr(settings, "seo_enabled", True)
    from lawcheck.web import blog

    dates = [a.date.isoformat() for a in blog.list_articles()
             if a.date and a.date.year > 1]
    assert dates, "в блоге нет ни одной датированной статьи — тест бессмыслен"

    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert (f"<loc>http://testserver/blog</loc><lastmod>{max(dates)}</lastmod>"
            in r.text)
