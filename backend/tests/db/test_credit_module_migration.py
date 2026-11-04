"""Postgres proof for the CREDIT module migration (``202609220067``).

Two things are proven on a MIGRATED database, because neither can be seen on
the hermetic ``create_all`` schema:

* enum ↔ CHECK parity for ``module_scope`` — the model derives the constraint
  from ``tuple(ModuleScope)`` while the migrated constraint is a literal, and
  the two disagreeing is exactly the live 500 ``202609200065`` closed for the
  role-bundle constraint. The parity test is written against the enum PLUS the
  literal ``credit``, so it also holds in a process where the enum has not yet
  grown the member;
* the ``risk`` → ``credit`` mirror — which rows are copied, which are skipped,
  and the session invalidation and audit evidence that ride with a grant.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from alembic import command
from app.core.authorization import ModuleScope
from tests.db.test_initial_owner_migration import (
    _insert_organization,
    _insert_refresh_token,
    _insert_user,
)
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE_URL") is None,
        reason="TEST_DATABASE_URL is required for Postgres migration tests.",
    ),
]

_CONSTRAINT = "ck_authorization_bindings_module_scope"
REVISION = "202609220067"
PREVIOUS_REVISION = "202609220066"
GRANTOR = "migration:202609220067"
REASON_PREFIX = "mirrored by migration 202609220067 from risk"
ORG = "OR-CRED00001"
BANK = "BK-CRED00001"


def _set_tenant(connection: Connection, organization_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": organization_id},
    )


def _insert_bank(connection: Connection, *, now: datetime) -> None:
    connection.execute(
        text(
            """
            INSERT INTO banks
              (id, organization_id, name, short_name, currency, jurisdiction_code,
               license_type, institution_type, created_at, updated_at)
            VALUES
              (:bank_id, :organization_id, 'Credit proof bank', 'CRP', 'GHS', 'GH',
               'universal', 'universal_bank', :now, :now)
            """
        ),
        {"bank_id": BANK, "organization_id": ORG, "now": now},
    )


def _insert_binding(  # noqa: PLR0913 - every scope dimension is explicit on purpose
    connection: Connection,
    *,
    user_id: UUID,
    principal_type: str = "human",
    role_bundle: str,
    institution_scope: str,
    institution_id: str | None,
    module_scope: str,
    sensitivity_scope: str,
    status: str = "active",
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    now: datetime,
) -> UUID:
    binding_id = uuid4()
    revoked = status == "revoked"
    connection.execute(
        text(
            """
            INSERT INTO authorization_bindings
                (id, organization_id, principal_user_id, principal_type, role_bundle,
                 institution_scope, institution_id, module_scope, sensitivity_scope,
                 granted_by_type, granted_by_id, grant_reason, granted_at, status,
                 valid_from, valid_until, revoked_at, revoked_by_type, revoked_by_id,
                 revoked_reason, created_at, updated_at)
            VALUES
                (:id, :organization_id, :user_id, :principal_type, :role_bundle,
                 :institution_scope, :institution_id, :module_scope, :sensitivity_scope,
                 'tenant_user', 'migration-test:preexisting', 'pre-existing row',
                 :now, :status, :valid_from, :valid_until, :revoked_at, :revoked_by_type,
                 :revoked_by_id, :revoked_reason, :now, :now)
            """
        ),
        {
            "id": binding_id,
            "organization_id": ORG,
            "user_id": user_id,
            "principal_type": principal_type,
            "role_bundle": role_bundle,
            "institution_scope": institution_scope,
            "institution_id": institution_id,
            "module_scope": module_scope,
            "sensitivity_scope": sensitivity_scope,
            "now": now,
            "status": status,
            "valid_from": now if valid_from is None else valid_from,
            "valid_until": valid_until,
            "revoked_at": now if revoked else None,
            "revoked_by_type": "system" if revoked else None,
            "revoked_by_id": "migration-test:revoker" if revoked else None,
            "revoked_reason": "revoked before the mirror" if revoked else None,
        },
    )
    return binding_id


def _credit_rows(schema: MigratedPostgresSchema) -> list[dict]:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, ORG)
        return [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT principal_user_id, principal_type, role_bundle, institution_scope,
                           institution_id, sensitivity_scope, granted_by_type, granted_by_id,
                           grant_reason, status, valid_until
                    FROM authorization_bindings
                    WHERE organization_id = :organization_id AND module_scope = 'credit'
                    ORDER BY principal_user_id, role_bundle, institution_id
                    """
                ),
                {"organization_id": ORG},
            ).mappings()
        ]


def _user_versions(schema: MigratedPostgresSchema) -> dict[str, int]:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, ORG)
        return {
            str(row.email): int(row.authorization_version)
            for row in connection.execute(
                text(
                    "SELECT email, authorization_version FROM users "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": ORG},
            ).all()
        }


def test_the_module_scope_constraint_accepts_every_module_scope(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    definition = _constraint_definition(migrated_postgres_schema)

    # ``credit`` is asserted by literal as well as through the enum: the enum
    # member lands in a sibling change, and the database must admit the value
    # either way.
    expected = {scope.value for scope in ModuleScope} | {"credit"}
    missing = sorted(value for value in expected if f"'{value}'" not in definition)
    assert not missing, (
        f"{_CONSTRAINT} rejects {missing}. A new ModuleScope needs a migration "
        "widening this constraint — the model derives it from the enum, so the "
        "hermetic suite will not catch this."
    )


@dataclass(frozen=True)
class _Principals:
    """One user per skip/mirror rule the migration implements."""

    mirrored_analyst: UUID  # institution risk/confidential analyst → mirrored
    org_wide_viewer: UUID  # organization-wide risk viewer → mirrored, org-wide
    covered_by_all: UUID  # risk row plus an `all` row → not duplicated
    already_credit: UUID  # risk row plus an equivalent credit row → skipped
    expired_risk: UUID  # risk row past valid_until → skipped
    revoked_risk: UUID  # revoked risk row → skipped
    expired_all: UUID  # risk row plus an EXPIRED `all` row → mirrored (A4-04)
    machine: UUID  # service identity → skipped


_LABELS = {
    "mirrored_analyst": "analyst",
    "org_wide_viewer": "orgviewer",
    "covered_by_all": "coveredall",
    "already_credit": "alreadycredit",
    "expired_risk": "expired",
    "revoked_risk": "revoked",
    "expired_all": "expiredall",
}


def _seed_fixture(schema: MigratedPostgresSchema, *, now: datetime) -> _Principals:
    principals = _Principals(*(uuid4() for _ in range(8)))
    with schema.app_engine.begin() as connection:
        _insert_organization(connection, ORG, now)
        _insert_bank(connection, now=now)
        for field_name, label in _LABELS.items():
            _insert_user(
                connection,
                organization_id=ORG,
                user_id=getattr(principals, field_name),
                email=f"{label}@credit.example",
                display_name=label.title(),
                role="viewer",
                now=now,
            )
        _insert_user(
            connection,
            organization_id=ORG,
            user_id=principals.machine,
            email="machine@credit.example",
            display_name="Machine",
            role="viewer",
            auth_provider="service",
            now=now,
        )
        for user_id in (
            principals.mirrored_analyst,
            principals.org_wide_viewer,
            principals.covered_by_all,
            principals.already_credit,
            principals.expired_all,
        ):
            _insert_refresh_token(
                connection, organization_id=ORG, user_id=user_id, token_id=uuid4(), now=now
            )

        institution: dict[str, Any] = {"institution_scope": "institution", "institution_id": BANK}
        organization: dict[str, Any] = {"institution_scope": "organization", "institution_id": None}
        rows: tuple[dict[str, Any], ...] = (
            {
                "user_id": principals.mirrored_analyst,
                "role_bundle": "analyst",
                **institution,
                "module_scope": "risk",
                "sensitivity_scope": "confidential",
            },
            {
                "user_id": principals.org_wide_viewer,
                "role_bundle": "viewer",
                **organization,
                "module_scope": "risk",
                "sensitivity_scope": "aggregated",
            },
            {
                "user_id": principals.covered_by_all,
                "role_bundle": "viewer",
                **institution,
                "module_scope": "risk",
                "sensitivity_scope": "all",
            },
            {
                "user_id": principals.covered_by_all,
                "role_bundle": "viewer",
                **organization,
                "module_scope": "all",
                "sensitivity_scope": "all",
            },
            {
                "user_id": principals.already_credit,
                "role_bundle": "approver",
                **institution,
                "module_scope": "risk",
                "sensitivity_scope": "confidential",
            },
            {
                "user_id": principals.expired_risk,
                "role_bundle": "viewer",
                **organization,
                "module_scope": "risk",
                "sensitivity_scope": "all",
                "valid_from": now - timedelta(days=30),
                "valid_until": now - timedelta(days=1),
            },
            {
                "user_id": principals.revoked_risk,
                "role_bundle": "viewer",
                **organization,
                "module_scope": "risk",
                "sensitivity_scope": "all",
                "status": "revoked",
            },
            # An expired-but-unrevoked `all` row grants nothing (the evaluator
            # ignores a binding past valid_until), so it must not mask the
            # mirror of the live `risk` row beside it.
            {
                "user_id": principals.expired_all,
                "role_bundle": "viewer",
                **institution,
                "module_scope": "risk",
                "sensitivity_scope": "aggregated",
            },
            {
                "user_id": principals.expired_all,
                "role_bundle": "viewer",
                **organization,
                "module_scope": "all",
                "sensitivity_scope": "all",
                "valid_from": now - timedelta(days=30),
                "valid_until": now - timedelta(days=1),
            },
            {
                "user_id": principals.machine,
                "principal_type": "machine",
                "role_bundle": "integration_writer",
                **institution,
                "module_scope": "risk",
                "sensitivity_scope": "restricted",
            },
        )
        for row in rows:
            _insert_binding(connection, now=now, **row)

    # A pre-existing ``credit`` row cannot be inserted while the old CHECK is in
    # force — that is the point of the migration — so widen it by hand for the
    # one row that proves the "already covered by credit" skip.
    with schema.app_engine.begin() as connection:
        connection.execute(
            text(f"ALTER TABLE authorization_bindings DROP CONSTRAINT {_CONSTRAINT}")
        )
        _set_tenant(connection, ORG)
        _insert_binding(
            connection,
            user_id=principals.already_credit,
            role_bundle="approver",
            institution_scope="institution",
            institution_id=BANK,
            module_scope="credit",
            sensitivity_scope="confidential",
            now=now,
        )
        connection.execute(
            text(
                f"ALTER TABLE authorization_bindings ADD CONSTRAINT {_CONSTRAINT} "
                "CHECK (module_scope IN ('all', 'liq', 'cap', 'irrbb', 'fx', 'ftp', 'fcst', "
                "'beh', 'data', 'reg', 'risk', 'markets', 'account', 'audit', 'credit'))"
            )
        )
    return principals


def _grant_evidence(schema: MigratedPostgresSchema) -> tuple[set[UUID], list[dict]]:
    """Refresh families revoked for authorization change, and the grant audit rows."""
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, ORG)
        revoked_for = set(
            connection.execute(
                text(
                    "SELECT user_id FROM refresh_tokens WHERE organization_id = :organization_id "
                    "AND revoked_reason = 'authorization_changed'"
                ),
                {"organization_id": ORG},
            ).scalars()
        )
        audit = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT details ->> 'grantee_user_id' AS grantee,
                           details ->> 'role_bundle' AS role_bundle,
                           details -> 'scope' ->> 'module_scope' AS module_scope,
                           details ->> 'grantor_id' AS grantor_id,
                           details ->> 'authority_sentence' AS sentence
                    FROM audit_events
                    WHERE organization_id = :organization_id
                      AND event_type = 'authorization.binding_granted'
                      AND entity_type = 'authorization_binding'
                    ORDER BY grantee
                    """
                ),
                {"organization_id": ORG},
            ).mappings()
        ]
    return revoked_for, audit


def _constraint_definition(schema: MigratedPostgresSchema) -> str:
    with schema.app_engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE c.conname = :name AND n.nspname = :schema"
            ),
            {"name": _CONSTRAINT, "schema": schema.schema_name},
        ).scalar_one()


def test_risk_bindings_are_mirrored_to_credit_with_invalidation_and_audit(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    config = alembic_config_for_app()
    command.downgrade(config, PREVIOUS_REVISION)
    clear_database_caches()
    now = datetime.now(UTC)
    principals = _seed_fixture(migrated_postgres_schema, now=now)
    versions_before = _user_versions(migrated_postgres_schema)

    command.upgrade(config, REVISION)
    clear_database_caches()

    rows = _credit_rows(migrated_postgres_schema)
    mirrored = {row["principal_user_id"]: row for row in rows if row["granted_by_id"] == GRANTOR}
    assert set(mirrored) == {
        principals.mirrored_analyst,
        principals.org_wide_viewer,
        principals.expired_all,
    }
    analyst_row = mirrored[principals.mirrored_analyst]
    assert (
        analyst_row["principal_type"],
        analyst_row["role_bundle"],
        analyst_row["institution_scope"],
        analyst_row["institution_id"],
        analyst_row["sensitivity_scope"],
        analyst_row["granted_by_type"],
        analyst_row["status"],
        analyst_row["valid_until"],
    ) == ("human", "analyst", "institution", BANK, "confidential", "system", "active", None)
    assert analyst_row["grant_reason"].startswith(REASON_PREFIX)
    assert analyst_row["grant_reason"].endswith(": pre-existing row")
    viewer_row = mirrored[principals.org_wide_viewer]
    assert (viewer_row["institution_scope"], viewer_row["institution_id"]) == ("organization", None)
    assert viewer_row["sensitivity_scope"] == "aggregated"
    expired_all_row = mirrored[principals.expired_all]
    assert (
        expired_all_row["institution_scope"],
        expired_all_row["institution_id"],
        expired_all_row["sensitivity_scope"],
        expired_all_row["valid_until"],
    ) == ("institution", BANK, "aggregated", None)
    # The pre-existing credit row survives untouched and was not duplicated.
    assert [row["principal_user_id"] for row in rows if row["granted_by_id"] != GRANTOR] == [
        principals.already_credit
    ]

    versions_after = _user_versions(migrated_postgres_schema)
    bumped = {email for email in versions_after if versions_after[email] > versions_before[email]}
    assert bumped == {
        "analyst@credit.example",
        "orgviewer@credit.example",
        "expiredall@credit.example",
    }

    revoked_for, audit = _grant_evidence(migrated_postgres_schema)
    assert revoked_for == {
        principals.mirrored_analyst,
        principals.org_wide_viewer,
        principals.expired_all,
    }
    assert {
        (row["grantee"], row["role_bundle"], row["module_scope"], row["grantor_id"])
        for row in audit
    } == {
        (str(principals.mirrored_analyst), "analyst", "credit", GRANTOR),
        (str(principals.org_wide_viewer), "viewer", "credit", GRANTOR),
        (str(principals.expired_all), "viewer", "credit", GRANTOR),
    }
    assert all("credit module" in row["sentence"] for row in audit)

    command.downgrade(config, PREVIOUS_REVISION)
    clear_database_caches()

    with migrated_postgres_schema.app_engine.begin() as connection:
        _set_tenant(connection, ORG)
        remaining = {
            key: value
            for key, value in connection.execute(
                text(
                    "SELECT module_scope, count(*) FROM authorization_bindings "
                    "WHERE organization_id = :organization_id GROUP BY module_scope"
                ),
                {"organization_id": ORG},
            ).tuples()
        }
    # Every credit row is gone — the mirrored two and the pre-existing one the
    # narrowed constraint could not admit — and nothing else moved.
    assert remaining == {"risk": 8, "all": 2}
    assert "'credit'" not in _constraint_definition(migrated_postgres_schema)
    versions_after_downgrade = _user_versions(migrated_postgres_schema)
    assert (
        versions_after_downgrade["alreadycredit@credit.example"]
        > versions_after["alreadycredit@credit.example"]
    )

    # Historical auth-provider constraints predate service identities, so
    # normalise only that synthetic fixture dimension before the shared fixture
    # crosses them on its way to base.
    with migrated_postgres_schema.app_engine.begin() as connection:
        _set_tenant(connection, ORG)
        connection.execute(
            text(
                "UPDATE users SET auth_provider = 'password' "
                "WHERE organization_id = :organization_id AND auth_provider = 'service'"
            ),
            {"organization_id": ORG},
        )
    command.upgrade(config, "head")
    clear_database_caches()
