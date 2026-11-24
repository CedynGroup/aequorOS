# pyright: standard
"""Alembic environment for the gateway's own migration chain.

The database URL comes from ``-x url=…``, else ``GATEWAY_MIGRATION_DATABASE_URL``, else
``GATEWAY_DATABASE_URL``. A deployment migrates as the schema owner
(``GATEWAY_MIGRATION_DATABASE_URL``) while the API connects as ``GATEWAY_DB_APP_ROLE``,
which the migrations grant only what the API needs; both come from one env file, so the
owner's URL cannot be the variable the API reads. ``-x schema=…`` runs the chain inside a
Postgres schema (used for disposable test schemas).
"""

from __future__ import annotations

import os

from sqlalchemy import Connection

import app.db.models  # noqa: F401  # pyright: ignore[reportUnusedImport] - registers the tables
from alembic import context
from app.core.settings import get_settings
from app.db.base import Base
from app.db.session import make_engine

config = context.config
target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    x_args = context.get_x_argument(as_dictionary=True)  # also reads cmd_opts.x
    url = (
        x_args.get("url")
        or os.environ.get("GATEWAY_MIGRATION_DATABASE_URL", "").strip()
        or get_settings().database_url
    )
    schema = x_args.get("schema")
    connection: object = config.attributes.get("connection")
    if isinstance(connection, Connection):
        _run(connection)
        return
    engine = make_engine(url, schema=schema)
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported")
run_migrations_online()
