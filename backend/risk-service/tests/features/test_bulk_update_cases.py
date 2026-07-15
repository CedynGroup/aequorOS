from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from tests.api.factories import CaseFactory
from tests.api.helpers import ORG_2, USER_1, USER_2, headers


def test_bulk_case_actions_assign_with_partial_failures(db_client: TestClient) -> None:
    cases = CaseFactory(db_client)
    assignable_case_id = str(cases.create(title="Assignable").id)
    archived_case_id = str(cases.create(title="Archived").id)
    other_tenant_case_id = str(cases.create(org_id=ORG_2, title="Other tenant").id)

    archive = db_client.post(f"/api/v1/cases/{archived_case_id}/archive", headers=headers())
    assert archive.status_code == 200, archive.text

    response = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={
            "case_ids": [assignable_case_id, archived_case_id, other_tenant_case_id],
            "action": "assign",
            "assigned_to_user_id": str(USER_1),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["case_id"] for item in body["succeeded"]] == [assignable_case_id]
    assert body["succeeded"][0]["case"]["assigned_to_user_id"] == str(USER_1)
    assert body["failed"] == [
        {
            "case_id": archived_case_id,
            "status_code": 409,
            "error": {
                "code": "conflict",
                "message": "Archived cases cannot be modified through this workflow.",
            },
        },
        {
            "case_id": other_tenant_case_id,
            "status_code": 404,
            "error": {"code": "not_found", "message": "Case not found."},
        },
    ]

    assigned = db_client.get(f"/api/v1/cases/{assignable_case_id}", headers=headers())
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assigned_to_user_id"] == str(USER_1)


def test_bulk_case_actions_invalid_assignee_is_per_item_failure(
    db_client: TestClient,
) -> None:
    case_id = str(CaseFactory(db_client).create().id)

    response = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={
            "case_ids": [case_id],
            "action": "assign",
            "assigned_to_user_id": str(USER_2),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["succeeded"] == []
    assert response.json()["failed"] == [
        {
            "case_id": case_id,
            "status_code": 404,
            "error": {"code": "not_found", "message": "Assignee not found."},
        }
    ]


def test_bulk_case_actions_update_status_unassign_and_archive(db_client: TestClient) -> None:
    cases = CaseFactory(db_client)
    case_id = str(cases.create(status="draft").id)

    assign = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={
            "case_ids": [case_id],
            "action": "assign",
            "assigned_to_user_id": str(USER_1),
        },
    )
    assert assign.status_code == 200, assign.text
    assert assign.json()["succeeded"][0]["case"]["assigned_to_user_id"] == str(USER_1)

    status_update = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={
            "case_ids": [case_id],
            "action": "update_status",
            "status": "in_review",
        },
    )
    assert status_update.status_code == 200, status_update.text
    assert status_update.json()["succeeded"][0]["status"] == "in_review"

    unassign = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={"case_ids": [case_id], "action": "unassign"},
    )
    assert unassign.status_code == 200, unassign.text
    assert unassign.json()["succeeded"][0]["case"]["assigned_to_user_id"] is None

    archive = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={"case_ids": [case_id], "action": "archive"},
    )
    assert archive.status_code == 200, archive.text
    assert archive.json()["succeeded"][0]["status"] == "archived"


def test_bulk_case_actions_status_conflicts_are_per_item_failures(
    db_client: TestClient,
) -> None:
    cases = CaseFactory(db_client)
    draft_case_id = str(cases.create(status="draft").id)
    active_case_id = str(cases.create(status="active").id)

    response = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={
            "case_ids": [draft_case_id, active_case_id],
            "action": "update_status",
            "status": "draft",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["case_id"] for item in body["succeeded"]] == [draft_case_id]
    assert body["failed"] == [
        {
            "case_id": active_case_id,
            "status_code": 409,
            "error": {
                "code": "conflict",
                "message": "Invalid case status transition from active to draft.",
            },
        }
    ]

    response = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={
            "case_ids": [draft_case_id],
            "action": "update_status",
            "status": "completed",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["succeeded"] == []
    assert response.json()["failed"] == [
        {
            "case_id": draft_case_id,
            "status_code": 409,
            "error": {
                "code": "conflict",
                "message": "Cases must be completed through the decision workflow.",
            },
        }
    ]


def test_bulk_case_actions_validate_payload(db_client: TestClient) -> None:
    case_id = str(CaseFactory(db_client).create().id)

    empty_case_ids = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={"case_ids": [], "action": "archive"},
    )
    assert empty_case_ids.status_code == 422

    too_many_case_ids = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={"case_ids": [str(uuid4()) for _ in range(101)], "action": "archive"},
    )
    assert too_many_case_ids.status_code == 422

    invalid_action = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={"case_ids": [case_id], "action": "delete"},
    )
    assert invalid_action.status_code == 422

    missing_parameters = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={"case_ids": [case_id], "action": "assign"},
    )
    assert missing_parameters.status_code == 422

    duplicate_ids = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={"case_ids": [case_id, case_id], "action": "archive"},
    )
    assert duplicate_ids.status_code == 422

    extra_archive_field = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={
            "case_ids": [case_id],
            "action": "archive",
            "assigned_to_user_id": str(USER_1),
        },
    )
    assert extra_archive_field.status_code == 422

    extra_unassign_field = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={"case_ids": [case_id], "action": "unassign", "status": "in_review"},
    )
    assert extra_unassign_field.status_code == 422

    extra_assign_field = db_client.post(
        "/api/v1/cases/bulk-actions",
        headers=headers(),
        json={
            "case_ids": [case_id],
            "action": "assign",
            "assigned_to_user_id": str(USER_1),
            "status": "in_review",
        },
    )
    assert extra_assign_field.status_code == 422
