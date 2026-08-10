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
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.irr.engine import (
    IRR_BUCKETS,
    IrrPosition,
    compute_duration,
    compute_eve,
)
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    IngestionBatch,
    LineageRecord,
)
from app.schemas.regulatory_irr import IrrScenarioBatchCreate
from app.services import regulatory_irr
from app.services.fact_derivation import derive_facts
from app.services.market_data import get_discount_curve
from app.services.regulatory_irr import discount_curve_midpoints_pct
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture

CTX = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
REPORTING_DATE = date(2026, 3, 31)

# One-position book for the hand-derived engine arithmetic in the module
# docstring: an asset repricing at the 1-3y midpoint.
ASSET = IrrPosition(
    side="asset",
    bucket="1-3y",
    amount=Decimal("1000000"),
    rate_pct=Decimal("20"),
    fixed_or_float="fixed",
    midpoint_years=Decimal("1.9"),
    source="unit",
)
PROJECTION = {Decimal("1.9"): Decimal("20")}
DISCOUNT = {Decimal("1.9"): Decimal("24")}

# AGD published nodes for the official-run tests: (tenor_months → decimal
# fraction), deliberately ABOVE the seed's 25.5–29.5% parameter base curve.
AGD_RATES: dict[int, str] = {
    1: "0.29",
    3: "0.295",
    6: "0.30",
    12: "0.305",
    24: "0.31",
    48: "0.315",
    84: "0.32",
}
# Hand-derived nine-midpoint conversion of AGD_RATES (linear on zero rates,
# flat past both ends, percent, 6 dp):
# - 0.003 / 0.014 / 0.06 y sit below the 1m node (1/12 y)          → flat 29
# - 0.17 y between 1m and 3m: 29 + 0.5·(0.17−1/12)/(0.25−1/12)     → 29.26
# - 0.38 y between 3m and 6m: 29.5 + 0.5·(0.38−0.25)/0.25          → 29.76
# - 0.75 y between 6m and 1y: 30 + 0.5·(0.75−0.5)/0.5              → 30.25
# - 1.9  y between 1y and 2y: 30.5 + 0.5·(1.9−1.0)/1.0             → 30.95
# - 4.0  y is the 48m node                                          → 31.50
# - 7.0  y is the 84m node                                          → 32.00
EXPECTED_AGD_MIDPOINTS_PCT: dict[str, str] = {
    "0.003": "29.000000",
    "0.014": "29.000000",
    "0.06": "29.000000",
    "0.17": "29.260000",
    "0.38": "29.760000",
    "0.75": "30.250000",
    "1.9": "30.950000",
    "4.0": "31.500000",
    "7.0": "32.000000",
}


# ---------------------------------------------------------------------------
# Engine: dual-curve PV / duration (pure, hand-derived literals)
# ---------------------------------------------------------------------------


def test_compute_eve_discounts_on_the_published_curve() -> None:
    single = compute_eve([ASSET], PROJECTION, {})
    assert single == Decimal("707221.7890")  # 1,000,000 / 1.20^1.9

    dual = compute_eve([ASSET], PROJECTION, {}, discount_curve=DISCOUNT)
    assert dual == Decimal("664505.8340")  # 1,000,000 / 1.24^1.9
    # Discount rate above the projection rate ⇒ strictly lower asset PV.
    assert dual < single


def test_the_same_shift_map_shocks_the_discounting_curve() -> None:
    # +200 bp (decimal 0.02) keyed by the shared 1.9y midpoint moves the
    # DISCOUNT rate from 24% to 26%: 1,000,000 / 1.26^1.9.
    shifted = compute_eve(
        [ASSET], PROJECTION, {Decimal("1.9"): Decimal("0.02")}, discount_curve=DISCOUNT
    )
    assert shifted == Decimal("644608.4055")


def test_absent_or_identical_discount_curve_is_byte_identical() -> None:
    single = compute_eve([ASSET], PROJECTION, {})
    assert compute_eve([ASSET], PROJECTION, {}, discount_curve=None) == single
    assert compute_eve([ASSET], PROJECTION, {}, discount_curve=dict(PROJECTION)) == single


def test_compute_duration_discounts_on_the_published_curve() -> None:
    dual = compute_duration([ASSET], PROJECTION, discount_curve=DISCOUNT)
    assert dual.pv_assets == Decimal("664505.8340")
    assert dual.asset_macaulay == Decimal("1.9000")
    assert dual.asset_modified == Decimal("1.5323")  # 1.9 / 1.24

    single = compute_duration([ASSET], PROJECTION)
    assert single.pv_assets == Decimal("707221.7890")
    assert single.asset_modified == Decimal("1.5833")  # 1.9 / 1.20


# ---------------------------------------------------------------------------
# CurveView points → nine-midpoint percent dict
# ---------------------------------------------------------------------------


def test_midpoint_conversion_reproduces_node_values_exactly() -> None:
    # 48m and 84m sit exactly on the 4.0y and 7.0y bucket midpoints.
    curve = discount_curve_midpoints_pct(((48, Decimal("0.289")), (84, Decimal("0.295"))))
    assert curve[Decimal("4.0")] == Decimal("28.900000")
    assert curve[Decimal("7.0")] == Decimal("29.500000")
    # Flat extrapolation below the first node.
    assert curve[Decimal("0.003")] == Decimal("28.900000")


def test_midpoint_conversion_interpolates_linearly_between_nodes() -> None:
    curve = discount_curve_midpoints_pct(((6, Decimal("0.20")), (18, Decimal("0.30"))))
    # 0.75y between 0.5y and 1.5y: 20 + 10 · (0.75 − 0.5)/(1.5 − 0.5) = 22.5.
    assert curve[Decimal("0.75")] == Decimal("22.500000")
    assert curve[Decimal("0.38")] == Decimal("20.000000")  # flat below 0.5y
    assert curve[Decimal("1.9")] == Decimal("30.000000")  # flat above 1.5y
    # Every one of the nine canonical midpoints is keyed — exact-lookup safe.
    assert set(curve) == {midpoint for _, midpoint in IRR_BUCKETS}


def test_midpoint_conversion_of_the_agd_fixture_matches_hand_derivation() -> None:
    points = tuple((months, Decimal(rate)) for months, rate in sorted(AGD_RATES.items()))
    curve = discount_curve_midpoints_pct(points)
    assert {str(midpoint): str(rate) for midpoint, rate in curve.items()} == (
        EXPECTED_AGD_MIDPOINTS_PCT
    )


# ---------------------------------------------------------------------------
# Canonical seeding (the established market-data test factory idiom)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# get_discount_curve selection
# ---------------------------------------------------------------------------


def test_get_discount_curve_prefers_the_desk_ois_by_name(db_session: Session) -> None:
    seed_sample_bank(db_session)
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
    seed_sample_bank(db_session)
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
    seed_sample_bank(db_session)
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


# ---------------------------------------------------------------------------
# Official IRR runs, snapshot, hash, workbench parity
# ---------------------------------------------------------------------------


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
    seed_sample_bank(db_session)
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
    seed_sample_bank(db_session)
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


# ---------------------------------------------------------------------------
# FTP projection-curve preference (desk sovereign zero over vendor arbitration)
# ---------------------------------------------------------------------------

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
    seed_sample_bank(db_session)
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
