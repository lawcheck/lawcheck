"""Цифра «N проверок» на сайте считается из реестра, а не вписывается руками.

Хардкод «30 проверок» разъехался с движком: на 10.09.2026 пунктов было 33,
а число стояло в семи местах, включая JSON-LD. Тест держит два инварианта —
счётчик равен сумме по реестру, и в шаблонах нет вписанного руками числа.
"""
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lawcheck.checks.registry import CHECKS, checkpoints
from lawcheck.config import settings
from lawcheck.db import session
from lawcheck.db.session import init_db
from lawcheck.reporting.gating import plural

_TEMPLATES = Path(__file__).parent.parent / "lawcheck" / "web" / "templates"
_HARDCODED_RE = re.compile(r"\d+\s+проверк\w*")


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp()) / "checkpoints.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "session_secret", "test-secret-please-ignore")
    init_db()
    from lawcheck.api.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def test_checkpoints_equals_registry_sum():
    assert checkpoints() == sum(c.points() for c in CHECKS)


def test_expanding_checks_counted_by_subpoints():
    """A3, E1 и G2 дают больше одного пункта — иначе цифра занижена."""
    points = {c.id: c.points() for c in CHECKS}
    assert points["A3"] > 1 and points["E1"] == 3 and points["G2"] > 1


def test_landing_shows_counted_number(client):
    body = client.get("/").text
    # Форму слова берём тем же склонением, что и шаблон: жёсткое «проверки»
    # ломало бы тест на 31 пункте («проверка») и на 35 («проверок»).
    word = plural(checkpoints(), "проверка", "проверки", "проверок")
    assert f"{checkpoints()} {word}" in body


def test_no_hardcoded_number_in_templates():
    offenders = [p.name for p in _TEMPLATES.glob("*.html")
                 if _HARDCODED_RE.search(p.read_text())]
    assert offenders == []
