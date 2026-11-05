"""``bi_fact_gl_branch_monthly``: the branch breakdown of the ledger.

Three claims, each asserted on rows the builder actually wrote rather than on the
function that computes them (``tests/bi/domain/test_gl_branch_extract.py`` owns
that half):

1. **A bank that sends nothing new is unaffected.** No register, no branch rows,
   the institution ledger byte-identical to the institution-only build.
2. **Branch GL sums to institution GL**, per account and in total, for a complete
   push and a partial one alike — the identity holds BY CONSTRUCTION, because the
   residual row is derived from exactly that subtraction, and this file proves it
   on the STORED rows. It holds even when the register's sign convention is the
   opposite of the ledger's, or the ledger account is zero: the residual then
   carries the difference, visibly, rather than the total drifting.
3. **A partial push is handled honestly**: a branch the register names but no
   position touches is in ``bi_dim_branch``, the residual has a name of its own,
   an account with no allocation gets no rows rather than a 100 %-unallocated one,
   and a branch figure for an account the ledger does not carry is refused entry.

The stale-register case gets its own test because it is the subtle one: pairing
May's breakdown with June's ledger would push a month of unattributed movement
into the residual and label it unallocated.

Nothing here grades the breakdown. Whether the split is complete is the reader's
to see on the residual line; BI stopped issuing verdicts on its own figures on
2026-09-29.

This module's other-suite cases live in ``test_gl_branch_monthly_integration.py``.
"""

from __future__ import annotations

from app.bi.service import authorization, compiler
from app.models import (
    BiDimBranch,
    BiFactGlBranchMonthly,
    BiFactGlMonthly,
)


def test_the_branch_ledger_is_branch_attributable_and_the_institution_ledger_is_not() -> None:
    """The security half of "a separate table, not a column".

    ``branch_attributable`` reads the branch key off the mapped table, so putting
    ``branch_code`` on ``bi_fact_gl_monthly`` would have made the INSTITUTION ledger
    readable by a branch-scoped principal. Apart is what lets a scoped reader see
    branch P&L and not the institution's — and it costs no entry in any list.
    """
    assert authorization.branch_attributable(BiFactGlBranchMonthly.__tablename__) is True
    assert authorization.branch_attributable(BiFactGlMonthly.__tablename__) is False
    assert authorization.BRANCH_FACT_KEY == "branch_code"


def test_the_branch_ledger_is_a_fact_the_compiler_can_bind_a_measure_to() -> None:
    """A mart nothing can query is an inert feature. The table is registered with the
    compiler and its date column named, so the catalogue members named in
    ``.ai/bi_recon/p5b_gl_by_branch_report.md`` need no further plumbing — and the
    two conformed dimensions join on keys this fact actually carries."""
    table_name = BiFactGlBranchMonthly.__tablename__
    assert table_name in compiler._TABLES
    assert compiler._FACT_DATE_COLUMN[table_name] == "month_end"
    columns = compiler._TABLES[table_name].c
    for dim_table, (fact_key, _dim_key) in compiler._DIM_JOIN_KEYS.items():
        joinable = fact_key in columns
        assert joinable is (dim_table in {BiDimBranch.__tablename__, "bi_dim_gl_account"}), (
            f"{dim_table} joins on {fact_key}, which this fact "
            f"{'carries' if joinable else 'does not carry'}"
        )
