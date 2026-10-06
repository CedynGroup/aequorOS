"""The enterprise stress IRRBB leg prices the same book as regulatory IRRBB (#306).

The leg's ΔEVE is the Appendix II Table 5 Pillar 2 IRRBB charge, so it must see
the bank's whole interest-rate book: every ``irr_position`` at its contractual
rate and every ``irr_swap`` decomposed into its receive and pay legs, exactly as
``regulatory_irr`` reads them. Over the deterministic seeded book (which carries
a pay-fixed swap and rate-bearing positions) these tests pin the leg three ways:
against the regulatory IRRBB run, against an independent recomputation from the
raw facts, and through to the Board-attested Appendix II package.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.irr.engine import IRR_BUCKETS, IrrPosition, compute_nii
from app.domain.stress import orchestrator
from app.domain.stress.appendix_ii import thousands
from app.models import BankFinancialFact, RegulatoryRun
from app.schemas.enterprise_stress import EnterpriseStressRunCreate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import default_macro_scenarios, enterprise_stress, regulatory_irr
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.services.test_icaap_stress_appendix2_report import (
    MAKER,
    _attested_signoff,
    _generate,
    _period_id,
    _section,
    _seed_checker,
)

pytestmark = [
    pytest.mark.usefixtures("fx_run_authority", "irrbb_run_authority"),
    pytest.mark.usefixtures("return_generation_authority"),
]

PARALLEL_UP_200 = default_macro_scenarios.DEFAULT_BY_CODE["system_irr_parallel_up_200"]
_HUNDRED = Decimal("100")
_TWELVE = Decimal("12")
_SHOCK = Decimal("0.02")
_CENT = Decimal("0.01")
_NIL = Decimal("0")

#: ``(side, bucket, amount, rate_pct, midpoint_years)`` of one priced position.
_Leg = tuple[str, str, Decimal, Decimal, Decimal]


@dataclass(frozen=True)
class _StressRun:
    run_id: UUID
    outcome: dict[str, Any]
    appendix_ii: dict[str, Any]
    irr: orchestrator.IrrStressInputs


def _run_parallel_up(db: Session, monkeypatch: pytest.MonkeyPatch) -> _StressRun:
    """Run the system +200 bp scenario, capturing the IRR inputs the leg priced."""
    captured: list[orchestrator.IrrStressInputs] = []
    real = enterprise_stress.run_enterprise_stress

    def capture(inputs: orchestrator.EnterpriseStressInputs) -> Any:
        assert inputs.irr is not None
        captured.append(inputs.irr)
        return real(inputs)

    monkeypatch.setattr(enterprise_stress, "run_enterprise_stress", capture)
    read = enterprise_stress.run_enterprise_stress_test(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        EnterpriseStressRunCreate(
            scenario_id=PARALLEL_UP_200.id,
            reporting_period_id=_period_id(db),
            reason="Price the IRRBB leg under the parallel +200 bp default.",
        ),
    )
    (irr,) = captured
    return _StressRun(read.run_id, read.outcome, read.appendix_ii, irr)


def _irr_facts(db: Session) -> list[BankFinancialFact]:
    return list(
        db.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.bank_id == SAMPLE_BANK_ID,
                BankFinancialFact.reporting_period_id == _period_id(db),
                BankFinancialFact.fact_group.in_(("irr_position", "irr_swap")),
            )
        )
    )


def _expected_book(facts: list[BankFinancialFact], curve: dict[Decimal, Decimal]) -> list[_Leg]:
    """Every priced position read straight off the facts, without the engine reader.

    A pay-fixed swap is an asset receiving the floating index (the curve zero at
    its receive midpoint) and a liability paying its fixed rate.
    """
    book: list[_Leg] = []
    for fact in facts:
        attrs = fact.attributes
        amount = Decimal(str(fact.amount))
        if fact.fact_group == "irr_position":
            midpoint = Decimal(str(attrs["midpoint_years"]))
            book.append(
                (attrs["side"], attrs["bucket"], amount, Decimal(attrs["rate_pct"]), midpoint)
            )
            continue
        assert attrs["direction"] == "pay_fixed"
        receive_midpoint = Decimal(attrs["receive_midpoint_years"])
        pay_midpoint = Decimal(attrs["pay_midpoint_years"])
        book.append(
            ("asset", attrs["receive_bucket"], amount, curve[receive_midpoint], receive_midpoint)
        )
        book.append(
            ("liability", attrs["pay_bucket"], amount, Decimal(attrs["pay_rate_pct"]), pay_midpoint)
        )
    return book


def _signed(side: str, value: Decimal) -> Decimal:
    return value if side == "asset" else -value


def _eve(book: list[_Leg], curve: dict[Decimal, Decimal], shift: Decimal) -> Decimal:
    """PV(assets) − PV(liabilities), each discounted at its curve zero plus ``shift``."""
    return sum(
        (
            _signed(side, amount / (1 + curve[midpoint] / _HUNDRED + shift) ** midpoint)
            for side, _, amount, _, midpoint in book
        ),
        _NIL,
    )


def _as_leg(position: IrrPosition) -> _Leg:
    return (
        position.side,
        position.bucket,
        position.amount,
        position.rate_pct,
        position.midpoint_years,
    )


def test_the_stress_irr_leg_matches_the_regulatory_irrbb_run(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialize_canonical_test_book(db_session)
    regulatory = regulatory_irr.create_irr_run(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate.model_construct(
            module="irr", reporting_period_id=_period_id(db_session), scenario_code="baseline"
        ),
    )
    stress = _run_parallel_up(db_session, monkeypatch)

    irr = stress.outcome["irr"]
    assert Decimal(irr["parallel_bp"]) == Decimal("200")
    assert Decimal(irr["base_eve"]) == Decimal(regulatory.metrics["eve_base_ghs"])
    assert Decimal(irr["delta_nii"]) == Decimal(regulatory.metrics["ear_up_200_ghs"])
    # The values the IRRBB dashboard shows for this book (#306 evidence table).
    assert Decimal(irr["base_eve"]).quantize(_CENT) == Decimal("-100562865.71")
    assert Decimal(irr["delta_nii"]) == Decimal("-7450600.0000")
    assert compute_nii(stress.irr.positions) == Decimal(regulatory.metrics["nii_base_ghs"])


def test_the_stress_irr_leg_independently_reprices_the_swap_hedged_book(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialize_canonical_test_book(db_session)
    stress = _run_parallel_up(db_session, monkeypatch)
    curve = dict(stress.irr.curve)
    book = _expected_book(_irr_facts(db_session), curve)
    positions = [_as_leg(position) for position in stress.irr.positions]

    assert sorted(positions) == sorted(book)
    assert all(rate != 0 for _, _, _, rate, _ in positions)
    swap_legs = [position for position in stress.irr.positions if position.source == "swap"]
    assert [(leg.side, leg.bucket, leg.fixed_or_float, leg.is_hedge) for leg in swap_legs] == [
        ("asset", "1-3m", "float", True),
        ("liability", "1-3y", "fixed", True),
    ]
    assert swap_legs[0].amount == swap_legs[1].amount == Decimal("120000000.0000")
    assert swap_legs[0].rate_pct == curve[Decimal("0.17")]
    assert swap_legs[1].rate_pct == Decimal("25.3")

    gaps: dict[str, Decimal] = defaultdict(Decimal)
    for side, bucket, amount, _, _ in book:
        gaps[bucket] += _signed(side, amount)
    ear = sum(
        (
            gaps[bucket] * _SHOCK * (_TWELVE - midpoint * _TWELVE) / _TWELVE
            for bucket, midpoint in IRR_BUCKETS
            if midpoint * _TWELVE < _TWELVE
        ),
        _NIL,
    )
    base_eve = _eve(book, curve, _NIL)
    delta_eve = _eve(book, curve, _SHOCK) - base_eve
    nii = sum((_signed(side, amount * rate / _HUNDRED) for side, _, amount, rate, _ in book), _NIL)

    irr = stress.outcome["irr"]
    assert Decimal(irr["delta_nii"]).quantize(_CENT) == ear.quantize(_CENT)
    assert abs(Decimal(irr["base_eve"]) - base_eve) < _CENT
    assert abs(Decimal(irr["delta_eve"]) - delta_eve) < _CENT
    assert compute_nii(stress.irr.positions).quantize(_CENT) == nii.quantize(_CENT)


def test_the_corrected_irrbb_charge_reaches_appendix_ii_and_the_attested_stress_pack(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialize_canonical_test_book(db_session)
    _seed_checker(db_session)
    stress = _run_parallel_up(db_session, monkeypatch)
    curve = dict(stress.irr.curve)
    book = _expected_book(_irr_facts(db_session), curve)
    delta_eve = _eve(book, curve, _SHOCK) - _eve(book, curve, _NIL)
    charge = thousands(max(-Decimal(stress.outcome["irr"]["delta_eve"]), _NIL))
    assert charge == thousands(max(-delta_eve, _NIL))
    assert charge > 0

    stress_rows = [
        row
        for row in stress.appendix_ii["table5_rwa"]["rows"]
        if row["label"].lower().startswith("stress")
    ]
    assert len(stress_rows) == 3
    assert {Decimal(row["pillar2"]["irrbb"]) for row in stress_rows} == {charge}

    _attested_signoff(db_session, stress.run_id)
    package = _generate(db_session)
    pillar2 = _section(package.snapshot, "t5_pillar2")
    assert pillar2 is not None
    filed = {row["code"]: row["pillar2_irrbb"] for row in pillar2["rows"]}
    assert {Decimal(filed[code]) for code in ("stress_y1", "stress_y2", "stress_y3")} == {charge}


def test_the_swap_facts_enter_the_reproducibility_snapshot(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialize_canonical_test_book(db_session)
    stress = _run_parallel_up(db_session, monkeypatch)
    run = db_session.get(RegulatoryRun, stress.run_id)
    assert run is not None
    groups = {fact["fact_group"] for fact in run.inputs["irr_facts"]}
    assert groups == {"irr_position", "irr_swap"}
    assert run.inputs["irr_base_curve"]

    swap = next(fact for fact in _irr_facts(db_session) if fact.fact_group == "irr_swap")
    swap.amount = Decimal(str(swap.amount)) * 2
    db_session.commit()
    rerun = _run_parallel_up(db_session, monkeypatch)
    assert db_session.get(RegulatoryRun, rerun.run_id).input_hash != run.input_hash  # type: ignore[union-attr]


def test_an_unsupported_swap_direction_refuses_the_run(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialize_canonical_test_book(db_session)
    swap = next(fact for fact in _irr_facts(db_session) if fact.fact_group == "irr_swap")
    swap.attributes = {**swap.attributes, "direction": "basis"}
    db_session.commit()

    with pytest.raises(enterprise_stress.EnterpriseStressError) as excinfo:
        _run_parallel_up(db_session, monkeypatch)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error_code"] == "unsupported_swap_direction"  # type: ignore[index]
