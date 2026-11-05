from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.core.config import get_settings
from app.core.security import create_token
from app.db.base import utc_now
from app.schemas.common import ErrorResponse
from tests.support.helpers import ORG_1, USER_1, headers

TAXONOMIES: list[tuple[str, dict[str, list[str]]]] = [
    (
        "/api/v1/risk-types",
        {
            "risk_types": [
                "concentration_risk",
                "liquidity_risk",
                "leverage_risk",
                "cash_flow_risk",
                "documentation_gap",
                "compliance_gap",
                "operational_risk",
            ]
        },
    ),
    (
        "/api/v1/assessment-types",
        {"assessment_types": ["vendor_risk", "borrower_risk", "financial_statement_review"]},
    ),
    (
        "/api/v1/taxonomies/cases",
        {
            "statuses": ["active", "archived", "completed", "draft", "in_review"],
            "decisions": ["approved", "escalated", "needs_more_info", "rejected"],
            "risk_levels": ["critical", "high", "low", "medium"],
            "sort_options": [
                "created_at_asc",
                "created_at_desc",
                "risk_score_asc",
                "risk_score_desc",
                "title_asc",
                "updated_at_asc",
                "updated_at_desc",
            ],
        },
    ),
]


@pytest.mark.parametrize(("path", "expected"), TAXONOMIES)
@pytest.mark.parametrize("roles", [(), ("viewer",), ("examiner",), ("admin",)])
def test_authenticated_taxonomy_payloads_are_unchanged(
    db_client: TestClient,
    path: str,
    expected: dict[str, list[str]],
    roles: tuple[str, ...],
) -> None:
    response = db_client.get(path, headers=headers(roles=roles))

    assert response.status_code == 200, response.text
    assert TypeAdapter(dict[str, list[str]]).validate_json(response.content) == expected


@pytest.mark.parametrize("path", [path for path, _ in TAXONOMIES])
@pytest.mark.parametrize("credential", ["missing", "invalid", "expired"])
def test_taxonomies_reject_missing_invalid_and_expired_credentials(
    db_client: TestClient, path: str, credential: str
) -> None:
    authorization: dict[str, str] = {}
    if credential == "invalid":
        authorization = {"Authorization": "Bearer not.a.valid.jwt"}
    elif credential == "expired":
        settings = get_settings().auth
        token = create_token(
            subject=USER_1,
            organization_id=ORG_1,
            roles=["viewer"],
            authorization_version=1,
            token_type="access",
            now=utc_now() - timedelta(seconds=settings.access_token_ttl_seconds + 60),
            settings=settings,
        )
        authorization = {"Authorization": f"Bearer {token}"}

    response = db_client.get(path, headers=authorization)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    error = ErrorResponse.model_validate_json(response.content).error
    assert error.code == "unauthorized"
    assert error.message == (
        "Missing bearer token." if credential == "missing" else "Invalid or expired token."
    )
    assert error.request_id == response.headers["X-Request-ID"]
