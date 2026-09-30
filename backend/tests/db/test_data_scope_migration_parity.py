"""The data-scope columns: the model and the migrated schema must be one thing.

The hermetic suite builds its schema with ``Base.metadata.create_all``, so a
model that disagrees with migration ``202609270073`` / ``202609270074`` is green
there and wrong in every deployment. Two suites already compare parts of this
table; neither reaches the two new columns or the two new CHECKs, so this one
does — and it exercises the constraints by INSERTING the refused shapes rather
than by reading their text, because a constraint's definition being present is
not evidence that it fires.

The same eleven shapes run in the hermetic suite through the ORM
(``tests/db/test_data_scope_check_constraints.py``), so the guarantee is proven
on both dialects rather than assumed from ``json_array_length`` existing in each.

Two deliberate shapes of this file:

* Everything runs on the FORWARD-only fixture. The ``bi_reader`` row this suite
  inserts is exactly what migration ``202609270074``'s downgrade refuses to lose
  the constraint over, so downgrading with one present fails BY DESIGN — that is
  the migration working, not a fault to route around.
* The assertions are gathered into two tests rather than fifteen. Each fixture
  instance migrates a fresh schema from base to head, which costs about two
  minutes; a parametrised case per shape would have spent half an hour proving
  one CHECK.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.authorization import MACHINE_ROLE_BUNDLES, DataScope, RoleBundle
from app.db.base import Base
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    forward_migrated_postgres_schema,
)

__all__ = ["forward_migrated_postgres_schema"]

pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE_URL") is None,
        reason="TEST_DATABASE_URL is required for Postgres migration tests.",
    ),
]

_TABLE = "authorization_bindings"
_NEW_COLUMNS = ("data_scope_kind", "data_scope_values")
_NEW_CONSTRAINTS = {
    "ck_authorization_bindings_data_scope_kind",
    "ck_authorization_bindings_data_scope_values",
}

ORG = "OR-DSCOPEPG"
PRINCIPAL = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
MACHINE = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)

#: ``(kind, values as JSON text, role bundle, principal type, admitted?)``.
_CASES: tuple[tuple[str, str | None, str, str, bool], ...] = (
    # Admitted.
    ("all", None, "viewer", "human", True),
    ("branch", '["ACC-001", "TEM-002"]', "viewer", "human", True),
    ("region", '["Greater Accra"]', "viewer", "human", True),
    ("all", None, "bi_reader", "machine", True),
    # Refused. The empty list is THE safety case: a reader written as
    # ``if values: inject a filter`` would serve an empty-list principal the
    # whole book, so "no branches" must be unstorable.
    ("all", '["ACC-001"]', "viewer", "human", False),
    ("branch", None, "viewer", "human", False),
    ("branch", "[]", "viewer", "human", False),
    ("region", "[]", "viewer", "human", False),
    ("mixed", '["ACC-001"]', "viewer", "human", False),
    ("all", None, "bi_reader", "human", False),
    ("all", None, "viewer", "machine", False),
)


def test_the_new_columns_and_constraints_match_the_model(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """Column parity, constraint presence, the admitted vocabulary, and RLS.

    Note what column parity does NOT cover: whether the ORM writes SQL NULL or
    the JSON value ``null`` into the nullable column. That is a property of the
    model's ``JSON(none_as_null=True)`` and of nothing the migration can express
    — the migrated column looks identical either way — so it is asserted in
    ``tests/models/test_nullable_json_is_sql_null.py`` instead. It is named here
    because a reader could reasonably assume "parity" included it; it does not,
    and getting it wrong refuses every binding the product writes.
    """
    schema = forward_migrated_postgres_schema
    assert schema.constraints(_NEW_CONSTRAINTS) == _NEW_CONSTRAINTS

    model = Base.metadata.tables[_TABLE].columns
    migrated = {
        column["name"]: column
        for column in inspect(schema.app_engine).get_columns(_TABLE, schema=schema.schema_name)
    }
    for name in _NEW_COLUMNS:
        assert name in migrated, f"{name} is absent from the migrated schema"
        assert migrated[name]["nullable"] == model[name].nullable, name
        assert str(migrated[name]["type"]).lower() == str(model[name].type).lower(), name
    assert "'all'" in str(migrated["data_scope_kind"]["default"]), (
        "the server default must stay: a binding written by any path that has not "
        "been taught about data scopes must mean the whole institution"
    )
    assert migrated["data_scope_values"]["default"] is None

    kinds = _definition(schema, "ck_authorization_bindings_data_scope_kind")
    for kind in DataScope:
        assert f"'{kind.value}'" in kinds, kind
    for derived in ("mixed", "none"):
        assert f"'{derived}'" not in kinds, (
            f"{derived} describes a derived union and must not be storable on a row"
        )

    bundles = _definition(schema, "ck_authorization_bindings_principal_bundle")
    for bundle in MACHINE_ROLE_BUNDLES:
        assert f"'{bundle.value}'" in bundles, bundle
    assert f"'{RoleBundle.VIEWER.value}'" not in bundles, (
        "a human bundle named here would make the constraint a list rather than "
        "the complement of the machine set"
    )
    assert f"'{RoleBundle.BI_READER.value}'" in _definition(
        schema, "ck_authorization_bindings_role_bundle"
    )

    # Adding columns must not have disturbed the table's tenant isolation.
    with schema.app_engine.connect() as connection:
        forced = connection.scalar(
            text(
                "SELECT c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND c.relname = :table"
            ),
            {"schema": schema.schema_name, "table": _TABLE},
        )
    assert forced is True
    assert schema.policies({_TABLE}) == {"authorization_bindings_tenant_isolation"}


def test_the_migrated_constraints_admit_and_refuse_the_documented_shapes(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """Each shape in its own transaction, against the schema a deployment gets."""
    engine = forward_migrated_postgres_schema.app_engine
    with engine.begin() as connection:
        _seed_tenant(connection)

    refused: list[str] = []
    admitted: list[str] = []
    for kind, values, role_bundle, principal_type, _expected in _CASES:
        label = f"{principal_type}/{role_bundle} {kind}={values}"
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": ORG},
            )
            try:
                _insert_binding(
                    connection,
                    kind=kind,
                    values=values,
                    role_bundle=role_bundle,
                    principal_type=principal_type,
                )
            except IntegrityError:
                refused.append(label)
                continue
        admitted.append(label)

    expected_admitted = [
        f"{principal_type}/{role_bundle} {kind}={values}"
        for kind, values, role_bundle, principal_type, ok in _CASES
        if ok
    ]
    expected_refused = [
        f"{principal_type}/{role_bundle} {kind}={values}"
        for kind, values, role_bundle, principal_type, ok in _CASES
        if not ok
    ]
    assert admitted == expected_admitted
    assert refused == expected_refused, (
        "the migrated CHECK constraints did not refuse these shapes; a constraint "
        "that never fires is not a constraint"
    )


def _definition(schema: MigratedPostgresSchema, name: str) -> str:
    with schema.app_engine.connect() as connection:
        return connection.execute(
            # Scoped to THIS migrated schema: the disposable test schemas are
            # created alongside each other, so a constraint name alone is not
            # unique across the database.
            text(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE c.conname = :name AND n.nspname = :schema"
            ),
            {"name": name, "schema": schema.schema_name},
        ).scalar_one()


def _seed_tenant(connection) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": ORG},
    )
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, created_at, updated_at) "
            "VALUES (:id, :name, :now, :now)"
        ),
        {"id": ORG, "name": "Data scope constraint proof", "now": NOW},
    )
    for user_id, email, provider in (
        (PRINCIPAL, "scope.human@example.test", "password"),
        (MACHINE, "scope.feed@service.aequoros.invalid", "service"),
    ):
        connection.execute(
            text(
                """
                INSERT INTO users
                    (id, organization_id, email, is_active, role, auth_provider,
                     failed_login_attempts, authorization_version, created_at, updated_at)
                VALUES
                    (:id, :organization_id, :email, true, 'viewer', :provider,
                     0, 1, :now, :now)
                """
            ),
            {
                "id": user_id,
                "organization_id": ORG,
                "email": email,
                "provider": provider,
                "now": NOW,
            },
        )


def _insert_binding(  # noqa: PLR0913 - one complete row is explicit
    connection,
    *,
    kind: str,
    values: str | None,
    role_bundle: str,
    principal_type: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO authorization_bindings
                (id, organization_id, principal_user_id, principal_type, role_bundle,
                 institution_scope, institution_id, module_scope, sensitivity_scope,
                 data_scope_kind, data_scope_values, granted_by_type, granted_by_id,
                 grant_reason, granted_at, status, valid_from, created_at, updated_at)
            VALUES
                (:id, :organization_id, :principal, :principal_type, :role_bundle,
                 'organization', NULL, 'all', 'all',
                 :kind, CAST(:values AS json), 'system', 'data-scope-parity',
                 'exercise the migrated constraints', :now, 'active', :now, :now, :now)
            """
        ),
        {
            "id": uuid4(),
            "organization_id": ORG,
            "principal": MACHINE if principal_type == "machine" else PRINCIPAL,
            "principal_type": principal_type,
            "role_bundle": role_bundle,
            "kind": kind,
            "values": values,
            "now": NOW,
        },
    )
