"""Deterministic coverage: every object-reference route refuses foreign objects.

Layer 1 of the object-reference (IDOR) proof.  It enumerates every ``/api/v1``
route that carries an object identifier beside ``{bank_id}`` from the FastAPI
registry (`tests.fixtures.object_reference_routes`) and parametrizes one case per
route × HTTP method × {cross-organization, same-org sibling bank}.  Fixed
fixtures seed one real object of every kind for three tenants (organization A
bank A, organization A sibling bank A2, organization B bank B); a fully entitled
bank-A caller then references a foreign tenant's object under bank A.

Each case asserts the refusal shape: a read discloses no foreign identifier (a
200 that hides the row is a valid refusal), and a mutation never returns 2xx and
inserts no rows into any table.  The two confirmed same-org cross-bank defects
are quarantined in ``KNOWN_DEFECTS`` and pinned as still-defective by
``test_known_defects_are_still_reproduced`` so a fix forces their promotion.

This layer runs on the default (non-Postgres) test database: the refusals it
checks come from the explicit organization/bank ``WHERE`` clauses in the guards
and services, which hold without row-level security, so it needs no Postgres.
The RLS backstop and the generative authorization-dimension coverage live in
`tests/db/test_authorization_object_reference_properties.py` (Postgres-only).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.features.ingest_data import get_ingestion_storage
from app.integrations.storage.s3 import get_object_storage
from app.main import create_app
from app.models import Organization, User
from tests.api.helpers import headers
from tests.fixtures.object_reference_routes import (
    KNOWN_DEFECTS,
    LAYOUTS,
    TENANT_A,
    TENANT_A2,
    TENANT_B,
    Layout,
    ObjectRoute,
    Request,
    foreign_references,
    foreign_request,
    full_authority_bindings,
    leaked,
    object_routes,
)
from tests.fixtures.object_references import (
    MODEL_BY_KIND,
    OBJECT_KINDS,
    ObjectSet,
    TenantSeed,
    bank_row,
    seed_objects,
)
from tests.fixtures.reference_data import seed_global_reference_data

# The census is read from a throwaway app at import so the cases are available
# for parametrization; the fixture below builds the app that serves requests.
_CENSUS_APP = create_app()
_ROUTES = object_routes(_CENSUS_APP)
_CASES = [
    pytest.param(route, layout, id=f"{route.method}:{route.path}:{layout}")
    for route in _ROUTES
    for layout in LAYOUTS
    if foreign_references(route, layout)
]


@dataclass(frozen=True)
class Outcome:
    problems: list[str]
    detail: str


@dataclass
class Coverage:
    """A seeded app plus the three tenants' objects, for one deterministic pass."""

    client: TestClient
    engine: Engine
    document: dict[str, Any]
    home: ObjectSet
    sibling: ObjectSet
    other_org: ObjectSet
    _count_statement: str

    def owner(self, layout: Layout) -> ObjectSet:
        return self.other_org if layout == "cross_organization" else self.sibling

    def _counts(self) -> dict[str, int]:
        with self.engine.connect() as connection:
            return {name: count for name, count in connection.execute(text(self._count_statement))}

    def exercise(self, route: ObjectRoute, layout: Layout) -> Outcome:
        built = foreign_request(
            route, self.document, layout, home=self.home, owner=self.owner(layout)
        )
        assert built is not None, f"{route.label} [{layout}] has no foreign reference"
        request, requested = built
        before = self._counts()
        response = self._send(route, request)
        after = self._counts()
        problems: list[str] = []
        inserted = sorted(
            f"{name}+{after[name] - before[name]}" for name in after if after[name] > before[name]
        )
        if route.mutation and response.status_code in (200, 201, 202, 204):
            problems.append(f"accepted with {response.status_code}")
        elif not route.mutation and response.status_code not in (200, 403, 404):
            problems.append(f"unexpected read status {response.status_code}")
        if route.mutation and inserted:
            problems.append(f"inserted rows {inserted}")
        leak = leaked(response.text, self.owner(layout).identifiers(), requested)
        if leak:
            problems.append(f"leaked {leak}")
        return Outcome(problems=problems, detail=response.text[:300])

    def _send(self, route: ObjectRoute, request: Request) -> Any:
        return self.client.request(
            route.method,
            request.path,
            params=request.query,
            json=request.body,
            headers=headers(
                org_id=TENANT_A.organization_id,
                user_id=TENANT_A.actor_id,
                roles=("admin",),
                authorization_version=1,
            ),
        )


def _count_statement() -> str:
    return " UNION ALL ".join(
        f"SELECT '{name}' AS name, count(*) AS n FROM {name}"
        for name in sorted(Base.metadata.tables)
    )


def _seed_tenant(session: Session, tenant: TenantSeed, *, with_users: bool) -> ObjectSet:
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
    return seed_objects(session, tenant)


@pytest.fixture(scope="module")
def coverage(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Coverage]:
    import app.api.deps as deps_mod  # noqa: PLC0415
    import app.db.session as session_mod  # noqa: PLC0415

    database_path = tmp_path_factory.mktemp("object-reference-coverage") / "coverage.db"
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    Base.metadata.create_all(engine)
    bound_sessionmaker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with Session(engine, expire_on_commit=False) as session:
        seed_global_reference_data(session)
        home = _seed_tenant(session, TENANT_A, with_users=True)
        # A2 shares organization A, so its organization and principals already
        # exist; only its bank and objects are new.
        sibling = _seed_tenant(session, TENANT_A2, with_users=False)
        other_org = _seed_tenant(session, TENANT_B, with_users=True)
        session.add_all(full_authority_bindings(TENANT_A, owner=True))
        session.commit()

    # Bind the app to this SQLite engine directly, the way the hermetic conftest
    # does, so the per-test ``DATABASE_URL=""`` reset cannot strand a request.
    originals = (session_mod.get_sessionmaker, deps_mod.get_sessionmaker, session_mod.get_engine)
    session_mod.get_sessionmaker = lambda: bound_sessionmaker
    deps_mod.get_sessionmaker = lambda: bound_sessionmaker
    session_mod.get_engine = lambda _url=None: engine

    app = create_app()
    fake_storage = _FakeStorage()
    app.dependency_overrides[get_object_storage] = lambda: fake_storage
    app.dependency_overrides[get_ingestion_storage] = lambda: fake_storage
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield Coverage(
                client=client,
                engine=engine,
                document=app.openapi(),
                home=home,
                sibling=sibling,
                other_org=other_org,
                _count_statement=_count_statement(),
            )
    finally:
        session_mod.get_sessionmaker, deps_mod.get_sessionmaker, session_mod.get_engine = originals
        engine.dispose()


class _FakeStorage:
    """A storage double: object-reference refusals never reach real storage."""

    def __getattr__(self, _name: str) -> Any:
        raise AssertionError("object-reference coverage must not reach object storage")


@pytest.mark.parametrize(("route", "layout"), _CASES)
def test_foreign_object_reference_is_refused(
    coverage: Coverage, route: ObjectRoute, layout: Layout
) -> None:
    """A fully entitled bank-A caller cannot read or mutate a foreign object."""
    if (route.method, route.path, layout) in KNOWN_DEFECTS:
        pytest.skip("quarantined product defect; see test_known_defects_are_still_reproduced")
    outcome = coverage.exercise(route, layout)
    assert not outcome.problems, f"{route.label} [{layout}]: {outcome.problems} {outcome.detail}"


def test_known_defects_are_still_reproduced(coverage: Coverage) -> None:
    """Each quarantined defect still leaks; a fix must promote it to the strict test."""
    fixed: list[str] = []
    for method, path, layout in sorted(KNOWN_DEFECTS):
        route = next((r for r in _ROUTES if r.method == method and r.path == path), None)
        assert route is not None, f"quarantined route {method} {path} is no longer in the census"
        if not coverage.exercise(route, layout).problems:
            fixed.append(f"{method} {path} [{layout}]")
    assert not fixed, (
        "documented object-reference defects are no longer reproduced; remove them from "
        f"KNOWN_DEFECTS and let the strict parametrization cover them: {fixed}"
    )


def test_seeded_objects_exist_for_their_owners(coverage: Coverage) -> None:
    """Positive control: every referenced object really exists for the tenant that owns it."""
    missing: list[str] = []
    for objects in (coverage.sibling, coverage.other_org):
        with Session(coverage.engine) as session:
            for kind in OBJECT_KINDS:
                if kind.name in {"bank", "user"}:
                    continue
                if session.get(MODEL_BY_KIND[kind.name], UUID(objects[kind.name])) is None:
                    missing.append(f"{objects.tenant.bank_id}:{kind.name}")
    assert not missing, f"positive control: seeded objects are missing: {missing}"
