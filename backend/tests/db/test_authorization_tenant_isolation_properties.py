"""Property proof that product reads cannot cross the Postgres tenant boundary.

The route set comes from FastAPI itself: every GET route below
``/api/v1/banks/{bank_id}``, plus organization-scoped listing routes.  Path and
required query values are derived from route metadata so a new read surface
joins the property automatically instead of waiting for a hand-maintained list.

This proof needs a migrated Postgres schema with FORCE RLS.  Every tenant setup
or reset sets ``app.organization_id`` in the same transaction.  Users and
organizations are never deleted during example reset; only generated binding
rows are replaced.
"""

from __future__ import annotations

import os
from datetime import date
from enum import Enum
from typing import Any, Literal, get_args, get_origin
from uuid import UUID, uuid5

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel
from pydantic_core import PydanticUndefined
from sqlalchemy import text

from app.core.authorization import (
    InstitutionScope,
    ModuleScope,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.session import get_engine
from app.features.ingest_data import get_ingestion_storage
from app.integrations.storage.s3 import get_object_storage
from app.main import create_app
from tests.api.helpers import headers
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    clear_database_caches,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres RLS authorization properties.",
)

_ORG_A = "OR-RLSPRP01"
_ORG_B = "OR-RLSPRP02"
_BANK_A = "BK-RLSPRP01"
_BANK_B = "BK-RLSPRP02"
_USER_NAMESPACE = UUID("2f352abc-f5f9-4ef4-8d68-e7bcf01197a1")
_BINDING_NAMESPACE = UUID("20d962b5-87dc-4169-b3d2-272745d4edcf")
_USERS_A = tuple(uuid5(_USER_NAMESPACE, f"{_ORG_A}:{index}") for index in range(3))
_USERS_B = tuple(uuid5(_USER_NAMESPACE, f"{_ORG_B}:{index}") for index in range(3))
_FACT_A = UUID("30000000-0000-4000-8000-000000000001")
_FACT_B = UUID("30000000-0000-4000-8000-000000000002")
_PERIOD_B = UUID("40000000-0000-4000-8000-000000000001")
_PERIOD_FACT_B = UUID("40000000-0000-4000-8000-000000000002")
_UNKNOWN_UUID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
_B_ROW_MARKER = "RLS-TENANT-B-ROW-MARKER"

_GENERATED_ROLES = (
    RoleBundle.VIEWER,
    RoleBundle.AUDITOR,
    RoleBundle.ANALYST,
    RoleBundle.APPROVER,
    RoleBundle.ACCOUNT_ADMIN,
)
_BINDING_SPEC = st.tuples(
    st.integers(min_value=0, max_value=2),
    st.sampled_from(_GENERATED_ROLES),
    st.sampled_from(tuple(ModuleScope)),
    st.sampled_from(tuple(SensitivityScope)),
    st.sampled_from(tuple(InstitutionScope)),
)
_BINDING_SETS = st.tuples(
    st.lists(_BINDING_SPEC, min_size=0, max_size=6),
    st.lists(_BINDING_SPEC, min_size=0, max_size=6),
)


def _set_tenant(connection, organization_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": organization_id},
    )


def _seed_tenant(
    schema: MigratedPostgresSchema,
    *,
    organization_id: str,
    bank_id: str,
    user_ids: tuple[UUID, ...],
    fact_id: UUID,
) -> None:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, organization_id)
        connection.execute(
            text(
                """
                INSERT INTO organizations (id, name, created_at, updated_at)
                VALUES (:organization_id, :name, now(), now())
                """
            ),
            {
                "organization_id": organization_id,
                "name": (
                    _B_ROW_MARKER
                    if organization_id == _ORG_B
                    else f"RLS property tenant {organization_id}"
                ),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO banks
                  (id, organization_id, name, short_name, currency, jurisdiction_code,
                   license_type, institution_type, created_at, updated_at)
                VALUES
                  (:bank_id, :organization_id, :name, :short_name, 'GHS', 'GH',
                   'universal_bank', 'universal_bank', now(), now())
                """
            ),
            {
                "bank_id": bank_id,
                "organization_id": organization_id,
                "name": (
                    _B_ROW_MARKER if organization_id == _ORG_B else f"RLS property bank {bank_id}"
                ),
                "short_name": bank_id,
            },
        )
        for index, user_id in enumerate(user_ids):
            connection.execute(
                text(
                    """
                    INSERT INTO users
                      (id, organization_id, email, display_name, is_active, role,
                       auth_provider, failed_login_attempts, authorization_version,
                       created_at, updated_at)
                    VALUES
                      (:user_id, :organization_id, :email, :display_name, true,
                       'viewer', 'password', 0, 1, now(), now())
                    """
                ),
                {
                    "user_id": user_id,
                    "organization_id": organization_id,
                    "email": f"rls-{index}@{organization_id.lower()}.example",
                    "display_name": f"RLS Principal {organization_id} {index}",
                },
            )
        connection.execute(
            text(
                """
                INSERT INTO current_financial_facts
                  (id, organization_id, bank_id, source_as_of_date, source_generation,
                   fact_group, category, amount, currency, attributes, created_at, updated_at)
                VALUES
                  (:fact_id, :organization_id, :bank_id, DATE '2026-09-18', 1,
                   'balance_sheet', :category, 12345, 'GHS', '{}', now(), now())
                """
            ),
            {
                "fact_id": fact_id,
                "organization_id": organization_id,
                "bank_id": bank_id,
                "category": f"rls_property_{organization_id.lower()}",
            },
        )

        if organization_id == _ORG_B:
            connection.execute(
                text(
                    """
                    INSERT INTO bank_reporting_periods
                      (id, organization_id, bank_id, period_start, period_end,
                       label, status, created_at, updated_at)
                    VALUES (:id, :org, :bank, DATE '2026-09-01', DATE '2026-09-18',
                            :label, 'open', now(), now())
                    """
                ),
                {"id": _PERIOD_B, "org": _ORG_B, "bank": _BANK_B, "label": _B_ROW_MARKER},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO bank_financial_facts
                      (id, organization_id, bank_id, reporting_period_id, fact_group,
                       category, amount, currency, created_at, updated_at)
                    VALUES (:id, :org, :bank, :period, 'balance_sheet', :category,
                            12345, 'GHS', now(), now())
                    """
                ),
                {
                    "id": _PERIOD_FACT_B,
                    "org": _ORG_B,
                    "bank": _BANK_B,
                    "period": _PERIOD_B,
                    "category": _B_ROW_MARKER,
                },
            )


def _binding_id(organization_id: str, index: int, suffix: str) -> UUID:
    return uuid5(_BINDING_NAMESPACE, f"{organization_id}:{index}:{suffix}")


def _insert_binding(  # noqa: PLR0913 - the complete binding sentence is explicit
    connection,
    *,
    binding_id: UUID,
    organization_id: str,
    user_id: UUID,
    role: RoleBundle,
    module_scope: ModuleScope,
    sensitivity_scope: SensitivityScope,
    institution_scope: InstitutionScope,
    bank_id: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO authorization_bindings
              (id, organization_id, principal_user_id, principal_type, role_bundle,
               institution_scope, institution_id, module_scope, sensitivity_scope,
               granted_by_type, granted_by_id, grant_reason, granted_at, status,
               valid_from, created_at, updated_at)
            VALUES
              (:id, :organization_id, :principal_user_id, 'human', :role_bundle,
               :institution_scope, :institution_id, :module_scope, :sensitivity_scope,
               'system', 'rls-property', 'RLS authorization property binding',
               now(), 'active', now(), now(), now())
            """
        ),
        {
            "id": binding_id,
            "organization_id": organization_id,
            "principal_user_id": user_id,
            "role_bundle": role.value,
            "institution_scope": institution_scope.value,
            "institution_id": (
                None if institution_scope is InstitutionScope.ORGANIZATION else bank_id
            ),
            "module_scope": module_scope.value,
            "sensitivity_scope": sensitivity_scope.value,
        },
    )


def _replace_generated_bindings(
    schema: MigratedPostgresSchema,
    *,
    organization_id: str,
    bank_id: str,
    user_ids: tuple[UUID, ...],
    specs: list[tuple[int, RoleBundle, ModuleScope, SensitivityScope, InstitutionScope]],
) -> set[str]:
    binding_ids: set[str] = set()
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, organization_id)
        connection.execute(
            text("DELETE FROM authorization_bindings WHERE organization_id = :organization_id"),
            {"organization_id": organization_id},
        )
        fixed = (
            (
                user_ids[0],
                RoleBundle.ORG_OWNER,
                ModuleScope.ACCOUNT,
                SensitivityScope.ALL,
            ),
            (
                user_ids[0],
                RoleBundle.VIEWER,
                ModuleScope.ALL,
                SensitivityScope.ALL,
            ),
        )
        for index, (user_id, role, module, sensitivity) in enumerate(fixed):
            binding_id = _binding_id(organization_id, index, "fixed")
            _insert_binding(
                connection,
                binding_id=binding_id,
                organization_id=organization_id,
                user_id=user_id,
                role=role,
                module_scope=module,
                sensitivity_scope=sensitivity,
                institution_scope=InstitutionScope.ORGANIZATION,
                bank_id=bank_id,
            )
            binding_ids.add(str(binding_id))

        for index, (user_slot, role, module, sensitivity, institution_scope) in enumerate(
            specs,
            start=len(fixed),
        ):
            effective_module = ModuleScope.ACCOUNT if role is RoleBundle.ACCOUNT_ADMIN else module
            effective_sensitivity = (
                SensitivityScope.ALL if role is RoleBundle.ACCOUNT_ADMIN else sensitivity
            )
            effective_institution_scope = (
                InstitutionScope.ORGANIZATION
                if role is RoleBundle.ACCOUNT_ADMIN
                else institution_scope
            )
            binding_id = _binding_id(organization_id, index, "generated")
            _insert_binding(
                connection,
                binding_id=binding_id,
                organization_id=organization_id,
                user_id=user_ids[user_slot],
                role=role,
                module_scope=effective_module,
                sensitivity_scope=effective_sensitivity,
                institution_scope=effective_institution_scope,
                bank_id=bank_id,
            )
            binding_ids.add(str(binding_id))
    return binding_ids


def _product_read_routes(app: FastAPI) -> tuple[list[APIRoute], list[APIRoute]]:
    bank_routes: list[APIRoute] = []
    organization_routes: list[APIRoute] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if route.path.startswith("/api/v1/banks/{bank_id}"):
            bank_routes.append(route)
        elif route.path == "/api/v1/banks" or route.path.startswith("/api/v1/organization/"):
            organization_routes.append(route)
    assert bank_routes, "the FastAPI bank read-route census is empty"
    assert organization_routes, "the organization listing-route census is empty"
    return (
        sorted(bank_routes, key=lambda route: route.path),
        sorted(organization_routes, key=lambda route: route.path),
    )


def _value_for_annotation(annotation: Any, name: str) -> str:
    origin = get_origin(annotation)
    if origin is Literal:
        return str(get_args(annotation)[0])
    if annotation is UUID:
        return str(_PERIOD_B if name in {"period_id", "reporting_period_id"} else _UNKNOWN_UUID)
    if annotation is date:
        return "2026-09-18"
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return str(next(iter(annotation)).value)
    string_values = {
        "bank_id": _BANK_B,
        "category": "rates",
        "channel": "email",
        "curve_name": "AEQ.GHS.OIS",
        "entity": "position",
        "module": "liquidity",
        "model": "nmd-duration",
        "return_code": "BSD1",
        "signing_role": "preparer",
    }
    return string_values.get(name, "rls-property")


def _route_request(route: APIRoute) -> tuple[str, dict[str, str]]:
    path_values = {
        parameter.name: _value_for_annotation(
            parameter.field_info.annotation,
            parameter.name,
        )
        for parameter in route.dependant.path_params
    }
    path = route.path.format(**path_values)
    query: dict[str, str] = {}
    for parameter in route.dependant.query_params:
        if parameter.default is not PydanticUndefined:
            continue
        annotation = parameter.field_info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            for name, field in annotation.model_fields.items():
                if field.is_required():
                    query[name] = _value_for_annotation(field.annotation, name)
            continue
        query[parameter.name] = _value_for_annotation(annotation, parameter.name)
    return path, query


def _assert_b_identifiers_absent(response, identifiers: set[str], route: APIRoute) -> None:
    if response.status_code != 200:
        return
    leaked = sorted(identifier for identifier in identifiers if identifier in response.text)
    assert not leaked, f"{route.path} exposed tenant-B identifiers: {leaked}"


def test_generated_bindings_never_cross_postgres_rls(
    migrated_postgres_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
    fake_storage: Any,
    storage_engine: Any,
) -> None:
    """Any tenant-A principal remains unable to observe tenant-B product rows."""

    with migrated_postgres_schema.app_engine.connect() as connection:
        role_state = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        rls_state = connection.execute(
            text(
                """
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE relname = 'current_financial_facts'
                  AND relnamespace = to_regnamespace(:schema_name)
                """
            ),
            {"schema_name": migrated_postgres_schema.schema_name},
        ).one()
    if role_state[0] or role_state[1]:
        pytest.skip("Current TEST_DATABASE_URL role bypasses RLS.")
    assert rls_state == (True, True)

    _seed_tenant(
        migrated_postgres_schema,
        organization_id=_ORG_A,
        bank_id=_BANK_A,
        user_ids=_USERS_A,
        fact_id=_FACT_A,
    )
    _seed_tenant(
        migrated_postgres_schema,
        organization_id=_ORG_B,
        bank_id=_BANK_B,
        user_ids=_USERS_B,
        fact_id=_FACT_B,
    )

    database_url = migrated_postgres_schema.app_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()
    get_settings.cache_clear()
    get_engine.cache_clear()
    app = create_app()
    app.dependency_overrides[get_object_storage] = lambda: fake_storage
    app.dependency_overrides[get_ingestion_storage] = lambda: storage_engine
    bank_routes, organization_routes = _product_read_routes(app)

    @settings(max_examples=15, deadline=None)
    @given(binding_sets=_BINDING_SETS)
    def property_check(
        binding_sets: tuple[
            list[
                tuple[
                    int,
                    RoleBundle,
                    ModuleScope,
                    SensitivityScope,
                    InstitutionScope,
                ]
            ],
            list[
                tuple[
                    int,
                    RoleBundle,
                    ModuleScope,
                    SensitivityScope,
                    InstitutionScope,
                ]
            ],
        ],
    ) -> None:
        bindings_a = _replace_generated_bindings(
            migrated_postgres_schema,
            organization_id=_ORG_A,
            bank_id=_BANK_A,
            user_ids=_USERS_A,
            specs=binding_sets[0],
        )
        bindings_b = _replace_generated_bindings(
            migrated_postgres_schema,
            organization_id=_ORG_B,
            bank_id=_BANK_B,
            user_ids=_USERS_B,
            specs=binding_sets[1],
        )
        b_row_identifiers = {
            _ORG_B,
            str(_FACT_B),
            str(_PERIOD_B),
            str(_PERIOD_FACT_B),
            _B_ROW_MARKER,
            *(str(user_id) for user_id in _USERS_B),
            *bindings_b,
        }
        all_b_identifiers = {_BANK_B, *b_row_identifiers}

        with migrated_postgres_schema.app_engine.begin() as connection:
            _assert_current_fact_isolation(connection)

        with TestClient(app, raise_server_exceptions=False) as client:
            facts_route = next(
                route for route in bank_routes if route.name == "get_bank_period_facts"
            )
            path, query = _route_request(facts_route)
            response = client.get(
                path,
                params=query,
                headers=headers(
                    org_id=_ORG_B,
                    user_id=_USERS_B[0],
                    roles=("viewer",),
                    authorization_version=1,
                ),
            )
            assert response.status_code == 200, response.text
            assert response.json()["period"]["id"] == str(_PERIOD_B)
            assert [fact["id"] for fact in response.json()["balance_sheet"]] == [
                str(_PERIOD_FACT_B)
            ]
            assert response.json()["balance_sheet"][0]["category"] == _B_ROW_MARKER
            for principal_id in _USERS_A:
                auth_headers = headers(
                    org_id=_ORG_A,
                    user_id=principal_id,
                    roles=("viewer",),
                    authorization_version=1,
                )
                for route in bank_routes:
                    path, query = _route_request(route)
                    response = client.get(path, params=query, headers=auth_headers)
                    assert response.status_code in (200, 403, 404), (
                        route.path,
                        response.status_code,
                        response.text,
                    )
                    _assert_b_identifiers_absent(response, b_row_identifiers, route)

                for route in organization_routes:
                    path, query = _route_request(route)
                    response = client.get(path, params=query, headers=auth_headers)
                    assert response.status_code in (200, 403), (
                        route.path,
                        response.status_code,
                        response.text,
                    )
                    _assert_b_identifiers_absent(response, all_b_identifiers, route)

        assert bindings_a.isdisjoint(bindings_b)

    property_check()


def test_every_sibling_bank_read_route_returns_not_found(
    migrated_postgres_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
    fake_storage: Any,
    storage_engine: Any,
) -> None:
    """Every sibling-bank read route returns a non-leaking 404."""

    _seed_tenant(
        migrated_postgres_schema,
        organization_id=_ORG_A,
        bank_id=_BANK_A,
        user_ids=_USERS_A,
        fact_id=_FACT_A,
    )
    _seed_tenant(
        migrated_postgres_schema,
        organization_id=_ORG_B,
        bank_id=_BANK_B,
        user_ids=_USERS_B,
        fact_id=_FACT_B,
    )
    _replace_generated_bindings(
        migrated_postgres_schema,
        organization_id=_ORG_A,
        bank_id=_BANK_A,
        user_ids=_USERS_A,
        specs=[],
    )
    _replace_generated_bindings(
        migrated_postgres_schema,
        organization_id=_ORG_B,
        bank_id=_BANK_B,
        user_ids=_USERS_B,
        specs=[],
    )

    database_url = migrated_postgres_schema.app_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()
    app = create_app()
    app.dependency_overrides[get_object_storage] = lambda: fake_storage
    app.dependency_overrides[get_ingestion_storage] = lambda: storage_engine
    bank_routes, _ = _product_read_routes(app)

    with TestClient(app, raise_server_exceptions=False) as client:
        for principal_id in _USERS_A:
            auth_headers = headers(
                org_id=_ORG_A,
                user_id=principal_id,
                roles=("viewer",),
                authorization_version=1,
            )
            for route in bank_routes:
                path, query = _route_request(route)
                response = client.get(path, params=query, headers=auth_headers)
                assert response.status_code == 404, (
                    route.path,
                    response.status_code,
                    response.text,
                )


def _assert_current_fact_isolation(connection) -> None:
    for organization_id, fact_id in ((_ORG_A, _FACT_A), (_ORG_B, _FACT_B)):
        _set_tenant(connection, organization_id)
        visible = set(connection.scalars(text("SELECT id FROM current_financial_facts")))
        assert visible == {fact_id}, "RLS isolation invariant: only the tenant's fact is visible"


def test_current_fact_isolation_detects_no_force_rls_mutation(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """Negative control: table-owner reads leak under NO FORCE, restored by rollback."""
    for org, bank, users, fact in (
        (_ORG_A, _BANK_A, _USERS_A, _FACT_A),
        (_ORG_B, _BANK_B, _USERS_B, _FACT_B),
    ):
        _seed_tenant(
            migrated_postgres_schema,
            organization_id=org,
            bank_id=bank,
            user_ids=users,
            fact_id=fact,
        )
    with migrated_postgres_schema.app_engine.connect() as connection:
        _assert_current_fact_isolation(connection)
        connection.rollback()
        transaction = connection.begin()
        try:
            connection.execute(
                text("ALTER TABLE current_financial_facts NO FORCE ROW LEVEL SECURITY")
            )
            with pytest.raises(AssertionError, match="RLS isolation invariant"):
                _assert_current_fact_isolation(connection)
        finally:
            transaction.rollback()
        _assert_current_fact_isolation(connection)
