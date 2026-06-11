"""Репозиторий — все DB-операции над Scan/Finding в одном месте."""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from lawcheck.checks.base import Finding as CheckFinding
from lawcheck.db.models import Finding, Order, Scan, utcnow
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


# === Заказы (оплата тарифов) ===

def create_order(order_id: str, plan: str, amount: int, email: str = "") -> None:
    with session_scope() as sess:
        sess.add(Order(id=order_id, plan=plan, amount=amount, email=email))


def set_order_payment(order_id: str, operation_id: str, payment_link: str) -> None:
    with session_scope() as sess:
        order = sess.get(Order, order_id)
        if order:
            order.operation_id = operation_id
            order.payment_link = payment_link
            order.status = "pending"


def mark_order_paid(order_id: str) -> None:
    with session_scope() as sess:
        order = sess.get(Order, order_id)
        if order and order.status != "paid":
            order.status = "paid"
            order.paid_at = utcnow()


def get_order(order_id: str) -> Order | None:
    with session_scope() as sess:
        return sess.get(Order, order_id)


def get_order_by_operation(operation_id: str) -> Order | None:
    with session_scope() as sess:
        return sess.execute(
            select(Order).where(Order.operation_id == operation_id)
        ).scalar_one_or_none()
