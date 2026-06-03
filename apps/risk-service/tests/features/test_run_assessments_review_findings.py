from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.factories import AssessmentFactory, CaseFactory, DocumentFactory
from tests.api.helpers import ORG_2, headers


def test_assessments_runs_findings_evidence_and_jobs(db_client: TestClient, fake_storage) -> None:
    case_id = str(CaseFactory(db_client).create().id)
    DocumentFactory(db_client, fake_storage).create_parsed(case_id=case_id)
    assessment_id = str(AssessmentFactory(db_client).create(case_id=case_id).id)

    response = db_client.post(f"/api/v1/assessments/{assessment_id}/run", headers=headers())
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    job_id = response.json()["job_id"]

    response = db_client.get(f"/api/v1/assessment-runs/{run_id}", headers=headers())
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    response = db_client.get(f"/api/v1/jobs/{job_id}", headers=headers())
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    response = db_client.get(f"/api/v1/cases/{case_id}/findings", headers=headers())
    assert response.status_code == 200
    finding_id = response.json()[0]["id"]

    response = db_client.patch(
        f"/api/v1/findings/{finding_id}",
        headers=headers(),
        json={"status": "dismissed"},
    )
    assert response.status_code == 400

    response = db_client.patch(
        f"/api/v1/findings/{finding_id}",
        headers=headers(),
        json={"status": "accepted", "disposition_reason": "Confirmed"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"

    response = db_client.patch(
        f"/api/v1/findings/{finding_id}",
        headers=headers(),
        json={"title": "Nope"},
    )
    assert response.status_code == 400

    response = db_client.get(f"/api/v1/findings/{finding_id}/evidence", headers=headers())
    assert response.status_code == 200
    assert response.json() == []

    response = db_client.get(f"/api/v1/findings/{finding_id}", headers=headers(ORG_2))
    assert response.status_code == 404


def test_reviewers_can_create_manual_findings(db_client: TestClient) -> None:
    case_id = str(CaseFactory(db_client).create().id)

    response = db_client.post(
        f"/api/v1/cases/{case_id}/findings",
        headers=headers(),
        json={
            "risk_type": "documentation_gap",
            "title": "Missing insurance addendum",
            "summary": "Reviewer could not locate the current insurance addendum.",
            "rationale": "The case file needs current support before approval.",
            "severity": "medium",
            "details": {"document": "insurance_addendum"},
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["source"] == "manual"
    assert response.json()["status"] == "open"
    assert response.json()["rule_id"] is None
    assert response.json()["details"] == {"document": "insurance_addendum"}

    response = db_client.get(f"/api/v1/cases/{case_id}/findings", headers=headers())
    assert response.status_code == 200
    assert response.json()[0]["title"] == "Missing insurance addendum"


def test_manual_finding_creation_validates_case_and_taxonomy(db_client: TestClient) -> None:
    case_id = str(CaseFactory(db_client).create().id)

    response = db_client.post(
        f"/api/v1/cases/{case_id}/findings",
        headers=headers(),
        json={
            "risk_type": "not_real",
            "title": "Invalid",
            "summary": "Invalid",
            "severity": "medium",
        },
    )
    assert response.status_code == 400

    response = db_client.post(f"/api/v1/cases/{case_id}/archive", headers=headers())
    assert response.status_code == 200
    response = db_client.post(
        f"/api/v1/cases/{case_id}/findings",
        headers=headers(),
        json={
            "risk_type": "documentation_gap",
            "title": "Archived",
            "summary": "Archived",
            "severity": "medium",
        },
    )
    assert response.status_code == 409


def test_cross_org_assessment_and_job_access_is_rejected(
    db_client: TestClient,
    fake_storage,
) -> None:
    case_id = str(CaseFactory(db_client).create().id)
    response = db_client.post(
        "/api/v1/assessments",
        headers=headers(ORG_2),
        json={"case_id": case_id, "assessment_type": "vendor_risk", "name": "Blocked"},
    )
    assert response.status_code == 404

    document_id = str(
        DocumentFactory(db_client, fake_storage).create_uploaded(case_id=case_id).document_id
    )
    parse_response = db_client.post(f"/api/v1/documents/{document_id}/parse", headers=headers())
    job_id = parse_response.json()["job_id"]

    response = db_client.get(f"/api/v1/jobs/{job_id}", headers=headers(ORG_2))
    assert response.status_code == 404
