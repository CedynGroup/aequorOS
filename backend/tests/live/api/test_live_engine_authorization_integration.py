"""Live-engine read authorization: only permitted modules are served.

Issue #177 part A. Freshness, live summary, alerts, window analytics and the
official-snapshot ladder are filtered through the caller's exact active VIEW
bindings resolved by ``live_access`` (``FRESHNESS_VIEW_MODULES``). These tests
pin the API-level contract: permitted modules present with their hashes,
unpermitted modules absent without data leakage, partial or unbound principals
never compose authority (and get no staleness signal), cross-tenant denial
stays opaque, and the snapshot gate is a single service-side check.

The dedicated authorization decisions themselves are unit-pinned in
``tests/live/service/test_live_authorization.py`` and the one-map wiring in
``tests/live/service/test_live_read_single_map.py``; this suite exercises the
changed service flows end to end.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import TenantContext
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.live.models.live import LiveFinding, LiveMetric
from app.live.service import live_view, pipeline
from app.models import Bank, User
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
                display_name=email.split("@", maxsplit=1)[0],
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
                display_name=email.split("@", maxsplit=1)[0],
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
    # Full serialized omission: no other module name appears anywhere in the
    # payload (no leaked hash/staleness entries under any serialization).
    for module_name in (
        "liquidity",
        "capital",
        "credit",
        "irr",
        "ftp",
        "rating",
        "forecast",
    ):
        assert module_name not in response.text
    # The top-level staleness verdict reflects the permitted FX module only.
    fx = body["modules"][0]
    assert body["is_stale"] == fx["is_stale"]


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


def _seed_findings() -> None:
    """One open critical liquidity breach and one open high capital breach."""
    session = get_sessionmaker()()
    try:
        session.add(
            LiveFinding(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                module="liquidity",
                rule_id="TEST-LCR-FLOOR",
                severity="critical",
                status="open",
                message="LCR breached the floor",
                metric="lcr_pct",
            )
        )
        session.add(
            LiveFinding(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                module="capital",
                rule_id="TEST-CAR-FLOOR",
                severity="high",
                status="open",
                message="CAR breached the floor",
                metric="car_pct",
            )
        )
        session.commit()
    finally:
        session.close()


def _backdate_live_metrics() -> None:
    """Make every cached live figure predate the bank's ingested batches."""
    session = get_sessionmaker()()
    try:
        session.query(LiveMetric).filter(
            LiveMetric.organization_id == ORG_1,
            LiveMetric.bank_id == SAMPLE_BANK_ID,
        ).update({LiveMetric.computed_at: utc_now() - timedelta(days=1)})
        session.commit()
    finally:
        session.close()


def test_live_summary_serves_only_bound_modules(db_client: TestClient) -> None:
    """A LIQUIDITY-only viewer sees exactly the liquidity module row."""
    _seed_and_refresh(db_client)
    viewer, version = _user_with_module_view(ModuleScope.LIQUIDITY, "summary-viewer@example.test")
    response = db_client.get(
        f"{_BASE}/live-summary", headers=headers(user_id=viewer, authorization_version=version)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [module["module"] for module in body["modules"]] == ["liquidity"]
    # Full serialized omission: no other module name appears anywhere.
    for module_name in ("capital", "credit", "irr", "fx", "ftp", "rating", "forecast"):
        assert module_name not in response.text


def test_live_summary_for_principal_without_bindings_is_empty_and_fresh(
    db_client: TestClient,
) -> None:
    """No bindings: no modules, no reconciliation, and no staleness signal."""
    _seed_and_refresh(db_client)
    _backdate_live_metrics()  # the bank now has ingested data newer than the cache
    nobody = _org_user_without_bindings("summary-no-bindings@example.test")
    response = db_client.get(f"{_BASE}/live-summary", headers=headers(user_id=nobody))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["modules"] == []
    assert body["is_stale"] is False
    assert body["reconciliation"] is None
    assert body["computed_at"] is None
    # A permitted caller keeps the correct staleness verdict for the same bank.
    viewer, version = _user_with_module_view(
        ModuleScope.LIQUIDITY, "summary-stale-viewer@example.test"
    )
    permitted = db_client.get(
        f"{_BASE}/live-summary", headers=headers(user_id=viewer, authorization_version=version)
    )
    assert permitted.status_code == 200, permitted.text
    assert permitted.json()["is_stale"] is True


def test_alerts_serve_only_bound_modules(db_client: TestClient) -> None:
    """A LIQUIDITY-only viewer sees the liquidity breach only."""
    _seed_and_refresh(db_client)
    _seed_findings()
    viewer, version = _user_with_module_view(ModuleScope.LIQUIDITY, "alerts-viewer@example.test")
    response = db_client.get(
        f"{_BASE}/alerts", headers=headers(user_id=viewer, authorization_version=version)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] > 0
    assert set(body["by_module"]) == {"liquidity"}
    assert all(item["module"] == "liquidity" for item in body["items"])
    assert any(item["rule_id"] == "TEST-LCR-FLOOR" for item in body["items"])
    assert "capital" not in response.text


def test_window_analytics_serve_only_bound_modules(db_client: TestClient) -> None:
    """A LIQUIDITY-only viewer gets liquidity ratios and daily rows only."""
    _seed_and_refresh(db_client)
    viewer, version = _user_with_module_view(ModuleScope.LIQUIDITY, "window-viewer@example.test")
    response = db_client.get(
        f"{_BASE}/analytics/window",
        headers=headers(user_id=viewer, authorization_version=version),
        params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {ratio["module"] for ratio in body["ratios"]} <= {"liquidity"}
    assert {entry["module"] for entry in body["daily"]} <= {"liquidity"}
    assert "capital" not in json.dumps(body)


def test_snapshot_ladder_serves_only_bound_modules(db_client: TestClient) -> None:
    """A CAPITAL-only viewer reads the capital ladder and no other module's."""
    _seed_and_refresh(db_client)
    viewer, version = _user_with_module_view(
        ModuleScope.CAPITAL, "ladder-viewer@example.test"
    )
    auth = headers(user_id=viewer, authorization_version=version)
    response = db_client.get(
        f"{_BASE}/live-snapshots", headers=auth, params={"module": "capital"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["module"] == "capital"
    assert len(body["snapshots"]) > 0
    assert all(snapshot["module"] == "capital" for snapshot in body["snapshots"])
    denied = db_client.get(f"{_BASE}/live-snapshots", headers=auth, params={"module": "liquidity"})
    assert denied.status_code == 403
    # No bindings at all: the single service gate denies before any row is read.
    nobody = _org_user_without_bindings("ladder-no-bindings@example.test")
    unbound = db_client.get(
        f"{_BASE}/live-snapshots",
        headers=headers(user_id=nobody),
        params={"module": "liquidity"},
    )
    assert unbound.status_code == 403


def test_snapshot_ladder_normalizes_the_bank_id(db_client: TestClient) -> None:
    """A lowercase or padded bank id resolves like the shared resolver's lookup."""
    _seed_and_refresh(db_client)
    viewer, version = _user_with_module_view(
        ModuleScope.CAPITAL, "ladder-normalized-viewer@example.test"
    )
    auth = headers(user_id=viewer, authorization_version=version)
    padded = db_client.get(
        f"{_BASE}/live-snapshots",
        headers=auth,
        params={"module": "capital"},
    )
    assert padded.status_code == 200
    lower = db_client.get(
        f"/api/v1/banks/{str(SAMPLE_BANK_ID).lower()}/live-snapshots",
        headers=auth,
        params={"module": "capital"},
    )
    assert lower.status_code == 200, lower.text
    assert lower.json()["module"] == "capital"


def test_snapshot_service_denies_an_unknown_module_in_one_evaluation(
    db_client: TestClient,
) -> None:
    """An unknown module is denied by the service; the permission is evaluated once."""
    _seed_and_refresh(db_client)
    viewer, version = _user_with_module_view(
        ModuleScope.LIQUIDITY, "mystery-module-viewer@example.test"
    )
    ctx = TenantContext(
        organization_id=ORG_1, actor_user_id=viewer, authorization_version=version
    )
    session = get_sessionmaker()()
    try:
        with (
            patch.object(
                live_view.scoped_authorization,
                "evaluate_bank_permission",
                wraps=live_view.scoped_authorization.evaluate_bank_permission,
            ) as evaluate,
            pytest.raises(HTTPException) as refused,
        ):
            live_view.list_live_snapshots(session, ctx, str(SAMPLE_BANK_ID), module="mystery")
        evaluate.assert_not_called()
    finally:
        session.close()
    assert refused.value.status_code == 403

