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
from app.market_data.service.market_desk.calculation import run_pipeline
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
