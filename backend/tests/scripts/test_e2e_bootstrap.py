"""The e2e book reaches the reporting anchor currently due.

The Returns workspace opens on the regulator's latest anchor on or before today, and a
return is only ever generated from the exact snapshot as of that date. The
canonical book ends at a fixed month, so the hermetic e2e bootstrap carries it
forward month by month; these tests pin that the carry-forward lands on the
anchor the workspace selects, repeats the canonical book without altering it,
and is a pure extension of the periods the golden suites pin.

The hermetic e2e fixture users are distinct identities, in both halves.
`scripts/e2e_bootstrap.py` creates one `users` row per fixture and enrols a
signing key for each; `dashboard/e2e/support/mint.ts` mints tokens for the same
identities by id. Two names sharing a UUID collapse to one row, the second key
enrolment refuses, and the Playwright stack cannot boot at all.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import BankFinancialFact, BankReportingPeriod
from app.services.regulatory_reporting import calendar
from app.services.regulatory_reporting.anchors import anchor_dates, horizon_end_for
from app.services.regulatory_reporting.registry import get_definition
from scripts.e2e_bootstrap import E2E_USERS, latest_month_end_on_or_before
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    PERIOD_COUNT,
    SAMPLE_BANK_ID,
    extend_canonical_test_book,
    materialize_canonical_test_book,
)

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)


def _period_ends(db: Session) -> list[date]:
    return list(
        db.scalars(
            select(BankReportingPeriod.period_end)
            .where(
                BankReportingPeriod.organization_id == DEMO_ORG_ID,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end)
        )
    )


def _fact_signature(db: Session, period_end: date) -> set[tuple[str, str, str]]:
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == period_end,
        )
    )
    facts = db.scalars(
        select(BankFinancialFact).where(BankFinancialFact.reporting_period_id == period_id)
    )
    return {(fact.fact_group, fact.category, str(fact.amount)) for fact in facts}


@pytest.mark.parametrize(
    "today",
    [date(2026, 9, 19), date(2026, 9, 30), date(2026, 10, 1), date(2027, 1, 1)],
)
def test_latest_month_end_on_or_before_matches_the_workspace_anchor(today: date) -> None:
    """The bootstrap's target date is the anchor the workspace defaults to."""
    monthly = get_definition("LCR-NSFR")
    assert monthly is not None and monthly.frequency == "monthly"
    anchors = sorted(anchor_dates(monthly, today, horizon_end_for(today, 3)), reverse=True)
    selected = next(anchor for anchor in anchors if anchor <= today)
    assert latest_month_end_on_or_before(today) == selected


def test_carry_forward_reaches_the_anchor_and_leaves_the_canonical_book_alone(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    canonical_ends = _period_ends(db_session)
    assert len(canonical_ends) == PERIOD_COUNT
    canonical_latest = canonical_ends[-1]
    canonical_signature = _fact_signature(db_session, canonical_latest)

    through = date(2026, 8, 31)
    appended = extend_canonical_test_book(db_session, through=through)

    assert appended == [
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
        date(2026, 7, 31),
        date(2026, 8, 31),
    ]
    assert _period_ends(db_session) == canonical_ends + appended
    # The same book, later as-of dates: every appended snapshot is the canonical
    # latest fact set, and the canonical latest itself is untouched.
    assert _fact_signature(db_session, canonical_latest) == canonical_signature
    for period_end in appended:
        assert _fact_signature(db_session, period_end) == canonical_signature
    fact_rows = db_session.scalar(
        select(func.count())
        .select_from(BankFinancialFact)
        .where(BankFinancialFact.bank_id == SAMPLE_BANK_ID)
    )
    assert fact_rows == len(canonical_signature) * (PERIOD_COUNT + len(appended))


def test_carry_forward_is_a_no_op_inside_the_canonical_span(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    canonical_ends = _period_ends(db_session)
    assert extend_canonical_test_book(db_session, through=canonical_ends[-1]) == []
    assert _period_ends(db_session) == canonical_ends


@pytest.mark.parametrize(
    ("today", "expected_anchor"),
    [
        (date(2026, 9, 19), date(2026, 8, 31)),
        (date(2026, 9, 30), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 9, 30)),
        (date(2027, 1, 1), date(2026, 12, 31)),
    ],
)
def test_the_workspace_anchor_is_computed_after_the_carry_forward(
    db_session: Session, today: date, expected_anchor: date
) -> None:
    """What the journeys see: the default anchor is generate-able, not awaiting data."""
    materialize_canonical_test_book(db_session)
    extend_canonical_test_book(db_session, through=latest_month_end_on_or_before(today))
    for code in ("LCR-NSFR", "LMT"):
        result = calendar.list_return_anchors(
            db_session, MAKER, SAMPLE_BANK_ID, code, horizon_months=3, as_of=today
        )
        selected = next(anchor for anchor in result.anchors if anchor.reporting_date <= today)
        assert selected.reporting_date == expected_anchor
        assert selected.data_status == "computed"


_MINT_TS = Path(__file__).resolve().parents[2] / "dashboard" / "e2e" / "support" / "mint.ts"
_MINT_USER = re.compile(r"^  (\w+): \{\n    id: \"([0-9a-f-]{36})\",", re.MULTILINE)


def test_every_fixture_user_has_its_own_uuid() -> None:
    duplicated = {
        user_id: [name for name, candidate in E2E_USERS.items() if candidate == user_id]
        for user_id, count in Counter(E2E_USERS.values()).items()
        if count > 1
    }
    assert not duplicated, f"fixture users sharing a UUID: {duplicated}"


def test_token_mint_mirrors_the_bootstrap_identities() -> None:
    minted = {name: user_id for name, user_id in _MINT_USER.findall(_MINT_TS.read_text())}
    assert minted == {name: str(user_id) for name, user_id in E2E_USERS.items()}
