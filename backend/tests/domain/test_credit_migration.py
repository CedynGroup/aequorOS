"""Migration-matrix + cure-rule goldens (credit PR-5) — hand-verified.

Opening book (month M−1): A perf 100 (current), B perf 200 (1_29),
C NPL 300 (90_179), D perf-restructured 50 (current), E perf 80 (current).
Closing book (M): A perf 110 (current), B NPL 190 (90_179),
C NPL 280 (180_359), D perf-restructured 55 (current), F new perf 60.
E departed (settled/written off).

Matrix (closing exposure): perf→perf 110, perf→npl 190, npl→npl 280,
pr→pr 55. Entries: new→perf 60. Exits: perf→departed 80 (opening exposure).
Roll rates (opening-weighted): current openings among matched = A 100 + D 50 +
(E excluded — departed) = 150 → current→current 150/150 = 100%;
1_29→90_179 = 200/200 = 100%; 90_179→180_359 = 300/300 = 100%.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.credit.migration import LoanState, compute_migration
from app.domain.credit.restructure import restructure_holds_npl


def _d(value: str) -> Decimal:
    return Decimal(value)


OPENING = [
    LoanState("A", _d("100"), "current", non_performing=False),
    LoanState("B", _d("200"), "1_29", non_performing=False),
    LoanState("C", _d("300"), "90_179", non_performing=True),
    LoanState("D", _d("50"), "current", non_performing=False, restructured_performing=True),
    LoanState("E", _d("80"), "current", non_performing=False),
]
CLOSING = [
    LoanState("A", _d("110"), "current", non_performing=False),
    LoanState("B", _d("190"), "90_179", non_performing=True),
    LoanState("C", _d("280"), "180_359", non_performing=True),
    LoanState("D", _d("55"), "current", non_performing=False, restructured_performing=True),
    LoanState("F", _d("60"), "current", non_performing=False),
]


def test_the_three_by_three_matrix_carries_the_hand_flows() -> None:
    result = compute_migration(OPENING, CLOSING)

    def flow(from_state: str, to_state: str) -> Decimal:
        cell = result.cell(from_state, to_state)
        assert cell is not None, (from_state, to_state)
        return cell.exposure_ghs

    assert flow("performing", "performing") == _d("110")
    assert flow("performing", "npl") == _d("190")
    assert flow("npl", "npl") == _d("280")
    cell = result.cell("performing_restructured", "performing_restructured")
    assert cell is not None and cell.exposure_ghs == _d("55")
    assert result.matched_loan_count == 4


def test_entries_and_exits_reconcile_the_matrix_to_the_stocks() -> None:
    """closing_total = Σ matrix closing flows + Σ entries — the reconciliation
    a matrix without legs cannot make."""
    result = compute_migration(OPENING, CLOSING)
    matrix_total = sum((c.exposure_ghs for c in result.matrix), _d("0"))
    entry_total = sum((c.exposure_ghs for c in result.entries), _d("0"))
    exit_total = sum((c.exposure_ghs for c in result.exits), _d("0"))
    assert matrix_total + entry_total == result.closing_total_ghs == _d("695")
    assert exit_total == _d("80")
    assert result.entry_loan_count == 1
    assert result.exit_loan_count == 1


def test_roll_rates_are_opening_weighted_over_matched_loans_only() -> None:
    result = compute_migration(OPENING, CLOSING)
    rates = {(c.from_bucket, c.to_bucket): c.rate_pct for c in result.roll_rates}
    # E (departed) is excluded, so current→current covers exactly A + D.
    assert rates[("current", "current")] == _d("100")
    assert rates[("1_29", "90_179")] == _d("100")
    assert rates[("90_179", "180_359")] == _d("100")


def test_cure_rule_boundaries_six_four_and_bullet() -> None:
    common = {"cure_payments": 6, "cure_payments_semi_annual": 4}
    # Monthly: 5 payments hold, 6 cure.
    assert restructure_holds_npl(
        restructured=True, payments_met=5, repayment_frequency="monthly", **common
    )
    assert not restructure_holds_npl(
        restructured=True, payments_met=6, repayment_frequency="monthly", **common
    )
    # Semi-annual: 3 hold, 4 cure.
    assert restructure_holds_npl(
        restructured=True, payments_met=3, repayment_frequency="semi_annual", **common
    )
    assert not restructure_holds_npl(
        restructured=True, payments_met=4, repayment_frequency="semi_annual", **common
    )
    # Bullet never cures on interim payments.
    assert restructure_holds_npl(
        restructured=True, payments_met=99, repayment_frequency="bullet", **common
    )
    # Unstated evidence holds; a non-restructured loan is never held.
    assert restructure_holds_npl(
        restructured=True, payments_met=None, repayment_frequency="monthly", **common
    )
    assert not restructure_holds_npl(
        restructured=False, payments_met=None, repayment_frequency=None, **common
    )
