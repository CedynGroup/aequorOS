"""Matrix PR 3 authorization regressions for the remaining Liquidity surface.

The first seven program tests (exact allow, default deny, partial rows,
lifecycle, tenant hiding, stale authv, and evaluator telemetry) are pinned in
``test_liquidity_monitoring.py`` against the same evaluator. These tests extend
T1-T10 across the remainder: representative route coverage, permission-specific
mutations, filtered registries, hidden details, and denial before side effects.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.api.deps import TenantContext
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.models import (
    AuthorizationBinding,
    BankReportingPeriod,
    CfpActivationEvent,
    ContingencyFundingPlan,
    Notification,
    RegulatoryRun,
    User,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import authorization, regulatory_capital, regulatory_liquidity
from tests.api.helpers import ORG_1, USER_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    try:
        session.execute(delete(AuthorizationBinding))
        session.commit()
    finally:
        session.close()


def _seed_book() -> UUID:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        period_id = session.scalar(
            select(BankReportingPeriod.id)
            .where(
                BankReportingPeriod.organization_id == ORG_1,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        )
        assert period_id is not None
        session.commit()
        return period_id
    finally:
        session.close()


def _grant(
    bundle: RoleBundle,
    *,
    module: ModuleScope = ModuleScope.LIQUIDITY,
    sensitivity: SensitivityScope = SensitivityScope.ALL,
) -> int:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        user = session.get(User, USER_1)
        assert user is not None
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                module,
                sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "matrix-pr-3-test"),
            reason="exercise exact Liquidity remainder authority",
        )
        session.refresh(user)
        return user.authorization_version
    finally:
        session.close()


def _auth(version: int = 1, *roles: str) -> dict[str, str]:
    return headers(
        ORG_1,
        user_id=USER_1,
        roles=roles or ("admin",),
        authorization_version=version,
    )


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/liquidity/dashboard", {}),
        ("/liquidity/ewis", {}),
        ("/liquidity/cfp", {}),
        ("/liquidity/cfp/events", {}),
        ("/liquidity-thresholds", {}),
        ("/liquidity-haircuts", {}),
        ("/cashflow-forecast", {}),
        ("/cashflow-history", {}),
        (
            "/analytics/cashflow-window",
            {"start_date": "2026-03-01", "end_date": "2026-03-31"},
        ),
        ("/sdi/liquidity-position", {}),
        ("/scenario-workbench/liquidity/scenarios", {}),
        ("/scenario-workbench/liquidity/analyses", {}),
    ],
)
def test_t2_scalar_roles_cannot_open_remaining_liquidity_reads(
    db_client: TestClient,
    path: str,
    params: dict[str, str],
) -> None:
    _seed_book()

    response = db_client.get(f"{BASE}{path}", headers=_auth(1, "admin"), params=params)

    assert response.status_code == 403, (path, response.text)


def test_t1_exact_sensitivity_grants_allow_aggregated_and_confidential_reads(
    db_client: TestClient,
) -> None:
    _seed_book()
    aggregated_version = _grant(
        RoleBundle.VIEWER,
        sensitivity=SensitivityScope.AGGREGATED,
    )

    dashboard = db_client.get(
        f"{BASE}/liquidity/dashboard",
        headers=_auth(aggregated_version, "viewer"),
    )
    confidential_denied = db_client.get(
        f"{BASE}/liquidity-thresholds",
        headers=_auth(aggregated_version, "viewer"),
    )
    scenario_details_denied = db_client.get(
        f"{BASE}/scenario-workbench/liquidity/scenarios",
        headers=_auth(aggregated_version, "viewer"),
    )

    assert dashboard.status_code == 200, dashboard.text
    assert confidential_denied.status_code == 403
    assert scenario_details_denied.status_code == 403

    confidential_version = _grant(
        RoleBundle.VIEWER,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    thresholds = db_client.get(
        f"{BASE}/liquidity-thresholds",
        headers=_auth(confidential_version, "viewer"),
    )
    assert thresholds.status_code == 200, thresholds.text


def test_t1_t9_run_requires_analyst_binding_and_denial_writes_nothing(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    payload = {
        "module": "liquidity",
        "reporting_period_id": str(period_id),
        "scenario_code": "baseline",
    }
    session = get_sessionmaker()()
    try:
        before = session.scalar(select(func.count()).select_from(RegulatoryRun)) or 0
    finally:
        session.close()

    denied = db_client.post(
        f"{BASE}/regulatory-runs",
        headers=_auth(1, "admin"),
        json=payload,
    )
    session = get_sessionmaker()()
    try:
        after_denial = session.scalar(select(func.count()).select_from(RegulatoryRun)) or 0
    finally:
        session.close()

    assert denied.status_code == 403
    assert after_denial == before

    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    allowed = db_client.post(
        f"{BASE}/regulatory-runs",
        headers=_auth(version, "viewer"),
        json=payload,
    )
    assert allowed.status_code == 201, allowed.text


def test_t1_t9_cfp_draft_permission_and_activation_denial_are_side_effect_free(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    session = get_sessionmaker()()
    try:
        plans_before = session.scalar(select(func.count()).select_from(ContingencyFundingPlan)) or 0
        events_before = session.scalar(select(func.count()).select_from(CfpActivationEvent)) or 0
        notifications_before = session.scalar(select(func.count()).select_from(Notification)) or 0
    finally:
        session.close()

    denied_draft = db_client.put(
        f"{BASE}/liquidity/cfp",
        headers=_auth(1, "admin"),
        json={"content": {}, "reason": "must not persist"},
    )
    denied_activation = db_client.post(
        f"{BASE}/liquidity/cfp/activate",
        headers=_auth(1, "approver"),
        json={
            "reporting_period_id": str(period_id),
            "reason": "must not notify",
        },
    )

    session = get_sessionmaker()()
    try:
        assert (
            session.scalar(select(func.count()).select_from(ContingencyFundingPlan)) or 0
        ) == plans_before
        assert (
            session.scalar(select(func.count()).select_from(CfpActivationEvent)) or 0
        ) == events_before
        assert (
            session.scalar(select(func.count()).select_from(Notification)) or 0
        ) == notifications_before
    finally:
        session.close()
    assert denied_draft.status_code == 403
    assert denied_activation.status_code == 403

    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    allowed_draft = db_client.put(
        f"{BASE}/liquidity/cfp",
        headers=_auth(version, "viewer"),
        json={"content": {}, "reason": "authorized draft"},
    )
    assert allowed_draft.status_code == 200, allowed_draft.text


def test_t8_regulatory_registry_filters_liquidity_before_count_and_page(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    session = get_sessionmaker()()
    try:
        regulatory_capital.create_capital_run(
            session,
            CTX,
            SAMPLE_BANK_ID,
            RegulatoryRunCreate(
                module="capital",
                reporting_period_id=period_id,
                scenario_code="baseline",
            ),
        )
        for scenario_code in ("baseline", "combined"):
            regulatory_liquidity.create_liquidity_run(
                session,
                CTX,
                SAMPLE_BANK_ID,
                RegulatoryRunCreate(
                    module="liquidity",
                    reporting_period_id=period_id,
                    scenario_code=scenario_code,
                ),
            )
    finally:
        session.close()
    version = _grant(
        RoleBundle.VIEWER,
        module=ModuleScope.CAPITAL,
        sensitivity=SensitivityScope.AGGREGATED,
    )

    response = db_client.get(
        f"{BASE}/regulatory-runs",
        headers=_auth(version, "admin"),
        params={"limit": 1, "offset": 0},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert [run["module"] for run in body["runs"]] == ["capital"]


def test_t6_liquidity_run_detail_is_hidden_without_confidential_authority(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    session = get_sessionmaker()()
    try:
        run = regulatory_liquidity.create_liquidity_run(
            session,
            CTX,
            SAMPLE_BANK_ID,
            RegulatoryRunCreate(
                module="liquidity",
                reporting_period_id=period_id,
                scenario_code="baseline",
            ),
        )
    finally:
        session.close()

    denied = db_client.get(
        f"{BASE}/regulatory-runs/{run.id}",
        headers=_auth(1, "admin"),
    )

    assert denied.status_code == 404
    assert denied.json()["error"]["message"] == "Regulatory run not found."
