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


def _forecast(
    client: TestClient,
    case_id: UUID,
    scenario_id: str,
    *,
    forecast_periods: int = 2,
    as_of_date: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "scenario_id": scenario_id,
        "forecast_periods": forecast_periods,
    }
    if as_of_date is not None:
        payload["as_of_date"] = as_of_date
    response = client.post(
        f"/api/v1/cases/{case_id}/calculation-runs",
        headers=headers(),
        json=payload,
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
    assert baseline["reporting_currency"] == "USD"
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
    assert summary.json()["projection"]["reporting_currency"] == "USD"

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


def test_capital_comparison_rejects_incompatible_forecast_bases(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenarios = _ready_scenarios(db_client, case.id)
    _financial_inputs(case.id)
    baseline_run = _forecast(db_client, case.id, scenarios[0]["id"])
    baseline = db_client.post(
        f"/api/v1/cases/{case.id}/capital-projections",
        headers=headers(),
        json={"calculation_run_id": baseline_run["id"]},
    ).json()

    with get_sessionmaker()() as session:
        for model in (FinancialBalance, FinancialCashFlow, FinancialObligation):
            for record in session.scalars(select(model).where(model.case_id == case.id)):
                record.currency = "EUR"
        session.commit()

    downside_run = _forecast(
        db_client,
        case.id,
        scenarios[1]["id"],
        forecast_periods=3,
        as_of_date="2026-07-01",
    )
    downside = db_client.post(
        f"/api/v1/cases/{case.id}/capital-projections",
        headers=headers(),
        json={"calculation_run_id": downside_run["id"]},
    ).json()

    response = db_client.get(f"/api/v1/cases/{case.id}/capital-comparison", headers=headers())
    assert response.status_code == 200
    comparison = response.json()
    assert comparison["baseline"]["id"] == baseline["id"]
    assert comparison["downside"]["id"] == downside["id"]
    assert comparison["periods"] == []
    assert comparison["diagnostic"] == {
        "code": "comparison_basis_mismatch",
        "message": "Baseline and downside projections use incompatible forecast bases.",
        "differing_attributes": [
            "as_of_date",
            "reporting_currency",
            "forecast_horizon",
        ],
        "baseline_basis": {
            "as_of_date": baseline_run["as_of_date"],
            "reporting_currency": "USD",
            "forecast_horizon": 2,
        },
        "downside_basis": {
            "as_of_date": "2026-07-01",
            "reporting_currency": "EUR",
            "forecast_horizon": 3,
        },
        "corrective_action": (
            "Rerun the other scenario using the matching as-of date, reporting currency, "
            "and forecast horizon, then generate a new capital projection."
        ),
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


def test_capital_projection_persists_invalid_opening_balance_diagnostic(
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
        period.components = {**period.components, "opening_assets": "invalid"}
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
    assert failed["error"] == {
        "code": "forecast_evidence_missing",
        "message": "The forecast output is missing opening balance evidence.",
        "details": {
            "forecast_period_id": period_id,
            "required_components": ["opening_assets", "opening_liabilities"],
        },
    }

    persisted = db_client.get(
        f"/api/v1/cases/{case.id}/capital-projections/{failed['id']}",
        headers=headers(),
    )
    assert persisted.status_code == 200
    assert persisted.json()["error"] == failed["error"]


def test_capital_projection_persists_derived_indicator_range_diagnostic(
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
        period.total_assets = Decimal("0.0001")
        period.total_liabilities = Decimal("100000000.0000")
        period.total_equity = Decimal("-99999999.9999")
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
    assert failed["error"] == {
        "code": "capital_indicator_out_of_range",
        "message": "A derived capital indicator exceeds its supported numeric range.",
        "details": {
            "forecast_period_id": period_id,
            "period_number": 1,
            "field": "equity_to_assets_ratio",
            "value": "-999999999999",
            "precision": 12,
            "scale": 8,
        },
    }
    assert failed["indicators"] == []

    persisted = db_client.get(
        f"/api/v1/cases/{case.id}/capital-projections/{failed['id']}",
        headers=headers(),
    )
    assert persisted.status_code == 200
    assert persisted.json()["error"] == failed["error"]


def test_capital_projection_history_persists_failures_and_is_tenant_scoped(
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
        period.components = {**period.components, "opening_assets": "NaN"}
        session.commit()

    failed = db_client.post(
        f"/api/v1/cases/{case.id}/capital-projections",
        headers=headers(),
        json={"calculation_run_id": run["id"]},
    ).json()
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "forecast_evidence_missing"

    history = db_client.get(
        f"/api/v1/cases/{case.id}/capital-projections?limit=1&offset=0",
        headers=headers(),
    )
    assert history.status_code == 200
    body = history.json()
    assert body["case_id"] == str(case.id)
    assert body["total"] == 1
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert body["has_more"] is False
    assert body["projections"][0]["id"] == failed["id"]
    assert "error" not in body["projections"][0]
    assert "indicators" not in body["projections"][0]
    assert "findings" not in body["projections"][0]
    detail = db_client.get(
        f"/api/v1/cases/{case.id}/capital-projections/{failed['id']}",
        headers=headers(),
    )
    assert detail.status_code == 200
    assert detail.json()["error"] == failed["error"]
    assert (
        db_client.get(
            f"/api/v1/cases/{case.id}/capital-projections", headers=headers(ORG_2)
        ).status_code
        == 404
    )


def test_capital_rerun_supersedes_only_unreviewed_findings_for_same_scenario(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenarios = _ready_scenarios(db_client, case.id)
    scenario = scenarios[0]
    _financial_inputs(case.id)
    run = _forecast(db_client, case.id, scenario["id"])
    other_run = _forecast(db_client, case.id, scenarios[1]["id"])
    other = db_client.post(
        f"/api/v1/cases/{case.id}/capital-projections",
        headers=headers(),
        json={"calculation_run_id": other_run["id"]},
    ).json()
    other_finding_id = UUID(other["findings"][0]["finding"]["id"])

    first = db_client.post(
        f"/api/v1/cases/{case.id}/capital-projections",
        headers=headers(),
        json={"calculation_run_id": run["id"]},
    ).json()
    first_finding_ids = [UUID(item["finding"]["id"]) for item in first["findings"]]
    assert first_finding_ids

    second = db_client.post(
        f"/api/v1/cases/{case.id}/capital-projections",
        headers=headers(),
        json={"calculation_run_id": run["id"]},
    ).json()
    assert second["status"] == "succeeded"
    second_finding_id = UUID(second["findings"][0]["finding"]["id"])

    with get_sessionmaker()() as session:
        prior = list(
            session.scalars(select(RiskFinding).where(RiskFinding.id.in_(first_finding_ids)))
        )
        for finding in prior:
            assert finding.status == "superseded"
            assert finding.details["superseded_by_capital_projection_id"] == second["id"]
        reviewed = session.get(RiskFinding, second_finding_id)
        assert reviewed is not None
        reviewed.status = "acknowledged"
        other_finding = session.get(RiskFinding, other_finding_id)
        assert other_finding is not None
        assert other_finding.status == "needs_review"
        session.commit()

    third = db_client.post(
        f"/api/v1/cases/{case.id}/capital-projections",
        headers=headers(),
        json={"calculation_run_id": run["id"]},
    ).json()
    assert third["status"] == "succeeded"
    with get_sessionmaker()() as session:
        reviewed = session.get(RiskFinding, second_finding_id)
        assert reviewed is not None
        assert reviewed.status == "acknowledged"
        assert "superseded_by_capital_projection_id" not in reviewed.details


def test_archived_capital_inputs_are_retired_without_hiding_history(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenarios(db_client, case.id)[0]
    _financial_inputs(case.id)
    run = _forecast(db_client, case.id, scenario["id"])
    created = db_client.post(
        f"/api/v1/cases/{case.id}/capital-projections",
        headers=headers(),
        json={"calculation_run_id": run["id"]},
    ).json()

    archived_scenario = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios/{scenario['id']}/archive",
        headers=headers(),
        json={"reason": "Retire scenario"},
    )
    assert archived_scenario.status_code == 200
    retired_scenario_projection = db_client.post(
        f"/api/v1/cases/{case.id}/capital-projections",
        headers=headers(),
        json={"calculation_run_id": run["id"]},
    )
    assert retired_scenario_projection.status_code == 409
    assert retired_scenario_projection.json()["error"]["message"] == (
        "Archived scenarios cannot be used for capital projections."
    )
    comparison = db_client.get(
        f"/api/v1/cases/{case.id}/capital-comparison", headers=headers()
    )
    assert comparison.status_code == 200
    assert comparison.json()[scenario["scenario_type"]] is None

    archived_case = db_client.post(
        f"/api/v1/cases/{case.id}/archive", headers=headers()
    )
    assert archived_case.status_code == 200
    retired_case_projection = db_client.post(
        f"/api/v1/cases/{case.id}/capital-projections",
        headers=headers(),
        json={"calculation_run_id": run["id"]},
    )
    assert retired_case_projection.status_code == 409
    finding_update = db_client.patch(
        f"/api/v1/findings/{created['findings'][0]['finding']['id']}",
        headers=headers(),
        json={"status": "acknowledged"},
    )
    assert finding_update.status_code == 409
    retired_comparison = db_client.get(
        f"/api/v1/cases/{case.id}/capital-comparison", headers=headers()
    )
    assert retired_comparison.status_code == 409

    history = db_client.get(
        f"/api/v1/cases/{case.id}/capital-projections", headers=headers()
    )
    detail = db_client.get(
        f"/api/v1/cases/{case.id}/capital-projections/{created['id']}",
        headers=headers(),
    )
    assert history.status_code == 200
    assert history.json()["projections"][0]["id"] == created["id"]
    assert detail.status_code == 200
    assert detail.json()["reporting_currency"] == "USD"
