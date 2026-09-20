"""Generative property: no caller authority reaches a foreign object (IDOR).

Layer 2 of the object-reference proof, and the Postgres-only, RLS-backed
complement to the deterministic coverage in
`tests/api/test_authorization_object_reference_coverage.py`.  Where Layer 1 fixes
a fully entitled caller and sweeps every route once, this layer fixes nothing
about the caller and lets Hypothesis vary the dimensions that actually decide
authorization — role/permission bundle, module scope, sensitivity scope,
institution scope, and binding lifecycle state — together with object placement
(cross-organization vs same-org sibling bank).  That is where generation earns
its cost: it proves no *combination* of a bank-A caller's authority reaches a
foreign object, not just that the fully entitled one does not.

For each generated caller and layout the property replaces bank A's bindings,
sweeps every applicable route, and asserts that a read leaks no foreign
identifier, a mutation never returns 2xx, and the content digest of every table
in both organizations is unchanged.  It runs against a migrated Postgres schema
with FORCE RLS (`forward_migrated_postgres_schema`), so the RLS backstop is
exercised alongside the explicit guards.  ``max_examples`` is deliberately
small: each example sweeps the whole route census.

The two confirmed same-org cross-bank defects are excluded here (their strict
pin and promotion guard live in Layer 1's ``KNOWN_DEFECTS`` test); the negative
control weakens the package bank guard under a rolled-back monkeypatch and
confirms the sweep then reports the leak.  The schema is dropped without the
downgrade round trip because issued integration keys and ``enterprise_stress``
runs are rows the older schemas refuse to carry.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.authorization import InstitutionScope, ModuleScope, RoleBundle, SensitivityScope
from app.db.base import Base
from app.features.ingest_data import get_ingestion_storage
from app.integrations.storage.s3 import get_object_storage
from app.main import create_app
from app.models import AuthorizationBinding, Organization, User
from app.services.regulatory_reporting import common as regulatory_common
from tests.api.helpers import headers
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    clear_database_caches,
    forward_migrated_postgres_schema,
)
from tests.fixtures.object_reference_routes import (
    GRANT_REASON,
    KNOWN_DEFECTS,
    LAYOUTS,
    ORG_A,
    ORG_B,
    TENANT_A,
    TENANT_A2,
    TENANT_B,
    Layout,
    ObjectRoute,
    binding,
    foreign_request,
    leaked,
    object_routes,
)
from tests.fixtures.object_references import ObjectSet, TenantSeed, bank_row, seed_objects

__all__ = ["forward_migrated_postgres_schema"]

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres object-reference authorization properties.",
)

_GENERATED_BUNDLES = (
    RoleBundle.VIEWER,
    RoleBundle.AUDITOR,
    RoleBundle.ANALYST,
    RoleBundle.APPROVER,
    RoleBundle.ACCOUNT_ADMIN,
)
_LIFECYCLES = ("active", "suspended", "expired", "not_yet_valid")
_BINDING_SPEC = st.tuples(
    st.integers(min_value=0, max_value=2),
    st.sampled_from(_GENERATED_BUNDLES),
    st.sampled_from(tuple(ModuleScope)),
    st.sampled_from(tuple(SensitivityScope)),
    st.sampled_from((InstitutionScope.ORGANIZATION, InstitutionScope.INSTITUTION)),
    st.sampled_from(_LIFECYCLES),
)
_CALLER = st.tuples(
    st.integers(min_value=0, max_value=2),
    st.lists(_BINDING_SPEC, max_size=6),
    st.sampled_from(LAYOUTS),
)

BindingSpec = tuple[int, RoleBundle, ModuleScope, SensitivityScope, InstitutionScope, str]


@dataclass
class Tenants:
    home: ObjectSet
    sibling: ObjectSet
    other_org: ObjectSet

    def owner(self, layout: Layout) -> ObjectSet:
        return self.other_org if layout == "cross_organization" else self.sibling


# --- Postgres tenant seeding ------------------------------------------------


def _tenant_session(engine: Engine, organization_id: str) -> Session:
    session = Session(engine, expire_on_commit=False)
    session.info["organization_id"] = organization_id
    return session


def _seed_organization(engine: Engine, tenant: TenantSeed, *, with_users: bool) -> ObjectSet:
    with _tenant_session(engine, tenant.organization_id) as session:
        if with_users:
            session.add(Organization(id=tenant.organization_id, name=tenant.marker))
        session.add(bank_row(tenant))
        if with_users:
            for index, user_id in enumerate(tenant.user_ids):
                session.add(
                    User(
                        id=user_id,
                        organization_id=tenant.organization_id,
                        email=f"idor-{index}@{tenant.organization_id.lower()}.example",
                        display_name=f"IDOR principal {tenant.organization_id} {index}",
                        role="viewer",
                        auth_provider="password",
                    )
                )
        session.flush()
        objects = seed_objects(session, tenant)
        session.commit()
    return objects


def _replace_home_bindings(engine: Engine, bindings: list[AuthorizationBinding]) -> None:
    with _tenant_session(engine, ORG_A) as session:
        session.execute(
            text(
                "DELETE FROM authorization_bindings"
                " WHERE organization_id = :organization_id AND grant_reason = :reason"
            ),
            {"organization_id": ORG_A, "reason": GRANT_REASON},
        )
        session.add_all(bindings)
        session.commit()


def _utcnow(engine: Engine):  # noqa: ANN202 - sqlalchemy returns a datetime
    with engine.connect() as connection:
        return connection.execute(text("SELECT now()")).scalar_one()


def _generated_bindings(engine: Engine, specs: list[BindingSpec]) -> list[AuthorizationBinding]:
    now = _utcnow(engine)
    bindings: list[AuthorizationBinding] = []
    for user_slot, bundle, module, sensitivity, institution_scope, lifecycle in specs:
        account_admin = bundle is RoleBundle.ACCOUNT_ADMIN
        row = binding(
            tenant=TENANT_A,
            user_id=TENANT_A.user_ids[user_slot],
            role=bundle,
            module_scope=ModuleScope.ACCOUNT if account_admin else module,
            sensitivity_scope=SensitivityScope.ALL if account_admin else sensitivity,
            institution_scope=(
                InstitutionScope.ORGANIZATION if account_admin else institution_scope
            ),
        )
        if lifecycle == "suspended":
            row.status = "suspended"
        elif lifecycle == "expired":
            row.valid_from = now - timedelta(days=2)
            row.valid_until = now - timedelta(days=1)
        elif lifecycle == "not_yet_valid":
            row.valid_from = now + timedelta(days=1)
        bindings.append(row)
    return bindings


# --- observation ------------------------------------------------------------


def _digest_statement() -> str:
    parts = [
        f"SELECT '{name}' AS name, md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), ''))"
        f" AS digest FROM {name} t"
        for name in sorted(Base.metadata.tables)
    ]
    return " UNION ALL ".join(parts)


def _snapshot(engine: Engine, statement: str) -> dict[tuple[str, str], str]:
    digests: dict[tuple[str, str], str] = {}
    for organization_id in (ORG_A, ORG_B):
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": organization_id},
            )
            for name, digest in connection.execute(text(statement)):
                digests[organization_id, name] = digest
    return digests


def _changed_tables(
    before: Mapping[tuple[str, str], str], after: Mapping[tuple[str, str], str]
) -> list[str]:
    return sorted(
        f"{org}:{name}" for (org, name), digest in after.items() if before[org, name] != digest
    )


@dataclass
class Sweep:
    client: TestClient
    document: dict[str, Any]
    engine: Engine
    tenants: Tenants
    routes: list[ObjectRoute]
    statement: str = field(default_factory=_digest_statement)

    def _send(self, route: ObjectRoute, path: str, query: dict[str, str], body: Any, auth) -> Any:
        return self.client.request(route.method, path, params=query, json=body, headers=auth)

    def run(self, auth: dict[str, str], layout: Layout) -> list[str]:
        """Sweep every route for ``layout`` as ``auth``; return refusal failures."""
        failures: list[str] = []
        before = _snapshot(self.engine, self.statement)
        for route in self.routes:
            if (route.method, route.path, layout) in KNOWN_DEFECTS:
                continue
            built = foreign_request(
                route,
                self.document,
                layout,
                home=self.tenants.home,
                owner=self.tenants.owner(layout),
            )
            if built is None:
                continue
            request, requested = built
            response = self._send(route, request.path, request.query, request.body, auth)
            leak = leaked(response.text, self.tenants.owner(layout).identifiers(), requested)
            if leak:
                failures.append(f"{route.label} [{layout}]: leaked {leak}")
            if route.mutation and response.status_code in (200, 201, 202, 204):
                failures.append(f"{route.label} [{layout}]: accepted with {response.status_code}")
        if changed := _changed_tables(before, _snapshot(self.engine, self.statement)):
            failures.append(f"[{layout}] side effects in {changed}")
        return failures


def _caller_headers(user_id: UUID, roles: tuple[str, ...]) -> dict[str, str]:
    return headers(org_id=ORG_A, user_id=user_id, roles=roles, authorization_version=1)


@pytest.fixture
def object_reference_sweep(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
    fake_storage: Any,
    storage_engine: Any,
) -> Iterator[tuple[FastAPI, Engine, Tenants]]:
    engine = forward_migrated_postgres_schema.app_engine
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    clear_database_caches()
    app = create_app()
    app.dependency_overrides[get_object_storage] = lambda: fake_storage
    app.dependency_overrides[get_ingestion_storage] = lambda: storage_engine
    tenants = Tenants(
        home=_seed_organization(engine, TENANT_A, with_users=True),
        sibling=_seed_organization(engine, TENANT_A2, with_users=False),
        other_org=_seed_organization(engine, TENANT_B, with_users=True),
    )
    yield app, engine, tenants
    _unwind_seeded_rows(engine)


def _unwind_seeded_rows(engine: Engine) -> None:
    """Remove seeded rows the migration chain cannot downgrade past.

    Issued bank-scoped integration keys make their migration refuse to
    downgrade, an ``enterprise_stress`` run no longer fits the 16-character
    module column the older schema restores, and a ``bsd`` package predates the
    return-family recode.  Service identities and machine bindings are dropped
    wholesale by the table downgrades, so they are left in place — deleting a
    user would cascade a ``SET NULL`` onto append-only ``audit_events``, which
    the restricted migration role may not update.
    """
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM integration_keys"))
        for organization_id in (ORG_A, ORG_B):
            connection.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": organization_id},
            )
            connection.execute(text("DELETE FROM enterprise_stress_signoffs"))
            connection.execute(
                text("DELETE FROM regulatory_runs WHERE module = 'enterprise_stress'")
            )
            connection.execute(text("DELETE FROM regulatory_packages"))


def test_generated_callers_never_reach_foreign_objects(
    object_reference_sweep: tuple[FastAPI, Engine, Tenants],
) -> None:
    """No generated bank-A authority reaches a foreign object, on any layout."""
    app, engine, tenants = object_reference_sweep
    routes = object_routes(app)

    @settings(max_examples=15, deadline=None)
    @given(caller=_CALLER)
    def property_check(caller: tuple[int, list[BindingSpec], Layout]) -> None:
        user_slot, specs, layout = caller
        _replace_home_bindings(engine, _generated_bindings(engine, specs))
        auth = _caller_headers(TENANT_A.user_ids[user_slot], ())
        with TestClient(app, raise_server_exceptions=False) as client:
            sweep = Sweep(
                client=client, document=app.openapi(), engine=engine, tenants=tenants, routes=routes
            )
            failures = sweep.run(auth, layout)
            assert not failures, "\n".join(failures)

    property_check()


def test_object_reference_property_detects_weakened_package_guard(
    object_reference_sweep: tuple[FastAPI, Engine, Tenants],
) -> None:
    """Negative control: a package lookup that ignores the bank must fail the sweep.

    The guard is weakened only under a rolled-back monkeypatch — no product code
    changes — and the same sweep that passes strictly must then report the leak.
    """
    app, engine, tenants = object_reference_sweep
    routes = [
        route
        for route in object_routes(app)
        if route.method == "GET" and route.path.endswith("/regulatory-packages/{package_id}")
    ]
    assert routes
    _replace_home_bindings(engine, _fully_entitled_bindings())
    auth = _caller_headers(TENANT_A.actor_id, ("admin",))
    original = regulatory_common.get_package_or_404

    def unscoped_lookup(db: Session, ctx: Any, bank_id: str, package_id: UUID) -> Any:
        # The defect under test: resolve the package by id inside the caller's
        # organization but ignore which bank the path names.
        del bank_id
        return original(db, ctx, TENANT_A2.bank_id, package_id)

    with (
        TestClient(app, raise_server_exceptions=False) as client,
        pytest.MonkeyPatch.context() as patched,
    ):
        sweep = Sweep(
            client=client, document=app.openapi(), engine=engine, tenants=tenants, routes=routes
        )
        patched.setattr(regulatory_common, "get_package_or_404", unscoped_lookup)
        patched.setattr(
            "app.services.regulatory_reporting.packages.get_package_or_404", unscoped_lookup
        )
        weakened = sweep.run(auth, "sibling_bank")
        assert weakened, "the weakened package guard went unnoticed"
    with TestClient(app, raise_server_exceptions=False) as client:
        sweep = Sweep(
            client=client, document=app.openapi(), engine=engine, tenants=tenants, routes=routes
        )
        restored = sweep.run(auth, "sibling_bank")
    assert not restored, "\n".join(restored)


def _fully_entitled_bindings() -> list[AuthorizationBinding]:
    bindings = [
        binding(
            tenant=TENANT_A,
            user_id=TENANT_A.actor_id,
            role=RoleBundle.ORG_OWNER,
            module_scope=ModuleScope.ACCOUNT,
            sensitivity_scope=SensitivityScope.ALL,
            institution_scope=InstitutionScope.ORGANIZATION,
        )
    ]
    for role in (RoleBundle.VIEWER, RoleBundle.ANALYST, RoleBundle.APPROVER):
        bindings.append(
            binding(
                tenant=TENANT_A,
                user_id=TENANT_A.actor_id,
                role=role,
                module_scope=ModuleScope.ALL,
                sensitivity_scope=SensitivityScope.ALL,
                institution_scope=InstitutionScope.INSTITUTION,
            )
        )
    return bindings
