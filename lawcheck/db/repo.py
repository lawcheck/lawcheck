"""Репозиторий — все DB-операции над Scan/Finding в одном месте."""
import secrets
from datetime import datetime, timedelta, timezone

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import load_only, raiseload, selectinload

from lawcheck.checks.base import Finding as CheckFinding
from lawcheck.db.models import AuthToken, Finding, Inquiry, Lead, NurtureSubscriber, Order, Scan, User, utcnow
from lawcheck.db.session import session_scope
from lawcheck.utils.contact import contact_url


# Период тарифа Pro. Совпадает с назначением платежа «LawCheck Pro, 1 месяц»
# (web/routes.py::_PLANS) — менять только вместе с ним.
PRO_PERIOD_DAYS = 30


def _active_clauses() -> list:
    """WHERE-условия «подписка действует» для запросов. Тот же смысл, что и
    subscription_active(), но применимо в SQL."""
    return [Order.status == "paid",
            Order.paid_until.is_not(None),
            Order.paid_until > utcnow()]


def subscription_active(order: Order | None) -> bool:
    """Активна ли подписка по заказу прямо сейчас.

    Единственное место, где решается «оплачено и ещё действует». Заказ без
    paid_until активным не считается: подписка либо активировалась и имеет срок,
    либо её нет.
    """
    if order is None or order.status != "paid" or order.paid_until is None:
        return False
    until = order.paid_until
    # sqlite отдаёт naive datetime — нормализуем к UTC (как в consume_auth_token).
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


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
                extra=f.extra or None,
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
    """Последние сканы: лента на главной и «пример отчёта» на /pricing.

    Находки намеренно не подгружаем. Обоим потребителям нужны только id, url,
    статус и дата, а findings — это 30–60 строк с Text-полями на каждый скан:
    на ленте из 80 сканов получалось несколько тысяч лишних строк на каждый
    заход на самую частую страницу сайта.

    `raiseload` вместо тихой ленивой загрузки: если находки здесь однажды
    понадобятся, пусть это упадёт сразу и явно, а не превратится в N+1.
    """
    with session_scope() as sess:
        rows = sess.execute(
            select(Scan)
            .options(load_only(Scan.id, Scan.url, Scan.status, Scan.created_at),
                     raiseload(Scan.findings))
            .order_by(Scan.created_at.desc())
            .limit(limit)
        ).scalars().all()
        return list(rows)


# === Заказы (оплата тарифов) ===

def create_order(order_id: str, plan: str, amount: int, email: str = "",
                 scan_id: str = "", entry_ref: str = "", entry_url: str = "") -> None:
    with session_scope() as sess:
        sess.add(Order(id=order_id, plan=plan, amount=amount, email=email,
                       scan_id=scan_id, entry_ref=entry_ref, entry_url=entry_url))


def recent_pending_order(email: str, scan_id: str, plan: str,
                         within_minutes: int = 10) -> Order | None:
    """Свежий незавершённый заказ с той же почты на тот же тариф и скан, если
    уже есть выданная ссылка на оплату. Нужен, чтобы повторный клик «Оплатить»
    (двойной клик, зависший редирект на банк) не плодил дубль заказа и не бил
    по кассе банка второй операцией — см. аварию 05.08.2026: один сломанный
    переход на кассу наплодил 14 заказов на один email за 30 секунд."""
    cutoff = utcnow() - timedelta(minutes=within_minutes)
    with session_scope() as sess:
        order = sess.execute(
            select(Order).where(
                Order.email == email,
                Order.scan_id == scan_id,
                Order.plan == plan,
                Order.status == "pending",
                Order.payment_link != "",
                Order.created_at >= cutoff,
            ).order_by(Order.created_at.desc())
        ).scalars().first()
        if order:
            sess.expunge(order)
        return order


def paid_order_id_for_scan(scan_id: str, order_ids: Sequence[str]) -> str | None:
    """id заказа, если среди предъявленных есть оплаченный и оформленный
    с отчёта ЭТОГО скана.

    Доступ к платному отчёту — свойство пары «покупатель + отчёт», а не самого
    отчёта. Раньше здесь возвращался любой оплаченный заказ по скану, поэтому
    оплата одного клиента открывала отчёт всем, кто знает ссылку — а ссылки на
    последние сканы публикуются в ленте на главной.

    Предъявленных заказов бывает несколько: тот, что в ссылке, плюс накопленные
    в cookie-сессии. Спрашиваем БД один раз списком, а не по разу на кандидата.
    """
    ids = [oid for oid in order_ids if oid]
    if not scan_id or not ids:
        return None
    with session_scope() as sess:
        return sess.execute(
            select(Order.id).where(
                Order.id.in_(ids),
                Order.scan_id == scan_id,
                Order.status == "paid",
            )
        ).scalars().first()


def set_order_payment(order_id: str, operation_id: str, payment_link: str) -> None:
    with session_scope() as sess:
        order = sess.get(Order, order_id)
        if order:
            order.operation_id = operation_id
            order.payment_link = payment_link
            order.status = "pending"


def mark_order_paid(order_id: str) -> bool:
    """Помечает заказ оплаченным и открывает подписку на период тарифа.
    Возвращает True, если это был переход «не оплачен → оплачен»
    (для разовых уведомлений)."""
    with session_scope() as sess:
        # Одним UPDATE с условием в WHERE, а не «прочитали → проверили → записали»:
        # вебхук банка и возврат клиента на /pay/success приходят одновременно,
        # оба видят статус «не оплачен» и оба возвращают True — владелец получает
        # два одинаковых уведомления об одной оплате.
        now = utcnow()
        result = cast(CursorResult[Any], sess.execute(
            update(Order)
            .where(Order.id == order_id, Order.status != "paid")
            .values(status="paid", paid_at=now,
                    paid_until=now + timedelta(days=PRO_PERIOD_DAYS))
        ))
        return result.rowcount == 1


def get_order(order_id: str) -> Order | None:
    with session_scope() as sess:
        return sess.get(Order, order_id)


def get_order_by_operation(operation_id: str) -> Order | None:
    with session_scope() as sess:
        return sess.execute(
            select(Order).where(Order.operation_id == operation_id)
        ).scalar_one_or_none()


def orders_to_remind(delay_hours: int = 6, max_age_days: int = 14,
                     limit: int = 20) -> list[Order]:
    """Заказы с брошенной оплатой, которым пора отправить разовое напоминание:
    статус `pending` (ссылка выдана, денег нет), `reminded_at` пуст, есть email,
    возраст в окне [delay_hours; max_age_days], и с этого email НЕ оплачен
    никакой другой заказ. Возвращает detached-объекты.

    На один email приходится РОВНО ОДНО письмо: берём самый свежий заказ и
    пропускаем адрес целиком, если ему уже писали. Иначе человек получает
    столько писем, сколько раз он жал «Оплатить»: один сломанный переход на
    кассу 05.08.2026 наплодил по 14 заказов на адрес, и без этого правила
    каждому ушла бы пачка одинаковых напоминаний.

    Верхняя граница возраста тут не косметика: напоминать про заказ трёхнедельной
    давности поздно и выглядит навязчиво, а платёжная ссылка к тому моменту
    всё равно мертва (её перевыпускает /pay/retry).
    """
    now = utcnow()
    lo = now - timedelta(days=max_age_days)
    hi = now - timedelta(hours=delay_hours)
    with session_scope() as sess:
        skip_emails = set(sess.execute(
            select(Order.email).where(Order.status == "paid", Order.email != "")
        ).scalars())
        skip_emails |= set(sess.execute(
            select(Order.email).where(Order.reminded_at.is_not(None), Order.email != "")
        ).scalars())
        rows = sess.execute(
            select(Order).where(
                Order.status == "pending",
                Order.reminded_at.is_(None),
                Order.email != "",
                Order.created_at >= lo,
                Order.created_at <= hi,
            ).order_by(Order.created_at)
        ).scalars().all()
        # rows отсортированы по created_at, поэтому запись в словарь оставляет
        # самый свежий заказ адреса — у него и ссылка самая живая.
        latest: dict[str, Order] = {}
        for o in rows:
            if o.email in skip_emails:
                continue
            latest[o.email] = o
        return list(latest.values())[:limit]


def mark_order_reminded(order_id: str) -> None:
    """Проставляет момент отправки напоминания (защита от повторной отправки)."""
    with session_scope() as sess:
        order = sess.get(Order, order_id)
        if order and order.reminded_at is None:
            order.reminded_at = utcnow()


# === Лиды (email со страницы отчёта) ===

def create_lead(scan_id: str, url: str, email: str) -> bool:
    """Сохраняет лид (dedupe по scan+email). True, если это новая запись."""
    with session_scope() as sess:
        exists = sess.execute(
            select(Lead).where(Lead.scan_id == scan_id, Lead.email == email)
        ).scalar_one_or_none()
        if not exists:
            sess.add(Lead(scan_id=scan_id, url=url, email=email,
                          unsub_token=secrets.token_urlsafe(24)))
            return True
    return False


def leads_to_followup(delay_hours: int = 24, max_age_days: int = 14,
                      limit: int = 50) -> list[Lead]:
    """Лиды, которым пора отправить письмо-догонялку (scan_submit → оплата):
    не писали (`mailed_at` пуст), не отписались, возраст в окне
    [delay_hours; max_age_days], по их скану/email НЕТ оплаченного заказа,
    а сам скан завершён и содержит нарушения. Возвращает detached-объекты
    (безопасен доступ к скалярным полям после закрытия сессии)."""
    now = utcnow()
    lo = now - timedelta(days=max_age_days)
    hi = now - timedelta(hours=delay_hours)
    with session_scope() as sess:
        paid_scans = set(sess.execute(
            select(Order.scan_id).where(Order.status == "paid", Order.scan_id != "")
        ).scalars())
        paid_emails = set(sess.execute(
            select(Order.email).where(Order.status == "paid", Order.email != "")
        ).scalars())
        done_scans = set(sess.execute(
            select(Scan.id).where(Scan.status == "done")
        ).scalars())
        problem_scans = set(sess.execute(
            select(Finding.scan_id).where(Finding.severity != "ok").distinct()
        ).scalars())
        candidates = sess.execute(
            select(Lead).where(
                Lead.mailed_at.is_(None),
                Lead.unsubscribed_at.is_(None),
                Lead.created_at >= lo,
                Lead.created_at <= hi,
            ).order_by(Lead.created_at.asc())
        ).scalars().all()
        out: list[Lead] = []
        for lead in candidates:
            if lead.email in paid_emails or lead.scan_id in paid_scans:
                continue
            if lead.scan_id not in done_scans or lead.scan_id not in problem_scans:
                continue
            sess.expunge(lead)
            out.append(lead)
            if len(out) >= limit:
                break
        return out


def mark_lead_mailed(lead_id: int) -> None:
    """Проставляет момент отправки письма-догонялки (защита от повторной отправки)."""
    with session_scope() as sess:
        lead = sess.get(Lead, lead_id)
        if lead and lead.mailed_at is None:
            lead.mailed_at = utcnow()


def unsubscribe_lead(token: str) -> str | None:
    """Отписка по токену из письма. Отписывает ВСЕ лиды с этим email (у человека
    может быть несколько сканов). Возвращает email для страницы-подтверждения
    или None, если токен неизвестен. Идемпотентна."""
    if not token:
        return None
    with session_scope() as sess:
        lead = sess.execute(
            select(Lead).where(Lead.unsub_token == token)
        ).scalars().first()
        if lead is None:
            return None
        now = utcnow()
        same_email = sess.execute(
            select(Lead).where(Lead.email == lead.email,
                               Lead.unsubscribed_at.is_(None))
        ).scalars().all()
        for row in same_email:
            row.unsubscribed_at = now
        return lead.email


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
                *_active_clauses(),
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


def create_inquiry(message: str, contact: str, page: str, ad_consent: bool = False) -> int:
    """Сохраняет вопрос из чат-виджета. Возвращает id записи."""
    with session_scope() as sess:
        inq = Inquiry(message=message[:4000], contact=contact[:255], page=page[:2048],
                      ad_consent=ad_consent, unsub_token=secrets.token_urlsafe(24))
        sess.add(inq)
        sess.flush()
        return inq.id


def inquiries_with_ad_consent(limit: int = 100) -> list[Inquiry]:
    """Заявки, которым по ст. 18 ФЗ «О рекламе» можно писать предложения:
    галочка стоит, отписки не было."""
    with session_scope() as sess:
        return list(sess.execute(
            select(Inquiry).where(Inquiry.ad_consent.is_(True),
                                  Inquiry.unsubscribed_at.is_(None))
            .order_by(Inquiry.created_at.desc()).limit(limit)
        ).scalars().all())


def _contact_key(contact: str) -> str:
    """Ключ «это один и тот же человек» для свободной строки контакта.

    Сравнивать сырые строки нельзя: `@maxim` и `t.me/maxim` — один телеграм,
    `Ya@Mail.ru` и `ya@mail.ru` — один ящик. Отписался бы он тогда только от
    одной из своих заявок и продолжил получать рекламу.
    """
    return (contact_url(contact) or contact).strip().lower()


def unsubscribe_inquiry(token: str) -> str | None:
    """Отписка заявки по токену. Отписывает все заявки того же человека.
    Возвращает контакт для страницы-подтверждения или None. Идемпотентна."""
    if not token:
        return None
    with session_scope() as sess:
        inq = sess.execute(
            select(Inquiry).where(Inquiry.unsub_token == token)
        ).scalars().first()
        if inq is None:
            return None
        now = utcnow()
        key = _contact_key(inq.contact)
        # Ключ считается в Python, поэтому перебираем подписанных. Их единицы:
        # рассылка идёт только по галочке, а фильтр по ней — в SQL.
        rows = sess.execute(
            select(Inquiry).where(Inquiry.unsubscribed_at.is_(None))
        ).scalars().all()
        for row in rows:
            if _contact_key(row.contact) == key:
                row.unsubscribed_at = now
        return inq.contact


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


# === Пользователи (аккаунты) ===

def get_user_by_email(email: str) -> User | None:
    with session_scope() as sess:
        return sess.execute(select(User).where(User.email == email)).scalar_one_or_none()


def get_user_by_id(user_id: int) -> User | None:
    with session_scope() as sess:
        return sess.get(User, user_id)


def create_user(email: str, password_hash: str) -> User | None:
    """Создаёт пользователя. None — если email уже занят."""
    with session_scope() as sess:
        if sess.execute(select(User.id).where(User.email == email)).first():
            return None
        user = User(email=email, password_hash=password_hash)
        sess.add(user)
        sess.flush()  # присвоить user.id до выхода из сессии
        return user


def set_email_verified(user_id: int) -> None:
    with session_scope() as sess:
        user = sess.get(User, user_id)
        if user and user.email_verified_at is None:
            user.email_verified_at = utcnow()


def set_user_password(user_id: int, password_hash: str) -> None:
    """Меняет пароль и завершает ВСЕ существующие сессии пользователя.

    Пароль меняют в том числе когда аккаунт увели. Если старые cookie продолжают
    пускать, смена пароля не решает исходную проблему.
    """
    with session_scope() as sess:
        user = sess.get(User, user_id)
        if user:
            user.password_hash = password_hash
            user.session_epoch = (user.session_epoch or 0) + 1


# === Одноразовые токены (подтверждение email, сброс пароля) ===

def create_auth_token(user_id: int, purpose: str, ttl_hours: int) -> str:
    """Создаёт одноразовый токен с TTL и возвращает его значение."""
    token = secrets.token_urlsafe(32)
    with session_scope() as sess:
        sess.add(AuthToken(
            token=token, user_id=user_id, purpose=purpose,
            expires_at=utcnow() + timedelta(hours=ttl_hours),
        ))
    return token


def consume_auth_token(token: str, purpose: str) -> int | None:
    """Проверяет токен (нужное назначение, не использован, не истёк) и помечает
    использованным. Возвращает user_id или None. Гонки закрыты uniq-токеном."""
    if not token:
        return None
    with session_scope() as sess:
        row = sess.execute(
            select(AuthToken).where(AuthToken.token == token)
        ).scalar_one_or_none()
        if row is None or row.purpose != purpose or row.used_at is not None:
            return None
        # sqlite отдаёт naive datetime — нормализуем к UTC для сравнения.
        exp = row.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return None
        row.used_at = utcnow()
        return row.user_id


# === Привязка контента к аккаунту (дашборд) ===

def set_scan_user(scan_id: str, user_id: int) -> None:
    """Привязать скан к пользователю (когда залогиненный запускает проверку)."""
    with session_scope() as sess:
        scan = sess.get(Scan, scan_id)
        if scan and scan.user_id is None:
            scan.user_id = user_id


def list_scans_for_user(user_id: int) -> list[Scan]:
    with session_scope() as sess:
        return list(sess.execute(
            select(Scan).where(Scan.user_id == user_id).order_by(Scan.created_at.desc())
        ).scalars())


def list_orders_for_user(user_id: int) -> list[Order]:
    with session_scope() as sess:
        return list(sess.execute(
            select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc())
        ).scalars())


def claim_for_user(user_id: int, email: str) -> int:
    """Привязать к аккаунту прошлые заказы и сканы, связанные с этим email
    (по заказам с этим email и по оставленным лидам). Вызывать ТОЛЬКО для
    подтверждённого email. Идемпотентно. Возвращает число привязанных сканов."""
    if not email:
        return 0
    with session_scope() as sess:
        for order in sess.execute(
            select(Order).where(Order.email == email, Order.user_id.is_(None))
        ).scalars():
            order.user_id = user_id
        scan_ids: set[str] = set()
        scan_ids.update(sess.execute(
            select(Order.scan_id).where(Order.email == email, Order.scan_id != "")
        ).scalars())
        scan_ids.update(sess.execute(
            select(Lead.scan_id).where(Lead.email == email)
        ).scalars())
        linked = 0
        for sid in scan_ids:
            scan = sess.get(Scan, sid)
            if scan and scan.user_id is None:
                scan.user_id = user_id
                linked += 1
        return linked


def user_has_paid_order(user_id: int) -> bool:
    """Есть ли у пользователя ДЕЙСТВУЮЩАЯ Pro-подписка.
    По ней его собственные отчёты открываются целиком."""
    with session_scope() as sess:
        return sess.execute(
            select(Order.id).where(Order.user_id == user_id, *_active_clauses())
        ).first() is not None


def latest_paid_order_id_for_user(user_id: int) -> str | None:
    """id последнего заказа с действующей подпиской (для ссылок в кабинет и
    шаблоны при подписочной разблокировке отчёта). None — если подписки нет."""
    with session_scope() as sess:
        return sess.execute(
            select(Order.id).where(Order.user_id == user_id, *_active_clauses())
            .order_by(Order.paid_at.desc())
        ).scalars().first()


# === Nurture-цепочка ===

NURTURE_STEPS = 8
NURTURE_INTERVAL_DAYS = 7


def nurture_subscribe(email: str) -> bool:
    """Добавить email в nurture-цепочку (dedupe по email).
    True — если новая запись. Существующего подписчика не трогаем."""
    with session_scope() as sess:
        exists = sess.execute(
            select(NurtureSubscriber).where(NurtureSubscriber.email == email)
        ).scalar_one_or_none()
        if exists:
            return False
        sess.add(NurtureSubscriber(
            email=email,
            step=1,
            next_send_at=utcnow(),
            unsub_token=secrets.token_urlsafe(24),
        ))
        return True


def nurture_to_send(limit: int = 50) -> list[NurtureSubscriber]:
    """Подписчики, которым пора отправить текущий шаг."""
    now = utcnow()
    with session_scope() as sess:
        rows = sess.execute(
            select(NurtureSubscriber).where(
                NurtureSubscriber.unsubscribed_at.is_(None),
                NurtureSubscriber.step <= NURTURE_STEPS,
                NurtureSubscriber.next_send_at <= now,
            ).order_by(NurtureSubscriber.next_send_at.asc())
        ).scalars().all()
        out: list[NurtureSubscriber] = []
        for sub in rows:
            sess.expunge(sub)
            out.append(sub)
            if len(out) >= limit:
                break
        return out


def nurture_advance(subscriber_id: int) -> None:
    """Увеличить шаг и сдвинуть next_send_at на N дней вперёд.
    Если шаг превышает NURTURE_STEPS — ничего не делаем (цепочка закончена)."""
    with session_scope() as sess:
        sub = sess.get(NurtureSubscriber, subscriber_id)
        if sub and sub.step <= NURTURE_STEPS:
            sub.step += 1
            sub.next_send_at = utcnow() + timedelta(days=NURTURE_INTERVAL_DAYS)


def nurture_unsubscribe_by_token(token: str) -> bool:
    """Отписка по токену из письма."""
    with session_scope() as sess:
        sub = sess.execute(
            select(NurtureSubscriber).where(NurtureSubscriber.unsub_token == token)
        ).scalar_one_or_none()
        if sub and sub.unsubscribed_at is None:
            sub.unsubscribed_at = utcnow()
            return True
        return False


def nurture_remove_paid(email: str) -> int:
    """Помечаем отписанными всех оплативших — чтобы не спамить клиентов."""
    with session_scope() as sess:
        paid_emails = set(sess.execute(
            select(Order.email).where(Order.status == "paid", Order.email != "")
        ).scalars())
        if email not in paid_emails:
            return 0
        subs = sess.execute(
            select(NurtureSubscriber).where(
                NurtureSubscriber.email == email,
                NurtureSubscriber.unsubscribed_at.is_(None),
            )
        ).scalars().all()
        for sub in subs:
            sub.unsubscribed_at = utcnow()
        return len(subs)
