"""Репозиторий — все DB-операции над Scan/Finding в одном месте."""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from lawcheck.checks.base import Finding as CheckFinding
from lawcheck.db.models import Finding, Scan, utcnow
from lawcheck.db.session import session_scope


def create_scan(scan_id: str, url: str, max_pages: int | None) -> None:
    with session_scope() as sess:
        sess.add(Scan(id=scan_id, url=url, status="pending", max_pages=max_pages))


def mark_running(scan_id: str) -> None:
    with session_scope() as sess:
        scan = sess.get(Scan, scan_id)
        if scan:
            scan.status = "running"


def mark_done(scan_id: str, pages_crawled: int, findings: list[CheckFinding]) -> None:
    with session_scope() as sess:
        scan = sess.get(Scan, scan_id)
        if not scan:
            return
        scan.status = "done"
        scan.pages_crawled = pages_crawled
        scan.finished_at = utcnow()
        scan.findings = [
            Finding(
                check_id=f.check_id, severity=f.severity.value, title=f.title,
                evidence=f.evidence, location=f.location,
                law_reference=f.law_reference, recommendation=f.recommendation,
            )
            for f in findings
        ]


def mark_error(scan_id: str, error: str) -> None:
    with session_scope() as sess:
        scan = sess.get(Scan, scan_id)
        if scan:
            scan.status = "error"
            scan.error = error[:4000]
            scan.finished_at = utcnow()


def get_scan(scan_id: str) -> Scan | None:
    with session_scope() as sess:
        scan = sess.get(Scan, scan_id)
        if scan:
            # форсим подгрузку findings до закрытия сессии
            _ = list(scan.findings)
        return scan


def list_recent_scans(limit: int = 50) -> list[Scan]:
    with session_scope() as sess:
        rows = sess.execute(
            select(Scan)
            .options(selectinload(Scan.findings))
            .order_by(Scan.created_at.desc())
            .limit(limit)
        ).scalars().all()
        return list(rows)
