"""``app.services.bi.reconciliation``: R1–R12 on the canonical fixture bank.

Each check compares what the builder wrote against what the platform already
computed: the live credit NPL, the live balance-sheet lines, the snapshot
count, the balance-sheet identity control's own record. On the fixture with
its live plane materialised, R1/R2/R3/R5 are green by construction; R6/R7 are
amber because the fixture carries one unconverted guarantee and four
positions with no branch; R9 is amber because the fixture's book balances
only under its governed exception. Without a live plane the checks that need
one are grey — never green. R4 lives in ``test_gl_monthly.py`` beside the mart
it grades.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.domain.bi.catalogue import catalogue
from app.domain.capital import loan_classification as classification_engine
from app.models import (
    Bank,
    BiFactEngineMetric,
    BiFactPositionDaily,
    BiReconciliationResult,
    CanonicalPositionSnapshot,
    CurrentFinancialFact,
    LiveMetric,
)
from app.models.bi import RECONCILIATION_CHECK_IDS
from app.schemas.bi import BiQuery, BiTime
from app.services import pipeline
from app.services.bi import reconciliation
from app.services.bi.compiler import compile_query
from app.services.bi.reconciliation import (
    AMBER,
    ARREARS_COMPLETENESS,
    DPD_COMPLETENESS,
    GREEN,
    GREY,
    RED,
    CheckResult,
    overall_trust,
    trust_for,
    trust_of,
)
from tests.api.helpers import ORG_1
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.services.bi.test_mart_builder import (
    AS_OF,
    CTX,
    FIXTURE_DEPOSITS_RC,
    FIXTURE_LOANS_RC,
    FIXTURE_ROWS,
    MID_MONTH,
    add_position,
    build,
    daily_rows,
    new_batch,
    seed_book,
)


def seed_live_only(db: Session) -> None:
    """Materialise the live plane AFTER extra canonical rows were added."""
    bank = db.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    outcome = pipeline.recompute_live(db, CTX, bank, AS_OF)
    assert not outcome.modules_failed, outcome.modules_failed


def _results(db: Session, as_of: date = AS_OF) -> dict[str, BiReconciliationResult]:
    return {
        row.check_id: row
        for row in db.scalars(
            select(BiReconciliationResult).where(
                BiReconciliationResult.bank_id == SAMPLE_BANK_ID,
                BiReconciliationResult.as_of_date == as_of,
            )
        )
    }


def test_the_fixture_reconciles_to_the_engine_and_the_live_balance_sheet(
    db_session: Session,
) -> None:
    seed_book(db_session)
    outcome = build(db_session)
    results = _results(db_session)
    assert set(results) == set(RECONCILIATION_CHECK_IDS)

    r1 = results["R1"]
    assert r1.status == GREEN
    live = db_session.scalar(
        select(LiveMetric).where(
            LiveMetric.bank_id == SAMPLE_BANK_ID, LiveMetric.module == "credit"
        )
    )
    assert live is not None
    assert r1.rhs == Decimal(str(live.metrics["npl_ratio_pct"]))
    # 3M non-performing over 84.85M, rounded as the engine rounds its fraction
    assert r1.lhs == (Decimal(3_000_000) / FIXTURE_LOANS_RC).quantize(Decimal("0.000001")) * 100
    assert r1.tolerance == Decimal("0.000001")
    assert r1.detail["classification_exposure_rc"] == str(
        FIXTURE_LOANS_RC.quantize(Decimal("0.000001"))
    )

    r2 = results["R2"]
    assert (r2.status, r2.lhs, r2.rhs) == (GREEN, FIXTURE_LOANS_RC, FIXTURE_LOANS_RC)
    r3 = results["R3"]
    assert (r3.status, r3.lhs, r3.rhs) == (GREEN, FIXTURE_DEPOSITS_RC, FIXTURE_DEPOSITS_RC)
    assert set(r3.detail["lines"]) == set(reconciliation.DEPOSIT_LINES)
    r5 = results["R5"]
    assert (r5.status, r5.lhs, r5.rhs) == (GREEN, Decimal(FIXTURE_ROWS), Decimal(FIXTURE_ROWS))

    r6 = results["R6"]
    assert (r6.status, r6.lhs, r6.detail["by_currency"]) == (AMBER, Decimal(1), {"USD": 1})
    r7 = results["R7"]
    assert r7.status == AMBER
    # SEC/1 15M + SEC/2 20M + IBP/1 5M + IBB/1 6M carry no branch = 46M of 211.42M
    assert r7.detail["no_branch_rc"] == "46000000"
    assert r7.detail["unmapped_branch_rc"] == "0"
    assert r7.detail["unmapped_codes"] == []
    r8 = results["R8"]
    assert (r8.status, r8.difference) == (GREEN, Decimal(0))
    assert r8.detail == {"mart_as_of": AS_OF.isoformat(), "live_as_of": AS_OF.isoformat()}
    r9 = results["R9"]
    assert r9.status == AMBER  # the fixture's book balances only under its governed exception
    assert r9.detail["record"]["status"] == "exception_applied"
    assert r9.lhs is not None and r9.rhs is not None and r9.difference == r9.rhs - r9.lhs

    # R10 (D-042): the fixture's loans state no days_past_due AT ALL, so every
    # PAR figure over this book would read 0 % — the badge must say so.
    r10 = results[DPD_COMPLETENESS]
    assert r10.status == RED
    assert r10.lhs == Decimal(100)
    assert Decimal(r10.detail["missing_share_of_exposure_pct"]) == Decimal(100)
    assert outcome.trust[DPD_COMPLETENESS] == RED
    assert outcome.trust["overall"] == RED
    assert outcome.trust == {
        **{row.check_id: row.status for row in results.values()},
        "overall": RED,
    }
    # R10 is stored like every other check, so the stored badge IS the build's.
    assert trust_for(db_session, ORG_1, SAMPLE_BANK_ID, AS_OF) == outcome.trust
    assert all(
        row.builder_version >= 1 and row.evaluated_at is not None for row in results.values()
    )


def test_an_unconverted_loan_keeps_r1_and_r2_green_under_their_own_fx_rules(
    db_session: Session,
) -> None:
    """D-015 end to end: R1 counts the loan at 0 like the engine, R2 excludes it like the
    derivation — both stay exact."""
    seed_book(db_session, live=False)
    common = new_batch(db_session, AS_OF)
    add_position(
        db_session,
        common,
        "LOAN/XFC",
        "LOAN",
        "USD",
        balance="750000",
        product="LN.CORP.5Y",
        stage=3,
    )
    db_session.commit()
    seed_live_only(db_session)
    build(db_session)
    results = _results(db_session)
    assert results["R1"].status == GREEN
    assert results["R2"].status == GREEN
    assert results["R2"].lhs == FIXTURE_LOANS_RC  # the unconverted loan is in neither side
    assert results["R6"].detail["by_currency"] == {"USD": 2}


def test_without_a_live_plane_the_checks_that_need_one_are_grey_never_green(
    db_session: Session,
) -> None:
    seed_book(db_session, live=False)
    outcome = build(db_session)
    results = _results(db_session)
    assert results["R1"].status == GREY
    assert results["R2"].status == GREY
    assert results["R3"].status == GREY
    assert results["R8"].status == GREY
    assert results["R9"].status == GREY
    assert results["R9"].detail["reason"] == "No live plane exists for this bank."
    assert results["R4"].status == GREY  # the fixture holds no P&L ledger
    assert results["R5"].status == GREEN  # completeness needs no live plane
    assert results[DPD_COMPLETENESS].status == RED  # the fixture states no arrears data
    assert outcome.trust["overall"] == RED
    assert db_session.scalar(select(CurrentFinancialFact.id).limit(1)) is None


def test_a_mart_behind_the_live_plane_is_amber_and_r1_grey(db_session: Session) -> None:
    seed_book(db_session)  # live plane at AS_OF
    common = new_batch(db_session, MID_MONTH)
    add_position(
        db_session,
        common,
        "DEP/MID",
        "DEPOSIT",
        "GHS",
        balance="5",
        balance_ghs="5",
        product="DEP.RET.CUR",
    )
    db_session.commit()
    build(db_session, MID_MONTH)
    results = _results(db_session, MID_MONTH)
    assert results["R8"].status == AMBER
    assert results["R8"].difference == Decimal((AS_OF - MID_MONTH).days)
    assert results["R1"].status == GREY  # the live credit figure is for another date
    assert results["R2"].status == GREY
    assert results["R2"].detail["live_as_of"] == AS_OF.isoformat()
    assert results["R5"].status == GREEN


def test_a_check_that_raises_is_grey_with_its_error_not_a_build_failure(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_book(db_session, live=False)

    def explode(*_args: object, **_kwargs: object) -> CheckResult:
        raise RuntimeError("no such column")

    monkeypatch.setattr(reconciliation, "check_r5_completeness", explode)
    outcome = build(db_session)
    assert outcome.status == "succeeded"
    r5 = _results(db_session)["R5"]
    assert r5.status == GREY
    assert r5.detail["reason"].startswith("The check could not run: RuntimeError: no such column")


def test_a_red_check_is_persisted_and_dominates_trust(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_book(db_session)
    real = reconciliation.check_r2_loans

    def shifted(*args: object, **kwargs: object) -> CheckResult:
        result = real(*args, **kwargs)  # type: ignore[arg-type]
        assert result.status == GREEN
        return CheckResult(
            "R2", RED, result.lhs, (result.rhs or Decimal(0)) + 1, Decimal(-1), result.tolerance, {}
        )

    monkeypatch.setattr(reconciliation, "check_r2_loans", shifted)
    outcome = build(db_session)
    assert outcome.trust["R2"] == RED
    assert outcome.trust["overall"] == RED
    assert _results(db_session)["R2"].difference == Decimal(-1)


def test_overall_trust_precedence_and_missing_checks() -> None:
    assert overall_trust([GREEN] * 9) == GREEN
    assert overall_trust([GREEN, AMBER, GREEN]) == AMBER
    assert overall_trust([AMBER, RED, GREY]) == RED
    assert overall_trust([GREEN, GREY]) == GREY
    assert overall_trust([GREEN, None]) == GREY
    assert overall_trust([]) == GREEN
    partial = trust_of({"R1": CheckResult("R1", GREEN)})
    assert partial["R1"] == GREEN
    assert all(partial[check] == GREY for check in reconciliation.CHECK_IDS if check != "R1")
    assert partial["overall"] == GREY
    assert set(partial) == set(reconciliation.CHECK_IDS) | {"overall"}


def test_trust_for_an_unbuilt_date_is_grey(db_session: Session) -> None:
    seed_book(db_session, live=False)
    trust = trust_for(db_session, ORG_1, SAMPLE_BANK_ID, AS_OF)
    assert trust == {**dict.fromkeys(RECONCILIATION_CHECK_IDS, GREY), "overall": GREY}


def test_tolerances_are_stated_for_every_check_and_r1_mirrors_the_engines_rounding() -> None:
    assert set(reconciliation.TOLERANCES) == set(reconciliation.CHECK_IDS)
    assert reconciliation.TOLERANCES["R1"] == Decimal("0.000001")
    assert reconciliation.TOLERANCES["R2"] == Decimal("0.0001")
    assert reconciliation.TOLERANCES["R3"] == Decimal("0.0005")
    assert reconciliation.TOLERANCES["R4"] == Decimal(0)
    assert reconciliation.TOLERANCES["R5"] == Decimal(0)
    assert reconciliation.TOLERANCES["R6"] is None and reconciliation.TOLERANCES["R7"] is None
    assert reconciliation.ENGINE_RATIO_QUANTUM == classification_engine._RATIO_Q  # noqa: SLF001 - parity pin


def test_balance_sheet_line_names_are_the_derivations_own() -> None:
    from pathlib import Path  # noqa: PLC0415

    source = (Path(__file__).parents[3] / "app" / "services" / "fact_derivation.py").read_text()
    assert f'bs("{reconciliation.LOANS_LINE}"' in source
    for line in reconciliation.DEPOSIT_LINES:
        assert f'"{line}"' in source, line


# --- R10 dpd completeness (D-042) -------------------------------------------------------------


def _r10(db: Session, as_of: date) -> CheckResult:
    """R10 over the built slice, from the check itself — and proven to be what
    the build persisted, so the status a reader sees is the one measured here."""
    result = reconciliation.check_r10_dpd_completeness(db, ORG_1, SAMPLE_BANK_ID, as_of)
    assert _results(db, as_of)[DPD_COMPLETENESS].status == result.status
    return result


def _loan_at(
    db: Session,
    reference: str,
    *,
    days_past_due: str | None,
    balance: str = "1000000",
    currency: str = "GHS",
) -> None:
    """One accepted LOAN at ``MID_MONTH``, with or without an arrears figure.

    A non-base ``currency`` is left UNCONVERTED on purpose (no ``balance_ghs``),
    which is how a row lands with ``classification_exposure_rc = 0`` under the
    D-015 classification rule.
    """
    common = new_batch(db, MID_MONTH)
    extra = {"days_past_due": days_past_due} if days_past_due is not None else None
    add_position(
        db,
        common,
        reference,
        "LOAN",
        currency,
        balance=balance,
        balance_ghs=balance if currency == "GHS" else None,
        product="LN.CORP.5Y",
        stage=1,
        extra=extra,
    )


def test_r10_is_green_when_every_loan_states_its_arrears(db_session: Session) -> None:
    seed_book(db_session, live=False)
    _loan_at(db_session, "LOAN/DPD1", days_past_due="0")
    _loan_at(db_session, "LOAN/DPD2", days_past_due="95")
    db_session.commit()
    outcome = build(db_session, MID_MONTH)
    assert outcome.trust[DPD_COMPLETENESS] == GREEN
    r10 = _r10(db_session, MID_MONTH)
    assert r10.status == GREEN
    assert r10.lhs == Decimal(0)
    assert r10.detail["loan_rows"] == 2
    assert r10.detail["loans_without_dpd_band"] == 0


def test_r10_is_amber_when_some_loans_state_it_and_red_when_none_do(db_session: Session) -> None:
    seed_book(db_session, live=False)
    _loan_at(db_session, "LOAN/DPD1", days_past_due="12")
    _loan_at(db_session, "LOAN/SILENT", days_past_due=None)
    db_session.commit()
    outcome = build(db_session, MID_MONTH)
    r10 = _r10(db_session, MID_MONTH)
    assert r10.status == AMBER
    assert r10.lhs == Decimal(50)  # both limbs agree here: 1 of 2 rows, half the exposure
    assert r10.detail["loan_rows"] == 2
    assert r10.detail["loans_without_dpd_band"] == 1
    assert Decimal(r10.detail["missing_share_pct"]) == Decimal(50)
    assert Decimal(r10.detail["missing_share_of_exposure_pct"]) == Decimal(50)
    assert Decimal(r10.detail["worst_share_pct"]) == Decimal(50)
    assert "reason" not in r10.detail  # amber is a coverage statement, not a verdict
    assert outcome.trust[DPD_COMPLETENESS] == AMBER

    # the whole book silent: RED, and the reason names what a reader would misread
    _loan_at(db_session, "LOAN/SILENT2", days_past_due=None)
    db_session.execute(
        update(CanonicalPositionSnapshot)
        .where(CanonicalPositionSnapshot.source_reference == "LOAN/DPD1")
        .values(attributes={"balance_ghs": "1000000"})
    )
    db_session.commit()
    red = build(db_session, MID_MONTH)
    r10 = _r10(db_session, MID_MONTH)
    assert r10.status == RED
    assert r10.lhs == Decimal(100)
    assert "no loan states days past due" in r10.detail["reason"]
    assert red.trust["overall"] == RED


def test_r10_is_grey_on_a_day_with_no_loans(db_session: Session) -> None:
    seed_book(db_session, live=False)
    common = new_batch(db_session, MID_MONTH)
    add_position(
        db_session,
        common,
        "DEP/ONLY",
        "DEPOSIT",
        "GHS",
        balance="500",
        balance_ghs="500",
        product="DEP.RET.CUR",
    )
    db_session.commit()
    outcome = build(db_session, MID_MONTH)
    assert outcome.trust[DPD_COMPLETENESS] == GREY
    r10 = _r10(db_session, MID_MONTH)
    assert r10.status == GREY
    assert r10.detail["reason"] == "The day's slice carries no loan rows."
    assert r10.lhs is None


def test_r10_is_evaluated_and_stored_through_the_same_vocabulary(db_session: Session) -> None:
    """Storage and evaluation have converged: R10 is in the model tuple (and so in
    migration 0066's CHECK), so it is written like every other check and the
    stored badge can no longer differ from the evaluated one by an R10-shaped
    gap. The two names stay distinct only so a FUTURE check written ahead of its
    vocabulary degrades instead of failing the build."""
    assert DPD_COMPLETENESS in RECONCILIATION_CHECK_IDS
    assert reconciliation.CHECK_IDS == RECONCILIATION_CHECK_IDS
    assert reconciliation.STORABLE_CHECK_IDS == reconciliation.CHECK_IDS
    assert len(set(reconciliation.CHECK_IDS)) == len(reconciliation.CHECK_IDS)  # no duplicate id
    assert DPD_COMPLETENESS in reconciliation.TOLERANCES
    assert reconciliation.TOLERANCES[DPD_COMPLETENESS] is None  # a threshold, not a tolerance
    assert len(DPD_COMPLETENESS) <= 4  # String(4): the id fits the column

    seed_book(db_session, live=False)
    outcome = build(db_session)
    stored = _results(db_session)
    assert set(stored) == set(reconciliation.CHECK_IDS)
    assert stored[DPD_COMPLETENESS].status == outcome.trust[DPD_COMPLETENESS]
    assert trust_for(db_session, ORG_1, SAMPLE_BANK_ID, AS_OF) == outcome.trust


def test_r10_is_red_when_a_small_count_of_loans_carries_all_the_exposure(
    db_session: Session,
) -> None:
    """D-049, the case a row-share badge got wrong: 1 of 4 loans lacks arrears
    data — mild amber by count — but it is the ENTIRE book by value, so the
    engine's PAR ratio is understated completely and the badge must read red."""
    seed_book(db_session, live=False)
    _loan_at(db_session, "LOAN/BIG", days_past_due=None, balance="900000000")
    for index in range(3):
        _loan_at(db_session, f"LOAN/TINY{index}", days_past_due="0", balance="0")
    db_session.commit()
    outcome = build(db_session, MID_MONTH)
    r10 = _r10(db_session, MID_MONTH)
    assert r10.status == RED
    assert Decimal(r10.detail["missing_share_pct"]) == Decimal(25)  # the count alone: amber
    assert Decimal(r10.detail["missing_share_of_exposure_pct"]) == Decimal(100)
    assert r10.lhs == Decimal(100)  # the worse limb decides
    assert "carry the whole book's exposure" in r10.detail["reason"]
    assert outcome.trust[DPD_COMPLETENESS] == RED


def test_r10_is_never_softened_by_a_silent_exposure_limb(db_session: Session) -> None:
    """A book whose loans are all unconverted foreign currency carries
    ``classification_exposure_rc = 0`` on every row (D-015), so there is no value
    to weight: the verdict falls back to the row share rather than to green.
    R6 reports the same rows under a DIFFERENT claim (no conversion), so both
    checks legitimately fire on such a book — they are not one warning twice."""
    seed_book(db_session, live=False)
    _loan_at(db_session, "LOAN/FX1", days_past_due=None, balance="400000", currency="USD")
    _loan_at(db_session, "LOAN/FX2", days_past_due="15", balance="400000", currency="USD")
    db_session.commit()
    build(db_session, MID_MONTH)
    r10 = _r10(db_session, MID_MONTH)
    assert Decimal(r10.detail["classification_exposure_rc"]) == Decimal(0)
    assert Decimal(r10.detail["missing_share_of_exposure_pct"]) == Decimal(0)  # limb silent
    assert Decimal(r10.detail["missing_share_pct"]) == Decimal(50)
    assert r10.status == AMBER  # max() falls back to the rows, never to green
    assert r10.lhs == Decimal(50)
    # the two checks make different claims about the same rows
    unconverted = _results(db_session, MID_MONTH)["R6"]
    assert unconverted.status == AMBER
    assert unconverted.detail["by_currency"] == {"USD": 2}


# --- R12 arrears completeness (P5-A) ----------------------------------------------------------


def _r12(db: Session, as_of: date) -> CheckResult:
    """R12 over the built slice, from the check itself — and proven to be what the
    build persisted, so the status a reader sees is the one measured here."""
    result = reconciliation.check_r12_arrears_completeness(db, ORG_1, SAMPLE_BANK_ID, as_of)
    assert _results(db, as_of)[ARREARS_COMPLETENESS].status == result.status
    return result


def _loan_with_arrears(  # noqa: PLR0913 - one keyword per attribute the cases vary
    db: Session,
    reference: str,
    *,
    arrears: str | None,
    balance: str = "1000000",
    currency: str = "GHS",
    balance_ghs: str | None = None,
) -> None:
    """One accepted LOAN at ``MID_MONTH``, with or without a stated arrears amount.

    The value goes in verbatim as the exact decimal STRING ingestion normalises it
    to, so the mart column is filled by the same path a real push fills it by.
    ``balance_ghs`` defaults to the balance for a reporting-currency loan and to
    nothing (UNCONVERTED) for a foreign one; pass it to state a CONVERTED
    foreign-currency loan, the case audit A360 R12 found dropped.
    """
    common = new_batch(db, MID_MONTH)
    extra = {"arrears_amount": arrears} if arrears is not None else None
    if balance_ghs is None and currency == "GHS":
        balance_ghs = balance
    add_position(
        db,
        common,
        reference,
        "LOAN",
        currency,
        balance=balance,
        balance_ghs=balance_ghs,
        product="LN.CORP.5Y",
        stage=1,
        extra=extra,
    )


def test_r12_is_green_when_every_loan_states_an_arrears_amount(db_session: Session) -> None:
    seed_book(db_session, live=False)
    _loan_with_arrears(db_session, "LOAN/ARR1", arrears="0")
    _loan_with_arrears(db_session, "LOAN/ARR2", arrears="125000.75")
    db_session.commit()
    outcome = build(db_session, MID_MONTH)
    r12 = _r12(db_session, MID_MONTH)
    assert r12.status == GREEN
    assert r12.lhs == Decimal(0)
    assert r12.detail["loan_rows"] == 2
    assert r12.detail["loans_without_arrears_amount"] == 0
    assert Decimal(r12.detail["stated_arrears_rc"]) == Decimal("125000.75")
    assert "reason" not in r12.detail
    assert outcome.trust[ARREARS_COMPLETENESS] == GREEN


def test_r12_reports_the_shortfall_when_only_part_of_the_book_states_arrears(
    db_session: Session,
) -> None:
    """The defect R12 exists for, measured: a bank that states arrears for half its
    book shows a sum that reads as the whole book's arrears, and a share silently
    understated. Nothing else in the platform would say so — and the check must
    FIRE here, not merely exist."""
    seed_book(db_session, live=False)
    _loan_with_arrears(db_session, "LOAN/ARR1", arrears="300000")
    _loan_with_arrears(db_session, "LOAN/SILENT", arrears=None)
    db_session.commit()
    outcome = build(db_session, MID_MONTH)
    r12 = _r12(db_session, MID_MONTH)
    assert r12.status == AMBER
    assert r12.lhs == Decimal(50)  # both limbs agree: 1 of 2 rows, half the exposure
    assert r12.detail["loan_rows"] == 2
    assert r12.detail["loans_without_arrears_amount"] == 1
    assert Decimal(r12.detail["missing_share_pct"]) == Decimal(50)
    assert Decimal(r12.detail["missing_share_of_exposure_pct"]) == Decimal(50)
    assert Decimal(r12.detail["worst_share_pct"]) == Decimal(50)
    # The stated total is the HALF that was stated, and the reason says as much,
    # so a reader cannot take it for the book's arrears.
    assert Decimal(r12.detail["stated_arrears_rc"]) == Decimal(300000)
    assert "understates the whole book" in r12.detail["reason"]
    assert outcome.trust[ARREARS_COMPLETENESS] == AMBER


def test_r12_is_red_when_no_loan_states_an_arrears_amount(db_session: Session) -> None:
    """The fixture book states none, which is exactly the "0 % reads as clean"
    case: ``loans.arrears_amount_rc`` would sum to nothing over it."""
    seed_book(db_session, live=False)
    outcome = build(db_session)
    r12 = _r12(db_session, AS_OF)
    assert r12.status == RED
    assert r12.lhs == Decimal(100)
    assert Decimal(r12.detail["missing_share_of_exposure_pct"]) == Decimal(100)
    assert Decimal(r12.detail["stated_arrears_rc"]) == Decimal(0)
    assert "no loan states an arrears amount" in r12.detail["reason"]
    assert outcome.trust[ARREARS_COMPLETENESS] == RED
    assert outcome.trust["overall"] == RED


def test_r12_reports_the_exposure_weighted_share_when_a_few_loans_hold_the_value(
    db_session: Session,
) -> None:
    """D-049 on this check: 1 of 4 loans silent is 25 % by count and almost all of
    the book by value, and the reported figure must be the second one — it is what
    a reader compares against ``loans.arrears_share_pct``, which is
    exposure-weighted.

    **On R12's population the two limbs cannot disagree on the COLOUR**, only on
    the magnitude, and that is worth stating rather than leaving as an untested
    implication of ``max``. Red is ``>= 100 %`` of either limb; a zero-exposure row
    is excluded from the population, so "every row silent" and "all the exposure
    silent" coincide, and neither limb can reach 100 % alone. ``max`` therefore
    chooses the figure the badge reports and is the conservative direction if the
    population ever changes; it is not a second verdict. Both shares are always
    disclosed, so the amber below is actionable rather than mild.
    """
    seed_book(db_session, live=False)
    _loan_with_arrears(db_session, "LOAN/BIG", arrears=None, balance="900000000")
    for index in range(3):
        _loan_with_arrears(db_session, f"LOAN/TINY{index}", arrears="0", balance="1")
    db_session.commit()
    build(db_session, MID_MONTH)
    r12 = _r12(db_session, MID_MONTH)
    assert Decimal(r12.detail["missing_share_pct"]) == Decimal(25)  # the count alone
    assert Decimal(r12.detail["missing_share_of_exposure_pct"]) > Decimal("99.99")
    assert r12.lhs is not None and r12.lhs > Decimal("99.99")  # the worse limb is reported
    assert r12.status == AMBER  # part of the book states arrears, so not "no data at all"
    assert "understates the whole book" in r12.detail["reason"]


def test_r12_is_grey_on_a_day_with_no_loan_carrying_a_positive_exposure(
    db_session: Session,
) -> None:
    seed_book(db_session, live=False)
    common = new_batch(db_session, MID_MONTH)
    add_position(
        db_session,
        common,
        "DEP/ONLY",
        "DEPOSIT",
        "GHS",
        balance="500",
        balance_ghs="500",
        product="DEP.RET.CUR",
    )
    db_session.commit()
    outcome = build(db_session, MID_MONTH)
    r12 = _r12(db_session, MID_MONTH)
    assert r12.status == GREY
    assert r12.lhs is None
    assert "no loan rows with a classified exposure" in r12.detail["reason"]
    assert outcome.trust[ARREARS_COMPLETENESS] == GREY


def test_r12_excludes_an_unconverted_loan_because_r6_owns_that_gap(
    db_session: Session,
) -> None:
    """The one place R12 deliberately differs from R10, and why.

    ``arrears_amount_rc`` is NULL for an unconverted foreign-currency loan
    whatever the bank stated (the derivation rule, like ``balance_rc``), so
    counting it as unstated arrears would report a gap the bank cannot close by
    stating arrears. R6 already states that gap under its own claim. R10 keeps
    such rows because ``dpd_band`` is stated independently of FX — two checks, two
    populations, and neither is a copy of the other.
    """
    seed_book(db_session, live=False)
    # Both state arrears; both are unconverted, so the column is NULL for both.
    _loan_with_arrears(db_session, "LOAN/FX1", arrears="1000", balance="400000", currency="USD")
    _loan_with_arrears(db_session, "LOAN/FX2", arrears="2000", balance="400000", currency="USD")
    db_session.commit()
    build(db_session, MID_MONTH)
    r12 = _r12(db_session, MID_MONTH)
    assert r12.status == GREY, "an FX-only day is R6's message, not a false arrears shortfall"
    results = _results(db_session, MID_MONTH)
    assert results["R6"].status == AMBER
    assert results["R6"].detail["by_currency"] == {"USD": 2}
    # R10 sees the same two rows and does NOT go grey: its population is the whole
    # loan book, which is the population the engine's PAR ratio divides by.
    assert _r10(db_session, MID_MONTH).status in (AMBER, RED)


def test_r12_is_evaluated_dispatched_and_stored_through_the_same_vocabulary(
    db_session: Session,
) -> None:
    """A check id with no evaluator behind it can never fire. All four sides are
    asserted here: the model vocabulary, the evaluated list, the tolerance table
    and ``evaluate``'s own dispatch."""
    assert ARREARS_COMPLETENESS in RECONCILIATION_CHECK_IDS
    assert ARREARS_COMPLETENESS in reconciliation.CHECK_IDS
    assert ARREARS_COMPLETENESS in reconciliation.STORABLE_CHECK_IDS
    assert ARREARS_COMPLETENESS in reconciliation.TOLERANCES
    assert reconciliation.TOLERANCES[ARREARS_COMPLETENESS] is None  # threshold, not tolerance
    assert len(ARREARS_COMPLETENESS) <= 4  # String(4): the id fits the column

    seed_book(db_session, live=False)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    outcome = build(db_session)
    evaluated = reconciliation.evaluate(db_session, CTX, bank, AS_OF)
    assert ARREARS_COMPLETENESS in evaluated, "evaluate() must dispatch it, not just define it"
    assert evaluated[ARREARS_COMPLETENESS].status == outcome.trust[ARREARS_COMPLETENESS]
    stored = _results(db_session)
    assert stored[ARREARS_COMPLETENESS].status == outcome.trust[ARREARS_COMPLETENESS]
    assert trust_for(db_session, ORG_1, SAMPLE_BANK_ID, AS_OF) == outcome.trust


def test_r12_counts_a_converted_foreign_currency_loans_stated_arrears(db_session: Session) -> None:
    """Audit A360 R12. A foreign-currency loan the bank DID convert (``balance_ghs``
    stated) states its arrears in its own currency; the platform used to drop them
    with the unconverted loans', so R12 reported "no loan states an arrears amount"
    for a loan that did and ``loans.arrears_share_pct`` left the loan out. The
    arrears are a slice of the same balance at the same date, so the bank's own
    ``balance_ghs / balance`` is the rate — 40,000 of 400,000 XFC on a 6,000,000
    conversion is 600,000 in the reporting currency, exactly one tenth either way.
    """
    seed_book(db_session, live=False)
    _loan_with_arrears(db_session, "LOAN/ARR1", arrears="300000")  # 1,000,000, domestic
    _loan_with_arrears(
        db_session,
        "LOAN/FXC",
        arrears="40000",
        balance="400000",
        currency="USD",
        balance_ghs="6000000",
    )
    db_session.commit()
    outcome = build(db_session, MID_MONTH)

    converted = daily_rows(db_session, MID_MONTH)["LOAN/FXC"]
    assert converted.fx_unconverted is False
    assert converted.balance_rc == Decimal("6000000")
    assert converted.arrears_amount_rc == Decimal("600000")
    assert converted.balance_rc is not None and converted.arrears_amount_rc is not None
    assert converted.arrears_amount_rc / converted.balance_rc == Decimal("40000") / Decimal(
        "400000"
    )

    r12 = _r12(db_session, MID_MONTH)
    assert r12.status == GREEN, r12.detail
    assert r12.detail["loan_rows"] == 2
    assert r12.detail["loans_without_arrears_amount"] == 0
    assert Decimal(r12.detail["stated_arrears_rc"]) == Decimal("900000")  # 300,000 + 600,000
    assert "reason" not in r12.detail
    assert outcome.trust[ARREARS_COMPLETENESS] == GREEN

    # The figure R12 guards, read through the compiler as the surface reads it:
    # 900,000 over the 7,000,000 both loans are part of, not 300,000 over 1,000,000.
    compiled = compile_query(
        db_session,
        catalogue(),
        BiQuery(
            measures=["loans.arrears_amount_rc", "loans.arrears_share_pct"],
            time=BiTime(as_of=MID_MONTH),
        ),
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
    )
    assert [column.id for column in compiled.columns] == [
        "loans.arrears_amount_rc",
        "loans.arrears_share_pct",
    ]
    amount, share = db_session.execute(compiled.select).one()
    assert Decimal(str(amount)) == Decimal("900000")
    assert float(share) == pytest.approx(900_000 / 7_000_000 * 100)


# --- audit A360: absence is not agreement ------------------------------------------------------


def test_r5_is_grey_not_green_when_there_is_nothing_on_either_side(db_session: Session) -> None:
    """``0 == 0`` is not completeness: nothing was projected because nothing was
    there, and a green here would badge an empty date."""
    seed_book(db_session, live=False)
    # The fixture has no snapshot at MID_MONTH and nothing has been built there.
    check = reconciliation.check_r5_completeness(db_session, ORG_1, SAMPLE_BANK_ID, MID_MONTH)
    assert check.status == GREY
    assert "empty match is not a pass" in check.detail["reason"]
    assert (check.lhs, check.rhs) == (None, None)
    # A build over that date persists the same verdict — and the badge follows it.
    outcome = build(db_session, MID_MONTH)
    assert outcome.trust["R5"] == GREY
    assert _results(db_session, MID_MONTH)["R5"].status == GREY
    # The control: a date with a book is still compared, exactly.
    build(db_session, AS_OF)
    r5 = _results(db_session, AS_OF)["R5"]
    assert (r5.status, r5.lhs, r5.rhs) == (GREEN, Decimal(FIXTURE_ROWS), Decimal(FIXTURE_ROWS))


def test_r1_is_grey_when_the_mart_holds_no_loan_rows_even_if_the_engine_says_zero(
    db_session: Session,
) -> None:
    """An empty loan mart and an engine ratio of 0 are one measurement and nothing,
    not two measurements that match. The built slice is tampered into exactly the
    shape the audit named: every loan row gone, the engine copy at 0."""
    seed_book(db_session)  # the live plane gives the engine an NPL ratio to copy
    build(db_session)
    assert _results(db_session)["R1"].status == GREEN  # before the tamper, a real match
    db_session.execute(
        delete(BiFactPositionDaily).where(
            BiFactPositionDaily.bank_id == SAMPLE_BANK_ID,
            BiFactPositionDaily.position_type == reconciliation.LOAN_TYPE,
        )
    )
    db_session.execute(
        update(BiFactEngineMetric)
        .where(
            BiFactEngineMetric.bank_id == SAMPLE_BANK_ID,
            BiFactEngineMetric.module == reconciliation.CREDIT_MODULE,
            BiFactEngineMetric.metric_id == reconciliation.NPL_METRIC_ID,
            BiFactEngineMetric.tier == "live",
        )
        .values(value=Decimal(0))
    )
    db_session.flush()
    check = reconciliation.check_r1_npl(db_session, ORG_1, SAMPLE_BANK_ID, AS_OF)
    assert check.status == GREY
    assert "no loan rows" in check.detail["reason"]
    assert Decimal(check.detail["engine_npl_ratio_pct"]) == Decimal(0)
    assert (check.lhs, check.rhs) == (None, None)


def test_r9_is_grey_when_the_live_plane_has_no_balance_sheet_lines_to_have_balanced(
    db_session: Session,
) -> None:
    """The derivation stamps its control record on a BALANCE-SHEET line; with no
    such line there is no book that could have balanced, and reading the absent
    stamp as "balanced exactly" is absence read as agreement."""
    seed_book(db_session, live=False)
    # A live plane that carries no balance-sheet line at all.
    db_session.add(
        CurrentFinancialFact(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            source_as_of_date=AS_OF,
            source_generation=1,
            fact_group="off_balance",
            category="guarantees",
            amount=Decimal("1"),
            currency="GHS",
            attributes={},
        )
    )
    db_session.flush()
    check = reconciliation.check_r9_balance_identity(db_session, CTX, SAMPLE_BANK_ID)
    assert check.status == GREY
    assert "no balance-sheet lines" in check.detail["reason"]


def test_r9_green_without_a_stamp_is_earned_only_where_balance_sheet_lines_exist(
    db_session: Session,
) -> None:
    """The control, and why the rule is drawn where it is: ``fact_derivation.bs``
    writes the ``reconciliation`` record ONLY when a plug was applied, so a live
    balance sheet with lines and no stamp genuinely balanced exactly. That green
    now states how many lines it rests on, so it can never be confused with the
    no-lines case above."""
    seed_book(db_session)  # the fixture's live plane balances under an exception → stamped
    assert reconciliation.check_r9_balance_identity(db_session, CTX, SAMPLE_BANK_ID).status == AMBER
    # Strip the stamp from every balance-sheet line: the shape of a book that
    # never needed a plug.
    for fact in db_session.scalars(
        select(CurrentFinancialFact).where(
            CurrentFinancialFact.bank_id == SAMPLE_BANK_ID,
            CurrentFinancialFact.fact_group == reconciliation.BALANCE_SHEET_GROUP,
        )
    ):
        fact.attributes = {
            k: v for k, v in (fact.attributes or {}).items() if k != "reconciliation"
        }
    db_session.flush()
    check = reconciliation.check_r9_balance_identity(db_session, CTX, SAMPLE_BANK_ID)
    assert check.status == GREEN
    assert check.detail["reason"] == "The live book balanced exactly; no plug was recorded."
    assert check.detail["balance_sheet_lines"] > 0
