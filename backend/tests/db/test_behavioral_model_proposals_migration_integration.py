"""Postgres proof for immutable, tenant-scoped Behavioral model proposals."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from tests.support.postgres_migrations import MigratedPostgresSchema, migrated_postgres_schema

__all__ = ["migrated_postgres_schema"]

_TABLE = "behavioral_model_proposals"
_ORG = "OR-BEHM0001"
_BANK = "BK-BEHM0001"
_NOW = datetime.now(UTC)
_PROPOSAL = uuid4()
_PROPOSER = uuid4()
_REVIEWER = uuid4()
_BATCH = uuid4()


class _AcceptedProposalRow(BaseModel):
    status: str
    reviewed_by_user_id: UUID
    ingestion_batch_id: UUID
    values: list[dict[str, object]]


def _set_tenant(connection: Connection) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": _ORG},
    )


def _seed_proposal(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, created_at, updated_at) "
            "VALUES (:organization_id, 'Behavioral migration proof', :now, :now)"
        ),
        {"organization_id": _ORG, "now": _NOW},
    )
    connection.execute(
        text(
            """
            INSERT INTO banks
              (id, organization_id, name, short_name, currency, jurisdiction_code,
               license_type, institution_type, created_at, updated_at)
            VALUES
              (:bank_id, :organization_id, 'Behavioral proof bank', 'BEH', 'GHS', 'GH',
               'universal_bank', 'universal_bank', :now, :now)
            """
        ),
        {"bank_id": _BANK, "organization_id": _ORG, "now": _NOW},
    )
    connection.execute(
        text(
            """
            INSERT INTO users
              (id, organization_id, email, display_name, is_active, role, auth_provider,
               failed_login_attempts, authorization_version, created_at, updated_at)
            VALUES
              (:proposer, :organization_id, 'proposer@behavioral.example', 'Proposer', true,
               'viewer', 'password', 0, 1, :now, :now),
              (:reviewer, :organization_id, 'reviewer@behavioral.example', 'Reviewer', true,
               'viewer', 'password', 0, 1, :now, :now)
            """
        ),
        {"proposer": _PROPOSER, "reviewer": _REVIEWER, "organization_id": _ORG, "now": _NOW},
    )
    connection.execute(
        text(
            """
            INSERT INTO behavioral_model_proposals
              (id, organization_id, bank_id, model, model_version, as_of_date, values,
               status, proposed_by_user_id, proposed_at, created_at, updated_at)
            VALUES
              (:id, :organization_id, :bank_id, 'nmd-duration', 'nmd-v1', '2026-03-31',
               CAST(:values AS JSONB), 'pending', :proposer, :now, :now, :now)
            """
        ),
        {
            "id": _PROPOSAL,
            "organization_id": _ORG,
            "bank_id": _BANK,
            "values": json.dumps(
                [{"product_code": "retail_savings", "value": 36.5, "unit": "months"}]
            ),
            "proposer": _PROPOSER,
            "now": _NOW,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO ingestion_batches
              (id, organization_id, bank_id, source_system, adapter_version, extraction_mode,
               status, as_of_date, created_by, created_at, updated_at)
            VALUES
              (:id, :organization_id, :bank_id, 'EXCEL_CSV', 'behavioral-test', 'full',
               'accepted', '2026-03-31', :reviewer, :reviewed_at, :reviewed_at)
            """
        ),
        {
            "id": _BATCH,
            "organization_id": _ORG,
            "bank_id": _BANK,
            "reviewer": _REVIEWER,
            "reviewed_at": _NOW,
        },
    )


def _accept(connection: Connection, reviewer_id: UUID) -> None:
    connection.execute(
        text(
            """
            UPDATE behavioral_model_proposals
            SET status = 'accepted', reviewed_by_user_id = :reviewer,
                reviewed_at = :reviewed_at, ingestion_batch_id = :batch_id,
                updated_at = :reviewed_at
            WHERE id = :proposal_id
            """
        ),
        {
            "reviewer": reviewer_id,
            "reviewed_at": _NOW,
            "batch_id": _BATCH,
            "proposal_id": _PROPOSAL,
        },
    )


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_behavioral_proposals_have_forced_rls_and_immutable_review_evidence(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    assert migrated_postgres_schema.tables({_TABLE}) == {_TABLE}
    assert migrated_postgres_schema.policies({_TABLE}) == {_TABLE + "_tenant_isolation"}
    assert migrated_postgres_schema.indexes({"ix_behavioral_model_proposals_scope_status"}) == {
        "ix_behavioral_model_proposals_scope_status"
    }
    with migrated_postgres_schema.app_engine.begin() as connection:
        _set_tenant(connection)
        row_security = connection.execute(
            text(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name AND c.relname = :table_name
                """
            ),
            {"schema_name": migrated_postgres_schema.schema_name, "table_name": _TABLE},
        ).one()
        assert tuple(row_security) == (True, True)
        qualified_table = f"{migrated_postgres_schema.schema_name}.{_TABLE}"
        column_types = connection.execute(
            text(
                """
                SELECT attname, format_type(atttypid, atttypmod)
                FROM pg_attribute
                WHERE attrelid = CAST(:table_name AS regclass)
                  AND attname IN ('id', 'values', 'proposed_by_user_id',
                                  'reviewed_by_user_id', 'ingestion_batch_id')
                ORDER BY attname
                """
            ),
            {"table_name": qualified_table},
        ).all()
        assert column_types == [
            ("id", "uuid"),
            ("ingestion_batch_id", "uuid"),
            ("proposed_by_user_id", "uuid"),
            ("reviewed_by_user_id", "uuid"),
            ("values", "jsonb"),
        ]
        for role in ("aqs_api", "aqs_worker"):
            privileges = connection.execute(
                text(
                    """
                    SELECT has_table_privilege(:role, :table_name, 'SELECT'),
                           has_table_privilege(:role, :table_name, 'INSERT'),
                           has_table_privilege(:role, :table_name, 'UPDATE'),
                           has_table_privilege(:role, :table_name, 'DELETE'),
                           has_table_privilege(:role, :table_name, 'TRUNCATE')
                    """
                ),
                {"role": role, "table_name": qualified_table},
            ).one()
            assert tuple(privileges) == (True, True, True, False, False)
        _seed_proposal(connection)

    with (
        pytest.raises(DBAPIError, match="values and provenance are immutable"),
        migrated_postgres_schema.app_engine.begin() as connection,
    ):
        _set_tenant(connection)
        connection.execute(
            text(
                "UPDATE behavioral_model_proposals SET model_version = 'tampered' "
                "WHERE id = :proposal_id"
            ),
            {"proposal_id": _PROPOSAL},
        )

    with (
        pytest.raises(DBAPIError, match="requires a distinct reviewer"),
        migrated_postgres_schema.app_engine.begin() as connection,
    ):
        _set_tenant(connection)
        _accept(connection, _PROPOSER)

    with migrated_postgres_schema.app_engine.begin() as connection:
        _set_tenant(connection)
        _accept(connection, _REVIEWER)
        accepted_mapping = (
            connection.execute(
                text(
                    "SELECT status, reviewed_by_user_id, ingestion_batch_id, values "
                    "FROM behavioral_model_proposals WHERE id = :proposal_id"
                ),
                {"proposal_id": _PROPOSAL},
            )
            .mappings()
            .one()
        )
        accepted = _AcceptedProposalRow.model_validate(accepted_mapping)
        assert accepted.status == "accepted"
        assert accepted.reviewed_by_user_id == _REVIEWER
        assert accepted.ingestion_batch_id == _BATCH
        assert accepted.values[0]["value"] == 36.5

    with (
        pytest.raises(DBAPIError, match="proposal history is immutable"),
        migrated_postgres_schema.app_engine.begin() as connection,
    ):
        _set_tenant(connection)
        connection.execute(
            text("DELETE FROM behavioral_model_proposals WHERE id = :proposal_id"),
            {"proposal_id": _PROPOSAL},
        )
