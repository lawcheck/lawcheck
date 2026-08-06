"""Согласие на обработку ПДн у форм собственного сайта (ст. 9 152-ФЗ).

Свой же сканер ставил здесь два критикала B2: форма оплаты на /pricing и форма
проверки ИНН на /uvedomlenie-rkn собирали ПДн без чекбокса согласия.

Проверяем обе стороны: разметку (чекбокс есть, не предустановлен, рядом ссылка
на Политику — ровно то, что смотрит B2) и сервер (без согласия запрос не
выполняется, даже если POST пришёл в обход браузера).
"""
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.session import init_db
from lawcheck.external.rkn_operators import RknLookupResult
from lawcheck.web import rkn as rkn_web

VALID_INN = "771481979800"


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "consent.db"
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


def _form_html(page: str, action: str) -> str:
    """Кусок HTML от <form action=...> до </form>."""
    m = re.search(r"<form[^>]*action=\"" + re.escape(action) + r"\"[^>]*>.*?</form>",
                  page, re.S)
    assert m, f"форма с action={action} не найдена"
    return m.group(0)


# === Разметка: то, на что смотрит наша же проверка B2 ===

@pytest.mark.parametrize("url,action", [
    ("/pricing", "/buy/pro"),
    ("/reestr-rkn", "/reestr-rkn"),
    ("/uvedomlenie-rkn", "/reestr-rkn"),
])
def test_chekboks_soglasiya_v_forme(client, url, action):
    form = _form_html(client.get(url).text, action)
    assert 'name="pd_consent"' in form
    assert "Даю согласие на обработку" in form


@pytest.mark.parametrize("url,action", [
    ("/pricing", "/buy/pro"),
    ("/reestr-rkn", "/reestr-rkn"),
    ("/uvedomlenie-rkn", "/reestr-rkn"),
])
def test_chekboks_ne_predustanovlen(client, url, action):
    """`checked` по умолчанию — тот же критикал B2: согласие должно быть
    результатом активного действия, а не значением по умолчанию."""
    form = _form_html(client.get(url).text, action)
    checkbox = re.search(r"<input[^>]*name=\"pd_consent\"[^>]*>", form)
    assert checkbox and "checked" not in checkbox.group(0)


@pytest.mark.parametrize("url,action", [
    ("/pricing", "/buy/pro"),
    ("/reestr-rkn", "/reestr-rkn"),
    ("/uvedomlenie-rkn", "/reestr-rkn"),
])
def test_ssylka_na_politiku_ryadom_s_formoy(client, url, action):
    """Без ссылки на Политику в радиусе формы B2 даёт warning вместо ok."""
    assert "/privacy" in _form_html(client.get(url).text, action)


# === Сервер: галочка в разметке без проверки на бэкенде — театр ===

def test_reestr_bez_soglasiya_ne_hodit_v_reestr(client, monkeypatch):
    called = []
    monkeypatch.setattr(rkn_web, "lookup_by_inn",
                        lambda inn: called.append(inn) or RknLookupResult(operator=None))
    r = client.post("/reestr-rkn", data={"inn": VALID_INN})
    assert r.status_code == 200
    assert "Нужно согласие на обработку данных" in r.text
    assert called == []  # до реестра дело не дошло


@pytest.mark.parametrize("value", ["", "0", "false", "нет"])
def test_reestr_lozhnye_znacheniya_ne_soglasie(client, monkeypatch, value):
    """Скрытое поле-спутник со строкой `0` не должно сойти за согласие."""
    monkeypatch.setattr(rkn_web, "lookup_by_inn",
                        lambda inn: RknLookupResult(operator=None, not_found=True))
    r = client.post("/reestr-rkn", data={"inn": VALID_INN, "pd_consent": value})
    assert "Нужно согласие на обработку данных" in r.text


def test_buy_bez_soglasiya_otklonyaetsya(client):
    r = client.post("/buy/pro", data={"email": "buyer@example.com"})
    assert r.status_code == 422


def test_buy_s_soglasiem_prohodit(client):
    """Касса в тестах не настроена — доходим до fallback-страницы заявки."""
    r = client.post("/buy/pro", data={"email": "buyer@example.com", "pd_consent": "1"})
    assert r.status_code == 200
