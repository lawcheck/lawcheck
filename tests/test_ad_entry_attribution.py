"""Рекламная метка переживает cookie-баннер.

Метрика на сайте включается только после «Принять», поэтому посетитель,
ушедший вглубь раньше, даёт счётчику стартовать уже без yclid — и рекламный
визит попадает в Метрику как прямой заход (замер 2026-08-10: у рекламы
10 сканов из 36 при 38 юзерах, у «прямых» 18 при 20). Сервер запоминает адрес
входа в сессии, шаблон отдаёт его скрипту, тот досылает хит.
"""
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.session import init_db

# var AD_ENTRY = "…"; — то, что реально уедет в браузер.
_AD_ENTRY = re.compile(r'var AD_ENTRY = (".*?"|"");')


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "adentry.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    monkeypatch.setattr(settings, "metrika_id", "12345")
    # Иначе cookie сессии уедет с флагом secure и TestClient (http) её не вернёт.
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()
    from lawcheck.api.main import create_app
    from lawcheck.web import routes
    # Глобалы шаблонов читают settings при импорте модуля — в тестовом прогоне
    # он мог быть импортирован раньше, с пустым metrika_id.
    routes.templates.env.globals["metrika_id"] = settings.metrika_id
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _ad_entry(resp) -> str:
    m = _AD_ENTRY.search(resp.text)
    assert m, "в шаблоне нет var AD_ENTRY — скрипту нечем восстановить метку"
    return m.group(1).strip('"')


def test_entry_without_label_leaves_nothing_to_restore(client):
    assert _ad_entry(client.get("/")) == ""


def test_yclid_survives_navigation_to_unlabelled_page(client):
    client.get("/?yclid=TESTATTR12345")
    # Посетитель ушёл вглубь до того, как нажал «Принять».
    assert "yclid=TESTATTR12345" in _ad_entry(client.get("/pricing"))


def test_entry_hranitsya_bez_shemy_i_hosta(client):
    """За Caddy `request.url` отдаёт внутренний `http`, и сохранённый целиком
    адрес уехал бы в Метрику как `http://lawchek.ru/…` — та же страница
    отдельной строкой в отчётах. Схему подставляет браузер, сервер хранит путь.
    """
    client.get("/?yclid=TESTATTR12345")
    entry = _ad_entry(client.get("/pricing"))
    assert entry.startswith("/?yclid=")
    assert "://" not in entry


@pytest.mark.parametrize("label", ["utm_source=yandex", "gclid=abc", "_openstat=x", "ymclid=y"])
def test_other_ad_labels_are_remembered_too(client, label):
    client.get(f"/?{label}")
    assert label in _ad_entry(client.get("/pricing"))


def test_entry_expires_with_the_visit(client, monkeypatch):
    client.get("/?yclid=TESTATTR12345")
    from lawcheck.web import deps
    # Визит Метрики — 30 минут без активности; после него метка чужая. Двигаем
    # границу, а не системные часы: `deps.time` — сам модуль, его патч глобален.
    monkeypatch.setattr(deps, "_AD_ENTRY_TTL", -1)
    assert _ad_entry(client.get("/pricing")) == ""


def test_hit_is_resent_only_right_after_consent(client):
    """Сразу после «Принять» — досылаем; тому, кто согласился раньше, — нет.

    Иначе счётчик, работающий с первой страницы, получил бы второй хит на тот
    же визит, и вместо потерянной атрибуции мы получили бы задвоенную.
    """
    body = client.get("/").text
    assert 'ym(MID, "hit", entryUrl)' in body
    # Схему и хост подставляет браузер: сервер за прокси знает только http.
    assert "location.origin + AD_ENTRY" in body
    assert "loadMetrika(true)" in body  # ветка «только что согласился»
    assert re.search(r"if \(choice === \"all\"\) \{ loadMetrika\(\); return; \}", body)
