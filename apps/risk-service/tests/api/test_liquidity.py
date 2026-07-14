from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.domain.risk_constants import LIQUIDITY_RISK_TYPE
from app.models import (
    AuditEvent,
    CalculationForecastPeriod,
    CalculationRun,
    FinancialBalance,
    FinancialReportingPeriod,
    RiskFinding,
)
from app.services import liquidity
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


def test_liquidity_summary_empty_success_evidence_and_review(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    empty = db_client.get(f"/api/v1/cases/{case.id}/liquidity/summary", headers=headers())
    assert empty.status_code == 200
    assert empty.json() == {
        "case_id": str(case.id),
        "scenario_id": None,
        "calculation_run_id": None,
        "calculation_input_hash": None,
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
        for finding in findings:
            finding.status = "superseded"
        setup_session.commit()

    with sessionmaker() as newer_session:
        newer_run = newer_session.get(CalculationRun, UUID(newer["id"]))
        assert newer_run is not None
        newer_periods = list(
            newer_session.scalars(
                select(CalculationForecastPeriod).where(
                    CalculationForecastPeriod.run_id == newer_run.id
                )
            )
        )
        generate_findings(
            newer_session,
            TenantContext(organization_id=ORG_1, actor_user_id=USER_1),
            newer_run,
            newer_periods,
        )
        newer_run.status = "succeeded"
        newer_session.flush()

        def publish_older() -> None:
            with sessionmaker() as older_session:
                older_run = older_session.get(CalculationRun, UUID(older["id"]))
                assert older_run is not None
                older_periods = list(
                    older_session.scalars(
                        select(CalculationForecastPeriod).where(
                            CalculationForecastPeriod.run_id == older_run.id
                        )
                    )
                )
                generate_findings(
                    older_session,
                    TenantContext(organization_id=ORG_1, actor_user_id=USER_1),
                    older_run,
                    older_periods,
                )
                older_run.status = "succeeded"
                older_session.commit()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(publish_older)
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.2)
            newer_session.commit()
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
