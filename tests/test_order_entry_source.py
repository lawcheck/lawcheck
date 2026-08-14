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


def test_pervoe_kasanie_ne_perezapisyvaetsya(client):
    """Вопрос «откуда пришёл» — про начало пути, а не про последний переход
    внутри сайта."""
    client.get("/?yclid=ABC123", headers={"referer": "https://yandex.ru/"})
    client.get("/pricing", headers={"referer": "https://lawchek.ru/report/x"})
    order = _buy(client)
    assert order.entry_ref == "https://yandex.ru/"
    assert order.entry_url == "/?yclid=ABC123"


def test_svoy_referer_istochnikom_ne_schitaetsya(client):
    """Вход на сайт с внутреннего адреса (перезагрузка, возврат из истории)
    источником не является — иначе своя же страница попадёт в атрибуцию."""
    client.get("/pricing", headers={"referer": "http://testserver/report/x"})
    order = _buy(client)
    assert order.entry_ref == ""
    assert order.entry_url == "/pricing"


def test_pryamoy_zahod_ostavlyaet_pusto(client):
    client.get("/")
    order = _buy(client)
    assert order.entry_ref == ""
    assert order.entry_url == "/"


def test_alert_vladeltsu_nazyvaet_istochnik(client):
    """Поле, которое никто не читает, вопрос не закрывает: источник едет в том
    же телеграм-алерте, что и сама оплата."""
    from lawcheck.web.payments import _paid_alert

    client.get("/?utm_source=yandex", headers={"referer": "https://yandex.ru/"})
    text = _paid_alert(_buy(client))
    assert "Источник: https://yandex.ru/ → /?utm_source=yandex" in text


def test_alert_o_pryamom_zahode_govorit_pryamo(client):
    from lawcheck.web.payments import _paid_alert

    client.get("/pricing")
    assert "Источник: прямой заход → /pricing" in _paid_alert(_buy(client))


def test_staticheskiy_zapros_ne_stanovitsya_tochkoy_vhoda(client):
    """Первым запросом в сессии может оказаться картинка или /api — точкой
    входа считается страница."""
    client.get("/static/css/site.css")
    client.get("/?utm_source=yandex", headers={"referer": "https://yandex.ru/"})
    assert _buy(client).entry_url == "/?utm_source=yandex"
