"""PostgreSQL contract for stress sign-off routing and step-up evidence."""

from __future__ import annotations

from sqlalchemy import inspect

from alembic import command
from tests.support.postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    forward_migrated_postgres_schema,
)

__all__ = ["forward_migrated_postgres_schema"]

_TABLE = "enterprise_stress_signoffs"
_COLUMNS = {
    "attestation_recipient_user_id",
    "signing_digest",
    "signing_authorization_id",
}
_FK = "fk_enterprise_stress_signoffs_signing_authorization"


def _columns(schema: MigratedPostgresSchema) -> set[str]:
    return {
        column["name"]
        for column in inspect(schema.app_engine).get_columns(_TABLE, schema=schema.schema_name)
    }


def test_stress_signer_ceremony_columns_round_trip(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    schema = forward_migrated_postgres_schema
    assert _columns(schema) >= _COLUMNS
    foreign_keys = inspect(schema.app_engine).get_foreign_keys(_TABLE, schema=schema.schema_name)
    authorization_fk = next(fk for fk in foreign_keys if fk["name"] == _FK)
    assert authorization_fk["referred_table"] == "signing_authorizations"
    assert authorization_fk["constrained_columns"] == ["signing_authorization_id"]
    assert authorization_fk["referred_columns"] == ["id"]

    config = alembic_config_for_app()
    command.downgrade(config, "202611070112")
    clear_database_caches()
    assert not (_COLUMNS & _columns(schema))

    command.upgrade(config, "head")
    clear_database_caches()
    assert _columns(schema) >= _COLUMNS
