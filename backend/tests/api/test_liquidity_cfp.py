"""EWI/CFP endpoint wiring + role gates (LRMD ¶28/¶70–77; Phase 2 item 3),
against the ACTUAL primary database.

Invariants: every starter indicator is evaluated with a status that follows
from its value and Board thresholds (no_data / unconfigured / normal / watch /
action) and the escalation state follows from those statuses; the register PUT
is approver-gated and its thresholds re-grade the live values; CFP drafting is
analyst work, approval is approver-gated AND maker-checker (self-approval is
refused); tenant isolation. Lifecycle depth lives in
tests/services/test_liquidity_cfp.py.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from tests.real_data import REAL_BANK_ID, other_headers, real_headers, requires_real_data

pytestmark = requires_real_data

EWIS_URL = f"/api/v1/banks/{REAL_BANK_ID}/liquidity/ewis"
CFP_URL = f"/api/v1/banks/{REAL_BANK_ID}/liquidity/cfp"
STARTER_CODES = {
    "asset_growth_volatile_funding",
    "funding_concentration",
    "currency_mismatch",
    "weighted_liability_maturity",
    "near_limit_incidents",
    "earnings_asset_quality",
    "debt_spreads",
    "funding_costs",
}


def _latest_period_id(client: TestClient) -> str:
    response = client.get(f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods[0]["id"]


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _expected_status(indicator: dict[str, Any]) -> str:
    """The server's own RAG rule, re-derived from the indicator's fields."""
    value = _dec(indicator["value"])
    watch = _dec(indicator["watch_threshold"])
    action = _dec(indicator["action_threshold"])
    if value is None:
        return "no_data"
    if watch is None and action is None:
        return "unconfigured"

    def breaches(threshold: Decimal) -> bool:
        return value >= threshold if indicator["direction"] == "above" else value <= threshold

    if action is not None and breaches(action):
        return "action"
    if watch is not None and breaches(watch):
        return "watch"
    return "normal"


def _expected_escalation(body: dict[str, Any]) -> str:
    if body["cfp_active"]:
        return "cfp_active"
    statuses = {indicator["status"] for indicator in body["indicators"]}
    if "action" in statuses:
        return "escalation"
    if "watch" in statuses:
        return "heightened_monitoring"
    return "normal"


def _assert_dashboard_consistent(body: dict[str, Any], period_id: str) -> None:
    assert body["bank_id"] == REAL_BANK_ID
    assert body["reporting_period_id"] == period_id
    codes = [indicator["code"] for indicator in body["indicators"]]
    assert len(codes) == len(set(codes))
    # Every directive starter is evaluated (a bank may add custom ones on top).
    assert set(codes) >= STARTER_CODES
    for indicator in body["indicators"]:
        assert indicator["enabled"] is True
        assert indicator["direction"] in {"above", "below"}
        assert indicator["status"] == _expected_status(indicator), indicator["code"]
        # A value read from the book is honest: absent values explain themselves.
        if indicator["value"] is None:
            assert indicator["detail"], indicator["code"]
    assert body["escalation_state"] == _expected_escalation(body)


def test_ewi_dashboard_serves_starters_and_holds_the_approver_gate(
    real_client: TestClient,
) -> None:
    period_id = _latest_period_id(real_client)

    dashboard = real_client.get(
        EWIS_URL, headers=real_headers(), params={"reporting_period_id": period_id}
    )
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    _assert_dashboard_consistent(body, period_id)
    # The real book has canonical positions, so the concentration indicator is
    # measured (never "no_data"); vendor debt spreads are not connected yet.
    concentration = next(
        entry for entry in body["indicators"] if entry["code"] == "funding_concentration"
    )
    assert concentration["value"] is not None
    assert Decimal("0") <= Decimal(str(concentration["value"])) <= Decimal("100")
    assert concentration["status"] != "no_data"
    spreads = next(entry for entry in body["indicators"] if entry["code"] == "debt_spreads")
    assert spreads["status"] == "no_data"

    # Set a Board watch level BELOW the measured concentration: the register
    # write must re-grade the indicator to "watch" and lift the escalation
    # state accordingly (status follows threshold, never the other way round).
    measured = Decimal(str(concentration["value"]))
    watch_level = str(max(measured - Decimal("0.5"), Decimal("0")))
    register_payload = {
        "indicators": [{"code": "funding_concentration", "watch_threshold": watch_level}],
        "approved_by": "Board minute",
        "reason": "adopt",
    }
    forbidden = real_client.put(
        EWIS_URL, headers=real_headers(roles=("analyst",)), json=register_payload
    )
    assert forbidden.status_code == 403
    allowed = real_client.put(
        EWIS_URL, headers=real_headers(roles=("approver",)), json=register_payload
    )
    assert allowed.status_code == 204, allowed.text

    regraded = real_client.get(
        EWIS_URL, headers=real_headers(), params={"reporting_period_id": period_id}
    )
    assert regraded.status_code == 200, regraded.text
    body = regraded.json()
    _assert_dashboard_consistent(body, period_id)
    concentration = next(
        entry for entry in body["indicators"] if entry["code"] == "funding_concentration"
    )
    assert Decimal(str(concentration["watch_threshold"])) == Decimal(watch_level)
    assert concentration["action_threshold"] is None
    assert Decimal(str(concentration["value"])) == measured
    assert concentration["status"] == "watch"
    assert body["escalation_state"] in {"heightened_monitoring", "escalation", "cfp_active"}

    foreign = real_client.get(
        EWIS_URL, headers=other_headers(), params={"reporting_period_id": period_id}
    )
    assert foreign.status_code == 404
    assert (
        real_client.put(
            EWIS_URL, headers=other_headers(roles=("approver",)), json=register_payload
        ).status_code
        == 404
    )


def test_cfp_routes_enforce_maker_checker_roles(real_client: TestClient) -> None:
    summary = real_client.get(CFP_URL, headers=real_headers())
    assert summary.status_code == 200, summary.text
    before = summary.json()
    assert set(before) == {"current", "approved"}
    events_before = real_client.get(f"{CFP_URL}/events", headers=real_headers())
    assert events_before.status_code == 200
    n_events = len(events_before.json()["events"])

    draft_payload = {
        "content": {"funding_options": [{"horizon": "intraday", "source": "Reserve balance"}]},
        "reason": "start drafting",
    }
    viewer = real_client.put(CFP_URL, headers=real_headers(roles=("viewer",)), json=draft_payload)
    assert viewer.status_code == 403
    drafted = real_client.put(CFP_URL, headers=real_headers(roles=("analyst",)), json=draft_payload)
    assert drafted.status_code == 200, drafted.text
    plan = drafted.json()
    assert plan["status"] == "draft"
    # A draft either continues the current draft or opens the next version.
    current_before = before["current"]
    if current_before is not None and current_before["status"] == "draft":
        assert plan["id"] == current_before["id"]
        assert plan["version"] == current_before["version"]
    else:
        assert plan["version"] == (
            current_before["version"] + 1 if current_before is not None else 1
        )
    after = real_client.get(CFP_URL, headers=real_headers()).json()
    assert after["current"]["id"] == plan["id"]
    assert after["current"]["status"] == "draft"
    # Drafting never touches the approved plan.
    assert after["approved"] == before["approved"]

    approve_payload = {"approval_reference": "BM-1", "reason": "annual"}
    gated = real_client.post(
        f"{CFP_URL}/approve", headers=real_headers(roles=("analyst",)), json=approve_payload
    )
    assert gated.status_code == 403
    # An approver-role token reaches the service, where maker-checker holds:
    # the same user drafted the plan, so approving it is refused.
    blocked = real_client.post(
        f"{CFP_URL}/approve", headers=real_headers(roles=("approver",)), json=approve_payload
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["error_code"] == "self_approval"
    assert real_client.get(CFP_URL, headers=real_headers()).json()["current"]["status"] == "draft"

    # No activation/de-escalation happened: the ¶74 event trail is unchanged.
    events = real_client.get(f"{CFP_URL}/events", headers=real_headers())
    assert events.status_code == 200
    assert len(events.json()["events"]) == n_events

    # Cross-tenant: authenticated foreign admin, RLS hides the bank.
    assert real_client.get(CFP_URL, headers=other_headers()).status_code == 404
    assert real_client.put(CFP_URL, headers=other_headers(), json=draft_payload).status_code == 404
    assert real_client.get(f"{CFP_URL}/events", headers=other_headers()).status_code == 404
