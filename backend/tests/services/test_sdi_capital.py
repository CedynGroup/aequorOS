"""SDI simplified s.29 capital regime (docs/sdi.md §4.2, Phase E).

The capital engine is shared; the institution class selects the parameter regime.
A bank still requires the full Basel CRD threshold set (unchanged); an SDI needs
only the CAR floor (board row, else the control-plane class default) and computes
CAR over the credit-risk base against the s.29 floor, with market/operational/
tier/leverage charges zeroed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.capital.engine import CapitalParams
from app.services.regulatory_capital import (
    CapitalRunError,
    _ActiveCapitalParams,  # pyright: ignore[reportPrivateUsage]
    _engine_params,  # pyright: ignore[reportPrivateUsage]
)

_ZERO = Decimal("0")


def _sdi_active(**overrides: object) -> _ActiveCapitalParams:
    base: dict[str, object] = {
        "risk_weights": {"loans": Decimal("100")},
        "thresholds": {},
        "institution_class": "sdi",
        "car_min_fallback": Decimal("10"),
    }
    base.update(overrides)
    return _ActiveCapitalParams(**base)  # type: ignore[arg-type]


def test_sdi_regime_needs_only_the_car_floor() -> None:
    params = _engine_params(_sdi_active())
    assert isinstance(params, CapitalParams)
    assert params.car_min_pct == Decimal("10")
    # s.29 has a single floor: early-warning and critical collapse onto it.
    assert params.car_early_warning_pct == Decimal("10")
    assert params.car_critical_pct == Decimal("10")
    # Market / operational / tier / leverage are structurally excluded under s.29.
    assert params.fx_charge_pct == _ZERO
    assert params.bia_alpha_pct == _ZERO
    assert params.cet1_min_pct == _ZERO
    assert params.tier1_min_pct == _ZERO
    assert params.leverage_min_pct == _ZERO
    # Risk weights still come from the tenant register (never invented).
    assert params.risk_weights == {"loans": Decimal("100")}


def test_sdi_board_row_overrides_the_control_plane_floor() -> None:
    # A tenant that tightened its CAR floor to 12% via its board register wins
    # over the 10% control-plane default.
    params = _engine_params(_sdi_active(thresholds={"car_min": Decimal("12")}))
    assert params.car_min_pct == Decimal("12")


def test_sdi_without_any_car_floor_fails_loud() -> None:
    with pytest.raises(CapitalRunError) as exc:
        _engine_params(_sdi_active(car_min_fallback=None))
    assert exc.value.code == "missing_parameter"


def test_bank_regime_still_requires_the_full_basel_set() -> None:
    # institution_class defaults to 'bank'; a bank with no thresholds must still
    # raise the Basel missing_parameter error (byte-identical behaviour).
    active = _ActiveCapitalParams(risk_weights={}, thresholds={})
    with pytest.raises(CapitalRunError) as exc:
        _engine_params(active)
    assert exc.value.code == "missing_parameter"
    assert "cet1_min" in str(exc.value)
