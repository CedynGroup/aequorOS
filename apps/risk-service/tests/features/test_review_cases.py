from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.factories import CaseFactory
from tests.api.helpers import ORG_2, USER_1, USER_2, headers


def score_case(client: TestClient, case_id: str) -> dict:
    assessment = client.post(
        "/api/v1/assessments",
        headers=headers(),
        json={"case_id": case_id, "assessment_type": "vendor_risk", "name": "Score"},
    )
    assert assessment.status_code == 201, assessment.text
    run = client.post(
        f"/api/v1/assessments/{assessment.json()['id']}/run",
        headers=headers(),
    )
    assert run.status_code == 200, run.text
    return run.json()


def acknowledge_open_findings(client: TestClient, case_id: str) -> dict[str, dict]:
    response = client.get(f"/api/v1/cases/{case_id}/findings", headers=headers())
    assert response.status_code == 200, response.text
    reviews = {}
    for finding in [item for item in response.json() if item["status"] == "open"]:
        review = client.patch(
            f"/api/v1/findings/{finding['id']}",
            headers=headers(),
            json={"status": "acknowledged"},
        )
        assert review.status_code == 200, review.text
        reviews[finding["id"]] = review.json()
    return reviews


def test_cases_are_org_scoped_and_archivable(db_client: TestClient) -> None:
    cases = CaseFactory(db_client)
    case_id = str(cases.create().id)
    other_case_id = str(cases.create(org_id=ORG_2).id)

    response = db_client.get("/api/v1/cases", headers=headers())
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [case["id"] for case in response.json()["items"]] == [case_id]

    response = db_client.get(f"/api/v1/cases/{other_case_id}", headers=headers())
    assert response.status_code == 404

    response = db_client.post(f"/api/v1/cases/{case_id}/archive", headers=headers())
    assert response.status_code == 200
    assert response.json()["status"] == "archived"


def test_case_queue_filters_assignment_summary_and_reports(db_client: TestClient) -> None:
    cases = CaseFactory(db_client)
    low_case_id = str(
        cases.create(
            title="Low risk vendor",
            metadata={"structured_data": {"vendor_criticality": "low"}},
        ).id
    )
    high_case_id = str(
        cases.create(
            title="Critical vendor",
            subject_name="Acme Payments",
            metadata={
                "structured_data": {
                    "vendor_criticality": "critical",
                    "debt_to_ebitda": 6.5,
                    "cash_runway_months": 2,
                    "required_documents": ["soc2", "financials"],
                    "provided_documents": ["financials"],
                }
            },
        ).id
    )

    response = db_client.post(
        f"/api/v1/cases/{high_case_id}/assign",
        headers=headers(),
        json={"assigned_to_user_id": str(USER_1)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["assigned_to_user_id"] == str(USER_1)

    response = db_client.post(
        f"/api/v1/cases/{low_case_id}/assign",
        headers=headers(),
        json={"assigned_to_user_id": str(USER_2)},
    )
    assert response.status_code == 404

    response = db_client.get(
        f"/api/v1/cases?assigned_to_user_id={USER_1}&q=payments",
        headers=headers(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [case["id"] for case in payload["items"]] == [high_case_id]
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["pages"] == 1
    assert payload["has_more"] is False
    assert payload["items"][0]["assignee_display_name"] == "Demo User One"
    assert payload["items"][0]["assignee_email"] == "demo.user.one@example.test"

    response = db_client.get("/api/v1/cases?sort=invalid", headers=headers())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"

    response = db_client.get("/api/v1/taxonomies/cases", headers=headers())
    assert response.status_code == 200
    assert "created_at_desc" in response.json()["sort_options"]
    assert "completed" in response.json()["statuses"]

    summary = db_client.get("/api/v1/cases/summary", headers=headers())
    assert summary.status_code == 200
    assert summary.json()["total_cases"] == 2
    assert summary.json()["archived_cases"] == 0
    assert summary.json()["unassigned_cases"] == 1
    assert summary.json()["by_assignee"][str(USER_1)] == 1


def test_case_decision_history_and_completed_report(db_client: TestClient) -> None:
    case_id = str(
        CaseFactory(db_client)
        .create(
            metadata={"structured_data": {"vendor_criticality": "critical"}},
        )
        .id
    )
    score_case(db_client, case_id)
    finding = db_client.post(
        f"/api/v1/cases/{case_id}/findings",
        headers=headers(),
        json={
            "risk_type": "documentation_gap",
            "title": "Missing insurance addendum",
            "summary": "Reviewer could not locate the current insurance addendum.",
            "severity": "medium",
            "details": {
                "document": "insurance_addendum",
                "input_hash": "a" * 64,
                "source_case_id": case_id,
            },
        },
    )
    assert finding.status_code == 201, finding.text
    reviews = acknowledge_open_findings(db_client, case_id)
    review = reviews[finding.json()["id"]]

    response = db_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        headers=headers(),
        json={"decision": "approved", "reason": "All findings reviewed"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "approved"

    response = db_client.get(f"/api/v1/cases/{case_id}", headers=headers())
    assert response.json()["status"] == "completed"
    assert response.json()["decision"] == "approved"

    response = db_client.get(f"/api/v1/cases/{case_id}/decisions", headers=headers())
    assert response.status_code == 200
    assert response.json()[0]["reason"] == "All findings reviewed"
    assert response.json()[0]["decided_by_display_name"] == "Demo User One"

    response = db_client.get("/api/v1/cases?decision=approved", headers=headers())
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == case_id

    response = db_client.get(f"/api/v1/cases/{case_id}/report", headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["case"]["decision"] == "approved"
    assert response.json()["scores"][0]["score"] == 45
    assert response.json()["decisions"][0]["decided_by"] == "Demo User One"
    assert response.json()["scores"][0]["assessment"] == "Score"
    assert len(response.json()["scores"][0]["input_hash"]) == 64
    assert response.json()["scores"][0]["rule_results"] == [
        {
            "details": {
                "thresholds": {"critical": 45, "high": 30},
                "vendor_criticality": "critical",
            },
            "risk_type": "operational_risk",
            "rule_id": "vendor_criticality",
            "score_impact": 45,
        }
    ]
    report_finding = next(
        item
        for item in response.json()["findings"]
        if item["title"] == "Missing insurance addendum"
    )
    assert report_finding["details"] == {
        "document": "insurance_addendum",
        "input_hash": "a" * 64,
        "reviewed_at": review["details"]["reviewed_at"],
        "reviewed_by": "[internal identifier redacted]",
        "source_case_id": "[internal identifier redacted]",
    }
    assert case_id not in response.text
    assert str(USER_1) not in response.text

    response = db_client.get(
        f"/api/v1/cases/{case_id}/report",
        headers={**headers(), "Accept": "text/html"},
    )
    assert response.status_code == 200, response.text
    assert "text/html" in response.headers["content-type"]
    assert "Risk review report" in response.text

    response = db_client.get(
        f"/api/v1/cases/{case_id}/report",
        headers={**headers(), "Accept": "text/html;q=1, application/json;q=0.1"},
    )
    assert response.status_code == 200, response.text
    assert "text/html" in response.headers["content-type"]
    assert "Risk review report" in response.text

    response = db_client.get(
        f"/api/v1/cases/{case_id}/report",
        headers={**headers(), "Accept": "text/html;q=0.1, application/json;q=1"},
    )
    assert response.status_code == 200, response.text
    assert "application/json" in response.headers["content-type"]
    assert response.json()["case"]["decision"] == "approved"

    response = db_client.get(
        f"/api/v1/cases/{case_id}/report",
        headers={**headers(), "Accept": "text/html;Q=0, application/json;q=1"},
    )
    assert response.status_code == 200, response.text
    assert "application/json" in response.headers["content-type"]
    assert response.json()["case"]["decision"] == "approved"

    response = db_client.get(f"/api/v1/cases/{case_id}/report", headers=headers(ORG_2))
    assert response.status_code == 404


def test_completed_status_requires_decision_workflow(db_client: TestClient) -> None:
    case_id = str(CaseFactory(db_client).create().id)

    response = db_client.patch(
        f"/api/v1/cases/{case_id}",
        headers=headers(),
        json={"status": "in_review"},
    )
    assert response.status_code == 200

    response = db_client.patch(
        f"/api/v1/cases/{case_id}",
        headers=headers(),
        json={"status": "draft"},
    )
    assert response.status_code == 409

    response = db_client.patch(
        f"/api/v1/cases/{case_id}",
        headers=headers(),
        json={"status": "completed"},
    )
    assert response.status_code == 409

    response = db_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        headers=headers(),
        json={"decision": "needs_more_info", "reason": "Waiting on support"},
    )
    assert response.status_code == 200

    response = db_client.patch(
        f"/api/v1/cases/{case_id}",
        headers=headers(),
        json={"status": "completed"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "Cases must be completed through the decision workflow."
    )


def test_final_decision_requires_scoring(db_client: TestClient) -> None:
    case_id = str(CaseFactory(db_client).create().id)

    response = db_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        headers=headers(),
        json={"decision": "approved", "reason": "Not scored"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "Case must be scored before a final decision."


def test_completed_case_can_be_redecided_and_reopened_for_more_information(
    db_client: TestClient,
) -> None:
    case_id = str(
        CaseFactory(db_client)
        .create(
            metadata={"structured_data": {"vendor_criticality": "low"}},
        )
        .id
    )
    score_case(db_client, case_id)
    first = db_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        headers=headers(),
        json={"decision": "approved", "reason": "Initial approval"},
    )
    assert first.status_code == 200, first.text

    second = db_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        headers=headers(),
        json={"decision": "needs_more_info", "reason": "New support requested"},
    )

    assert second.status_code == 200, second.text
    assert second.json()["previous_decision"] == "approved"
    case_response = db_client.get(f"/api/v1/cases/{case_id}", headers=headers())
    assert case_response.status_code == 200
    assert case_response.json()["status"] == "in_review"
    assert case_response.json()["decision"] == "needs_more_info"
    decisions = db_client.get(f"/api/v1/cases/{case_id}/decisions", headers=headers())
    assert decisions.status_code == 200
    assert [decision["decision"] for decision in decisions.json()] == [
        "needs_more_info",
        "approved",
    ]


def test_assignment_can_be_cleared(db_client: TestClient) -> None:
    case_id = str(CaseFactory(db_client).create().id)
    assigned = db_client.post(
        f"/api/v1/cases/{case_id}/assign",
        headers=headers(),
        json={"assigned_to_user_id": str(USER_1)},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assigned_to_user_id"] == str(USER_1)
    assert assigned.json()["assigned_at"] is not None

    cleared = db_client.post(
        f"/api/v1/cases/{case_id}/assign",
        headers=headers(),
        json={"assigned_to_user_id": None},
    )

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["assigned_to_user_id"] is None
    assert cleared.json()["assigned_at"] is None


def test_manual_findings_block_final_decision_until_reviewed(db_client: TestClient) -> None:
    case_id = str(
        CaseFactory(db_client)
        .create(
            metadata={"structured_data": {"vendor_criticality": "low"}},
        )
        .id
    )
    score_case(db_client, case_id)
    finding = db_client.post(
        f"/api/v1/cases/{case_id}/findings",
        headers=headers(),
        json={
            "risk_type": "documentation_gap",
            "title": "Reviewer concern",
            "summary": "Reviewer added a manual concern.",
            "severity": "medium",
        },
    )
    assert finding.status_code == 201, finding.text

    response = db_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        headers=headers(),
        json={"decision": "approved", "reason": "Ready"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "Open findings must be acknowledged, dismissed, or resolved before completion."
    )

    response = db_client.patch(
        f"/api/v1/findings/{finding.json()['id']}",
        headers=headers(),
        json={"status": "dismissed", "disposition_reason": "Reviewed and accepted risk"},
    )
    assert response.status_code == 200, response.text

    response = db_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        headers=headers(),
        json={"decision": "approved", "reason": "Manual finding reviewed"},
    )
    assert response.status_code == 200, response.text


def test_case_queue_filters_sorting_and_pagination(db_client: TestClient) -> None:
    cases = CaseFactory(db_client)
    draft_case_id = str(cases.create(title="Alpha draft", status="draft").id)
    active_case_id = str(
        cases.create(
            title="Beta active",
            status="active",
            metadata={"structured_data": {"vendor_criticality": "critical"}},
        ).id
    )
    score_case(db_client, active_case_id)

    response = db_client.get(f"/api/v1/cases/{active_case_id}/scores", headers=headers())
    assert response.status_code == 200
    assert response.json()[0]["score"] == 45
    assert response.json()[0]["risk_level"] == "medium"
    assert response.json()[0]["input_snapshot"] == {
        "structured_data": {"vendor_criticality": "critical"}
    }

    response = db_client.get(f"/api/v1/cases/{active_case_id}/scores", headers=headers(ORG_2))
    assert response.status_code == 404

    response = db_client.get(f"/api/v1/cases/{active_case_id}/findings", headers=headers())
    assert response.status_code == 200
    assert response.json()[0]["source"] == "deterministic_rule"

    response = db_client.get("/api/v1/cases?status=draft", headers=headers())
    assert response.status_code == 200
    assert [case["id"] for case in response.json()["items"]] == [draft_case_id]

    response = db_client.get("/api/v1/cases?risk_level=medium", headers=headers())
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == active_case_id

    response = db_client.get("/api/v1/cases?sort=title_asc&limit=1&offset=1", headers=headers())
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["page"] == 2
    assert response.json()["pages"] == 2
    assert response.json()["has_more"] is False
    assert response.json()["items"][0]["title"] == "Beta active"


def test_case_queue_rejects_invalid_pagination_parameters(db_client: TestClient) -> None:
    CaseFactory(db_client).create()

    for query in ("limit=0", "limit=201", "offset=-1"):
        response = db_client.get(f"/api/v1/cases?{query}", headers=headers())
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_archived_filter_requires_include_archived(db_client: TestClient) -> None:
    case_id = str(CaseFactory(db_client).create().id)
    archive = db_client.post(f"/api/v1/cases/{case_id}/archive", headers=headers())
    assert archive.status_code == 200, archive.text

    excluded = db_client.get("/api/v1/cases?status=archived", headers=headers())
    assert excluded.status_code == 200
    assert excluded.json()["total"] == 0

    included = db_client.get(
        "/api/v1/cases?status=archived&include_archived=true",
        headers=headers(),
    )
    assert included.status_code == 200
    assert included.json()["total"] == 1
    assert included.json()["items"][0]["id"] == case_id


def test_archived_cases_reject_assignment_decisions_and_leave_summary_queue(
    db_client: TestClient,
) -> None:
    case_id = str(CaseFactory(db_client).create().id)
    response = db_client.post(f"/api/v1/cases/{case_id}/archive", headers=headers())
    assert response.status_code == 200, response.text

    response = db_client.patch(
        f"/api/v1/cases/{case_id}",
        headers=headers(),
        json={"title": "Should not update"},
    )
    assert response.status_code == 409

    response = db_client.post(
        f"/api/v1/cases/{case_id}/assign",
        headers=headers(),
        json={"assigned_to_user_id": str(USER_1)},
    )
    assert response.status_code == 409

    response = db_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        headers=headers(),
        json={"decision": "approved", "reason": "Archived case"},
    )
    assert response.status_code == 409

    summary = db_client.get("/api/v1/cases/summary", headers=headers())
    assert summary.status_code == 200
    assert summary.json()["total_cases"] == 0
    assert summary.json()["archived_cases"] == 1
    assert summary.json()["unassigned_cases"] == 0


def test_tenant_context_and_report_edges_use_error_envelope(db_client: TestClient) -> None:
    missing_tenant = db_client.get("/api/v1/cases")
    assert missing_tenant.status_code == 422
    assert missing_tenant.json()["error"]["code"] == "validation_error"

    invalid_tenant = db_client.get("/api/v1/cases", headers={"X-Org-Id": "not-a-uuid"})
    assert invalid_tenant.status_code == 401
    assert invalid_tenant.json()["error"]["code"] == "unauthorized"

    other_case_id = str(CaseFactory(db_client).create(org_id=ORG_2).id)
    report = db_client.get(f"/api/v1/cases/{other_case_id}/report", headers=headers())
    assert report.status_code == 404
    assert report.json()["error"]["code"] == "not_found"


def test_report_html_preserves_zero_risk_score(db_client: TestClient) -> None:
    case_id = str(
        CaseFactory(db_client)
        .create(
            metadata={"structured_data": {"vendor_criticality": "low"}},
        )
        .id
    )
    score_case(db_client, case_id)
    decision = db_client.post(
        f"/api/v1/cases/{case_id}/decisions",
        headers=headers(),
        json={"decision": "approved", "reason": "No deterministic findings"},
    )
    assert decision.status_code == 200, decision.text

    response = db_client.get(
        f"/api/v1/cases/{case_id}/report",
        headers={**headers(), "Accept": "text/html"},
    )

    assert response.status_code == 200, response.text
    assert '<div class="label">Risk score</div><div class="value">0</div>' in response.text
