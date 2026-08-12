"""The desk calculation pipeline (spec §5 steps 1-9): reproducibility, real
fixtures, hard QA gates, declared treatments.

The integration tests seed DeskObservations from the REAL harvested Ghana
fixtures (tests/fixtures/market_desk/series/ — values captured off the wire
2026-08-09) and run the full pipeline for cob 2026-08-07, pinning the BoG
golden yield conversions (5.68 -> 5.7618 etc.) and the observed -477 bps
interbank-to-MPR spread. The pure tests exercise :func:`run_pipeline`
directly: same snapshot + same methodology version must be byte-identical,
poisoned inputs must fail the gate loudly, undeclared series must refuse.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.curves.conventions import DayCount, bond_price
from app.models import (
    Bank,
    CanonicalFxRate,
    CanonicalMarketIndex,
    CanonicalYieldCurve,
    DeskDetermination,
    DeskObservation,
)
from app.services.market_desk import calculation, determinations, publication, register
from app.services.market_desk.calculation import CalculationError, run_pipeline
from tests.api.helpers import ORG_1
from tests.storage.inmemory import InMemoryStorageClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "market_desk" / "series"
COB = date(2026, 8, 7)
ANALYST = "analyst@aequoros.com"
LEAD = "lead@aequoros.com"
PARAMS: dict[str, Any] = register.DEFAULT_METHODOLOGY_PARAMETERS_V1

# BoG 2026-08-03 tender: published discount/interest pairs (the conventions
# goldens) — the pipeline must reproduce the interest legs from the discounts.
GOLDEN_TENDER_YIELDS = {91: 5.7618, 182: 7.6409, 364: 12.9821}

# Synthetic per-bank APR prints (the BoG APR notice is captured raw but not
# parsed to a fixture series; these are test stand-ins for that dataset).
APR_SEED = (
    ("GHS.APR.GCB", "24.90"),
    ("GHS.APR.ABSA", "27.40"),
    ("GHS.APR.SCB", "22.10"),
    ("GHS.APR.CAL", "30.20"),
    ("GHS.APR.FID", "26.00"),
    ("GHS.APR.ZEN", "28.80"),
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _read_csv(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def _obs(series_code: str, as_of: date, value: str, unit: str = "pct") -> DeskObservation:
    return DeskObservation(
        series_code=series_code,
        as_of_date=as_of,
        value=Decimal(value),
        unit=unit,
        entered_by=ANALYST,
    )


def _seed_fixture_observations(db: Session) -> None:  # noqa: PLR0912 - one seed, many series
    """The last ~year of the relevant fixture series (plus the GRR
    reference-month inputs), bulk-inserted for speed — every (series, as-of)
    pair is unique so the append-only supersession path is not in play."""
    rows: list[DeskObservation] = []
    for row in _read_csv("mpc_policy_rate.csv"):
        day = date.fromisoformat(row["date"])
        if day >= date(2024, 1, 1):
            rows.append(_obs("GHS.MPR", day, row["rate"]))
    for row in _read_csv("interbank_rate.csv"):
        day = date.fromisoformat(row["date"])
        in_history = day >= date(2025, 11, 1)
        in_grr_month = date(2024, 6, 1) <= day <= date(2024, 6, 30)
        if in_history or in_grr_month:
            rows.append(_obs("GHS.INTERBANK.ON", day, row["rate"]))
    for row in _read_csv("tbill_rates.csv"):
        security = row["security"].strip()
        if not security.endswith("DAY BILL"):
            continue
        tenor = int(security.split()[0])
        day = date.fromisoformat(row["date"])
        if float(row["discount_rate"]) <= 0.0 or day > COB:
            continue
        wanted = (tenor == 91 and (day >= date(2025, 11, 1) or
                                   date(2024, 5, 1) <= day <= date(2024, 6, 30))) or (
            tenor in (182, 364) and day >= date(2026, 6, 1)
        )
        if wanted:
            rows.append(_obs(f"GHS.TBILL.{tenor}.DISCOUNT", day, row["discount_rate"]))
            rows.append(_obs(f"GHS.TBILL.{tenor}.YIELD", day, row["interest_rate"]))
    for row in _read_csv("usdghs_interbank.csv"):
        day = date.fromisoformat(row["date"])
        if day >= date(2026, 8, 1):
            rows.append(_obs("GHS.USDGHS.MID", day, row["mid"], unit="ghs"))
    grr_rows = _read_csv("grr_monthly.csv")
    last_grr = grr_rows[-1]
    rows.append(_obs("GHS.GRR", date.fromisoformat(last_grr["date"]), last_grr["value"]))
    # BoG weighted-median USD/GHS reference (raw fixture banner, 2026-08-07).
    rows.append(_obs("GHS.FX.USDGHS.REF", COB, "11.7615", unit="ghs"))
    for series_code, apr in APR_SEED:
        rows.append(_obs(series_code, date(2026, 7, 31), apr))
    db.add_all(rows)
    db.flush()


@pytest.fixture
def desk(db_session: Session) -> Session:
    register.ensure_default_methodology(db_session)
    register.approve_version(
        db_session,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=1,
        approved_by=LEAD,
        effective_from=date(2026, 1, 1),
    )
    _seed_fixture_observations(db_session)
    return db_session


def _computed_draft(db: Session) -> DeskDetermination:
    draft = determinations.create_draft(db, cob_date=COB, prepared_by=ANALYST)
    methodology = register.get_version(db, register.DEFAULT_METHODOLOGY_CODE, 1)
    calculation.compute_determination(db, draft, methodology=methodology)
    return determinations.get(db, draft.id)


# -- pure snapshot builders (no DB) ---------------------------------------------------


def _entry(series_code: str, as_of: date, value: str) -> dict[str, str]:
    return {"series_code": series_code, "as_of_date": as_of.isoformat(), "value": value}


def _minimal_snapshot() -> list[dict[str, str]]:
    """The smallest computable snapshot: MPR, a 20-print interbank window,
    and the three standing tender bills."""
    entries = [
        _entry("GHS.MPR", date(2026, 7, 22), "15.00"),
        _entry("GHS.TBILL.91.DISCOUNT", date(2026, 8, 3), "5.6800"),
        _entry("GHS.TBILL.182.DISCOUNT", date(2026, 8, 3), "7.3597"),
        _entry("GHS.TBILL.364.DISCOUNT", date(2026, 8, 3), "11.4904"),
    ]
    day, prints = COB, 0
    while prints < 20:
        if day.weekday() < 5:
            entries.append(_entry("GHS.INTERBANK.ON", day, "10.23"))
            prints += 1
        day -= timedelta(days=1)
    return sorted(entries, key=_canonical)


# -- real-fixture integration ----------------------------------------------------------


class TestRealFixtureIntegration:
    def test_full_pipeline_over_harvested_fixtures(self, desk: Session) -> None:
        draft = _computed_draft(desk)
        derived, qa = draft.derived_values, draft.qa_results

        # Hard gates pass on the real data.
        assert derived["qa_passed"] is True
        assert derived["rates_qa_passed"] is True
        assert derived["curves_qa_passed"] is True
        assert qa["gates"]["rates_package"] == "pass"
        assert qa["gates"]["curve_build"] == "pass"
        assert qa["gates"]["forward_qa"] == "pass"
        assert qa["gates"]["discounting"] == "synthetic_agd"
        assert qa["gates"]["credit_curve"] in {"pass", "skipped"}
        assert derived.get("discounting_mode") == "synthetic_agd"
        assert qa["forward_qa"]["passed"] is True
        assert qa["nss_fallback_used"] is False

        # AGS exists with nodes exactly at the standing bill tenors.
        ags = derived["curves"]["AEQ.GHS.SOV.ZERO"]
        assert [point["tenor_months"] for point in ags["points"]] == [3, 6, 12]
        assert len(ags["nodes"]) == 3
        assert len(ags["digest"]) == 64

        # Short-end zeros reproduce the BoG published discount->interest
        # conversions (ACT/364): simple yield off the curve == published leg.
        for node, (tenor_days, golden) in zip(
            ags["nodes"], sorted(GOLDEN_TENDER_YIELDS.items()), strict=True
        ):
            t = float(node["tenor_years"])
            zero = float(node["value_pct"]) / 100.0
            simple_yield = (math.exp(zero * t) - 1.0) / t * 100.0
            assert simple_yield == pytest.approx(golden, abs=1e-3), tenor_days

        # Derived T-bill yield rates pin the same goldens.
        for tenor_days, golden in GOLDEN_TENDER_YIELDS.items():
            entry = derived["rates"][f"GHS.TBILL.{tenor_days}.YIELD"]
            assert float(entry["value"]) == pytest.approx(golden, abs=1e-3)
            assert entry["treatment"] == "derived"

        # AGD short level: MPR 15.00 + observed -477 bps window spread.
        agd = derived["curves"]["AEQ.GHS.OIS"]
        assert float(agd["overnight_anchor_pct"]) == pytest.approx(10.23, abs=0.05)
        shortest = agd["points"][0]
        assert shortest["tenor_months"] == 1
        assert float(shortest["rate_pct"]) == pytest.approx(10.23, abs=0.05)
        assert float(qa["overnight_spread"]["mean_spread_pp"]) == pytest.approx(-4.77, abs=0.01)
        assert qa["overnight_spread"]["observations_used"] == 20

        # The sovereign forward curve is published alongside.
        fwd = derived["curves"]["AEQ.GHS.SOV.FWD"]
        assert fwd["curve_type"] == "forward"
        assert len(fwd["points"]) == len(PARAMS["fwd_node_grid_months"])

        # GRR cross-check RAN (three-input reconstruction, flag-not-block).
        assert qa["grr_check"]["status"] in {"pass", "mismatch_flagged"}
        assert "reconstructed_pct" in qa["grr_check"]

        # The rates set is complete.
        assert set(derived["rates"]) >= set(calculation.EMITTED_RATE_SERIES)
        assert derived["reference_rates"]["GHS.MPR"] == "15.000000"
        assert derived["fx_rates"]["USD/GHS"] == "11.7615"
        assert derived["fx"]["USD/GHS"]["published"] == "reference"

        # The 2024-07 GRR is stale by the declared limit — flagged, published.
        assert derived["rates"]["GHS.GRR"]["staleness_flag"] is True
        assert any(
            flag["series"] == "GHS.GRR" and flag["flag"] == "stale_carry_forward"
            for flag in qa["flags"]
        )

        # Cointegration ran as a DIAGNOSTIC with disclosed significance.
        diagnostic = qa["cointegration_diagnostic"]
        assert diagnostic["role"] == "diagnostic"
        assert diagnostic["significance_disclosed"] is True
        assert diagnostic["status"] == "computed"
        assert diagnostic["verdict"] in {"cointegrated_at_5pct", "not_cointegrated_at_5pct"}

    def test_compute_finalizes_the_snapshot_with_windowed_history(
        self, desk: Session
    ) -> None:
        draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
        assert len(draft.input_snapshot) == len(determinations.DEFAULT_INPUT_SERIES)
        digest_before = draft.input_digest

        methodology = register.get_version(desk, register.DEFAULT_METHODOLOGY_CODE, 1)
        calculation.compute_determination(desk, draft, methodology=methodology)

        interbank_entries = [
            entry
            for entry in draft.input_snapshot
            if entry["series_code"] == "GHS.INTERBANK.ON"
        ]
        assert len(interbank_entries) > 20  # the rolling window rode into the snapshot
        assert draft.input_digest != digest_before
        assert draft.input_digest == determinations.snapshot_digest(draft.input_snapshot)

    def test_compute_refuses_non_draft_and_mismatched_methodology(
        self, desk: Session
    ) -> None:
        draft = _computed_draft(desk)
        determinations.submit_for_review(desk, draft.id)
        methodology = register.get_version(desk, register.DEFAULT_METHODOLOGY_CODE, 1)
        with pytest.raises(HTTPException) as excinfo:
            calculation.compute_determination(desk, draft, methodology=methodology)
        assert excinfo.value.status_code == 409

        other = register.create_methodology(
            desk,
            methodology_code="AEQ-OTHER",
            parameters=dict(PARAMS),
            change_rationale="mismatch fixture",
            proposed_by=ANALYST,
        )
        fresh = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
        with pytest.raises(HTTPException) as excinfo:
            calculation.compute_determination(desk, fresh, methodology=other)
        assert excinfo.value.status_code == 409


# -- reproducibility invariant -----------------------------------------------------------


class TestReproducibility:
    def test_same_snapshot_same_methodology_is_byte_identical(self, desk: Session) -> None:
        draft = _computed_draft(desk)
        snapshot = draft.input_snapshot

        first_derived, first_qa = run_pipeline(snapshot, PARAMS, COB)
        second_derived, second_qa = run_pipeline(snapshot, PARAMS, COB)

        assert _canonical(first_derived) == _canonical(second_derived)
        assert _canonical(first_qa) == _canonical(second_qa)
        # Stored results equal pure pipeline output plus package metadata
        # (digest + empty research_adjustments) folded in after compute.
        stored = dict(draft.derived_values)
        package_digest = stored.pop("package_digest", None)
        research_adjustments = stored.pop("research_adjustments", None)
        assert package_digest and len(package_digest) == 64
        assert research_adjustments == []
        assert _canonical(stored) == _canonical(first_derived)
        # Curve digests are value-based and stable.
        for code in ("AEQ.GHS.SOV.ZERO", "AEQ.GHS.SOV.FWD", "AEQ.GHS.OIS"):
            assert first_derived["curves"][code]["digest"] == (
                second_derived["curves"][code]["digest"]
            )

    def test_two_drafts_over_identical_observations_match(self, desk: Session) -> None:
        first = _computed_draft(desk)
        second = _computed_draft(desk)
        assert first.input_digest == second.input_digest
        assert _canonical(first.derived_values) == _canonical(second.derived_values)
        assert _canonical(first.qa_results) == _canonical(second.qa_results)


# -- QA-gate failure path ------------------------------------------------------------------


class TestQaGateFailure:
    def _poisoned_snapshot(self) -> list[dict[str, str]]:
        """Real bills + synthetic GoG bonds priced off violently alternating
        yields (30% / 5% / 35% / 4%) — an internally repriceable but
        economically absurd set that rings the forward curve."""
        entries = _minimal_snapshot()
        for maturity, ytm in (
            (date(2028, 8, 7), 0.30),
            (date(2030, 8, 7), 0.05),
            (date(2032, 8, 7), 0.35),
            (date(2034, 8, 7), 0.04),
        ):
            price = bond_price(ytm, COB, maturity, 0.10, 2, DayCount.ACT_365F, clean=True)
            code = f"GHS.GOG.BOND.{maturity.strftime('%Y%m%d')}.1000.CLEAN"
            entries.append(_entry(code, date(2026, 8, 6), f"{price:.4f}"))
        return sorted(entries, key=_canonical)

    def test_oscillating_bonds_fail_curves_qa_but_rates_package_still_passes(self) -> None:
        """Rates-first: forward oscillation fails curves QA without killing rates."""
        derived, qa = run_pipeline(self._poisoned_snapshot(), PARAMS, COB)

        assert derived["curves_qa_passed"] is False
        assert qa["curves_qa_passed"] is False
        assert qa["gates"]["forward_qa"] == "fail"
        assert qa["forward_qa"]["passed"] is False
        assert qa["forward_qa"]["positivity_pass"] is False
        # Rates package is independent of curve smoothness.
        assert derived["rates_qa_passed"] is True
        assert derived["qa_passed"] is True  # legacy key follows rates readiness
        assert qa["gates"]["rates_package"] == "pass"
        assert "GHS.MPR" in derived["rates"]
        assert "GHS.INTERBANK.ON" in derived["rates"]
        # The curve attempt is still recorded: the nodes exist for review.
        assert len(derived["curves"]["AEQ.GHS.SOV.ZERO"]["points"]) == 7
        assert any(flag["flag"] == "forward_qa_failed" for flag in qa["flags"])

    def test_failed_rates_gate_blocks_approval_curve_failure_does_not(self) -> None:
        derived, qa = run_pipeline(self._poisoned_snapshot(), PARAMS, COB)
        determination = DeskDetermination(
            cob_date=COB,
            methodology_code=register.DEFAULT_METHODOLOGY_CODE,
            methodology_version=1,
            input_snapshot=[],
            input_digest="0" * 64,
            derived_values=derived,
            qa_results=qa,
            status="pending_review",
            prepared_by=ANALYST,
        )
        # Curve QA failed but rates package passed — approval is allowed.
        calculation.ensure_approvable(determination)

        determination.derived_values = {
            "qa_passed": False,
            "rates_qa_passed": False,
            "curves_qa_passed": False,
        }
        with pytest.raises(HTTPException) as excinfo:
            calculation.ensure_approvable(determination)
        assert excinfo.value.status_code == 409

        determination.derived_values = {"rates_qa_passed": True, "qa_passed": True}
        calculation.ensure_approvable(determination)

        determination.derived_values = {}
        with pytest.raises(HTTPException) as empty:
            calculation.ensure_approvable(determination)
        assert empty.value.status_code == 409


# -- declared treatments ---------------------------------------------------------------------


class TestTreatments:
    def test_series_without_declared_treatment_is_a_hard_error(self) -> None:
        snapshot = [*_minimal_snapshot(), _entry("GHS.MYSTERY.RATE", COB, "1.23")]
        with pytest.raises(CalculationError, match="GHS.MYSTERY.RATE"):
            run_pipeline(sorted(snapshot, key=_canonical), PARAMS, COB)

    def test_every_emitted_series_has_a_declared_treatment(self) -> None:
        for series_code in calculation.EMITTED_RATE_SERIES:
            treatment = calculation.resolve_treatment(series_code, PARAMS)
            assert treatment["treatment"] in {"pass_through", "windowed", "derived"}
        for series_code in ("GHS.USDGHS.MID", "GHS.FX.USDGHS.REF"):
            assert calculation.resolve_treatment(series_code, PARAMS)["treatment"] == (
                "pass_through"
            )
        # Pattern families resolve too (per-bank APRs, GFIM bond quotes).
        assert calculation.resolve_treatment("GHS.APR.GCB", PARAMS)["role"] == (
            "lending_indicator_input"
        )
        assert calculation.resolve_treatment(
            "GHS.GOG.BOND.20330329.1250.CLEAN", PARAMS
        )["treatment"] == "bond_quote"

    def test_every_series_in_a_computed_snapshot_resolves(self, desk: Session) -> None:
        draft = _computed_draft(desk)
        for series_code in {entry["series_code"] for entry in draft.input_snapshot}:
            assert calculation.resolve_treatment(series_code, PARAMS)


# -- adapter conformance: real pipeline output publishes through the seam -------------


class TestPublishConformance:
    def test_computed_derived_values_publish_through_the_adapter_seam(
        self, desk: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The assembled derived_values shape must be exactly what
        ``aequor_desk.build_extraction`` consumes: stringified percent rates,
        integer tenor months, flat reference_rates/fx_rates alongside the
        rich sections."""
        storage = InMemoryStorageClient()
        for target in (
            "app.adapters.market_data.pull_runner.get_storage_client",
            "app.adapters.market_data.cache.get_storage_client",
        ):
            monkeypatch.setattr(target, lambda: storage)
        bank = Bank(
            organization_id=ORG_1,
            name="Desk Calc Publish Bank",
            short_name="desk-calc-publish",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
        )
        desk.add(bank)
        desk.commit()

        draft = _computed_draft(desk)
        determinations.submit_for_review(desk, draft.id)
        calculation.ensure_approvable(draft)  # the API-layer gate passes
        determinations.approve(desk, draft.id, reviewed_by=LEAD)
        desk.commit()

        result = publication.publish(desk, draft.id, actor=LEAD)
        assert result.status == "complete", result.results

        for curve_name in ("AEQ.GHS.SOV.ZERO", "AEQ.GHS.SOV.FWD", "AEQ.GHS.OIS"):
            curve = desk.scalar(
                select(CanonicalYieldCurve).where(
                    CanonicalYieldCurve.bank_id == bank.id,
                    CanonicalYieldCurve.curve_name == curve_name,
                    CanonicalYieldCurve.superseded_by.is_(None),
                )
            )
            assert curve is not None, curve_name
        for index_code in calculation.EMITTED_RATE_SERIES:
            index = desk.scalar(
                select(CanonicalMarketIndex).where(
                    CanonicalMarketIndex.bank_id == bank.id,
                    CanonicalMarketIndex.index_code == index_code,
                    CanonicalMarketIndex.superseded_by.is_(None),
                )
            )
            assert index is not None, index_code
        fx = desk.scalar(
            select(CanonicalFxRate).where(
                CanonicalFxRate.bank_id == bank.id,
                CanonicalFxRate.base_currency == "USD",
                CanonicalFxRate.quote_currency == "GHS",
                CanonicalFxRate.superseded_by.is_(None),
            )
        )
        assert fx is not None
        assert fx.rate == Decimal("11.7615")
