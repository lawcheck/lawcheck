import pytest

from lawcheck.checks.base import Finding, Severity
from lawcheck.config import settings
from lawcheck.db import repo, session
from lawcheck.db.session import init_db


@pytest.fixture(autouse=True)
def isolated_in_memory_db(monkeypatch):
    """Каждый тест получает свежую in-memory sqlite-БД."""
    # Сбрасываем кэшированные engine/sessionmaker
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()
    monkeypatch.setattr(settings, "database_url", "sqlite:///:memory:")
    init_db()
    yield
    session.get_engine.cache_clear()
    session.get_sessionmaker.cache_clear()


def _finding(check_id: str = "A1", severity: Severity = Severity.OK) -> Finding:
    return Finding(
        check_id=check_id, severity=severity, title="t",
        evidence="e", location="https://x/", law_reference="ст.1",
    )


def test_create_and_get_scan():
    repo.create_scan("abc", "https://example.com/", max_pages=5)
    scan = repo.get_scan("abc")
    assert scan is not None
    assert scan.status == "pending"
    assert scan.url == "https://example.com/"
    assert scan.findings == []


def test_get_nonexistent_returns_none():
    assert repo.get_scan("nope") is None


def test_lifecycle_pending_running_done():
    repo.create_scan("abc", "https://x/", None)
    repo.mark_running("abc")
    assert repo.get_scan("abc").status == "running"

    repo.mark_done("abc", pages_crawled=7, findings=[
        _finding("A1", Severity.OK),
        _finding("B2", Severity.CRITICAL),
    ])
    scan = repo.get_scan("abc")
    assert scan.status == "done"
    assert scan.pages_crawled == 7
    assert scan.finished_at is not None
    assert {f.check_id for f in scan.findings} == {"A1", "B2"}
    assert {f.severity for f in scan.findings} == {"ok", "critical"}


def test_mark_error():
    repo.create_scan("abc", "https://x/", None)
    repo.mark_error("abc", "boom")
    scan = repo.get_scan("abc")
    assert scan.status == "error"
    assert scan.error == "boom"


def test_long_error_is_truncated():
    repo.create_scan("abc", "https://x/", None)
    repo.mark_error("abc", "x" * 10_000)
    assert len(repo.get_scan("abc").error) <= 4000


def test_mark_done_replaces_previous_findings():
    repo.create_scan("abc", "https://x/", None)
    repo.mark_done("abc", pages_crawled=1, findings=[_finding("A1")])
    repo.mark_done("abc", pages_crawled=2, findings=[_finding("B2"), _finding("E1")])
    scan = repo.get_scan("abc")
    assert {f.check_id for f in scan.findings} == {"B2", "E1"}


def test_recent_scans_ordered_desc():
    for i in range(3):
        repo.create_scan(f"id{i}", f"https://x{i}/", None)
    scans = repo.list_recent_scans(limit=10)
    assert [s.id for s in scans] == ["id2", "id1", "id0"]
