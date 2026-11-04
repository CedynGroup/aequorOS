"""Tenant overrides may only tighten a regulatory floor (QA audit 2026-08-20 P1-5).

A per-tenant board register (``ParamCapitalThreshold`` …) must never be able to
WEAKEN a control-plane regulatory floor/limit. ``regulatory_parameters.tighten``
is the shared unit-aware guard; the capital engine applies it so a board CAR
minimum below the regulatory floor (Ghana 13% = 10% + 3% CCB) is clamped up.

This module's other-suite cases live in
``test_regulatory_parameter_tightening_integration.py``.
"""

from __future__ import annotations

from decimal import Decimal

from app.policy.service import regulatory_parameters as rp


def test_tighten_floor_takes_the_higher_value() -> None:
    # A board minimum below the regulatory floor is raised to the floor …
    assert rp.tighten("car_min", Decimal("10"), Decimal("13")) == Decimal("13")
    # … a stricter board minimum is honoured.
    assert rp.tighten("car_min", Decimal("15"), Decimal("13")) == Decimal("15")


def test_tighten_ceiling_takes_the_lower_value() -> None:
    # A board exposure limit above the regulatory ceiling is capped to it …
    assert rp.tighten("large_exposure_limit_pct", Decimal("25"), Decimal("20")) == Decimal("20")
    # … a stricter (lower) board limit is honoured.
    assert rp.tighten("large_exposure_limit_pct", Decimal("15"), Decimal("20")) == Decimal("15")


def test_undirected_code_returns_the_tenant_value() -> None:
    # A code with no declared direction is unconstrained (used as-is).
    assert rp.tighten("car_early_warning", Decimal("5"), Decimal("13")) == Decimal("5")
