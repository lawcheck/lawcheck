"""Ограничение частоты: формы, которые дорого дёргать.

Главное, что защищаем — регистрацию: она отправляет письмо на ЛЮБОЙ введённый
адрес, то есть без лимита это рассылка с нашего домена по чужим ящикам.
"""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.session import init_db
from lawcheck.web import ratelimit


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "rl.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    monkeypatch.setattr("lawcheck.web.auth.mailer.send_email", lambda *a, **k: True)
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


# === Счётчик ===

def test_schetchik_srabatyvaet_na_prevyshenii():
    for i in range(3):
        assert ratelimit.hit("b", "id", limit=3, window_sec=60) is False, f"запрос {i+1}"
    assert ratelimit.hit("b", "id", limit=3, window_sec=60) is True


def test_raznye_klyuchi_ne_meshayut_drug_drugu():
    assert ratelimit.hit("b", "ip-1", limit=1, window_sec=60) is False
    assert ratelimit.hit("b", "ip-2", limit=1, window_sec=60) is False
    assert ratelimit.hit("drugoy-bucket", "ip-1", limit=1, window_sec=60) is False
    assert ratelimit.hit("b", "ip-1", limit=1, window_sec=60) is True


def test_okno_istekaet(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])
    assert ratelimit.hit("b", "id", limit=1, window_sec=60) is False
    assert ratelimit.hit("b", "id", limit=1, window_sec=60) is True
    now[0] += 61
    assert ratelimit.hit("b", "id", limit=1, window_sec=60) is False


# === Путь через Redis (в проде считает он, а не память процесса) ===

class _FakePipeline:
    def __init__(self, store, fail=False):
        self._store, self._fail, self._ops = store, fail, []

    def incr(self, key):
        self._ops.append(("incr", key))

    def expire(self, key, ttl, nx=False):
        self._ops.append(("expire", key, ttl, nx))

    def execute(self):
        if self._fail:
            raise ConnectionError("redis лёг")
        key = self._ops[0][1]
        self._store[key] = self._store.get(key, 0) + 1
        return [self._store[key], True]


class _FakeConn:
    def __init__(self, fail=False):
        self.store, self.fail, self.last = {}, fail, None

    def pipeline(self):
        self.last = _FakePipeline(self.store, self.fail)
        return self.last


def test_schitaet_v_redis_kogda_on_est(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(ratelimit, "get_queue",
                        lambda: type("Q", (), {"connection": conn})())
    assert ratelimit.hit("b", "id", limit=2, window_sec=60) is False
    assert ratelimit.hit("b", "id", limit=2, window_sec=60) is False
    assert ratelimit.hit("b", "id", limit=2, window_sec=60) is True
    # TTL ставится только при создании ключа, иначе окно ездило бы вперёд
    # на каждом запросе и лимит никогда не отпускал бы.
    assert ("expire", "rl:b:id", 60, True) in conn.last._ops
    # В память при живом Redis не пишем.
    assert not ratelimit._local


def test_padenie_redis_ne_lomaet_zapros(monkeypatch):
    """Redis лёг — лимит должен деградировать до памяти, а не ронять форму."""
    conn = _FakeConn(fail=True)
    monkeypatch.setattr(ratelimit, "get_queue",
                        lambda: type("Q", (), {"connection": conn})())
    assert ratelimit.hit("b", "id", limit=1, window_sec=60) is False
    assert ratelimit.hit("b", "id", limit=1, window_sec=60) is True


# === IP за прокси ===

class _Req:
    def __init__(self, xff=None, peer="10.0.0.9"):
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = type("C", (), {"host": peer})()


def test_ip_beryotsya_posledniy_iz_xff():
    """Caddy дописывает реальный адрес пира в КОНЕЦ X-Forwarded-For.

    Первые элементы клиент может подделать, прислав свой заголовок, — если брать
    их, лимит обходится одной строкой в curl.
    """
    assert ratelimit.client_ip(_Req("203.0.113.9")) == "203.0.113.9"
    assert ratelimit.client_ip(_Req("1.2.3.4, 203.0.113.9")) == "203.0.113.9"
    assert ratelimit.client_ip(_Req(None, peer="198.51.100.7")) == "198.51.100.7"


def test_poddelannyy_xff_ne_daet_obyti_limit():
    real = _Req("evil-spoof, 203.0.113.9")
    same_real = _Req("drugoy-spoof, 203.0.113.9")
    assert ratelimit.client_ip(real) == ratelimit.client_ip(same_real)


# === Эндпоинты ===

def test_registraciya_ogranichena(client):
    codes = [client.post("/register",
                         data={"email": f"u{i}@x.ru", "password": "longenough1"}).status_code
             for i in range(7)]
    assert codes.count(429) >= 1, codes
    assert 429 in codes[5:], "первые пять должны пройти"


def test_vhod_ogranichen(client):
    codes = [client.post("/login", data={"email": "a@x.ru", "password": "wrong"}).status_code
             for _ in range(12)]
    assert codes[-1] == 429


def test_sbros_parolya_ogranichen(client):
    codes = [client.post("/forgot-password", data={"email": "a@x.ru"}).status_code
             for _ in range(5)]
    assert codes[-1] == 429


def test_skan_ogranichen_i_soobshchaet_ponyatno(client):
    last = None
    for _ in range(22):
        last = client.post("/scan", data={"url": "https://example.ru"})
    assert last.status_code == 429
    assert "слишком много проверок" in last.text.lower()


def test_chat_vidzhet_ogranichen(client):
    codes = [client.post("/inquiry", data={"message": "вопрос номер такой-то"}).status_code
             for _ in range(12)]
    assert codes[-1] == 429


def test_limit_ne_meshaet_normalnomu_polzovatelyu(client):
    """Живой человек регистрируется один раз и ошибается паролем пару раз."""
    assert client.post("/register",
                       data={"email": "normal@x.ru", "password": "longenough1"}).status_code == 303
    for _ in range(3):
        assert client.post("/login",
                           data={"email": "normal@x.ru", "password": "wrong"}).status_code == 401
    assert client.post("/login",
                       data={"email": "normal@x.ru", "password": "longenough1"}).status_code == 303
