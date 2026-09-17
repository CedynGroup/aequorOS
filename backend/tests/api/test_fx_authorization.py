"""Scoped-binding enforcement for FX dashboards and calculation runs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
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
    ResourceLocator,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.core.observability import Condition
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import (
    AuthorizationBinding,
    Bank,
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
from app.schemas.data_activation import DataActivationCreate
from app.schemas.regulatory_fx import FxScenarioBatchCreate
from app.schemas.regulatory_liquidity import RegulatoryRunBatchRead
from app.schemas.scenario_workbench import ScenarioResultRead
from app.services import (
    analysis_workbench,
    authorization,
    data_activation,
    enterprise_stress,
    pipeline,
    regulatory_fx,
    scheduler,
)
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

SIBLING_BANK_ID = "BK-FX000002"
BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
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
                    name="FX authorization bank",
                    short_name="FX auth",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                )
            )
        session.commit()
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
                name="FX sibling bank",
                short_name="FX sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()


def _grant(  # noqa: PLR0913 - each binding dimension is an enforcement input
    *,
    role_bundle: RoleBundle = RoleBundle.VIEWER,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
    institution_id: str | None = SAMPLE_BANK_ID,
    module_scope: ModuleScope = ModuleScope.FX,
    sensitivity_scope: SensitivityScope = SensitivityScope.AGGREGATED,
    status: BindingStatus = BindingStatus.ACTIVE,
    expired: bool = False,
) -> tuple[UUID, int]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        user = session.get(User, USER_1)
        assert user is not None
        binding = authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=role_bundle,
            scope=authorization.BindingScope(
                institution_scope,
                institution_id,
                module_scope,
                sensitivity_scope,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise FX scoped-binding enforcement.",
        )
        if status is not BindingStatus.ACTIVE or expired:
            binding.status = status.value
            if status is BindingStatus.REVOKED:
                binding.revoked_at = utc_now()
                binding.revoked_by_type = GrantorType.SYSTEM.value
                binding.revoked_by_id = "test-suite"
                binding.revoked_reason = "Exercise revoked binding denial."
            if expired:
                binding.valid_from = utc_now() - timedelta(days=2)
                binding.valid_until = utc_now() - timedelta(days=1)
            session.commit()
        session.refresh(user)
        return binding.id, user.authorization_version
    finally:
        session.close()


def _capture_binding_records() -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda message: records.append(dict(message.record)), level="DEBUG")
    return records, sink_id


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


def _add_regulatory_run(period_id: UUID, module: str, scenario_code: str) -> UUID:
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
            engine_version=f"test-{module}-v1",
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


def _binding_extras(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record["extra"]
        for record in records
        if record["extra"].get("condition") == Condition.AUTHORIZATION_BINDING_DECISION.value
    ]


def _dashboard_probe(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    version: int,
) -> tuple[Any, list[str]]:
    calls: list[str] = []

    def stop_after_authorization(
        _db: object,
        _ctx: object,
        bank_id: str,
        _reporting_period_id: object,
    ) -> None:
        calls.append(bank_id)
        raise HTTPException(status_code=409, detail="authorized FX probe")

    monkeypatch.setattr(regulatory_fx, "get_fx_dashboard", stop_after_authorization)
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/dashboard",
        headers=headers(authorization_version=version),
    )
    return response, calls


@pytest.mark.parametrize(
    ("institution_scope", "institution_id"),
    [
        (InstitutionScope.INSTITUTION, SAMPLE_BANK_ID),
        (InstitutionScope.ORGANIZATION, None),
    ],
)
def test_exact_or_explicit_organization_binding_allows_aggregated_fx_view(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    institution_scope: InstitutionScope,
    institution_id: str | None,
) -> None:
    binding_id, version = _grant(
        institution_scope=institution_scope,
        institution_id=institution_id,
    )
    records, sink_id = _capture_binding_records()
    try:
        response, calls = _dashboard_probe(db_client, monkeypatch, version)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 409
    assert calls == [SAMPLE_BANK_ID]
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["allowed"] is True
    assert decisions[0]["permission"] == "view"
    assert decisions[0]["module"] == "fx"
    assert decisions[0]["sensitivity"] == "aggregated"
    assert decisions[0]["matching_binding_ids"] == str(binding_id)


def test_no_binding_denies_fx_without_legacy_role_fallback(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        regulatory_fx,
    scheduler,
        "get_fx_dashboard",
        lambda *_args, **_kwargs: calls.append("called"),
    )

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/dashboard",
        headers=headers(roles=("admin",)),
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == ("FX access requires an active scoped binding.")
    assert calls == []


@pytest.mark.parametrize(
    ("grant_changes", "trace_reason"),
    [
        ({"institution_id": SIBLING_BANK_ID}, "institution_mismatch"),
        ({"module_scope": ModuleScope.CAPITAL}, "module_mismatch"),
        (
            {"sensitivity_scope": SensitivityScope.CONFIDENTIAL},
            "sensitivity_mismatch",
        ),
        (
            {"role_bundle": RoleBundle.ACCOUNT_ADMIN},
            "permission_not_in_bundle",
        ),
    ],
)
def test_partial_fx_binding_denies(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    grant_changes: dict[str, object],
    trace_reason: str,
) -> None:
    if grant_changes.get("institution_id") == SIBLING_BANK_ID:
        _add_sibling_bank()
    _, version = _grant(**grant_changes)  # type: ignore[arg-type]
    records, sink_id = _capture_binding_records()
    try:
        response, calls = _dashboard_probe(db_client, monkeypatch, version)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    assert calls == []
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["binding_trace"].endswith(f":{trace_reason}")


def test_partial_bindings_never_compose_into_fx_authority(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _grant(
        module_scope=ModuleScope.CAPITAL,
        sensitivity_scope=SensitivityScope.AGGREGATED,
    )
    _, version = _grant(sensitivity_scope=SensitivityScope.CONFIDENTIAL)

    response, calls = _dashboard_probe(db_client, monkeypatch, version)

    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    ("status_value", "expired"),
    [
        (BindingStatus.SUSPENDED, False),
        (BindingStatus.REVOKED, False),
        (BindingStatus.ACTIVE, True),
    ],
)
def test_inactive_fx_binding_denies(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    status_value: BindingStatus,
    expired: bool,
) -> None:
    _, version = _grant(status=status_value, expired=expired)

    response, calls = _dashboard_probe(db_client, monkeypatch, version)

    assert response.status_code == 403
    assert calls == []


def test_stale_authorization_version_denies_before_fx_evaluation(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _grant()

    response, calls = _dashboard_probe(db_client, monkeypatch, 1)

    assert response.status_code == 401
    assert calls == []


def test_cross_tenant_fx_probe_stays_hidden(db_client: TestClient) -> None:
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/dashboard",
        headers=headers(ORG_2),
    )

    assert response.status_code == 404


def test_fx_evaluator_failure_denies_closed(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, version = _grant()

    def fail_evaluation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("evaluator unavailable")

    monkeypatch.setattr(authorization, "evaluate_permission", fail_evaluation)
    records, sink_id = _capture_binding_records()
    try:
        response, calls = _dashboard_probe(db_client, monkeypatch, version)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    assert calls == []
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["reason"] == "binding_evaluation_failed"
    assert decisions[0]["severity"] == "error"


def test_fx_run_requires_analyst_confidential_binding_before_execution(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    period_id = uuid4()
    calls: list[str] = []

    def run_probe(
        _db: object,
        _ctx: object,
        bank_id: str,
        payload: FxScenarioBatchCreate,
    ) -> RegulatoryRunBatchRead:
        calls.append(bank_id)
        return RegulatoryRunBatchRead(
            bank_id=bank_id,
            reporting_period_id=payload.reporting_period_id,
            runs=[],
        )

    monkeypatch.setattr(regulatory_fx, "run_all_fx_scenarios", run_probe)
    _, viewer_version = _grant(sensitivity_scope=SensitivityScope.CONFIDENTIAL)
    denied = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/run-all-scenarios",
        headers=headers(authorization_version=viewer_version),
        json={"reporting_period_id": str(period_id)},
    )
    assert denied.status_code == 403
    assert calls == []

    _, analyst_version = _grant(
        role_bundle=RoleBundle.ANALYST,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    allowed = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/run-all-scenarios",
        headers=headers(authorization_version=analyst_version),
        json={"reporting_period_id": str(period_id)},
    )
    assert allowed.status_code == 201, allowed.text
    assert calls == [SAMPLE_BANK_ID]


def test_fx_v1_authority_is_institution_and_module_scoped_only(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One FX binding covers the institution; v1 has no desk or currency dimension."""

    assert "desk_id" not in ResourceLocator.__dataclass_fields__
    assert "currency" not in ResourceLocator.__dataclass_fields__
    _, version = _grant()
    response, calls = _dashboard_probe(db_client, monkeypatch, version)

    assert response.status_code == 409
    assert calls == [SAMPLE_BANK_ID]


def test_fx_scenario_catalogue_and_analysis_list_require_exact_view_sensitivity(
    db_client: TestClient,
) -> None:
    _seed_book()
    catalogue = f"{BASE}/scenario-workbench/fx/scenarios"
    analyses = f"{BASE}/scenario-workbench/fx/analyses"

    assert db_client.get(catalogue, headers=headers(roles=("admin",))).status_code == 403
    assert db_client.get(analyses, headers=headers(roles=("admin",))).status_code == 403

    _, aggregated_version = _grant(sensitivity_scope=SensitivityScope.AGGREGATED)
    aggregated_headers = headers(
        roles=("admin",),
        authorization_version=aggregated_version,
    )
    allowed_list = db_client.get(analyses, headers=aggregated_headers)
    denied_catalogue = db_client.get(catalogue, headers=aggregated_headers)

    assert allowed_list.status_code == 200, allowed_list.text
    assert denied_catalogue.status_code == 403

    _, confidential_version = _grant(sensitivity_scope=SensitivityScope.CONFIDENTIAL)
    allowed_catalogue = db_client.get(
        catalogue,
        headers=headers(
            roles=("viewer",),
            authorization_version=confidential_version,
        ),
    )
    assert allowed_catalogue.status_code == 200, allowed_catalogue.text


def test_fx_scenario_mutations_use_binding_permissions_and_deny_before_writes(
    db_client: TestClient,
) -> None:
    _seed_book()
    url = f"{BASE}/scenario-workbench/fx/scenarios"
    payload = {
        "code": "fx_authorization_case",
        "name": "FX authorization case",
        "shocks": {"ghs_usd_shock_pct": "12.5"},
    }
    with get_sessionmaker()() as session:
        before = (
            session.scalar(
                select(func.count())
                .select_from(StressScenario)
                .where(StressScenario.module == "fx")
            )
            or 0
        )

    _, viewer_version = _grant(sensitivity_scope=SensitivityScope.CONFIDENTIAL)
    denied = db_client.post(
        url,
        headers=headers(
            roles=("admin",),
            authorization_version=viewer_version,
        ),
        json=payload,
    )
    assert denied.status_code == 403
    with get_sessionmaker()() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(StressScenario)
                .where(StressScenario.module == "fx")
            )
            or 0
        ) == before

    _, analyst_version = _grant(
        role_bundle=RoleBundle.ANALYST,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    allowed = db_client.post(
        url,
        headers=headers(
            roles=("viewer",),
            authorization_version=analyst_version,
        ),
        json=payload,
    )
    assert allowed.status_code == 201, allowed.text
    scenario_id = allowed.json()["id"]

    edited = db_client.patch(
        f"{url}/{scenario_id}",
        headers=headers(
            roles=("viewer",),
            authorization_version=analyst_version,
        ),
        json={"name": "Edited FX authorization case"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Edited FX authorization case"


def test_fx_compute_and_save_require_analyst_before_engine_or_persistence(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    period_id = _seed_book()
    compute_calls: list[str] = []

    def compute_probe(*_args: object, **_kwargs: object) -> ScenarioResultRead:
        compute_calls.append("computed")
        return ScenarioResultRead(
            kind="system",
            code="baseline",
            label="Baseline",
            shocks_applied={},
            status="succeeded",
        )

    monkeypatch.setattr(analysis_workbench, "_compute_one", compute_probe)
    analysis_url = f"{BASE}/scenario-workbench/fx/analysis"
    save_url = f"{BASE}/scenario-workbench/fx/analyses"
    run_payload = {
        "reporting_period_id": str(period_id),
        "scenarios": [{"kind": "system", "code": "baseline"}],
    }
    save_payload = {**run_payload, "name": "FX authorization snapshot"}
    with get_sessionmaker()() as session:
        before = (
            session.scalar(
                select(func.count())
                .select_from(SavedScenarioAnalysis)
                .where(SavedScenarioAnalysis.module == "fx")
            )
            or 0
        )

    _, viewer_version = _grant(sensitivity_scope=SensitivityScope.CONFIDENTIAL)
    viewer_headers = headers(
        roles=("admin",),
        authorization_version=viewer_version,
    )
    denied_run = db_client.post(analysis_url, headers=viewer_headers, json=run_payload)
    denied_save = db_client.post(save_url, headers=viewer_headers, json=save_payload)

    assert denied_run.status_code == 403
    assert denied_save.status_code == 403
    assert compute_calls == []
    with get_sessionmaker()() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(SavedScenarioAnalysis)
                .where(SavedScenarioAnalysis.module == "fx")
            )
            or 0
        ) == before

    _, analyst_version = _grant(
        role_bundle=RoleBundle.ANALYST,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    analyst_headers = headers(
        roles=("viewer",),
        authorization_version=analyst_version,
    )
    allowed_run = db_client.post(analysis_url, headers=analyst_headers, json=run_payload)
    allowed_save = db_client.post(save_url, headers=analyst_headers, json=save_payload)

    assert allowed_run.status_code == 200, allowed_run.text
    assert allowed_save.status_code == 201, allowed_save.text
    assert compute_calls == ["computed", "computed"]


def test_fx_saved_analysis_detail_is_hidden_without_confidential_view(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        analysis = SavedScenarioAnalysis(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            module="fx",
            reporting_period_id=period_id,
            name="Protected FX analysis",
            engine_version="test-fx-v1",
            scenarios=[],
            results=[],
            created_by=USER_1,
        )
        session.add(analysis)
        session.commit()
        analysis_id = analysis.id
    finally:
        session.close()

    _, aggregated_version = _grant(sensitivity_scope=SensitivityScope.AGGREGATED)
    denied = db_client.get(
        f"{BASE}/scenario-workbench/fx/analyses/{analysis_id}",
        headers=headers(
            roles=("admin",),
            authorization_version=aggregated_version,
        ),
    )

    assert denied.status_code == 404
    assert denied.json()["error"]["message"] == "Analysis not found."


def test_fx_regulatory_registry_filters_before_count_and_hides_details(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    fx_run_ids = [
        _add_regulatory_run(period_id, "fx", scenario) for scenario in ("baseline", "combined")
    ]
    _add_regulatory_run(period_id, "capital", "baseline")
    _, capital_version = _grant(
        module_scope=ModuleScope.CAPITAL,
        sensitivity_scope=SensitivityScope.AGGREGATED,
    )

    filtered = db_client.get(
        f"{BASE}/regulatory-runs",
        headers=headers(
            roles=("admin",),
            authorization_version=capital_version,
        ),
        params={"limit": 1, "offset": 0},
    )
    hidden_detail = db_client.get(
        f"{BASE}/regulatory-runs/{fx_run_ids[0]}",
        headers=headers(
            roles=("admin",),
            authorization_version=capital_version,
        ),
    )

    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 1
    assert [run["module"] for run in filtered.json()["runs"]] == ["capital"]
    assert hidden_detail.status_code == 404

    _, fx_aggregated_version = _grant(sensitivity_scope=SensitivityScope.AGGREGATED)
    visible_fx = db_client.get(
        f"{BASE}/regulatory-runs",
        headers=headers(
            roles=("viewer",),
            authorization_version=fx_aggregated_version,
        ),
        params={"module": "fx", "limit": 1, "offset": 0},
    )
    still_hidden_detail = db_client.get(
        f"{BASE}/regulatory-runs/{fx_run_ids[0]}",
        headers=headers(
            roles=("viewer",),
            authorization_version=fx_aggregated_version,
        ),
    )

    assert visible_fx.status_code == 200, visible_fx.text
    assert visible_fx.json()["total"] == 2
    assert len(visible_fx.json()["runs"]) == 1
    assert visible_fx.json()["runs"][0]["module"] == "fx"
    assert still_hidden_detail.status_code == 404

    _, fx_confidential_version = _grant(
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    visible_detail = db_client.get(
        f"{BASE}/regulatory-runs/{fx_run_ids[0]}",
        headers=headers(
            roles=("viewer",),
            authorization_version=fx_confidential_version,
        ),
    )
    assert visible_detail.status_code == 200, visible_detail.text


def test_fx_shared_feeds_filter_rows_and_counts(db_client: TestClient) -> None:
    now = utc_now()
    with get_sessionmaker()() as session:
        for module in ("capital", "fx"):
            session.add(
                LiveMetric(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    module=module,
                    metrics={"nop_pct_tier1": "12"},
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
                    metrics={"nop_pct_tier1": "12", "car_pct": "15"},
                    status="red",
                    computed_at=now,
                )
            )
        session.commit()

    _, version = _grant(module_scope=ModuleScope.CAPITAL)
    denied_headers = headers(authorization_version=version)
    summary = db_client.get(f"{BASE}/live-summary", headers=denied_headers)
    assert summary.status_code == 200, summary.text
    assert [row["module"] for row in summary.json()["modules"]] == ["capital"]
    alerts = db_client.get(f"{BASE}/alerts", headers=denied_headers)
    assert alerts.status_code == 200, alerts.text
    assert alerts.json()["total"] == 1
    assert alerts.json()["by_module"] == {"capital": 1}
    snapshots = db_client.get(
        f"{BASE}/live-snapshots",
        params={"module": "fx"},
        headers=denied_headers,
    )
    assert snapshots.status_code == 403
    window_params = {"start_date": str(now.date()), "end_date": str(now.date())}
    window = db_client.get(
        f"{BASE}/analytics/window",
        params=window_params,
        headers=denied_headers,
    )
    assert window.status_code == 200, window.text
    assert all(row["module"] != "fx" for row in window.json()["daily"])

    _, version = _grant()
    allowed_headers = headers(authorization_version=version)
    summary = db_client.get(f"{BASE}/live-summary", headers=allowed_headers)
    assert {row["module"] for row in summary.json()["modules"]} == {"capital", "fx"}
    alerts = db_client.get(f"{BASE}/alerts", headers=allowed_headers)
    assert alerts.json()["total"] == 2
    assert alerts.json()["by_module"] == {"capital": 1, "fx": 1}
    snapshots = db_client.get(
        f"{BASE}/live-snapshots",
        params={"module": "fx"},
        headers=allowed_headers,
    )
    assert snapshots.status_code == 200, snapshots.text
    assert len(snapshots.json()["snapshots"]) == 1
    window = db_client.get(
        f"{BASE}/analytics/window",
        params=window_params,
        headers=allowed_headers,
    )
    assert any(row["module"] == "fx" for row in window.json()["daily"])


def test_fx_batch_and_activation_deny_before_work(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, version = _grant(
        role_bundle=RoleBundle.ANALYST,
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    ctx = TenantContext(
        organization_id=ORG_1,
        actor_user_id=USER_1,
        authorization_version=version,
    )
    calls = []

    def unexpected_work(*args: object, **kwargs: object) -> None:
        calls.append("work")
        raise AssertionError("Unauthorized calculation reached work")

    monkeypatch.setattr(data_activation, "derive_facts", unexpected_work)
    monkeypatch.setattr(regulatory_fx, "_get_period_or_404", unexpected_work)
    with get_sessionmaker()() as session:
        with pytest.raises(HTTPException) as batch_denial:
            regulatory_fx.run_all_fx_scenarios(
                session,
                ctx,
                SAMPLE_BANK_ID,
                FxScenarioBatchCreate(reporting_period_id=uuid4()),
            )
        assert batch_denial.value.status_code == 403
        with pytest.raises(HTTPException) as activation_denial:
            data_activation.activate_bank_data(
                session,
                ctx,
                SAMPLE_BANK_ID,
                DataActivationCreate(
                    as_of_date=utc_now().date(),
                    reason="Verify FX preflight",
                    run_calculations=True,
                ),
            )
        assert activation_denial.value.status_code == 403
    assert calls == []


@pytest.mark.parametrize("include_fx", [True, False])
def test_enterprise_fx_permission_precedes_input_reads(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    include_fx: bool,
) -> None:
    calls: list[str] = []

    def input_probe(*_args: object, **_kwargs: object) -> None:
        calls.append("period")
        raise HTTPException(status_code=409, detail="Authorized input probe")

    monkeypatch.setattr(enterprise_stress, "_get_period_or_404", input_probe)
    _, version = _grant(
        role_bundle=RoleBundle.ANALYST,
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    payload = {
        "scenario_id": str(uuid4()),
        "reporting_period_id": str(uuid4()),
        "include_fx": include_fx,
        "reason": "Verify enterprise FX boundary",
    }
    url = f"{BASE}/enterprise-stress/runs"
    with get_sessionmaker()() as session:
        before = session.scalar(select(func.count()).select_from(RegulatoryRun))
    response = db_client.post(
        url,
        headers=headers(roles=("analyst",), authorization_version=version),
        json=payload,
    )
    assert response.status_code == (403 if include_fx else 409), response.text
    assert calls == ([] if include_fx else ["period"])
    with get_sessionmaker()() as session:
        assert session.scalar(select(func.count()).select_from(RegulatoryRun)) == before

    if include_fx:
        _, version = _grant(
            role_bundle=RoleBundle.ANALYST,
            sensitivity_scope=SensitivityScope.CONFIDENTIAL,
        )
        response = db_client.post(
            url,
            headers=headers(roles=("analyst",), authorization_version=version),
            json=payload,
        )
        assert response.status_code == 409, response.text
        assert calls == ["period"]


@pytest.mark.parametrize("scheduled", [False, True])
def test_queued_official_run_mints_authorized_fx_results(
    db_client: TestClient, scheduled: bool
) -> None:
    period_id = _seed_book()
    _grant(
        role_bundle=RoleBundle.ANALYST,
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    _, version = _grant(
        role_bundle=RoleBundle.ANALYST,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    with get_sessionmaker()() as session:
        period = session.get(BankReportingPeriod, period_id)
        assert period is not None
        as_of = period.period_end.isoformat()
        if scheduled:
            scheduler._enqueue_due_official_runs(session, ORG_1, get_settings(), utc_now())
            session.commit()
        else:
            response = db_client.post(
                f"{BASE}/official-runs",
                headers=headers(authorization_version=version),
                json={"as_of_date": as_of, "reason": "Authorized queued FX filing"},
            )
            assert response.status_code == 202, response.text
        job = session.scalar(
            select(Job).where(Job.job_type == "official_run", Job.bank_id == SAMPLE_BANK_ID)
        )
        assert job is not None
        pipeline.run_official(session, job)
        runs = list(
            session.scalars(
                select(RegulatoryRun).where(
                    RegulatoryRun.bank_id == SAMPLE_BANK_ID,
                    RegulatoryRun.module == "fx",
                )
            )
        )
        assert {run.scenario_code for run in runs} == set(regulatory_fx.FX_RUN_SCENARIO_CODES)
        assert all(run.status == "succeeded" for run in runs)


@pytest.mark.parametrize("fx_scope", ["absent", "aggregated", "sibling"])
def test_official_enqueue_requires_exact_confidential_fx_run(
    db_client: TestClient, fx_scope: str
) -> None:
    _, version = _grant(
        role_bundle=RoleBundle.ANALYST,
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    if fx_scope == "aggregated":
        _, version = _grant(role_bundle=RoleBundle.ANALYST)
    elif fx_scope == "sibling":
        _add_sibling_bank()
        _, version = _grant(
            role_bundle=RoleBundle.ANALYST,
            institution_id=SIBLING_BANK_ID,
            sensitivity_scope=SensitivityScope.CONFIDENTIAL,
        )
    response = db_client.post(
        f"{BASE}/official-runs",
        headers=headers(authorization_version=version),
        json={"as_of_date": "2026-08-31", "reason": "Denied queued FX filing"},
    )
    assert response.status_code == 403, response.text
    with get_sessionmaker()() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.scalar(select(func.count()).select_from(RegulatoryRun)) == 0
