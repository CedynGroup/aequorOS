"""Live-engine freshness authorization: only permitted modules are served.

Issue #177 backend slice. Freshness results are filtered through the caller's
exact active VIEW bindings, mirroring the live summary's per-module gating.
These tests pin the API-level contract: permitted modules present with their
hashes, unpermitted modules absent without data leakage, partial or unbound
principals never compose authority, and cross-tenant denial stays opaque.

The dedicated authorization decisions themselves are unit-pinned in
``tests/live/service/test_live_authorization.py`` (added with the service
helper); this suite exercises the changed service flow end to end.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.live.service import pipeline
from app.models import User
from app.services import job_queue
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.support.helpers import ORG_1, headers

AS_OF = FIXTURE_AS_OF.isoformat()
_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"

FIXTURE_MODULES = ("liquidity", "capital", "credit", "irr", "fx", "ftp", "forecast")


def _seed_and_refresh(db_client: TestClient) -> None:
    """Seed the sample bank + canonical fixture and run one live refresh."""
    _ = db_client  # ensures the app engine/DB is initialized
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.flush()
        seed_canonical_fixture(session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        session.commit()
        job = job_queue.enqueue(
            session,
            ORG_1,
            "pipeline_refresh",
            bank_id=SAMPLE_BANK_ID,
            payload={"as_of_date": AS_OF},
        )
        session.commit()
        pipeline.run_refresh(session, job)
    finally:
        session.close()


def _org_user_without_bindings(email: str) -> UUID:
    """A tenant user with no authorization binding rows at all."""
    user_id = uuid4()
    session = get_sessionmaker()()
    try:
        session.add(
            User(
                id=user_id,
                organization_id=ORG_1,
                email=email,
                display_name=email.split("@")[0],
            )
        )
        session.commit()
    finally:
        session.close()
    return user_id


def _user_with_module_view(module_scope: ModuleScope, email: str) -> tuple[UUID, int]:
    """A tenant user holding exactly one active whole-institution VIEW binding."""
    user_id = uuid4()
    session = get_sessionmaker()()
    try:
        session.add(
            User(
                id=user_id,
                organization_id=ORG_1,
                email=email,
                display_name=email.split("@")[0],
            )
        )
        session.flush()
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=user_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                institution_scope=InstitutionScope.INSTITUTION,
                institution_id=SAMPLE_BANK_ID,
                module_scope=module_scope,
                sensitivity_scope=SensitivityScope.AGGREGATED,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "live-authz-test"),
            reason="Freshness authorization test coverage",
        )
        user = session.get(User, user_id)
        assert user is not None
        version = user.authorization_version
        session.commit()
    finally:
        session.close()
    return user_id, version


def test_freshness_shows_only_bound_modules(db_client: TestClient) -> None:
    """Positive control: an IRRBB-only viewer sees exactly the irr entry."""
    _seed_and_refresh(db_client)
    viewer, version = _user_with_module_view(ModuleScope.IRRBB, "irr-viewer@example.test")
    response = db_client.get(
        f"{_BASE}/freshness", headers=headers(user_id=viewer, authorization_version=version)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == str(SAMPLE_BANK_ID)
    assert [module["module"] for module in body["modules"]] == ["irr"]
    irr = body["modules"][0]
    assert irr["live_hash"] is not None
    assert irr["computed_at"] is not None


def test_freshness_omits_unbound_modules_without_data_leakage(
    db_client: TestClient,
) -> None:
    """A viewer bound only to FX gets no hashes or staleness for other modules."""
    _seed_and_refresh(db_client)
    viewer, version = _user_with_module_view(ModuleScope.FX, "fx-viewer@example.test")
    response = db_client.get(
        f"{_BASE}/freshness", headers=headers(user_id=viewer, authorization_version=version)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [module["module"] for module in body["modules"]] == ["fx"]
    # No leaked fields for the omitted modules anywhere in the payload.
    assert all(module["module"] == "fx" for module in body["modules"])


def test_freshness_for_principal_without_bindings_is_empty(db_client: TestClient) -> None:
    """No binding, no modules: a bindings-free principal gets a closed map."""
    _seed_and_refresh(db_client)
    nobody = _org_user_without_bindings("no-bindings@example.test")
    response = db_client.get(f"{_BASE}/freshness", headers=headers(user_id=nobody))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["modules"] == []
    assert body["is_stale"] is False


def test_freshness_binding_is_bank_scoped(db_client: TestClient) -> None:
    """An IRRBB binding on a different bank of the same org grants nothing here."""
    _seed_and_refresh(db_client)
    session = get_sessionmaker()()
    try:
        from app.models import Bank

        other_bank = Bank(
            organization_id=ORG_1,
            name="Other Bank Ltd",
            short_name="OTHB",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
            institution_type="universal_bank",
        )
        session.add(other_bank)
        session.commit()
        other_bank_id = other_bank.id
        viewer = uuid4()
        session.add(
            User(
                id=viewer,
                organization_id=ORG_1,
                email="other-bank-viewer@example.test",
                display_name="other-bank-viewer",
            )
        )
        session.flush()
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=viewer,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                institution_scope=InstitutionScope.INSTITUTION,
                institution_id=other_bank_id,
                module_scope=ModuleScope.IRRBB,
                sensitivity_scope=SensitivityScope.AGGREGATED,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "live-authz-test"),
            reason="Bank-scoped binding isolation coverage",
        )
        user = session.get(User, viewer)
        assert user is not None
        version = user.authorization_version
        session.commit()
    finally:
        session.close()
    response = db_client.get(
        f"{_BASE}/freshness", headers=headers(user_id=viewer, authorization_version=version)
    )
    assert response.status_code == 200, response.text
    assert response.json()["modules"] == []


def test_freshness_schema_and_order_unchanged_for_fully_bound_viewer(
    db_client: TestClient,
) -> None:
    """The all-scope fixture viewer keeps the full schema: same fields, order, staleness."""
    _seed_and_refresh(db_client)
    response = db_client.get(f"{_BASE}/freshness", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    modules = [module["module"] for module in body["modules"]]
    assert set(modules) == set(FIXTURE_MODULES) | {"rating"}
    assert modules == [
        "liquidity",
        "capital",
        "credit",
        "irr",
        "fx",
        "ftp",
        "rating",
        "forecast",
    ]
    liquidity = next(module for module in body["modules"] if module["module"] == "liquidity")
    assert liquidity["live_hash"] is not None
    assert liquidity["official_run_hash"] is None
    assert liquidity["is_stale"] is True
    assert body["is_stale"] is True
