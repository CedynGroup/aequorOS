"""Reporting anchors: the regulator owns the reporting date, not ingestion.

Founder review 2026-08-23. The Returns workspace used to select its reporting
date from ``bank_reporting_periods`` — rows created as a side effect of a book
arriving — which made BoG's filing calendar a function of the bank's ingestion
cadence. These tests pin the corrected direction:

    ReturnDefinition ──▶ reporting date ──▶ snapshot lookup (exact, may miss)

and the two properties that follow from it: an anchor exists whether or not the
bank has data for it, and a return is never assembled from a book as of some
other date.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services.regulatory_reporting import calendar, generation
from app.services.regulatory_reporting.anchors import (
    anchor_dates,
    horizon_end_for,
    snapshot_coverage,
)
from app.services.regulatory_reporting.common import get_snapshot_for_reporting_date
from app.services.regulatory_reporting.registry import REGISTRY, get_definition
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
#: A Tuesday, deliberately not a month end and not the weekly anchor weekday.
AS_OF = date(2026, 3, 31)
FRIDAY = 4


def _bank(db: Session) -> Bank:
    bank = db.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    return bank


# ---------------------------------------------------------------------------
# The anchors are the regulator's
# ---------------------------------------------------------------------------


def test_anchors_are_a_pure_function_of_the_return_definition() -> None:
    """No database, no tenant, no ingestion — the same dates for every bank."""
    horizon = horizon_end_for(AS_OF, 3)
    for definition in REGISTRY.values():
        first = anchor_dates(definition, AS_OF, horizon)
        assert first == anchor_dates(definition, AS_OF, horizon)
        if definition.event_driven:
            # An event-driven pack has no periodic cycle; expanding its nominal
            # frequency would fabricate obligations that do not exist.
            assert first == []
        else:
            assert first, f"{definition.code} must offer at least one reporting date"
            assert first == sorted(first)


def test_weekly_anchors_are_friday_closes_not_month_ends() -> None:
    """The defect this module exists for.

    ``BSD1`` is a weekly return. Under the old model its reporting date had to
    be a ``bank_reporting_periods.period_end``, and on the primary the reference
    tenant had 19 Friday period-ends against 517 Fridays in its span — 17 of
    those only because the month happened to end on a Friday. Weekly returns
    were 96% unfileable.
    """
    definition = get_definition("BSD1")
    assert definition is not None and definition.frequency == "weekly"

    anchors = anchor_dates(definition, AS_OF, horizon_end_for(AS_OF, 3))
    assert len(anchors) > 8, "a trailing window plus the horizon's Fridays"
    assert {anchor.weekday() for anchor in anchors} == {FRIDAY}
    # Consecutive Fridays — no gaps where a month simply did not end on one.
    for earlier, later in zip(anchors, anchors[1:], strict=False):
        assert (later - earlier).days == 7


def test_daily_anchors_are_business_days_and_stay_bounded() -> None:
    definition = get_definition("DBK-DAILY")
    assert definition is not None and definition.frequency == "daily"
    for horizon_months in (3, 24):
        anchors = anchor_dates(definition, AS_OF, horizon_end_for(AS_OF, horizon_months))
        # The daily window is a trailing window, independent of the horizon.
        assert len(anchors) == 5
        assert all(anchor.weekday() < FRIDAY + 1 for anchor in anchors)


def test_monthly_anchors_are_month_ends() -> None:
    definition = get_definition("BSD2")
    assert definition is not None and definition.frequency == "monthly"
    anchors = anchor_dates(definition, AS_OF, horizon_end_for(AS_OF, 3))
    assert date(2026, 4, 30) in anchors
    assert all((anchor + timedelta(days=1)).day == 1 for anchor in anchors)


# ---------------------------------------------------------------------------
# Coverage is reported, never substituted
# ---------------------------------------------------------------------------


def test_anchor_list_offers_dates_the_bank_has_no_data_for(db_session: Session) -> None:
    """An obligation the bank cannot yet meet is still an obligation.

    Before this change such a date was simply absent from the picker, so a
    weekly BoG deadline the bank was going to miss was invisible in the product.
    """
    materialize_canonical_test_book(db_session)
    result = calendar.list_return_anchors(
        db_session, MAKER, SAMPLE_BANK_ID, "BSD1", horizon_months=3, as_of=AS_OF
    )
    assert result.frequency == "weekly"
    assert result.anchors, "weekly anchors exist regardless of what was ingested"
    statuses = {anchor.data_status for anchor in result.anchors}
    assert "awaiting_data" in statuses, (
        "the seeded book is monthly month-ends, so most Friday closes have no position"
    )
    awaiting = [a for a in result.anchors if a.data_status == "awaiting_data"]
    # Each uncovered anchor still says what the bank DOES have, for the message —
    # a value that is reported, never used as the figures.
    assert any(anchor.nearest_computed_before is not None for anchor in awaiting)
    assert all(
        anchor.nearest_computed_before is None
        for anchor in result.anchors
        if anchor.data_status == "computed"
    )


def test_anchor_list_marks_a_covered_month_end_as_computed(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    result = calendar.list_return_anchors(
        db_session, MAKER, SAMPLE_BANK_ID, "BSD2", horizon_months=1, as_of=AS_OF
    )
    by_date = {anchor.reporting_date: anchor for anchor in result.anchors}
    seeded = db_session.scalars(
        select(BankReportingPeriod.period_end).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
        )
    ).all()
    overlap = set(by_date) & set(seeded)
    assert overlap, "the seed must cover at least one month-end anchor in range"
    for reporting_date in overlap:
        assert by_date[reporting_date].data_status == "computed"


def test_snapshot_coverage_is_exact_not_nearest(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    covered_end = db_session.scalar(
        select(BankReportingPeriod.period_end)
        .where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )
    assert covered_end is not None
    day_after = covered_end + timedelta(days=1)

    coverage = snapshot_coverage(db_session, MAKER, bank, [covered_end, day_after])
    assert coverage[covered_end].covered is True
    assert coverage[day_after].covered is False
    # The nearest earlier snapshot is reported for the message only.
    assert coverage[day_after].nearest_before == covered_end


def test_ineligible_return_says_why_instead_of_offering_dates(db_session: Session) -> None:
    """An empty anchor list must carry its reason.

    The Returns workspace renders ``ineligible_reason`` verbatim, so an SDI that
    opened a bank-only BSD code sees the eligibility authority's own words
    rather than a blank picker.
    """
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    bank.institution_type = "savings_and_loans"
    db_session.flush()

    result = calendar.list_return_anchors(
        db_session, MAKER, SAMPLE_BANK_ID, "BSD1", horizon_months=3, as_of=AS_OF
    )
    assert result.anchors == []
    assert result.ineligible_reason, "an empty list without a reason is the silence to avoid"
    assert "class" in result.ineligible_reason.lower()


def test_unregistered_return_is_404_not_an_empty_anchor_list(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    with pytest.raises(HTTPException) as exc_info:
        calendar.list_return_anchors(
            db_session, MAKER, SAMPLE_BANK_ID, "NOT-A-RETURN", as_of=AS_OF
        )
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Generation never borrows another date's book
# ---------------------------------------------------------------------------


def test_generation_refuses_a_date_with_no_computed_position(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    absent = date(1990, 1, 31)
    assert not db_session.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == absent,
        )
    )
    with pytest.raises(HTTPException) as exc_info:
        get_snapshot_for_reporting_date(
            db_session, MAKER, bank, absent, return_code="BSD1", frequency="weekly"
        )
    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict), "the refusal is structured, not a bare string"
    detail = cast("dict[str, str]", detail)
    assert detail["error_code"] == "no_computed_position"
    assert absent.isoformat() in detail["message"]
    assert "weekly close" in detail["message"]


def test_daily_return_no_longer_borrows_an_earlier_book(db_session: Session) -> None:
    """The fail-open this change removed.

    Until 2026-08-23 a daily return resolved to "the latest period ending on or
    before" its reporting date. A bank on a monthly ingestion cadence would have
    filed a month-old book as that business day's position, with the stale date
    visible only inside the snapshot.
    """
    materialize_canonical_test_book(db_session)
    latest = db_session.scalar(
        select(BankReportingPeriod.period_end)
        .where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )
    assert latest is not None
    later_business_day = latest + timedelta(days=1)

    with pytest.raises(HTTPException) as exc_info:
        generation.generate_package(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            RegulatoryPackageCreate(
                return_code="DBK-DAILY", reporting_date=later_business_day, basis="solo"
            ),
        )
    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict), "the refusal is structured, not a bare string"
    detail = cast("dict[str, str]", detail)
    assert detail["error_code"] == "no_computed_position"
    assert latest.isoformat() in detail["message"], "names what the bank does have"
    assert "not a substitute" in detail["message"]


# ---------------------------------------------------------------------------
# One authority: the calendar and the workspace cannot disagree
# ---------------------------------------------------------------------------


def test_calendar_and_returns_workspace_offer_the_same_dates(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    obligations = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, horizon_months=3, as_of=AS_OF
    )
    by_code: dict[str, set[date]] = {}
    for item in obligations.obligations:
        by_code.setdefault(item.return_code, set()).add(item.reporting_date)
    assert by_code, "the sample bank has an eligible return set"

    for return_code, calendar_dates in by_code.items():
        anchors = calendar.list_return_anchors(
            db_session, MAKER, SAMPLE_BANK_ID, return_code, horizon_months=3, as_of=AS_OF
        )
        assert {anchor.reporting_date for anchor in anchors.anchors} == calendar_dates, (
            f"{return_code}: the calendar and the Returns workspace read one authority"
        )


def test_calendar_reports_data_status_per_obligation(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    obligations = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, horizon_months=3, as_of=AS_OF
    )
    assert obligations.obligations
    assert {item.data_status for item in obligations.obligations} <= {
        "computed",
        "awaiting_data",
    }
    # A future anchor cannot have a position yet — that is normal, not an error.
    future = [item for item in obligations.obligations if item.reporting_date > AS_OF]
    assert all(item.data_status == "awaiting_data" for item in future)
