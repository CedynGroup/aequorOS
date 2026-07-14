from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import (
    AuditEvent,
    CalculationForecastPeriod,
    CapitalIndicator,
    CapitalProjection,
    FinancialBalance,
    FinancialCashFlow,
    FinancialObligation,
    RiskFinding,
    RiskFindingEvidence,
)
from tests.api.factories import CaseFactory
from tests.api.helpers import ORG_1, ORG_2, headers


def _ready_scenarios(client: TestClient, case_id: UUID) -> list[dict]:
    workspace = client.post(
        f"/api/v1/cases/{case_id}/scenarios/initialize",
        headers=headers(),
        json={"reason": "Prepare capital scenarios"},
    ).json()
    for scenario in workspace["scenarios"]:
        for assumption in scenario["assumptions"]:
            response = client.post(
                f"/api/v1/cases/{case_id}/scenarios/{scenario['id']}"
                f"/assumptions/{assumption['id']}/review",
                headers=headers(),
                json={"reason": "Approved capital input"},
            )
            assert response.status_code == 200, response.text
    return client.get(f"/api/v1/cases/{case_id}/scenarios", headers=headers()).json()["scenarios"]


def _financial_inputs(case_id: UUID) -> None:
    with get_sessionmaker()() as session:
        session.add_all(
            [
                FinancialBalance(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="capital:cash",
                    balance_type="cash",
                    amount=Decimal("100"),
                    currency="USD",
                    as_of_date=date(2026, 6, 30),
                ),
                FinancialBalance(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="capital:equipment",
                    balance_type="equipment",
                    amount=Decimal("900"),
                    currency="USD",
                    as_of_date=date(2026, 6, 30),
                ),
                FinancialBalance(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="capital:payable",
                    balance_type="payable",
                    amount=Decimal("850"),
                    currency="USD",
                    as_of_date=date(2026, 6, 30),
                ),
                FinancialCashFlow(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="capital:inflow",
                    amount=Decimal("100"),
                    currency="USD",
                    direction="inflow",
                    category="operations",
                    cash_flow_date=date(2026, 6, 30),
                ),
                FinancialCashFlow(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="capital:outflow",
                    amount=Decimal("100"),
                    currency="USD",
                    direction="outflow",
                    category="operations",
                    cash_flow_date=date(2026, 6, 30),
                ),
                FinancialObligation(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="capital:facility",
                    obligation_type="revolver",
                    principal_amount=Decimal("100"),
                    outstanding_amount=Decimal("100"),
                    currency="USD",
                    status="active",
                ),
            ]
        )
        session.commit()


def _forecast(client: TestClient, case_id: UUID, scenario_id: str) -> dict:
    response = client.post(
        f"/api/v1/cases/{case_id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario_id, "forecast_periods": 2},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "succeeded"
    return response.json()


def test_capital_projection_findings_evidence_comparison_and_tenant_isolation(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenarios = _ready_scenarios(db_client, case.id)
    _financial_inputs(case.id)

    projections: dict[str, dict] = {}
    for scenario in scenarios:
        run = _forecast(db_client, case.id, scenario["id"])
        response = db_client.post(
            f"/api/v1/cases/{case.id}/capital-projections",
            headers=headers(),
            json={"calculation_run_id": run["id"]},
        )
        assert response.status_code == 201, response.text
        projections[scenario["scenario_type"]] = response.json()

    baseline = projections["baseline"]
    assert baseline["status"] == "succeeded"
    assert baseline["engine_version"] == "capital-projection-v1.0.0"
    assert baseline["indicators"][0] == {
        **baseline["indicators"][0],
        "period_number": 1,
        "equity": "50.0000",
        "equity_to_assets_ratio": "0.05263158",
        "liabilities_to_assets_ratio": "0.94736842",
        "equity_change": "0.0000",
        "pressure_level": "high",
    }
    assert baseline["findings"][0]["finding"]["rule_id"] == "capital_thin_buffer"
    assert (
        baseline["findings"][0]["evidence"][0]["locator"]["calculation_run_id"]
        == baseline["calculation_run_id"]
    )

    summary = db_client.get(
        f"/api/v1/cases/{case.id}/capital-summary?scenario_id={baseline['scenario_id']}",
        headers=headers(),
    )
    assert summary.status_code == 200
    assert summary.json()["projection"]["id"] == baseline["id"]

    comparison = db_client.get(f"/api/v1/cases/{case.id}/capital-comparison", headers=headers())
    assert comparison.status_code == 200
    compared = comparison.json()
    assert compared["baseline"]["id"] == baseline["id"]
    assert compared["downside"]["id"] == projections["downside"]["id"]
    assert len(compared["periods"]) == 2
    assert Decimal(compared["periods"][0]["equity_delta"]) < 0

    for path in (
        f"/api/v1/cases/{case.id}/capital-summary",
        f"/api/v1/cases/{case.id}/capital-comparison",
        f"/api/v1/cases/{case.id}/capital-projections/{baseline['id']}",
    ):
        assert db_client.get(path, headers=headers(ORG_2)).status_code == 404

    with get_sessionmaker()() as session:
        assert session.scalar(
            select(CapitalProjection).where(CapitalProjection.id == UUID(baseline["id"]))
        )
        assert (
            len(
                list(
                    session.scalars(
                        select(CapitalIndicator).where(
                            CapitalIndicator.projection_id == UUID(baseline["id"])
                        )
                    )
                )
            )
            == 2
        )
        finding = session.scalar(
            select(RiskFinding).where(RiskFinding.rule_id == "capital_thin_buffer")
        )
        assert finding is not None
        assert session.scalar(
            select(RiskFindingEvidence).where(RiskFindingEvidence.finding_id == finding.id)
        )
        assert session.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_id == UUID(baseline["id"]),
                AuditEvent.event_type == "capital_projection.succeeded",
            )
        )


def test_capital_summary_is_explicitly_empty(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    response = db_client.get(f"/api/v1/cases/{case.id}/capital-summary", headers=headers())
    assert response.status_code == 200
    assert response.json() == {
        "case_id": str(case.id),
        "scenario_id": None,
        "projection": None,
    }


def test_capital_projection_rejects_invalid_named_forecast_period(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenarios(db_client, case.id)[0]
    _financial_inputs(case.id)
    run = _forecast(db_client, case.id, scenario["id"])
    with get_sessionmaker()() as session:
        period = session.scalar(
            select(CalculationForecastPeriod).where(
                CalculationForecastPeriod.run_id == UUID(run["id"])
            )
        )
        assert period is not None
        period.total_assets = Decimal("0")
        period_id = str(period.id)
        session.commit()

    response = db_client.post(
        f"/api/v1/cases/{case.id}/capital-projections",
        headers=headers(),
        json={"calculation_run_id": run["id"]},
    )
    assert response.status_code == 201
    failed = response.json()
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "non_positive_projected_assets"
    assert failed["error"]["details"]["forecast_periods"] == [
        {"forecast_period_id": period_id, "period_number": 1}
    ]
