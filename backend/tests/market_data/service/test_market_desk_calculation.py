"""The desk calculation pipeline (spec §5 steps 1-9): reproducibility, real
fixtures, hard QA gates, declared treatments.

The integration tests seed DeskObservations from the REAL harvested Ghana
fixtures (tests/fixtures/market_desk/series/ — values captured off the wire
2026-08-09) and run the full pipeline for cob 2026-08-07, pinning the BoG
golden yield conversions (5.68 -> 5.7618 etc.) and the observed -477 bps
interbank-to-MPR spread. The pure tests exercise :func:`run_pipeline`
directly: same snapshot + same methodology version must be byte-identical,
poisoned inputs must fail the gate loudly, undeclared series must refuse.

This module's other-suite cases live in ``test_market_desk_calculation_integration.py``.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from app.market_data.domain.curves.conventions import DayCount, bond_price
from app.market_data.service.market_desk import calculation, register
from app.market_data.service.market_desk.calculation import CalculationError, run_pipeline
from app.models import (
    DeskDetermination,
)
from tests.market_data.service.test_market_desk_calculation_integration import (
    ANALYST,
    COB,
    PARAMS,
    _canonical,
    _entry,
    _minimal_snapshot,
)


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


class TestRealFixtureIntegration:
    """The dense-surface case that runs on the pure fixture helper, not a database."""

    def test_legacy_sparse_tenor_parameter_still_publishes_dense_surface(self) -> None:
        parameters = dict(PARAMS)
        parameters["fwd_node_grid_months"] = [3, 6, 12, 24, 36, 60, 84, 120]
        derived, _ = run_pipeline(_minimal_snapshot(), parameters, COB)

        tenors = [point["tenor_months"] for point in derived["curves"]["AEQ.GHS.SOV.FWD"]["points"]]
        assert tenors[:12] == list(range(1, 13))
        assert tenors[-1] == 120
        assert set(derived["forward_grids"]["AEQ.GHS.SOV.FWD"]["grids"]) == {
            "1D",
            "1M",
            "3M",
            "6M",
            "1Y",
        }


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
        assert (
            calculation.resolve_treatment("GHS.GOG.BOND.20330329.1250.CLEAN", PARAMS)["treatment"]
            == "bond_quote"
        )
