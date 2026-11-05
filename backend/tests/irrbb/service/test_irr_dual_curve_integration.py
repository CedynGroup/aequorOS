"""Dual-curve discounting: the published AGD wires into the IRR engines.

Curve platform spec §6/§13 Stage 2: discounting is a SEPARATE selection from
projection. When the desk publishes a discounting curve for the bank's
currency (``AEQ.GHS.OIS`` — the Aequor Ghana Discounting Curve), every
EVE/duration present value discounts on it while the parameter-table base
curve keeps its projection role (floating-leg repricing, EaR gap arithmetic).
Absent a published discount curve, behavior is byte-identical to the
single-curve engine — that graceful fallback is what keeps the hermetic
golden suite green, because the sample-bank seed publishes no desk curves.

Hand-derived engine arithmetic (one asset, midpoint 1.9y, face 1,000,000):

- single-curve: projection zero 20%  → PV = 1,000,000 / 1.20^1.9 = 707,221.7890
- dual-curve:   discount   zero 24%  → PV = 1,000,000 / 1.24^1.9 = 664,505.8340
  (the discount rate sits ABOVE the projection rate, so the PV — and an
  asset-only book's EVE — is strictly LOWER under the discount curve)
- the SAME +200 bp shift map shocks the discounting curve:
  PV = 1,000,000 / 1.26^1.9 = 644,608.4055
- modified duration divides by (1 + y) of the DISCOUNTING curve:
  1.9 / 1.24 = 1.5323 (vs 1.9 / 1.20 = 1.5833 single-curve)

This module's other-suite cases live in ``test_irr_dual_curve.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.irrbb.schemas.regulatory_irr import IrrScenarioBatchCreate
from app.irrbb.service import regulatory_irr
from app.live.service.fact_derivation import derive_facts
from app.market_data.service import market_data_sources
from app.market_data.service.market_data import get_discount_curve
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    IngestionBatch,
    LineageRecord,
    ParamStressShock,
)
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.irrbb.service.test_irr_dual_curve import AGD_RATES, EXPECTED_AGD_MIDPOINTS_PCT
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture

CTX = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
REPORTING_DATE = date(2026, 3, 31)


def _meta(
    db: Session,
    bank_id: str,
    *,
    source_system: str,
    as_of: date,
    ingested_at: datetime,
) -> dict[str, Any]:
    batch = IngestionBatch(
        organization_id=DEMO_ORG_ID,
        bank_id=bank_id,
        source_system=source_system,
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=DEMO_ORG_ID,
        bank_id=batch.bank_id,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="dual-curve-test-fixture",
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    return {
        "organization_id": DEMO_ORG_ID,
        "bank_id": bank_id,
        "as_of_date": as_of,
        "ingested_at": ingested_at,
        "source_system": source_system,
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }


def _seed_curve(  # noqa: PLR0913 - fixture knob per selection axis
    db: Session,
    bank_id: str,
    *,
    curve_name: str,
    curve_type: str,
    source_system: str,
    rates: dict[int, str],
    as_of: date,
    ingested_at: datetime,
) -> None:
    meta = _meta(db, bank_id, source_system=source_system, as_of=as_of, ingested_at=ingested_at)
    curve = CanonicalYieldCurve(
        **meta,
        source_reference=f"{source_system}/{curve_name}",
        currency="GHS",
        curve_name=curve_name,
        curve_type=curve_type,
    )
    db.add(curve)
    db.flush()
    for tenor_months, rate in rates.items():
        db.add(
            CanonicalYieldCurvePoint(
                **meta,
                source_reference=f"{source_system}/{curve_name}/{tenor_months}m",
                yield_curve_id=curve.id,
                tenor_months=tenor_months,
                rate=Decimal(rate),
            )
        )
    db.flush()


def _seed_agd(db: Session, *, as_of: date = REPORTING_DATE) -> None:
    _seed_curve(
        db,
        SAMPLE_BANK_ID,
        curve_name="AEQ.GHS.OIS",
        curve_type="discount",
        source_system="AEQUOR_DESK",
        rates=AGD_RATES,
        as_of=as_of,
        ingested_at=datetime(as_of.year, as_of.month, as_of.day, 18, 0, tzinfo=UTC),
    )


def test_get_discount_curve_prefers_the_desk_ois_by_name(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _seed_agd(db_session)
    # A fresher generic discount-type curve must NOT beat the named AGD.
    _seed_curve(
        db_session,
        SAMPLE_BANK_ID,
        curve_name="GHS_DISCOUNT_BVAL",
        curve_type="discount",
        source_system="BLOOMBERG",
        rates={12: "0.28"},
        as_of=REPORTING_DATE,
        ingested_at=datetime(2026, 3, 31, 23, 0, tzinfo=UTC),
    )

    view = get_discount_curve(db_session, DEMO_ORG_ID, SAMPLE_BANK_ID, "GHS", REPORTING_DATE)
    assert view is not None
    assert view.curve_name == "AEQ.GHS.OIS"
    assert view.curve_type == "discount"
    assert view.attribution.source_system == "AEQUOR_DESK"


def test_get_discount_curve_falls_back_to_any_discount_type(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _seed_curve(
        db_session,
        SAMPLE_BANK_ID,
        curve_name="GHS_DISCOUNT_BVAL",
        curve_type="discount",
        source_system="BLOOMBERG",
        rates={12: "0.28"},
        as_of=REPORTING_DATE,
        ingested_at=datetime(2026, 3, 31, 23, 0, tzinfo=UTC),
    )

    view = get_discount_curve(db_session, DEMO_ORG_ID, SAMPLE_BANK_ID, "GHS", REPORTING_DATE)
    assert view is not None
    assert view.curve_name == "GHS_DISCOUNT_BVAL"


def test_get_discount_curve_is_none_without_discount_curves(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    # A projection-family curve never satisfies the discount selection.
    _seed_curve(
        db_session,
        SAMPLE_BANK_ID,
        curve_name="AEQ.GHS.SOV.ZERO",
        curve_type="zero",
        source_system="AEQUOR_DESK",
        rates={12: "0.25"},
        as_of=REPORTING_DATE,
        ingested_at=datetime(2026, 3, 31, 18, 0, tzinfo=UTC),
    )

    assert (
        get_discount_curve(db_session, DEMO_ORG_ID, SAMPLE_BANK_ID, "GHS", REPORTING_DATE) is None
    )


def _period(db: Session) -> BankReportingPeriod:
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period is not None
    return period


def _bank(db: Session) -> Bank:
    bank = db.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    return bank


def _baseline_run(db: Session, period_id: UUID):
    """Mint the official scenario batch and return its baseline run."""
    batch = regulatory_irr.run_all_irr_scenarios(
        db, CTX, SAMPLE_BANK_ID, IrrScenarioBatchCreate(reporting_period_id=period_id)
    )
    baseline = batch.runs[0]
    assert baseline.scenario_code == "baseline"
    return baseline


def test_official_run_discounts_on_the_published_agd(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    period = _period(db_session)

    before = _baseline_run(db_session, period.id)
    assert before.status == "succeeded"
    # Without a published discount curve the snapshot keeps its historical
    # parameter keys — the hash-compatibility contract.
    assert set(before.inputs["parameters"]) == {
        "base_curve_pct",
        "scenario_shocks",
        "limits_pct",
    }

    _seed_agd(db_session)
    after = _baseline_run(db_session, period.id)
    assert after.status == "succeeded"

    parameters = after.inputs["parameters"]
    assert set(parameters) == {
        "base_curve_pct",
        "scenario_shocks",
        "limits_pct",
        "discount_curve_pct",
    }
    assert parameters["discount_curve_pct"] == EXPECTED_AGD_MIDPOINTS_PCT
    # The base curve block is untouched — the AGD never rewrites projection.
    assert parameters["base_curve_pct"] == before.inputs["parameters"]["base_curve_pct"]
    # A run that discounted on the AGD is a different reproducible input.
    assert after.input_hash != before.input_hash
    # ... and the live freshness comparison sees the same current hash.
    assert (
        regulatory_irr.current_input_hash(db_session, CTX, _bank(db_session), period)
        == after.input_hash
    )

    # EVE present values moved onto the discount curve.
    assert Decimal(after.metrics["eve_base_ghs"]) != Decimal(before.metrics["eve_base_ghs"])
    assert after.metrics["worst_eve_change_ghs"] != ""  # scenarios computed
    # Gap-based EaR is curve-free and must not move with AGD presence.
    assert after.metrics["ear_up_200_ghs"] == before.metrics["ear_up_200_ghs"]
    assert after.metrics["ear_down_200_ghs"] == before.metrics["ear_down_200_ghs"]
    # Accrual NII — including the pay-fixed swap carry priced off the
    # PROJECTION curve's 0.17y zero (25.8): 253.78M book + 120M×(25.8−25.3)/100
    # = 254.38M. Unchanged with the AGD present ⇒ floating legs still read
    # the projection curve, never the discount curve.
    assert after.metrics["nii_base_ghs"] == before.metrics["nii_base_ghs"]
    assert Decimal(after.metrics["nii_base_ghs"]) == Decimal("254380000")


def test_workbench_parity_with_official_run_under_agd(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    period = _period(db_session)
    _seed_agd(db_session)

    run = _baseline_run(db_session, period.id)
    assert run.status == "succeeded"
    by_code = {entry["scenario_code"]: entry for entry in run.metrics["eve_by_scenario"]}

    analysis = regulatory_irr.compute_scenario_analysis(
        db_session,
        CTX,
        _bank(db_session),
        period,
        {"parallel_bp": Decimal("200")},
        "parallel_up_200",
    )
    assert analysis.base_eve == Decimal(run.metrics["eve_base_ghs"])
    assert analysis.shifted_eve == Decimal(by_code["parallel_up_200"]["eve_ghs"])
    assert analysis.delta_eve == Decimal(by_code["parallel_up_200"]["delta_eve_ghs"])


# Vendor curve (fresher ingest — would win pure most-recent arbitration).
VENDOR_RATES: dict[int, str] = {12: "0.195", 60: "0.23", 120: "0.25"}
# Desk sovereign zero (older ingest — must still win by name preference).
DESK_SOV_RATES: dict[int, str] = {
    1: "0.24",
    3: "0.245",
    6: "0.25",
    12: "0.255",
    24: "0.26",
    48: "0.265",
    84: "0.27",
}


def test_ftp_curve_prefers_the_desk_sovereign_zero(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID)
    _seed_curve(
        db_session,
        SAMPLE_BANK_ID,
        curve_name="GHS_SOVEREIGN_BVAL",
        curve_type="sovereign",
        source_system="BLOOMBERG",
        rates=VENDOR_RATES,
        as_of=FIXTURE_AS_OF,
        ingested_at=datetime(2026, 6, 30, 20, 0, tzinfo=UTC),
    )
    _seed_curve(
        db_session,
        SAMPLE_BANK_ID,
        curve_name="AEQ.GHS.SOV.ZERO",
        curve_type="zero",
        source_system="AEQUOR_DESK",
        rates=DESK_SOV_RATES,
        as_of=FIXTURE_AS_OF,
        ingested_at=datetime(2026, 6, 30, 8, 0, tzinfo=UTC),
    )

    result = derive_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()

    facts = {
        fact.category: fact
        for fact in db_session.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.reporting_period_id == result.reporting_period_id,
                BankFinancialFact.fact_group == "ftp_curve_point",
            )
        )
    }
    assert len(facts) == len(DESK_SOV_RATES)
    one_year = facts["1y"]
    # Desk 12m zero (25.5%) wins over the fresher vendor 12m (19.5%).
    assert Decimal(str(one_year.attributes["base_yield_pct"])) == Decimal("25.5")
    derived_from = one_year.attributes["derived_from"]
    assert "AEQ.GHS.SOV.ZERO (AEQUOR_DESK)" in derived_from
    assert "desk-published sovereign zero preferred" in derived_from


def test_base_curve_falls_back_to_the_published_projection_curve(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base curve is market data, resolved through the Markets-tab preference.

    Until 2026-08-23 an IRRBB run refused outright unless a curve snapshot had
    been seeded into ``param_stress_shock`` — which no provisioned tenant ever
    had, so an institution with a published desk curve still could not run
    IRRBB. Governed param rows keep precedence (the hermetic book seeds them, so
    every golden reproduces byte-identically); the market plane fills in only
    where they are absent.
    """
    materialize_canonical_test_book(db_session)
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None

    seeded = regulatory_irr._load_irr_params_or_none(db_session, CTX, bank, REPORTING_DATE)
    assert seeded is not None and seeded.curve, "the fixture seeds a governed base curve"

    calls: list[str] = []

    def _spy(*args: object, **kwargs: object) -> None:
        calls.append("projection")

    monkeypatch.setattr(market_data_sources, "preferred_projection_curve", _spy)
    again = regulatory_irr._load_irr_params_or_none(db_session, CTX, bank, REPORTING_DATE)
    assert again is not None and again.curve == seeded.curve
    assert calls == [], "a governed base curve is never overridden by the market plane"

    # With the governed rows gone, the market plane is consulted.
    db_session.query(ParamStressShock).filter(
        ParamStressShock.organization_id == DEMO_ORG_ID,
        ParamStressShock.scenario_code == "base_curve",
    ).delete(synchronize_session=False)
    db_session.flush()
    assert regulatory_irr._load_irr_params_or_none(db_session, CTX, bank, REPORTING_DATE) is None
    assert calls == ["projection"], "the preference seam is the fallback, and it was asked"
