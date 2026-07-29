"""Заявки из чат-виджета: контакт обязателен, алерт владельцу кликабелен.

Заявок единицы, отвечает на них владелец руками — поэтому вся ценность роута
в том, что после заявки есть куда ответить и это делается в один тап.
"""
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.session import init_db
from lawcheck.notify import telegram


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "inq.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    # /inbox отдаёт 404 запросу с чужим Host — тесты ходят как testserver.
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


# === Роут ===

ZAYAVKA = {"message": "Нужна проверка магазина", "contact": "@maxim",
           "page": "/pricing", "pd_consent": "1"}


def test_zayavka_s_kontaktom_sohranyaetsya(client):
    r = client.post("/inquiry", data=ZAYAVKA)
    assert r.status_code == 200
    inq = repo.list_inquiries()[0]
    assert inq.contact == "@maxim" and inq.page == "/pricing"


def test_zayavka_bez_kontakta_otklonyaetsya(client):
    r = client.post("/inquiry", data={"message": "Нужна проверка магазина",
                                      "pd_consent": "1"})
    assert r.status_code == 422
    assert repo.list_inquiries() == []


def test_zayavka_bez_soglasiya_na_pdn_otklonyaetsya(client):
    """Контакт — персональные данные: без согласия его нельзя даже сохранить."""
    r = client.post("/inquiry", data={"message": "Нужна проверка", "contact": "@maxim"})
    assert r.status_code == 422
    assert repo.list_inquiries() == []


def test_honeypot_ne_trebuet_kontakta(client):
    """Бот заполнил скрытое поле — тихо отвечаем ok, не заводя заявку."""
    r = client.post("/inquiry", data={"message": "spam", "website": "http://spam"})
    assert r.status_code == 200
    assert repo.list_inquiries() == []


# === Согласие на рекламу (ст. 18 ФЗ «О рекламе») ===

def test_bez_galochki_reklamy_pisat_predlozheniya_nelzya(client):
    client.post("/inquiry", data=ZAYAVKA)
    assert repo.list_inquiries()[0].ad_consent is False
    assert repo.inquiries_with_ad_consent() == []


def test_s_galochkoy_zayavka_popadaet_v_rassylku(client):
    client.post("/inquiry", data=ZAYAVKA | {"ad_consent": "1"})
    inq = repo.list_inquiries()[0]
    assert inq.ad_consent is True
    assert inq.unsub_token  # ссылка отписки обязана быть в каждом письме
    assert [i.id for i in repo.inquiries_with_ad_consent()] == [inq.id]


def test_snyataya_galochka_ne_stanovitsya_soglasiem(client):
    """Снятый чекбокс браузер не шлёт вовсе, но поле-спутник или чужая
    интеграция могут прислать `0`/`false` — согласием это быть не должно."""
    for lozh in ("0", "false", "off", ""):
        client.post("/inquiry", data=ZAYAVKA | {"ad_consent": lozh})
    assert repo.inquiries_with_ad_consent() == []
    # То же для обязательного согласия на ПДн: «0» не пропускает заявку.
    assert client.post("/inquiry", data=ZAYAVKA | {"pd_consent": "0"}).status_code == 422


def test_otpiska_srabatyvaet_na_vseh_zapisyah_cheloveka(client):
    """Один и тот же телеграм записан по-разному — отписка одна на всех."""
    client.post("/inquiry", data=ZAYAVKA | {"contact": "@maxim", "ad_consent": "1"})
    client.post("/inquiry", data=ZAYAVKA | {"contact": "t.me/maxim", "ad_consent": "1"})
    client.post("/inquiry", data=ZAYAVKA | {"contact": "Ya@Mail.ru", "ad_consent": "1"})
    client.post("/inquiry", data=ZAYAVKA | {"contact": "ya@mail.ru", "ad_consent": "1"})
    assert len(repo.inquiries_with_ad_consent()) == 4
    token = [i for i in repo.list_inquiries() if i.contact == "@maxim"][0].unsub_token
    client.get(f"/unsubscribe/{token}")
    ostalis = {i.contact for i in repo.inquiries_with_ad_consent()}
    assert ostalis == {"Ya@Mail.ru", "ya@mail.ru"}  # телеграм отписан целиком
    token = [i for i in repo.list_inquiries() if i.contact == "ya@mail.ru"][0].unsub_token
    client.get(f"/unsubscribe/{token}")
    assert repo.inquiries_with_ad_consent() == []


def test_otpiska_ubiraet_iz_rassylki(client):
    client.post("/inquiry", data=ZAYAVKA | {"ad_consent": "1"})
    token = repo.list_inquiries()[0].unsub_token
    r = client.get(f"/unsubscribe/{token}")
    assert r.status_code == 200 and "отписаны" in r.text.lower()
    assert repo.inquiries_with_ad_consent() == []
    # Повторный переход по той же ссылке ничего не ломает.
    assert client.get(f"/unsubscribe/{token}").status_code == 200


def test_chuzhoy_token_otpiski_ne_srabatyvaet(client):
    client.post("/inquiry", data=ZAYAVKA | {"ad_consent": "1"})
    r = client.get("/unsubscribe/vydumannyy-token")
    assert "недействительна" in r.text.lower()
    assert len(repo.inquiries_with_ad_consent()) == 1


# === Ссылка на ответ в алерте ===

def test_kontakt_ssylkoy_po_vidu_stroki():
    assert telegram.contact_link("@maxim_p") == '<a href="https://t.me/maxim_p">@maxim_p</a>'
    assert telegram.contact_link("t.me/maxim_p") == '<a href="https://t.me/maxim_p">t.me/maxim_p</a>'
    assert telegram.contact_link("ya@mail.ru") == '<a href="mailto:ya@mail.ru">ya@mail.ru</a>'
    assert telegram.contact_link("+7 (999) 123-45-67") == (
        '<a href="tel:+79991234567">+7 (999) 123-45-67</a>')


def test_neponyatnyy_kontakt_ostayotsya_tekstom():
    assert telegram.contact_link("звоните после обеда") == "звоните после обеда"
    assert telegram.contact_link("") == "не оставлен"


def test_ampersand_v_adrese_ne_lomaet_razmetku():
    """`&` разрешён в локальной части адреса, а Telegram в HTML-режиме требует
    его экранировать — иначе 400 и потерянное уведомление о заявке."""
    link = telegram.contact_link("a&b@x.ru")
    assert link == '<a href="mailto:a&amp;b@x.ru">a&amp;b@x.ru</a>'


def test_chuzhaya_stroka_ne_popadaet_v_href():
    """Сообщение уходит с parse_mode=HTML: кавычка в href — сломанная разметка
    и 400 от Telegram, то есть заявка, о которой владелец не узнает."""
    zlodey = telegram.contact_link('"><script>alert(1)</script>')
    assert "<script>" not in zlodey and "href" not in zlodey
    assert telegram.contact_link('a"@x.ru') == 'a&quot;@x.ru'


def test_alert_vladeltsu_soderzhit_ssylku(client):
    with mock.patch.object(telegram, "notify_owner") as notify:
        client.post("/inquiry", data=ZAYAVKA | {"message": "Интересует Business",
                                                "contact": "ya@mail.ru"})
    text = notify.call_args.args[0]
    assert '<a href="mailto:ya@mail.ru">' in text
    assert "Интересует Business" in text
    assert "предложения" not in text  # галочки рекламы не было


def test_inbox_pokazyvaet_komu_mozhno_pisat(client):
    """Владельцу нужно видеть галочку до того, как он напишет предложение."""
    client.post("/inquiry", data=ZAYAVKA | {"contact": "ya@mail.ru", "ad_consent": "1"})
    client.post("/inquiry", data=ZAYAVKA | {"contact": "@tihiy"})
    page = client.get("/inbox").text
    assert 'href="mailto:ya@mail.ru"' in page  # ответить в один клик
    assert 'href="https://t.me/tihiy"' in page
    assert "можно писать предложения" in page
    assert "только ответ на вопрос" in page


def test_alert_pomechaet_soglasie_na_rassylku(client):
    with mock.patch.object(telegram, "notify_owner") as notify:
        client.post("/inquiry", data=ZAYAVKA | {"ad_consent": "1"})
    assert "можно писать предложения" in notify.call_args.args[0]
