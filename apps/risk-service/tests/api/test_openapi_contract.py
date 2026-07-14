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
    assert "/api/v1/cases/{case_id}/financial-workspace/map" in paths
    assert "/api/v1/cases/{case_id}/financial-data/validate" in paths
    assert "/api/v1/cases/{case_id}/financial-data/validation-issues" in paths

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
    financial_workspace_map_ref = paths["/api/v1/cases/{case_id}/financial-workspace/map"]["post"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]["$ref"]
    financial_validate_ref = paths["/api/v1/cases/{case_id}/financial-data/validate"]["post"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]["$ref"]

    assert case_list_ref == "#/components/schemas/CaseListRead"
    assert report_ref == "#/components/schemas/RiskReportPayload"
    assert finding_create_ref == "#/components/schemas/FindingCreate"
    assert financial_workspace_ref == "#/components/schemas/FinancialDataWorkspaceRead"
    assert financial_workspace_map_ref == "#/components/schemas/FinancialWorkspaceMapResponse"
    assert financial_validate_ref == "#/components/schemas/FinancialValidationRunResponse"
    assert case_list_operation["operationId"] == "listCases"

    case_parameters = {
        parameter["name"]: parameter for parameter in case_list_operation["parameters"]
    }
    assert case_parameters["X-Org-Id"]["required"] is True
    assert case_parameters["sort"]["schema"]["$ref"] == "#/components/schemas/CaseSort"
    assert case_parameters["limit"]["schema"]["minimum"] == 1
    assert case_parameters["limit"]["schema"]["maximum"] == 200
    assert case_parameters["offset"]["schema"]["minimum"] == 0

    finding_update_parameters = {
        parameter["name"]: parameter
        for parameter in paths["/api/v1/findings/{finding_id}"]["patch"]["parameters"]
    }
    assert finding_update_parameters["X-User-Id"]["required"] is True

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
        "cash_flows",
        "obligations",
        "covenants",
        "source_rows",
        "record_source_links",
        "manual_edits",
        "validation_issues",
        "validation_summary",
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
    assert "/api/v1/cases/{case_id}/financial-workspace/map" in paths
    assert "/api/v1/cases/{case_id}/financial-data/validate" in paths
    assert "/api/v1/cases/{case_id}/financial-data/validation-issues" in paths
    assert "/api/v1/cases/{case_id}/financial-workspace/cash-flows" in paths
    assert "/api/v1/cases/{case_id}/financial-workspace/cash-flows/{cash_flow_id}" in paths
    assert "/api/v1/taxonomies/cases" in paths

    report_ref = paths["/api/v1/cases/{case_id}/report"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    financial_workspace_ref = paths["/api/v1/cases/{case_id}/financial-workspace"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]["$ref"]
    financial_workspace_map = paths["/api/v1/cases/{case_id}/financial-workspace/map"]["post"]
    financial_validate = paths["/api/v1/cases/{case_id}/financial-data/validate"]["post"]
    financial_issues = paths["/api/v1/cases/{case_id}/financial-data/validation-issues"]["get"]
    bulk_action_request_schema = paths["/api/v1/cases/bulk-actions"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    bulk_action_response_ref = paths["/api/v1/cases/bulk-actions"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]["$ref"]
    report_response = paths["/api/v1/cases/{case_id}/report"]["get"]["responses"]["200"]

    assert report_ref == "#/components/schemas/RiskReportPayload"
    assert financial_workspace_ref == "#/components/schemas/FinancialDataWorkspaceRead"
    assert financial_workspace_map["operationId"] == "mapCaseFinancialWorkspace"
    assert financial_validate["operationId"] == "validateCaseFinancialData"
    assert financial_issues["operationId"] == "listCaseFinancialValidationIssues"
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
    assert "/api/v1/cases/taxonomy" not in paths


def test_canonical_mutation_contracts_use_resource_specific_allowlisted_paths(
    client: TestClient,
) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    components = schema["components"]["schemas"]
    resources = {
        "institutions": "FinancialInstitution",
        "accounts": "FinancialAccount",
        "reporting-periods": "FinancialReportingPeriod",
        "balances": "FinancialBalance",
        "cash-flows": "FinancialCashFlow",
        "obligations": "FinancialObligation",
        "covenants": "FinancialCovenant",
    }

    for resource, model_name in resources.items():
        collection_path = f"/api/v1/cases/{{case_id}}/financial-workspace/{resource}"
        identifier = resource.removesuffix("s").replace("-", "_") + "_id"
        if resource == "reporting-periods":
            identifier = "reporting_period_id"
        item_path = f"{collection_path}/{{{identifier}}}"
        assert (
            paths[collection_path]["post"]["requestBody"]["content"]["application/json"]["schema"][
                "$ref"
            ]
            == f"#/components/schemas/{model_name}Create"
        )
        parameters = {
            parameter["name"]: parameter
            for parameter in paths[collection_path]["post"]["parameters"]
        }
        assert parameters["X-User-Id"]["required"] is True
        response_ref = paths[item_path]["patch"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        assert response_ref == f"#/components/schemas/{model_name}MutationResponse"
        assert components[f"{model_name}Create"]["additionalProperties"] is False
        assert components[f"{model_name}Update"]["additionalProperties"] is False

    assert "/api/v1/cases/{case_id}/financial-data/{entity_type}/{entity_id}" not in paths


def test_scenario_contracts_are_case_scoped_closed_and_generated(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    components = schema["components"]["schemas"]
    base = "/api/v1/cases/{case_id}/scenarios"

    assert paths[base]["get"]["operationId"] == "listCaseScenarios"
    assert paths[f"{base}/initialize"]["post"]["operationId"] == "initializeCaseScenarios"
    assert paths[f"{base}/readiness"]["get"]["operationId"] == ("getCaseScenarioReadiness")
    assert paths[f"{base}/{{scenario_id}}/validation"]["get"]["operationId"] == (
        "validateCaseScenario"
    )
    assert f"{base}/{{scenario_id}}/copy" in paths
    assert f"{base}/{{scenario_id}}/archive" in paths
    assumption_path = f"{base}/{{scenario_id}}/assumptions/{{assumption_id}}"
    assert paths[assumption_path]["patch"]["operationId"] == "updateScenarioAssumption"
    assert f"{assumption_path}/review" in paths

    for name in (
        "ScenarioInitialize",
        "ScenarioCreate",
        "ScenarioUpdate",
        "ScenarioCopy",
        "ScenarioArchive",
        "AssumptionCreate",
        "AssumptionUpdate",
        "AssumptionReview",
    ):
        assert components[name]["additionalProperties"] is False
    assert {"scenarios", "readiness", "case_id"} <= set(
        components["ScenarioWorkspaceRead"]["required"]
    )
    assert "assumptions" in components["ScenarioRead"]["required"]
    assert components["AssumptionValue"]["anyOf"] == [
        {"type": "string"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "null"},
    ]


def test_calculation_contracts_include_lifecycle_errors_versions_and_outputs(
    client: TestClient,
) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    components = schema["components"]["schemas"]
    base = "/api/v1/cases/{case_id}/calculation-runs"

    assert paths[base]["get"]["operationId"] == "listCalculationRuns"
    assert paths[base]["post"]["operationId"] == "startCalculationRun"
    assert paths[f"{base}/{{run_id}}"]["get"]["operationId"] == "getCalculationRun"
    assert paths[f"{base}/{{run_id}}/rerun"]["post"]["operationId"] == ("rerunCalculation")
    assert components["CalculationRunCreate"]["additionalProperties"] is False
    assert components["CalculationRerunCreate"]["additionalProperties"] is False
    list_parameters = {
        parameter["name"]: parameter for parameter in paths[base]["get"]["parameters"]
    }
    assert list_parameters["limit"]["schema"]["maximum"] == 100
    assert list_parameters["offset"]["schema"]["minimum"] == 0
    assert {
        "runs",
        "latest_successful_runs_by_scenario",
        "total",
        "limit",
        "offset",
        "has_more",
    } <= set(
        components["CalculationRunListRead"]["required"]
    )
    assert {"inputs", "outputs"}.isdisjoint(components["CalculationRunSummaryRead"]["properties"])
    assert {
        "status",
        "engine_version",
        "input_schema_version",
        "output_schema_version",
        "input_hash",
        "inputs",
        "error",
        "outputs",
    } <= set(components["CalculationRunRead"]["required"])
    assert {
        "total_assets",
        "total_liabilities",
        "total_equity",
        "cash",
        "projected_inflows",
        "projected_outflows",
    } <= set(components["ForecastPeriodRead"]["required"])
