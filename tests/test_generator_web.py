"""Бесплатный генератор Политики ПДн /politika-obrabotki-pd (web/generator.py)."""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.models import ConsentLog, Lead
from lawcheck.db.session import init_db, session_scope
from lawcheck.external.egrul import EgrulLookupResult, EgrulRecord
from lawcheck.web import generator

VALID_INN = "771481979800"  # проходит контрольную сумму (12 цифр, ИП)


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "gen.db"
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


@pytest.fixture()
def sent(monkeypatch):
    """Перехват письма и алерта владельцу; ЕГРЮЛ отвечает записью ИП."""
    mails, alerts = [], []
    monkeypatch.setattr("lawcheck.notify.mailer.send_email",
                        lambda to, subject, body, *a, **kw: mails.append((to, subject, body)))
    monkeypatch.setattr("lawcheck.notify.telegram.notify_owner", alerts.append)
    record = EgrulRecord(inn=VALID_INN, ogrn="322774600250213",
                         short_name="ИП Фисташкин Ф. Ф.", full_name="", kind="fl")
    monkeypatch.setattr(generator.egrul, "lookup_by_inn",
                        lambda inn: EgrulLookupResult(record=record))
    return mails, alerts


def _form(**over):
    data = {"domain": "https://www.Fistashki.org/about", "email": "Owner@Fistashki.org",
            "inn": VALID_INN, "categories": ["email", "phone"],
            "services": ["Яндекс.Метрика", "Google Analytics 4", "Несуществующий"],
            "purposes": "обработка заявок", "pd_consent": "1"}
    data.update(over)
    return data


def test_page_renders_form_with_services(client):
    r = client.get(generator.PATH)
    assert r.status_code == 200
    assert "образец под ваши данные" in r.text
    assert "Google Analytics 4" in r.text and "зарубежный" in r.text
    assert 'data-goal="generator_submit"' in r.text


def test_page_in_sitemap(client):
    assert generator.PATH in client.get("/sitemap.xml").text


def test_generates_policy_sends_copy_and_saves_lead(client, sent):
    mails, alerts = sent
    r = client.post(generator.PATH, data=_form())
    assert r.status_code == 200
    # реквизиты из ЕГРЮЛ, домен без схемы и www, данные владельца
    assert "ИП Фисташкин Ф. Ф." in r.text and "322774600250213" in r.text
    assert "https://fistashki.org" in r.text
    assert "обработка заявок" in r.text
    # зарубежный сервис → трансграничная передача, неизвестный сервис отброшен
    assert "трансграничной" in r.text and "Несуществующий" not in r.text
    # мост в скан с подставленным доменом
    assert 'action="/scan"' in r.text and 'value="fistashki.org"' in r.text
    # бланки без отсылок к отчёту, которого у человека нет
    assert "сверьтесь с отчётом" not in r.text

    assert mails and mails[0][0] == "owner@fistashki.org"
    assert "fistashki.org" in mails[0][1] and 'class="blank"' not in mails[0][2]
    assert alerts and "owner@" not in alerts[0]  # почта в Telegram не уходит

    with session_scope() as s:
        leads = s.execute(select(Lead)).scalars().all()
        consents = s.execute(select(ConsentLog)).scalars().all()
    assert [(lead.scan_id, lead.email) for lead in leads] == [("generator:politika", "owner@fistashki.org")]
    assert [c.form for c in consents] == ["policy_generator"]


def test_repeat_submit_does_not_duplicate_lead_or_alert(client, sent):
    _, alerts = sent
    client.post(generator.PATH, data=_form())
    client.post(generator.PATH, data=_form())
    with session_scope() as s:
        assert len(s.execute(select(Lead)).scalars().all()) == 1
    assert len(alerts) == 1


@pytest.mark.parametrize("over, error", [
    ({"email": "не почта"}, "Укажите почту"),
    ({"domain": "fistashki"}, "Укажите адрес сайта"),
    ({"inn": "12345"}, "ИНН не проходит проверку"),
    ({"pd_consent": ""}, "Отметьте согласие"),
])
def test_invalid_form_shows_error_and_keeps_input(client, sent, over, error):
    mails, _ = sent
    r = client.post(generator.PATH, data=_form(**over))
    assert r.status_code == 200
    assert error in r.text
    assert "обработка заявок" in r.text  # введённое не потерялось
    assert not mails
    with session_scope() as s:
        assert not s.execute(select(Lead)).scalars().all()
        assert not s.execute(select(ConsentLog)).scalars().all()


def test_egrul_failure_falls_back_to_typed_name(client, sent, monkeypatch):
    monkeypatch.setattr(generator.egrul, "lookup_by_inn",
                        lambda inn: EgrulLookupResult(record=None, error="captcha"))
    r = client.post(generator.PATH, data=_form(name="ООО «Ромашка»"))
    assert "ООО «Ромашка»" in r.text
    assert "[ЗАПОЛНИТЕ: ОГРН/ОГРНИП]" in r.text


@pytest.mark.parametrize("raw, domain", [
    ("https://www.Shop.ru/about?x=1", "shop.ru"),
    ("магазин.рф", "магазин.рф"),
    ("shop", ""),
    ("javascript:alert(1)", ""),
])
def test_normalize_domain(raw, domain):
    assert generator.normalize_domain(raw) == domain
