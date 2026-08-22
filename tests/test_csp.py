"""Content-Security-Policy: заголовок и nonce на каждом инлайн-скрипте.

Главное здесь — не заголовок, а сторож: под CSP с nonce скрипт без nonce
просто не выполнится, и сломается это молча. Отдельно важен JSON-LD: браузер
блокирует и его, а значит разметка пропадёт из выдачи.
"""
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.session import init_db

# <script …> без атрибутов src (внешние скрипты nonce не требуют).
_INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\ssrc=)[^>]*>", re.I)


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "csp.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    # С включённой Метрикой рендерится самый большой инлайн-скрипт.
    monkeypatch.setattr(settings, "metrika_id", "12345")
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _pages(client) -> list[tuple[str, str]]:
    repo.create_scan("scan1", "https://example.com/", 10)
    repo.mark_done("scan1", pages_crawled=1, findings=[])
    paths = ["/", "/pricing", "/privacy", "/oferta", "/login", "/register",
             "/uvedomlenie-rkn", "/reestr-rkn", "/report/scan1"]
    return [(p, client.get(p).text) for p in paths]


def test_zagolovok_stoit_i_nesyot_nonce(client):
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert "'nonce-" in csp
    assert "object-src 'none'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


def test_form_action_puskaet_na_kassu_banka(client):
    """form-action действует на всю цепочку редиректов после сабмита формы.
    Без кассы в списке браузер молча гасит переход на оплату по 303 с
    /buy/pro, и кнопка «Перейти к оплате» перестаёт работать — сервер при
    этом отвечает 303, в логах всё выглядит исправным."""
    csp = client.get("/pricing").headers["content-security-policy"]
    form_action = [d for d in csp.split(";") if d.strip().startswith("form-action")][0]
    assert "https://merch.tochka.com" in form_action


def test_metrika_puskaetsya_na_oba_svoih_domena(client):
    """Загрузчик приходит с mc.yandex.ru, а хиты счётчик шлёт куда сам решит —
    туда же или на mc.yandex.com, судя по региону посетителя. Пока в списке был
    один .ru, у получивших .com CSP резал watch, sync_cookie_image_check и
    callback-скрипт: такой визит не доходил до Метрики вообще, его не было
    даже среди «прямых заходов» (проверено в браузере 10.08.2026)."""
    csp = client.get("/").headers["content-security-policy"]
    for name in ("script-src", "img-src", "connect-src"):
        directive = [d for d in csp.split(";") if d.strip().startswith(name)][0]
        assert "https://mc.yandex.ru" in directive, name
        assert "https://mc.yandex.com" in directive, name


def test_connect_src_puskaet_vebsoket_schyotchika(client):
    """Счётчик открывает ещё и `wss://mc.yandex.com/solid.ws`. Источник для
    WebSocket сравнивается вместе со схемой, поэтому `https://mc.yandex.com`
    его не покрывает — без `wss://` браузер рвал соединение."""
    csp = client.get("/").headers["content-security-policy"]
    connect = [d for d in csp.split(";") if d.strip().startswith("connect-src")][0]
    assert "wss://mc.yandex.ru" in connect
    assert "wss://mc.yandex.com" in connect


def test_nonce_raznyy_na_kazhdyy_zapros(client):
    first = client.get("/").headers["content-security-policy"]
    second = client.get("/").headers["content-security-policy"]
    assert first != second


def test_u_vseh_inlayn_skriptov_est_nonce(client):
    for path, html in _pages(client):
        nonce = re.search(r"'nonce-([\w-]+)'",
                          client.get(path).headers["content-security-policy"])
        for tag in _INLINE_SCRIPT.findall(html):
            assert "nonce=" in tag, f"{path}: скрипт без nonce → {tag[:80]}"
        assert nonce is not None


def test_vse_shablony_na_diske_s_nonce():
    """Статический сторож по всем шаблонам, а не только публичным URL.

    Живой тест выше ходит по девяти адресам и не видит страницы за сессией
    (dashboard, кабинет заказа, сброс пароля) — новый шаблон с инлайн-скриптом
    без nonce уехал бы на прод незамеченным. Здесь проверяется каждый *.html
    в каталоге шаблонов, рендерится он или нет.
    """
    import lawcheck
    templates_dir = Path(lawcheck.__file__).parent / "web" / "templates"
    offenders = []
    for tpl in sorted(templates_dir.glob("*.html")):
        for match in _INLINE_SCRIPT.finditer(tpl.read_text(encoding="utf-8")):
            if "nonce=" not in match.group(0):
                offenders.append(f"{tpl.name}: {match.group(0)[:80]}")
    assert not offenders, "скрипты без nonce:\n" + "\n".join(offenders)


def test_json_ld_tozhe_s_nonce(client):
    html = client.get("/pricing").text
    for tag in re.findall(r'<script[^>]*application/ld\+json[^>]*>', html, re.I):
        assert "nonce=" in tag
