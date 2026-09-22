"""Scoped-binding enforcement for the credit module's shared live surfaces.

Phase 1 of the credit cutover (``backend/docs/credit_enforcement_rollout.md``)
introduces ``Module.CREDIT`` and gates the credit rows of the SHARED surfaces —
live summary, alerts, explicit live-snapshot ladders and window analytics — on
an exact CREDIT/aggregated ``view`` binding, exactly as liquidity, IRRBB, FX
and FTP already are. The direct ``/credit/*`` routes are the Phase 4 cutover
and are deliberately not exercised here.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

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
from app.models import (
    AuthorizationBinding,
    Bank,
    LiveFinding,
    LiveMetric,
    LiveMetricSnapshot,
    User,
)
from app.services import authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, USER_1, headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    """Drop the hermetic ``viewer/all/all`` sentence so each test grants exactly."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        if session.get(Bank, SAMPLE_BANK_ID) is None:
            session.add(
                Bank(
                    id=SAMPLE_BANK_ID,
                    organization_id=ORG_1,
                    name="Credit authorization bank",
                    short_name="Credit auth",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                )
            )
        session.commit()
    finally:
        session.close()


def _grant(
    *,
    module_scope: ModuleScope,
    sensitivity_scope: SensitivityScope = SensitivityScope.AGGREGATED,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
) -> int:
    """One indivisible Viewer sentence for USER_1; returns the new ``authv``."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        user = session.get(User, USER_1)
        assert user is not None
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                institution_scope,
                SAMPLE_BANK_ID if institution_scope is InstitutionScope.INSTITUTION else None,
                module_scope,
                sensitivity_scope,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise credit shared-surface enforcement.",
        )
        session.refresh(user)
        return user.authorization_version
    finally:
        session.close()


def _seed_live_rows() -> None:
    """One live metric, one critical finding and one daily snapshot per engine.

    Capital is the control: it carries no binding gate on these surfaces, so it
    proves the response is filtered rather than emptied.
    """
    now = utc_now()
    with get_sessionmaker()() as session:
        session.execute(delete(LiveMetric))
        session.execute(delete(LiveFinding))
        session.execute(delete(LiveMetricSnapshot))
        for module, key in (("capital", "car_pct"), ("credit", "npl_ratio_pct")):
            session.add(
                LiveMetric(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    module=module,
                    metrics={key: "12"},
                    status="red",
                    computed_at=now,
                )
            )
            session.add(
                LiveFinding(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    module=module,
                    rule_id=f"{module}_breach",
                    severity="critical",
                    message="Protected finding",
                )
            )
            session.add(
                LiveMetricSnapshot(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    module=module,
                    reporting_period_id=uuid4(),
                    snapshot_date=now.date(),
                    metrics={key: "12"},
                    status="red",
                    computed_at=now,
                )
            )
        session.commit()


def _shared_surfaces(db_client: TestClient, version: int) -> dict[str, object]:
    auth = headers(authorization_version=version)
    today = utc_now().date().isoformat()
    summary = db_client.get(f"{BASE}/live-summary", headers=auth)
    assert summary.status_code == 200, summary.text
    alerts = db_client.get(f"{BASE}/alerts", headers=auth)
    assert alerts.status_code == 200, alerts.text
    window = db_client.get(
        f"{BASE}/analytics/window",
        params={"start_date": today, "end_date": today},
        headers=auth,
    )
    assert window.status_code == 200, window.text
    snapshots = db_client.get(f"{BASE}/live-snapshots", params={"module": "credit"}, headers=auth)
    return {
        "summary_modules": {row["module"] for row in summary.json()["modules"]},
        "alerts_total": alerts.json()["total"],
        "alerts_by_module": alerts.json()["by_module"],
        "window_daily_modules": {row["module"] for row in window.json()["daily"]},
        "snapshots_status": snapshots.status_code,
    }


def test_liquidity_only_principal_no_longer_sees_credit_on_shared_surfaces(
    db_client: TestClient,
) -> None:
    """The D-017 retro-gate: credit rows are filtered like every gated engine.

    A liquidity binding — the classic case of a Treasury reader — used to see
    the credit live metric, the credit breach and the NPL daily ladder; it now
    sees only what it holds a sentence for, and capital (ungated) proves the
    surfaces still answer rather than going blank.
    """
    _seed_live_rows()
    version = _grant(module_scope=ModuleScope.LIQUIDITY)

    seen = _shared_surfaces(db_client, version)

    assert seen["summary_modules"] == {"capital"}
    assert seen["alerts_total"] == 1
    assert seen["alerts_by_module"] == {"capital": 1}
    assert seen["window_daily_modules"] == {"capital"}
    assert seen["snapshots_status"] == 403


@pytest.mark.parametrize(
    ("module_scope", "institution_scope"),
    [
        (ModuleScope.CREDIT, InstitutionScope.INSTITUTION),
        (ModuleScope.CREDIT, InstitutionScope.ORGANIZATION),
        (ModuleScope.ALL, InstitutionScope.ORGANIZATION),
    ],
    ids=["credit-exact-institution", "credit-organization-wide", "all-modules"],
)
def test_credit_or_all_aggregated_view_serves_credit_on_shared_surfaces(
    db_client: TestClient,
    module_scope: ModuleScope,
    institution_scope: InstitutionScope,
) -> None:
    _seed_live_rows()
    version = _grant(module_scope=module_scope, institution_scope=institution_scope)

    seen = _shared_surfaces(db_client, version)

    assert seen["summary_modules"] == {"capital", "credit"}
    assert seen["alerts_total"] == 2
    assert seen["alerts_by_module"] == {"capital": 1, "credit": 1}
    assert seen["window_daily_modules"] == {"capital", "credit"}
    assert seen["snapshots_status"] == 200


@pytest.mark.parametrize(
    "sensitivity_scope",
    [SensitivityScope.CONFIDENTIAL, SensitivityScope.RESTRICTED],
)
def test_credit_sensitivity_is_exact_on_shared_surfaces(
    db_client: TestClient, sensitivity_scope: SensitivityScope
) -> None:
    """A higher credit sensitivity does not imply the aggregated one.

    Sensitivity is exact-or-all, never a ladder: the Phase 4 blotter sentence
    (CREDIT/restricted) must not silently unlock the aggregated feeds.
    """
    _seed_live_rows()
    version = _grant(module_scope=ModuleScope.CREDIT, sensitivity_scope=sensitivity_scope)

    seen = _shared_surfaces(db_client, version)

    assert seen["summary_modules"] == {"capital"}
    assert seen["alerts_by_module"] == {"capital": 1}
    assert seen["window_daily_modules"] == {"capital"}
    assert seen["snapshots_status"] == 403


def test_risk_binding_grants_no_credit_authority(db_client: TestClient) -> None:
    """``risk`` was only ever a dashboard label for credit; it is not the module.

    The mirror migration copies each ``risk`` row to a ``credit`` row precisely
    because the evaluator never reads one module as another.
    """
    _seed_live_rows()
    version = _grant(module_scope=ModuleScope.RISK)

    seen = _shared_surfaces(db_client, version)

    assert seen["summary_modules"] == {"capital"}
    assert seen["alerts_by_module"] == {"capital": 1}
    assert seen["window_daily_modules"] == {"capital"}
    assert seen["snapshots_status"] == 403


def test_credit_binding_row_stores_the_exact_module_value(db_client: TestClient) -> None:
    """The stored ``module_scope`` is the literal the DB CHECK and evaluator compare.

    The model derives its CHECK from ``ModuleScope``; the mirror migration must
    widen the migrated literal to the same value or a real deployment rejects
    what this hermetic schema accepts.
    """
    _grant(module_scope=ModuleScope.CREDIT)
    with get_sessionmaker()() as session:
        rows = list(
            session.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.organization_id == ORG_1,
                    AuthorizationBinding.principal_user_id == USER_1,
                )
            )
        )
    assert [row.module_scope for row in rows] == ["credit"]
    assert isinstance(rows[0].id, UUID)
