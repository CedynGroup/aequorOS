from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import get_settings
from app.db.base import Base
from app.models import audit_event, financial, organization, risk, user

_ = (audit_event, financial, organization, risk, user)

config = context.config

if config.config_file_name is not None:
    # ``fileConfig`` disables every logger that already exists unless told
    # otherwise, and this module is imported in-process by the Postgres test
    # suite's ``command.upgrade`` calls: with the default, every application
    # logger created before the upgrade (``app.ai`` among them) went silent for
    # the rest of the run. Configure the alembic/sqlalchemy loggers only.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_database_url() -> str:
    database_url = get_settings().database.database_url
    if database_url is None:
        msg = "DATABASE_URL is required to run Alembic migrations."
        raise RuntimeError(msg)
    return database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
