"""Scoped-binding enforcement for FTP dashboards, runs, and workbench routes."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
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
from app.schemas.regulatory_ftp import FtpScenarioBatchCreate
from app.schemas.regulatory_liquidity import RegulatoryRunBatchRead, RegulatoryRunCreate
from app.services import (
    analysis_workbench,
    authorization,
    data_activation,
    module_scope,
    pipeline,
    regulatory_capital,
    regulatory_ftp,
    scheduler,
)
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/ftp"
WORKBENCH_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/scenario-workbench/ftp"
REGULATORY_RUNS_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs"
SIBLING_BANK_ID = "BK-FTP00002"
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
                name="FTP sibling bank",
                short_name="FTP sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()


def _add_regulatory_run(period_id: UUID, scenario_code: str = "baseline") -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        run = RegulatoryRun(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            reporting_period_id=period_id,
            module="ftp",
            scenario_code=scenario_code,
            status="succeeded",
            engine_version="test-ftp-v1",
            input_schema_version="test-input-v1",
            output_schema_version="test-output-v1",
            input_hash=uuid4().hex * 2,
            inputs={},
            metrics={},
            parameter_provenance=[],
            created_by=USER_1,
        )
        session.add(run)
        session.commit()
        return run.id
    finally:
        session.close()


def _grant(
    bundle: RoleBundle = RoleBundle.VIEWER,
    *,
    module: ModuleScope = ModuleScope.FTP,
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
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "ftp-test"),
            reason="FTP authorization regression",
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


def test_t2_scalar_roles_cannot_open_ftp_without_a_binding(db_client: TestClient) -> None:
    _seed_book()
    response = _dashboard(db_client)
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "FTP access requires an active scoped binding."


@pytest.mark.parametrize(
    "changes",
    [
        {"institution_id": SIBLING_BANK_ID},
        {"module_scope": ModuleScope.IRRBB.value},
        {"sensitivity_scope": SensitivityScope.CONFIDENTIAL.value},
        {"role_bundle": RoleBundle.ACCOUNT_ADMIN.value},
    ],
)
def test_t3_partial_ftp_binding_denies(
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


def test_t3_partial_bindings_never_compose_into_ftp_authority(
    db_client: TestClient,
) -> None:
    _seed_book()
    _grant(module=ModuleScope.IRRBB)
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
            "revoked_by_id": "ftp-test",
            "revoked_reason": "Exercise revoked binding denial.",
        },
        {"valid_from": utc_now() + timedelta(days=1)},
        {
            "valid_from": utc_now() - timedelta(days=2),
            "valid_until": utc_now() - timedelta(days=1),
        },
    ],
)
def test_t4_inactive_ftp_binding_denies(
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


def test_t5_cross_tenant_ftp_probe_stays_hidden(db_client: TestClient) -> None:
    _seed_book()
    response = db_client.get(f"{BASE}/dashboard", headers=headers(ORG_2))
    assert response.status_code == 404


def test_t6_stale_authorization_version_denies_before_ftp_evaluation(
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


def test_aggregated_view_does_not_grant_confidential_run(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    period_id = _seed_book()
    _, version = _grant()
    calls: list[str] = []

    def forbidden_run(
        _db: object,
        _ctx: object,
        bank_id: str,
        payload: FtpScenarioBatchCreate,
    ) -> RegulatoryRunBatchRead:
        calls.append(bank_id)
        return RegulatoryRunBatchRead(
            bank_id=bank_id,
            reporting_period_id=payload.reporting_period_id,
            runs=[],
        )

    monkeypatch.setattr(regulatory_ftp, "run_all_ftp_scenarios", forbidden_run)
    with get_sessionmaker()() as session:
        before = session.scalar(select(func.count()).select_from(RegulatoryRun)) or 0

    response = db_client.post(
        f"{BASE}/run-all-scenarios",
        headers=_auth(version),
        json={"reporting_period_id": str(period_id)},
    )

    assert response.status_code == 403
    assert calls == []
    with get_sessionmaker()() as session:
        assert (session.scalar(select(func.count()).select_from(RegulatoryRun)) or 0) == before


def test_scenario_workbench_requires_exact_permission_per_operation(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()

    denied_catalogue = db_client.get(f"{WORKBENCH_BASE}/scenarios", headers=_auth())
    denied_list = db_client.get(f"{WORKBENCH_BASE}/analyses", headers=_auth())
    denied_run = db_client.post(
        f"{WORKBENCH_BASE}/analysis",
        headers=_auth(),
        json={
            "reporting_period_id": str(period_id),
            "scenarios": [{"kind": "system", "code": "baseline"}],
        },
    )
    assert denied_catalogue.status_code == 403
    assert denied_list.status_code == 403
    assert denied_run.status_code == 403

    _, aggregated_version = _grant()
    aggregated_headers = _auth(aggregated_version, "admin")
    listed = db_client.get(f"{WORKBENCH_BASE}/analyses", headers=aggregated_headers)
    denied_catalogue = db_client.get(f"{WORKBENCH_BASE}/scenarios", headers=aggregated_headers)
    assert listed.status_code == 200, listed.text
    assert denied_catalogue.status_code == 403

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
            "code": "desk_curve_up",
            "name": "Desk curve up",
            "shocks": {"curve_shift_bp": 100},
        },
    )
    assert catalogue.status_code == 200, catalogue.text
    assert denied_create.status_code == 403

    _, analyst_version = _grant(
        RoleBundle.ANALYST,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    analyst_headers = _auth(analyst_version, "viewer")
    created = db_client.post(
        f"{WORKBENCH_BASE}/scenarios",
        headers=analyst_headers,
        json={
            "code": "desk_curve_up",
            "name": "Desk curve up",
            "shocks": {"curve_shift_bp": 100},
        },
    )
    assert created.status_code == 201, created.text
    scenario_id = created.json()["id"]

    updated = db_client.patch(
        f"{WORKBENCH_BASE}/scenarios/{scenario_id}",
        headers=analyst_headers,
        json={"name": "Desk curve up revised"},
    )
    archived = db_client.post(
        f"{WORKBENCH_BASE}/scenarios/{scenario_id}/archive",
        headers=analyst_headers,
        json={"is_archived": True},
    )
    run = db_client.post(
        f"{WORKBENCH_BASE}/analysis",
        headers=analyst_headers,
        json={
            "reporting_period_id": str(period_id),
            "scenarios": [{"kind": "system", "code": "baseline"}],
        },
    )
    saved = db_client.post(
        f"{WORKBENCH_BASE}/analyses",
        headers=analyst_headers,
        json={
            "reporting_period_id": str(period_id),
            "name": "FTP baseline review",
            "scenarios": [{"kind": "system", "code": "baseline"}],
        },
    )
    assert updated.status_code == 200, updated.text
    assert archived.status_code == 200, archived.text
    assert run.status_code == 200, run.text
    assert saved.status_code == 201, saved.text
    analysis_id = saved.json()["id"]

    detail = db_client.get(
        f"{WORKBENCH_BASE}/analyses/{analysis_id}",
        headers=analyst_headers,
    )
    deleted = db_client.delete(
        f"{WORKBENCH_BASE}/analyses/{analysis_id}",
        headers=analyst_headers,
    )
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
        raise AssertionError("Unauthorized FTP workbench computation")

    monkeypatch.setattr(analysis_workbench, "_compute_one", forbidden_compute)
    with get_sessionmaker()() as session:
        before = (
            session.scalar(select(func.count()).select_from(StressScenario)) or 0,
            session.scalar(select(func.count()).select_from(SavedScenarioAnalysis)) or 0,
        )

    denied_create = db_client.post(
        f"{WORKBENCH_BASE}/scenarios",
        headers=_auth(),
        json={
            "code": "must_not_persist",
            "name": "Must not persist",
            "shocks": {"curve_shift_bp": 100},
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

    with get_sessionmaker()() as session:
        after = (
            session.scalar(select(func.count()).select_from(StressScenario)) or 0,
            session.scalar(select(func.count()).select_from(SavedScenarioAnalysis)) or 0,
        )
    assert after == before


def test_regulatory_registry_filters_ftp_before_count_and_page(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    _add_regulatory_run(period_id)
    _add_regulatory_run(period_id, "rates_up_200")
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


def test_ftp_regulatory_run_detail_is_hidden_without_confidential_view(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    run_id = _add_regulatory_run(period_id)
    _, version = _grant(
        RoleBundle.VIEWER,
        sensitivity=SensitivityScope.AGGREGATED,
    )

    response = db_client.get(
        f"{REGULATORY_RUNS_BASE}/{run_id}",
        headers=_auth(version, "viewer"),
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Regulatory run not found."


@pytest.mark.parametrize("operation", ["official-runs", "data-activations"])
@pytest.mark.parametrize("ftp_in_plan", [False, True])
@pytest.mark.parametrize("ftp_authority", [False, True])
def test_mixed_execution_requires_ftp_only_in_plan(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    ftp_in_plan: bool,
    ftp_authority: bool,
) -> None:
    _seed_book()
    _grant(
        RoleBundle.ANALYST,
        module=ModuleScope.IRRBB,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    _grant(
        RoleBundle.ANALYST,
        module=ModuleScope.FX,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    _grant(
        RoleBundle.ANALYST,
        module=ModuleScope.FORECASTING,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    _, version = _grant(
        RoleBundle.ANALYST,
        module=ModuleScope.LIQUIDITY,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    if ftp_authority:
        _, version = _grant(
            RoleBundle.ANALYST,
            sensitivity=SensitivityScope.CONFIDENTIAL,
        )
    original = module_scope.runs_module
    monkeypatch.setattr(
        module_scope,
        "runs_module",
        lambda db, bank, module: ftp_in_plan if module == "ftp" else original(db, bank, module),
    )
    denied = ftp_in_plan and not ftp_authority
    if denied:

        def forbid_derivation(*_args: object, **_kwargs: object) -> None:
            pytest.fail("Unauthorized derivation")

        monkeypatch.setattr(data_activation, "derive_facts", forbid_derivation)
    models = (BankFinancialFact, RegulatoryRun, AuditEvent, Job)
    with get_sessionmaker()() as session:
        before = [session.scalar(select(func.count()).select_from(model)) for model in models]
    payload: dict[str, str | bool] = {
        "as_of_date": "2026-03-31",
        "reason": "mixed filing request",
    }
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


@pytest.mark.parametrize("ftp_binding", [None, "confidential", "sibling", "aggregated"])
def test_shared_reads_filter_ftp_before_counts_limits_and_aggregation(
    db_client: TestClient,
    ftp_binding: str | None,
) -> None:
    period_id = _seed_book()
    _add_sibling_bank()
    with get_sessionmaker()() as session:
        for model in (LiveMetric, LiveMetricSnapshot, LiveFinding):
            session.execute(delete(model))
        for module, key in (("ftp", "portfolio_nim_pct"), ("capital", "car_pct")):
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
                    severity="critical" if module == "ftp" else "high",
                    message=f"{module} breach",
                )
            )
        session.commit()
    _, version = _grant(module=ModuleScope.CAPITAL)
    if ftp_binding is not None:
        _, version = _grant(
            sensitivity=(
                SensitivityScope.CONFIDENTIAL
                if ftp_binding == "confidential"
                else SensitivityScope.AGGREGATED
            ),
            institution_id=SIBLING_BANK_ID if ftp_binding == "sibling" else SAMPLE_BANK_ID,
        )
    allowed = ftp_binding == "aggregated"
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}"
    auth = _auth(version, "analyst")
    summary = db_client.get(f"{base}/live-summary", headers=auth)
    assert summary.status_code == 200, summary.text
    assert {row["module"] for row in summary.json()["modules"]} == (
        {"capital", "ftp"} if allowed else {"capital"}
    )
    snapshots = db_client.get(f"{base}/live-snapshots?module=ftp", headers=auth)
    assert snapshots.status_code == (200 if allowed else 403), snapshots.text
    if allowed:
        assert snapshots.json()["snapshots"][0]["metrics"] == {"portfolio_nim_pct": 123}
    alerts = db_client.get(f"{base}/alerts?limit=1", headers=auth)
    assert alerts.status_code == 200, alerts.text
    assert alerts.json()["total"] == (2 if allowed else 1)
    assert alerts.json()["by_module"] == ({"ftp": 1, "capital": 1} if allowed else {"capital": 1})
    assert alerts.json()["items"][0]["module"] == ("ftp" if allowed else "capital")
    window = db_client.get(
        f"{base}/analytics/window",
        headers=auth,
        params={"start_date": "2026-03-31", "end_date": "2026-03-31"},
    )
    assert window.status_code == 200, window.text
    assert {row["module"] for row in window.json()["daily"]} == (
        {"capital", "ftp"} if allowed else {"capital"}
    )


def test_queued_ftp_requires_run_before_any_execution(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    period_id = _seed_book()
    _grant(RoleBundle.ANALYST, module=ModuleScope.FX, sensitivity=SensitivityScope.CONFIDENTIAL)
    _grant(
        RoleBundle.ANALYST,
        module=ModuleScope.FORECASTING,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    _, version = _grant(sensitivity=SensitivityScope.CONFIDENTIAL)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Unauthorized FTP execution reached a side effect")

    monkeypatch.setattr(pipeline, "derive_facts", forbidden)
    monkeypatch.setattr(data_activation, "run_official_modules", forbidden)
    monkeypatch.setattr(regulatory_ftp, "_create_and_execute", forbidden)
    with get_sessionmaker()() as session:
        ctx = TenantContext(
            organization_id=ORG_1, actor_user_id=USER_1, authorization_version=version
        )
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        assert scheduler._scheduled_official_actor(session, bank) is None
        with pytest.raises(HTTPException) as denied:
            regulatory_ftp.run_all_ftp_scenarios(
                session, ctx, bank.id, FtpScenarioBatchCreate(reporting_period_id=period_id)
            )
        assert denied.value.status_code == 403
        job = Job(
            organization_id=ORG_1,
            bank_id=bank.id,
            job_type="official_run",
            payload={"actor_user_id": str(USER_1), "as_of_date": "2030-01-01"},
        )
        with pytest.raises(HTTPException) as denied:
            pipeline.run_official(session, job)
        assert denied.value.status_code == 403
        assert session.scalar(select(func.count()).select_from(RegulatoryRun)) == 0
    _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    with get_sessionmaker()() as session:
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        actor = scheduler._scheduled_official_actor(session, bank)
        assert actor is not None
        assert actor.id == USER_1
