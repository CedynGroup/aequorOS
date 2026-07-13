from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import (
    AuditEvent,
    CalculationForecastPeriod,
    CalculationRun,
    FinancialBalance,
    FinancialCashFlow,
    FinancialObligation,
)
from tests.api.factories import CaseFactory
from tests.api.helpers import ORG_1, ORG_2, headers


def _ready_scenario(client: TestClient, case_id: UUID) -> dict:
    workspace = client.post(
        f"/api/v1/cases/{case_id}/scenarios/initialize",
        headers=headers(),
        json={"reason": "Prepare calculation assumptions"},
    ).json()
    scenario = workspace["scenarios"][0]
    for assumption in scenario["assumptions"]:
        response = client.post(
            f"/api/v1/cases/{case_id}/scenarios/{scenario['id']}/assumptions/{assumption['id']}/review",
            headers=headers(),
            json={"reason": "Approved calculation input"},
        )
        assert response.status_code == 200, response.text
    return client.get(
        f"/api/v1/cases/{case_id}/scenarios/{scenario['id']}", headers=headers()
    ).json()


def _financial_inputs(case_id: UUID) -> None:
    with get_sessionmaker()() as session:
        session.add_all(
            [
                FinancialBalance(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="forecast:cash",
                    balance_type="cash",
                    amount=Decimal("1000"),
                    currency="USD",
                    as_of_date=date(2026, 6, 30),
                ),
                FinancialBalance(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="forecast:equipment",
                    balance_type="equipment",
                    amount=Decimal("4000"),
                    currency="USD",
                    as_of_date=date(2026, 6, 30),
                ),
                FinancialBalance(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="forecast:payable",
                    balance_type="payable",
                    amount=Decimal("500"),
                    currency="USD",
                    as_of_date=date(2026, 6, 30),
                ),
                FinancialCashFlow(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="forecast:inflow",
                    amount=Decimal("100"),
                    currency="USD",
                    direction="inflow",
                    category="operations",
                ),
                FinancialCashFlow(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="forecast:outflow",
                    amount=Decimal("50"),
                    currency="USD",
                    direction="outflow",
                    category="operations",
                ),
                FinancialObligation(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="forecast:facility",
                    obligation_type="revolver",
                    principal_amount=Decimal("2000"),
                    outstanding_amount=Decimal("1000"),
                    currency="USD",
                    status="active",
                ),
            ]
        )
        session.commit()


def test_calculation_correctness_persistence_and_reproducible_rerun(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)

    started = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"], "forecast_periods": 2},
    )
    assert started.status_code == 201, started.text
    run = started.json()
    assert run["status"] == "succeeded"
    assert run["engine_version"] == "balance-sheet-v1.0.0"
    assert run["as_of_date"] == "2026-06-30"
    assert len(run["input_hash"]) == 64
    assert [
        (row["total_assets"], row["total_liabilities"], row["total_equity"])
        for row in run["outputs"]
    ] == [
        ("4550.0000", "1000.0000", "3550.0000"),
        ("4100.0000", "500.0000", "3600.0000"),
    ]

    rerun = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs/{run['id']}/rerun",
        headers=headers(),
        json={},
    )
    assert rerun.status_code == 201, rerun.text
    repeated = rerun.json()
    assert repeated["id"] != run["id"]
    assert repeated["rerun_of_run_id"] == run["id"]
    assert repeated["input_hash"] == run["input_hash"]
    assert repeated["outputs"] == [
        {**row, "id": repeated["outputs"][index]["id"]} for index, row in enumerate(run["outputs"])
    ]

    listing = db_client.get(f"/api/v1/cases/{case.id}/calculation-runs", headers=headers()).json()
    assert listing["latest_successful_run_id"] == repeated["id"]
    assert len(listing["runs"]) == 2

    with get_sessionmaker()() as session:
        assert session.scalar(select(CalculationRun).where(CalculationRun.id == UUID(run["id"])))
        assert (
            len(
                list(
                    session.scalars(
                        select(CalculationForecastPeriod).where(
                            CalculationForecastPeriod.run_id == UUID(run["id"])
                        )
                    )
                )
            )
            == 2
        )
        assert session.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_id == UUID(run["id"]),
                AuditEvent.event_type == "calculation_run.succeeded",
            )
        )


def test_rerun_after_assumption_change_versions_inputs_without_replacing_output(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    first = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"], "forecast_periods": 1},
    ).json()
    growth = next(item for item in scenario["assumptions"] if item["key"] == "revenue_growth_rate")
    assert (
        db_client.patch(
            f"/api/v1/cases/{case.id}/scenarios/{scenario['id']}/assumptions/{growth['id']}",
            headers=headers(),
            json={"value": 0.1, "reason": "Update forecast plan"},
        ).status_code
        == 200
    )
    assert (
        db_client.post(
            f"/api/v1/cases/{case.id}/scenarios/{scenario['id']}/assumptions/{growth['id']}/review",
            headers=headers(),
            json={"reason": "Approve changed plan"},
        ).status_code
        == 200
    )
    second = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs/{first['id']}/rerun",
        headers=headers(),
        json={},
    ).json()
    assert second["input_hash"] != first["input_hash"]
    assert second["outputs"][0]["projected_inflows"] == "110.0000"
    persisted_first = db_client.get(
        f"/api/v1/cases/{case.id}/calculation-runs/{first['id']}", headers=headers()
    ).json()
    assert persisted_first["outputs"] == first["outputs"]


def test_failed_run_is_persisted_and_prior_success_remains_current(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    workspace = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios/initialize",
        headers=headers(),
        json={"reason": "Prepare scenarios"},
    ).json()
    scenario = workspace["scenarios"][0]
    _financial_inputs(case.id)
    failed = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    )
    assert failed.status_code == 201
    body = failed.json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "scenario_not_ready"
    assert body["outputs"] == []
    assert body["error"]["details"]["unreviewed_assumptions"]
    listing = db_client.get(f"/api/v1/cases/{case.id}/calculation-runs", headers=headers()).json()
    assert listing["latest_successful_run_id"] is None


@pytest.mark.parametrize("input_model", [FinancialCashFlow, FinancialObligation])
def test_calculation_rejects_mixed_currencies_across_all_inputs(
    db_client: TestClient,
    input_model: type[FinancialCashFlow] | type[FinancialObligation],
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    with get_sessionmaker()() as session:
        input_row = session.scalar(select(input_model).where(input_model.case_id == case.id))
        assert input_row is not None
        input_row.currency = "EUR"
        session.commit()

    response = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    )

    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "failed"
    assert run["error"] == {
        "code": "multiple_currencies",
        "message": "The first forecast supports one reporting currency per case.",
        "details": {"currencies": ["EUR", "USD"]},
    }


def test_out_of_range_forecast_is_persisted_as_failed(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    with get_sessionmaker()() as session:
        session.add(
            FinancialBalance(
                organization_id=ORG_1,
                case_id=case.id,
                dedupe_key="forecast:max-equipment",
                balance_type="equipment",
                amount=Decimal("9999999999999999.9999"),
                currency="USD",
                as_of_date=date(2026, 6, 30),
            )
        )
        session.commit()

    response = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    )

    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "failed"
    assert run["error"]["code"] == "calculation_output_out_of_range"
    assert run["outputs"] == []
    with get_sessionmaker()() as session:
        persisted = session.scalar(
            select(CalculationRun).where(CalculationRun.id == UUID(run["id"]))
        )
        assert persisted is not None
        assert persisted.status == "failed"


def test_calculation_runs_are_tenant_scoped(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    response = db_client.get(f"/api/v1/cases/{case.id}/calculation-runs", headers=headers(ORG_2))
    assert response.status_code == 404
