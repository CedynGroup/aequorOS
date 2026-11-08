"""Live READ scope end to end: only permitted modules cross the wire.

Pilot C of the #177 backend slice. The three remaining live READ surfaces —
the official-snapshot ladder, the alerts feed and the window analytics — are
filtered through the caller's exact active VIEW bindings with the same closed
module map the live summary and the freshness slice use. These tests pin the
API-level contract on all three surfaces: a scoped viewer sees only their
bound module (including the capital ladder), unbound, foreign-bank, suspended
or stale-version principals get closed maps or refusals, a fully bound viewer
keeps the pre-existing response shapes, and an unknown module row can never
exist to surface — the storage layer's own module CHECK refuses it before any
serialization could name or carry it.

The freshness surface's identical contract is pinned by
``tests/live/api/test_live_engine_authorization_integration.py``; the
closed-map helper pins live in
``tests/live/service/test_live_read_scope.py``.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.authorization import (
    BindingStatus,
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
from app.live.models.live import LiveFinding, LiveMetric, LiveMetricSnapshot
from app.live.service import pipeline
from app.models import AuthorizationBinding, Bank, User
from app.services import job_queue
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.support.helpers import ORG_1, headers

AS_OF = FIXTURE_AS_OF.isoformat()
_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
# The fixture's canonical period ends 2026-03-31 and a refresh stamps today's
# plane-2 snapshot rows, so the analysis window must span both.
_WINDOW_START = date(2026, 3, 1)
_WINDOW_END = utc_now().date() + timedelta(days=1)


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
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "live-read-scope-test"),
            reason="Live READ scope test coverage",
        )
        user = session.get(User, user_id)
        assert user is not None
        version = user.authorization_version
        session.commit()
    finally:
        session.close()
    return user_id, version


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


def _alerts(db_client: TestClient, auth: dict[str, str]):
    response = db_client.get(f"{_BASE}/alerts", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def _window(db_client: TestClient, auth: dict[str, str]):
    response = db_client.get(
        f"{_BASE}/analytics/window",
        headers=auth,
        params={"start_date": _WINDOW_START.isoformat(), "end_date": _WINDOW_END.isoformat()},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_snapshot_ladder_serves_only_the_bound_module(db_client: TestClient) -> None:
    """A CAPITAL-only viewer reads the capital ladder and no other module's.

    This is the capital snapshot entry end to end: the same exact VIEW binding
    that gates the live summary gates the official-snapshot loop.
    """
    _seed_and_refresh(db_client)
    viewer, version = _user_with_module_view(ModuleScope.CAPITAL, "capital-viewer@example.test")
    auth = headers(user_id=viewer, authorization_version=version)

    response = db_client.get(
        f"{_BASE}/live-snapshots", headers=auth, params={"module": "capital"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["module"] == "capital"
    assert len(body["snapshots"]) > 0
    assert all(snapshot["module"] == "capital" for snapshot in body["snapshots"])

    denied = db_client.get(
        f"{_BASE}/live-snapshots", headers=auth, params={"module": "liquidity"}
    )
    assert denied.status_code == 403


def test_alerts_serve_only_bound_modules(db_client: TestClient) -> None:
    """An IRRBB-free, LIQUIDITY-only viewer sees the liquidity breach only."""
    _seed_and_refresh(db_client)
    _seed_findings()
    viewer, version = _user_with_module_view(ModuleScope.LIQUIDITY, "alerts-viewer@example.test")
    auth = headers(user_id=viewer, authorization_version=version)

    body = _alerts(db_client, auth)
    assert body["total"] > 0
    assert set(body["by_module"]) == {"liquidity"}
    items = body["items"]
    assert items and all(item["module"] == "liquidity" for item in items)
    assert any(item["rule_id"] == "TEST-LCR-FLOOR" for item in items)
    # Full serialized omission: no capital name, breach or metric anywhere.
    raw = db_client.get(f"{_BASE}/alerts", headers=auth)
    assert raw.status_code == 200, raw.text
    assert "capital" not in raw.text


def test_window_analytics_serves_only_bound_modules(db_client: TestClient) -> None:
    """A LIQUIDITY-only viewer gets liquidity ratios and daily rows only."""
    _seed_and_refresh(db_client)
    viewer, version = _user_with_module_view(ModuleScope.LIQUIDITY, "window-viewer@example.test")
    auth = headers(user_id=viewer, authorization_version=version)

    body = _window(db_client, auth)
    ratios = body["ratios"]
    daily = body["daily"]
    assert {ratio["ratio"] for ratio in ratios} == {"lcr_pct", "nsfr_pct"}
    assert all(ratio["module"] == "liquidity" for ratio in ratios)
    assert {entry["module"] for entry in daily} == {"liquidity"}
    assert all(entry["day_count"] > 0 for entry in daily)
    assert "capital" not in _window(db_client, auth)  # serialized omission


def test_unbound_principal_gets_closed_maps(db_client: TestClient) -> None:
    """No binding, no modules: every surface answers with a closed map."""
    _seed_and_refresh(db_client)
    nobody = _org_user_without_bindings("read-scope-no-bindings@example.test")
    auth = headers(user_id=nobody)

    assert _alerts(db_client, auth)["total"] == 0
    window = _window(db_client, auth)
    assert window["daily"] == []
    assert window["ratios"] == []
    ladder = db_client.get(
        f"{_BASE}/live-snapshots", headers=auth, params={"module": "capital"}
    )
    assert ladder.status_code == 403


def test_foreign_bank_binding_grants_nothing_here(db_client: TestClient) -> None:
    """An IRRBB binding on a different bank of the same org grants nothing."""
    _seed_and_refresh(db_client)
    session = get_sessionmaker()()
    try:
        other_bank = Bank(
            organization_id=ORG_1,
            name="Read Scope Other Bank Ltd",
            short_name="RSOB",
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
                email="other-bank-read-scope@example.test",
                display_name="other-bank-read-scope",
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
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "live-read-scope-test"),
            reason="Bank-scoped binding isolation coverage",
        )
        user = session.get(User, viewer)
        assert user is not None
        version = user.authorization_version
        session.commit()
    finally:
        session.close()
    auth = headers(user_id=viewer, authorization_version=version)

    assert _alerts(db_client, auth)["total"] == 0
    window = _window(db_client, auth)
    assert window["daily"] == []
    assert window["ratios"] == []
    ladder = db_client.get(
        f"{_BASE}/live-snapshots", headers=auth, params={"module": "irr"}
    )
    assert ladder.status_code == 403


def test_suspended_binding_stops_serving_its_module(db_client: TestClient) -> None:
    """Suspending the only VIEW binding closes every surface for that module."""
    _seed_and_refresh(db_client)
    _seed_findings()
    viewer, version = _user_with_module_view(ModuleScope.LIQUIDITY, "suspended@example.test")
    session = get_sessionmaker()()
    try:
        binding = (
            session.query(AuthorizationBinding)
            .filter(AuthorizationBinding.principal_user_id == viewer)
            .one()
        )
        binding.status = BindingStatus.SUSPENDED.value
        session.commit()
    finally:
        session.close()
    auth = headers(user_id=viewer, authorization_version=version)

    assert _alerts(db_client, auth)["total"] == 0
    window = _window(db_client, auth)
    assert window["daily"] == []
    ladder = db_client.get(
        f"{_BASE}/live-snapshots", headers=auth, params={"module": "liquidity"}
    )
    assert ladder.status_code == 403


def test_stale_authorization_version_is_refused(db_client: TestClient) -> None:
    """A token minted before the latest authorization change is refused."""
    _seed_and_refresh(db_client)
    viewer, version = _user_with_module_view(ModuleScope.FX, "stale-version@example.test")
    response = db_client.get(
        f"{_BASE}/alerts",
        headers=headers(user_id=viewer, authorization_version=version - 1),
    )
    assert response.status_code == 401


def test_fully_bound_viewer_keeps_the_response_shapes(db_client: TestClient) -> None:
    """The all-authority fixture viewer keeps every surface's full payload."""
    _seed_and_refresh(db_client)
    _seed_findings()
    auth = headers()

    alerts_body = _alerts(db_client, auth)
    assert alerts_body["total"] >= 2
    assert set(alerts_body["by_severity"]) <= {"critical", "high"}
    window = _window(db_client, auth)
    assert window["start_date"] == _WINDOW_START.isoformat()
    assert window["end_date"] == _WINDOW_END.isoformat()
    modules = {entry["module"] for entry in window["daily"]}
    assert {"liquidity", "capital"} <= modules
    assert modules <= {"liquidity", "capital", "credit", "irr", "fx", "ftp", "rating", "forecast"}
    ladder = db_client.get(
        f"{_BASE}/live-snapshots", headers=auth, params={"module": "capital"}
    )
    assert ladder.status_code == 200


def test_unknown_module_row_cannot_exist_to_surface(db_client: TestClient) -> None:
    """The storage layer's module CHECK refuses an unmapped module row.

    An unknown module name therefore has no serialized value, metric or name
    to leak on any surface: the row cannot be persisted at all, and the
    responses stay clean and closed.
    """
    _seed_and_refresh(db_client)
    session = get_sessionmaker()()
    try:
        with pytest.raises(IntegrityError):
            session.add(
                LiveMetric(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    module="mystery",
                    metrics={"mystery_pct": "99"},
                    status="green",
                    computed_at=utc_now(),
                )
            )
            session.flush()
        session.rollback()
        with pytest.raises(IntegrityError):
            session.add(
                LiveFinding(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    module="mystery",
                    rule_id="TEST-MYSTERY",
                    severity="high",
                    status="open",
                    message="mystery breach",
                )
            )
            session.flush()
        session.rollback()
        snapshot_row = session.query(LiveMetricSnapshot.reporting_period_id).first()
        assert snapshot_row is not None
        with pytest.raises(IntegrityError):
            session.add(
                LiveMetricSnapshot(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    reporting_period_id=snapshot_row[0],
                    snapshot_date=utc_now().date(),
                    module="mystery",
                    metrics={"mystery_pct": "99"},
                    status="green",
                    computed_at=utc_now(),
                )
            )
            session.flush()
        session.rollback()
    finally:
        session.close()

    auth = headers()
    assert db_client.get(f"{_BASE}/alerts", headers=auth).status_code == 200
    assert "mystery" not in db_client.get(f"{_BASE}/alerts", headers=auth).text
    summary = db_client.get(f"{_BASE}/live-summary", headers=auth)
    assert summary.status_code == 200
    assert "mystery" not in summary.text
