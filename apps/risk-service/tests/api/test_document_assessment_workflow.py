from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.factories import AssessmentFactory, CaseFactory, DocumentFactory
from tests.api.helpers import ORG_1, ORG_2, headers


def test_phase_1_happy_path_e2e(db_client: TestClient, fake_storage) -> None:  # noqa: PLR0915
    case_id = str(
        CaseFactory(db_client)
        .create(
            title="E2E vendor case",
            subject_type="company",
            subject_name="Acme Vendor",
        )
        .id
    )
    documents = DocumentFactory(db_client, fake_storage)

    upload_body = documents.request_upload(case_id=case_id)
    document_id = str(upload_body.document_id)
    assert upload_body.method == "PUT"
    assert f"orgs/{ORG_1}/documents/{document_id}/original" in upload_body.upload_url

    documents.complete_upload(document_id=str(document_id))

    documents = db_client.get(f"/api/v1/cases/{case_id}/documents", headers=headers())
    assert documents.status_code == 200, documents.text
    assert documents.json()[0]["status"] == "uploaded"

    parse = db_client.post(f"/api/v1/documents/{document_id}/parse", headers=headers())
    assert parse.status_code == 200, parse.text
    parse_job_id = parse.json()["job_id"]
    assert parse.json()["status"] == "completed"

    parse_job = db_client.get(f"/api/v1/jobs/{parse_job_id}", headers=headers())
    assert parse_job.status_code == 200, parse_job.text
    assert parse_job.json()["job_type"] == "document_parse"
    assert parse_job.json()["status"] == "completed"

    parse_status = db_client.get(f"/api/v1/documents/{document_id}/parse-status", headers=headers())
    assert parse_status.status_code == 200, parse_status.text
    assert parse_status.json()["parse_status"] == "parsed"

    assessment = AssessmentFactory(db_client).create(
        case_id=case_id,
        name="Initial vendor assessment",
    )
    assessment_id = str(assessment.id)
    assert assessment.input_snapshot["document_ids"] == [document_id]

    run = db_client.post(f"/api/v1/assessments/{assessment_id}/run", headers=headers())
    assert run.status_code == 200, run.text
    run_body = run.json()
    assert run_body["status"] == "completed"

    assessment_run = db_client.get(
        f"/api/v1/assessment-runs/{run_body['run_id']}", headers=headers()
    )
    assert assessment_run.status_code == 200, assessment_run.text
    assert assessment_run.json()["summary"]["findings_created"] == 1
    assert assessment_run.json()["summary"]["risk_score"] == 20

    assessment_job = db_client.get(f"/api/v1/jobs/{run_body['job_id']}", headers=headers())
    assert assessment_job.status_code == 200, assessment_job.text
    assert assessment_job.json()["job_type"] == "assessment_run"
    assert assessment_job.json()["progress"]["findings_created"] == 1

    findings = db_client.get(f"/api/v1/cases/{case_id}/findings", headers=headers())
    assert findings.status_code == 200, findings.text
    assert len(findings.json()) == 1
    finding = findings.json()[0]
    assert finding["risk_type"] == "documentation_gap"
    assert finding["rule_id"] == "missing_structured_data"
    assert finding["assessment_id"] == assessment_id
    assert finding["run_id"] == run_body["run_id"]

    assert db_client.get(f"/api/v1/cases/{case_id}", headers=headers(ORG_2)).status_code == 404
    assert (
        db_client.get(f"/api/v1/documents/{document_id}", headers=headers(ORG_2)).status_code == 404
    )
    assert (
        db_client.get(f"/api/v1/findings/{finding['id']}", headers=headers(ORG_2)).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/jobs/{run_body['job_id']}", headers=headers(ORG_2)).status_code
        == 404
    )


def test_assessment_run_references_are_unique_and_console_visible(
    db_client: TestClient,
) -> None:
    case_id = str(CaseFactory(db_client).create().id)
    assessment_factory = AssessmentFactory(db_client)
    first_assessment = assessment_factory.create(
        case_id=case_id,
        name="Credit review",
    )
    second_assessment = assessment_factory.create(
        case_id=case_id,
        name="Credit review",
    )

    run_ids = [
        db_client.post(
            f"/api/v1/assessments/{assessment.id}/run",
            headers=headers(),
        ).json()["run_id"]
        for assessment in (first_assessment, second_assessment)
    ]
    run_details = [
        db_client.get(f"/api/v1/assessment-runs/{run_id}", headers=headers()).json()
        for run_id in run_ids
    ]

    assert {detail["reference"].rsplit(" run ", 1)[1] for detail in run_details} == {
        "1",
        "2",
    }
    assert all(
        detail["reference"].startswith("Credit review ") for detail in run_details
    )
    first_runs = db_client.get(
        f"/api/v1/assessments/{first_assessment.id}/runs",
        headers=headers(),
    ).json()
    assert first_runs[0]["reference"] == run_details[0]["reference"]
