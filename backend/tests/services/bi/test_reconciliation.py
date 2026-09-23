"""``app.services.bi.reconciliation``: R1–R9 on the canonical fixture bank.

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
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.capital import loan_classification as classification_engine
from app.models import (
    Bank,
    BiReconciliationResult,
    CanonicalPositionSnapshot,
    CurrentFinancialFact,
    LiveMetric,
)
from app.models.bi import RECONCILIATION_CHECK_IDS
from app.services import pipeline
from app.services.bi import reconciliation
from app.services.bi.reconciliation import (
    AMBER,
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
