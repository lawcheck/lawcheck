"""Сторож за задачами по расписанию: молчание должно быть событием.

Три недели без бэкапов Postgres и три пропущенных прогона мониторинга прошли
незамеченными именно потому, что не запустившаяся задача исключения не
бросает. Здесь проверяется обратная логика: тревогу поднимает отсутствие
отметки, а не пойманная ошибка.
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from lawcheck.api.main import app
from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.db.models import utcnow
from lawcheck.notify import heartbeat, telegram

_KEY = {"X-Internal-Key": "s3cret-key"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "internal_key", "s3cret-key")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def alerts(monkeypatch):
    box: list[str] = []
    monkeypatch.setattr(telegram, "notify_owner", lambda text: box.append(text))
    return box


def _seen(name: str, ago: timedelta):
    """Проставить отметку задачи заданной давности."""
    repo.mark_job_ok(name)
    from lawcheck.db.models import JobRun
    from lawcheck.db.session import session_scope
    with session_scope() as sess:
        row = sess.get(JobRun, name)
        row.last_ok_at = utcnow() - ago
        row.last_alert_at = None


def test_ruchka_zakryta_bez_klyucha(client):
    assert client.post("/internal/heartbeat/backup").status_code == 403
    assert client.get("/internal/heartbeat").status_code == 403


def test_neizvestnaya_zadacha_otvergaetsya(client):
    """Опечатка в cron-скрипте иначе выглядела бы как исправная отметка."""
    r = client.post("/internal/heartbeat/bakcup", headers=_KEY)
    assert r.status_code == 404


def test_otmetka_zapisyvaetsya(client):
    r = client.post("/internal/heartbeat/backup", headers=_KEY)
    assert r.status_code == 200 and r.json()["job"] == "backup"
    assert "backup" in {row.name for row in repo.list_job_runs()}


def test_svezhaya_zadacha_ne_trevozhit(alerts):
    _seen("backup", timedelta(hours=2))
    assert heartbeat.check_and_alert() == []
    assert alerts == []


def test_prosrochennaya_zadacha_daet_alert(alerts):
    _seen("backup", timedelta(days=3))
    late = heartbeat.check_and_alert()
    assert "backup" in late
    assert len(alerts) == 1
    assert "backup" in alerts[0]
    assert "3 сут" in alerts[0]


def test_alert_ne_povtoryaetsya_kazhdyy_chas(alerts):
    """Пока задачу не починили, напоминаем раз в сутки, а не каждый прогон."""
    _seen("backup", timedelta(days=3))
    heartbeat.check_and_alert()
    heartbeat.check_and_alert()
    heartbeat.check_and_alert()
    assert len(alerts) == 1


def test_ozhivshaya_zadacha_snova_dast_alert_srazu(alerts):
    """После починки следующая просрочка не должна ждать суток тишины."""
    _seen("backup", timedelta(days=3))
    heartbeat.check_and_alert()
    assert len(alerts) == 1

    repo.mark_job_ok("backup")          # задача ожила
    _seen("backup", timedelta(days=3))  # и снова замолчала
    heartbeat.check_and_alert()
    assert len(alerts) == 2


def test_zadacha_bez_otmetok_ne_schitaetsya_prosrochennoy(alerts):
    """Свежевыкаченная схема не должна поднимать тревогу по всем задачам."""
    from lawcheck.db.models import JobRun
    from lawcheck.db.session import session_scope
    with session_scope() as sess:
        for row in sess.query(JobRun).all():
            sess.delete(row)

    assert heartbeat.overdue() == []
    assert alerts == []


def test_status_pokazyvaet_vse_zadachi(client):
    r = client.get("/internal/heartbeat", headers=_KEY)
    assert r.status_code == 200
    body = r.json()
    assert set(body["jobs"]) == set(heartbeat.JOBS)
    assert body["jobs"]["backup"]["limit_hours"] == 26
    # Контейнерные циклы сюда не попадают — см. комментарий у JOBS.
    assert "followups" not in body["jobs"]
