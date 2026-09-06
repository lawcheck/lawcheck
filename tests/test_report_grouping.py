"""Свёрнутые разделы Политики и единый счёт исправлений.

Отчёт показывал восемь отсутствующих разделов Политики восемью карточками с
одинаковым замком, а /pricing считал исправления по-своему — обещание «открыть
18 исправлений» встречало числом 23 (вики lawcheck-otchet-put-do-oplaty).
"""
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db import repo
from lawcheck.db.models import Finding, Scan
from lawcheck.db.session import init_db, session_scope
from lawcheck.reporting import gating


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "grouping.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    # без http-базы cookie сессии ставится secure и TestClient её не сохраняет
    monkeypatch.setattr(settings, "site_base_url", "http://testserver")
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


SCAN_ID = "g" * 32


def _scan(sections: int = 8, others: int = 3):
    with session_scope() as s:
        s.add(Scan(id=SCAN_ID, url="https://mysite.ru", status="done", pages_crawled=3))
        for i in range(sections):
            s.add(Finding(scan_id=SCAN_ID, check_id=f"A3.sec{i}", severity="critical",
                          title=f"Обязательные разделы Политики обработки ПДн: Раздел {i}",
                          evidence="Раздел не найден", location="https://mysite.ru/policy",
                          law_reference="ст. 18.1 ч. 2 152-ФЗ",
                          recommendation=f"Добавьте в Политику раздел «Раздел {i}»."))
        for i in range(others):
            s.add(Finding(scan_id=SCAN_ID, check_id=f"B1.f{i}", severity="warning",
                          title=f"Форма {i}", evidence="", location="",
                          law_reference="ст. 9 152-ФЗ",
                          recommendation=f"Почините форму {i}."))


def test_policy_sections_collapse_into_one_card(client):
    _scan()
    r = client.get(f"/report/{SCAN_ID}")
    assert r.status_code == 200
    assert "В Политике не хватает обязательных разделов: 8 из 8" in r.text
    # диагноз не режем: разделы остаются внутри раскрывающегося списка
    assert "Каких разделов нет (8)" in r.text
    assert "Раздел 7" in r.text


def test_lock_price_promise_matches_pricing_page(client):
    _scan()
    report = client.get(f"/report/{SCAN_ID}")
    pricing = client.get(f"/pricing?scan={SCAN_ID}")
    # Замок отчёта и баннер тарифов обязаны называть одно число.
    expected = gating.locked_fix_count(_findings())
    assert f"Открыть {expected} исправлен" in report.text
    assert f"Под замком — {expected} исправлен" in pricing.text


def _findings():
    from lawcheck.db import repo
    return repo.get_scan(SCAN_ID).findings


def test_single_missing_section_is_not_collapsed(client):
    _scan(sections=1, others=3)
    r = client.get(f"/report/{SCAN_ID}")
    assert "не хватает обязательных разделов" not in r.text


@pytest.mark.parametrize("n,word", [(1, "исправление"), (2, "исправления"),
                                    (5, "исправлений"), (11, "исправлений"),
                                    (21, "исправление"), (23, "исправления")])
def test_plural_of_fixes(n, word):
    assert gating.plural(n, "исправление", "исправления", "исправлений") == word


def _paid_order_for_scan() -> str:
    oid = uuid.uuid4().hex
    repo.create_order(oid, "docs", 8000, "buyer@mysite.ru", SCAN_ID)
    repo.mark_order_paid(oid)
    return oid


def test_paid_report_opens_recipes_inside_grouped_card(client):
    """Оплата обязана открывать и свёрнутую карточку — она не находка, и её id
    сам в open_rec_ids не попадает."""
    _scan()
    oid = _paid_order_for_scan()
    # ссылка с заказом уводит на чистый URL и запоминает заказ в сессии
    assert client.get(f"/report/{SCAN_ID}?order={oid}").status_code == 303
    r = client.get(f"/report/{SCAN_ID}")
    assert r.status_code == 200
    assert "🔒 Как исправить" not in r.text
    # рецепты отдельных разделов доступны внутри раскрывающегося списка
    assert "Добавьте в Политику раздел «Раздел 3»." in r.text
