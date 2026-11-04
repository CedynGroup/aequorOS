"""Tests for test_liquidity."""

from fastapi.testclient import TestClient


def test_liquidity_openapi_contracts(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    summary_path = "/api/v1/cases/{case_id}/liquidity/summary"
    review_path = "/api/v1/cases/{case_id}/liquidity/findings/{finding_id}/review"

    assert paths[summary_path]["get"]["operationId"] == "getLiquiditySummary"
    assert paths[review_path]["post"]["operationId"] == "reviewLiquidityFinding"
    assert (
        paths[summary_path]["get"]["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ]
        == "#/components/schemas/LiquiditySummaryRead"
    )
    review = paths[review_path]["post"]
    assert review["requestBody"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/LiquidityFindingReview"
    )
    assert review["security"] == [{"HTTPBearer": []}]
    components = schema["components"]["schemas"]
    for name in (
        "LiquiditySummaryRead",
        "LiquidityMetricRead",
        "LiquidityFindingRead",
        "LiquidityEvidenceRead",
        "LiquidityFindingReview",
    ):
        assert components[name]["additionalProperties"] is False
    metric = components["LiquidityMetricRead"]
    assert metric["properties"]["availability"]["enum"] == ["available", "unavailable"]
    assert {item.get("type") for item in metric["properties"]["value"]["anyOf"]} == {
        "string",
        "null",
    }
    assert "diagnostic" in metric["properties"]
