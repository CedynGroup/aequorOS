"""Migrations production has not yet applied must succeed under its migration session.

Production's migration login runs with ``row_security = off``, so any statement
that forced row-level security would filter raises instead of quietly seeing
fewer rows: a LANGUAGE sql body planned at CREATE time, or the existing-row
check of a new foreign key. The default migration fixture runs with
``row_security = on``, where both pass, and that gap took the API down when
the 2026-11 deploy could not migrate.

Revisions up to ``PRODUCTION_BASELINE`` were applied before production adopted
that setting and are not re-run there; every later revision, including each
one added from now on, must apply under it.
"""

from __future__ import annotations

import os

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url

from alembic import command
from tests.support import postgres_migrations as migrations

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")

#: The last revision production applied before its migration login ran with
#: ``row_security = off``. Fixed: later revisions all run under the setting.
PRODUCTION_BASELINE = "202610140100"


def test_migrations_after_the_production_baseline_apply_without_row_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_config = migrations.alembic_config_for_app()
    schemas = migrations.migrate_isolated_postgres_schema(
        monkeypatch, downgrade=False, revision=PRODUCTION_BASELINE
    )
    try:
        schema = next(schemas)
        url = make_url(os.environ["DATABASE_URL"])
        options = " ".join(url.normalized_query.get("options", ()))
        monkeypatch.setenv(
            "DATABASE_URL",
            url.update_query_dict({"options": f"{options} -crow_security=off"}).render_as_string(
                hide_password=False
            ),
        )
        migrations.clear_database_caches()

        command.upgrade(alembic_config, "head")

        with schema.app_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                ScriptDirectory.from_config(alembic_config).get_current_head()
            )
    finally:
        schemas.close()
