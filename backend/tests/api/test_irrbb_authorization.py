"""Scoped-binding enforcement for the IRRBB dashboard and compute routes."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from sqlalchemy import delete, func, select

from app.api.deps import TenantContext
from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.observability import Condition
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    Job,
    LiveFinding,
    LiveMetric,
    LiveMetricSnapshot,
    RegulatoryRun,
    SavedScenarioAnalysis,
    StressScenario,
    User,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import (
    analysis_workbench,
    authorization,
    data_activation,
    module_scope,
    regulatory_capital,
    regulatory_irr,
)
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/irr"
WORKBENCH_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/scenario-workbench/irr"
REGULATORY_RUNS_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs"
SIBLING_BANK_ID = "BK-IRR00002"
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
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


def _add_sibling_bank() -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.add(
            Bank(
                id=SIBLING_BANK_ID,
                organization_id=ORG_1,
                name="IRRBB sibling bank",
                short_name="IRRBB sibling",
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
    bundle: RoleBundle = RoleBundle.VIEWER,
    *,
    module: ModuleScope = ModuleScope.IRRBB,
    sensitivity: SensitivityScope = SensitivityScope.AGGREGATED,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
    institution_id: str | None = SAMPLE_BANK_ID,
) -> tuple[UUID, int]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        binding = authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                institution_scope=institution_scope,
                institution_id=institution_id,
                module_scope=module,
                sensitivity_scope=sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "irrbb-test"),
            reason="IRRBB authorization regression",
        )
        user = session.get(User, USER_1)
        assert user is not None
        return binding.id, user.authorization_version
    finally:
        session.close()


def _auth(version: int = 1, *roles: str) -> dict[str, str]:
    return headers(
        ORG_1,
        user_id=USER_1,
        roles=roles or ("admin",),
        authorization_version=version,
    )


def _dashboard(client: TestClient, version: int = 1):
    return client.get(f"{BASE}/dashboard", headers=_auth(version))


def _capture_binding_records() -> tuple[list[dict[str, object]], int]:
    records: list[dict[str, object]] = []
    return records, logger.add(
        lambda message: records.append(dict(message.record)),
        level="DEBUG",
    )


def _binding_extras(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        record["extra"]  # type: ignore[index]
        for record in records
        if record["extra"].get("condition")  # type: ignore[union-attr,index]
        == Condition.AUTHORIZATION_BINDING_DECISION.value
    ]


def test_t1_exact_and_explicit_organization_bindings_allow_aggregated_view(
    db_client: TestClient,
) -> None:
    _seed_book()
    _, exact_version = _grant()
    exact = _dashboard(db_client, exact_version)
    assert exact.status_code == 200, exact.text

    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    finally:
        session.close()
    _, organization_version = _grant(
        institution_scope=InstitutionScope.ORGANIZATION,
        institution_id=None,
    )
    organization = _dashboard(db_client, organization_version)
    assert organization.status_code == 200, organization.text


def test_t2_scalar_roles_cannot_open_irrbb_without_a_binding(
    db_client: TestClient,
) -> None:
    _seed_book()
    response = _dashboard(db_client)
    assert response.status_code == 403
    assert response.json()["error"]["message"] == (
        "IRRBB access requires an active scoped binding."
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"institution_id": SIBLING_BANK_ID},
        {"module_scope": ModuleScope.LIQUIDITY.value},
        {"sensitivity_scope": SensitivityScope.CONFIDENTIAL.value},
        {"role_bundle": RoleBundle.ACCOUNT_ADMIN.value},
    ],
)
def test_t3_partial_irrbb_binding_denies(
    db_client: TestClient,
    changes: dict[str, str],
) -> None:
    _seed_book()
    if changes.get("institution_id") == SIBLING_BANK_ID:
        _add_sibling_bank()
    binding_id, version = _grant()
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        binding = session.get(AuthorizationBinding, binding_id)
        assert binding is not None
        for name, value in changes.items():
            setattr(binding, name, value)
        session.commit()
    finally:
        session.close()

    assert _dashboard(db_client, version).status_code == 403


def test_t3_partial_bindings_never_compose_into_irrbb_authority(
    db_client: TestClient,
) -> None:
    _seed_book()
    _grant(module=ModuleScope.LIQUIDITY)
    _, version = _grant(sensitivity=SensitivityScope.CONFIDENTIAL)
    assert _dashboard(db_client, version).status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"status": BindingStatus.SUSPENDED.value},
        {
            "status": BindingStatus.REVOKED.value,
            "revoked_at": utc_now(),
            "revoked_by_type": GrantorType.SYSTEM.value,
            "revoked_by_id": "irrbb-test",
            "revoked_reason": "Exercise revoked binding denial.",
        },
        {"valid_from": utc_now() + timedelta(days=1)},
        {
            "valid_from": utc_now() - timedelta(days=2),
            "valid_until": utc_now() - timedelta(days=1),
        },
    ],
)
def test_t4_inactive_irrbb_binding_denies(
    db_client: TestClient,
    changes: dict[str, object],
) -> None:
    _seed_book()
    binding_id, version = _grant()
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        binding = session.get(AuthorizationBinding, binding_id)
        assert binding is not None
        for name, value in changes.items():
            setattr(binding, name, value)
        session.commit()
    finally:
        session.close()

    assert _dashboard(db_client, version).status_code == 403


def test_t5_cross_tenant_irrbb_probe_stays_hidden(db_client: TestClient) -> None:
    _seed_book()
    response = db_client.get(f"{BASE}/dashboard", headers=headers(ORG_2))
    assert response.status_code == 404


def test_t6_stale_authorization_version_denies_before_irrbb_evaluation(
    db_client: TestClient,
) -> None:
    _seed_book()
    _grant()
    response = _dashboard(db_client, 1)
    assert response.status_code == 401


def test_t7_evaluator_failure_denies_closed_with_telemetry(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_book()
    _, version = _grant()

    def fail_evaluation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("evaluator unavailable")

    monkeypatch.setattr(authorization, "evaluate_prefetched_permission", fail_evaluation)
    records, sink_id = _capture_binding_records()
    try:
        response = _dashboard(db_client, version)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["reason"] == "binding_evaluation_failed"
    assert decisions[0]["severity"] == "error"


def test_aggregated_view_does_not_grant_confidential_compute(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    period_id = _seed_book()
    _, version = _grant()
    called = False

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("IRRBB engine must not execute before authorization")

    monkeypatch.setattr(regulatory_irr, "run_all_irr_scenarios", fail_if_called)
    session = get_sessionmaker()()
    try:
        before = session.scalar(select(func.count()).select_from(RegulatoryRun)) or 0
    finally:
        session.close()

    response = db_client.post(
        f"{BASE}/run-all-scenarios",
        headers=_auth(version),
        json={"reporting_period_id": str(period_id)},
    )

    assert response.status_code == 403
    assert called is False
    session = get_sessionmaker()()
    try:
        after = session.scalar(select(func.count()).select_from(RegulatoryRun)) or 0
    finally:
        session.close()
    assert after == before


def test_confidential_analyst_binding_allows_compute_only_analysis(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    _, version = _grant(
        RoleBundle.ANALYST,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    response = db_client.get(
        f"{BASE}/ear-analysis",
        headers=_auth(version, "viewer"),
        params={
            "reporting_period_id": str(period_id),
            "horizon_months": 12,
            "delta_bp": 200,
        },
    )
    assert response.status_code == 200, response.text
    assert _dashboard(db_client, version).status_code == 403


def test_scenario_workbench_requires_exact_permission_per_operation(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()

    denied_catalogue = db_client.get(
        f"{WORKBENCH_BASE}/scenarios",
        headers=_auth(),
    )
    denied_run = db_client.post(
        f"{WORKBENCH_BASE}/analysis",
        headers=_auth(),
        json={
            "reporting_period_id": str(period_id),
            "scenarios": [{"kind": "system", "code": "baseline"}],
        },
    )
    assert denied_catalogue.status_code == 403
    assert denied_run.status_code == 403

    _, viewer_version = _grant(
        RoleBundle.VIEWER,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    catalogue = db_client.get(
        f"{WORKBENCH_BASE}/scenarios",
        headers=_auth(viewer_version, "admin"),
    )
    denied_create = db_client.post(
        f"{WORKBENCH_BASE}/scenarios",
        headers=_auth(viewer_version, "admin"),
        json={
            "code": "desk_parallel_up",
            "name": "Desk parallel up",
            "shocks": {"parallel_bp": 100},
        },
    )
    assert catalogue.status_code == 200, catalogue.text
    assert denied_create.status_code == 403

    _, analyst_version = _grant(
        RoleBundle.ANALYST,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    created = db_client.post(
        f"{WORKBENCH_BASE}/scenarios",
        headers=_auth(analyst_version, "viewer"),
        json={
            "code": "desk_parallel_up",
            "name": "Desk parallel up",
            "shocks": {"parallel_bp": 100},
        },
    )
    assert created.status_code == 201, created.text
    scenario_id = created.json()["id"]

    updated = db_client.patch(
        f"{WORKBENCH_BASE}/scenarios/{scenario_id}",
        headers=_auth(analyst_version, "viewer"),
        json={"name": "Desk parallel up revised"},
    )
    archived = db_client.post(
        f"{WORKBENCH_BASE}/scenarios/{scenario_id}/archive",
        headers=_auth(analyst_version, "viewer"),
        json={"is_archived": True},
    )
    run = db_client.post(
        f"{WORKBENCH_BASE}/analysis",
        headers=_auth(analyst_version, "viewer"),
        json={
            "reporting_period_id": str(period_id),
            "scenarios": [{"kind": "system", "code": "baseline"}],
        },
    )
    saved = db_client.post(
        f"{WORKBENCH_BASE}/analyses",
        headers=_auth(analyst_version, "viewer"),
        json={
            "reporting_period_id": str(period_id),
            "name": "IRRBB baseline review",
            "scenarios": [{"kind": "system", "code": "baseline"}],
        },
    )
    assert updated.status_code == 200, updated.text
    assert archived.status_code == 200, archived.text
    assert run.status_code == 200, run.text
    assert saved.status_code == 201, saved.text
    analysis_id = saved.json()["id"]

    listed = db_client.get(
        f"{WORKBENCH_BASE}/analyses",
        headers=_auth(analyst_version, "viewer"),
    )
    detail = db_client.get(
        f"{WORKBENCH_BASE}/analyses/{analysis_id}",
        headers=_auth(analyst_version, "viewer"),
    )
    deleted = db_client.delete(
        f"{WORKBENCH_BASE}/analyses/{analysis_id}",
        headers=_auth(analyst_version, "viewer"),
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert detail.status_code == 200, detail.text
    assert deleted.status_code == 204, deleted.text


def test_workbench_denial_precedes_engine_and_persistence(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    period_id = _seed_book()
    engine_called = False

    def forbidden_compute(*_args: object, **_kwargs: object) -> None:
        nonlocal engine_called
        engine_called = True
        raise AssertionError("Unauthorized IRRBB workbench computation")

    monkeypatch.setattr(analysis_workbench, "_compute_one", forbidden_compute)
    session = get_sessionmaker()()
    try:
        before = (
            session.scalar(select(func.count()).select_from(StressScenario)) or 0,
            session.scalar(select(func.count()).select_from(SavedScenarioAnalysis)) or 0,
        )
    finally:
        session.close()

    denied_create = db_client.post(
        f"{WORKBENCH_BASE}/scenarios",
        headers=_auth(),
        json={
            "code": "must_not_persist",
            "name": "Must not persist",
            "shocks": {"parallel_bp": 100},
        },
    )
    denied_save = db_client.post(
        f"{WORKBENCH_BASE}/analyses",
        headers=_auth(),
        json={
            "reporting_period_id": str(period_id),
            "name": "Must not persist",
            "scenarios": [{"kind": "system", "code": "baseline"}],
        },
    )
    assert denied_create.status_code == 403
    assert denied_save.status_code == 403
    assert engine_called is False

    session = get_sessionmaker()()
    try:
        after = (
            session.scalar(select(func.count()).select_from(StressScenario)) or 0,
            session.scalar(select(func.count()).select_from(SavedScenarioAnalysis)) or 0,
        )
    finally:
        session.close()
    assert after == before


def test_regulatory_registry_filters_irrbb_before_count_and_page(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    session = get_sessionmaker()()
    try:
        regulatory_irr.create_irr_run(
            session,
            CTX,
            SAMPLE_BANK_ID,
            RegulatoryRunCreate.model_construct(
                module="irr",
                reporting_period_id=period_id,
                scenario_code="baseline",
            ),
        )
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
    finally:
        session.close()
    _, version = _grant(
        RoleBundle.VIEWER,
        module=ModuleScope.CAPITAL,
        sensitivity=SensitivityScope.AGGREGATED,
    )

    response = db_client.get(
        REGULATORY_RUNS_BASE,
        headers=_auth(version, "admin"),
        params={"limit": 1, "offset": 0},
    )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [run["module"] for run in response.json()["runs"]] == ["capital"]


def test_irrbb_regulatory_run_detail_is_hidden_without_confidential_view(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    session = get_sessionmaker()()
    try:
        run = regulatory_irr.create_irr_run(
            session,
            CTX,
            SAMPLE_BANK_ID,
            RegulatoryRunCreate.model_construct(
                module="irr",
                reporting_period_id=period_id,
                scenario_code="baseline",
            ),
        )
    finally:
        session.close()
    _, version = _grant(
        RoleBundle.VIEWER,
        sensitivity=SensitivityScope.AGGREGATED,
    )

    response = db_client.get(
        f"{REGULATORY_RUNS_BASE}/{run.id}",
        headers=_auth(version, "viewer"),
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Regulatory run not found."


@pytest.mark.parametrize("operation", ["official-runs", "data-activations"])
@pytest.mark.parametrize("irr_in_plan", [False, True])
@pytest.mark.parametrize("irr_authority", [False, True])
def test_mixed_execution_requires_irrbb_only_in_plan(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    irr_in_plan: bool,
    irr_authority: bool,
) -> None:
    _seed_book()
    _, version = _grant(
        RoleBundle.ANALYST, module=ModuleScope.LIQUIDITY, sensitivity=SensitivityScope.CONFIDENTIAL
    )
    if irr_authority:
        _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    original = module_scope.runs_module
    monkeypatch.setattr(
        module_scope,
        "runs_module",
        lambda db, bank, module: irr_in_plan if module == "irr" else original(db, bank, module),
    )
    denied = irr_in_plan and not irr_authority
    if denied:

        def forbid_derivation(*args, **kwargs):
            pytest.fail("Unauthorized derivation")

        monkeypatch.setattr(data_activation, "derive_facts", forbid_derivation)
    models = (BankFinancialFact, RegulatoryRun, AuditEvent, Job)
    with get_sessionmaker()() as session:
        before = [session.scalar(select(func.count()).select_from(model)) for model in models]
    payload: dict[str, str | bool] = {"as_of_date": "2026-03-31", "reason": "mixed filing request"}
    if operation == "data-activations":
        payload["run_calculations"] = True
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/{operation}",
        headers=_auth(version, "analyst"),
        json=payload,
    )
    if denied:
        assert response.status_code == 403, response.text
        with get_sessionmaker()() as session:
            assert [
                session.scalar(select(func.count()).select_from(model)) for model in models
            ] == before
    elif operation == "official-runs":
        assert response.status_code == 202, response.text
    else:
        assert response.status_code == 409, response.text
        assert response.json()["error"]["details"]["error_code"] == "no_canonical_data"


@pytest.mark.parametrize("irr_binding", [None, "confidential", "sibling", "aggregated"])
def test_shared_reads_filter_irrbb_before_counts_limits_and_aggregation(
    db_client: TestClient,
    irr_binding: str | None,
) -> None:
    period_id = _seed_book()
    _add_sibling_bank()
    with get_sessionmaker()() as session:
        for model in (LiveMetric, LiveMetricSnapshot, LiveFinding):
            session.execute(delete(model))
        for module, key in (("irr", "eve_limit_pct"), ("capital", "car_pct")):
            session.add(
                LiveMetric(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    module=module,
                    metrics={key: 123},
                    status="green",
                    computed_at=utc_now(),
                )
            )
            session.add(
                LiveMetricSnapshot(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    module=module,
                    reporting_period_id=period_id,
                    snapshot_date=date(2026, 3, 31),
                    metrics={key: 123},
                    status="green",
                    computed_at=utc_now(),
                )
            )
            session.add(
                LiveFinding(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    module=module,
                    rule_id=f"{module}_breach",
                    severity="critical" if module == "irr" else "high",
                    message=f"{module} breach",
                )
            )
        session.commit()
    _, version = _grant(module=ModuleScope.CAPITAL)
    if irr_binding is not None:
        _, version = _grant(
            sensitivity=SensitivityScope.CONFIDENTIAL
            if irr_binding == "confidential"
            else SensitivityScope.AGGREGATED,
            institution_id=SIBLING_BANK_ID if irr_binding == "sibling" else SAMPLE_BANK_ID,
        )
    allowed = irr_binding == "aggregated"
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}"
    auth = _auth(version, "analyst")
    summary = db_client.get(f"{base}/live-summary", headers=auth)
    assert summary.status_code == 200, summary.text
    assert {row["module"] for row in summary.json()["modules"]} == (
        {"capital", "irr"} if allowed else {"capital"}
    )
    snapshots = db_client.get(f"{base}/live-snapshots?module=irr", headers=auth)
    assert snapshots.status_code == (200 if allowed else 403), snapshots.text
    if allowed:
        assert snapshots.json()["snapshots"][0]["metrics"] == {"eve_limit_pct": 123}
    alerts = db_client.get(f"{base}/alerts?limit=1", headers=auth)
    assert alerts.status_code == 200, alerts.text
    assert alerts.json()["total"] == (2 if allowed else 1)
    assert alerts.json()["by_module"] == ({"irr": 1, "capital": 1} if allowed else {"capital": 1})
    assert alerts.json()["items"][0]["module"] == ("irr" if allowed else "capital")
    window = db_client.get(
        f"{base}/analytics/window",
        headers=auth,
        params={"start_date": "2026-03-31", "end_date": "2026-03-31"},
    )
    assert window.status_code == 200, window.text
    assert {row["module"] for row in window.json()["daily"]} == (
        {"capital", "irr"} if allowed else {"capital"}
    )
