from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import AuditEvent, ScenarioAssumptionHistory
from tests.api.factories import CaseFactory
from tests.api.helpers import ORG_2, headers


def initialize(client: TestClient, case_id: UUID) -> dict:
    response = client.post(
        f"/api/v1/cases/{case_id}/scenarios/initialize",
        headers=headers(),
        json={"reason": "Initialize forecast assumptions"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_default_scenario_lifecycle_validation_review_and_readiness(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    workspace = initialize(db_client, case.id)

    assert [scenario["scenario_type"] for scenario in workspace["scenarios"]] == [
        "baseline",
        "downside",
    ]
    assert all(len(scenario["assumptions"]) == 5 for scenario in workspace["scenarios"])
    assert workspace["readiness"] == {
        "case_id": str(case.id),
        "ready": False,
        "scenario_count": 2,
        "complete_scenario_count": 0,
        "incomplete_scenario_ids": [scenario["id"] for scenario in workspace["scenarios"]],
    }

    baseline = workspace["scenarios"][0]
    assumption = baseline["assumptions"][0]
    updated = db_client.patch(
        f"/api/v1/cases/{case.id}/scenarios/{baseline['id']}/assumptions/{assumption['id']}",
        headers=headers(),
        json={"value": 0.04, "reason": "Reviewer adjusted the operating plan"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["scenario"]["assumptions"][0]["review_status"] == "draft"

    for scenario in workspace["scenarios"]:
        for item in scenario["assumptions"]:
            response = db_client.post(
                f"/api/v1/cases/{case.id}/scenarios/{scenario['id']}/assumptions/{item['id']}/review",
                headers=headers(),
                json={"reason": "Approved for calculation inputs"},
            )
            assert response.status_code == 200, response.text

    readiness = db_client.get(
        f"/api/v1/cases/{case.id}/scenarios/readiness", headers=headers()
    )
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    assert readiness.json()["complete_scenario_count"] == 2

    validation = db_client.get(
        f"/api/v1/cases/{case.id}/scenarios/{baseline['id']}/validation",
        headers=headers(),
    )
    assert validation.status_code == 200
    assert validation.json() == {
        "scenario_id": baseline["id"],
        "complete": True,
        "issue_count": 0,
        "issues": [],
    }


def test_custom_scenario_assumptions_copy_archive_and_provenance(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    baseline = initialize(db_client, case.id)["scenarios"][0]

    copied = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios/{baseline['id']}/copy",
        headers=headers(),
        json={"name": "Liquidity squeeze", "reason": "Create reviewer stress case"},
    )
    assert copied.status_code == 200, copied.text
    custom = copied.json()["scenario"]
    assert custom["scenario_type"] == "custom"
    assert custom["copied_from_scenario_id"] == baseline["id"]
    assert all(item["review_status"] == "draft" for item in custom["assumptions"])
    assert all(item["provenance"]["source"] == "scenario_copy" for item in custom["assumptions"])

    created = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios/{custom['id']}/assumptions",
        headers=headers(),
        json={
            "category": "other",
            "key": "minimum_cash_buffer",
            "label": "Minimum cash buffer",
            "value": 100000,
            "unit": "USD",
            "provenance": {"document": "Treasury policy"},
            "reason": "Add policy constraint",
        },
    )
    assert created.status_code == 200, created.text
    extra = next(
        item
        for item in created.json()["scenario"]["assumptions"]
        if item["key"] == "minimum_cash_buffer"
    )
    assert extra["provenance"] == {"source": "manual", "document": "Treasury policy"}

    archived = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios/{custom['id']}/archive",
        headers=headers(),
        json={"reason": "Superseded by approved plan"},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["scenario"]["archived_at"] is not None

    active = db_client.get(f"/api/v1/cases/{case.id}/scenarios", headers=headers()).json()
    assert custom["id"] not in {scenario["id"] for scenario in active["scenarios"]}
    with_archived = db_client.get(
        f"/api/v1/cases/{case.id}/scenarios?include_archived=true", headers=headers()
    ).json()
    assert custom["id"] in {scenario["id"] for scenario in with_archived["scenarios"]}

    with get_sessionmaker()() as session:
        assert session.scalar(
            select(ScenarioAssumptionHistory).where(
                ScenarioAssumptionHistory.assumption_id == UUID(extra["id"]),
                ScenarioAssumptionHistory.action == "created",
            )
        )
        assert session.scalar(
            select(AuditEvent).where(
                AuditEvent.entity_id == UUID(custom["id"]),
                AuditEvent.event_type == "scenario.archived",
            )
        )


def test_custom_scenario_validation_reports_missing_categories(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    created = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios",
        headers=headers(),
        json={"name": "Custom plan", "reason": "Build from scratch"},
    )
    assert created.status_code == 200, created.text
    validation = created.json()["validation"]
    assert validation["complete"] is False
    assert {issue["category"] for issue in validation["issues"]} == {
        "growth",
        "expenses",
        "cash_flow_timing",
        "credit_usage",
        "repayment_behavior",
    }


def test_scenario_contracts_are_closed_and_initialization_is_idempotent(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    first = initialize(db_client, case.id)
    second = initialize(db_client, case.id)
    assert [scenario["id"] for scenario in second["scenarios"]] == [
        scenario["id"] for scenario in first["scenarios"]
    ]

    rejected = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios",
        headers=headers(),
        json={"name": "Invalid", "reason": "test", "unexpected": True},
    )
    assert rejected.status_code == 422


def test_scenario_endpoints_enforce_tenant_isolation(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = initialize(db_client, case.id)["scenarios"][0]

    assert (
        db_client.get(f"/api/v1/cases/{case.id}/scenarios", headers=headers(ORG_2)).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/cases/{case.id}/scenarios/{scenario['id']}",
            headers=headers(ORG_2),
        ).status_code
        == 404
    )
    assert (
        db_client.patch(
            f"/api/v1/cases/{case.id}/scenarios/{scenario['id']}",
            headers=headers(ORG_2),
            json={"name": "Cross tenant", "reason": "must fail"},
        ).status_code
        == 404
    )
