"""``bi_fact_gl_branch_monthly`` and R11: the branch breakdown of the ledger.

Four claims, each asserted on rows the builder actually wrote rather than on the
function that computes them (``tests/domain/bi/test_gl_branch_extract.py`` owns
that half):

1. **A bank that sends nothing new reconciles exactly as before.** No register,
   no branch rows, R4 byte-identical to the institution-only build, and R11 green
   over zero accounts — the absence reported as an absence, never as a pass over
   a table of unallocated lines.
2. **Branch GL sums to institution GL**, per account and in total, for a complete
   push and a partial one alike, and R11 grades the partial case amber because the
   total is right and the breakdown is incomplete.
3. **A partial push is handled honestly**: a branch the register names but no
   position touches is in ``bi_dim_branch``, the residual has a name of its own,
   and an account with no allocation gets no rows rather than a 100 %-unallocated
   one.
4. **R11 can fire.** A row edited to break the identity turns it red, and a
   branch figure for an account the ledger does not carry is refused entry.

The stale-register case gets its own test because it is the subtle one: pairing
May's breakdown with June's ledger would push a month of unattributed movement
into the residual and label it unallocated.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.gl import pl_mapping
from app.domain.ingestion.reference_schemas import gl_segment_balances
from app.models import (
    Bank,
    BiDimBranch,
    BiFactGlBranchMonthly,
    BiFactGlMonthly,
    CanonicalReferenceRow,
)
from app.services.bi import authorization, compiler, reconciliation
from tests.api.helpers import ORG_1
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.services.bi.test_gl_monthly import M, _seed_ledger
from tests.services.bi.test_mart_builder import AS_OF, CTX, build, new_batch, seed_book

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


def r11(db: Session) -> reconciliation.CheckResult:
    return reconciliation.check_r11_gl_branch_identity(db, ORG_1, SAMPLE_BANK_ID, JUN)


def r4(db: Session) -> reconciliation.CheckResult:
    row = db.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert row is not None
    return reconciliation.check_r4_gl_pl(db, CTX, row, JUN)


# --- 1. a bank that sends nothing new -----------------------------------------------------------


def test_no_register_means_no_branch_rows_and_an_unchanged_r4(db_session: Session) -> None:
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
    baseline_r4 = r4(db_session)
    assert baseline_r4.status == reconciliation.GREEN

    check = r11(db_session)
    assert check.status == reconciliation.GREEN
    # Green, but never mistakable for "the branch breakdown reconciles": the detail
    # says nothing was compared, and says which figures exist.
    assert check.detail["accounts_compared"] == 0
    assert check.detail["accounts_in_ledger"] == 6
    assert "no branch breakdown" in check.detail["reason"]
    assert RESIDUAL not in {row.branch_code for row in db_session.scalars(select(BiDimBranch))}


# --- 2. the identity ----------------------------------------------------------------------------


def test_a_partial_breakdown_sums_to_the_ledger_and_r11_calls_it_amber(
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
    ledger = institution_ytd(db_session)
    per_account: dict[tuple[str, str], Decimal] = {}
    for row in rows:
        key = (row.gl_account_code, row.currency)
        per_account[key] = per_account.get(key, Decimal(0)) + row.ytd_rc
    # THE claim: every account the breakdown covers sums to the ledger, exactly.
    for key, total in per_account.items():
        assert total == ledger[key], key
    assert per_account[("4001", "")] == Decimal(330 * M)
    assert per_account[("5301", "")] == Decimal(950 * M)
    # Only the covered accounts were built; 4002 / 4101 / 5302 / 6001 get no rows.
    assert set(per_account) == {("4001", ""), ("5301", "")}

    residuals = {row.gl_account_code: row for row in rows if row.branch_code == RESIDUAL}
    assert residuals["4001"].ytd_rc == Decimal(130 * M)
    assert "5301" not in residuals  # fully allocated, so no empty remainder row

    check = r11(db_session)
    assert check.status == reconciliation.AMBER  # the total is right; the split is partial
    assert check.difference == Decimal(0)
    assert Decimal(check.detail["unattributed_share_pct"]) > Decimal(0)
    assert check.detail["accounts_compared"] == 2

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


def test_a_complete_breakdown_of_every_account_is_green(db_session: Session) -> None:
    """Green requires the identity AND full attribution: every cedi of the month's
    P&L on a real branch, nothing on the remainder and no account left out."""
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

    check = r11(db_session)
    assert check.status == reconciliation.GREEN
    assert Decimal(check.detail["unattributed_share_pct"]) == Decimal(0)
    assert check.detail["accounts_compared"] == check.detail["accounts_in_ledger"] == 6
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
    assert r11(db_session).status == reconciliation.AMBER  # still only 4001 is covered


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
    assert r11(db_session).detail["accounts_compared"] == 0


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


# --- 4. R11 can fire ----------------------------------------------------------------------------


def test_r11_turns_red_when_a_stored_branch_row_breaks_the_identity(
    db_session: Session,
) -> None:
    """The guard's self-proving case. The builder cannot produce this state — the
    residual is derived from exactly this subtraction — which is why the check is
    worth having: it proves the construction on the STORED rows."""
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu"})
    push_segments(db_session, JUN, [segment("4001", "BR-101", 200)])
    db_session.commit()
    build(db_session)
    assert r11(db_session).status == reconciliation.AMBER

    tampered = db_session.scalar(
        select(BiFactGlBranchMonthly).where(
            BiFactGlBranchMonthly.bank_id == SAMPLE_BANK_ID,
            BiFactGlBranchMonthly.branch_code == "BR-101",
        )
    )
    assert tampered is not None
    tampered.ytd_rc = tampered.ytd_rc + Decimal(M)
    db_session.flush()

    check = r11(db_session)
    assert check.status == reconciliation.RED
    assert check.difference == Decimal(M)
    assert check.detail["mismatches"][0]["account"] == "4001"
    assert check.tolerance == Decimal(0)


def test_r11_turns_red_on_a_branch_row_the_ledger_has_no_account_for(
    db_session: Session,
) -> None:
    """A branch figure with no institution row would make a branch total exceed the
    ledger. The builder refuses it at write time (it is an ``orphan``); the check
    refuses it at read time, so neither half can be the only defence."""
    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu"})
    push_segments(db_session, JUN, [segment("4001", "BR-101", 200), segment("9999", "BR-101", 5)])
    db_session.commit()
    build(db_session)

    # Write time: the unknown account never reaches the mart.
    assert {row.gl_account_code for row in branch_rows(db_session)} == {"4001"}
    assert r11(db_session).status == reconciliation.AMBER

    # Read time: the same row inserted behind the builder's back is convicted.
    template = branch_rows(db_session)[0]
    db_session.add(
        BiFactGlBranchMonthly(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            month_end=template.month_end,
            gl_account_code="9999",
            branch_code="BR-101",
            currency="",
            calendar_month=template.calendar_month,
            account_class="INCOME",
            ytd_rc=Decimal(5 * M),
            prior_ytd_rc=None,
            movement_rc=None,
            missing_prior=True,
            balance_basis=pl_mapping.YTD,
            pl_line=None,
            pl_sign=None,
            register_as_of=JUN,
            builder_version=template.builder_version,
            built_at=template.built_at,
        )
    )
    db_session.flush()

    check = r11(db_session)
    assert check.status == reconciliation.RED
    assert check.detail["mismatches"][0]["account"] == "9999"
    assert check.detail["mismatches"][0]["ledger"] is None


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


def test_an_inverted_sign_convention_is_RED_and_not_amber_at_a_nonsense_share(
    db_session: Session,
) -> None:
    """Audit A11-F4: a register sent with flipped signs reconciled, and read amber.

    The identity cannot catch this, and that is the point. The residual absorbs
    whatever the branches did not account for, so the sum equals the ledger BY
    CONSTRUCTION however wrong the reported figures are. What gives it away is the
    coverage share: a ledger of one sign against a register of the other leaves a
    residual LARGER than the ledger, so the share exceeds 100% — which a partial
    breakdown mathematically cannot do.

    Amber and red are different requests to the reader. Amber says part of the
    ledger is not broken down yet, which they answer by sending more of the
    register. This needs them to fix the register they already sent. Reporting the
    second as the first, at "190% unattributed", tells them to do the wrong thing.

    Account 4001 is 330 in the fixture ledger, as the amber test above states. A
    register of -297 against it leaves a residual of 627, i.e. 190% of the ledger —
    the audit's own numbers, reached from the other side because this fixture's
    ledger is all positive.
    """

    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu", "BR-102": "Tema"})
    push_segments(db_session, JUN, [segment("4001", "BR-101", -297)])
    db_session.commit()
    build(db_session)

    ledger = institution_ytd(db_session)
    # Keyed on (account, currency); the fixture's currency is read rather than
    # assumed, because jurisdiction is data and a literal here would be a leak.
    account_rows = {key: value for key, value in ledger.items() if key[0] == "4001"}
    assert account_rows and all(value > 0 for value in account_rows.values()), ledger
    outcome = r11(db_session)
    share = Decimal(outcome.detail["unattributed_share_pct"])
    assert share > Decimal("100"), (
        f"share is {share}; the inverted register should push it past 100%, and if it "
        "cannot then this test is not exercising the fault"
    )
    assert outcome.status == reconciliation.RED, outcome.detail
    assert "sign convention" in outcome.detail["reason"]


def test_a_partial_breakdown_of_the_RIGHT_sign_is_still_only_amber(
    db_session: Session,
) -> None:
    """The control for the test above. Without it, a change that reddened every
    incomplete breakdown would pass and would nag every bank mid-rollout.

    The same account and the same proportion as the inverted case, correctly signed:
    +297 of 330, leaving 33 unattributed, i.e. 10%.
    """

    seed_book(db_session, live=False)
    _seed_ledger(db_session)
    push_units(db_session, {"BR-101": "Osu"})
    push_segments(db_session, JUN, [segment("4001", "BR-101", 297)])
    db_session.commit()
    build(db_session)

    outcome = r11(db_session)
    share = Decimal(outcome.detail["unattributed_share_pct"])
    assert Decimal("0") < share < Decimal("100"), share
    assert outcome.status == reconciliation.AMBER, outcome.detail
    assert "reason" not in outcome.detail or "sign convention" not in outcome.detail["reason"]
