"""Хвост ревью: гонка при отметке оплаты, очередь краулера, выбор Политики."""
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from lawcheck import net
from lawcheck.checks.base import Severity
from lawcheck.checks.pd_152.policy_validity import PolicyValidityCheck
from lawcheck.config import settings
from lawcheck.crawler.crawler import Crawler
from lawcheck.crawler.snapshot import Link, PageSnapshot, SiteSnapshot
from lawcheck.db import repo, session
from lawcheck.db.session import init_db


@pytest.fixture()
def db(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "tail.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    init_db()
    yield
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


# === №12: отметка оплаты не должна срабатывать дважды ===

def test_povtornaya_otmetka_oplaty_vozvrashchaet_false(db):
    """True — это право отправить уведомление. Второй раз его быть не должно."""
    oid = uuid.uuid4().hex
    repo.create_order(oid, "pro", 990)
    assert repo.mark_order_paid(oid) is True
    assert repo.mark_order_paid(oid) is False
    assert repo.mark_order_paid(oid) is False


def test_otmetka_nesushchestvuyushchego_zakaza_ne_padaet(db):
    assert repo.mark_order_paid("net-takogo-zakaza") is False


# === №18: пиннинг IP Telegram протухает ===

def test_kesh_ip_telegram_protuhaet(monkeypatch):
    """Без TTL мёртвый IP жил до рестарта контейнера."""
    calls = []

    def fake_connection(addr, timeout=None):
        calls.append(addr[0])

        class S:
            def close(self):
                pass
        return S()

    monkeypatch.setattr(net.socket, "create_connection", fake_connection)
    monkeypatch.setattr(net, "_orig_getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("149.154.167.220", 443))])
    monkeypatch.setattr(net, "_tg_ip_cache", None)
    monkeypatch.setattr(net, "_tg_ip_cached_at", 0.0)

    assert net._pick_telegram_ip() == "149.154.167.220"
    net._pick_telegram_ip()
    assert len(calls) == 1, "второй вызов должен брать из кеша"

    # Отматываем время за пределы TTL.
    monkeypatch.setattr(net, "_tg_ip_cached_at", time.monotonic() - net._TG_IP_TTL_SEC - 1)
    net._pick_telegram_ip()
    assert len(calls) == 2, "после TTL адрес должен перепроверяться"


# === №17: очередь краулера ===

class _FakeBrowser:
    """Отдаёт страницу с большим числом ссылок на свой же домен."""

    def __init__(self, links_per_page: int):
        self.links_per_page = links_per_page
        self.fetched: list[str] = []

    async def fetch(self, url: str) -> PageSnapshot:
        self.fetched.append(url)
        links = [Link(url=f"https://mysite.ru/p{i}?page={len(self.fetched)}", text="")
                 for i in range(self.links_per_page)]
        return PageSnapshot(url=url, status=200, text="текст", links=links)


@pytest.mark.asyncio
async def test_ochered_ne_rastyot_beskonechno(monkeypatch):
    monkeypatch.setattr("lawcheck.crawler.crawler.check_url", lambda url: None)
    monkeypatch.setattr("lawcheck.crawler.crawler.is_safe", lambda url: True)
    browser = _FakeBrowser(links_per_page=500)
    snap = await Crawler(browser, max_pages=5).crawl("https://mysite.ru/")
    assert len(snap.pages) == 5
    assert snap.budget_reached is True


@pytest.mark.asyncio
async def test_prioritetnye_stranicy_obhodyatsya_ranshe(monkeypatch):
    """Куча должна сохранять приоритет: Политика важнее случайной страницы."""
    monkeypatch.setattr("lawcheck.crawler.crawler.check_url", lambda url: None)
    monkeypatch.setattr("lawcheck.crawler.crawler.is_safe", lambda url: True)

    class B:
        def __init__(self):
            self.fetched = []

        async def fetch(self, url):
            self.fetched.append(url)
            links = []
            if url.endswith("/"):
                links = [Link(url="https://mysite.ru/blog/post-1", text="пост"),
                         Link(url="https://mysite.ru/o-kompanii", text="о нас"),
                         Link(url="https://mysite.ru/policy", text="Политика конфиденциальности")]
            return PageSnapshot(url=url, status=200, text="т", links=links)

    b = B()
    await Crawler(b, max_pages=2).crawl("https://mysite.ru/")
    assert b.fetched[1] == "https://mysite.ru/policy"


# === №20: A2 выбирает лучший документ из нескольких ссылок ===

def test_a2_beryot_luchshiy_dokument_a_ne_pervyy():
    """В футере устаревшая ссылка на 404, на странице — живой документ."""
    home = PageSnapshot(url="https://mysite.ru/", status=200, text="главная", links=[
        Link(url="https://mysite.ru/old-policy", text="Политика конфиденциальности"),
    ])
    about = PageSnapshot(url="https://mysite.ru/about", status=200, text="о нас", links=[
        Link(url="https://mysite.ru/policy", text="Политика обработки персональных данных"),
    ])
    broken = PageSnapshot(url="https://mysite.ru/old-policy", status=404, text="")
    good = PageSnapshot(url="https://mysite.ru/policy", status=200, text="х" * 3000)
    snap = SiteSnapshot(start_url="https://mysite.ru/",
                        pages=[home, about, broken, good])

    findings = PolicyValidityCheck().run(snap)
    assert len(findings) == 1
    assert findings[0].severity == Severity.OK
    assert findings[0].location == "https://mysite.ru/policy"


def test_a2_soobshchaet_ob_oshibke_esli_vse_ssylki_bity():
    home = PageSnapshot(url="https://mysite.ru/", status=200, text="главная", links=[
        Link(url="https://mysite.ru/policy", text="Политика конфиденциальности"),
    ])
    broken = PageSnapshot(url="https://mysite.ru/policy", status=404, text="")
    findings = PolicyValidityCheck().run(
        SiteSnapshot(start_url="https://mysite.ru/", pages=[home, broken]))
    assert findings[0].severity == Severity.CRITICAL


# === №25: слаг статьи блога не должен превращаться в путь к файлу ===

@pytest.mark.parametrize("slug", [
    "../../etc/passwd",
    "..%2F..%2Fsecret",
    "/etc/passwd",
    "articles/../../secret",
    "ПОЛИТИКА",          # верхний регистр и кириллица не наши слаги
    "a" * 200,
    "",
])
def test_slag_bloga_otsekaetsya(slug):
    from lawcheck.web.blog import get_article
    assert get_article(slug) is None
