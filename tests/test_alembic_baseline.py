"""Миграции не должны расходиться с моделями.

Сторож против самой вероятной ошибки при работе с Alembic: колонку добавили
в модель, миграцию сгенерировать забыли. Локально всё работает (dev-база
пересоздаётся), а на проде колонки нет — и это выясняется в рантайме.

Тест накатывает миграции на пустую базу и сравнивает результат с `Base.metadata`
тем же механизмом, которым пользуется `alembic revision --autogenerate`. Пустой
diff = миграции описывают ровно те модели, что в коде.
"""
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from lawcheck.config import settings
from lawcheck.db.models import Base

_ROOT = Path(__file__).parent.parent


def test_migracii_sovpadayut_s_modelyami(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    # env.py берёт URL из настроек, а не из ini — подменяем настройки.
    monkeypatch.setattr(settings, "database_url", url)

    command.upgrade(Config(str(_ROOT / "alembic.ini")), "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"схема после миграций разошлась с моделями: {diff}"


def test_init_db_podnimaet_shemu_s_nulya(tmp_path, monkeypatch):
    """init_db на пустой базе должен дать рабочую схему — теперь через Alembic."""
    from lawcheck.db import repo, session as db_session

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'fresh.db'}")
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    try:
        db_session.init_db()
        repo.create_scan("s1", "https://example.com/", 5)
        assert repo.get_scan("s1") is not None
    finally:
        db_session.get_engine.cache_clear()
        db_session.get_sessionmaker.cache_clear()
