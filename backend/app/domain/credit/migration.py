"""Monthly credit-migration matrix + DPD roll rates (pure; credit PR-5).

BoG Notice BG/GOV/SEC/2025/23 Appendix II table 2 mandates a MONTHLY credit
migration matrix over three states — performing (not restructured), performing
restructured, and NPL — measured as flows between two consecutive month-end
books. A matrix without entry/exit legs cannot reconcile to the stocks it sits
between (new disbursements enter, write-offs and settlements leave), so both
legs are first-class here rather than a footnote.

The finer 7-bucket days-past-due ROLL-RATE matrix is the management view the
regulatory 3×3 summarises: per bucket, where did last month's exposure go.
Roll rates compare each loan with ITSELF across the two dates (joined on the
loan key); loans present on only one date are the entry/exit legs and are
excluded from the rates — a rate over a changing population is not a rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: The Notice's three states, in its printed order.
STATE_PERFORMING = "performing"
STATE_PERFORMING_RESTRUCTURED = "performing_restructured"
STATE_NPL = "npl"
MIGRATION_STATES: tuple[str, ...] = (
    STATE_PERFORMING,
    STATE_PERFORMING_RESTRUCTURED,
    STATE_NPL,
)

#: The analytical DPD bands (mirrors the classification service's `_DPD_BANDS`).
ROLL_BUCKETS: tuple[str, ...] = (
    "current",
    "1_29",
    "30_59",
    "60_89",
    "90_179",
    "180_359",
    "360_plus",
)

_ZERO = Decimal("0")
_PCT_Q = Decimal("0.0001")
_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class LoanState:
    """One loan at one month-end, reduced to what migration needs."""

    loan_key: str
    exposure_ghs: Decimal
    #: One of ROLL_BUCKETS, or None when the loan states no DPD (excluded from
    #: roll rates; still in the 3×3 via its state).
    dpd_bucket: str | None
    non_performing: bool
    #: Restructured AND currently performing (i.e. cured under the Notice's
    #: consecutive-payment rule). A restructured facility still held NPL is
    #: ``non_performing=True`` and lands in the NPL state.
    restructured_performing: bool = False

    @property
    def state(self) -> str:
        if self.non_performing:
            return STATE_NPL
        if self.restructured_performing:
            return STATE_PERFORMING_RESTRUCTURED
        return STATE_PERFORMING


@dataclass(frozen=True)
class MigrationCell:
    from_state: str
    to_state: str
    exposure_ghs: Decimal
    loan_count: int


@dataclass(frozen=True)
class RollRateCell:
    from_bucket: str
    to_bucket: str
    exposure_ghs: Decimal
    loan_count: int
    #: Exposure-weighted share of the from-bucket's opening (matched) exposure.
    rate_pct: Decimal


@dataclass(frozen=True)
class MigrationResult:
    opening_total_ghs: Decimal
    closing_total_ghs: Decimal
    #: 3×3 state flows over loans present at BOTH dates.
    matrix: tuple[MigrationCell, ...]
    #: New loans (present only at closing), by closing state.
    entries: tuple[MigrationCell, ...]
    #: Departed loans (present only at opening), by opening state — write-offs,
    #: settlements, withdrawals; the events plane says which.
    exits: tuple[MigrationCell, ...]
    roll_rates: tuple[RollRateCell, ...]
    matched_loan_count: int
    entry_loan_count: int
    exit_loan_count: int

    def cell(self, from_state: str, to_state: str) -> MigrationCell | None:
        return next(
            (c for c in self.matrix if c.from_state == from_state and c.to_state == to_state),
            None,
        )


def compute_migration(
    opening: list[LoanState] | tuple[LoanState, ...],
    closing: list[LoanState] | tuple[LoanState, ...],
) -> MigrationResult:
    """Flows between two month-end books, joined on the loan key."""
    opening_by_key = {state.loan_key: state for state in opening}
    closing_by_key = {state.loan_key: state for state in closing}

    matrix: dict[tuple[str, str], tuple[Decimal, int]] = {}
    entries: dict[str, tuple[Decimal, int]] = {}
    exits: dict[str, tuple[Decimal, int]] = {}
    rolls: dict[tuple[str, str], tuple[Decimal, int]] = {}
    bucket_openings: dict[str, Decimal] = {}

    matched = 0
    for key, before in opening_by_key.items():
        after = closing_by_key.get(key)
        if after is None:
            amount, count = exits.get(before.state, (_ZERO, 0))
            # Exits are measured at their OPENING exposure — what left the book.
            exits[before.state] = (amount + before.exposure_ghs, count + 1)
            continue
        matched += 1
        cell_key = (before.state, after.state)
        amount, count = matrix.get(cell_key, (_ZERO, 0))
        # Flows are measured at CLOSING exposure (the Notice reports the
        # month-end stock of what moved).
        matrix[cell_key] = (amount + after.exposure_ghs, count + 1)
        if before.dpd_bucket is not None and after.dpd_bucket is not None:
            bucket_openings[before.dpd_bucket] = (
                bucket_openings.get(before.dpd_bucket, _ZERO) + before.exposure_ghs
            )
            roll_key = (before.dpd_bucket, after.dpd_bucket)
            r_amount, r_count = rolls.get(roll_key, (_ZERO, 0))
            rolls[roll_key] = (r_amount + before.exposure_ghs, r_count + 1)

    for key, after in closing_by_key.items():
        if key not in opening_by_key:
            amount, count = entries.get(after.state, (_ZERO, 0))
            entries[after.state] = (amount + after.exposure_ghs, count + 1)

    return MigrationResult(
        opening_total_ghs=sum((s.exposure_ghs for s in opening), _ZERO),
        closing_total_ghs=sum((s.exposure_ghs for s in closing), _ZERO),
        matrix=tuple(
            MigrationCell(from_state=f, to_state=t, exposure_ghs=amount, loan_count=count)
            for (f, t), (amount, count) in sorted(matrix.items())
        ),
        entries=tuple(
            MigrationCell(
                from_state="new", to_state=state, exposure_ghs=amount, loan_count=count
            )
            for state, (amount, count) in sorted(entries.items())
        ),
        exits=tuple(
            MigrationCell(
                from_state=state, to_state="departed", exposure_ghs=amount, loan_count=count
            )
            for state, (amount, count) in sorted(exits.items())
        ),
        roll_rates=tuple(
            RollRateCell(
                from_bucket=f,
                to_bucket=t,
                exposure_ghs=amount,
                loan_count=count,
                rate_pct=(
                    (amount / bucket_openings[f] * _HUNDRED).quantize(_PCT_Q)
                    if bucket_openings.get(f, _ZERO) > _ZERO
                    else _ZERO
                ),
            )
            for (f, t), (amount, count) in sorted(rolls.items())
        ),
        matched_loan_count=matched,
        entry_loan_count=sum(count for _, count in entries.values()),
        exit_loan_count=sum(count for _, count in exits.values()),
    )
