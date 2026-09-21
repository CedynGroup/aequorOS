"""Scoped-binding enforcement for Forecasting and reverse-stress routes.

One complete Forecasting binding decides every route: aggregated view for the
scenario presets and run summaries, confidential view for a full run or the
reverse-stress frontier, confidential run for anything that mints a run. The
suite also pins the shared consumers — live projections, the regulatory-run
registry, the capital-plan projection, and queued official runs — which filter
or deny Forecasting output server-side before any count, page, or side effect.
"""

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
    User,
)
from app.schemas.forecasting import ForecastRunCreate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.reverse_stress import ReverseStressRunCreate
from app.services import (
    authorization,
    data_activation,
    module_scope,
    pipeline,
    regulatory_capital,
    regulatory_forecasting,
    reverse_stress,
    scheduler,
)
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast"
REVERSE_STRESS_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/reverse-stress"
REGULATORY_RUNS_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs"
SIBLING_BANK_ID = "BK-FCST0002"
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
FORECAST_RUN_MODULES = ("forecast", "optimizer", "whatif", "reverse_stress")


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
                name="Forecasting sibling bank",
                short_name="Forecasting sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()


def _add_regulatory_run(
    period_id: UUID, module: str = "forecast", scenario_code: str = "base"
) -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        run = RegulatoryRun(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            reporting_period_id=period_id,
            module=module,
            scenario_code=scenario_code,
            status="succeeded",
            engine_version="test-forecasting-v1",
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
    module: ModuleScope = ModuleScope.FORECASTING,
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
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "forecasting-test"),
            reason="Forecasting authorization regression",
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


def _scenarios(client: TestClient, version: int = 1):
    return client.get(f"{BASE}/scenarios", headers=_auth(version))


def _shape(body: dict[str, dict[str, str]]) -> dict[str, str]:
    """The error body without its per-request id, for hidden-vs-missing comparison."""
    return {key: value for key, value in body["error"].items() if key != "request_id"}


def _count(model: type) -> int:
    with get_sessionmaker()() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


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
    exact = _scenarios(db_client, exact_version)
    assert exact.status_code == 200, exact.text
    assert exact.json()["bank_id"] == SAMPLE_BANK_ID

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
    organization = _scenarios(db_client, organization_version)
    assert organization.status_code == 200, organization.text
    runs = db_client.get(f"{BASE}/runs", headers=_auth(organization_version))
    assert runs.status_code == 200, runs.text


def test_t2_scalar_roles_cannot_open_forecasting_without_a_binding(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    for path in (f"{BASE}/scenarios", f"{BASE}/runs"):
        response = db_client.get(path, headers=_auth())
        assert response.status_code == 403, response.text
        assert (
            response.json()["error"]["message"]
            == "Forecasting access requires an active scoped binding."
        )
    latest = db_client.get(
        f"{REVERSE_STRESS_BASE}/latest",
        headers=_auth(),
        params={"reporting_period_id": str(period_id)},
    )
    assert latest.status_code == 403, latest.text


@pytest.mark.parametrize(
    "changes",
    [
        {"institution_id": SIBLING_BANK_ID},
        {"module_scope": ModuleScope.FTP.value},
        {"sensitivity_scope": SensitivityScope.CONFIDENTIAL.value},
        {"role_bundle": RoleBundle.ACCOUNT_ADMIN.value},
    ],
)
def test_t3_partial_forecasting_binding_denies(
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

    assert _scenarios(db_client, version).status_code == 403


def test_t3_partial_bindings_never_compose_into_forecasting_authority(
    db_client: TestClient,
) -> None:
    _seed_book()
    _grant(module=ModuleScope.FTP)
    _, version = _grant(sensitivity=SensitivityScope.CONFIDENTIAL)
    assert _scenarios(db_client, version).status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"status": BindingStatus.SUSPENDED.value},
        {
            "status": BindingStatus.REVOKED.value,
            "revoked_at": utc_now(),
            "revoked_by_type": GrantorType.SYSTEM.value,
            "revoked_by_id": "forecasting-test",
            "revoked_reason": "Exercise revoked binding denial.",
        },
        {"valid_from": utc_now() + timedelta(days=1)},
        {
            "valid_from": utc_now() - timedelta(days=2),
            "valid_until": utc_now() - timedelta(days=1),
        },
    ],
)
def test_t4_inactive_forecasting_binding_denies(
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

    assert _scenarios(db_client, version).status_code == 403


def test_t5_cross_tenant_forecasting_probe_stays_hidden(db_client: TestClient) -> None:
    period_id = _seed_book()
    run_id = _add_regulatory_run(period_id)
    other = headers(ORG_2)
    assert db_client.get(f"{BASE}/scenarios", headers=other).status_code == 404
    assert db_client.get(f"{BASE}/runs/{run_id}", headers=other).status_code == 404
    assert (
        db_client.get(
            f"{REVERSE_STRESS_BASE}/latest",
            headers=other,
            params={"reporting_period_id": str(period_id)},
        ).status_code
        == 404
    )


def test_t6_stale_authorization_version_denies_before_forecasting_evaluation(
    db_client: TestClient,
) -> None:
    _seed_book()
    _grant()
    response = _scenarios(db_client, 1)
    assert response.status_code == 401


def test_t7_evaluator_failure_denies_closed_with_telemetry(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_book()
    _, version = _grant()

    def fail_evaluation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("evaluator unavailable")

    monkeypatch.setattr(authorization, "evaluate_permission", fail_evaluation)
    records, sink_id = _capture_binding_records()
    try:
        response = _scenarios(db_client, version)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["reason"] == "binding_evaluation_failed"
    assert decisions[0]["severity"] == "error"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("forecast/runs", {"scenario_code": "base"}),
        ("forecast/optimizer", {}),
        ("forecast/whatif", {"shock_code": "rate_shock_up_400"}),
        ("reverse-stress/runs", {}),
    ],
)
def test_t8_aggregated_view_does_not_grant_confidential_run(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, str],
) -> None:
    period_id = _seed_book()
    _, version = _grant()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Unauthorized Forecasting execution reached a side effect")

    for target, name in (
        (regulatory_forecasting, "_create_run_row"),
        (regulatory_forecasting, "_load_facts"),
        (reverse_stress, "_hash"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    before = (_count(RegulatoryRun), _count(AuditEvent))

    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/{path}",
        headers=_auth(version),
        json={"reporting_period_id": str(period_id), **payload},
    )

    assert response.status_code == 403, response.text
    assert (
        response.json()["error"]["message"]
        == "Running Forecasting calculations requires an active scoped binding."
    )
    assert (_count(RegulatoryRun), _count(AuditEvent)) == before


def test_confidential_view_does_not_grant_run_and_analyst_mints_every_run(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    _, viewer_version = _grant(sensitivity=SensitivityScope.CONFIDENTIAL)
    denied = db_client.post(
        f"{BASE}/runs",
        headers=_auth(viewer_version, "viewer"),
        json={"reporting_period_id": str(period_id), "scenario_code": "base"},
    )
    assert denied.status_code == 403, denied.text

    _, analyst_version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    analyst = _auth(analyst_version, "viewer")
    created = db_client.post(
        f"{BASE}/runs",
        headers=analyst,
        json={"reporting_period_id": str(period_id), "scenario_code": "base"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "succeeded"
    optimizer = db_client.post(
        f"{BASE}/optimizer", headers=analyst, json={"reporting_period_id": str(period_id)}
    )
    assert optimizer.status_code == 201, optimizer.text
    whatif = db_client.post(
        f"{BASE}/whatif",
        headers=analyst,
        json={"reporting_period_id": str(period_id), "shock_code": "rate_shock_up_400"},
    )
    assert whatif.status_code == 201, whatif.text
    frontier = db_client.post(
        f"{REVERSE_STRESS_BASE}/runs",
        headers=analyst,
        json={"reporting_period_id": str(period_id)},
    )
    assert frontier.status_code == 201, frontier.text

    detail = db_client.get(f"{BASE}/runs/{created.json()['id']}", headers=analyst)
    assert detail.status_code == 200, detail.text
    latest = db_client.get(
        f"{REVERSE_STRESS_BASE}/latest",
        headers=analyst,
        params={"reporting_period_id": str(period_id)},
    )
    assert latest.status_code == 200, latest.text
    assert latest.json()["run_id"] == frontier.json()["run_id"]


def test_run_detail_and_frontier_are_hidden_without_confidential_view(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    run_id = _add_regulatory_run(period_id)
    _add_regulatory_run(period_id, module="reverse_stress", scenario_code="frontier")
    _, version = _grant()
    auth = _auth(version, "viewer")

    listed = db_client.get(f"{BASE}/runs", headers=auth)
    assert listed.status_code == 200, listed.text
    assert [run["id"] for run in listed.json()["runs"]] == [str(run_id)]

    detail = db_client.get(f"{BASE}/runs/{run_id}", headers=auth)
    assert detail.status_code == 404
    assert detail.json()["error"]["message"] == "Regulatory run not found."
    unknown = db_client.get(f"{BASE}/runs/{uuid4()}", headers=auth)
    assert unknown.status_code == 404
    assert _shape(unknown.json()) == _shape(detail.json())

    registry_detail = db_client.get(f"{REGULATORY_RUNS_BASE}/{run_id}", headers=auth)
    assert registry_detail.status_code == 404
    assert registry_detail.json()["error"]["message"] == "Regulatory run not found."

    frontier = db_client.get(
        f"{REVERSE_STRESS_BASE}/latest",
        headers=auth,
        params={"reporting_period_id": str(period_id)},
    )
    assert frontier.status_code == 403, frontier.text


def test_regulatory_registry_filters_every_forecasting_module_before_count_and_page(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    for module in FORECAST_RUN_MODULES:
        _add_regulatory_run(period_id, module=module)
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
    auth = _auth(version, "admin")

    response = db_client.get(REGULATORY_RUNS_BASE, headers=auth, params={"limit": 1})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [run["module"] for run in response.json()["runs"]] == ["capital"]
    for module in FORECAST_RUN_MODULES:
        filtered = db_client.get(REGULATORY_RUNS_BASE, headers=auth, params={"module": module})
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] == 0

    _, version = _grant()
    response = db_client.get(REGULATORY_RUNS_BASE, headers=_auth(version, "admin"))
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1 + len(FORECAST_RUN_MODULES)


@pytest.mark.parametrize("operation", ["official-runs", "data-activations"])
@pytest.mark.parametrize("forecast_in_plan", [False, True])
@pytest.mark.parametrize("forecast_authority", [False, True])
def test_mixed_execution_requires_forecasting_only_in_plan(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    forecast_in_plan: bool,
    forecast_authority: bool,
) -> None:
    _seed_book()
    version = 1
    for module in (ModuleScope.IRRBB, ModuleScope.FX, ModuleScope.FTP, ModuleScope.LIQUIDITY):
        _, version = _grant(
            RoleBundle.ANALYST,
            module=module,
            sensitivity=SensitivityScope.CONFIDENTIAL,
        )
    if forecast_authority:
        _, version = _grant(
            RoleBundle.ANALYST,
            sensitivity=SensitivityScope.CONFIDENTIAL,
        )
    original = module_scope.runs_module
    monkeypatch.setattr(
        module_scope,
        "runs_module",
        lambda db, bank, module: (
            forecast_in_plan if module == "forecast" else original(db, bank, module)
        ),
    )
    denied = forecast_in_plan and not forecast_authority
    if denied:

        def forbid_derivation(*_args: object, **_kwargs: object) -> None:
            pytest.fail("Unauthorized derivation")

        monkeypatch.setattr(data_activation, "derive_facts", forbid_derivation)
    models = (BankFinancialFact, RegulatoryRun, AuditEvent, Job)
    before = [_count(model) for model in models]
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
        assert [_count(model) for model in models] == before
    elif operation == "official-runs":
        assert response.status_code == 202, response.text
    else:
        assert response.status_code == 409, response.text
        assert response.json()["error"]["details"]["error_code"] == "no_canonical_data"


@pytest.mark.parametrize("forecasting_binding", [None, "confidential", "sibling", "aggregated"])
def test_shared_reads_filter_forecasting_before_counts_limits_and_aggregation(
    db_client: TestClient,
    forecasting_binding: str | None,
) -> None:
    period_id = _seed_book()
    _add_sibling_bank()
    with get_sessionmaker()() as session:
        for model in (LiveMetric, LiveMetricSnapshot, LiveFinding):
            session.execute(delete(model))
        for module, key in (("forecast", "year5_car_pct"), ("capital", "car_pct")):
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
                    severity="critical" if module == "forecast" else "high",
                    message=f"{module} breach",
                )
            )
        session.commit()
    _, version = _grant(module=ModuleScope.CAPITAL)
    if forecasting_binding is not None:
        _, version = _grant(
            sensitivity=(
                SensitivityScope.CONFIDENTIAL
                if forecasting_binding == "confidential"
                else SensitivityScope.AGGREGATED
            ),
            institution_id=(
                SIBLING_BANK_ID if forecasting_binding == "sibling" else SAMPLE_BANK_ID
            ),
        )
    allowed = forecasting_binding == "aggregated"
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}"
    auth = _auth(version, "analyst")
    summary = db_client.get(f"{base}/live-summary", headers=auth)
    assert summary.status_code == 200, summary.text
    assert {row["module"] for row in summary.json()["modules"]} == (
        {"capital", "forecast"} if allowed else {"capital"}
    )
    snapshots = db_client.get(f"{base}/live-snapshots?module=forecast", headers=auth)
    assert snapshots.status_code == (200 if allowed else 403), snapshots.text
    if allowed:
        assert snapshots.json()["snapshots"][0]["metrics"] == {"year5_car_pct": 123}
    alerts = db_client.get(f"{base}/alerts?limit=1", headers=auth)
    assert alerts.status_code == 200, alerts.text
    assert alerts.json()["total"] == (2 if allowed else 1)
    assert alerts.json()["by_module"] == (
        {"forecast": 1, "capital": 1} if allowed else {"capital": 1}
    )
    assert alerts.json()["items"][0]["module"] == ("forecast" if allowed else "capital")
    window = db_client.get(
        f"{base}/analytics/window",
        headers=auth,
        params={"start_date": "2026-03-31", "end_date": "2026-03-31"},
    )
    assert window.status_code == 200, window.text
    assert {row["module"] for row in window.json()["daily"]} == (
        {"capital", "forecast"} if allowed else {"capital"}
    )


@pytest.mark.parametrize("forecasting_view", [False, True])
def test_capital_plan_withholds_only_the_projection_without_forecasting_view(
    db_client: TestClient,
    forecasting_view: bool,
) -> None:
    period_id = _seed_book()
    _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    with get_sessionmaker()() as session:
        run = regulatory_forecasting.create_forecast_run(
            session,
            TenantContext(
                organization_id=ORG_1, actor_user_id=USER_1, authorization_version=version
            ),
            SAMPLE_BANK_ID,
            ForecastRunCreate(reporting_period_id=period_id, scenario_code="base"),
        )
        assert run.status == "succeeded", run
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    _, version = _grant(module=ModuleScope.CAPITAL, sensitivity=SensitivityScope.CONFIDENTIAL)
    if forecasting_view:
        _, version = _grant()

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital-plan", headers=_auth(version, "viewer")
    )

    assert response.status_code == 200, response.text
    body = response.json()
    if forecasting_view:
        assert body["projection_unavailable"] is None
        assert body["projection"] is not None
        assert [scenario["run_id"] for scenario in body["projection"]["scenarios"]] == [str(run.id)]
    else:
        assert body["projection"] is None
        assert body["projection_unavailable"]["error_code"] == "forecasting_view_required"
        assert "Forecasting · Aggregated · View" in body["projection_unavailable"]["reason"]


def test_queued_forecasting_requires_run_before_any_execution(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    period_id = _seed_book()
    version = 1
    for module in (ModuleScope.FX, ModuleScope.FTP):
        _, version = _grant(
            RoleBundle.ANALYST, module=module, sensitivity=SensitivityScope.CONFIDENTIAL
        )
    _, version = _grant(sensitivity=SensitivityScope.CONFIDENTIAL)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Unauthorized Forecasting execution reached a side effect")

    monkeypatch.setattr(pipeline, "derive_facts", forbidden)
    monkeypatch.setattr(data_activation, "run_official_modules", forbidden)
    monkeypatch.setattr(regulatory_forecasting, "_create_run_row", forbidden)
    monkeypatch.setattr(reverse_stress, "_hash", forbidden)
    with get_sessionmaker()() as session:
        ctx = TenantContext(
            organization_id=ORG_1, actor_user_id=USER_1, authorization_version=version
        )
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        assert scheduler._scheduled_official_actor(session, bank) is None
        with pytest.raises(HTTPException) as denied:
            regulatory_forecasting.create_forecast_run(
                session,
                ctx,
                bank.id,
                ForecastRunCreate(reporting_period_id=period_id, scenario_code="base"),
            )
        assert denied.value.status_code == 403
        with pytest.raises(HTTPException) as denied:
            reverse_stress.run_reverse_stress(
                session, ctx, bank.id, ReverseStressRunCreate(reporting_period_id=period_id)
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
