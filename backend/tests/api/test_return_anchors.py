"""The reporting-date picker's window, over HTTP (founder review 2026-09-19).

``/return-anchors`` and ``/reporting-obligations`` offer one bounded window of
the regulator's own reporting dates: ``lookback_months`` back over the anchors
the bank already owes, ``horizon_months`` forward over the ones coming. The
defect these tests pin: the trailing half did not exist, so a tenant whose
newest computed position was a quarter old was offered only future dates — all
``awaiting_data`` — and could not generate the return at all.

Deliberately date-agnostic. The routes read the real clock (there is no
``as_of`` query parameter, by design: the reporting calendar is not something a
client gets to move), so every assertion here is about the SHAPE of the window
relative to the ``as_of`` the response reports. ``anchors.py`` arithmetic for
fixed dates is pinned in ``tests/services/test_reporting_anchors.py``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.services.regulatory_reporting.anchors import (
    DEFAULT_HORIZON_MONTHS,
    DEFAULT_LOOKBACK_MONTHS,
)
from tests.api.helpers import headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
ANCHORS = f"{BASE}/return-anchors"
OBLIGATIONS = f"{BASE}/reporting-obligations"
#: A monthly BoG form: one elapsed anchor per lookback month, exactly.
MONTHLY_RETURN = "BSD2"
#: Month ends inside a lookback of N months are all elapsed — the current
#: month's end never is — so a monthly return offers exactly N of them.
ELAPSED_MONTH_ENDS_PER_LOOKBACK_MONTH = 1


def _seed() -> None:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


def _anchors(client: TestClient, **params: int) -> dict[str, Any]:
    response = client.get(
        ANCHORS,
        headers=headers(),
        params={"return_code": MONTHLY_RETURN, **params},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _split(body: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The elapsed and upcoming reporting dates, as the response reports them."""
    as_of = body["as_of"]
    dates = [anchor["reporting_date"] for anchor in body["anchors"]]
    return (
        sorted(value for value in dates if value < as_of),
        sorted(value for value in dates if value >= as_of),
    )


def test_anchor_window_defaults_to_two_quarters_of_elapsed_dates(
    db_client: TestClient,
) -> None:
    _seed()
    body = _anchors(db_client)

    assert body["lookback_months"] == DEFAULT_LOOKBACK_MONTHS
    assert body["horizon_months"] == DEFAULT_HORIZON_MONTHS
    elapsed, upcoming = _split(body)
    assert len(elapsed) == (
        DEFAULT_LOOKBACK_MONTHS * ELAPSED_MONTH_ENDS_PER_LOOKBACK_MONTH
    ), "a bank two quarters behind must be offered every month end it owes"
    assert upcoming, "the forward half of the window is unchanged"
    # Data status is reported per anchor and is never a filter: an elapsed date
    # with no computed position is still a date the regulator expects.
    assert {anchor["data_status"] for anchor in body["anchors"]} <= {
        "computed",
        "awaiting_data",
    }


def test_lookback_months_widens_only_the_elapsed_half(db_client: TestClient) -> None:
    _seed()
    narrow = _anchors(db_client, lookback_months=1)
    wide = _anchors(db_client, lookback_months=24)
    assert narrow["lookback_months"] == 1
    assert wide["lookback_months"] == 24

    narrow_elapsed, narrow_upcoming = _split(narrow)
    wide_elapsed, wide_upcoming = _split(wide)
    assert len(narrow_elapsed) == 1
    assert len(wide_elapsed) == 24  # noqa: PLR2004 - the requested lookback
    assert set(narrow_elapsed) < set(wide_elapsed)
    assert narrow_upcoming == wide_upcoming, "the horizon is untouched by the lookback"


def test_horizon_months_widens_only_the_upcoming_half(db_client: TestClient) -> None:
    _seed()
    short = _anchors(db_client, horizon_months=1)
    long = _anchors(db_client, horizon_months=12)

    short_elapsed, short_upcoming = _split(short)
    long_elapsed, long_upcoming = _split(long)
    assert short_elapsed == long_elapsed
    assert set(short_upcoming) < set(long_upcoming)


def test_lookback_months_is_bounded_like_the_horizon(db_client: TestClient) -> None:
    _seed()
    for value in (0, 25, -1):
        response = db_client.get(
            ANCHORS,
            headers=headers(),
            params={"return_code": MONTHLY_RETURN, "lookback_months": value},
        )
        assert response.status_code == 422, f"{value}: {response.text}"
    for value in (1, 24):
        assert _anchors(db_client, lookback_months=value)["lookback_months"] == value


def test_calendar_takes_the_same_window_and_cannot_disagree(
    db_client: TestClient,
) -> None:
    """One authority: the Calendar and the Returns workspace read one window."""
    _seed()
    params = {"lookback_months": 2, "horizon_months": 2}
    anchors = _anchors(db_client, **params)

    response = db_client.get(OBLIGATIONS, headers=headers(), params=params)
    assert response.status_code == 200, response.text
    calendar = response.json()
    assert calendar["lookback_months"] == 2  # noqa: PLR2004 - the requested lookback
    assert calendar["as_of"] == anchors["as_of"]

    calendar_dates = {
        item["reporting_date"]
        for item in calendar["obligations"]
        if item["return_code"] == MONTHLY_RETURN
    }
    assert calendar_dates == {anchor["reporting_date"] for anchor in anchors["anchors"]}


def test_calendar_lookback_is_bounded_too(db_client: TestClient) -> None:
    _seed()
    for value in (0, 25):
        response = db_client.get(
            OBLIGATIONS, headers=headers(), params={"lookback_months": value}
        )
        assert response.status_code == 422, f"{value}: {response.text}"


def test_elapsed_anchors_carry_their_deadline_and_grade(db_client: TestClient) -> None:
    """Why elapsed dates belong in the picker: they are the ones still owed."""
    _seed()
    body = _anchors(db_client)
    as_of = date.fromisoformat(body["as_of"])
    elapsed = [
        anchor
        for anchor in body["anchors"]
        if date.fromisoformat(anchor["reporting_date"]) < as_of
    ]
    assert elapsed
    overdue = [anchor for anchor in elapsed if anchor["rag"] == "overdue"]
    assert overdue, "an unfiled elapsed return grades overdue, not hidden"
    for anchor in elapsed:
        assert date.fromisoformat(anchor["due_date"]) > date.fromisoformat(
            anchor["reporting_date"]
        )
