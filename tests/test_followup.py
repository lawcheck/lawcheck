"""Письмо-догонялка: отбор лидов, сборка письма, отписка."""
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.models import Finding, Lead, Scan, utcnow
from lawcheck.db.session import init_db, session_scope
from lawcheck.reporting import followup


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    # Файловая БД (не :memory:) — чтобы таблицы были видны и в threadpool,
    # где FastAPI выполняет sync-роуты через to_thread (см. test_report_unlock).
    tmp = Path(tempfile.mkdtemp()) / "followup.db"
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp}")
    monkeypatch.setattr(settings, "site_base_url", "https://lawchek.ru")
    monkeypatch.setattr(settings, "session_secret", "test-secret")
    init_db()
    yield
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _add_scan(sid: str, *, problems: bool = True, status: str = "done",
              url: str = "https://mysite.ru") -> None:
    with session_scope() as s:
        s.add(Scan(id=sid, url=url, status=status, pages_crawled=2))
        if problems:
            for cid, sev, title, rec in [
                ("B2", "critical", "Нет согласия у формы", "Добавьте чекбокс"),
                ("A1", "critical", "Нет политики ПДн", "Разместите политику"),
                ("G1", "warning", "Реклама без пометки", "Добавьте пометку"),
                ("D1", "warning", "Cookie без баннера", "Добавьте баннер"),
            ]:
                s.add(Finding(scan_id=sid, check_id=cid, severity=sev, title=title,
                              evidence="e", location=f"{url}/p",
                              law_reference="ст. 18.1 152-ФЗ", recommendation=rec))
        else:
            s.add(Finding(scan_id=sid, check_id="A1", severity="ok",
                          title="Политика есть", evidence="e"))


def _add_lead(email: str, sid: str, *, days_ago: float = 3, mailed: bool = False,
              unsub: bool = False, token: str = "", url: str = "https://mysite.ru") -> int:
    with session_scope() as s:
        lead = Lead(scan_id=sid, url=url, email=email,
                    unsub_token=token or f"tok-{email}",
                    created_at=utcnow() - timedelta(days=days_ago))
        if mailed:
            lead.mailed_at = utcnow()
        if unsub:
            lead.unsubscribed_at = utcnow()
        s.add(lead)
        s.flush()
        return lead.id


# --- отбор leads_to_followup ---

def test_eligible_lead_selected():
    _add_scan("s1")
    _add_lead("a@x.ru", "s1")
    got = repo.leads_to_followup()
    assert [ld.email for ld in got] == ["a@x.ru"]


def test_too_recent_lead_skipped():
    _add_scan("s1")
    _add_lead("a@x.ru", "s1", days_ago=0.1)  # моложе delay_hours=24
    assert repo.leads_to_followup() == []


def test_too_old_lead_skipped():
    _add_scan("s1")
    _add_lead("a@x.ru", "s1", days_ago=30)  # старше max_age_days=14
    assert repo.leads_to_followup() == []


def test_already_mailed_skipped():
    _add_scan("s1")
    _add_lead("a@x.ru", "s1", mailed=True)
    assert repo.leads_to_followup() == []


def test_unsubscribed_skipped():
    _add_scan("s1")
    _add_lead("a@x.ru", "s1", unsub=True)
    assert repo.leads_to_followup() == []


def test_scan_without_problems_skipped():
    _add_scan("s1", problems=False)
    _add_lead("a@x.ru", "s1")
    assert repo.leads_to_followup() == []


def test_unfinished_scan_skipped():
    _add_scan("s1", status="running")
    _add_lead("a@x.ru", "s1")
    assert repo.leads_to_followup() == []


def test_paid_order_by_scan_excludes():
    _add_scan("s1")
    _add_lead("a@x.ru", "s1")
    oid = "o1"
    repo.create_order(oid, "pro", 990, "buyer@x.ru", "s1")  # оплата с этого скана
    repo.mark_order_paid(oid)
    assert repo.leads_to_followup() == []


def test_paid_order_by_email_excludes():
    _add_scan("s1")
    _add_lead("payer@x.ru", "s1")
    oid = "o1"
    repo.create_order(oid, "pro", 990, "payer@x.ru", "")  # тот же email уже платил
    repo.mark_order_paid(oid)
    assert repo.leads_to_followup() == []


# --- сборка письма ---

def test_build_context_fields():
    _add_scan("s1", url="https://www.shop.ru")
    lid = _add_lead("a@x.ru", "s1", url="https://www.shop.ru")
    lead = repo.leads_to_followup()[0]
    scan = repo.get_scan("s1")
    ctx = followup.build_context(lead, scan)
    assert ctx["site"] == "shop.ru"           # www. срезан
    assert ctx["problems"] == 4
    assert ctx["critical"] == 2
    assert len(ctx["top3"]) == 3
    assert ctx["top3"][0] in ("Нет согласия у формы", "Нет политики ПДн")  # critical сверху
    assert ctx["locked"] == 2                 # 4 рецепта − 2 бесплатных
    assert "152-ФЗ" in ctx["laws"]
    assert ctx["unsub_url"].endswith("/unsubscribe/tok-a@x.ru")  # отписка без UTM
    assert lid  # sanity


def test_report_url_has_utm():
    _add_scan("s1")
    _add_lead("a@x.ru", "s1")
    lead = repo.leads_to_followup()[0]
    ctx = followup.build_context(lead, repo.get_scan("s1"))
    assert "utm_source=email" in ctx["report_url"]
    assert "utm_medium=email" in ctx["report_url"]
    assert "utm_campaign=followup" in ctx["report_url"]
    assert "utm_" not in ctx["unsub_url"]  # служебная ссылка — без меток


def test_with_utm_respects_existing_query():
    assert followup._with_utm("https://x.ru/r/1").startswith("https://x.ru/r/1?utm_")
    assert "?a=1&utm_source=email" in followup._with_utm("https://x.ru/r/1?a=1")


@pytest.mark.parametrize("n,word", [
    (1, "находка"), (2, "находки"), (4, "находки"), (5, "находок"),
    (11, "находок"), (21, "находка"), (22, "находки"),
])
def test_plural(n, word):
    assert followup._plural(n, "находка", "находки", "находок") == word


def test_render_contains_key_parts():
    _add_scan("s1")
    _add_lead("a@x.ru", "s1")
    lead = repo.leads_to_followup()[0]
    subject, html, text = followup.render(followup.build_context(lead, repo.get_scan("s1")))
    assert "mysite.ru" in subject
    assert "Отписаться" in html and "unsubscribe/tok-a@x.ru" in html
    assert "Отписаться" in text
    assert "990" in html  # апселл Pro присутствует


# --- отправка отмечает mailed_at, dry-run — нет ---

def test_send_marks_mailed(monkeypatch):
    sent = {}
    monkeypatch.setattr(followup.mailer, "send_email",
                        lambda to, subj, html, text=None: sent.update(to=to) or True)
    _add_scan("s1")
    _add_lead("a@x.ru", "s1")
    summary = followup.run()
    assert summary["sent"] == 1 and sent["to"] == "a@x.ru"
    assert repo.leads_to_followup() == []  # больше не кандидат


def test_dry_run_does_not_send_or_mark(monkeypatch):
    calls = []
    monkeypatch.setattr(followup.mailer, "send_email",
                        lambda *a, **k: calls.append(a) or True)
    _add_scan("s1")
    _add_lead("a@x.ru", "s1")
    summary = followup.run(dry_run=True)
    assert summary["sent"] == 0 and calls == []
    assert len(repo.leads_to_followup()) == 1  # остался кандидатом


def test_failed_send_keeps_candidate(monkeypatch):
    monkeypatch.setattr(followup.mailer, "send_email", lambda *a, **k: False)
    _add_scan("s1")
    _add_lead("a@x.ru", "s1")
    summary = followup.run()
    assert summary["sent"] == 0 and summary["skipped"] == 1
    assert len(repo.leads_to_followup()) == 1  # не отмечен — попробуем ещё раз


# --- отписка ---

def test_unsubscribe_all_leads_with_email():
    _add_scan("s1")
    _add_scan("s2")
    _add_lead("a@x.ru", "s1", token="tokA")
    _add_lead("a@x.ru", "s2", token="tokB")  # тот же email, другой скан
    email = repo.unsubscribe_lead("tokA")
    assert email == "a@x.ru"
    # оба лида этого email отписаны
    assert repo.leads_to_followup() == []


def test_unsubscribe_unknown_token():
    assert repo.unsubscribe_lead("nope") is None
    assert repo.unsubscribe_lead("") is None


def test_cron_endpoint_requires_key(monkeypatch):
    from fastapi.testclient import TestClient

    from lawcheck.api.main import create_app
    monkeypatch.setattr(settings, "internal_key", "sekret")
    monkeypatch.setattr(followup.mailer, "send_email", lambda *a, **k: True)
    _add_scan("s1")
    _add_lead("a@x.ru", "s1")
    with TestClient(create_app()) as c:
        assert c.post("/internal/followups/run").status_code == 403  # без ключа
        assert c.post("/internal/followups/run",
                      headers={"X-Internal-Key": "wrong"}).status_code == 403
        r = c.post("/internal/followups/run", headers={"X-Internal-Key": "sekret"})
        assert r.status_code == 200 and r.json()["sent"] == 1
    assert repo.leads_to_followup() == []


def test_cron_endpoint_blocked_when_key_unset(monkeypatch):
    """Пустой internal_key не должен открывать эндпойнт всем подряд."""
    from fastapi.testclient import TestClient

    from lawcheck.api.main import create_app
    monkeypatch.setattr(settings, "internal_key", "")
    with TestClient(create_app()) as c:
        assert c.post("/internal/followups/run",
                      headers={"X-Internal-Key": ""}).status_code == 403


def test_unsubscribe_route():
    from fastapi.testclient import TestClient

    from lawcheck.api.main import create_app
    _add_scan("s1")
    _add_lead("a@x.ru", "s1", token="rtok")
    with TestClient(create_app()) as c:
        r = c.get("/unsubscribe/rtok")
        assert r.status_code == 200 and "Вы отписаны" in r.text
        # повторно и с мусорным токеном — не падает
        assert c.get("/unsubscribe/rtok").status_code == 200
        assert "недействительна" in c.get("/unsubscribe/nope").text
    assert repo.leads_to_followup() == []  # лид отписан
