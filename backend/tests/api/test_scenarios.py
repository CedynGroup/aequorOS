from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.models import AuditEvent, RiskScenario, ScenarioAssumption, ScenarioAssumptionHistory
from app.schemas.scenarios import AssumptionUpdate
from app.services.scenarios import update_assumption
from tests.api.factories import CaseFactory
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers


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
    updated_assumption = updated.json()["scenario"]["assumptions"][0]
    assert updated_assumption["review_status"] == "draft"
    assert updated_assumption["provenance"] == {
        "source": "reviewer_edit",
        "scenario_type": "baseline",
    }

    for scenario in workspace["scenarios"]:
        for item in scenario["assumptions"]:
            response = db_client.post(
                f"/api/v1/cases/{case.id}/scenarios/{scenario['id']}/assumptions/{item['id']}/review",
                headers=headers(),
                json={"reason": "Approved for calculation inputs"},
            )
            assert response.status_code == 200, response.text

    readiness = db_client.get(f"/api/v1/cases/{case.id}/scenarios/readiness", headers=headers())
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

    forged = db_client.patch(
        f"/api/v1/cases/{case.id}/scenarios/{custom['id']}/assumptions/{extra['id']}",
        headers=headers(),
        json={
            "provenance": {"source": "system_default", "document": "Updated policy"},
            "reason": "Update supporting evidence",
        },
    )
    assert forged.status_code == 200, forged.text
    forged_extra = next(
        item for item in forged.json()["scenario"]["assumptions"] if item["id"] == extra["id"]
    )
    assert forged_extra["provenance"] == {
        "source": "reviewer_edit",
        "document": "Updated policy",
    }

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

    for payload in (
        {"name": "   ", "reason": "test"},
        {"name": None, "reason": "test"},
    ):
        response = db_client.patch(
            f"/api/v1/cases/{case.id}/scenarios/{first['scenarios'][0]['id']}",
            headers=headers(),
            json=payload,
        )
        assert response.status_code == 422

    blank_create = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios",
        headers=headers(),
        json={"name": "   ", "reason": "test"},
    )
    assert blank_create.status_code == 422

    blank_copy = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios/{first['scenarios'][0]['id']}/copy",
        headers=headers(),
        json={"name": "   ", "reason": "test"},
    )
    assert blank_copy.status_code == 422

    blank_label = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios/{first['scenarios'][0]['id']}/assumptions",
        headers=headers(),
        json={
            "category": "other",
            "key": "blank_label",
            "label": "   ",
            "value": "value",
            "reason": "test",
        },
    )
    assert blank_label.status_code == 422


def test_duplicate_assumption_returns_conflict_and_rolls_back(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    scenario = initialize(db_client, case.id)["scenarios"][0]
    existing = scenario["assumptions"][0]

    duplicate = db_client.post(
        f"/api/v1/cases/{case.id}/scenarios/{scenario['id']}/assumptions",
        headers=headers(),
        json={
            "category": "other",
            "key": existing["key"],
            "label": "Duplicate",
            "value": "001",
            "provenance": {"source": "forged"},
            "reason": "Exercise duplicate validation",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["message"] == (
        "An assumption with this key already exists in the scenario."
    )

    workspace = db_client.get(f"/api/v1/cases/{case.id}/scenarios", headers=headers()).json()
    current = next(item for item in workspace["scenarios"] if item["id"] == scenario["id"])
    assert len(current["assumptions"]) == len(scenario["assumptions"])


def test_concurrent_review_and_edit_serialize_on_the_assumption(db_client: TestClient) -> None:
    sessionmaker = get_sessionmaker()
    with sessionmaker() as dialect_session:
        if dialect_session.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL row locks are required for concurrency coverage.")

    case = CaseFactory(db_client).create()
    scenario = initialize(db_client, case.id)["scenarios"][0]
    assumption_id = UUID(scenario["assumptions"][0]["id"])

    with sessionmaker() as review_session:
        assumption = review_session.scalar(
            select(ScenarioAssumption)
            .where(ScenarioAssumption.id == assumption_id)
            .with_for_update()
        )
        assert assumption is not None
        assumption.review_status = "reviewed"
        assumption.reviewed_by = USER_1
        assumption.reviewed_at = datetime.now(UTC)
        review_session.flush()

        def edit_assumption() -> str:
            with sessionmaker() as edit_session:
                result = update_assumption(
                    edit_session,
                    TenantContext(organization_id=ORG_1, actor_user_id=USER_1),
                    case.id,
                    UUID(scenario["id"]),
                    assumption_id,
                    AssumptionUpdate(value=0.07, reason="Concurrent reviewer edit"),
                )
                return result.scenario.assumptions[0].review_status

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(edit_assumption)
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.2)
            review_session.commit()
            assert future.result(timeout=5) == "draft"

    with sessionmaker() as verification_session:
        persisted = verification_session.get(ScenarioAssumption, assumption_id)
        assert persisted is not None
        assert persisted.review_status == "draft"
        assert persisted.reviewed_by is None
        assert persisted.reviewed_at is None
        assert persisted.provenance["source"] == "reviewer_edit"


def test_concurrent_archive_prevents_an_assumption_edit(db_client: TestClient) -> None:
    sessionmaker = get_sessionmaker()
    with sessionmaker() as dialect_session:
        if dialect_session.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL row locks are required for concurrency coverage.")

    case = CaseFactory(db_client).create()
    scenario = initialize(db_client, case.id)["scenarios"][0]
    scenario_id = UUID(scenario["id"])
    assumption_id = UUID(scenario["assumptions"][0]["id"])

    with sessionmaker() as archive_session:
        archived = archive_session.scalar(
            select(RiskScenario).where(RiskScenario.id == scenario_id).with_for_update()
        )
        assert archived is not None
        archived.archived_at = datetime.now(UTC)
        archive_session.flush()

        def edit_assumption() -> int:
            with sessionmaker() as edit_session:
                try:
                    update_assumption(
                        edit_session,
                        TenantContext(organization_id=ORG_1, actor_user_id=USER_1),
                        case.id,
                        scenario_id,
                        assumption_id,
                        AssumptionUpdate(value=0.09, reason="Late concurrent edit"),
                    )
                except HTTPException as exc:
                    return exc.status_code
                return 200

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(edit_assumption)
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.2)
            archive_session.commit()
            assert future.result(timeout=5) == 409


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
