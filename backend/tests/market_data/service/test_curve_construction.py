"""FC-3 curve construction service: construction + QA, definition governance, publish.

Construction is a pure function of (quotes x definition version x calendar) — same
inputs, identical grid and value digest. Definition governance mirrors the
methodology register's Track-2 rules (immutable after approval, dual control).
Publication reuses the desk determination -> fan-out seam, so a constructed forward
curve lands in every bank's canonical store under its AEQ.* code.

This module's other-suite cases live in ``test_curve_construction_integration.py``.
"""

from __future__ import annotations

import pytest

from app.market_data.domain.curves.calendars import GHANA
from app.market_data.service.market_desk import curve_construction as cc
from app.models import (
    DeskCurveDefinition,
)
from tests.market_data.service.test_curve_construction_integration import (
    ANALYST,
    COB,
    CURVE_CODE,
    GHS_QUOTES,
    LEAD,
    _spec,
)


def _definition(**overrides: object) -> DeskCurveDefinition:
    """An in-memory approved definition for the pure construction tests."""
    spec = _spec(**overrides)
    return DeskCurveDefinition(
        curve_code=spec.curve_code,
        version=1,
        status="approved",
        currency=spec.currency,
        calendar_name=spec.calendar_name,
        curve_kind=spec.curve_kind,
        projection_index=spec.projection_index,
        discount_curve_code=spec.discount_curve_code,
        instrument_set_ref=spec.instrument_set_ref,
        interpolation_method=spec.interpolation_method,
        output_daycount=spec.output_daycount,
        payment_frequency=spec.payment_frequency,
        payment_interval_months=spec.payment_interval_months,
        curve_frequency=spec.curve_frequency,
        spot_lag_days=spec.spot_lag_days,
        roll_convention=spec.roll_convention,
        extrapolation_rule=spec.extrapolation_rule,
        params=spec.params,
        change_rationale="test",
        proposed_by=ANALYST,
        approved_by=LEAD,
    )


def test_build_ghana_sovereign_forward_grid_has_stub_and_forward_rows() -> None:
    result = cc.build_forward_curve(_definition(), COB, GHS_QUOTES, calendar=GHANA)

    # Spot stub (row 0) + 8 forward periods (grid_periods).
    assert len(result.grid.rows) == 9
    stub = result.grid.rows[0]
    assert stub.start == COB
    assert stub.discount_factor == 1.0
    assert stub.yield_ == 0.0

    # Each forward row chains, discounts below 1, and carries a positive yield.
    for previous, row in zip(result.grid.rows, result.grid.rows[1:], strict=False):
        assert row.start == previous.end
        assert 0.0 < row.discount_factor <= 1.0
        assert row.yield_ > 0.0


def test_construction_passes_hard_qa_gates_and_reprices_to_input() -> None:
    result = cc.build_forward_curve(_definition(), COB, GHS_QUOTES, calendar=GHANA)
    qa = result.qa

    assert qa.passed
    # Reprice acid test (spec §2.3): every instrument reprices to its input.
    assert qa.reprice_pass
    assert qa.reprice_max_residual < 1e-8
    assert all(abs(pillar.reprice_residual) < 1e-8 for pillar in result.pillars)
    # Forward positivity + oscillation gate (qa_forwards) and monotone DF.
    assert qa.forward_qa.positivity_pass
    assert qa.forward_qa.oscillation_pass
    assert qa.monotone_df_pass
    assert qa.pillar_coverage_pass
    assert qa.pillar_count == len(GHS_QUOTES)


def test_construction_is_deterministic_and_value_digested() -> None:
    first = cc.build_forward_curve(_definition(), COB, GHS_QUOTES, calendar=GHANA)
    second = cc.build_forward_curve(_definition(), COB, GHS_QUOTES, calendar=GHANA)

    assert first.input_digest == second.input_digest
    assert len(first.input_digest) == 64
    assert first.grid == second.grid  # frozen dataclass value equality

    # A changed quote changes the digest and the grid.
    bumped = [dict(quote) for quote in GHS_QUOTES]
    bumped[0]["quote"] = 0.246
    changed = cc.build_forward_curve(_definition(), COB, bumped, calendar=GHANA)
    assert changed.input_digest != first.input_digest

    # A changed definition version changes the digest even for identical quotes.
    v2_defn = _definition()
    v2_defn.version = 2
    v2 = cc.build_forward_curve(v2_defn, COB, GHS_QUOTES, calendar=GHANA)
    assert v2.input_digest != first.input_digest


def test_derived_values_conform_to_aequor_desk_forward_contract() -> None:
    result = cc.build_forward_curve(_definition(), COB, GHS_QUOTES, calendar=GHANA)
    derived = cc.to_determination_derived_values(result, CURVE_CODE)

    assert derived["curves_qa_passed"] is True
    spec = derived["curves"][CURVE_CODE]
    assert spec["curve_type"] == "forward"
    points = spec["points"]
    # One point per forward row (stub dropped); tenor_months step = curve frequency.
    assert len(points) == len(result.grid.rows) - 1
    assert points[0]["tenor_months"] == 3
    assert points[1]["tenor_months"] == 6
    # Rates are PERCENT (the adapter divides by 100 to the canonical decimal store).
    assert all(isinstance(point["rate_pct"], float) for point in points)
    assert points[0]["rate_pct"] == pytest.approx(result.grid.rows[1].yield_ * 100.0)
    stored = derived["forward_grids"][CURVE_CODE]
    assert set(stored["grids"]) == {"1D", "1M", "3M", "6M", "1Y"}
    assert len(stored["grids"]["1D"]["rows"]) == 23
    assert len(stored["grids"]["1Y"]["rows"]) == 31
    assert stored["rows"][0] == {
        "start": COB.isoformat(),
        "end": result.grid.rows[0].end.isoformat(),
        "discount_factor": "1.000000000000",
        "forward_yield": "0.000000000000",
    }


def test_unknown_instrument_and_calendar_raise_construction_error() -> None:
    with pytest.raises(cc.CurveConstructionError):
        cc.build_forward_curve(
            _definition(),
            COB,
            [{"instrument": "future", "tenor": "3M", "quote": 0.2}],
            calendar=GHANA,
        )
    with pytest.raises(cc.CurveConstructionError):
        cc.resolve_calendar("ATLANTIS")
