"""Подключение к БД и фабрика сессий.

Для MVP — синхронный SQLAlchemy. Все вызовы из async-эндпойнтов оборачиваем
в asyncio.to_thread() (см. api/routes/scan.py).
"""
import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
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
