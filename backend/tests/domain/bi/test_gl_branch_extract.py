"""``extract.gl_branch_monthly_rows``: the branch breakdown sums to the ledger.

The whole point of the residual row is that the identity holds by construction
rather than by hope, so these tests state the identity as an assertion over
COMPUTED rows in every shape the register can arrive in: complete, partial,
over-allocated, with a branch that joined since last month and one that left,
naming an account the ledger does not carry, and with no prior register at all.

The movement identity is deliberately CONDITIONAL — a branch with no prior
reading has no month figure, so the block does not claim one — and both halves of
that are pinned: it holds whenever nothing in the block is ``missing_prior``, and
a joining branch is what makes a block stop claiming it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.bi.extract import (
    GlBranchAllocation,
    GlMonthlyFactRow,
    gl_branch_monthly_rows,
)
from app.domain.ingestion.reference_schemas.gl_segment_balances import RESIDUAL_BRANCH_ID

ORG, BANK = "OR-TEST0001", "BK-TEST0001"
JUN, MAY = date(2026, 6, 30), date(2026, 5, 31)
RESIDUAL = RESIDUAL_BRANCH_ID


def ledger(  # noqa: PLR0913 - one keyword per ledger property a case varies
    code: str,
    ytd: str,
    prior: str | None,
    *,
    currency: str = "",
    account_class: str = "INCOME",
    line: str | None = "1a",
    sign: int | None = 1,
) -> GlMonthlyFactRow:
    return GlMonthlyFactRow(
        organization_id=ORG,
        bank_id=BANK,
        month_end=JUN,
        gl_account_code=code,
        currency=currency,
        calendar_month=date(2026, 6, 1),
        account_class=account_class,
        ytd_rc=Decimal(ytd),
        prior_ytd_rc=None if prior is None else Decimal(prior),
        movement_rc=None if prior is None else Decimal(ytd) - Decimal(prior),
        missing_prior=prior is None,
        balance_basis="ytd",
        pl_line=line,
        pl_sign=sign,
    )


def alloc(code: str, branch: str, ytd: str, currency: str = "") -> GlBranchAllocation:
    return GlBranchAllocation(
        gl_account_code=code, branch_id=branch, currency=currency, ytd=Decimal(ytd)
    )


def run(institution, current, prior=()):  # noqa: ANN001, ANN201 - a test-local shorthand
    return gl_branch_monthly_rows(
        institution,
        current=current,
        prior=prior,
        residual_branch_id=RESIDUAL,
        register_as_of=JUN,
    )


def totals(result, code: str, currency: str = "") -> Decimal:  # noqa: ANN001
    return sum(
        (
            row.ytd_rc
            for row in result.rows
            if row.gl_account_code == code and row.currency == currency
        ),
        Decimal(0),
    )


def by_branch(result, code: str) -> dict[str, Decimal]:  # noqa: ANN001
    return {row.branch_code: row.ytd_rc for row in result.rows if row.gl_account_code == code}


def test_a_partial_allocation_still_sums_to_the_ledger() -> None:
    """The case the whole design exists for: four fifths allocated, the fifth NAMED.

    A breakdown that summed only what the bank allocated would report this bank's
    interest income as 550 rather than 1000 — a board figure wrong by the part
    nobody attributed, with nothing on the surface to say so.
    """
    institution = [ledger("4010", "1000", "700")]
    result = run(institution, [alloc("4010", "BR-002", "300"), alloc("4010", "BR-003", "250")])

    assert totals(result, "4010") == Decimal(1000)
    assert by_branch(result, "4010") == {
        "BR-002": Decimal(300),
        "BR-003": Decimal(250),
        RESIDUAL: Decimal(450),
    }
    assert result.branch_codes == frozenset({"BR-002", "BR-003"})
    assert result.orphans == () and result.over_allocated == ()


def test_a_full_allocation_writes_no_empty_residual() -> None:
    """A residual that is zero at both readings carries nothing, so it is omitted —
    and the identity is unaffected, because adding zero is what it would have added."""
    institution = [ledger("4010", "1000", "700")]
    result = run(
        institution,
        [alloc("4010", "BR-002", "600"), alloc("4010", "BR-003", "400")],
        [alloc("4010", "BR-002", "400"), alloc("4010", "BR-003", "300")],
    )

    assert totals(result, "4010") == Decimal(1000)
    assert RESIDUAL not in by_branch(result, "4010")
    assert sum((row.movement_rc or Decimal(0)) for row in result.rows) == Decimal(300)


def test_a_residual_that_is_zero_only_now_is_kept_because_it_carries_a_movement() -> None:
    """Fully allocated this month, not last: the remainder went from 100 to 0, which
    is a real movement of −100 and would be lost if the row were dropped as empty."""
    institution = [ledger("4010", "1000", "800")]
    result = run(
        institution,
        [alloc("4010", "BR-002", "1000")],
        [alloc("4010", "BR-002", "700")],
    )

    rows = {row.branch_code: row for row in result.rows}
    assert rows[RESIDUAL].ytd_rc == Decimal(0)
    assert rows[RESIDUAL].prior_ytd_rc == Decimal(100)
    assert rows[RESIDUAL].movement_rc == Decimal(-100)
    assert sum(row.movement_rc or Decimal(0) for row in result.rows) == Decimal(200)


def test_the_movement_identity_holds_when_no_row_is_missing_a_prior() -> None:
    """Σ branch movement is the institution's movement, exactly — including when a
    branch has LEFT since the prior month, whose prior balance the residual absorbs
    rather than stranding."""
    institution = [ledger("4010", "1000", "700")]
    result = run(
        institution,
        [alloc("4010", "BR-002", "300"), alloc("4010", "BR-003", "250")],
        # BR-009 was here last month and is gone now; its 50 is inside the 700.
        [
            alloc("4010", "BR-002", "200"),
            alloc("4010", "BR-003", "150"),
            alloc("4010", "BR-009", "50"),
        ],
    )

    assert not any(row.missing_prior for row in result.rows)
    assert sum(row.movement_rc or Decimal(0) for row in result.rows) == Decimal(300)
    assert institution[0].movement_rc == Decimal(300)
    # The residual's prior is the ledger's prior less THIS month's branches, so
    # BR-009's 50 rides in it.
    residual = next(row for row in result.rows if row.branch_code == RESIDUAL)
    assert residual.prior_ytd_rc == Decimal(350)


def test_a_branch_that_joined_this_month_has_no_movement_and_the_block_claims_none() -> None:
    """A joining branch's month figure is unknown, not zero. The block therefore
    carries a ``missing_prior`` row, which is the signal that the movement identity
    is not being claimed — never a fabricated movement equal to the whole balance."""
    institution = [ledger("4010", "1000", "700")]
    result = run(
        institution,
        [alloc("4010", "BR-002", "300"), alloc("4010", "BR-007", "250")],
        [alloc("4010", "BR-002", "200")],
    )

    rows = {row.branch_code: row for row in result.rows}
    assert rows["BR-002"].movement_rc == Decimal(100)
    assert rows["BR-007"].missing_prior is True
    assert rows["BR-007"].movement_rc is None and rows["BR-007"].prior_ytd_rc is None
    assert any(row.missing_prior for row in result.rows)
    # The YTD identity is unconditional and holds anyway.
    assert totals(result, "4010") == Decimal(1000)


def test_no_prior_register_means_no_movement_anywhere_and_still_a_correct_total() -> None:
    institution = [ledger("4010", "1000", "700")]
    result = run(institution, [alloc("4010", "BR-002", "400")])

    assert all(row.missing_prior for row in result.rows)
    assert all(row.movement_rc is None for row in result.rows)
    assert totals(result, "4010") == Decimal(1000)


def test_an_account_the_ledger_does_not_carry_is_reported_not_written() -> None:
    """The ledger is the authority. A branch figure with no institution row cannot
    be reconciled to anything, and adding it would make the branch total exceed the
    ledger — so it is named in ``orphans`` and left out, never silently dropped and
    never silently included."""
    institution = [ledger("4010", "1000", "700")]
    result = run(
        institution,
        [
            alloc("4010", "BR-002", "300"),
            alloc("9999", "BR-002", "12"),
            alloc("4010", "BR-002", "0", "USD"),
        ],
    )

    assert result.orphans == (("4010", "USD"), ("9999", ""))
    assert {row.gl_account_code for row in result.rows} == {"4010"}
    assert totals(result, "4010") == Decimal(1000)


def test_over_allocation_is_reported_and_the_identity_is_still_kept() -> None:
    """A register that claims more of an account than the ledger holds makes the
    residual run the other way. Clamping it to zero would break the one property
    that may not break, so the residual is written and the condition reported."""
    institution = [ledger("4010", "1000", "700")]
    result = run(institution, [alloc("4010", "BR-002", "1200")])

    assert result.over_allocated == ("4010",)
    assert by_branch(result, "4010")[RESIDUAL] == Decimal(-200)
    assert totals(result, "4010") == Decimal(1000)


def test_a_branch_named_twice_for_one_account_is_summed_not_dropped() -> None:
    """Two rows for one (account, branch, currency) are two parts of one figure; any
    other reading loses money out of a total that must equal the ledger."""
    institution = [ledger("4010", "1000", "700")]
    result = run(institution, [alloc("4010", "BR-002", "300"), alloc("4010", "BR-002", "200")])

    assert by_branch(result, "4010") == {"BR-002": Decimal(500), RESIDUAL: Decimal(500)}


def test_currency_is_part_of_the_key_so_two_currencies_reconcile_separately() -> None:
    institution = [ledger("4010", "1000", "700"), ledger("4010", "50", "40", currency="USD")]
    result = run(
        institution,
        [alloc("4010", "BR-002", "600"), alloc("4010", "BR-002", "20", "USD")],
    )

    assert totals(result, "4010", "") == Decimal(1000)
    assert totals(result, "4010", "USD") == Decimal(50)


def test_a_branch_row_inherits_the_accounts_own_mapping_never_its_own() -> None:
    """One account, one BSD7 line, one sign — so Σ ``pl_sign × ytd_rc`` per line over
    branches is the same line the return files."""
    institution = [ledger("5302", "80", "50", account_class="EXPENSE", line="17", sign=-1)]
    result = run(institution, [alloc("5302", "BR-002", "30")])

    for row in result.rows:
        assert (row.pl_line, row.pl_sign, row.account_class) == ("17", -1, "EXPENSE")
        assert (row.balance_basis, row.month_end, row.calendar_month) == (
            "ytd",
            JUN,
            date(2026, 6, 1),
        )
        assert row.register_as_of == JUN


def test_an_account_the_register_never_mentions_gets_no_rows_at_all() -> None:
    """Not a row of 100 % unallocated. A reader must be able to see WHICH accounts
    have a breakdown; a table that answers for every account by saying "nobody
    allocated it" looks like an answer and is none."""
    institution = [ledger("4010", "1000", "700"), ledger("4020", "500", "400")]
    result = run(institution, [alloc("4010", "BR-002", "1000")])

    assert {row.gl_account_code for row in result.rows} == {"4010"}
