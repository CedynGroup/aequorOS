from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

import app.models as model_registry
from alembic import context
from app.core.config import get_migration_database_url
from app.core.tls import database_connect_args
from app.db.base import Base
from app.db.session import database_timeout_connect_args

#: The registry: importing it maps every feature's tables onto ``Base.metadata``.
_ = model_registry

config = context.config

if config.config_file_name is not None:
    # ``fileConfig`` disables every logger that already exists unless told
    # otherwise, and this module is imported in-process by the Postgres test
    # suite's ``command.upgrade`` calls: with the default, every application
    # logger created before the upgrade (``app.ai`` among them) went silent for
    # the rest of the run. Configure the alembic/sqlalchemy loggers only.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_migration_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_migration_database_url()

    url = get_migration_database_url()
    connect_args = database_connect_args(url)
    if make_url(url).drivername.startswith("postgresql"):
        connect_args.update(database_timeout_connect_args(url, "aequoros-migrator"))
        connect_args["application_name"] = "aequoros-migrator"
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    with connectable.connect() as connection:
        if connection.dialect.name == "postgresql" and make_url(url).username == "aqs_migrator":
            connection.exec_driver_sql("SET ROLE aqs_owner")
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
