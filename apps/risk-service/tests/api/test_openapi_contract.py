from __future__ import annotations

from fastapi.testclient import TestClient


def test_frontend_facing_case_contracts_are_named_and_present(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = schema["paths"]
    components = schema["components"]["schemas"]

    assert "/api/v1/cases" in paths
    assert "/api/v1/cases/{case_id}/scores" in paths
    assert "/api/v1/cases/{case_id}/report.json" in paths
    assert "/api/v1/cases/{case_id}/findings" in paths

    case_list_operation = paths["/api/v1/cases"]["get"]
    case_list_ref = paths["/api/v1/cases"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    report_ref = paths["/api/v1/cases/{case_id}/report.json"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    finding_create_ref = paths["/api/v1/cases/{case_id}/findings"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]["$ref"]

    assert case_list_ref == "#/components/schemas/CaseListRead"
    assert report_ref == "#/components/schemas/RiskReportPayload"
    assert finding_create_ref == "#/components/schemas/FindingCreate"
    assert case_list_operation["operationId"] == "listCases"

    case_parameters = {
        parameter["name"]: parameter for parameter in case_list_operation["parameters"]
    }
    assert case_parameters["X-Org-Id"]["required"] is True
    assert case_parameters["sort"]["schema"]["$ref"] == "#/components/schemas/CaseSort"
    assert case_parameters["limit"]["schema"]["minimum"] == 1
    assert case_parameters["limit"]["schema"]["maximum"] == 200
    assert case_parameters["offset"]["schema"]["minimum"] == 0

    error_ref = case_list_operation["responses"]["422"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    assert error_ref == "#/components/schemas/ErrorResponse"

    report_html = paths["/api/v1/cases/{case_id}/report.html"]["get"]["responses"]["200"]
    assert "text/html" in report_html["content"]

    assert {
        "items",
        "total",
        "limit",
        "offset",
        "page",
        "pages",
        "has_more",
    } <= set(components["CaseListRead"]["required"])
    assert {"case", "findings", "decisions", "assessments", "scores"} <= set(
        components["RiskReportPayload"]["required"]
    )
    assert "metadata" in components["CaseRead"]["properties"]
    assert "metadata_" not in components["CaseRead"]["properties"]
    assert set(components["FindingUpdate"]["properties"]) == {"status", "disposition_reason"}
