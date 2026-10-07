"""A bank-certified calculated measure, compiled and EXECUTED (D-195 … D-206).

The fixture is five month-end books and two loan events, so every expected number
in this file is hand arithmetic over rows that are written out below. That is the
point: a calculated measure is a figure a bank certifies and then files decisions
against, so "the compiler emits the shape we expected" is not enough — the number
has to be the right number.

Three properties this file exists to hold, each of which would be a defect nobody
sees if it broke:

* **the declared period is the period.** ``PCT_CHANGE([m:x], MONTH)`` compares the
  reporting month with the month before it wherever that measure appears, and a
  query that cannot be answered at month grain is refused BY NAME rather than
  answered at whatever grain it happens to have (D-195);
* **a missing figure stays missing.** No ``?? 0``, no zero denominator becoming
  zero, no "flat". A zero that the bank actually reported is 0; an absence is no
  value; and the two are proved apart on two adjacent reporting dates;
* **a formula is not a way round a binding.** The figures a statement reads come
  from the SERVER's parse of the certified text, so the authorization walk is given
  exactly the set the statement will read — proved here by doctoring the stored
  ``referenced_members`` column and by withholding one of the figures.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, get_args
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.domain.bi import expr
from app.domain.bi.catalogue import Catalogue, catalogue
from app.domain.bi.catalogue.members import VALUE_TYPES
from app.models import AuthorizationBinding, Bank, User
from app.models.bi import (
    BiAggPositionDaily,
    BiDimDate,
    BiFactLoanEvent,
    BiFactPositionDaily,
)
from app.models.bi_content import MEASURE_STATES, BiMeasure
from app.schemas.bi import (
    BI_MAX_MEASURES,
    BiDateRange,
    BiLayoutItem,
    BiPackQuery,
    BiPackWidget,
    BiPivot,
    BiQuery,
    BiResultColumnFormat,
    BiSort,
)
from app.schemas.bi_content import BiDashboardSpec
from app.services import authorization as authorization_service
from app.services.bi import compiler, content
from app.services.bi.compiler import compile_query, expand_calculated_measures
from app.services.bi.errors import InvalidQuery, UnknownMember
from app.services.bi.execution import execute
from tests.support.helpers import ORG_1, USER_1, USER_2

BUILT_AT = datetime(2026, 10, 1, 2, tzinfo=UTC)

#: Five month ends. Every one is the last date with data in its month, which is
#: what ``bi_dim_date.is_last_in_month`` means (D-014) and what a period comparison
#: requires of the date it is read at.
MAY = date(2026, 5, 31)
JUN = date(2026, 6, 30)
JUL = date(2026, 7, 31)
AUG = date(2026, 8, 31)
SEP = date(2026, 9, 30)

#: Gross loans per month end. The growth from August to September is 30 on 120,
#: which is 0.25 exactly — chosen so a floating result is still unambiguous.
LOANS_BY_MONTH = {
    MAY: Decimal("60"),
    JUN: Decimal("80"),
    JUL: Decimal("100"),
    AUG: Decimal("120"),
    SEP: Decimal("150"),
}

#: Deposits. May reports a REAL ZERO; June reports nothing at all. That pair is
#: how "0 is a figure" and "absence is not 0" are told apart.
DEPOSITS_BY_MONTH = {
    MAY: Decimal("0"),
    JUL: Decimal("400"),
    AUG: Decimal("480"),
    SEP: Decimal("600"),
}

#: Loan movements, which are a FLOW: added up across the period rather than read at
#: its close. August totals 30, September 45.
EVENTS = (
    (date(2026, 8, 12), Decimal("10")),
    (date(2026, 8, 20), Decimal("20")),
    (date(2026, 9, 10), Decimal("45")),
)

LOANS = "loans.balance_rc"
DEPOSITS = "deposits.balance_rc"
MOVEMENTS = "events.amount_rc"
OBLIGOR = "counterparty.name"

#: 400 days, so a month reach of 12 and a quarter reach of 4 are writable. The
#: shipped default is 95 days and is exercised where the bound itself is the
#: subject (``tests/domain/bi/test_expr.py``).
RETENTION_DAYS = 400


@pytest.fixture(autouse=True)
def _long_retention(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A retention window long enough to write a month-on-month formula down.

    Set on the settings object the compiler reads rather than on the environment,
    because ``get_settings`` is cached for the process.
    """

    monkeypatch.setattr(get_settings().bi, "daily_retention_days", RETENTION_DAYS)
    yield


def _position(bank: Bank, as_of: date, position_type: str, balance: Decimal) -> BiFactPositionDaily:
    return BiFactPositionDaily(
        as_of_date=as_of,
        snapshot_id=uuid4(),
        position_id=uuid4(),
        organization_id=bank.organization_id,
        bank_id=bank.id,
        source_system="API_PUSH",
        source_reference=f"ref-{uuid4().hex[:8]}",
        position_type=position_type,
        currency=bank.currency,
        balance_native=balance,
        balance_rc=balance,
        fx_unconverted=False,
        classification_exposure_rc=balance,
        non_performing=False,
        product_family="retail_loans" if position_type == "LOAN" else "retail_deposits",
        deposit_account_type=None if position_type == "LOAN" else "demand",
        builder_version=1,
        built_at=BUILT_AT,
    )


#: ``bi_agg_position_daily``'s grain, restated from ``bi_contracts.md`` so the
#: aggregate below is built from the FACTS by its definition rather than copied
#: from the builder. The compiler serves an additive sum off the aggregate
#: whenever it can, so a fixture without these rows would silently test nothing.
_AGG_GRAIN = (
    "position_type",
    "product_family",
    "branch_code",
    "currency",
    "ifrs9_stage",
    "dpd_band",
    "grade",
    "deposit_account_type",
)


def _aggregate(fact: BiFactPositionDaily) -> BiAggPositionDaily:
    """One aggregate row per fact row: this fixture has one position per grain."""
    return BiAggPositionDaily(
        as_of_date=fact.as_of_date,
        id=uuid4(),
        organization_id=fact.organization_id,
        bank_id=fact.bank_id,
        **{column: getattr(fact, column) for column in _AGG_GRAIN},
        row_count=1,
        balance_rc_sum=fact.balance_rc or Decimal("0"),
        classification_exposure_rc_sum=fact.classification_exposure_rc or Decimal("0"),
        non_performing_exposure_rc_sum=Decimal("0"),
        provision_required_rc_sum=Decimal("0"),
        provision_held_rc_sum=Decimal("0"),
        collateral_rc_sum=Decimal("0"),
        rate_x_balance_rc_sum=Decimal("0"),
        fx_unconverted_count=0,
        builder_version=1,
        built_at=BUILT_AT,
    )


def _calendar(bank: Bank, day: date, *, has_data: bool) -> BiDimDate:
    quarter_month = 3 * ((day.month - 1) // 3) + 1
    return BiDimDate(
        organization_id=bank.organization_id,
        bank_id=bank.id,
        date=day,
        has_data=has_data,
        is_last_in_month=has_data,
        is_last_in_quarter=has_data and day.month in (3, 6, 9, 12),
        is_last_in_year=has_data and day.month == 12,
        calendar_month=day.replace(day=1),
        calendar_quarter=date(day.year, quarter_month, 1),
        calendar_year=day.year,
        fiscal_year=day.year,
        fiscal_quarter=(day.month - 1) // 3 + 1,
        builder_version=1,
        built_at=BUILT_AT,
    )


@pytest.fixture
def mart(db_session: Session) -> Bank:
    """One institution, five month-end books, two months of loan movements."""

    bank = Bank(
        organization_id=ORG_1,
        name="Calculated Bank",
        short_name="calc",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.flush()
    for as_of, balance in LOANS_BY_MONTH.items():
        loan = _position(bank, as_of, "LOAN", balance)
        db_session.add_all([loan, _aggregate(loan)])
    for as_of, balance in DEPOSITS_BY_MONTH.items():
        deposit = _position(bank, as_of, "DEPOSIT", balance)
        db_session.add_all([deposit, _aggregate(deposit)])
    for day, amount in EVENTS:
        db_session.add(
            BiFactLoanEvent(
                event_date=day,
                event_id=uuid4(),
                organization_id=ORG_1,
                bank_id=bank.id,
                event_type="DISBURSEMENT",
                source_system="API_PUSH",
                source_reference=f"evt-{uuid4().hex[:8]}",
                position_source_reference="L1",
                amount_native=amount,
                currency=bank.currency,
                amount_rc=amount,
                fx_unconverted=False,
                attribution_basis="snapshot_on_or_before",
                builder_version=1,
                built_at=BUILT_AT,
            )
        )
    # A contiguous calendar from the first month end to the last, with data only on
    # the month ends — which is how a bank that feeds last-business-day books looks.
    day = date(2026, 5, 1)
    while day <= date(2026, 9, 30):
        db_session.add(_calendar(bank, day, has_data=day in LOANS_BY_MONTH))
        day += _ONE_DAY
    db_session.flush()
    return bank


_ONE_DAY = date(2026, 1, 2) - date(2026, 1, 1)


@pytest.fixture
def cat() -> Catalogue:
    return catalogue()


def _certify(  # noqa: PLR0913 - one complete certified row, stated explicitly
    db: Session,
    bank: Bank,
    *,
    key: str,
    expression: str,
    label: str = "Certified figure",
    value_type: str = "fraction",
    state: str = "bank_certified",
) -> BiMeasure:
    """A calculated measure on the row, in ``state``.

    Written directly rather than through ``content.create_measure`` +
    ``propose_measure`` + ``decide_promotion`` so the compiler tests do not also
    depend on the promotion path having granted the right bindings; the promotion
    itself is ``tests/services/bi/test_bi_content.py``'s subject.
    """

    now = datetime.now(UTC)
    certified = state == "bank_certified"
    digest = content.expression_digest(expression)
    row = BiMeasure(
        organization_id=ORG_1,
        bank_id=bank.id,
        measure_key=key,
        owner_user_id=USER_1,
        label=label,
        description="",
        expression=expression,
        expression_digest=digest,
        referenced_members=list(
            expr.referenced_members(expr.parse(expression, retention_days=None))
        ),
        value_type=value_type,
        favourable_direction="neutral",
        state=state,
        proposed_by_user_id=USER_1 if state != "personal" else None,
        proposed_at=now if state != "personal" else None,
        proposed_expression_digest=digest if state != "personal" else None,
        proposal_reason="Exercise the compiler." if state != "personal" else None,
        approved_by_user_id=USER_2 if certified else None,
        approved_at=now if certified else None,
        approved_expression=expression if certified else None,
        approved_expression_digest=digest if certified else None,
        approval_reason="Certified for the institution." if certified else None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _query(*measures: str, **overrides: Any) -> BiQuery:
    payload: dict[str, Any] = {"measures": list(measures), "time": {"as_of": SEP}}
    payload.update(overrides)
    return BiQuery.model_validate(payload)


def _one_row(db: Session, cat: Catalogue, bank: Bank, query: BiQuery) -> list[Any]:
    compiled = compile_query(db, cat, query, organization_id=ORG_1, bank_id=bank.id)
    result = execute(db, compiled, timeout_ms=10_000, row_cap=100)
    assert len(result.rows) == 1, result.rows
    return list(result.rows[0])


def _refuse(db: Session, cat: Catalogue, bank: Bank, query: BiQuery) -> InvalidQuery:
    with pytest.raises(InvalidQuery) as caught:
        compile_query(db, cat, query, organization_id=ORG_1, bank_id=bank.id)
    return caught.value


# --- the number ---------------------------------------------------------------------------


def test_a_certified_calculated_measure_returns_the_right_number(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Loans 150 over deposits 600 at the September book: 0.25, and one column."""

    _certify(
        db_session,
        mart,
        key="custom.loans_to_deposits",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
        label="Loans to deposits",
    )
    compiled = compile_query(
        db_session,
        cat,
        _query("custom.loans_to_deposits"),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert [spec.id for spec in compiled.columns] == ["custom.loans_to_deposits"]
    assert compiled.columns[0].label == "Loans to deposits"
    assert compiled.columns[0].format == "fraction"
    assert compiled.columns[0].member_id == "custom.loans_to_deposits"

    result = execute(db_session, compiled, timeout_ms=10_000, row_cap=100)
    assert [float(value) for value in result.rows[0]] == [0.25]


def test_a_month_on_month_change_compares_the_two_month_end_books(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """September 150 against August 120: 30 on 120, which is 0.25.

    A stock, so "the month before" is that month's closing book — the last date
    with data in it — and not a sum of its days.
    """

    _certify(
        db_session,
        mart,
        key="custom.loan_growth",
        expression=f"PCT_CHANGE([m:{LOANS}], MONTH)",
        label="Loan growth on the month",
    )
    assert [
        float(value) for value in _one_row(db_session, cat, mart, _query("custom.loan_growth"))
    ] == [0.25]


def test_a_lag_reads_the_period_it_names_and_not_the_one_before_it(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Two months before September is July, which closed at 100."""

    _certify(
        db_session,
        mart,
        key="custom.loans_two_months_ago",
        expression=f"LAG([m:{LOANS}], 2, MONTH)",
        value_type="amount",
    )
    assert [
        float(value)
        for value in _one_row(db_session, cat, mart, _query("custom.loans_two_months_ago"))
    ] == [100.0]


def test_a_nested_lag_composes_rather_than_accumulating(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """``LAG(PCT_CHANGE(x, MONTH), 2, MONTH)`` is July against June: 20 on 80 = 0.25.

    The trap it guards is reading one and two months back instead of two and three
    — silently a whole period off, and arithmetically plausible either way.
    """

    _certify(
        db_session,
        mart,
        key="custom.growth_two_months_ago",
        expression=f"LAG(PCT_CHANGE([m:{LOANS}], MONTH), 2, MONTH)",
    )
    assert [
        float(value)
        for value in _one_row(db_session, cat, mart, _query("custom.growth_two_months_ago"))
    ] == [0.25]


def test_a_quarter_on_quarter_change_uses_the_quarter_boundaries(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """September ends Q3 (150); Q2 closed in June at 80. 70 on 80 is 0.875."""

    _certify(
        db_session,
        mart,
        key="custom.loan_growth_q",
        expression=f"PCT_CHANGE([m:{LOANS}], QUARTER)",
    )
    assert [
        float(value) for value in _one_row(db_session, cat, mart, _query("custom.loan_growth_q"))
    ] == [0.875]


def test_a_flow_is_added_up_across_the_period_rather_than_read_at_its_close(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """September movements total 45, August 10 + 20 = 30: 15 on 30 is 0.5.

    The reporting date itself carries no movement (the events are on the 12th, 20th
    and 10th), so a period comparison that read the DATE rather than the PERIOD
    would return no value here. That is the stock/flow half of D-195, and it
    follows from the base measure's declared ``time_behaviour`` rather than from a
    rule of its own.
    """

    _certify(
        db_session,
        mart,
        key="custom.movement_growth",
        expression=f"PCT_CHANGE([m:{MOVEMENTS}], MONTH)",
    )
    assert [
        float(value) for value in _one_row(db_session, cat, mart, _query("custom.movement_growth"))
    ] == [0.5]


def test_a_calculated_measure_sits_beside_the_figures_it_is_made_of(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """One result, three columns, in the order asked for — and the plain measure
    keeps the reporting date's own value while the formula reads whole periods."""

    _certify(
        db_session,
        mart,
        key="custom.loan_growth",
        expression=f"PCT_CHANGE([m:{LOANS}], MONTH)",
    )
    row = _one_row(db_session, cat, mart, _query(LOANS, "custom.loan_growth", DEPOSITS))
    assert [float(value) for value in row] == [150.0, 0.25, 600.0]


def test_a_dimension_breakdown_still_works_under_a_period_comparison(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Grouping by a non-time dimension is untouched: the period axis is the
    formula's, the row axis is the query's.

    The deposits group has no loan population at all, so its growth is NO VALUE
    rather than 0 — the same NULL-versus-zero rule every other measure follows,
    reached through a formula.
    """

    _certify(
        db_session,
        mart,
        key="custom.loan_growth",
        expression=f"PCT_CHANGE([m:{LOANS}], MONTH)",
    )
    compiled = compile_query(
        db_session,
        cat,
        _query("custom.loan_growth", dimensions=["product.family"]),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    result = execute(db_session, compiled, timeout_ms=10_000, row_cap=100)
    assert [(row[0], row[1]) for row in result.rows] == [
        ("retail_deposits", None),
        ("retail_loans", 0.25),
    ]


def test_a_calculated_measure_can_be_sorted_by(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    _certify(
        db_session,
        mart,
        key="custom.loan_growth",
        expression=f"PCT_CHANGE([m:{LOANS}], MONTH)",
    )
    query = _query(
        "custom.loan_growth",
        dimensions=["product.family"],
        sort=[BiSort(member="custom.loan_growth", direction="desc").model_dump()],
    )
    compiled = compile_query(db_session, cat, query, organization_id=ORG_1, bank_id=mart.id)
    result = execute(db_session, compiled, timeout_ms=10_000, row_cap=100)
    # Descending by the formula, and the group with no value trails the ranked one.
    assert [row[1] for row in result.rows] == [0.25, None]


def test_a_formula_with_no_period_function_can_still_be_compared(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """``compare_to`` is refused only for a formula that declares its OWN period.

    A formula that is plain arithmetic has no comparison of its own, so the query's
    comparison applies to it exactly as it does to a catalogue measure: September
    150/600 and August 120/480 are both 0.25, so the change is zero — and a zero
    that the two books really produce is a figure, not a gap.
    """

    _certify(
        db_session,
        mart,
        key="custom.loans_to_deposits",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
    )
    compiled = compile_query(
        db_session,
        cat,
        _query("custom.loans_to_deposits", time={"as_of": SEP, "compare_to": AUG}),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert [column.id for column in compiled.columns] == [
        "custom.loans_to_deposits",
        "custom.loans_to_deposits|prior",
        "custom.loans_to_deposits|delta",
        "custom.loans_to_deposits|delta_pct",
    ]
    result = execute(db_session, compiled, timeout_ms=10_000, row_cap=100)
    assert [float(value) for value in result.rows[0]] == [0.25, 0.25, 0.0, 0.0]


def test_top_n_can_rank_by_a_calculated_measure(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """The ranked subquery is the same assembly, so it plans a formula the same way.

    The collapsed remainder keeps its own row and its NO VALUE: "Other" here is the
    deposits family, which has no loan population to have grown.
    """

    _certify(
        db_session,
        mart,
        key="custom.loan_growth",
        expression=f"PCT_CHANGE([m:{LOANS}], MONTH)",
    )
    compiled = compile_query(
        db_session,
        cat,
        _query(
            "custom.loan_growth",
            dimensions=["product.family"],
            top_n={"dimension": "product.family", "n": 1},
        ),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    result = execute(db_session, compiled, timeout_ms=10_000, row_cap=100)
    assert [(row[0], row[1]) for row in result.rows] == [("Other", None), ("retail_loans", 0.25)]


# --- a missing figure stays missing -------------------------------------------------------


def test_a_reported_zero_and_an_absence_are_not_the_same_answer(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """The whole of the division-and-absence rule, on two adjacent dates.

    May reports deposits of ZERO, so a condition about them is answerable and
    ``IF`` returns the false branch — a real 0. June reports no deposits at all, so
    the condition is unknown and the answer is NO VALUE. A renderer that coalesced
    either into the other would show a bank a figure it never reported.
    """

    _certify(
        db_session,
        mart,
        key="custom.has_deposits",
        expression=f"IF([m:{DEPOSITS}] > 0, 1, 0)",
        value_type="count",
    )
    assert _one_row(db_session, cat, mart, _query("custom.has_deposits", time={"as_of": MAY})) == [
        0
    ]
    assert _one_row(db_session, cat, mart, _query("custom.has_deposits", time={"as_of": JUN})) == [
        None
    ]


@pytest.mark.parametrize("form", ["SAFE_DIV([m:{n}], [m:{d}])", "[m:{n}] / [m:{d}]"])
def test_dividing_by_a_zero_or_an_absence_gives_no_value_either_way(
    db_session: Session, cat: Catalogue, mart: Bank, form: str
) -> None:
    """``SAFE_DIV`` is the form the language teaches, and the bare ``/`` must not be
    worse: a zero denominator is no value, never zero, and never a database error
    that would fail the whole result."""

    _certify(
        db_session,
        mart,
        key="custom.ratio",
        expression=form.format(n=LOANS, d=DEPOSITS),
    )
    assert _one_row(db_session, cat, mart, _query("custom.ratio", time={"as_of": MAY})) == [None]
    assert _one_row(db_session, cat, mart, _query("custom.ratio", time={"as_of": JUN})) == [None]


def test_a_change_on_a_period_with_no_book_is_no_value_and_not_flat(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """May is the first month with data, so April has none. The change is unknown —
    not 0, which would read as "the book did not move"."""

    _certify(
        db_session,
        mart,
        key="custom.loan_growth",
        expression=f"PCT_CHANGE([m:{LOANS}], MONTH)",
    )
    assert _one_row(db_session, cat, mart, _query("custom.loan_growth", time={"as_of": MAY})) == [
        None
    ]


# --- the query has to carry the declared grain (D-195) -----------------------------------


def test_a_window_cannot_answer_a_period_comparison(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    _certify(
        db_session,
        mart,
        key="custom.loan_growth",
        expression=f"PCT_CHANGE([m:{LOANS}], MONTH)",
        label="Loan growth on the month",
    )
    error = _refuse(
        db_session,
        cat,
        mart,
        _query(
            "custom.loan_growth",
            time={"range": BiDateRange(start=JUL, end=SEP).model_dump(mode="json")},
        ),
    )
    assert "Loan growth on the month" in error.message
    assert "month" in error.message
    assert "single reporting date" in error.message
    assert error.members == ("custom.loan_growth",)


def test_a_second_period_axis_is_refused_by_name(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """``compare_to`` and a time dimension each add a comparison of their own, and
    two comparisons under one certified label is the ambiguity the rule refuses."""

    _certify(
        db_session,
        mart,
        key="custom.loan_growth",
        expression=f"PCT_CHANGE([m:{LOANS}], MONTH)",
    )
    compared = _refuse(
        db_session, cat, mart, _query("custom.loan_growth", time={"as_of": SEP, "compare_to": AUG})
    )
    assert "cannot also be compared with another period" in compared.message

    sliced = _refuse(
        db_session, cat, mart, _query("custom.loan_growth", dimensions=["time.calendar_month"])
    )
    assert "cannot also be broken down by" in sliced.message
    assert "custom.loan_growth" in sliced.members

    pivoted = _refuse(
        db_session,
        cat,
        mart,
        _query(
            "custom.loan_growth",
            dimensions=["product.family"],
            pivot=BiPivot(dimension="time.calendar_month").model_dump(),
        ),
    )
    assert "cannot also be broken down by" in pivoted.message


def test_a_date_that_does_not_end_its_period_is_refused_and_names_one_that_does(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Mid-period, a comparison would put a part period against a whole one.

    The refusal names the nearest date that would work and never substitutes it:
    answering about a different reporting date than the one asked for is how a
    figure comes to be about a day nobody chose.
    """

    _certify(
        db_session,
        mart,
        key="custom.loan_growth_q",
        expression=f"PCT_CHANGE([m:{LOANS}], QUARTER)",
    )
    error = _refuse(
        db_session, cat, mart, _query("custom.loan_growth_q", time={"as_of": date(2026, 8, 31)})
    )
    assert "close of a quarter" in error.message
    assert JUN.isoformat() in error.message


def test_a_reporting_date_before_any_period_end_says_there_is_none(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    _certify(
        db_session,
        mart,
        key="custom.loan_growth",
        expression=f"PCT_CHANGE([m:{LOANS}], MONTH)",
    )
    error = _refuse(
        db_session, cat, mart, _query("custom.loan_growth", time={"as_of": date(2026, 4, 1)})
    )
    assert "no month end on or before" in error.message


def test_two_comparison_periods_cannot_share_one_result(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    _certify(
        db_session,
        mart,
        key="custom.monthly",
        expression=f"PCT_CHANGE([m:{LOANS}], MONTH)",
        label="Monthly growth",
    )
    _certify(
        db_session,
        mart,
        key="custom.quarterly",
        expression=f"PCT_CHANGE([m:{LOANS}], QUARTER)",
        label="Quarterly growth",
    )
    error = _refuse(db_session, cat, mart, _query("custom.monthly", "custom.quarterly"))
    assert "Monthly growth" in error.message
    assert "Quarterly growth" in error.message
    assert set(error.members) == {"custom.monthly", "custom.quarterly"}


def test_a_flow_comparison_needs_the_whole_lagged_period_retained(
    db_session: Session, cat: Catalogue, mart: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stock reads the lagged period's closing book; a flow is added up across it,
    so the same reach costs one more period of history. At the shipped 95-day
    default a quarter-on-quarter FLOW cannot be computed and says so."""

    _certify(
        db_session,
        mart,
        key="custom.movement_growth_q",
        expression=f"PCT_CHANGE([m:{MOVEMENTS}], QUARTER)",
        label="Movement growth on the quarter",
    )
    monkeypatch.setattr(get_settings().bi, "daily_retention_days", 95)
    error = _refuse(db_session, cat, mart, _query("custom.movement_growth_q"))
    assert "adds up over each quarter" in error.message
    assert "95" in error.message


def test_a_formula_reaching_past_retention_is_refused_when_it_is_read_too(
    db_session: Session, cat: Catalogue, mart: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-196 is applied at the WRITE, and again here.

    A deployment that shortens ``BI_DAILY_RETENTION_DAYS`` after a measure was
    certified would otherwise start answering it out of partitions that no longer
    exist — and an empty lagged period renders as a real figure of nothing.
    """

    _certify(
        db_session,
        mart,
        key="custom.loans_ten_months_ago",
        expression=f"LAG([m:{LOANS}], 10, MONTH)",
        label="Loans ten months ago",
        value_type="amount",
    )
    monkeypatch.setattr(get_settings().bi, "daily_retention_days", 95)
    error = _refuse(db_session, cat, mart, _query("custom.loans_ten_months_ago"))
    assert "Loans ten months ago cannot be computed" in error.message
    assert "95 days" in error.message


# --- only a certified measure is evaluated ------------------------------------------------


@pytest.mark.parametrize("state", ["personal", "proposed"])
def test_a_measure_that_is_not_certified_is_not_a_figure_a_query_can_name(
    db_session: Session, cat: Catalogue, mart: Bank, state: str
) -> None:
    """A saved or shared dashboard must not be able to make someone else run a
    formula nobody certified, and the compiler holds no principal with which to tell
    a draft's own author from anybody else. The refusal is the GENERIC unknown-id
    one, because naming the measure would disclose that another person's private
    draft exists.
    """

    _certify(
        db_session,
        mart,
        key="custom.draft",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
        state=state,
    )
    with pytest.raises(UnknownMember) as caught:
        compile_query(
            db_session, cat, _query("custom.draft"), organization_id=ORG_1, bank_id=mart.id
        )
    assert caught.value.members == ("custom.draft",)


def test_the_certified_state_is_the_one_the_model_declares() -> None:
    assert compiler._CERTIFIED_STATE in MEASURE_STATES
    assert compiler._CERTIFIED_STATE == "bank_certified"


def test_another_institution_s_certified_measure_is_not_in_scope(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Two banks of one organization share an RLS tenant, so the lookup is scoped at
    the query by institution as well as organization."""

    sibling = Bank(
        organization_id=ORG_1,
        name="Sibling",
        short_name="sib",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(sibling)
    db_session.flush()
    _certify(
        db_session,
        sibling,
        key="custom.sibling_ratio",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
    )
    with pytest.raises(UnknownMember):
        compile_query(
            db_session,
            cat,
            _query("custom.sibling_ratio"),
            organization_id=ORG_1,
            bank_id=mart.id,
        )


# --- a formula is not a way round a binding ------------------------------------------------


def test_the_authorization_walk_is_given_the_figures_the_statement_reads(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """``expand_calculated_measures`` is the authorization surface of a formula.

    The ids come from the server's own parse of the certified text, so the set the
    walk is asked about is the set the statement will read. An expansion that
    reported FEWER figures than the statement reads would be the leak.
    """

    _certify(
        db_session,
        mart,
        key="custom.exposure_share",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}]) + [m:loans.npl_exposure_rc]",
    )
    expanded = expand_calculated_measures(
        db_session,
        cat,
        _query("custom.exposure_share"),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert expanded.measures == [LOANS, DEPOSITS, "loans.npl_exposure_rc"]

    compiled = compile_query(
        db_session,
        cat,
        _query("custom.exposure_share"),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    for member_id in expanded.measures:
        assert member_id in compiled.member_ids
    assert "custom.exposure_share" in compiled.member_ids


def test_a_doctored_member_column_cannot_shorten_what_the_walk_is_asked_about(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """The stored ``referenced_members`` column is an audit copy, never an input.

    Emptying it is the attack: if anything on the read path trusted it, the walk
    would be asked about no figures at all and the statement would still read two.
    """

    row = _certify(
        db_session,
        mart,
        key="custom.loans_to_deposits",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
    )
    row.referenced_members = []
    db_session.flush()

    expanded = expand_calculated_measures(
        db_session,
        cat,
        _query("custom.loans_to_deposits"),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert expanded.measures == [LOANS, DEPOSITS]


def test_an_unknown_id_is_left_alone_so_it_is_still_refused(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """An expansion that dropped an id it could not resolve would authorize a query
    over nothing and then read whatever the compiler made of it."""

    query = _query("custom.never_existed")
    expanded = expand_calculated_measures(
        db_session, cat, query, organization_id=ORG_1, bank_id=mart.id
    )
    assert expanded.measures == ["custom.never_existed"]


def test_a_sort_key_naming_a_calculated_measure_is_dropped_from_the_probe(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """It is not a catalogue member, and the figures behind it are already named."""

    _certify(
        db_session,
        mart,
        key="custom.loans_to_deposits",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
    )
    expanded = expand_calculated_measures(
        db_session,
        cat,
        _query(
            "custom.loans_to_deposits",
            dimensions=["product.family"],
            sort=[{"member": "custom.loans_to_deposits", "direction": "desc"}],
        ),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert expanded.sort == []


def test_a_formula_cannot_reach_a_figure_its_reader_may_not_see(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """THE security property, end to end through the content plane's own walk.

    The reader holds credit at ``aggregated`` and nothing else. A formula naming a
    credit total is served; one naming an obligor NAME — ``restricted``, and in the
    same module — is refused, and the refusal names the withheld figure rather than
    the formula, because that is the grant the reader is missing.
    """

    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    if db_session.get(User, USER_1) is None:
        db_session.add(User(id=USER_1, organization_id=ORG_1, email="reader@example.test"))
    db_session.flush()
    authorization_service.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization_service.BindingScope(
            InstitutionScope.ORGANIZATION, None, ModuleScope.CREDIT, SensitivityScope.AGGREGATED
        ),
        grantor=authorization_service.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise the calculated-measure walk.",
        commit=False,
    )
    db_session.flush()
    _certify(
        db_session,
        mart,
        key="custom.allowed",
        expression=f"SAFE_DIV([m:{LOANS}], [m:loans.classification_exposure_rc])",
    )
    authority = content.ViewerAuthority(
        db=db_session,
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
        bank=mart,
        cat=cat,
        surface=content.DASHBOARD_SURFACE,
    )
    assert authority.refused_members(_query("custom.allowed")) == ()


def test_a_formula_naming_a_restricted_figure_is_refused_for_a_narrow_reader(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """The other half: the formula names a figure in a module the reader does not
    hold, and the walk refuses it by naming THAT figure rather than the formula —
    which is the grant the reader is missing."""

    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    if db_session.get(User, USER_1) is None:
        db_session.add(User(id=USER_1, organization_id=ORG_1, email="reader@example.test"))
    db_session.flush()
    authorization_service.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization_service.BindingScope(
            InstitutionScope.ORGANIZATION, None, ModuleScope.CREDIT, SensitivityScope.AGGREGATED
        ),
        grantor=authorization_service.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise the calculated-measure walk.",
        commit=False,
    )
    db_session.flush()
    _certify(
        db_session,
        mart,
        key="custom.loans_to_deposits",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
    )
    authority = content.ViewerAuthority(
        db=db_session,
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
        bank=mart,
        cat=cat,
        surface=content.DASHBOARD_SURFACE,
    )
    refused = authority.refused_members(_query("custom.loans_to_deposits"))
    assert DEPOSITS in refused
    assert LOANS not in refused
    assert "custom.loans_to_deposits" not in refused


# --- a saved dashboard may only name a certified one ---------------------------------------


def test_a_saved_canvas_may_not_name_an_uncertified_calculated_measure() -> None:
    """A saved dashboard is a document other people open.

    A personal formula on one turns a share into a way to make someone else compute
    the owner's arithmetic under the owner's label, so the shape check refuses any
    measure id the catalogue does not know unless it is named as certified — and the
    certified set defaults to EMPTY, so a caller that does not supply it refuses
    every calculated measure rather than admitting one unchecked.
    """

    spec = BiDashboardSpec(
        widgets=[
            BiPackWidget(
                id="w1",
                kind="kpi",
                title="Loans to deposits",
                query=BiPackQuery(measures=["custom.loans_to_deposits"], window="as_of"),
            )
        ],
        layout=[BiLayoutItem(i="w1", x=0, y=0, w=4, h=4)],
    )
    with pytest.raises(content.CanvasRefused) as caught:
        content.check_canvas_shape(spec)
    assert "has certified" in str(caught.value)

    # Named as certified, it passes: what breakdowns it allows is the compiler's
    # question, decided against the formula it re-parses.
    content.check_canvas_shape(spec, certified_measures=frozenset({"custom.loans_to_deposits"}))


def test_the_served_member_log_records_the_figures_that_were_authorized(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """What the append-only log calls SERVED is the expanded set, not the ids the
    request happened to name — otherwise a read of two figures through a formula
    would be recorded as a read of none of them."""

    _certify(
        db_session,
        mart,
        key="custom.loans_to_deposits",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
    )
    authority = content.ViewerAuthority(
        db=db_session,
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
        bank=mart,
        cat=cat,
        surface=content.DASHBOARD_SURFACE,
    )
    served = [member.id for member in authority.served_members(_query("custom.loans_to_deposits"))]
    assert served == [LOANS, DEPOSITS]


# --- what a formula may not be made of ----------------------------------------------------


def test_a_formula_may_not_name_a_dimension(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    _certify(db_session, mart, key="custom.bad", expression=f"[m:{LOANS}] + [m:branch.region]")
    error = _refuse(db_session, cat, mart, _query("custom.bad"))
    assert "group or filter by" in error.message


def test_a_formula_may_not_name_a_concentration_figure(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """A concentration measure is aggregated per obligor and then over the group, so
    combining it inside a formula would re-aggregate one inside the other."""

    _certify(
        db_session,
        mart,
        key="custom.hhi_ratio",
        expression=f"SAFE_DIV([m:loans.sector_hhi], [m:{LOANS}])",
    )
    error = _refuse(db_session, cat, mart, _query("custom.hhi_ratio"))
    assert "concentration figure" in error.message


def test_a_concentration_figure_and_a_calculated_measure_cannot_share_one_result(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    _certify(
        db_session,
        mart,
        key="custom.loans_to_deposits",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
    )
    error = _refuse(
        db_session,
        cat,
        mart,
        _query("loans.sector_hhi", "custom.loans_to_deposits", dimensions=["branch.code"]),
    )
    assert "concentration figure and a calculated measure" in error.message


def test_a_formula_reading_two_fact_tables_is_refused_by_the_one_fact_rule(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """The rule is not relaxed for a calculated measure: its figures join the
    query's own for every shape check, so a formula mixing a position balance with
    a loan movement is refused exactly as a query naming both would be."""

    _certify(
        db_session,
        mart,
        key="custom.cross_fact",
        expression=f"SAFE_DIV([m:{MOVEMENTS}], [m:{LOANS}])",
    )
    error = _refuse(db_session, cat, mart, _query("custom.cross_fact"))
    assert "more than one fact table" in error.message


def test_a_calculated_measure_is_held_to_the_allowed_dimensions_of_its_figures(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    _certify(
        db_session,
        mart,
        key="custom.loans_to_deposits",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
    )
    error = _refuse(
        db_session, cat, mart, _query("custom.loans_to_deposits", dimensions=["event.type"])
    )
    assert "cannot be sliced by event.type" in error.message


def test_too_many_figures_once_the_formulas_are_worked_out_is_refused_by_name(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Twenty-five formulas of twenty-five figures is not twenty-five figures."""

    position_sums = [
        measure.id
        for measure in cat.measures()
        if measure.table == "bi_fact_position_daily"
        and measure.aggregation not in {"top_n_share", "hhi"}
        and ".budget." not in measure.id
        and ".reforecast." not in measure.id
    ]
    assert len(position_sums) > BI_MAX_MEASURES, "the catalogue no longer has enough to overrun"
    half = len(position_sums) // 2
    _certify(
        db_session,
        mart,
        key="custom.wide",
        expression=" + ".join(f"[m:{member_id}]" for member_id in position_sums[:half]),
        value_type="amount",
    )
    error = _refuse(db_session, cat, mart, _query("custom.wide", *position_sums[half:]))
    assert str(BI_MAX_MEASURES) in error.message
    assert "once its calculated measures are worked out" in error.message


# --- the value-type vocabulary (A8-11) ----------------------------------------------------


def test_the_wire_format_vocabulary_is_the_catalogue_s_plus_the_marker() -> None:
    """Both directions, because a one-way check is what let ``ratio`` be retired in
    four consumers' silence (A8-02, A8-03, D-189). ``app/schemas`` may not import
    ``app.domain.bi`` — the plane boundary keeps it in the scan — so the two are
    held together here instead of by an import."""

    wire = set(get_args(BiResultColumnFormat))
    assert wire == set(VALUE_TYPES) | {"int"}
    assert set(VALUE_TYPES) <= wire
    assert "ratio" not in wire


def test_a_calculated_measure_declaring_an_unrenderable_number_is_refused(
    cat: Catalogue,
) -> None:
    """The narrowing from a text column to the wire vocabulary is CHECKED.

    A stored row cannot reach this — ``ck_bi_measures_value_type`` refuses the
    write, which is proved below — so the guard is exercised on the function that
    does the narrowing. Without it the narrowing would be an unverified assertion
    about a text column, which is the shape of A8-11 itself.
    """

    with pytest.raises(InvalidQuery) as caught:
        compiler._resolve_calculated(
            cat,
            compiler._Certified(
                id="custom.odd",
                label="Odd figure",
                value_type="ratio",
                expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
            ),
            RETENTION_DAYS,
        )
    assert "kind of number this result cannot render" in caught.value.message


def test_a_row_cannot_store_a_value_type_outside_the_vocabulary(
    db_session: Session, mart: Bank
) -> None:
    """Why the guard above has to be tested where it is."""

    row = _certify(
        db_session,
        mart,
        key="custom.loans_to_deposits",
        expression=f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
    )
    row.value_type = "ratio"
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- the period calendar ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("grain", "back", "expected"),
    [
        ("month", 0, (date(2026, 5, 1), date(2026, 5, 31))),
        ("month", 3, (date(2026, 2, 1), date(2026, 2, 28))),
        ("month", 5, (date(2025, 12, 1), date(2025, 12, 31))),
        ("quarter", 0, (date(2026, 4, 1), date(2026, 6, 30))),
        ("quarter", 2, (date(2025, 10, 1), date(2025, 12, 31))),
        ("year", 1, (date(2025, 1, 1), date(2025, 12, 31))),
    ],
)
def test_a_period_is_shifted_by_months_and_not_by_days(
    grain: str, back: int, expected: tuple[date, date]
) -> None:
    """Three months before 31 May is February, whose end is the 28th, and no number
    of days says that."""

    assert compiler._shifted_period(MAY, grain, back) == expected


def test_the_period_in_progress_is_never_read_past_the_reporting_date() -> None:
    window = compiler._period_window(date(2026, 9, 15), "month", 0)
    assert (window.start, window.end) == (date(2026, 9, 1), date(2026, 9, 15))
    assert not window.single
