"""Unit tests for the interest-rate-swap leg decomposition in regulatory_irr.

``_swap_legs`` turns one seed-shaped ``irr_swap`` fact into its two hedge
legs. Every expectation here is hand-derived from the fact attributes and the
base curve (explicit Decimal literals), never echoed from the engine:

- pay-fixed 120M @ 25.3 fixed vs the 91d T-bill index: the floating receive
  leg reprices at the base-curve zero for its 0.17y midpoint (25.8), so
  carry = 120,000,000 x (25.8 - 25.3) / 100 = +600,000;
- receive-fixed is the mirror image: fixed leg received (asset), floating leg
  paid (liability), carry = 120,000,000 x (25.3 - 25.8) / 100 = -600,000.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.irr.engine import compute_gap, compute_nii
from app.models import BankFinancialFact
from app.services.regulatory_irr import IrrRunError, _swap_legs

NOTIONAL = Decimal("120000000")
FIXED_RATE = Decimal("25.3")
# Base-curve zero rates keyed by bucket midpoint (percent), as in the seed.
CURVE = {Decimal("0.17"): Decimal("25.8"), Decimal("1.9"): Decimal("27.8")}


def _fact(attributes: dict[str, str]) -> BankFinancialFact:
    return BankFinancialFact(
        fact_group="irr_swap",
        category="IRS-UNIT-001",
        amount=NOTIONAL,
        attributes=attributes,
    )


def _pay_fixed_fact(direction: str | None = "pay_fixed") -> BankFinancialFact:
    attributes = {
        "pay_rate_pct": "25.3",
        "receive_bucket": "1-3m",
        "receive_midpoint_years": "0.17",
        "pay_bucket": "1-3y",
        "pay_midpoint_years": "1.9",
    }
    if direction is not None:
        attributes["direction"] = direction
    return _fact(attributes)


def _receive_fixed_fact() -> BankFinancialFact:
    # Mirror image: the fixed leg (remaining maturity, 1-3y) is received, the
    # floating leg (index reset, 1-3m) is paid.
    return _fact(
        {
            "direction": "receive_fixed",
            "pay_rate_pct": "25.3",
            "receive_bucket": "1-3y",
            "receive_midpoint_years": "1.9",
            "pay_bucket": "1-3m",
            "pay_midpoint_years": "0.17",
        }
    )


def test_pay_fixed_swap_decomposes_into_floating_asset_and_fixed_liability() -> None:
    receive, pay = _swap_legs(_pay_fixed_fact(), CURVE)

    assert receive.side == "asset"
    assert receive.bucket == "1-3m"
    assert receive.amount == NOTIONAL
    # Floating index rate = base-curve zero at the receive-leg midpoint (0.17y).
    assert receive.rate_pct == Decimal("25.8")
    assert receive.fixed_or_float == "float"
    assert receive.is_hedge is True

    assert pay.side == "liability"
    assert pay.bucket == "1-3y"
    assert pay.amount == NOTIONAL
    assert pay.rate_pct == FIXED_RATE
    assert pay.fixed_or_float == "fixed"
    assert pay.is_hedge is True

    # Carry = 120M x (25.8 - 25.3)/100 = +600,000.
    assert compute_nii([receive, pay]) == Decimal("600000")


def test_receive_fixed_swap_is_the_mirror_image_with_flipped_carry() -> None:
    receive, pay = _swap_legs(_receive_fixed_fact(), CURVE)

    # The fixed leg becomes the asset (receive side) at the maturity bucket...
    assert receive.side == "asset"
    assert receive.bucket == "1-3y"
    assert receive.rate_pct == FIXED_RATE
    assert receive.fixed_or_float == "fixed"
    assert receive.is_hedge is True

    # ...and the floating leg the liability (pay side) at the index-reset
    # bucket, repricing at the base-curve zero for its 0.17y midpoint.
    assert pay.side == "liability"
    assert pay.bucket == "1-3m"
    assert pay.rate_pct == Decimal("25.8")
    assert pay.fixed_or_float == "float"
    assert pay.is_hedge is True

    # Carry sign flips: 120M x (25.3 - 25.8)/100 = -600,000.
    assert compute_nii([receive, pay]) == Decimal("-600000")

    # Gap contributions flip buckets/sides versus the pay-fixed decomposition.
    pay_fixed_gap = compute_gap(_swap_legs(_pay_fixed_fact(), CURVE))
    receive_fixed_gap = compute_gap([receive, pay])
    pf = {bucket.bucket: bucket for bucket in pay_fixed_gap.buckets}
    rf = {bucket.bucket: bucket for bucket in receive_fixed_gap.buckets}
    assert pf["1-3m"].gap == NOTIONAL
    assert pf["1-3y"].gap == -NOTIONAL
    assert rf["1-3m"].gap == -NOTIONAL
    assert rf["1-3y"].gap == NOTIONAL


def test_missing_direction_defaults_to_pay_fixed() -> None:
    # Facts derived before the direction attribute existed keep pricing as
    # pay-fixed swaps.
    receive, pay = _swap_legs(_pay_fixed_fact(direction=None), CURVE)
    assert receive.fixed_or_float == "float"
    assert pay.fixed_or_float == "fixed"
    assert compute_nii([receive, pay]) == Decimal("600000")


def test_unknown_direction_fails_the_run_as_data() -> None:
    fact = _pay_fixed_fact(direction="basis_swap")
    with pytest.raises(IrrRunError) as excinfo:
        _swap_legs(fact, CURVE)
    assert excinfo.value.code == "unsupported_swap_direction"
    assert excinfo.value.details == {"category": "IRS-UNIT-001", "direction": "basis_swap"}
