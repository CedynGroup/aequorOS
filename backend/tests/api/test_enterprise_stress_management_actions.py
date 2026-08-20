"""Enterprise-stress management-actions overlay API (docs/stress.md §3.7, Phase 3).

An APPROVED governed management-actions plan, selected on a run, produces the
directive's "results with and without management actions" (¶67(f), ¶78–81):
Appendix II Table 1's "Management actions" / "Post-capitalisation" / residual
blocks and the with/without summary. Covers the plan library's maker-checker
governance, the approved-plan run gate, reproducibility (the plan is in the input
hash), and tenant isolation of the plan library.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.models import User
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.api.test_ingestion import seed_bank

RUNS_URL = "/api/v1/banks/{bank_id}/enterprise-stress/runs"
SCENARIO_URL = "/api/v1/macro-scenarios"
PLAN_URL = "/api/v1/management-action-plans"


def _period_id(client: TestClient, bank_id: str) -> str:
    response = client.get(f"/api/v1/banks/{bank_id}/reporting-periods", headers=headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    return next(p["id"] for p in periods if p["period_end"] == "2026-03-31")


def _seed_checker() -> UUID:
    checker_id = uuid4()
    session = get_sessionmaker()()
    try:
        session.add(
            User(
                id=checker_id,
                organization_id=ORG_1,
                email=f"checker-{checker_id.hex[:8]}@aequoros.example",
                display_name="Checker",
                role="approver",
            )
        )
        session.commit()
    finally:
        session.close()
    return checker_id


def _severe_paths() -> list[dict]:
    levels = {
        "gdp_growth": ("0.05", "0.00"),
        "interest_rate": ("0.20", "0.25"),
        "inflation": ("0.15", "0.21"),
        "unemployment": ("0.06", "0.09"),
        "fx_usd_ghs": ("12.5", "15.0"),
        "gse_index": ("5000", "3500"),
        "gog_yield": ("0.22", "0.26"),
    }
    paths: list[dict] = []
    for variable, (base, stress) in levels.items():
        for year in (1, 2, 3):
            paths.append(
                {"variable": variable, "year_index": year, "base_value": base,
                 "stress_value": stress}
            )
    return paths


def _create_approved_scenario(client: TestClient, checker: UUID, code: str = "adverse_2027") -> str:
    create = client.post(
        SCENARIO_URL,
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={
            "code": code,
            "name": "2027 severe downturn",
            "scenario_type": "adverse",
            "severity": "severe",
            "horizon_years": 3,
            "source": "BoG MPC + internal desk",
            "paths": _severe_paths(),
            "reason": "Author the annual adverse scenario.",
        },
    )
    assert create.status_code == 201, create.text
    scenario_id = create.json()["id"]
    submit = client.post(
        f"{SCENARIO_URL}/{scenario_id}/submit",
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={"reason": "Ready."},
    )
    assert submit.status_code == 200, submit.text
    approve = client.post(
        f"{SCENARIO_URL}/{scenario_id}/approve",
        headers=headers(user_id=checker, roles=("approver",)),
        json={"reason": "Approved."},
    )
    assert approve.status_code == 200, approve.text
    return scenario_id


def _plan_payload(code: str = "recovery_2027") -> dict:
    return {
        "code": code,
        "name": "2027 capital-restoration plan",
        "description": "Suspend dividends and raise equity on a breach.",
        "actions": [
            {
                "action_id": "dividend_suspension",
                "kind": "revise_dividend",
                "label": "Suspend dividends",
                "trigger_kind": "on_breach",
                "effective_year": 1,
                "dividend_reduction_pct": "100",
                "rationale": "Retain earnings under stress.",
            },
            {
                "action_id": "capital_raise",
                "kind": "raise_capital",
                "label": "Equity issuance",
                "trigger_kind": "on_breach",
                "watch_minima": ["car", "paid_up", "leverage"],
                "effective_year": 1,
                "sizing": "fill_residual",
                "capital_raise_ghs": "1",
                "counts_as_paid_up": True,
                "rationale": "Restore the capital position above all minima.",
            },
        ],
        "reason": "Author the recovery plan.",
    }


def _create_plan(client: TestClient, code: str = "recovery_2027") -> str:
    response = client.post(
        PLAN_URL,
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json=_plan_payload(code),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _approve_plan(client: TestClient, plan_id: str, checker: UUID) -> None:
    submit = client.post(
        f"{PLAN_URL}/{plan_id}/submit",
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={"reason": "Ready for approval."},
    )
    assert submit.status_code == 200, submit.text
    approve = client.post(
        f"{PLAN_URL}/{plan_id}/approve",
        headers=headers(user_id=checker, roles=("approver",)),
        json={"reason": "Reviewed and approved."},
    )
    assert approve.status_code == 200, approve.text


def test_management_action_plan_governance_lifecycle(db_client: TestClient) -> None:
    seed_bank(db_client)
    checker = _seed_checker()
    plan_id = _create_plan(db_client)

    # A draft plan is not consumable and the maker cannot approve their own plan.
    submit = db_client.post(
        f"{PLAN_URL}/{plan_id}/submit",
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={"reason": "Ready."},
    )
    assert submit.status_code == 200, submit.text
    self_approve = db_client.post(
        f"{PLAN_URL}/{plan_id}/approve",
        headers=headers(user_id=USER_1, roles=("approver",)),
        json={"reason": "Approve my own."},
    )
    assert self_approve.status_code == 409
    assert self_approve.json()["error"]["details"]["error_code"] == "maker_is_checker"

    approve = db_client.post(
        f"{PLAN_URL}/{plan_id}/approve",
        headers=headers(user_id=checker, roles=("approver",)),
        json={"reason": "Approved."},
    )
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["status"] == "approved"
    assert body["version"] == 2
    assert len(body["actions"]) == 2

    # Tenant isolation: another org cannot read the plan.
    foreign = db_client.get(f"{PLAN_URL}/{plan_id}", headers=headers(ORG_2))
    assert foreign.status_code == 404


def test_enterprise_stress_reports_with_and_without_management_actions(
    db_client: TestClient,
) -> None:
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    checker = _seed_checker()
    scenario_id = _create_approved_scenario(db_client, checker)
    plan_id = _create_plan(db_client)
    _approve_plan(db_client, plan_id, checker)

    payload = {
        "scenario_id": scenario_id,
        "reporting_period_id": period_id,
        "management_action_plan_id": plan_id,
        "paid_up_min": "400000000",
        "reason": "Annual ICAAP stress test with recovery plan.",
    }
    response = db_client.post(RUNS_URL.format(bank_id=bank_id), headers=headers(), json=payload)
    assert response.status_code == 201, response.text
    run = response.json()

    # WITHOUT actions the paid-up floor is breached; WITH the plan it is restored.
    summary = run["summary"]
    assert summary["stress_stays_above_all_minima"] is False
    assert "paid_up" in summary["binding_minima"]
    assert summary["management_action_plan_code"] == "recovery_2027"
    assert summary["with_actions_stays_above_all_minima"] is True
    assert Decimal(summary["residual_capital_required_after_actions"]) == Decimal("0")

    # Appendix II Table 1 carries the management-action, post-cap and residual blocks.
    t1 = run["appendix_ii"]["table1_summary"]
    assert t1["management_actions"] is not None
    assert t1["management_actions"]["plan_id"] == "recovery_2027"
    assert len(t1["management_actions"]["rows"]) == 3
    assert len(t1["post_capitalisation"]) == 3
    assert t1["residual_capital_required_after_actions"]["worst"] == "0.000"
    # The equity raise restores paid-up to the 400,000 ('000) floor.
    for snapshot in t1["post_capitalisation"]:
        assert snapshot["paid_up"] == "400000.000"
    # Total management actions is positive (dividends preserved + equity raised).
    assert all(
        row["total_management_actions"] != "0.000" for row in t1["management_actions"]["rows"]
    )
    # Both legs are present: the WITHOUT projection still breaches.
    assert run["projection"]["stress_stays_above_all_minima"] is False

    # Reproducibility: the plan is in the value-based input hash.
    again = db_client.post(RUNS_URL.format(bank_id=bank_id), headers=headers(), json=payload)
    assert again.status_code == 201, again.text
    assert again.json()["input_hash"] == run["input_hash"]

    # A run WITHOUT a plan anchors a DIFFERENT hash and leaves the blocks empty.
    no_plan = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "paid_up_min": "400000000",
            "reason": "Pre-management-action run.",
        },
    )
    assert no_plan.status_code == 201, no_plan.text
    assert no_plan.json()["input_hash"] != run["input_hash"]
    assert no_plan.json()["appendix_ii"]["table1_summary"]["management_actions"] is None
    assert no_plan.json()["summary"]["management_action_plan_code"] is None


def test_enterprise_stress_rejects_an_unapproved_plan(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    checker = _seed_checker()
    scenario_id = _create_approved_scenario(db_client, checker)
    plan_id = _create_plan(db_client, code="draft_plan")  # left as a draft

    response = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "management_action_plan_id": plan_id,
            "reason": "Attempt to use an unapproved plan.",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "plan_not_approved"
