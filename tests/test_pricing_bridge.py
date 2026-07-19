"""Мост «Ваш отчёт по {url}» на /pricing?scan=… — персонализация точки
перехода из отчёта (воронка scan→pay, ревизия 2026-07-19)."""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.models import Finding, Scan
from lawcheck.db.session import init_db, session_scope


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "pricing.db"
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


SCAN_ID = "c" * 32


def _scan_with_problems(n_problems: int = 5):
    with session_scope() as s:
        s.add(Scan(id=SCAN_ID, url="https://mysite.ru", status="done",
                   pages_crawled=3))
        for i in range(n_problems):
            s.add(Finding(scan_id=SCAN_ID, check_id=f"A{i}", severity="critical",
                          title=f"Проблема {i}", evidence="", location="",
                          law_reference="", recommendation=f"Исправление {i}"))


def test_pricing_bridge_shown_for_scan(client):
    _scan_with_problems(5)
    r = client.get(f"/pricing?scan={SCAN_ID}")
    assert r.status_code == 200
    assert "Ваш отчёт по" in r.text
    assert "https://mysite.ru" in r.text
    # 5 проблем с рецептами − 2 бесплатных = 3 закрытых
    assert "3 исправления" in r.text
    # scan_id пробрасывается в форму покупки
    assert f'name="scan_id" value="{SCAN_ID}"' in r.text


def test_pricing_no_bridge_without_scan(client):
    r = client.get("/pricing")
    assert r.status_code == 200
    assert "Ваш отчёт по" not in r.text


def test_pricing_bridge_ignores_unknown_scan(client):
    r = client.get("/pricing?scan=deadbeef")
    assert r.status_code == 200
    assert "Ваш отчёт по" not in r.text
