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


def test_baseline_daet_tu_zhe_shemu_chto_create_all(tmp_path, monkeypatch):
    """Ровно то, на чём держится безопасность `alembic stamp head` на проде.

    База там создана `create_all` плюс ручными `_migrate_*`. Если baseline
    описывает что-то другое, `stamp` соврёт: Alembic будет считать схему
    приведённой, а она другая.
    """
    from lawcheck.db import session as db_session

    migrated = f"sqlite:///{tmp_path / 'a.db'}"
    monkeypatch.setattr(settings, "database_url", migrated)
    command.upgrade(Config(str(_ROOT / "alembic.ini")), "head")

    created = f"sqlite:///{tmp_path / 'b.db'}"
    monkeypatch.setattr(settings, "database_url", created)
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    db_session.init_db()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()

    def schema(url: str) -> set[str]:
        engine = create_engine(url)
        with engine.connect() as conn:
            rows = conn.exec_driver_sql(
                "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE 'alembic_%'"
            ).fetchall()
        return {" ".join(str(c).split()) for c in ("|".join(r) for r in rows)}

    assert schema(migrated) == schema(created)
