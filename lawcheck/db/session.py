"""Подключение к БД и фабрика сессий.

Для MVP — синхронный SQLAlchemy. Все вызовы из async-эндпойнтов оборачиваем
в asyncio.to_thread() (см. api/routes/scan.py).

Схему приводит в порядок Alembic (`alembic/`). Раньше здесь жили `create_all`
и шесть функций `_migrate_*` с ручными ALTER: `create_all` умеет только создать
отсутствующую таблицу, а добавить колонку в существующую — нет, и молчит об
этом. Каждое новое поле приходилось дописывать руками и там, и на проде.
"""
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from lawcheck.config import settings

log = logging.getLogger(__name__)

_ALEMBIC_INI = Path(__file__).parent.parent.parent / "alembic.ini"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = settings.database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def init_db() -> None:
    """Привести схему к последней ревизии Alembic.

    Вызывается при старте API и из батч-инструментов. Идемпотентно: если база
    уже на head, Alembic ничего не делает. Накатывает только `api` — воркер
    сюда не заходит, поэтому гонки двух контейнеров нет.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    # Не давать Alembic переопределить логирование приложения: его ini ставит
    # root-логгер в WARN, и логи сервиса замолчали бы после первого же старта.
    cfg.attributes["configure_logger"] = False
    # Своё соединение, а не отдельный движок из alembic.ini: иначе миграции
    # уедут в другую базу — на sqlite `:memory:` это видно сразу.
    with get_engine().begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
    log.info("db: схема приведена к последней ревизии")


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
