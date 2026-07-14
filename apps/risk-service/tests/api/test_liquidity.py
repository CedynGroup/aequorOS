from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.models import AuditEvent, CalculationForecastPeriod, CalculationRun, RiskFinding
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
                    RiskFinding.risk_type == "liquidity",
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
                    risk_type="liquidity",
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
