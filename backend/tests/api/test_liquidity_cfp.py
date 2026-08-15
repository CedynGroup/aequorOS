"""EWI/CFP endpoint wiring + role gates (LRMD ¶28/¶70–77; Phase 2 item 3).

Lifecycle depth lives in tests/services/test_liquidity_cfp.py; this suite
proves the routes exist, resolve tenancy, and hold the approver gate on
register writes, approval and activation.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.api.helpers import ORG_2, headers

EWIS_URL = f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity/ewis"
CFP_URL = f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity/cfp"


def _seed(db_client: TestClient) -> str:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    return periods[0]["id"]


def test_ewi_dashboard_serves_starters_and_holds_the_approver_gate(
    db_client: TestClient,
) -> None:
    period_id = _seed(db_client)

    dashboard = db_client.get(
        EWIS_URL, headers=headers(), params={"reporting_period_id": period_id}
    )
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    assert len(body["indicators"]) == 8
    assert body["escalation_state"] == "normal"
    # No canonical positions in the bare seed: values are honestly absent.
    concentration = next(
        entry for entry in body["indicators"] if entry["code"] == "funding_concentration"
    )
    assert concentration["status"] == "no_data"

    register_payload = {
        "indicators": [{"code": "funding_concentration", "watch_threshold": "50"}],
        "approved_by": "Board minute",
        "reason": "adopt",
    }
    forbidden = db_client.put(
        EWIS_URL, headers=headers(roles=("analyst",)), json=register_payload
    )
    assert forbidden.status_code == 403
    allowed = db_client.put(
        EWIS_URL, headers=headers(roles=("approver",)), json=register_payload
    )
    assert allowed.status_code == 204, allowed.text

    foreign = db_client.get(
        EWIS_URL, headers=headers(ORG_2), params={"reporting_period_id": period_id}
    )
    assert foreign.status_code == 404


def test_cfp_routes_enforce_maker_checker_roles(db_client: TestClient) -> None:
    _seed(db_client)

    summary = db_client.get(CFP_URL, headers=headers())
    assert summary.status_code == 200
    assert summary.json() == {"current": None, "approved": None}

    draft_payload = {
        "content": {
            "funding_options": [{"horizon": "intraday", "source": "Reserve balance"}]
        },
        "reason": "start drafting",
    }
    viewer = db_client.put(CFP_URL, headers=headers(roles=("viewer",)), json=draft_payload)
    assert viewer.status_code == 403
    drafted = db_client.put(CFP_URL, headers=headers(roles=("analyst",)), json=draft_payload)
    assert drafted.status_code == 200, drafted.text
    assert drafted.json()["status"] == "draft"

    approve_payload = {"approval_reference": "BM-1", "reason": "annual"}
    gated = db_client.post(
        f"{CFP_URL}/approve", headers=headers(roles=("analyst",)), json=approve_payload
    )
    assert gated.status_code == 403
    # An approver-role token reaches the service, where maker-checker holds:
    # the same user drafted the plan, so approving it is refused.
    blocked = db_client.post(
        f"{CFP_URL}/approve", headers=headers(roles=("approver",)), json=approve_payload
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["error_code"] == "self_approval"

    events = db_client.get(f"{CFP_URL}/events", headers=headers())
    assert events.status_code == 200
    assert events.json() == {"events": []}
