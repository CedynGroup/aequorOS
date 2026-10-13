"""Prepare roles and migrations in a disposable local PostgreSQL database."""

from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command


def main() -> None:
    if os.getenv("APP_ENV", "local") != "local":
        raise RuntimeError(
            "Local bootstrap requires APP_ENV=local; use the deployment runbook elsewhere"
        )
    os.environ.setdefault("APP_ENV", "local")
    os.environ.setdefault("TLS_ALLOW_PLAINTEXT", "1")
    url = os.environ["DATABASE_URL"]
    if make_url(url).host not in {"localhost", "127.0.0.1", "risk-postgres"}:
        raise RuntimeError("Local bootstrap requires the local PostgreSQL service")
    # Alembic must use the validated local URL even if a shell has a separate
    # deployment migration URL configured.
    os.environ["MIGRATION_DATABASE_URL"] = url
    root = Path(__file__).resolve().parents[1]
    engine = create_engine(url, connect_args={"application_name": "aequoros-local-bootstrap"})
    try:
        with engine.begin() as connection:
            for filename in (
                "database_roles_202610120096.sql",
                "database_roles_local.sql",
                "database_session_timeouts.sql",
            ):
                connection.execute(text((root / "scripts" / filename).read_text()))
    finally:
        engine.dispose()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
