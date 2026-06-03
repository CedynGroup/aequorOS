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
    assert "/api/v1/cases/{case_id}/report" in paths
    assert "/api/v1/cases/{case_id}/findings" in paths
    assert "/api/v1/cases/{case_id}/financial-workspace" in paths

    case_list_operation = paths["/api/v1/cases"]["get"]
    case_list_ref = paths["/api/v1/cases"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    report_ref = paths["/api/v1/cases/{case_id}/report"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    finding_create_ref = paths["/api/v1/cases/{case_id}/findings"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]["$ref"]
    financial_workspace_ref = paths["/api/v1/cases/{case_id}/financial-workspace"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]["$ref"]

    assert case_list_ref == "#/components/schemas/CaseListRead"
    assert report_ref == "#/components/schemas/RiskReportPayload"
    assert finding_create_ref == "#/components/schemas/FindingCreate"
    assert financial_workspace_ref == "#/components/schemas/FinancialDataWorkspaceRead"
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

    report = paths["/api/v1/cases/{case_id}/report"]["get"]["responses"]["200"]
    assert "text/html" in report["content"]

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
    assert {
        "institutions",
        "accounts",
        "reporting_periods",
        "balances",
        "obligations",
        "source_rows",
        "record_source_links",
        "manual_edits",
        "validation_issues",
    } <= set(components["FinancialDataWorkspaceRead"]["required"])
    assert "metadata" in components["CaseRead"]["properties"]
    assert "metadata" in components["FinancialInstitutionRead"]["properties"]
    assert "metadata_" not in components["FinancialInstitutionRead"]["properties"]
    assert "metadata_" not in components["CaseRead"]["properties"]
    assert set(components["FindingUpdate"]["properties"]) == {"status", "disposition_reason"}


def test_case_api_preferred_aliases_are_in_openapi(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    assert "/api/v1/cases/{case_id}/decisions" in paths
    assert "/api/v1/cases/bulk-actions" in paths
    assert "/api/v1/cases/{case_id}/report" in paths
    assert "/api/v1/cases/{case_id}/financial-workspace" in paths
    assert "/api/v1/taxonomies/cases" in paths

    report_ref = paths["/api/v1/cases/{case_id}/report"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    financial_workspace_ref = paths["/api/v1/cases/{case_id}/financial-workspace"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]["$ref"]
    bulk_action_request_schema = paths["/api/v1/cases/bulk-actions"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    bulk_action_response_ref = paths["/api/v1/cases/bulk-actions"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]["$ref"]
    report_response = paths["/api/v1/cases/{case_id}/report"]["get"]["responses"]["200"]

    assert report_ref == "#/components/schemas/RiskReportPayload"
    assert financial_workspace_ref == "#/components/schemas/FinancialDataWorkspaceRead"
    assert bulk_action_request_schema["discriminator"]["propertyName"] == "action"
    assert {option["$ref"] for option in bulk_action_request_schema["oneOf"]} == {
        "#/components/schemas/CaseBulkAssignCreate",
        "#/components/schemas/CaseBulkUnassignCreate",
        "#/components/schemas/CaseBulkArchiveCreate",
        "#/components/schemas/CaseBulkUpdateStatusCreate",
    }
    components = response.json()["components"]["schemas"]
    for schema_name in (
        "CaseBulkAssignCreate",
        "CaseBulkUnassignCreate",
        "CaseBulkArchiveCreate",
        "CaseBulkUpdateStatusCreate",
    ):
        assert components[schema_name]["additionalProperties"] is False
    assert bulk_action_response_ref == "#/components/schemas/CaseBulkActionRead"
    assert "application/json" in report_response["content"]
    assert "text/html" in report_response["content"]
    assert paths["/api/v1/cases/{case_id}/decisions"]["post"]["operationId"] == (
        "createCaseDecision"
    )
    assert "/api/v1/cases/{case_id}/decision" not in paths
    assert "/api/v1/cases/{case_id}/report.json" not in paths
    assert "/api/v1/cases/{case_id}/report.html" not in paths
    assert "/api/v1/cases/{case_id}/financial-data" not in paths
    assert "/api/v1/cases/taxonomy" not in paths
