"""Посадочная /uvedomlenie-rkn и проверка по реестру /reestr-rkn (web/rkn.py)."""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.session import init_db
from lawcheck.external.rkn_operators import RknLookupResult, RknOperator
from lawcheck.web import rkn as rkn_web


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "rkn.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret-please-ignore")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


VALID_INN = "771481979800"  # проходит контрольную сумму (12 цифр, ИП)


def test_landing_renders(client):
    r = client.get("/uvedomlenie-rkn")
    assert r.status_code == 200
    assert "Уведомление в Роскомнадзор" in r.text
    assert "/reestr-rkn" in r.text  # встроенная форма проверки


def test_check_page_renders(client):
    r = client.get("/reestr-rkn")
    assert r.status_code == 200
    assert "реестре операторов" in r.text


def test_check_invalid_inn(client):
    r = client.post("/reestr-rkn", data={"inn": "12345"})
    assert r.status_code == 200
    assert "не похоже на ИНН" in r.text


def test_check_found(client, monkeypatch):
    op = RknOperator(inn=VALID_INN, registry_id="77-12-345678",
                     name="ООО «Фисташки»", detail_url="https://pd.rkn.gov.ru/x")
    monkeypatch.setattr(rkn_web, "lookup_by_inn",
                        lambda inn: RknLookupResult(operator=op))
    r = client.post("/reestr-rkn", data={"inn": f" {VALID_INN} "})
    assert r.status_code == 200
    assert "Запись в реестре найдена" in r.text
    assert "ООО «Фисташки»" in r.text and "77-12-345678" in r.text


def test_check_not_found_shows_risk_and_cta(client, monkeypatch):
    monkeypatch.setattr(rkn_web, "lookup_by_inn",
                        lambda inn: RknLookupResult(operator=None, not_found=True))
    r = client.post("/reestr-rkn", data={"inn": VALID_INN})
    assert r.status_code == 200
    assert "в реестре операторов не найден" in r.text
    assert "300 000" in r.text
    assert "/uvedomlenie-rkn" in r.text  # CTA «подготовить уведомление»


def test_check_registry_error(client, monkeypatch):
    monkeypatch.setattr(rkn_web, "lookup_by_inn",
                        lambda inn: RknLookupResult(operator=None, error="timeout"))
    r = client.post("/reestr-rkn", data={"inn": VALID_INN})
    assert r.status_code == 200
    assert "не отвечает" in r.text


def test_sitemap_includes_rkn_pages(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "/uvedomlenie-rkn" in r.text and "/reestr-rkn" in r.text
