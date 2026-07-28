"""Подключение к БД и фабрика сессий.

Для MVP — синхронный SQLAlchemy. Все вызовы из async-эндпойнтов оборачиваем
в asyncio.to_thread() (см. api/routes/scan.py).
"""
import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from functools import lru_cache

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from lawcheck.config import settings
from lawcheck.db.models import Base, Lead

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = settings.database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def init_db() -> None:
    """Создаёт таблицы, если их нет. Для MVP — вместо Alembic."""
    Base.metadata.create_all(bind=get_engine())
    _migrate_leads_followup()
    _migrate_findings_extra()
    _migrate_users_session_epoch()
    _migrate_orders_paid_until()


def _migrate_leads_followup() -> None:
    """Лёгкая миграция вместо Alembic: досоздаёт колонки follow-up в `leads`
    и генерирует `unsub_token` для старых записей. Идемпотентна — `create_all`
    не изменяет уже существующую таблицу, поэтому колонки добавляем вручную."""
    engine = get_engine()
    insp = inspect(engine)
    if "leads" not in insp.get_table_names():
        return  # свежая БД — create_all уже создал колонки
    cols = {c["name"] for c in insp.get_columns("leads")}
    ts = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "TIMESTAMP"
    stmts = []
    if "unsub_token" not in cols:
        stmts.append("ALTER TABLE leads ADD COLUMN unsub_token VARCHAR(64) DEFAULT ''")
    if "mailed_at" not in cols:
        stmts.append(f"ALTER TABLE leads ADD COLUMN mailed_at {ts}")
    if "unsubscribed_at" not in cols:
        stmts.append(f"ALTER TABLE leads ADD COLUMN unsubscribed_at {ts}")
    if stmts:
        with engine.begin() as conn:
            for stmt in stmts:
                conn.execute(text(stmt))
        log.info("migrate: leads follow-up columns added (%d)", len(stmts))
    # Бэкфилл токенов отписки для строк без него (старые лиды + только что добавленная колонка).
    with session_scope() as sess:
        rows = sess.execute(
            select(Lead).where((Lead.unsub_token == "") | (Lead.unsub_token.is_(None)))
        ).scalars().all()
        for lead in rows:
            lead.unsub_token = secrets.token_urlsafe(24)
        if rows:
            log.info("migrate: backfilled unsub_token for %d leads", len(rows))


def _migrate_findings_extra() -> None:
    """Досоздаёт `findings.extra` (структурные факты проверки) на БД,
    созданных до появления колонки в модели. Идемпотентна."""
    engine = get_engine()
    insp = inspect(engine)
    if "findings" not in insp.get_table_names():
        return  # свежая БД — create_all уже создал колонку
    cols = {c["name"] for c in insp.get_columns("findings")}
    if "extra" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE findings ADD COLUMN extra JSON"))
    log.info("migrate: findings.extra column added")


def _migrate_users_session_epoch() -> None:
    """Досоздаёт `users.session_epoch` (версия сессий). Идемпотентна.

    Значение по умолчанию 0 совпадает с тем, что читается из старых cookie без
    этого поля, поэтому уже вошедшие пользователи не разлогиниваются при выкате.
    """
    engine = get_engine()
    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        return  # свежая БД — create_all уже создал колонку
    cols = {c["name"] for c in insp.get_columns("users")}
    if "session_epoch" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0"))
    log.info("migrate: users.session_epoch column added")
def _migrate_orders_paid_until() -> None:
    """Досоздаёт `orders.paid_until` и выдаёт действующим заказам месяц с даты
    выката. Идемпотентна.

    Раньше доступ определялся `status == "paid"` без срока, то есть разовая
    оплата открывала Pro навсегда. Бэкфилл сознательно считает срок от МОМЕНТА
    МИГРАЦИИ, а не от `paid_at`: иначе все ранее оплатившие потеряли бы доступ
    ровно в секунду выката, без предупреждения.
    """
    engine = get_engine()
    insp = inspect(engine)
    if "orders" not in insp.get_table_names():
        return  # свежая БД — create_all уже создал колонку
    cols = {c["name"] for c in insp.get_columns("orders")}
    if "paid_until" in cols:
        return
    ts = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "TIMESTAMP"
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE orders ADD COLUMN paid_until {ts}"))
    log.info("migrate: orders.paid_until column added")

    from lawcheck.db.models import Order, utcnow
    from lawcheck.db.repo import PRO_PERIOD_DAYS
    grace_until = utcnow() + timedelta(days=PRO_PERIOD_DAYS)
    with session_scope() as sess:
        rows = sess.execute(
            select(Order).where(Order.status == "paid", Order.paid_until.is_(None))
        ).scalars().all()
        for order in rows:
            order.paid_until = grace_until
        if rows:
            log.info("migrate: выдан месяц с даты выката %d оплаченным заказам", len(rows))


@contextmanager
def session_scope() -> Iterator[Session]:
    sess = get_sessionmaker()()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()
