"""``bi_fact_gl_branch_monthly``: the branch breakdown of the ledger.

Three claims, each asserted on rows the builder actually wrote rather than on the
function that computes them (``tests/domain/bi/test_gl_branch_extract.py`` owns
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
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.ingestion.reference_schemas import gl_segment_balances
from app.models import (
    BiDimBranch,
    BiFactGlBranchMonthly,
    BiFactGlMonthly,
    CanonicalReferenceRow,
)
from app.models.canonical import CanonicalGlAccount
from app.services.bi import authorization, compiler
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.services.bi.test_gl_monthly import M, _seed_ledger
from tests.services.bi.test_mart_builder import AS_OF, build, new_batch, seed_book
from tests.support.helpers import ORG_1

JUN, MAY = AS_OF, date(2026, 5, 31)
KIND = gl_segment_balances.SCHEMA.kind
RESIDUAL = gl_segment_balances.RESIDUAL_BRANCH_ID


def push_segments(db: Session, as_of: date, rows: list[dict[str, Any]]) -> None:
    """One ``gl_segment_balances`` batch at ``as_of`` — the whole breakdown per push."""
    common = new_batch(db, as_of)
    for index, row in enumerate(rows):
        db.add(
            CanonicalReferenceRow(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                ingestion_batch_id=common["ingestion_batch_id"],
                as_of_date=as_of,
                dataset_kind=KIND,
                row_index=index,
                payload={"as_of_date": as_of.isoformat(), **row},
                source_reference=f"gls#{as_of.isoformat()}#{index}",
                lineage_id=common["lineage_id"],
            )
        )
    db.flush()


def segment(code: str, branch: str, ytd: int, currency: str | None = None) -> dict[str, Any]:
    row = {"gl_account_code": code, "branch_id": branch, "ytd_balance": str(ytd * M)}
    if currency is not None:
        row["currency"] = currency
    return row


def push_units(db: Session, units: dict[str, str]) -> None:
    common = new_batch(db, JUN)
    for index, (unit_id, name) in enumerate(units.items()):
        db.add(
            CanonicalReferenceRow(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                ingestion_batch_id=common["ingestion_batch_id"],
                as_of_date=JUN,
                dataset_kind="business_units",
                row_index=index,
                payload={"business_unit_id": unit_id, "business_unit_name": name},
                source_reference=f"bu#{index}",
                lineage_id=common["lineage_id"],
            )
        )
    db.flush()


def branch_rows(db: Session) -> list[BiFactGlBranchMonthly]:
    return list(
        db.scalars(
            select(BiFactGlBranchMonthly)
            .where(BiFactGlBranchMonthly.bank_id == SAMPLE_BANK_ID)
            .order_by(BiFactGlBranchMonthly.gl_account_code, BiFactGlBranchMonthly.branch_code)
        )
    )


def institution_ytd(db: Session) -> dict[tuple[str, str], Decimal]:
    return {
        (row.gl_account_code, row.currency): row.ytd_rc
        for row in db.scalars(
            select(BiFactGlMonthly).where(BiFactGlMonthly.bank_id == SAMPLE_BANK_ID)
        )
    }


def branch_totals(db: Session) -> dict[tuple[str, str], Decimal]:
    """Σ ``ytd_rc`` over every branch row — reported branches AND the residual —
    per (account, currency): the left-hand side of the identity."""
    totals: dict[tuple[str, str], Decimal] = {}
    for row in branch_rows(db):
        key = (row.gl_account_code, row.currency)
        totals[key] = totals.get(key, Decimal(0)) + row.ytd_rc
    return totals


def assert_identity_holds(db: Session) -> None:
    """Every account the breakdown covers sums to its institution row, exactly."""
    ledger = institution_ytd(db)
    totals = branch_totals(db)
    for key, total in totals.items():
        assert key in ledger, f"{key}: branch rows for an account the ledger does not carry"
        assert total == ledger[key], (key, total, ledger[key])


# --- 1. a bank that sends nothing new -----------------------------------------------------------


def test_no_register_means_no_branch_rows_and_an_unchanged_ledger(db_session: Session) -> None:
    """The institution GL path is not an input to this feature, so a tenant that
    never hears of ``gl_segment_balances`` is unaffected — structurally, not within
    a tolerance."""
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    db_session.commit()
    outcome = build(db_session)

    assert outcome.row_counts["bi_fact_gl_branch_monthly"] == 0
    assert branch_rows(db_session) == []
    assert outcome.row_counts["bi_fact_gl_monthly"] == 6
    assert len(institution_ytd(db_session)) == 6
    assert RESIDUAL not in {row.branch_code for row in db_session.scalars(select(BiDimBranch))}


# --- 2. the identity ----------------------------------------------------------------------------


def test_a_partial_breakdown_sums_to_the_ledger_with_the_rest_on_the_residual(
    db_session: Session,
) -> None:
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu", "BR-102": "Tema"})
    push_segments(
        db_session,
        JUN,
        [
            # 4001 is 330 in the ledger; 200 allocated, 130 left over.
            segment("4001", "BR-101", 120),
            segment("4001", "BR-102", 80),
            # 5301 is 950 (period basis) and fully allocated.
            segment("5301", "BR-101", 500),
            segment("5301", "BR-102", 450),
        ],
    )
    db_session.commit()
    outcome = build(db_session)

    rows = branch_rows(db_session)
    assert outcome.row_counts["bi_fact_gl_branch_monthly"] == len(rows)
    # THE claim: every account the breakdown covers sums to the ledger, exactly.
    assert_identity_holds(db_session)
    per_account = branch_totals(db_session)
    assert per_account[("4001", "")] == Decimal(330 * M)
    assert per_account[("5301", "")] == Decimal(950 * M)
    # Only the covered accounts were built; 4002 / 4101 / 5302 / 6001 get no rows.
    assert set(per_account) == {("4001", ""), ("5301", "")}

    residuals = {row.gl_account_code: row for row in rows if row.branch_code == RESIDUAL}
    assert residuals["4001"].ytd_rc == Decimal(130 * M)
    assert "5301" not in residuals  # fully allocated, so no empty remainder row

    # And the account's own mapping rides on every branch row.
    fours = [row for row in rows if row.gl_account_code == "4001"]
    assert {row.pl_line for row in fours} == {"1a"}
    assert {row.pl_sign for row in fours} == {1}
    assert {row.balance_basis for row in fours} == {"ytd"}
    assert {row.month_end for row in fours} == {JUN}
    assert {row.register_as_of for row in fours} == {JUN}
    assert [row.balance_basis for row in rows if row.gl_account_code == "5301"] == [
        "period",
        "period",
    ]


def test_a_complete_breakdown_of_every_account_leaves_no_residual(db_session: Session) -> None:
    """Every cedi of the month's P&L on a real branch: nothing on the remainder and
    no account left out."""
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu"})
    push_segments(
        db_session,
        JUN,
        [
            segment("4001", "BR-101", 330),
            segment("4002", "BR-101", 150),
            segment("4101", "BR-101", 30, "USD"),
            segment("5301", "BR-101", 950),
            segment("5302", "BR-101", 80),
            segment("6001", "BR-101", 7),
        ],
    )
    db_session.commit()
    build(db_session)

    assert_identity_holds(db_session)
    assert set(branch_totals(db_session)) == set(institution_ytd(db_session))
    assert RESIDUAL not in {row.branch_code for row in branch_rows(db_session)}


def test_the_movement_identity_holds_over_two_pushed_months(db_session: Session) -> None:
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu", "BR-102": "Tema"})
    # 4001: ledger 210 at May, 330 at June — movement 120.
    push_segments(db_session, MAY, [segment("4001", "BR-101", 130), segment("4001", "BR-102", 60)])
    push_segments(db_session, JUN, [segment("4001", "BR-101", 200), segment("4001", "BR-102", 90)])
    db_session.commit()
    build(db_session)

    rows = [row for row in branch_rows(db_session) if row.gl_account_code == "4001"]
    assert not any(row.missing_prior for row in rows)
    assert sum(row.movement_rc or Decimal(0) for row in rows) == Decimal(120 * M)
    institution = db_session.scalar(
        select(BiFactGlMonthly).where(
            BiFactGlMonthly.bank_id == SAMPLE_BANK_ID, BiFactGlMonthly.gl_account_code == "4001"
        )
    )
    assert institution is not None and institution.movement_rc == Decimal(120 * M)
    assert_identity_holds(db_session)


def test_the_identity_holds_by_construction_under_an_inverted_sign_convention(
    db_session: Session,
) -> None:
    """A register sent with flipped signs (audit A11-F4): the residual absorbs
    whatever the branches did not account for, so the branch total still equals the
    ledger — and the residual is then LARGER than the ledger it completes, which is
    visible on the row rather than hidden in a total that drifted.

    Account 4001 is 330 in the fixture ledger. A register of -297 against it
    leaves a residual of 627.
    """
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu", "BR-102": "Tema"})
    push_segments(db_session, JUN, [segment("4001", "BR-101", -297)])
    db_session.commit()
    build(db_session)

    rows = {row.branch_code: row.ytd_rc for row in branch_rows(db_session)}
    assert rows == {"BR-101": Decimal(-297 * M), RESIDUAL: Decimal(627 * M)}
    assert_identity_holds(db_session)


def _seed_income_ledger(db: Session, balances: dict[str, int]) -> None:
    """A June P&L ledger of INCOME accounts at the given balances (× M), mapped to
    no return line — the identity does not need one."""
    common = new_batch(db, JUN)
    for code, balance in balances.items():
        db.add(
            CanonicalGlAccount(
                **common,
                source_reference=f"GL/{code}/{JUN.isoformat()}",
                account_code=code,
                name=f"P&L {code}",
                account_class="INCOME",
                currency="GHS",
                balance=Decimal(balance * M),
                attributes={},
            )
        )
    db.flush()


def test_a_negative_ledger_account_gets_a_negative_residual_beside_a_fully_allocated_one(
    db_session: Session,
) -> None:
    """Account 4100 is −1,000 in the ledger and the register sends +900, so the
    residual is −1,900; beside it 4200 is 10,000 and fully allocated, so it gets no
    remainder row at all. Per account, by construction."""
    seed_book(db_session, live=False)
    _seed_income_ledger(db_session, {"4100": -1000, "4200": 10000})
    push_units(db_session, {"BR-101": "Osu"})
    push_segments(
        db_session, JUN, [segment("4100", "BR-101", 900), segment("4200", "BR-101", 10000)]
    )
    db_session.commit()
    build(db_session)

    rows = {(row.gl_account_code, row.branch_code): row.ytd_rc for row in branch_rows(db_session)}
    assert rows[("4100", RESIDUAL)] == Decimal(-1900 * M)
    assert ("4200", RESIDUAL) not in rows  # fully allocated: no remainder row
    assert_identity_holds(db_session)


def test_a_zero_ledger_account_with_branch_figures_carries_an_offsetting_residual(
    db_session: Session,
) -> None:
    """Ledger 0, branch +500: the residual is −500, so the total is still the
    ledger's zero and the +500 is visibly matched by a −500 nobody attributed."""
    seed_book(db_session, live=False)
    _seed_income_ledger(db_session, {"4100": 0})
    push_units(db_session, {"BR-101": "Osu"})
    push_segments(db_session, JUN, [segment("4100", "BR-101", 500)])
    db_session.commit()
    build(db_session)

    rows = {row.branch_code: row.ytd_rc for row in branch_rows(db_session)}
    assert rows == {"BR-101": Decimal(500 * M), RESIDUAL: Decimal(-500 * M)}
    assert_identity_holds(db_session)


# --- 3. honesty about what is missing -----------------------------------------------------------


def test_a_stale_register_is_refused_rather_than_paired_with_this_months_ledger(
    db_session: Session,
) -> None:
    """May's breakdown against June's ledger would put a whole month of unattributed
    movement on the residual and call it unallocated. No rows is the honest answer,
    and the surface then says which dataset it needs."""
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu"})
    push_segments(db_session, MAY, [segment("4001", "BR-101", 210)])
    db_session.commit()
    outcome = build(db_session)

    assert outcome.row_counts["bi_fact_gl_branch_monthly"] == 0
    assert branch_rows(db_session) == []


def test_a_branch_no_position_touches_still_reaches_the_dimension(db_session: Session) -> None:
    """Otherwise the compiler's ``bi_dim_branch`` join drops the ledger row it labels
    and the branch total silently stops matching the ledger. The residual gets a name
    of its own — it is a computed line, not a branch the register forgot."""
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu"})
    push_segments(db_session, JUN, [segment("4001", "BR-101", 200), segment("4001", "BR-777", 50)])
    db_session.commit()
    build(db_session)

    branches = {
        row.branch_code: row
        for row in db_session.scalars(
            select(BiDimBranch).where(BiDimBranch.bank_id == SAMPLE_BANK_ID)
        )
    }
    assert branches["BR-101"].mapped is True and branches["BR-101"].name == "Osu"
    # Named by the ledger breakdown, unknown to the register: visible, and flagged.
    assert branches["BR-777"].mapped is False
    assert branches[RESIDUAL].name == gl_segment_balances.RESIDUAL_BRANCH_NAME
    assert branches[RESIDUAL].mapped is False
    # Every branch code the mart wrote resolves to a dimension row.
    assert {row.branch_code for row in branch_rows(db_session)} <= set(branches)


def test_a_malformed_register_row_is_skipped_rather_than_guessed_at(db_session: Session) -> None:
    """A payload stored before the kind was enforced can still be malformed. The row
    is left out — inventing a branch or an amount for it would put a figure in a
    board pack that no bank sent — and the residual absorbs it, so the total stays
    the ledger's."""
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu"})
    push_segments(
        db_session,
        JUN,
        [
            segment("4001", "BR-101", 200),
            {"gl_account_code": "4001", "branch_id": "BR-102"},  # no amount
            {"gl_account_code": "4001", "branch_id": RESIDUAL, "ytd_balance": "1"},  # reserved
        ],
    )
    db_session.commit()
    build(db_session)

    rows = {row.branch_code: row for row in branch_rows(db_session)}
    assert set(rows) == {"BR-101", RESIDUAL}
    assert rows[RESIDUAL].ytd_rc == Decimal(130 * M)
    assert sum(row.ytd_rc for row in rows.values()) == Decimal(330 * M)


def test_a_branch_row_for_an_account_the_ledger_does_not_carry_is_refused_at_write_time(
    db_session: Session,
) -> None:
    """A branch figure with no institution row would make a branch total exceed the
    ledger. The builder refuses it (it is an ``orphan``) and reports it."""
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu"})
    push_segments(db_session, JUN, [segment("4001", "BR-101", 200), segment("9999", "BR-101", 5)])
    db_session.commit()
    build(db_session)

    assert {row.gl_account_code for row in branch_rows(db_session)} == {"4001"}
    assert_identity_holds(db_session)


def test_the_register_enters_the_build_fingerprint(db_session: Session) -> None:
    """Otherwise a bank that re-pushes its breakdown keeps being served the OLD
    split — and the old residual — out of an ETag that never changed."""
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu", "BR-102": "Tema"})
    push_segments(db_session, JUN, [segment("4001", "BR-101", 200)])
    db_session.commit()
    first = build(db_session)

    push_segments(db_session, JUN, [segment("4001", "BR-101", 150), segment("4001", "BR-102", 50)])
    db_session.commit()
    second = build(db_session)

    assert second.fingerprint != first.fingerprint
    assert {row.branch_code for row in branch_rows(db_session)} == {
        "BR-101",
        "BR-102",
        RESIDUAL,
    }
    assert sum(row.ytd_rc for row in branch_rows(db_session)) == Decimal(330 * M)


# --- the two seams the separate-table decision turns on ------------------------------------------


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


# --- audit A360 H3: a branch id the mart cannot store is skipped, never truncated --------------


def test_a_segment_row_whose_branch_id_is_wider_than_the_mart_is_skipped_not_truncated(
    db_session: Session,
) -> None:
    """``bi_fact_gl_branch_monthly.branch_code`` is a 120-character PRIMARY KEY
    column. A wider ``branch_id`` used to reach the insert verbatim and, on
    Postgres, fail the whole build; SQLite would have stored it. Neither is the
    branch the bank named: the row is skipped like any malformed register row, the
    residual absorbs its amount so the total is still the ledger's, and no code of
    exactly 120 characters — the truncation — appears anywhere."""
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu"})
    wide = "BR-" + "W" * 118  # 121 characters
    at_limit = "BR-" + "L" * 117  # 120 characters: storable, kept
    push_segments(
        db_session,
        JUN,
        [segment("4001", "BR-101", 200), segment("4001", wide, 50), segment("4001", at_limit, 30)],
    )
    db_session.commit()
    outcome = build(db_session)
    assert outcome.status == "succeeded"

    rows = {row.branch_code: row for row in branch_rows(db_session)}
    assert set(rows) == {"BR-101", at_limit, RESIDUAL}
    assert rows[at_limit].ytd_rc == Decimal(30 * M)
    # 330 − 200 − 30: the skipped 50 is unallocated, not lost.
    assert rows[RESIDUAL].ytd_rc == Decimal(100 * M)
    assert sum(row.ytd_rc for row in rows.values()) == Decimal(330 * M)
    dimension_codes = {
        row.branch_code
        for row in db_session.scalars(
            select(BiDimBranch).where(BiDimBranch.bank_id == SAMPLE_BANK_ID)
        )
    }
    assert at_limit in dimension_codes
    assert wide not in dimension_codes
    assert wide[:120] not in dimension_codes
    assert_identity_holds(db_session)
