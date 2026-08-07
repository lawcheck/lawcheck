"""Свой сайт через собственный сканер не проходит.

Оценка соответствия, выданная сервисом самому себе, ничего не стоит для того,
кто её читает, — поэтому отказываем на входе, а не рисуем результат.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.session import init_db
from lawcheck.utils.domain import is_own_site, registrable_domain
from lawcheck.web.routes import _feed_domain_blocked


@pytest.fixture(autouse=True)
def _own_domain(monkeypatch):
    monkeypatch.setattr(settings, "site_base_url", "https://lawchek.ru")


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "self.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


# === Что считается своим адресом ===

@pytest.mark.parametrize("value", [
    "lawchek.ru",
    "https://lawchek.ru",
    "http://lawchek.ru/pricing?utm_source=direct",
    "www.lawchek.ru",
    "LAWCHEK.RU",
    "staging.lawchek.ru",   # поддомен — тот же сайт
    "lawchek.ru:8000",
])
def test_svoy_adres(value):
    assert is_own_site(value) is True


@pytest.mark.parametrize("value", [
    "example.ru",
    "mystore.ru",
    "lawcheck.ru",           # другой домен: c вместо k
    "lawchek.ru.example.com",  # свой домен в поддомене чужого
    "notlawchek.ru",
])
def test_chuzhoy_adres(value):
    assert is_own_site(value) is False


def test_domen_beryotsya_iz_nastroek(monkeypatch):
    """Домен не константа в коде: на staging исключение едет вместе с базой."""
    monkeypatch.setattr(settings, "site_base_url", "https://staging.example.com")
    assert is_own_site("example.com") is True
    assert is_own_site("lawchek.ru") is False


def test_registrable_domain():
    assert registrable_domain("https://www.lawchek.ru/pricing") == "lawchek.ru"
    assert registrable_domain("lawchek.ru") == "lawchek.ru"


# === Отказ на входах ===

def test_forma_ne_zapuskaet_skan(client):
    r = client.post("/scan", data={"url": "lawchek.ru", "max_pages": "10"})
    assert r.status_code == 200
    # Не редирект на /report — скан не создан.
    assert "location" not in {k.lower() for k in r.headers}
    assert "Сами себя не проверяем" in r.text


def test_forma_lovit_www_i_shemu(client):
    r = client.post("/scan", data={"url": "https://www.lawchek.ru/", "max_pages": "10"})
    assert r.status_code == 200
    assert "Сами себя не проверяем" in r.text


def test_api_otkazyvaet(client):
    r = client.post("/api/scan", json={"url": "https://lawchek.ru", "max_pages": 5})
    assert r.status_code == 422
    assert "сами себя не проверяем" in r.json()["detail"]


def test_chuzhoy_sayt_cherez_formu_ne_lomaetsya(client, monkeypatch):
    """Отказ не должен зацепить обычную проверку — она идёт своим путём."""
    monkeypatch.setattr("lawcheck.web.routes.check_url", lambda url: None)
    monkeypatch.setattr("lawcheck.web.routes.start_scan",
                        lambda bg, url, max_pages, user_id=None: "deadbeef")
    r = client.post("/scan", data={"url": "example.ru", "max_pages": "10"})
    assert r.status_code == 303
    assert r.headers["location"] == "/report/deadbeef"


# === Витрина ===

def test_svoy_domen_ne_popadaet_v_lentu():
    """Сканы, снятые до появления правила, в БД остались — фильтруем на выдаче."""
    assert _feed_domain_blocked("lawchek.ru") is True
    assert _feed_domain_blocked("example.ru") is False


def test_razbor_domena_ne_hodit_v_set():
    """`_feed_domain_blocked` вызывается при рендере главной — до 160 раз на
    запрос. У `tldextract.extract` по умолчанию включён public suffix list из
    сети: на пустом кеше (а он пуст после каждой пересборки контейнера) первый
    вызов уходит на publicsuffix.org, и ждёт его первый посетитель после выката.

    Тест сторожит именно это: экстрактор должен работать на снапшоте из пакета.
    """
    from lawcheck.utils.domain import _extract

    assert _extract.suffix_list_urls == (), (
        "экстрактор снова тянет public suffix list по сети — "
        "в пути веб-запроса этого быть не должно"
    )
