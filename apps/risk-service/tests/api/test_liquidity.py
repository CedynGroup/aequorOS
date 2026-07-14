from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date
from threading import Event
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, delete, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.domain.risk_constants import LIQUIDITY_RISK_TYPE
from app.models import (
    AuditEvent,
    CalculationForecastPeriod,
    CalculationRun,
    FinancialBalance,
    FinancialObligation,
    FinancialReportingPeriod,
    LiquidityAnalysisResult,
    RiskFinding,
    RiskFindingEvidence,
)
from app.services import calculations, liquidity
from app.services.liquidity import generate_findings
from tests.api.factories import CaseFactory
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2, headers
from tests.api.test_calculations import _financial_inputs, _ready_scenario


def test_liquidity_openapi_contracts(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    summary_path = "/api/v1/cases/{case_id}/liquidity/summary"
    review_path = "/api/v1/cases/{case_id}/liquidity/findings/{finding_id}/review"

    assert paths[summary_path]["get"]["operationId"] == "getLiquiditySummary"
    assert paths[review_path]["post"]["operationId"] == "reviewLiquidityFinding"
    assert (
        paths[summary_path]["get"]["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ]
        == "#/components/schemas/LiquiditySummaryRead"
    )
    review = paths[review_path]["post"]
    assert review["requestBody"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/LiquidityFindingReview"
    )
    parameters = {item["name"]: item for item in review["parameters"]}
    assert parameters["X-Org-Id"]["required"] is True
    assert parameters["X-User-Id"]["required"] is True
    components = schema["components"]["schemas"]
    for name in (
        "LiquiditySummaryRead",
        "LiquidityMetricRead",
        "LiquidityFindingRead",
        "LiquidityEvidenceRead",
        "LiquidityFindingReview",
    ):
        assert components[name]["additionalProperties"] is False
    metric = components["LiquidityMetricRead"]
    assert metric["properties"]["availability"]["enum"] == ["available", "unavailable"]
    assert {item.get("type") for item in metric["properties"]["value"]["anyOf"]} == {
        "string",
        "null",
    }
    assert "diagnostic" in metric["properties"]


def test_legacy_successful_run_returns_honest_unavailable_summary(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    run_response = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"], "forecast_periods": 2},
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    with get_sessionmaker()() as session:
        session.execute(
            delete(LiquidityAnalysisResult).where(LiquidityAnalysisResult.run_id == UUID(run["id"]))
        )
        session.commit()

    response = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary",
        headers=headers(),
        params={"run_id": run["id"]},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "case_id": str(case.id),
        "scenario_id": scenario["id"],
        "calculation_run_id": run["id"],
        "calculation_input_hash": run["input_hash"],
        "analysis_version": None,
        "status": "not_calculated",
        "currency": None,
        "as_of_date": run["as_of_date"],
        "metrics": [],
        "findings": [],
        "generated_at": None,
    }


def test_liquidity_summary_empty_success_evidence_and_review(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    empty = db_client.get(f"/api/v1/cases/{case.id}/liquidity/summary", headers=headers())
    assert empty.status_code == 200
    assert empty.json() == {
        "case_id": str(case.id),
        "scenario_id": None,
        "calculation_run_id": None,
        "calculation_input_hash": None,
        "analysis_version": None,
        "status": "not_calculated",
        "currency": None,
        "as_of_date": None,
        "metrics": [],
        "findings": [],
        "generated_at": None,
    }

    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    with get_sessionmaker()() as session:
        reporting_period = FinancialReportingPeriod(
            organization_id=ORG_1,
            case_id=case.id,
            dedupe_key="liquidity:evidence:reporting-period",
            period_type="year",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            label="2026",
        )
        session.add(reporting_period)
        session.flush()
        balances = list(
            session.scalars(select(FinancialBalance).where(FinancialBalance.case_id == case.id))
        )
        for balance in balances:
            balance.reporting_period_id = reporting_period.id
        reporting_period_id = reporting_period.id
        session.commit()
    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"], "forecast_periods": 2},
    ).json()
    response = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary",
        headers=headers(),
        params={"scenario_id": scenario["id"]},
    )
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["status"] == "ready"
    assert summary["calculation_run_id"] == run["id"]
    assert summary["calculation_input_hash"] == run["input_hash"]
    assert summary["analysis_version"] == "liquidity-v1.0.0"
    assert {metric["key"] for metric in summary["metrics"]} == {
        "minimum_cash_balance",
        "peak_liquidity_gap",
        "minimum_sources_coverage",
        "credit_reliance",
        "cash_runway_periods",
    }
    finding = summary["findings"][0]
    assert finding["status"] == "open"
    assert finding["rationale"]
    assert {evidence["source_type"] for evidence in finding["evidence"]} == {
        "forecast_output",
        "canonical_input",
        "scenario_assumption",
    }
    assert all(
        evidence["source_url"].startswith(f"/cases/{case.id}?") for evidence in finding["evidence"]
    )
    assert any(
        "tab=financial#financial-obligations-" in evidence["source_url"]
        for evidence in finding["evidence"]
    )
    reporting_period_evidence = next(
        evidence
        for evidence in finding["evidence"]
        if evidence["locator"].get("record_id") == str(reporting_period_id)
    )
    assert reporting_period_evidence["label"] == "Canonical record: 2026"
    assert reporting_period_evidence["source_url"] == (
        f"/cases/{case.id}?tab=financial#financial-reportingPeriods-{reporting_period_id}"
    )

    invalid = db_client.post(
        f"/api/v1/cases/{case.id}/liquidity/findings/{finding['id']}/review",
        headers=headers(),
        json={"action": "dismiss"},
    )
    assert invalid.status_code == 422
    reviewed = db_client.post(
        f"/api/v1/cases/{case.id}/liquidity/findings/{finding['id']}/review",
        headers=headers(),
        json={"action": "acknowledge", "reason": "Reviewed forecast evidence"},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "acknowledged"

    with get_sessionmaker()() as session:
        persisted_finding = session.get(RiskFinding, UUID(finding["id"]))
        assert persisted_finding is not None
        assert persisted_finding.risk_type == LIQUIDITY_RISK_TYPE
        assert session.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_id == UUID(finding["id"]),
                AuditEvent.event_type == "liquidity_finding.reviewed",
            )
        )


def test_credit_reliance_finding_evidence_covers_all_forecast_periods(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"], "forecast_periods": 3},
    ).json()

    with get_sessionmaker()() as session:
        persisted_run = session.get(CalculationRun, UUID(run["id"]))
        assert persisted_run is not None
        periods = list(
            session.scalars(
                select(CalculationForecastPeriod)
                .where(CalculationForecastPeriod.run_id == persisted_run.id)
                .order_by(CalculationForecastPeriod.period_number)
            )
        )
        for period in periods:
            period.credit_draw = period.projected_outflows + period.debt_repayment
        generate_findings(
            session,
            TenantContext(organization_id=ORG_1, actor_user_id=USER_1),
            persisted_run,
            periods,
        )
        session.flush()
        credit_finding = session.scalar(
            select(RiskFinding)
            .where(
                RiskFinding.case_id == case.id,
                RiskFinding.rule_id == "liquidity.credit_reliance",
                RiskFinding.details["liquidity"]["calculation_run_id"].as_string() == run["id"],
            )
            .order_by(RiskFinding.created_at.desc(), RiskFinding.id.desc())
        )
        assert credit_finding is not None
        period_evidence = list(
            session.scalars(
                select(RiskFindingEvidence).where(
                    RiskFindingEvidence.finding_id == credit_finding.id,
                    RiskFindingEvidence.locator["source_type"].as_string() == "forecast_output",
                )
            )
        )

    assert {item.locator["period_number"] for item in period_evidence} == {1, 2, 3}


def test_liquidity_summary_and_review_are_tenant_scoped(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    )
    own_summary = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary", headers=headers()
    ).json()
    finding_id = own_summary["findings"][0]["id"]
    other_headers = headers(org_id=ORG_2, user_id=USER_2)

    assert (
        db_client.get(
            f"/api/v1/cases/{case.id}/liquidity/summary", headers=other_headers
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/cases/{case.id}/liquidity/findings/{finding_id}/review",
            headers=other_headers,
            json={"action": "acknowledge"},
        ).status_code
        == 404
    )


def test_liquidity_review_rolls_back_when_audit_event_fails(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    )
    finding_id = UUID(
        db_client.get(f"/api/v1/cases/{case.id}/liquidity/summary", headers=headers()).json()[
            "findings"
        ][0]["id"]
    )

    def fail_audit_event(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit persistence failed")

    monkeypatch.setattr(liquidity, "record_event", fail_audit_event)
    response = db_client.post(
        f"/api/v1/cases/{case.id}/liquidity/findings/{finding_id}/review",
        headers=headers(),
        json={"action": "acknowledge", "reason": "Reviewed forecast evidence"},
    )

    assert response.status_code == 500
    with get_sessionmaker()() as session:
        persisted_finding = session.get(RiskFinding, finding_id)
        assert persisted_finding is not None
        assert persisted_finding.status == "open"
        event_types = set(
            session.scalars(select(AuditEvent.event_type).where(AuditEvent.entity_id == finding_id))
        )
    assert "finding.status_changed" not in event_types
    assert "liquidity_finding.reviewed" not in event_types


def test_liquidity_summary_reads_immutable_persisted_metrics(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()
    first = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary",
        headers=headers(),
        params={"run_id": run["id"]},
    ).json()
    diagnostic = (
        "Credit reliance is unavailable because projected outflows plus debt repayment "
        "must be positive; period 1 uses 0.0000. The ratio is undefined and was "
        "excluded from threshold classification."
    )
    with get_sessionmaker()() as session:
        analysis = session.scalar(
            select(LiquidityAnalysisResult).where(LiquidityAnalysisResult.run_id == UUID(run["id"]))
        )
        assert analysis is not None
        persisted = dict(analysis.result)
        persisted["metrics"] = [
            {
                **metric,
                **(
                    {
                        "value": None,
                        "availability": "unavailable",
                        "diagnostic": diagnostic,
                    }
                    if metric["key"] == "credit_reliance"
                    else {}
                ),
            }
            for metric in persisted["metrics"]
        ]
        analysis.result = persisted
        session.commit()

    def reject_recalculation(_periods: object) -> object:
        raise AssertionError("historical liquidity metrics were recalculated")

    monkeypatch.setattr(liquidity, "calculate_metrics", reject_recalculation)
    historical = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary",
        headers=headers(),
        params={"run_id": run["id"]},
    )

    assert historical.status_code == 200, historical.text
    assert historical.json()["analysis_version"] == "liquidity-v1.0.0"
    metrics = {metric["key"]: metric for metric in historical.json()["metrics"]}
    assert metrics["credit_reliance"]["value"] is None
    assert metrics["credit_reliance"]["availability"] == "unavailable"
    assert metrics["credit_reliance"]["diagnostic"] == diagnostic
    assert metrics["minimum_cash_balance"] == next(
        metric for metric in first["metrics"] if metric["key"] == "minimum_cash_balance"
    )


def test_zero_finding_run_persists_liquidity_analysis(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    with get_sessionmaker()() as session:
        session.execute(delete(FinancialObligation).where(FinancialObligation.case_id == case.id))
        session.commit()

    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()
    summary = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary",
        headers=headers(),
        params={"run_id": run["id"]},
    )

    assert summary.status_code == 200, summary.text
    assert summary.json()["findings"] == []
    assert summary.json()["metrics"]
    with get_sessionmaker()() as session:
        analysis = session.scalar(
            select(LiquidityAnalysisResult).where(LiquidityAnalysisResult.run_id == UUID(run["id"]))
        )
    assert analysis is not None
    assert analysis.analysis_version == "liquidity-v1.0.0"


def test_liquidity_review_rejects_findings_not_owned_by_workflow(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()
    other_case = CaseFactory(db_client).create()
    other_scenario = _ready_scenario(db_client, other_case.id)
    _financial_inputs(other_case.id)
    other_run = db_client.post(
        f"/api/v1/cases/{other_case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": other_scenario["id"]},
    ).json()

    with get_sessionmaker()() as session:
        fixtures = (
            ("manual", None, None, run["id"]),
            ("deterministic_rule", "low_cash_runway", "scoring-v1", run["id"]),
            (
                "deterministic_rule",
                "liquidity.negative_cash",
                "liquidity-v1.0.0",
                other_run["id"],
            ),
        )
        finding_ids = []
        for source, rule_id, rule_version, calculation_run_id in fixtures:
            finding = RiskFinding(
                organization_id=ORG_1,
                case_id=case.id,
                risk_type=LIQUIDITY_RISK_TYPE,
                title="Unrelated liquidity finding",
                summary="Not generated by the liquidity workflow.",
                rationale="Not generated by the liquidity workflow.",
                severity="high",
                status="open",
                source=source,
                rule_id=rule_id,
                rule_version=rule_version,
                details={"liquidity": {"calculation_run_id": calculation_run_id}},
            )
            session.add(finding)
            session.flush()
            finding_ids.append(finding.id)
        session.commit()

    for finding_id in finding_ids:
        response = db_client.post(
            f"/api/v1/cases/{case.id}/liquidity/findings/{finding_id}/review",
            headers=headers(),
            json={"action": "acknowledge", "reason": "Should not persist"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["message"] == "Liquidity finding not found."

    with get_sessionmaker()() as session:
        persisted = list(
            session.scalars(select(RiskFinding).where(RiskFinding.id.in_(finding_ids)))
        )
        event_types = set(
            session.scalars(
                select(AuditEvent.event_type).where(AuditEvent.entity_id.in_(finding_ids))
            )
        )
    assert len(persisted) == len(finding_ids)
    assert all(finding.status == "open" for finding in persisted)
    assert "finding.status_changed" not in event_types
    assert "liquidity_finding.reviewed" not in event_types


def test_liquidity_review_uses_finding_producing_rule_version(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    )
    finding = db_client.get(f"/api/v1/cases/{case.id}/liquidity/summary", headers=headers()).json()[
        "findings"
    ][0]

    monkeypatch.setattr(liquidity, "RULE_VERSION", "liquidity-v2.0.0")
    response = db_client.post(
        f"/api/v1/cases/{case.id}/liquidity/findings/{finding['id']}/review",
        headers=headers(),
        json={"action": "acknowledge", "reason": "Historical analysis reviewed"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"
    assert response.json()["rule_version"] == "liquidity-v1.0.0"


def test_liquidity_review_rejects_terminal_findings(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    older = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()
    db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs/{older['id']}/rerun",
        headers=headers(),
        json={},
    )
    historical = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary",
        headers=headers(),
        params={"run_id": older["id"]},
    ).json()
    finding_id = UUID(historical["findings"][0]["id"])

    response = db_client.post(
        f"/api/v1/cases/{case.id}/liquidity/findings/{finding_id}/review",
        headers=headers(),
        json={"action": "acknowledge", "reason": "Must remain historical"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "Liquidity finding is read-only."
    with get_sessionmaker()() as session:
        finding = session.get(RiskFinding, finding_id)
        assert finding is not None
        assert finding.status == "superseded"
        assert (
            session.scalar(
                select(AuditEvent).where(
                    AuditEvent.entity_id == finding_id,
                    AuditEvent.event_type == "liquidity_finding.reviewed",
                )
            )
            is None
        )


def test_liquidity_review_rejects_archived_scenario_findings(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    )
    finding_id = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary", headers=headers()
    ).json()["findings"][0]["id"]
    archived = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios/{scenario['id']}/archive",
        headers=headers(),
        json={"reason": "Retain historical liquidity analysis"},
    )
    assert archived.status_code == 200, archived.text

    response = db_client.post(
        f"/api/v1/cases/{case.id}/liquidity/findings/{finding_id}/review",
        headers=headers(),
        json={"action": "acknowledge", "reason": "Historical review"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "Archived scenario liquidity findings are read-only."
    )
    with get_sessionmaker()() as session:
        finding = session.get(RiskFinding, UUID(finding_id))
        assert finding is not None
        assert finding.status == "open"
        assert (
            session.scalar(
                select(AuditEvent).where(
                    AuditEvent.entity_id == UUID(finding_id),
                    AuditEvent.event_type == "liquidity_finding.reviewed",
                )
            )
            is None
        )


def test_liquidity_review_cannot_overwrite_concurrent_supersession(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_factory = get_sessionmaker()
    with session_factory() as dialect_session:
        if dialect_session.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL advisory locks are required for concurrency coverage.")

    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    )
    finding_id = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary", headers=headers()
    ).json()["findings"][0]["id"]
    context = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    review_waiting = Event()
    original_lock = liquidity.lock_finding_publication

    def observed_lock(
        db: Session,
        tenant_context: TenantContext,
        locked_case_id: UUID,
        scenario_id: UUID,
    ) -> None:
        review_waiting.set()
        original_lock(db, tenant_context, locked_case_id, scenario_id)

    monkeypatch.setattr(liquidity, "lock_finding_publication", observed_lock)

    def review() -> Response:
        return db_client.post(
            f"/api/v1/cases/{case.id}/liquidity/findings/{finding_id}/review",
            headers=headers(),
            json={"action": "acknowledge"},
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with (
            session_factory() as outer_session,
            liquidity.serialize_finding_publication(
                outer_session, context, case.id, UUID(scenario["id"])
            ) as publication_session,
        ):
            future = executor.submit(review)
            assert review_waiting.wait(timeout=5)
            finding = publication_session.get(RiskFinding, UUID(finding_id))
            assert finding is not None
            finding.status = "superseded"
            finding.disposition_reason = "Superseded by a newer liquidity forecast run."
            publication_session.commit()
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.2)
        response = future.result(timeout=5)

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "Liquidity finding is read-only."
    with session_factory() as verification_session:
        finding = verification_session.get(RiskFinding, UUID(finding_id))
        assert finding is not None
        assert finding.status == "superseded"
        assert (
            verification_session.scalar(
                select(AuditEvent).where(
                    AuditEvent.entity_id == UUID(finding_id),
                    AuditEvent.event_type == "liquidity_finding.reviewed",
                )
            )
            is None
        )


def test_generic_finding_update_rejects_liquidity_workflow_findings(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    older = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()
    older_finding_id = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary",
        headers=headers(),
        params={"run_id": older["id"]},
    ).json()["findings"][0]["id"]
    db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs/{older['id']}/rerun",
        headers=headers(),
        json={},
    )
    current_finding_id = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary",
        headers=headers(),
    ).json()["findings"][0]["id"]

    for finding_id in (older_finding_id, current_finding_id):
        response = db_client.patch(
            f"/api/v1/findings/{finding_id}",
            headers=headers(),
            json={"status": "acknowledged", "disposition_reason": "Generic review"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["message"] == (
            "Liquidity workflow findings are read-only in the generic findings endpoint."
        )

    with get_sessionmaker()() as session:
        persisted = list(
            session.scalars(
                select(RiskFinding).where(
                    RiskFinding.id.in_((UUID(older_finding_id), UUID(current_finding_id)))
                )
            )
        )
        generic_review_events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_id.in_((UUID(older_finding_id), UUID(current_finding_id))),
                    AuditEvent.event_type.in_(
                        ("finding.status_changed", "liquidity_finding.reviewed")
                    ),
                )
            )
        )
    assert {finding.status for finding in persisted} == {"open", "superseded"}
    assert generic_review_events == []


def test_liquidity_rerun_supersedes_prior_scenario_findings(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    first = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()
    first_summary = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary", headers=headers()
    ).json()
    second = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs/{first['id']}/rerun",
        headers=headers(),
        json={},
    ).json()
    second_summary = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary", headers=headers()
    ).json()

    assert second_summary["calculation_run_id"] == second["id"]
    assert {item["id"] for item in first_summary["findings"]}.isdisjoint(
        {item["id"] for item in second_summary["findings"]}
    )
    prior_ids = [UUID(item["id"]) for item in first_summary["findings"]]
    with get_sessionmaker()() as session:
        prior = list(session.scalars(select(RiskFinding).where(RiskFinding.id.in_(prior_ids))))
    assert prior and all(item.status == "superseded" for item in prior)


def test_stale_run_completion_does_not_replace_newer_findings(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    first = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()
    second = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs/{first['id']}/rerun",
        headers=headers(),
        json={},
    ).json()

    with get_sessionmaker()() as session:
        first_run = session.get(CalculationRun, UUID(first["id"]))
        assert first_run is not None
        periods = list(
            session.scalars(
                select(CalculationForecastPeriod).where(
                    CalculationForecastPeriod.run_id == first_run.id
                )
            )
        )
        generate_findings(
            session,
            TenantContext(organization_id=ORG_1, actor_user_id=USER_1),
            first_run,
            periods,
        )
        session.commit()

        current = list(
            session.scalars(
                select(RiskFinding).where(
                    RiskFinding.case_id == case.id,
                    RiskFinding.risk_type == LIQUIDITY_RISK_TYPE,
                    RiskFinding.status == "open",
                )
            )
        )
    assert current
    assert {item.details["liquidity"]["calculation_run_id"] for item in current} == {second["id"]}
    historical = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary?run_id={first['id']}",
        headers=headers(),
    ).json()
    assert historical["findings"]
    assert {item["status"] for item in historical["findings"]} == {"superseded"}


def test_stale_run_uses_latest_newer_run_as_superseder(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    oldest = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()
    middle = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs/{oldest['id']}/rerun",
        headers=headers(),
        json={},
    ).json()
    newest = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs/{middle['id']}/rerun",
        headers=headers(),
        json={},
    ).json()

    with get_sessionmaker()() as session:
        oldest_run = session.get(CalculationRun, UUID(oldest["id"]))
        assert oldest_run is not None
        periods = list(
            session.scalars(
                select(CalculationForecastPeriod).where(
                    CalculationForecastPeriod.run_id == oldest_run.id
                )
            )
        )
        generate_findings(
            session,
            TenantContext(organization_id=ORG_1, actor_user_id=USER_1),
            oldest_run,
            periods,
        )
        session.commit()
        completed_events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_id == oldest_run.id,
                    AuditEvent.event_type == "liquidity_analysis.completed",
                )
            )
        )

    assert any(
        event.details.get("superseded_by_calculation_run_id") == newest["id"]
        for event in completed_events
    )


def test_liquidity_summary_ranks_findings_by_severity(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()

    with get_sessionmaker()() as session:
        for severity in ("medium", "critical"):
            session.add(
                RiskFinding(
                    organization_id=ORG_1,
                    case_id=case.id,
                    risk_type=LIQUIDITY_RISK_TYPE,
                    title=f"{severity} finding",
                    summary="Severity ordering regression fixture.",
                    rationale="Severity ordering regression fixture.",
                    severity=severity,
                    status="open",
                    source="deterministic_rule",
                    rule_id=f"liquidity.test.{severity}",
                    rule_version="liquidity-v1.0.0",
                    details={
                        "liquidity": {
                            "workflow_id": "liquidity_analysis",
                            "calculation_run_id": run["id"],
                            "scenario_id": scenario["id"],
                            "input_hash": run["input_hash"],
                            "metrics": [],
                        }
                    },
                )
            )
        session.commit()

    findings = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary", headers=headers()
    ).json()["findings"]
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    assert [rank[item["severity"]] for item in findings] == sorted(
        rank[item["severity"]] for item in findings
    )


def test_liquidity_summary_loads_only_findings_for_selected_run(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    first = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()
    second = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs/{first['id']}/rerun",
        headers=headers(),
        json={},
    ).json()
    loaded_run_ids: list[str | None] = []

    def observe_finding_load(finding: RiskFinding, _context: object) -> None:
        loaded_run_ids.append(finding.details.get("liquidity", {}).get("calculation_run_id"))

    event.listen(RiskFinding, "load", observe_finding_load)
    try:
        response = db_client.get(
            f"/api/v1/cases/{case.id}/liquidity/summary",
            headers=headers(),
            params={"run_id": first["id"]},
        )
    finally:
        event.remove(RiskFinding, "load", observe_finding_load)

    assert response.status_code == 200, response.text
    assert loaded_run_ids
    assert set(loaded_run_ids) == {first["id"]}
    assert second["id"] not in loaded_run_ids


def test_concurrent_publication_keeps_only_newest_run_findings(
    db_client: TestClient,
) -> None:
    sessionmaker = get_sessionmaker()
    with sessionmaker() as dialect_session:
        if dialect_session.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL advisory locks are required for concurrency coverage.")

    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    older = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()
    newer = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs/{older['id']}/rerun",
        headers=headers(),
        json={},
    ).json()

    with sessionmaker() as setup_session:
        runs = list(
            setup_session.scalars(
                select(CalculationRun).where(
                    CalculationRun.id.in_((UUID(older["id"]), UUID(newer["id"])))
                )
            )
        )
        for run in runs:
            run.status = "running"
        findings = list(
            setup_session.scalars(
                select(RiskFinding).where(
                    RiskFinding.case_id == case.id,
                    RiskFinding.risk_type == LIQUIDITY_RISK_TYPE,
                )
            )
        )
        finding_ids = [finding.id for finding in findings]
        setup_session.execute(
            delete(RiskFindingEvidence).where(RiskFindingEvidence.finding_id.in_(finding_ids))
        )
        for finding in findings:
            setup_session.delete(finding)
        setup_session.commit()

    context = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)

    def publish_older() -> None:
        with (
            sessionmaker() as older_session,
            liquidity.serialize_finding_publication(
                older_session, context, case.id, UUID(scenario["id"])
            ) as publication_session,
        ):
            calculations._begin_repeatable_read(publication_session)
            older_run = publication_session.get(CalculationRun, UUID(older["id"]))
            assert older_run is not None
            older_periods = list(
                publication_session.scalars(
                    select(CalculationForecastPeriod).where(
                        CalculationForecastPeriod.run_id == older_run.id
                    )
                )
            )
            generate_findings(
                publication_session,
                context,
                older_run,
                older_periods,
                publication_locked=True,
            )
            older_run.status = "succeeded"
            publication_session.commit()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with (
            sessionmaker() as newer_session,
            liquidity.serialize_finding_publication(
                newer_session, context, case.id, UUID(scenario["id"])
            ) as publication_session,
        ):
            calculations._begin_repeatable_read(publication_session)
            newer_run = publication_session.get(CalculationRun, UUID(newer["id"]))
            assert newer_run is not None
            newer_periods = list(
                publication_session.scalars(
                    select(CalculationForecastPeriod).where(
                        CalculationForecastPeriod.run_id == newer_run.id
                    )
                )
            )
            generate_findings(
                publication_session,
                context,
                newer_run,
                newer_periods,
                publication_locked=True,
            )
            newer_run.status = "succeeded"
            publication_session.flush()
            future = executor.submit(publish_older)
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.2)
            publication_session.commit()
        future.result(timeout=5)

    with sessionmaker() as verification_session:
        current = list(
            verification_session.scalars(
                select(RiskFinding).where(
                    RiskFinding.case_id == case.id,
                    RiskFinding.risk_type == LIQUIDITY_RISK_TYPE,
                    RiskFinding.status == "open",
                )
            )
        )
    assert current
    assert {item.details["liquidity"]["calculation_run_id"] for item in current} == {newer["id"]}
    historical = db_client.get(
        f"/api/v1/cases/{case.id}/liquidity/summary?run_id={older['id']}",
        headers=headers(),
    ).json()
    assert historical["findings"]
    assert {item["status"] for item in historical["findings"]} == {"superseded"}


def test_publication_lock_and_transaction_share_a_constrained_pool_connection(
    db_client: TestClient,
) -> None:
    configured_sessionmaker = get_sessionmaker()
    configured_engine = configured_sessionmaker.kw["bind"]
    if configured_engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL advisory locks are required for pool coverage.")

    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()
    constrained_engine = create_engine(
        configured_engine.url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.25,
    )
    constrained_sessionmaker = sessionmaker(
        bind=constrained_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    context = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    try:
        with (
            constrained_sessionmaker() as outer_session,
            liquidity.serialize_finding_publication(
                outer_session,
                context,
                case.id,
                UUID(scenario["id"]),
            ) as publication_session,
        ):
            calculations._begin_repeatable_read(publication_session)
            persisted = publication_session.get(CalculationRun, UUID(run["id"]))
            assert persisted is not None
            assert persisted.organization_id == ORG_1
    finally:
        constrained_engine.dispose()
