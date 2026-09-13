"""Отдельный текст согласия и журнал согласий (ст. 9 152-ФЗ).

После 01.09.2025 согласие оформляется отдельно от договора и Политики, а доказать
его должен оператор (ч. 3 ст. 9). Галочка в форме после отправки следа не
оставляет, поэтому проверяем обе стороны: у каждой формы ссылка на /soglasie,
а после отправки с согласием в consent_log лежит строка с формой, записью,
версией текста и IP. Без согласия строки нет.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.models import ConsentLog
from lawcheck.db.session import init_db, session_scope
from lawcheck.external.rkn_operators import RknLookupResult
from lawcheck.notify import telegram
from lawcheck.payments.tochka import PaymentLink
from lawcheck.utils.consent import CONSENT_VERSION
from lawcheck.web import payments
from lawcheck.web import rkn as rkn_web

VALID_INN = "771481979800"
IP = "203.0.113.7"
FROM_IP = {"X-Forwarded-For": IP}


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "consent_log.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    monkeypatch.setattr(telegram, "notify_owner", lambda *a, **k: None)
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _log() -> list[tuple[str, str, str, str]]:
    with session_scope() as sess:
        rows = sess.execute(select(ConsentLog).order_by(ConsentLog.id)).scalars()
        return [(r.form, r.ref, r.version, r.ip) for r in rows]


def _form_html(page: str, marker: str) -> str:
    start = page.rindex("<form", 0, page.index(marker))
    return page[start:page.index("</form>", start)]


# === Текст согласия ===

def test_tekst_soglasiya_otkryvaetsya(client):
    r = client.get("/soglasie")
    assert r.status_code == 200
    assert "Согласие на обработку персональных данных" in r.text
    assert CONSENT_VERSION in r.text
    assert VALID_INN in r.text  # оператор назван с ИНН
    assert "Как отозвать согласие" in r.text


@pytest.mark.parametrize("url,marker", [
    ("/pricing", 'action="/buy/pro"'),
    ("/reestr-rkn", 'action="/reestr-rkn"'),
    ("/register", 'action="/register"'),
    ("/", 'id="lc-chat-form"'),
])
def test_forma_ssylaetsya_na_tekst_soglasiya(client, url, marker):
    assert 'href="/soglasie"' in _form_html(client.get(url).text, marker)


# === Журнал ===

def test_zakaz_pishet_soglasie_s_nomerom_zakaza(client, monkeypatch):
    monkeypatch.setattr(payments.tochka, "is_configured", lambda: True)
    monkeypatch.setattr(payments.tochka, "create_payment",
                        lambda **kw: PaymentLink(operation_id="op-1",
                                                 url=f"https://bank/pay/{kw['order_id']}"))
    r = client.post("/buy/pro", data={"email": "buyer@example.com", "pd_consent": "1"},
                    headers=FROM_IP)
    assert r.status_code == 303
    order_id = r.headers["location"].rsplit("/", 1)[-1]
    assert _log() == [("buy", order_id, CONSENT_VERSION, IP)]


def test_zakaz_bez_kassy_pishet_soglasie(client):
    r = client.post("/buy/pro", data={"email": "buyer@example.com", "pd_consent": "1"},
                    headers=FROM_IP)
    assert r.status_code == 200
    assert _log() == [("buy", "", CONSENT_VERSION, IP)]


def test_zakaz_bez_soglasiya_ne_pishetsya(client):
    assert client.post("/buy/pro", data={"email": "buyer@example.com"}).status_code == 422
    assert _log() == []


def test_zayavka_pishet_soglasie_s_nomerom_zayavki(client):
    r = client.post("/inquiry", data={"message": "Нужна проверка", "contact": "@maxim",
                                      "pd_consent": "1"}, headers=FROM_IP)
    assert r.status_code == 200
    inq_id = repo.list_inquiries()[0].id
    assert _log() == [("inquiry", str(inq_id), CONSENT_VERSION, IP)]


def test_proverka_inn_pishet_soglasie_bez_inn(client, monkeypatch):
    monkeypatch.setattr(rkn_web, "lookup_by_inn",
                        lambda inn: RknLookupResult(operator=None, not_found=True))
    r = client.post("/reestr-rkn", data={"inn": VALID_INN, "pd_consent": "1"},
                    headers=FROM_IP)
    assert r.status_code == 200
    assert _log() == [("rkn_check", "", CONSENT_VERSION, IP)]


def test_proverka_inn_bez_soglasiya_ne_pishetsya(client):
    client.post("/reestr-rkn", data={"inn": VALID_INN})
    assert _log() == []


def test_nevalidnyy_inn_ne_pishetsya(client, monkeypatch):
    """Обработки не было – и записи о согласии на неё быть не должно."""
    called = []
    monkeypatch.setattr(rkn_web, "lookup_by_inn", lambda inn: called.append(inn))
    r = client.post("/reestr-rkn", data={"inn": "abc", "pd_consent": "1"})
    assert r.status_code == 200
    assert called == [] and _log() == []


def test_proverka_inn_ogranichena_po_chastote(client, monkeypatch):
    monkeypatch.setattr(rkn_web, "lookup_by_inn",
                        lambda inn: RknLookupResult(operator=None, not_found=True))
    codes = [client.post("/reestr-rkn", data={"inn": VALID_INN, "pd_consent": "1"}).status_code
             for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    assert len(_log()) == 20


def test_registraciya_bez_soglasiya_otklonyaetsya(client):
    r = client.post("/register", data={"email": "new@x.ru", "password": "longenough1"})
    assert r.status_code == 422
    assert "Нужно согласие на обработку персональных данных" in r.text
    assert _log() == []
    # аккаунт не заведён: повторная регистрация с согласием проходит
    r = client.post("/register", data={"email": "new@x.ru", "password": "longenough1",
                                       "pd_consent": "1"})
    assert r.status_code == 303


def test_registraciya_pishet_soglasie(client):
    r = client.post("/register", data={"email": "new@x.ru", "password": "longenough1",
                                       "pd_consent": "1"}, headers=FROM_IP)
    assert r.status_code == 303
    [(form, ref, version, ip)] = _log()
    assert (form, version, ip) == ("register", CONSENT_VERSION, IP)
    assert ref.isdigit()


def test_chekboks_registracii_ne_predustanovlen(client):
    form = _form_html(client.get("/register").text, 'action="/register"')
    tag = form[form.index('name="pd_consent"') - 40:form.index('name="pd_consent"') + 40]
    assert "checked" not in tag
