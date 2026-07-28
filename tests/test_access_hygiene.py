"""Гигиена доступа: сессии, перечисление адресов, утечка магик-ссылок."""
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.models import Order
from lawcheck.db.session import init_db, session_scope
from lawcheck.web import security


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "hygiene.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    # metrika_id попадает в globals шаблонов при ИМПОРТЕ модуля, поэтому патчить
    # только settings недостаточно: на машине с METRIKA_ID в .env тест проходил,
    # а в CI с пустым ключом падал. Патчим и то, и другое.
    monkeypatch.setattr(settings, "metrika_id", "12345")
    from lawcheck.web import routes as web_routes
    monkeypatch.setitem(web_routes.templates.env.globals, "metrika_id", "12345")
    monkeypatch.setattr("lawcheck.web.auth.mailer.send_email", lambda *a, **k: True)
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


# === №7: смена пароля завершает чужие сессии ===

def test_smena_parolya_vygonyaet_starye_sessii(client):
    """Пароль меняют в том числе когда аккаунт увели. Если старые cookie
    продолжают пускать, смена пароля не решает исходную проблему."""
    client.post("/register", data={"email": "victim@x.ru", "password": "longenough1"})
    user = repo.get_user_by_email("victim@x.ru")
    assert client.get("/dashboard").status_code == 200  # сессия жива

    # Владелец сбросил пароль в другом браузере.
    token = repo.create_auth_token(user.id, "reset_password", 1)
    fresh = TestClient(client.app, follow_redirects=False)
    r = fresh.post("/reset-password", data={"token": token, "password": "brandnewpass1"})
    assert r.status_code == 200

    # Старая сессия (cookie у злоумышленника) больше не пускает.
    r = client.get("/dashboard")
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_obychnyy_vhod_posle_smeny_parolya_rabotaet(client):
    client.post("/register", data={"email": "u@x.ru", "password": "longenough1"})
    user = repo.get_user_by_email("u@x.ru")
    token = repo.create_auth_token(user.id, "reset_password", 1)
    client.post("/reset-password", data={"token": token, "password": "brandnewpass1"})

    r = client.post("/login", data={"email": "u@x.ru", "password": "brandnewpass1"})
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"
    assert client.get("/dashboard").status_code == 200


def test_sessiya_bez_epoch_ne_razloginivaetsya(client):
    """Сессии, выданные до появления поля, несут 0 и совпадают с дефолтом —
    выкат не должен разлогинить всех разом."""
    client.post("/register", data={"email": "old@x.ru", "password": "longenough1"})
    user = repo.get_user_by_email("old@x.ru")
    assert user.session_epoch == 0
    assert client.get("/dashboard").status_code == 200


# === №9: вход не выдаёт существование email ===

def test_vhod_ne_razlichaet_sushchestvuyushchiy_i_net(client):
    client.post("/register", data={"email": "real@x.ru", "password": "longenough1"})
    client.post("/logout")

    r_real = client.post("/login", data={"email": "real@x.ru", "password": "wrongpass1"})
    r_fake = client.post("/login", data={"email": "nope@x.ru", "password": "wrongpass1"})
    assert r_real.status_code == r_fake.status_code == 401
    # Текст ответа тоже одинаковый — иначе перечисление даже время мерить не надо.
    assert "Неверный email или пароль" in r_real.text
    assert "Неверный email или пароль" in r_fake.text


def test_holostoy_hesh_realno_schitaetsya():
    """Проверка не должна молча превратиться в no-op: тогда тайминг вернётся."""
    assert security.waste_time_like_verify("любой пароль") is None
    # Хеш заведомо не совпадает с реальным паролем.
    assert security.verify_password("любой пароль", security._DUMMY_HASH) is False


# === №8: магик-ссылка не утекает третьим сторонам ===

def _paid_order() -> str:
    oid = uuid.uuid4().hex
    with session_scope() as s:
        s.add(Order(id=oid, plan="pro", amount=990, status="paid"))
    return oid


def test_kabinet_ne_otdayot_url_metrike_i_poiskovikam(client):
    oid = _paid_order()
    r = client.get(f"/account/{oid}")
    assert r.status_code == 200
    # Метрика шлёт путь ТЕКУЩЕЙ страницы — Referrer-Policy её не остановит,
    # поэтому на страницах-пропусках её просто не должно быть.
    assert "mc.yandex.ru/metrika" not in r.text
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in r.headers["x-robots-tag"]


def test_na_obychnyh_stranicah_metrika_ostayotsya(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "mc.yandex.ru/metrika" in r.text


# === №10: сравнение секрета ===

def test_vnutrenniy_klyuch_sravnivaetsya_bezopasno(client, monkeypatch):
    monkeypatch.setattr(settings, "internal_key", "s3cret-key")
    assert client.post("/internal/monitoring/run",
                       headers={"X-Internal-Key": "s3cret-key"}).status_code == 200
    assert client.post("/internal/monitoring/run",
                       headers={"X-Internal-Key": "s3cret-ke"}).status_code == 403
    assert client.post("/internal/monitoring/run").status_code == 403


def test_pustoy_vnutrenniy_klyuch_zakryvaet_endpoint(client, monkeypatch):
    """Не настроен ключ — эндпоинт закрыт, а не открыт всем с пустым заголовком."""
    monkeypatch.setattr(settings, "internal_key", "")
    assert client.post("/internal/monitoring/run",
                       headers={"X-Internal-Key": ""}).status_code == 403
