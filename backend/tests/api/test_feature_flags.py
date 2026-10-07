"""``GET /api/v1/feature-flags``: how the dashboard learns whether BI is on.

Authenticated tenant route, no bank, mounted unconditionally. It projects the BI
booleans and nothing else — no limit, no URL, no key — so a future flag is added to
the schema deliberately, never by spreading settings. The count is pinned rather
than derived, which is why adding ``bi_nlq_enabled`` in Phase 5 made three of these
tests fail: that is the tripwire working, not a defect. The reason it is pinned is
that this route is the one place deployment configuration crosses into the browser,
so a flag arriving here by accident is a configuration leak.

The two flags added for audit A360-2 M3 (``bi_alerts_enabled``,
``bi_subscriptions_enabled``) are projected for a second reason, and the last
section pins it: the SERVER's own copy for an alert with no verdict and a report
that has never gone out must name the deployment switch when that is the cause,
never the bank's data. The flag the browser reads and the sentence the row carries
are the same fact, so a test here asks for both at once.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.features import manage_bi_notifications as notifications
from app.models import Bank
from app.schemas.feature_flags import FeatureFlagsRead
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

URL = "/api/v1/feature-flags"

EXPECTED_KEYS = {
    "bi_enabled",
    "bi_mart_enqueue_enabled",
    "bi_scheduler_enabled",
    # Phase 5. Projected because the ask routes answer 409 rather than 404 when it
    # is off — deliberately, so the flag cannot be probed — which leaves the
    # navigation as the only thing that can decline the door. Without this key the
    # surface is built and permanently invisible.
    "bi_nlq_enabled",
    # A360-2 M3. Projected so the alerts and scheduled-reports pages can say WHY
    # nothing has happened. Before these keys existed, an alert in a deployment
    # with evaluation off read "Waiting for figures" — the operator's switch
    # reported as the bank's late data.
    "bi_alerts_enabled",
    "bi_subscriptions_enabled",
}


def test_an_unauthenticated_caller_is_refused(db_client: TestClient) -> None:
    assert db_client.get(URL).status_code == 401


def test_the_shape_is_exactly_the_bi_booleans(db_client: TestClient) -> None:
    response = db_client.get(URL, headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == EXPECTED_KEYS
    assert all(isinstance(value, bool) for value in body.values())


def test_the_defaults_are_off(db_client: TestClient) -> None:
    """The hermetic pins mirror the product defaults: nothing is on."""
    body = db_client.get(URL, headers=headers()).json()
    assert body == {
        "bi_enabled": False,
        "bi_mart_enqueue_enabled": False,
        "bi_scheduler_enabled": False,
        "bi_nlq_enabled": False,
        "bi_alerts_enabled": False,
        "bi_subscriptions_enabled": False,
    }


def test_the_values_follow_the_deployment_settings(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BI_ENABLED", "1")
    monkeypatch.setenv("BI_SCHEDULER_ENABLED", "1")
    # Left OFF while BI_ENABLED is on, deliberately: the two are independent, and a
    # reader who can reach Insights must not thereby be able to ask a model
    # anything. The pairing is what proves the independence.
    monkeypatch.setenv("BI_NLQ_ENABLED", "0")
    # Alerts on, subscriptions off: the two notification flags are independent of
    # each other and of the scheduler flag, and each must be read from its own
    # setting rather than inferred from a neighbour.
    monkeypatch.setenv("BI_ALERTS_ENABLED", "1")
    monkeypatch.setenv("BI_SUBSCRIPTIONS_ENABLED", "0")
    get_settings.cache_clear()

    body = db_client.get(URL, headers=headers()).json()

    assert body == {
        "bi_enabled": True,
        "bi_mart_enqueue_enabled": False,
        "bi_scheduler_enabled": True,
        "bi_nlq_enabled": False,
        "bi_alerts_enabled": True,
        "bi_subscriptions_enabled": False,
    }


def test_the_response_carries_no_key_outside_the_schema(db_client: TestClient) -> None:
    """Nothing secret and nothing tunable rides along: no URL, cap, timeout or key."""
    body = db_client.get(URL, headers=headers()).json()
    assert set(body) == set(FeatureFlagsRead.model_fields)
    forbidden = ("url", "cap", "timeout", "license", "licence", "key", "retention")
    assert not [key for key in body if any(token in key for token in forbidden)]


def test_the_flags_are_deployment_wide_not_per_tenant(db_client: TestClient) -> None:
    """Any authenticated tenant reads the same projection; there is no bank in the path."""
    first = db_client.get(URL, headers=headers()).json()
    second = db_client.get(URL, headers=headers(org_id=ORG_2)).json()
    assert first == second


def test_no_bank_is_required_or_accepted(db_client: TestClient) -> None:
    """A bank query parameter is ignored: the route is not bank-scoped."""
    response = db_client.get(URL, params={"bank_id": "BK-NOPE0000"}, headers=headers())
    assert response.status_code == 200


# --- the flag the browser reads is the fact the row states ---------------------------------
#
# A deployment with BI on and evaluation off ADMITS an alert (a bank may prepare its
# watch list ahead of the operator's switch) and must then say, on the row itself,
# that the deployment is what is not judging it. The projection and the sentence are
# checked together because they are one fact: a page that reads the flag and a
# client that reads only the row must reach the same conclusion.

BANK_ID = "BK-FLAGS0001"
BASE = f"/api/v1/banks/{BANK_ID}/bi"
LOANS = "loans.balance_rc"


@pytest.fixture
def bi_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.setenv("BI_ENABLED", "1")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.fixture
def flagged_bank(db_session: Session) -> Bank:
    bank = db_session.get(Bank, BANK_ID)
    if bank is None:
        bank = Bank(
            id=BANK_ID,
            organization_id=ORG_1,
            name="Feature flags bank",
            short_name="Flags",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type="universal_bank",
        )
        db_session.add(bank)
        db_session.commit()
    return bank


def _alert_body(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "measure_id": LOANS,
        "direction": "above",
        "threshold_basis": "stated",
        "threshold": "1000000",
        "notify_user_ids": [str(USER_1)],
        "reason": "Told when the loan book passes the plan.",
    }


def _subscription_body(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "query": {"measures": [LOANS], "dimensions": [], "time": {"as_of": "2026-08-31"}},
        "artifact_format": "csv",
        "cadence": "weekly",
        "hour": 7,
        "minute": 30,
        "day_of_week": 1,
        "recipient_user_ids": [str(USER_1)],
        "reason": "The Monday lending pack.",
    }


def _set(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    get_settings.cache_clear()


@pytest.mark.usefixtures("flagged_bank")
def test_an_alert_with_evaluation_off_names_the_deployment_not_the_data(
    db_client: TestClient, bi_on: pytest.MonkeyPatch
) -> None:
    _set(bi_on, "BI_ALERTS_ENABLED", "0")
    assert db_client.get(URL, headers=headers()).json()["bi_alerts_enabled"] is False

    created = db_client.post(f"{BASE}/alerts", json=_alert_body("Off"), headers=headers())
    assert created.status_code == 201, created.text
    detail = created.json()["latest_detail"]
    assert detail == notifications.ALERTS_NOT_ENABLED
    assert "not enabled in this deployment" in detail
    # Never the bank's figures: the sentence that was there before said the alert
    # "is evaluated each time the institution's figures are rebuilt", which is the
    # promise a shut flag does not keep.
    assert "figures" not in detail

    # The list read composes the same sentence from the same fact.
    listed = db_client.get(f"{BASE}/alerts", headers=headers())
    assert listed.status_code == 200, listed.text
    rows = [row for row in listed.json()["alerts"] if row["name"] == "Off"]
    assert rows and rows[0]["latest_detail"] == notifications.ALERTS_NOT_ENABLED


@pytest.mark.usefixtures("flagged_bank")
def test_an_alert_with_evaluation_on_is_awaiting_its_first_build(
    db_client: TestClient, bi_on: pytest.MonkeyPatch
) -> None:
    _set(bi_on, "BI_ALERTS_ENABLED", "1")
    assert db_client.get(URL, headers=headers()).json()["bi_alerts_enabled"] is True

    created = db_client.post(f"{BASE}/alerts", json=_alert_body("On"), headers=headers())
    assert created.status_code == 201, created.text
    detail = created.json()["latest_detail"]
    assert detail == notifications.NEVER_EVALUATED
    assert "deployment" not in detail


@pytest.mark.usefixtures("flagged_bank")
def test_a_stopped_alert_is_stopped_whatever_the_deployment_says(
    db_client: TestClient, bi_on: pytest.MonkeyPatch
) -> None:
    """The row's own state comes first: a stopped alert is not "not enabled here"."""
    _set(bi_on, "BI_ALERTS_ENABLED", "0")
    body = {**_alert_body("Stopped"), "is_active": False}
    created = db_client.post(f"{BASE}/alerts", json=body, headers=headers())
    assert created.status_code == 201, created.text
    assert created.json()["latest_detail"] == notifications.ALERT_INACTIVE


@pytest.mark.usefixtures("flagged_bank")
def test_a_report_with_delivery_off_says_so_beside_its_disclosure_sentence(
    db_client: TestClient, bi_on: pytest.MonkeyPatch
) -> None:
    _set(bi_on, "BI_SUBSCRIPTIONS_ENABLED", "0")
    assert db_client.get(URL, headers=headers()).json()["bi_subscriptions_enabled"] is False

    created = db_client.post(
        f"{BASE}/subscriptions", json=_subscription_body("Off"), headers=headers()
    )
    assert created.status_code == 201, created.text
    note = created.json()["delivery_note"]
    # The disclosure sentence is true of the report whenever it goes out and stays;
    # the deployment fact follows it.
    assert note.startswith(notifications.DELIVERY_NOTE_ATTACHED)
    assert note.endswith(notifications.SUBSCRIPTIONS_NOT_ENABLED)
    assert "not enabled here" in note

    listed = db_client.get(f"{BASE}/subscriptions", headers=headers())
    assert listed.status_code == 200, listed.text
    rows = [row for row in listed.json()["subscriptions"] if row["name"] == "Off"]
    assert rows and rows[0]["delivery_note"] == note


@pytest.mark.usefixtures("flagged_bank")
def test_a_report_with_delivery_on_carries_only_its_disclosure_sentence(
    db_client: TestClient, bi_on: pytest.MonkeyPatch
) -> None:
    _set(bi_on, "BI_SUBSCRIPTIONS_ENABLED", "1")
    created = db_client.post(
        f"{BASE}/subscriptions", json=_subscription_body("On"), headers=headers()
    )
    assert created.status_code == 201, created.text
    assert created.json()["delivery_note"] == notifications.DELIVERY_NOTE_ATTACHED


@pytest.mark.usefixtures("flagged_bank")
def test_a_stopped_report_does_not_blame_the_deployment(
    db_client: TestClient, bi_on: pytest.MonkeyPatch
) -> None:
    _set(bi_on, "BI_SUBSCRIPTIONS_ENABLED", "0")
    body = {**_subscription_body("Stopped"), "is_active": False}
    created = db_client.post(f"{BASE}/subscriptions", json=body, headers=headers())
    assert created.status_code == 201, created.text
    assert created.json()["delivery_note"] == notifications.DELIVERY_NOTE_ATTACHED
