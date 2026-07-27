"""SSRF: краулер идёт по адресу от произвольного посетителя.

Сервис живёт в сети Compose рядом с postgres, redis, api и caddy, поэтому
непроверенный адрес — это доступ во внутреннюю сеть чужими руками.
"""
import socket
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.crawler.url_guard import UnsafeUrl, check_url, is_safe
from lawcheck.db import session
from lawcheck.db.session import init_db


@pytest.fixture()
def resolver(monkeypatch):
    """Подменяем DNS: тесты не должны зависеть от сети и чужих зон."""
    table = {
        "example.ru": "93.184.216.34",
        "api": "172.18.0.5",            # сосед по сети Compose
        "internal.example.ru": "10.0.0.7",  # публичный домен, приватная A-запись
        "metadata.example": "169.254.169.254",
    }

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host in table:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]
        if host in ("localhost",):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        raise OSError("name or service not known")

    monkeypatch.setattr("lawcheck.crawler.url_guard.socket.getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize("url", [
    "http://api:8000/inbox",           # сосед по сети Compose
    "http://localhost:8000/inbox",
    "http://127.0.0.1/",
    "http://[::1]/",
    "http://169.254.169.254/",         # метаданные облака
    "http://metadata.example/",        # то же, но через DNS
    "http://internal.example.ru/",     # публичное имя, приватный адрес
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://0.0.0.0/",
])
def test_nepublichnye_adresa_otklonyayutsya(resolver, url):
    assert is_safe(url) is False


def test_publichnyy_adres_prohodit(resolver):
    assert is_safe("https://example.ru/policy") is True


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://example.ru/",
    "ftp://example.ru/",
    "https://",
])
def test_postoronnie_shemy_otklonyayutsya(resolver, url):
    assert is_safe(url) is False


def test_nerezolvyashchiysya_host_otklonyaetsya(resolver):
    with pytest.raises(UnsafeUrl, match="не резолвится"):
        check_url("https://nope.invalid/")


def test_soobshchenie_ob_oshibke_nazyvaet_prichinu(resolver):
    with pytest.raises(UnsafeUrl, match="внутренний адрес"):
        check_url("http://api:8000/inbox")


# === Входные точки ===

@pytest.fixture()
def client(monkeypatch, resolver):
    tmp = Path(tempfile.mkdtemp()) / "guard.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def test_veb_forma_ne_zapuskaet_skan_vnutrennego_adresa(client):
    r = client.post("/scan", data={"url": "http://api:8000/inbox"})
    assert r.status_code == 422
    assert "Проверить этот адрес нельзя" in r.text


def test_api_ne_zapuskaet_skan_vnutrennego_adresa(client):
    r = client.post("/api/scan", json={"url": "http://127.0.0.1:8000/inbox"})
    assert r.status_code == 422


def test_veb_forma_ogranichivaet_max_pages(client):
    """Публичная форма не должна быть защищена слабее API-схемы (ge=1, le=100)."""
    r = client.post("/scan", data={"url": "https://example.ru", "max_pages": 1000000})
    assert r.status_code == 303
    scan_id = r.headers["location"].removeprefix("/report/")
    from lawcheck.db import repo
    assert repo.get_scan(scan_id).max_pages == 100


def test_inbox_ne_otdayotsya_na_vnutrenniy_host(client):
    """SSRF-запрос идёт с Host внутреннего сервиса — до данных не доходит."""
    assert client.get("/inbox").status_code == 200          # через прокси, Host сайта
    r = client.get("/inbox", headers={"host": "api:8000"})   # мимо прокси, изнутри сети
    assert r.status_code == 404
