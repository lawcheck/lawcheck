"""Репозиторий — все DB-операции над Scan/Finding в одном месте."""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from lawcheck.checks.base import Finding as CheckFinding
from lawcheck.db.models import Finding, Inquiry, Lead, Order, Scan, utcnow
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

def create_order(order_id: str, plan: str, amount: int, email: str = "",
                 scan_id: str = "") -> None:
    with session_scope() as sess:
        sess.add(Order(id=order_id, plan=plan, amount=amount, email=email,
                       scan_id=scan_id))


def paid_order_id_for_scan(scan_id: str) -> str | None:
    """id оплаченного заказа, оформленного с отчёта этого скана, или None.
    По нему на странице отчёта открываются рецепты «Как исправить»,
    а плашка ведёт в кабинет заказа за шаблонами и PDF."""
    if not scan_id:
        return None
    with session_scope() as sess:
        return sess.execute(
            select(Order.id)
            .where(Order.scan_id == scan_id, Order.status == "paid")
            .order_by(Order.paid_at.desc())
        ).scalars().first()


def set_order_payment(order_id: str, operation_id: str, payment_link: str) -> None:
    with session_scope() as sess:
        order = sess.get(Order, order_id)
        if order:
            order.operation_id = operation_id
            order.payment_link = payment_link
            order.status = "pending"


def mark_order_paid(order_id: str) -> bool:
    """Помечает заказ оплаченным. Возвращает True, если это был переход
    «не оплачен → оплачен» (для разовых уведомлений)."""
    with session_scope() as sess:
        order = sess.get(Order, order_id)
        if order and order.status != "paid":
            order.status = "paid"
            order.paid_at = utcnow()
            return True
    return False


def get_order(order_id: str) -> Order | None:
    with session_scope() as sess:
        return sess.get(Order, order_id)


def get_order_by_operation(operation_id: str) -> Order | None:
    with session_scope() as sess:
        return sess.execute(
            select(Order).where(Order.operation_id == operation_id)
        ).scalar_one_or_none()


# === Лиды (email со страницы отчёта) ===

def create_lead(scan_id: str, url: str, email: str) -> bool:
    """Сохраняет лид (dedupe по scan+email). True, если это новая запись."""
    with session_scope() as sess:
        exists = sess.execute(
            select(Lead).where(Lead.scan_id == scan_id, Lead.email == email)
        ).scalar_one_or_none()
        if not exists:
            sess.add(Lead(scan_id=scan_id, url=url, email=email))
            return True
    return False


def set_monitored_url(order_id: str, url: str) -> None:
    with session_scope() as sess:
        order = sess.get(Order, order_id)
        if order:
            order.monitored_url = url


def list_monitored_orders() -> list[Order]:
    """Оплаченные заказы с подключённым И подтверждённым сайтом — еженедельно
    сканируем только сайты, владение которыми подтверждено."""
    with session_scope() as sess:
        rows = sess.execute(
            select(Order).where(
                Order.status == "paid",
                Order.monitored_url != "",
                Order.verified_at.is_not(None),
            )
        ).scalars().all()
        return list(rows)


def list_done_scans_for_url(url: str, limit: int = 5) -> list[Scan]:
    """Завершённые сканы конкретного сайта, новые первыми (для diff и истории)."""
    with session_scope() as sess:
        rows = sess.execute(
            select(Scan)
            .options(selectinload(Scan.findings))
            .where(Scan.url == url, Scan.status == "done")
            .order_by(Scan.created_at.desc())
            .limit(limit)
        ).scalars().all()
        return list(rows)


def latest_scan_for_url(url: str) -> Scan | None:
    """Последний скан сайта в любом статусе (для троттлинга мониторинга)."""
    with session_scope() as sess:
        return sess.execute(
            select(Scan).where(Scan.url == url)
            .order_by(Scan.created_at.desc()).limit(1)
        ).scalar_one_or_none()


def ensure_verify_token(order_id: str, token: str) -> str:
    """Возвращает токен верификации заказа, генерируя при первом обращении."""
    with session_scope() as sess:
        order = sess.get(Order, order_id)
        if order is None:
            return ""
        if not order.verify_token:
            order.verify_token = token
        return order.verify_token


def mark_verified(order_id: str) -> None:
    with session_scope() as sess:
        order = sess.get(Order, order_id)
        if order and order.verified_at is None:
            order.verified_at = utcnow()


def reset_verification(order_id: str) -> None:
    """Сбрасывается при смене наблюдаемого сайта."""
    with session_scope() as sess:
        order = sess.get(Order, order_id)
        if order:
            order.verified_at = None


def create_inquiry(message: str, contact: str, page: str) -> int:
    """Сохраняет вопрос из чат-виджета. Возвращает id записи."""
    with session_scope() as sess:
        inq = Inquiry(message=message[:4000], contact=contact[:255], page=page[:2048])
        sess.add(inq)
        sess.flush()
        return inq.id


def list_inquiries(limit: int = 100) -> list[Inquiry]:
    """Вопросы из чат-виджета, новые первыми."""
    with session_scope() as sess:
        return list(sess.execute(
            select(Inquiry).order_by(Inquiry.created_at.desc()).limit(limit)
        ).scalars().all())


def list_leads(limit: int = 100) -> list[Lead]:
    """Email-лиды со страницы отчёта, новые первыми."""
    with session_scope() as sess:
        return list(sess.execute(
            select(Lead).order_by(Lead.created_at.desc()).limit(limit)
        ).scalars().all())


def set_client_chat_id(order_id: str, chat_id: str) -> Order | None:
    """Привязывает Telegram-чат клиента к заказу (deep-link бота)."""
    with session_scope() as sess:
        order = sess.get(Order, order_id)
        if order:
            order.client_chat_id = chat_id
        return order


def clients_subscribed_to_url(url: str) -> list[tuple[str, str]]:
    """(order_id, client_chat_id) для подтверждённых заказов, мониторящих url
    и подключивших Telegram. Для рассылки diff после скана."""
    with session_scope() as sess:
        rows = sess.execute(
            select(Order).where(
                Order.monitored_url == url,
                Order.verified_at.is_not(None),
                Order.client_chat_id != "",
            )
        ).scalars().all()
        return [(o.id, o.client_chat_id) for o in rows]
