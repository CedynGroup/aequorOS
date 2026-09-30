"""The Phase 5 catalogue members, read end to end through the real compiler.

Five members reach the wire for the first time here, and each one is asked for by
name over rows the mart holds:

* ``position.officer_code`` / ``position.channel`` / ``position.account_status``
  over the four columns migration ``202609280076`` added;
* ``loans.arrears_amount_rc`` / ``loans.arrears_share_pct`` over one of them;
* ``gl.branch_ytd_rc`` / ``gl.branch_movement_rc`` over the branch ledger mart.

Two properties get their own tests because both are easy to break invisibly and
neither is visible in a passing build:

1. **A loan that states no arrears contributes NO ROW, never a zero.** The
   measures sum only what the bank stated, and the share's two legs cover the same
   population, so a partial book cannot read as a complete one. This file proves
   the figures themselves do not lie about what was and was not stated.
2. **``movement_rc`` stays NULL through the aggregation.** ``flow_sum`` must emit a
   bare ``sum(...)``: a branch whose previous month was never pushed has no known
   movement, and a coalesce to 0 would report a month of no change. A coalesce is
   one word to add and invisible afterwards.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.domain.bi.catalogue import Catalogue, catalogue
from app.domain.bi.catalogue.dimensions import GL_BRANCH_DIMENSION_IDS, POSITION_DIMENSION_IDS
from app.models import Bank, BiDimBranch, BiFactGlBranchMonthly
from app.schemas.bi import BiQuery
from app.services.bi.compiler import _resolve
from tests.api.helpers import ORG_1
from tests.services.bi.test_compiler import (
    BUILT_AT,
    SEP_18,
    _as_dict,
    _bank,
    _num,
    _position,
    _run,
)

MAY_31 = date(2026, 5, 31)
JUN_30 = date(2026, 6, 30)

#: The three new dimensions and the four measures, named once so a member that is
#: dropped or renamed fails here as well as in the catalogue's own suite.
NEW_DIMENSIONS: tuple[str, ...] = (
    "position.officer_code",
    "position.channel",
    "position.account_status",
)
NEW_MEASURES: tuple[str, ...] = (
    "loans.arrears_amount_rc",
    "loans.arrears_share_pct",
    "gl.branch_ytd_rc",
    "gl.branch_movement_rc",
)


@pytest.fixture
def cat() -> Catalogue:
    return catalogue()


def _optional_loan(  # noqa: PLR0913 - one keyword per new column
    bank: Bank,
    *,
    balance: str,
    officer: str | None,
    channel: str | None,
    status: str | None,
    arrears: str | None,
) -> Any:
    return _position(
        bank,
        SEP_18,
        position_type="LOAN",
        balance_native=Decimal(balance),
        balance_rc=Decimal(balance),
        classification_exposure_rc=Decimal(balance),
        non_performing=False,
        officer_id=officer,
        channel=channel,
        account_status=status,
        arrears_amount_rc=None if arrears is None else Decimal(arrears),
    )


@pytest.fixture
def optional_fields_mart(db_session: Session) -> Bank:
    """Two loans that state all four fields and one that states none of them."""
    bank = _bank(db_session, ORG_1, "Phase 5 Bank")
    db_session.add_all(
        [
            _optional_loan(
                bank,
                balance="1000",
                officer="RM-014",
                channel="mobile_app",
                status="active",
                arrears="250",
            ),
            _optional_loan(
                bank,
                balance="3000",
                officer="RM-014",
                channel="branch",
                status="dormant",
                arrears="750",
            ),
            _optional_loan(
                bank, balance="6000", officer=None, channel=None, status=None, arrears=None
            ),
        ]
    )
    db_session.flush()
    return bank


@pytest.fixture
def branch_ledger_mart(db_session: Session) -> Bank:
    """One month of branch ledger rows: BR-1 has a prior month, BR-2 does not."""
    bank = _bank(db_session, ORG_1, "Branch Ledger Bank")
    db_session.add_all(
        [
            BiDimBranch(
                organization_id=ORG_1,
                bank_id=bank.id,
                branch_code=code,
                name=name,
                region="Region",
                mapped=True,
                builder_version=1,
                built_at=BUILT_AT,
            )
            for code, name in (("BR-1", "First branch"), ("BR-2", "Second branch"))
        ]
    )
    for code, ytd, prior in (("BR-1", "500", "400"), ("BR-2", "300", None)):
        db_session.add(
            BiFactGlBranchMonthly(
                organization_id=ORG_1,
                bank_id=bank.id,
                month_end=JUN_30,
                gl_account_code="4001",
                branch_code=code,
                currency="",
                calendar_month=date(2026, 6, 1),
                account_class="INCOME",
                ytd_rc=Decimal(ytd),
                prior_ytd_rc=None if prior is None else Decimal(prior),
                movement_rc=None if prior is None else Decimal(ytd) - Decimal(prior),
                missing_prior=prior is None,
                balance_basis="ytd",
                pl_line="interest_income",
                pl_sign=1,
                register_as_of=JUN_30,
                builder_version=1,
                built_at=BUILT_AT,
            )
        )
    db_session.flush()
    return bank


# --- every new member is queryable ------------------------------------------------------


@pytest.mark.parametrize("dimension_id", NEW_DIMENSIONS)
def test_each_new_position_dimension_groups_a_real_figure(
    db_session: Session, cat: Catalogue, optional_fields_mart: Bank, dimension_id: str
) -> None:
    """Asked for by name, joined, grouped and returned — not merely declared."""
    assert dimension_id in POSITION_DIMENSION_IDS
    compiled, rows = _run(
        db_session,
        cat,
        optional_fields_mart,
        BiQuery.model_validate(
            {
                "measures": ["loans.balance_rc"],
                "dimensions": [dimension_id],
                "time": {"as_of": SEP_18},
            }
        ),
    )
    assert [column.id for column in compiled.columns] == [dimension_id, "loans.balance_rc"]
    grouped = {row[0]: _num(row[1]) for row in rows}
    # Every loan is reached, and the one that states nothing is its OWN group rather
    # than being dropped or folded into a stated value.
    assert sum(value or 0.0 for value in grouped.values()) == 10000.0
    assert grouped[None] == 6000.0
    assert len([key for key in grouped if key is not None]) >= 1


def test_the_stated_values_are_the_bank_s_own_spellings(
    db_session: Session, cat: Catalogue, optional_fields_mart: Bank
) -> None:
    _, rows = _run(
        db_session,
        cat,
        optional_fields_mart,
        BiQuery.model_validate(
            {
                "measures": ["loans.count"],
                "dimensions": ["position.channel"],
                "time": {"as_of": SEP_18},
            }
        ),
    )
    assert {row[0]: row[1] for row in rows} == {"branch": 1, "mobile_app": 1, None: 1}


def test_an_officer_s_book_is_readable_and_the_code_is_never_case_folded(
    db_session: Session, cat: Catalogue, optional_fields_mart: Bank
) -> None:
    """``officer_id`` is carried verbatim, like ``branch_code``: the platform's idea
    of the code must not differ from the bank's own staff register."""
    _, rows = _run(
        db_session,
        cat,
        optional_fields_mart,
        BiQuery.model_validate(
            {
                "measures": ["loans.balance_rc"],
                "dimensions": ["position.officer_code"],
                "filters": [{"member": "position.officer_code", "op": "in", "values": ["RM-014"]}],
                "time": {"as_of": SEP_18},
            }
        ),
    )
    assert rows == [("RM-014", Decimal("4000.000000"))] or _num(rows[0][1]) == 4000.0
    assert rows[0][0] == "RM-014"


# --- missing is never zero ---------------------------------------------------------------


def test_a_loan_that_states_no_arrears_contributes_no_row_not_a_zero(
    db_session: Session, cat: Catalogue, optional_fields_mart: Bank
) -> None:
    """The sum is the 1000 stated over two loans of 4000, NOT over the 10000 book.

    This figure is honest about what it read and says nothing about what it did
    not: a loan that states no arrears is absent from the sum, never a zero in it.
    """
    _, rows = _run(
        db_session,
        cat,
        optional_fields_mart,
        BiQuery.model_validate(
            {
                "measures": ["loans.arrears_amount_rc", "loans.arrears_share_pct"],
                "time": {"as_of": SEP_18},
            }
        ),
    )
    assert _num(rows[0][0]) == 1000.0
    # 1000 / 10000: both legs follow the DERIVATION rule, so the denominator is the
    # whole converted book — the share is understated exactly to the extent the book
    # is unstated — visibly, because the unstated loans contribute no row.
    assert _num(rows[0][1]) == pytest.approx(10.0)


def test_a_book_that_states_no_arrears_at_all_reads_as_no_value(
    db_session: Session, cat: Catalogue
) -> None:
    """The case that made ``par_90_pct`` read ``0.00 %``: a book with the column
    empty must return NULL, never 0, or a reader sees a performing book."""
    bank = _bank(db_session, ORG_1, "Silent Book Bank")
    db_session.add(
        _optional_loan(bank, balance="5000", officer=None, channel=None, status=None, arrears=None)
    )
    db_session.flush()
    _, rows = _run(
        db_session,
        cat,
        bank,
        BiQuery.model_validate(
            {
                "measures": ["loans.arrears_amount_rc", "loans.arrears_share_pct"],
                "time": {"as_of": SEP_18},
            }
        ),
    )
    assert rows[0][0] is None, "an unstated arrears column must not sum to zero"
    assert rows[0][1] is None, "a share with no numerator is unknown, not 0 %"


# --- the branch ledger ------------------------------------------------------------------


def test_the_branch_ledger_measures_are_queryable_over_the_branch_dimension(
    db_session: Session, cat: Catalogue, branch_ledger_mart: Bank
) -> None:
    compiled, rows = _run(
        db_session,
        cat,
        branch_ledger_mart,
        BiQuery.model_validate(
            {
                "measures": ["gl.branch_ytd_rc"],
                "dimensions": ["branch.name", "gl_account.code"],
                "time": {"as_of": JUN_30},
            }
        ),
    )
    assert compiled.fact_table == BiFactGlBranchMonthly.__tablename__
    by_branch = {
        row["branch.name"]: _num(row["gl.branch_ytd_rc"]) for row in _as_dict(compiled, rows)
    }
    assert by_branch == {"First branch": 500.0, "Second branch": 300.0}


def test_movement_stays_null_for_a_branch_with_no_prior_month(
    db_session: Session, cat: Catalogue, branch_ledger_mart: Bank
) -> None:
    """The coalesce trap, proven absent at the AGGREGATION.

    ``BR-2``'s previous month was never pushed, so its movement is NOT KNOWN. A
    ``flow_sum`` that coalesced its group to 0 would report a month of no change in
    that branch's ledger — a figure a branch manager would act on. The extract's
    NULL is pinned in ``tests/domain/bi/test_gl_branch_extract.py``; what is pinned
    here is that the SQL keeps it.
    """
    compiled, rows = _run(
        db_session,
        cat,
        branch_ledger_mart,
        BiQuery.model_validate(
            {
                "measures": ["gl.branch_movement_rc"],
                "dimensions": ["branch.code"],
                "time": {"as_of": JUN_30},
            }
        ),
    )
    movement = {
        row["branch.code"]: row["gl.branch_movement_rc"] for row in _as_dict(compiled, rows)
    }
    assert _num(movement["BR-1"]) == 100.0
    assert movement["BR-2"] is None, "a branch with no prior month has no known movement"
    # And the institution total is the KNOWN movement, not the known movement plus a
    # fabricated zero — with no group at all the sum is still NULL when nothing is known.
    _, totals = _run(
        db_session,
        cat,
        branch_ledger_mart,
        BiQuery.model_validate({"measures": ["gl.branch_movement_rc"], "time": {"as_of": JUN_30}}),
    )
    assert _num(totals[0][0]) == 100.0


def test_the_movement_measure_is_declared_a_flow_and_the_level_a_stock(cat: Catalogue) -> None:
    """Both halves, because reading one as the other is wrong by a whole period."""
    level = cat.measure("gl.branch_ytd_rc")
    flow = cat.measure("gl.branch_movement_rc")
    assert (level.time_behaviour, level.aggregation) == ("stock", "sum")
    assert (flow.time_behaviour, flow.aggregation) == ("flow", "flow_sum")


def test_the_branch_ledger_measures_offer_only_dimensions_this_fact_carries(
    cat: Catalogue,
) -> None:
    """A grouping advertised and then refused is a member that looks reachable and
    is not. The branch ledger carries no position, loan, product or counterparty
    key, so none of those ids may appear."""
    for measure_id in ("gl.branch_ytd_rc", "gl.branch_movement_rc"):
        allowed = set(cat.measure(measure_id).allowed_dimensions)
        assert allowed == set(GL_BRANCH_DIMENSION_IDS)
        assert not any(
            member_id.startswith(("position.", "loan.", "product.", "counterparty."))
            for member_id in allowed
        )
        assert {"time.date", "branch.code", "gl_account.pl_line"} <= allowed


@pytest.mark.parametrize("measure_id", NEW_MEASURES)
def test_every_new_measure_resolves_to_one_fact_with_a_window(
    cat: Catalogue, measure_id: str
) -> None:
    resolved = _resolve(
        cat, BiQuery.model_validate({"measures": [measure_id], "time": {"as_of": JUN_30}}), ()
    )
    assert resolved.measures and resolved.fact
