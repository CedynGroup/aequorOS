from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo
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
    FinancialReportingPeriod,
    ScenarioAssumption,
)
from app.services import calculations
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
                    cash_flow_date=date(2026, 6, 30),
                ),
                FinancialCashFlow(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="forecast:outflow",
                    amount=Decimal("50"),
                    currency="USD",
                    direction="outflow",
                    category="operations",
                    cash_flow_date=date(2026, 6, 30),
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
    assert run["as_of_date"] == run["inputs"]["as_of_date"]
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


def test_default_as_of_date_excludes_future_balances_unless_explicit(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> FixedDatetime:
            return cls(2026, 7, 13, tzinfo=UTC)

    monkeypatch.setattr(calculations, "datetime", FixedDatetime)
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    with get_sessionmaker()() as session:
        future_balance = FinancialBalance(
            organization_id=ORG_1,
            case_id=case.id,
            dedupe_key="forecast:future-cash",
            balance_type="cash",
            amount=Decimal("9000"),
            currency="USD",
            as_of_date=date(2027, 6, 30),
        )
        session.add(future_balance)
        session.commit()
        future_balance_id = str(future_balance.id)

    default_run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"], "forecast_periods": 1},
    ).json()
    future_run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={
            "scenario_id": scenario["id"],
            "forecast_periods": 1,
            "as_of_date": "2027-06-30",
        },
    ).json()

    assert default_run["status"] == "succeeded"
    assert default_run["as_of_date"] == "2026-07-13"
    assert default_run["inputs"]["effective_balance_date"] == "2026-06-30"
    assert future_balance_id not in {item["id"] for item in default_run["inputs"]["balances"]}
    assert future_run["status"] == "succeeded"
    assert future_run["as_of_date"] == "2027-06-30"
    assert future_run["inputs"]["effective_balance_date"] == "2027-06-30"
    assert [item["id"] for item in future_run["inputs"]["balances"]] == [future_balance_id]


def test_audit_binds_pending_run_to_established_input_hash(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)

    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()

    with get_sessionmaker()() as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.entity_id == UUID(run["id"]))
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
    started = next(item for item in events if item.event_type == "calculation_run.started")
    established = next(
        item for item in events if item.event_type == "calculation_run.input_snapshot_established"
    )
    assert started.details["input_hash_status"] == "pending"
    assert started.details["input_hash"] != run["input_hash"]
    assert established.details == {
        "input_hash": run["input_hash"],
        "input_hash_status": "established",
        "as_of_date": run["as_of_date"],
        "input_schema_version": run["input_schema_version"],
    }


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
    assert body["inputs"]["snapshot_status"] == "pending"
    assert "validation_error" not in body["inputs"]
    assert body["input_hash"] == calculations._snapshot_hash(body["inputs"])
    with get_sessionmaker()() as session:
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.entity_id == UUID(body["id"]))
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
    assert not any(
        item.event_type == "calculation_run.input_snapshot_established" for item in events
    )
    rejected = next(
        item for item in events if item.event_type == "calculation_run.input_snapshot_rejected"
    )
    failed_event = next(item for item in events if item.event_type == "calculation_run.failed")
    assert rejected.details["input_hash"] == body["input_hash"]
    assert rejected.details["input_hash_status"] == "rejected"
    assert rejected.details["error_code"] == body["error"]["code"]
    assert failed_event.details["input_hash"] == body["input_hash"]
    assert failed_event.details["input_hash_status"] == "rejected"
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
    assert run["error"]["code"] == "multiple_currencies"
    assert run["error"]["details"]["currencies"] == ["EUR", "USD"]
    assert "Convert" in run["error"]["details"]["corrective_action"]


@pytest.mark.parametrize(
    ("input_model", "input_type"),
    [
        (FinancialBalance, "balance"),
        (FinancialCashFlow, "cash_flow"),
        (FinancialObligation, "obligation"),
    ],
)
def test_calculation_rejects_missing_currencies_across_all_inputs(
    db_client: TestClient,
    input_model: type[FinancialBalance] | type[FinancialCashFlow] | type[FinancialObligation],
    input_type: str,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    with get_sessionmaker()() as session:
        input_row = session.scalar(select(input_model).where(input_model.case_id == case.id))
        assert input_row is not None
        input_id = input_row.id
        input_row.currency = None
        session.commit()

    response = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    )

    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "failed"
    assert run["error"]["code"] == "missing_currency"
    assert run["error"]["details"]["inputs"] == [{"type": input_type, "id": str(input_id)}]
    assert "currency" in run["error"]["details"]["corrective_action"]


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
    assert "snapshot_status" not in run["inputs"]
    assert "validation_error" not in run["inputs"]
    assert run["input_hash"] == calculations._snapshot_hash(run["inputs"])
    assert run["inputs"]["scenario"]["id"] == scenario["id"]
    assert run["inputs"]["balances"]
    assert run["inputs"]["cash_flows"]
    assert run["inputs"]["obligations"]
    with get_sessionmaker()() as session:
        persisted = session.scalar(
            select(CalculationRun).where(CalculationRun.id == UUID(run["id"]))
        )
        assert persisted is not None
        assert persisted.status == "failed"
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.entity_id == UUID(run["id"]))
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
    established = [
        item for item in events if item.event_type == "calculation_run.input_snapshot_established"
    ]
    assert len(established) == 1
    assert established[0].details["input_hash"] == run["input_hash"]
    assert established[0].details["input_hash_status"] == "established"
    assert not any(item.event_type == "calculation_run.input_snapshot_rejected" for item in events)
    failed_event = next(item for item in events if item.event_type == "calculation_run.failed")
    assert failed_event.details["input_hash"] == run["input_hash"]
    assert failed_event.details["input_hash_status"] == "established"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_assumption_is_rejected(value: str) -> None:
    with pytest.raises(calculations.CalculationInputError) as raised:
        calculations._decimal_assumption(value, "revenue_growth_rate")

    assert raised.value.code == "invalid_assumption"
    assert raised.value.details == {
        "assumption": "revenue_growth_rate",
        "corrective_action": "Enter a finite numeric value and review the assumption again.",
    }


def test_only_active_obligations_participate(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    with get_sessionmaker()() as session:
        obligation = session.scalar(
            select(FinancialObligation).where(FinancialObligation.case_id == case.id)
        )
        assert obligation is not None
        obligation.status = "closed"
        obligation.principal_amount = None
        obligation.outstanding_amount = None
        obligation.currency = None
        session.add(
            FinancialObligation(
                organization_id=ORG_1,
                case_id=case.id,
                dedupe_key="forecast:future-facility",
                obligation_type="term_loan",
                principal_amount=None,
                outstanding_amount=None,
                currency=None,
                start_date=date(2027, 1, 1),
                status="active",
            )
        )
        session.commit()

    response = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"], "forecast_periods": 1},
    )

    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "succeeded"
    assert run["inputs"]["obligations"] == []
    assert run["outputs"][0]["total_liabilities"] == "500.0000"


def test_undated_cash_flow_without_reporting_period_is_actionable_failure(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    with get_sessionmaker()() as session:
        cash_flow = session.scalar(
            select(FinancialCashFlow).where(
                FinancialCashFlow.case_id == case.id,
                FinancialCashFlow.direction == "inflow",
            )
        )
        assert cash_flow is not None
        cash_flow.cash_flow_date = None
        session.commit()
        cash_flow_id = str(cash_flow.id)

    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()

    assert run["status"] == "failed"
    assert run["error"]["code"] == "financial_period_missing"
    assert run["error"]["details"]["cash_flows"] == [{"id": cash_flow_id, "category": "operations"}]
    assert "cash-flow date" in run["error"]["details"]["corrective_action"]


def test_cash_flows_outside_selected_reporting_period_name_every_record(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    with get_sessionmaker()() as session:
        reporting_period = FinancialReportingPeriod(
            organization_id=ORG_1,
            case_id=case.id,
            dedupe_key="forecast:period:2026",
            period_type="year",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            label="2026",
        )
        session.add(reporting_period)
        session.flush()
        session.add(
            FinancialBalance(
                organization_id=ORG_1,
                case_id=case.id,
                dedupe_key="forecast:period-cash",
                reporting_period_id=reporting_period.id,
                balance_type="cash",
                amount=Decimal("1000"),
                currency="USD",
            )
        )
        cash_flows = [
            FinancialCashFlow(
                organization_id=ORG_1,
                case_id=case.id,
                dedupe_key=f"forecast:outside-flow:{cash_flow_date.isoformat()}",
                reporting_period_id=reporting_period.id,
                amount=Decimal("100"),
                currency="USD",
                direction="inflow",
                category="operations",
                cash_flow_date=cash_flow_date,
            )
            for cash_flow_date in (date(2025, 12, 31), date(2027, 1, 1))
        ]
        session.add_all(cash_flows)
        session.commit()
        expected = sorted(
            [
                {
                    "id": str(item.id),
                    "category": "operations",
                    "cash_flow_date": str(item.cash_flow_date),
                    "reporting_period_id": str(reporting_period.id),
                    "period_start_date": "2026-01-01",
                    "period_end_date": "2026-12-31",
                }
                for item in cash_flows
            ],
            key=lambda item: item["id"],
        )

    response = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"], "as_of_date": "2026-12-31"},
    )

    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "failed"
    assert run["error"]["code"] == "cash_flow_date_outside_reporting_period"
    assert run["error"]["details"]["cash_flows"] == expected
    assert "review workspace" in run["error"]["details"]["corrective_action"]
    persisted = db_client.get(
        f"/api/v1/cases/{case.id}/calculation-runs/{run['id']}", headers=headers()
    ).json()
    assert persisted["error"] == run["error"]


def test_active_obligations_missing_amounts_name_every_record(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    with get_sessionmaker()() as session:
        first = session.scalar(
            select(FinancialObligation).where(FinancialObligation.case_id == case.id)
        )
        assert first is not None
        first.principal_amount = None
        second = FinancialObligation(
            organization_id=ORG_1,
            case_id=case.id,
            dedupe_key="forecast:incomplete-facility",
            obligation_type="term_loan",
            principal_amount=Decimal("500"),
            outstanding_amount=None,
            currency="USD",
            status="active",
        )
        session.add(second)
        session.commit()
        expected = {
            str(first.id): ["principal_amount"],
            str(second.id): ["outstanding_amount"],
        }

    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()

    assert run["status"] == "failed"
    assert run["error"]["code"] == "active_obligation_amounts_missing"
    assert {
        item["id"]: item["missing_fields"] for item in run["error"]["details"]["obligations"]
    } == expected
    assert "mark an obligation inactive" in run["error"]["details"]["corrective_action"]


def test_snapshot_uses_effective_reporting_period_and_excludes_later_records(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    with get_sessionmaker()() as session:
        old_period = FinancialReportingPeriod(
            organization_id=ORG_1,
            case_id=case.id,
            dedupe_key="forecast:period:2025",
            period_type="year",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            label="2025",
        )
        new_period = FinancialReportingPeriod(
            organization_id=ORG_1,
            case_id=case.id,
            dedupe_key="forecast:period:2026",
            period_type="year",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            label="2026",
        )
        session.add_all([old_period, new_period])
        session.flush()
        old_balance = FinancialBalance(
            organization_id=ORG_1,
            case_id=case.id,
            dedupe_key="forecast:old-cash",
            reporting_period_id=old_period.id,
            balance_type="cash",
            amount=Decimal("100"),
            currency="USD",
        )
        new_balance = FinancialBalance(
            organization_id=ORG_1,
            case_id=case.id,
            dedupe_key="forecast:new-cash",
            reporting_period_id=new_period.id,
            balance_type="cash",
            amount=Decimal("1000"),
            currency="USD",
        )
        old_flow = FinancialCashFlow(
            organization_id=ORG_1,
            case_id=case.id,
            dedupe_key="forecast:old-flow",
            amount=Decimal("10"),
            currency="USD",
            direction="inflow",
            category="operations",
            cash_flow_date=date(2025, 6, 30),
        )
        new_flow = FinancialCashFlow(
            organization_id=ORG_1,
            case_id=case.id,
            dedupe_key="forecast:new-flow",
            amount=Decimal("100"),
            currency="USD",
            direction="inflow",
            category="operations",
            cash_flow_date=date(2026, 6, 30),
        )
        session.add_all([old_balance, new_balance, old_flow, new_flow])
        session.commit()

    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"], "as_of_date": "2025-12-31"},
    ).json()

    assert run["status"] == "succeeded"
    assert run["inputs"]["reporting_period"]["label"] == "2025"
    assert [item["id"] for item in run["inputs"]["balances"]] == [str(old_balance.id)]
    assert [item["id"] for item in run["inputs"]["cash_flows"]] == [str(old_flow.id)]


def test_unknown_balance_type_is_an_actionable_persisted_failure(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    with get_sessionmaker()() as session:
        balance = session.scalar(
            select(FinancialBalance).where(
                FinancialBalance.case_id == case.id,
                FinancialBalance.balance_type == "equipment",
            )
        )
        assert balance is not None
        balance.balance_type = "mystery_position"
        session.commit()
        balance_id = str(balance.id)

    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()

    assert run["status"] == "failed"
    assert run["error"]["code"] == "unknown_balance_type"
    assert run["error"]["details"]["balances"] == [
        {"id": balance_id, "balance_type": "mystery_position"}
    ]
    assert run["error"]["details"]["corrective_action"]


def test_category_semantics_keep_readiness_and_calculation_aligned(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    with get_sessionmaker()() as session:
        growth = session.scalar(
            select(ScenarioAssumption).where(
                ScenarioAssumption.scenario_id == UUID(scenario["id"]),
                ScenarioAssumption.category == "growth",
            )
        )
        assert growth is not None
        growth.key = "custom_revenue_plan"
        session.commit()

    validation = db_client.get(
        f"/api/v1/cases/{case.id}/scenarios/{scenario['id']}/validation", headers=headers()
    ).json()
    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()

    assert validation["complete"] is True
    assert run["status"] == "succeeded"
    assert run["inputs"]["scenario"]["numeric_assumptions"]["revenue_growth_rate"] == "0"


def test_history_is_paginated_summaries_and_details_are_fetched_separately(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    for _ in range(2):
        response = db_client.post(
            f"/api/v1/cases/{case.id}/calculation-runs",
            headers=headers(),
            json={"scenario_id": scenario["id"]},
        )
        assert response.status_code == 201

    listing = db_client.get(
        f"/api/v1/cases/{case.id}/calculation-runs?limit=1&offset=0", headers=headers()
    ).json()

    assert listing["total"] == 2
    assert listing["limit"] == 1
    assert listing["offset"] == 0
    assert listing["has_more"] is True
    assert len(listing["runs"]) == 1
    assert "inputs" not in listing["runs"][0]
    assert "outputs" not in listing["runs"][0]
    detail = db_client.get(
        f"/api/v1/cases/{case.id}/calculation-runs/{listing['runs'][0]['id']}",
        headers=headers(),
    ).json()
    assert detail["inputs"]
    assert detail["outputs"]


def test_running_state_is_committed_before_snapshot_execution(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)
    original = calculations.build_input_snapshot
    observed: list[str] = []

    def observe_persisted_run(*args: object, **kwargs: object) -> tuple[dict, date]:
        with get_sessionmaker()() as session:
            persisted = session.scalar(
                select(CalculationRun)
                .where(CalculationRun.case_id == case.id)
                .order_by(CalculationRun.created_at.desc())
            )
            assert persisted is not None
            observed.append(persisted.status)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(calculations, "build_input_snapshot", observe_persisted_run)
    response = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    )

    assert response.status_code == 201
    assert observed == ["running"]


def test_unexpected_failure_is_persisted_with_sanitized_diagnostic(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = CaseFactory(db_client).create()
    scenario = _ready_scenario(db_client, case.id)
    _financial_inputs(case.id)

    def fail(_snapshot: dict) -> list:
        raise RuntimeError("secret database path and SQL")

    monkeypatch.setattr(calculations, "calculate_forecast", fail)
    run = db_client.post(
        f"/api/v1/cases/{case.id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": scenario["id"]},
    ).json()

    assert run["status"] == "failed"
    assert run["error"]["code"] == "calculation_error"
    assert "secret" not in str(run["error"])
    assert run["error"]["details"]["corrective_action"]


def test_calculation_runs_are_tenant_scoped(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    response = db_client.get(f"/api/v1/cases/{case.id}/calculation-runs", headers=headers(ORG_2))
    assert response.status_code == 404
