"""The FX-forward construction console API (FC-6b): auth gate + preview wiring.

Preview only — the endpoint writes no curve state, just a staff audit row. The CIP
maths + leg resolution are proven at the domain/service layers
(``tests/domain/curves/test_fx_forward.py``, ``tests/services/test_fx_forward.py``);
here the focus is the operator wiring, auth, and the request/response contract.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OperatorAuditLog
from tests.operator.conftest import operator_headers

BASE = "/operator/v1/fx-forward"
AS_OF = "2026-08-07"


def _leg(*, high: bool) -> dict[str, object]:
    bump = 0.15 if high else 0.0
    return {
        "source": "quotes",
        "calendar_name": "GHANA",
        "quotes": [
            {"instrument": "deposit", "tenor": "3M", "quote": 0.05 + bump},
            {"instrument": "deposit", "tenor": "6M", "quote": 0.051 + bump},
            {"instrument": "ois", "tenor": "1Y", "quote": 0.052 + bump},
            {"instrument": "ois", "tenor": "2Y", "quote": 0.053 + bump},
            {"instrument": "ois", "tenor": "3Y", "quote": 0.054 + bump},
        ],
    }


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "base_ccy": "USD",
        "quote_ccy": "GHS",
        "spot": 12.5,
        "as_of": AS_OF,
        "base_leg": _leg(high=False),
        "quote_leg": _leg(high=True),
        "tenor_grid": ["3M", "6M", "1Y", "2Y"],
        "grid_calendar": "GHANA",
    }
    body.update(overrides)
    return body


def test_requires_operator_context(operator_client: TestClient) -> None:
    assert operator_client.post(f"{BASE}/construct", json=_body()).status_code == 401


def test_construct_fx_forward(operator_client: TestClient, operator_db: Session) -> None:
    response = operator_client.post(
        f"{BASE}/construct", headers=operator_headers(), json=_body()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pair"] == "USDGHS"
    assert body["basis_calibrated"] is False
    assert body["base_source"] == "quotes"
    assert len(body["input_digest"]) == 64
    assert len(body["rows"]) == 4
    # Quote currency out-yields base => base at a forward premium (F > S), growing.
    forwards = [row["forward_rate"] for row in body["rows"]]
    assert all(rate > body["spot"] for rate in forwards)
    assert all(later > earlier for earlier, later in zip(forwards, forwards[1:], strict=False))

    actions = list(operator_db.scalars(select(OperatorAuditLog.action)))
    assert "desk.fx_forward.construct" in actions


def test_identity_when_legs_match(operator_client: TestClient) -> None:
    response = operator_client.post(
        f"{BASE}/construct",
        headers=operator_headers(),
        json=_body(quote_leg=_leg(high=False)),
    )
    assert response.status_code == 200, response.text
    for row in response.json()["rows"]:
        assert abs(row["forward_rate"] - 12.5) < 1e-6
        assert abs(row["forward_points"]) < 1e-6


def test_basis_bps_shifts_the_forward(operator_client: TestClient) -> None:
    base = operator_client.post(
        f"{BASE}/construct", headers=operator_headers(), json=_body()
    ).json()
    shifted = operator_client.post(
        f"{BASE}/construct", headers=operator_headers(), json=_body(basis_bps=75.0)
    ).json()
    # A positive basis on the base leg lowers every forward.
    for zero, adj in zip(base["rows"], shifted["rows"], strict=False):
        assert adj["forward_rate"] < zero["forward_rate"]


def test_bad_grid_request_is_422(operator_client: TestClient) -> None:
    # Neither tenor_grid nor dates -> pydantic model validation 422.
    body = _body()
    del body["tenor_grid"]
    del body["grid_calendar"]
    assert (
        operator_client.post(f"{BASE}/construct", headers=operator_headers(), json=body).status_code
        == 422
    )


def test_unknown_calendar_is_422(operator_client: TestClient) -> None:
    response = operator_client.post(
        f"{BASE}/construct",
        headers=operator_headers(),
        json=_body(grid_calendar="ATLANTIS"),
    )
    assert response.status_code == 422, response.text
