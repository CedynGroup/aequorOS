"""Operating-Environment desk console API (operator surface).

Auth gate, compute-preview (persists no assessment state), the maker-checker
lifecycle at the API boundary, and the four-eyes refusal (the operator dev
session is a single identity, so self-approval is exactly the 422 path). Heavy
publish fan-out is proven at the service layer
(``tests/services/test_operating_environment.py``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DeskOperatingEnvironmentAssessment, OperatorAuditLog
from tests.operator.conftest import operator_headers

BASE = "/operator/v1/operating-environment"
COB = "2026-08-07"

_INPUTS = {
    "real_gdp_growth_pct": "4.0",
    "gdp_per_capita_usd": "2200",
    "cpi_inflation_pct": "23",
    "private_credit_to_gdp_growth_pct": "11",
    "system_npl_pct": "21",
    "private_debt_to_gdp_pct": "32",
    "regulatory_quality_score": 4,
    "system_roa_pct": "1.2",
    "system_credit_growth_pct": "18",
    "system_loan_to_deposit_pct": "70",
    "system_car_pct": "14",
    "external_funding_pct": "25",
    "sovereign_rating": "BBB-",
    "policy_rate_pct": "27",
}


def _body(**overrides: object) -> dict[str, object]:
    inputs = {**_INPUTS, **overrides}
    return {"jurisdiction_code": "GH", "cob_date": COB, "inputs": inputs}


def test_requires_authentication(operator_client: TestClient) -> None:
    assert operator_client.post(f"{BASE}/compute-preview", json=_body()).status_code == 401


def test_compute_preview_persists_nothing(
    operator_client: TestClient, operator_db: Session
) -> None:
    response = operator_client.post(
        f"{BASE}/compute-preview", json=_body(), headers=operator_headers()
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["score"] == "0.491000"
    assert payload["breakdown"]["sovereign_category"] == "bbb"
    assert payload["breakdown"]["governor_applied"] is False
    assert payload["methodology_version"]
    # No assessment row landed — only a compute-preview audit row.
    assert operator_db.scalars(select(DeskOperatingEnvironmentAssessment)).all() == []
    actions = {row.action for row in operator_db.scalars(select(OperatorAuditLog))}
    assert "desk.operating_environment.compute_preview" in actions


def test_stage_and_lifecycle(operator_client: TestClient, operator_db: Session) -> None:
    created = operator_client.post(
        f"{BASE}/assessments", json=_body(), headers=operator_headers()
    )
    assert created.status_code == 201, created.text
    assessment = created.json()
    assessment_id = assessment["id"]
    assert assessment["status"] == "draft"
    assert assessment["score"] == "0.491000"
    assert assessment["proposed_by"] == "dev@aequoros.com"

    # GET item + list.
    got = operator_client.get(
        f"{BASE}/assessments/{assessment_id}", headers=operator_headers()
    )
    assert got.status_code == 200
    listed = operator_client.get(f"{BASE}/assessments", headers=operator_headers())
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    submitted = operator_client.post(
        f"{BASE}/assessments/{assessment_id}/submit", headers=operator_headers()
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "pending_review"


def test_four_eyes_self_approval_rejected(operator_client: TestClient) -> None:
    """The dev session proposes AND tries to approve — four-eyes → 422."""
    created = operator_client.post(
        f"{BASE}/assessments", json=_body(), headers=operator_headers()
    )
    assessment_id = created.json()["id"]
    operator_client.post(
        f"{BASE}/assessments/{assessment_id}/submit", headers=operator_headers()
    )
    approved = operator_client.post(
        f"{BASE}/assessments/{assessment_id}/approve", headers=operator_headers()
    )
    assert approved.status_code == 422


def test_missing_required_input_is_422(operator_client: TestClient) -> None:
    inputs = dict(_INPUTS)
    del inputs["system_car_pct"]
    body = {"jurisdiction_code": "GH", "cob_date": COB, "inputs": inputs}
    response = operator_client.post(
        f"{BASE}/assessments", json=body, headers=operator_headers()
    )
    # Pydantic rejects the incomplete closed model before the service runs.
    assert response.status_code == 422
