"""Источник визита сохраняется в заказе.

Раньше «откуда пришёл тот, кто заплатил» восстанавливалось только вручную —
грепом по access-логам Caddy (разбор продажи 12.08.2026 занял полчаса). Теперь
первое касание визита едет в сессии и записывается в заказ при создании.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.models import Order
from lawcheck.db.session import init_db, session_scope
from lawcheck.payments.tochka import PaymentLink


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "entrysrc.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    # Иначе cookie сессии уедет с флагом secure и TestClient (http) её не вернёт.
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()

    from lawcheck.api.main import create_app
    from lawcheck.web import payments

    def fake_create(*, amount_rub: int, purpose: str, order_id: str, email: str) -> PaymentLink:
        return PaymentLink(operation_id="op-1", url="https://bank/pay")

    monkeypatch.setattr(payments.tochka, "is_configured", lambda: True)
    monkeypatch.setattr(payments.tochka, "create_payment", fake_create)

    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _buy(client) -> Order:
    r = client.post("/buy/pro", data={"email": "buyer@example.com", "pd_consent": "on"})
    assert r.status_code == 303, r.text
    with session_scope() as s:
        order = s.query(Order).one()
        s.expunge(order)
    return order


def test_reklamnaya_metka_i_referer_edut_v_zakaz(client):
    client.get("/?utm_source=yandex&utm_medium=cpc&utm_campaign=search_152fz",
               headers={"referer": "https://yandex.ru/"})
    order = _buy(client)
    assert order.entry_ref == "https://yandex.ru/"
    assert order.entry_url == "/?utm_source=yandex&utm_medium=cpc&utm_campaign=search_152fz"


def test_perehod_bez_metki_tozhe_atribuciruetsya(client):
    """Инстаграм и телеграм меток не ставят: без реферера они неотличимы от
    прямого захода — ровно та дыра, из-за которой источник и спрашивали."""
    client.get("/", headers={"referer": "https://www.instagram.com/lawcheck.ru/"})
    assert _buy(client).entry_ref == "https://www.instagram.com/lawcheck.ru/"


def test_perehod_vnutri_sayta_istochnik_ne_trogaet(client):
    """Хождение по сайту новым источником не является."""
    client.get("/?yclid=ABC123", headers={"referer": "https://yandex.ru/"})
    client.get("/pricing", headers={"referer": "http://testserver/report/x"})
    order = _buy(client)
    assert order.entry_ref == "https://yandex.ru/"
    assert order.entry_url == "/?yclid=ABC123"


def test_novyy_zahod_so_storony_perezapisyvaet_istochnik(client):
    """Сессия живёт 30 дней и переживает визит. Вернулся по письму-напоминанию
    или по второму клику в рекламе — деньги принесло оно, а не органика
    месячной давности."""
    client.get("/", headers={"referer": "https://www.google.com/"})
    client.get("/pricing?utm_source=email&utm_campaign=order_reminder")
    order = _buy(client)
    assert order.entry_ref == ""
    assert order.entry_url == "/pricing?utm_source=email&utm_campaign=order_reminder"


def test_svoy_referer_istochnikom_ne_schitaetsya(client):
    """Вход на сайт с внутреннего адреса (перезагрузка, возврат из истории)
    источником не является — иначе своя же страница попадёт в атрибуцию."""
    client.get("/pricing", headers={"referer": "http://testserver/report/x"})
    order = _buy(client)
    assert order.entry_ref == ""
    assert order.entry_url == ""


def test_pryamoy_zahod_ne_zavodit_sessiyu(client):
    """Прямой заход в сессию не пишется вовсе: пустой источник и означает
    «прямой», а cookie каждому посетителю (и каждому боту) заводить не за чем."""
    client.get("/")
    assert "lc_session" not in client.cookies
    order = _buy(client)
    assert order.entry_ref == ""
    assert order.entry_url == ""


def test_magik_ssylka_v_kabinet_ne_stanovitsya_istochnikom(client):
    """`/account/{id}` — ссылка-пропуск в кабинет: в источнике другого заказа
    ей делать нечего."""
    client.get("/account/deadbeef", headers={"referer": "https://mail.yandex.ru/"})
    order = _buy(client)
    assert order.entry_ref == ""
    assert order.entry_url == ""


def test_vozvrat_s_kassy_ne_stanovitsya_istochnikom(client):
    """Сессия живёт 30 дней. Без этого следующий заказ того же человека уехал
    бы в отчёт с источником «касса банка»."""
    client.get("/", headers={"referer": "https://www.instagram.com/lawcheck.ru/"})
    client.get("/pay/success?order=deadbeef",
               headers={"referer": "https://securepayments.tochka.com/"})
    order = _buy(client)
    assert order.entry_ref == "https://www.instagram.com/lawcheck.ru/"


def test_alert_vladeltsu_nazyvaet_istochnik(client):
    """Поле, которое никто не читает, вопрос не закрывает: источник едет в том
    же телеграм-алерте, что и сама оплата."""
    from lawcheck.notify.telegram import paid_alert

    client.get("/?utm_source=yandex", headers={"referer": "https://yandex.ru/"})
    text = paid_alert(_buy(client))
    assert "Источник: https://yandex.ru/ → /?utm_source=yandex" in text


def test_alert_o_pryamom_zahode_govorit_pryamo(client):
    from lawcheck.notify.telegram import paid_alert

    client.get("/pricing")
    assert "Источник: прямой заход" in paid_alert(_buy(client))


def test_staticheskiy_zapros_ne_stanovitsya_tochkoy_vhoda(client):
    """Первым запросом в сессии может оказаться картинка или /api — точкой
    входа считается страница."""
    client.get("/static/css/site.css")
    client.get("/?utm_source=yandex", headers={"referer": "https://yandex.ru/"})
    assert _buy(client).entry_url == "/?utm_source=yandex"
