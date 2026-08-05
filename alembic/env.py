"""Окружение Alembic.

URL базы берём из настроек приложения (lawcheck.config), а не из alembic.ini:
в проде он собирается docker-compose из пароля Postgres, и вторая копия того же
адреса в конфиге миграций — способ однажды накатить миграцию не туда.

`target_metadata` — то же `Base.metadata`, по которому раньше работал
`create_all`. Автогенерация сравнивает модели с фактической схемой базы.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from lawcheck.config import settings
from lawcheck.db.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

# Логирование настраиваем только при запуске из CLI. При программном вызове
# из init_db() приложение уже настроило своё, а ini Alembic ставит root в WARN.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """SQL без подключения к базе (alembic upgrade --sql) — для ревью миграции."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # sqlite не умеет ALTER в большинстве случаев — batch-режим
        # пересоздаёт таблицу. В проде Postgres, но dev-база sqlite,
        # и миграции должны накатываться на обе.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Приложение (init_db) передаёт свою соединение — тогда миграции идут по
    # тому же движку, что и запросы. Для sqlite это обязательно: `:memory:`
    # у каждого нового подключения СВОЯ, и отдельный движок мигрировал бы
    # чужую пустую базу.
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as conn:
        _run(conn)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
