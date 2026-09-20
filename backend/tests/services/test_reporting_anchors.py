"""Reporting anchors: the regulator owns the reporting date, not ingestion.

Founder review 2026-08-23. The Returns workspace used to select its reporting
date from ``bank_reporting_periods`` — rows created as a side effect of a book
arriving — which made BoG's filing calendar a function of the bank's ingestion
cadence. These tests pin the corrected direction:

    ReturnDefinition ──▶ reporting date ──▶ snapshot lookup (exact, may miss)

and the two properties that follow from it: an anchor exists whether or not the
bank has data for it, and a return is never assembled from a book as of some
other date.

Founder review 2026-09-19 added the third: the offered span is a WINDOW that
runs both ways. Offering only one elapsed period end left a bank whose newest
book was a quarter old unable to select any date it had figures for — an
overdue return is exactly the one still owed — so ``lookback_months`` mirrors
``horizon_months`` and both resolve through one :class:`AnchorWindow`.
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
    DEFAULT_LOOKBACK_MONTHS,
    EVENT_DRIVEN_SNAPSHOT_LIMIT,
    anchor_dates,
    anchor_window,
    computed_snapshot_dates,
    horizon_end_for,
    lookback_start_for,
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
    window = anchor_window(AS_OF, horizon_months=3)
    for definition in REGISTRY.values():
        first = anchor_dates(definition, window)
        assert first == anchor_dates(definition, window)
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

    anchors = anchor_dates(definition, anchor_window(AS_OF, horizon_months=3))
    assert len(anchors) > 8, "a trailing window plus the horizon's Fridays"
    assert {anchor.weekday() for anchor in anchors} == {FRIDAY}
    # Consecutive Fridays — no gaps where a month simply did not end on one.
    for earlier, later in zip(anchors, anchors[1:], strict=False):
        assert (later - earlier).days == 7


def test_daily_anchors_are_business_days_and_stay_bounded() -> None:
    definition = get_definition("DBK-DAILY")
    assert definition is not None and definition.frequency == "daily"
    for horizon_months in (3, 24):
        anchors = anchor_dates(definition, anchor_window(AS_OF, horizon_months=horizon_months))
        # The daily window is a trailing window, independent of the horizon.
        assert len(anchors) == 5
        assert all(anchor.weekday() < FRIDAY + 1 for anchor in anchors)
    # And independent of the lookback too: daily anchors are dense, so the
    # cadence keeps a count cap on top of the shared window.
    for lookback_months in (1, 24):
        capped = anchor_dates(definition, anchor_window(AS_OF, lookback_months=lookback_months))
        assert len(capped) == 5


def test_monthly_anchors_are_month_ends() -> None:
    definition = get_definition("BSD2")
    assert definition is not None and definition.frequency == "monthly"
    anchors = anchor_dates(definition, anchor_window(AS_OF, horizon_months=3))
    assert date(2026, 4, 30) in anchors
    assert all((anchor + timedelta(days=1)).day == 1 for anchor in anchors)


# ---------------------------------------------------------------------------
# The window runs both ways (founder review 2026-09-19)
# ---------------------------------------------------------------------------
#
# The reported defect: on 19 September 2026 a tenant whose newest computed
# position was 30 June 2026 could not generate a monthly return. The picker
# offered 31 August onward — five dates, every one ``awaiting_data`` — because
# the period-end path returned exactly ONE elapsed anchor. 30 June, the date
# with a snapshot, was not in the list at all.

#: The founder's date, and the last reporting date their tenant had figures for.
FOUNDER_AS_OF = date(2026, 9, 19)
FOUNDER_LAST_COMPUTED = date(2026, 6, 30)


def test_monthly_window_offers_the_elapsed_date_the_bank_can_actually_file() -> None:
    """The defect, as a pure function: a quarter-old book must stay selectable."""
    definition = get_definition("BSD2")
    assert definition is not None and definition.frequency == "monthly"

    anchors = anchor_dates(definition, anchor_window(FOUNDER_AS_OF))
    assert FOUNDER_LAST_COMPUTED in anchors, (
        "the one date this tenant has figures for must be offered"
    )
    # Every elapsed month end back to the lookback start, not just the newest.
    assert [anchor for anchor in anchors if anchor < FOUNDER_AS_OF] == [
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
        date(2026, 7, 31),
        date(2026, 8, 31),
    ]


def test_quarterly_window_covers_a_bank_two_quarters_behind() -> None:
    """The default lookback is chosen for exactly this: both open quarter ends."""
    definition = get_definition("LAS-QUARTERLY")
    assert definition is not None and definition.frequency == "quarterly"

    anchors = anchor_dates(definition, anchor_window(FOUNDER_AS_OF))
    elapsed = [anchor for anchor in anchors if anchor < FOUNDER_AS_OF]
    assert elapsed == [date(2026, 3, 31), date(2026, 6, 30)]


def test_lookback_bounds_the_elapsed_half_and_nothing_else() -> None:
    """Widening the lookback adds elapsed dates only; the horizon is untouched."""
    definition = get_definition("BSD2")
    assert definition is not None

    narrow = anchor_dates(definition, anchor_window(FOUNDER_AS_OF, lookback_months=1))
    wide = anchor_dates(definition, anchor_window(FOUNDER_AS_OF, lookback_months=12))
    assert set(narrow) < set(wide)
    upcoming = [anchor for anchor in narrow if anchor >= FOUNDER_AS_OF]
    assert upcoming == [anchor for anchor in wide if anchor >= FOUNDER_AS_OF]
    # No anchor predates the window's own start.
    assert min(wide) >= lookback_start_for(FOUNDER_AS_OF, 12)


def test_horizon_behaviour_is_unchanged() -> None:
    """The forward half is still exactly the period ends up to the horizon end."""
    definition = get_definition("BSD2")
    assert definition is not None

    for horizon_months in (1, 3, 12):
        window_end = horizon_end_for(FOUNDER_AS_OF, horizon_months)
        window = anchor_window(FOUNDER_AS_OF, horizon_months=horizon_months)
        anchors = anchor_dates(definition, window)
        upcoming = [anchor for anchor in anchors if anchor >= FOUNDER_AS_OF]
        assert upcoming[0] == date(2026, 9, 30)
        assert max(upcoming) == window_end
        assert all((anchor + timedelta(days=1)).day == 1 for anchor in upcoming)
        # Widening the horizon never drops an elapsed date.
        elapsed = [anchor for anchor in anchors if anchor < FOUNDER_AS_OF]
        assert elapsed == [
            anchor
            for anchor in anchor_dates(definition, anchor_window(FOUNDER_AS_OF))
            if anchor < FOUNDER_AS_OF
        ]


def test_weekly_and_period_end_share_one_window() -> None:
    """One window concept, not two — both cadences obey the same two bounds."""
    window = anchor_window(FOUNDER_AS_OF)
    for code in ("BSD1", "BSD2"):
        definition = get_definition(code)
        assert definition is not None
        anchors = anchor_dates(definition, window)
        assert anchors, code
        assert min(anchors) >= window.start, code
        assert max(anchors) <= window.end, code
        assert any(anchor < window.as_of for anchor in anchors), code
        assert any(anchor >= window.as_of for anchor in anchors), code


def test_a_long_cadence_keeps_its_most_recent_elapsed_anchor() -> None:
    """A cadence longer than the lookback must not lose the date it owes.

    An annual return's only elapsed anchor can be eleven months old. Dropping it
    because the window is shorter than the cycle would hide an obligation rather
    than bound a list — so the floor that predates this change is kept.
    """
    definition = get_definition("ICAAP-STRESS")
    assert definition is not None and definition.frequency == "annual"

    anchors = anchor_dates(definition, anchor_window(FOUNDER_AS_OF, lookback_months=1))
    assert date(2025, 12, 31) in anchors
    assert date(2025, 12, 31) < lookback_start_for(FOUNDER_AS_OF, 1)


def test_monthly_return_offers_an_elapsed_date_with_its_real_data_status(
    db_session: Session,
) -> None:
    """End to end: the elapsed date is offered AND marked ``computed``.

    The seeded book ends 31 March 2026, so an ``as_of`` three months later is
    the founder's situation exactly. Before the window ran both ways the only
    elapsed date offered was 31 May — ``awaiting_data`` — and the return could
    not be generated at all.
    """
    materialize_canonical_test_book(db_session)
    as_of = date(2026, 6, 19)
    last_computed = date(2026, 3, 31)

    result = calendar.list_return_anchors(
        db_session, MAKER, SAMPLE_BANK_ID, "BSD2", horizon_months=3, as_of=as_of
    )
    assert result.lookback_months == DEFAULT_LOOKBACK_MONTHS
    by_date = {anchor.reporting_date: anchor for anchor in result.anchors}
    assert last_computed in by_date, "the only filable date must be offered"
    assert by_date[last_computed].data_status == "computed"
    # Data status is reported, never used as a filter: the elapsed months with
    # no book are listed too, because BoG's deadline ran for them regardless.
    assert by_date[date(2026, 5, 31)].data_status == "awaiting_data"


def test_weekly_return_offers_an_elapsed_close_with_its_real_data_status(
    db_session: Session,
) -> None:
    """The same, for the weekly cadence: 31 October 2025 is a Friday close AND a
    month end, so the seeded monthly book covers it. It sits twelve weeks before
    the ``as_of`` below — outside the eight-week trailing window this module
    shipped with, inside the shared one."""
    materialize_canonical_test_book(db_session)
    as_of = date(2026, 1, 27)
    computed_close = date(2025, 10, 31)
    assert computed_close.weekday() == FRIDAY

    result = calendar.list_return_anchors(
        db_session, MAKER, SAMPLE_BANK_ID, "BSD1", horizon_months=3, as_of=as_of
    )
    by_date = {anchor.reporting_date: anchor for anchor in result.anchors}
    assert computed_close in by_date
    assert by_date[computed_close].data_status == "computed"
    assert (as_of - computed_close).days > 8 * 7, "outside the old trailing window"


def test_an_overdue_elapsed_anchor_is_graded_overdue(db_session: Session) -> None:
    """The reason elapsed anchors belong in the list, stated as a grade."""
    materialize_canonical_test_book(db_session)
    result = calendar.list_return_anchors(
        db_session, MAKER, SAMPLE_BANK_ID, "BSD2", horizon_months=3, as_of=date(2026, 6, 19)
    )
    overdue = [anchor for anchor in result.anchors if anchor.rag == "overdue"]
    assert overdue, "a return the bank still owes is exactly what a picker must offer"
    assert all(anchor.package_id is None for anchor in overdue)


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
        calendar.list_return_anchors(db_session, MAKER, SAMPLE_BANK_ID, "NOT-A-RETURN", as_of=AS_OF)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Event-driven packs take their as-of date from the computed snapshots
# ---------------------------------------------------------------------------


def _seeded_period_ends(db: Session) -> list[date]:
    return list(
        db.scalars(
            select(BankReportingPeriod.period_end)
            .where(
                BankReportingPeriod.organization_id == DEMO_ORG_ID,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        ).all()
    )


def test_periodic_return_anchors_are_labelled_the_regulators(db_session: Session) -> None:
    """The event-driven path changes nothing for a periodic return."""
    materialize_canonical_test_book(db_session)
    result = calendar.list_return_anchors(
        db_session, MAKER, SAMPLE_BANK_ID, "BSD2", horizon_months=3, as_of=AS_OF
    )
    assert result.reporting_date_source == "regulator_anchor"
    definition = get_definition("BSD2")
    assert definition is not None
    assert [anchor.reporting_date for anchor in result.anchors] == sorted(
        anchor_dates(definition, anchor_window(AS_OF, horizon_months=3)), reverse=True
    )
    assert all(anchor.due_date is not None for anchor in result.anchors)


def test_event_driven_pack_offers_the_computed_snapshot_dates(db_session: Session) -> None:
    """An LRT pack has no regulator anchor; it offers the positions the bank holds."""
    materialize_canonical_test_book(db_session)
    definition = get_definition("LRT-PROFILE")
    assert definition is not None and definition.event_driven
    assert anchor_dates(definition, anchor_window(AS_OF, horizon_months=3)) == []

    result = calendar.list_return_anchors(
        db_session, MAKER, SAMPLE_BANK_ID, "LRT-PROFILE", horizon_months=3, as_of=AS_OF
    )
    assert result.reporting_date_source == "computed_snapshot"
    offered = [anchor.reporting_date for anchor in result.anchors]
    expected = [end for end in _seeded_period_ends(db_session) if end <= AS_OF]
    assert offered == expected[:EVENT_DRIVEN_SNAPSHOT_LIMIT], "newest first, bounded"
    assert offered[0] == max(expected)
    # Every offered date IS a computed position, and none carries a deadline the
    # regulator never set.
    assert all(anchor.data_status == "computed" for anchor in result.anchors)
    assert all(anchor.nearest_computed_before is None for anchor in result.anchors)
    assert all(anchor.due_date is None for anchor in result.anchors)


def test_event_driven_pack_with_nothing_computed_offers_no_dates(db_session: Session) -> None:
    """No snapshot, no date: nothing is fabricated and no earlier book is borrowed."""
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    earliest = min(_seeded_period_ends(db_session))
    before_any_data = earliest - timedelta(days=1)

    assert computed_snapshot_dates(db_session, MAKER, bank, before_any_data) == []
    result = calendar.list_return_anchors(
        db_session, MAKER, SAMPLE_BANK_ID, "LRT-PROFILE", as_of=before_any_data
    )
    assert result.reporting_date_source == "computed_snapshot"
    assert result.anchors == []
    assert result.ineligible_reason is None


def test_computed_snapshot_dates_never_offer_a_future_position(db_session: Session) -> None:
    """A snapshot dated after ``as_of`` is not a position the bank holds yet."""
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    ends = _seeded_period_ends(db_session)
    assert len(ends) >= 2
    as_of = ends[1]
    assert computed_snapshot_dates(db_session, MAKER, bank, as_of)[0] == ends[1]
    assert ends[0] not in computed_snapshot_dates(db_session, MAKER, bank, as_of)


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
