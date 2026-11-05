"""``app.bi.service.limits``: the limit a BI figure is judged against (D-069).

Every expectation here is worked from the two registers the canonical fixture
bank carries, never from a number this file invented:

* the **regulatory control plane** seeded for every hermetic schema by
  ``tests/fixtures/reference_data.py`` from ``regulatory_parameters.SEED_ROWS``
  — where ``car_min`` for the bank class lives; and
* the bank's own **board register**, seeded by
  ``tests/fixtures/canonical_bank_fixture.py`` from
  ``parameter_register.BANK_CAPITAL_THRESHOLDS`` / ``BANK_FX_THRESHOLDS`` —
  where the net-open-position and internal liquidity limits live.

Both catalogues are imported rather than copied, so a value staff change in
either register moves the expectation with it and cannot leave a stale literal
here asserting yesterday's limit.

This module's other-suite cases live in ``test_limits_integration.py``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import get_args

from app.bi.domain.catalogue.members import NUMERIC_VALUE_TYPES
from app.bi.service import limits
from app.bi.service.limits import (
    GovernedLimit,
    NoGovernedLimit,
    absence_copy,
    is_governed,
)
from app.policy.service import regulatory_parameters
from tests.bi.service.test_limits_integration import AS_OF, CAR_MIN

if CAR_MIN.value is None:  # pragma: no cover - a scalar floor in every generation
    raise AssertionError("car_min is a scalar in the control-plane seed catalogue")


def test_neither_outcome_is_a_truthiness_test() -> None:
    """A limit OF zero must not be falsy, and an absent one must not be truthy.

    Neither class defines ``__bool__``, so ``if limit:`` cannot be used as the
    check — :func:`is_governed` is. A zero limit is a real limit (a board may
    hold a product margin at zero) and is the one value a truthiness test gets
    wrong in the dangerous direction.
    """
    zero = GovernedLimit(
        param_code="ftp_min_product_margin_pct",
        measure_id="x",
        value=Decimal(0),
        stated_as="pct",
        authority="board_register",
        source_unit="percent",
        source="test",
        confirmation_status=None,
        source_row_id="row",
        effective_from=AS_OF,
        effective_to=None,
        as_of=AS_OF,
    )
    assert is_governed(zero)
    assert bool(zero) is True
    assert zero.value == Decimal(0)
    missing = NoGovernedLimit(param_code="x", measure_id="x", reason="no_governed_row", as_of=AS_OF)
    assert not is_governed(missing)


def test_the_unit_rule_admits_only_exact_scalings() -> None:
    factors = limits.conversion_factors()
    assert set(factors) == {
        ("percent", "pct"),
        ("percent", "fraction"),
        ("bps", "pct"),
        ("bps", "fraction"),
        ("count", "count"),
    }
    for (unit, value_type), factor in factors.items():
        assert value_type in NUMERIC_VALUE_TYPES, (unit, value_type)
        # A power of ten: exact in decimal, so no limit is ever rounded.
        assert factor.normalize().as_tuple().digits == (1,), (unit, factor)


def test_a_percentage_row_is_scaled_into_a_fraction_valued_measure() -> None:
    """13 percent against a proportion-of-one figure is 0.13, exactly."""
    factor = limits.conversion_factors()[("percent", "fraction")]
    assert Decimal("13") * factor == Decimal("0.13")
    assert Decimal("6.5") * factor == Decimal("0.065")


def test_the_value_is_stated_the_way_the_control_plane_states_its_own() -> None:
    """``_normalise`` mirrors ``ResolvedParameter.normalized_value``, not its own rule."""
    for raw in ("100.000000", "13", "6.500000", "0.065000", "1250.000000"):
        value = Decimal(raw)
        mirrored = regulatory_parameters.ResolvedParameter(
            param_code="x",
            value=value,
            value_json=None,
            unit="percent",
            source_citation="",
            confirmation_status="confirmed",
            scope_type="institution_class",
            scope_key="bank",
            jurisdiction_code="GH",
            effective_from=AS_OF,
            parameter_id="row",
        ).normalized_value
        assert limits._normalise(value) == mirrored
        assert str(limits._normalise(value)) == str(mirrored)


def test_every_absence_reason_has_production_copy() -> None:
    """A reason with no sentence is a widget that shows nothing and says nothing."""
    for reason in get_args(limits.LimitAbsenceReason):
        copy = absence_copy(reason)
        assert copy.endswith(".")
        assert copy[0].isupper()
        assert "_" not in copy, reason
