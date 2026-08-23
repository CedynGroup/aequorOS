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
from fastapi import HTTPException

from app.domain.capital.engine import CapitalParams
from app.services import sdi_capital
from app.services.regulatory_capital import (
    CapitalRunError,
    _ActiveCapitalParams,  # pyright: ignore[reportPrivateUsage]
    _buffers_or_409,  # pyright: ignore[reportPrivateUsage]
    _engine_params,  # pyright: ignore[reportPrivateUsage]
)

_ZERO = Decimal("0")


def _sdi_active(**overrides: object) -> _ActiveCapitalParams:
    base: dict[str, object] = {
        "risk_weights": {"loans": Decimal("100")},
        "thresholds": {},
        "institution_class": "sdi",
        "car_min_fallback": Decimal("10"),
        # ``_load_active_params`` resolves the governed RWA scope for every SDI, so
        # an SDI params object always carries one. The unit fixture supplies the
        # documented default explicitly rather than relying on a code substitution
        # inside ``_sdi_engine_params`` — the audit's D-19 "unlabelled substitution",
        # which is now a hard error there.
        "rwa_scope": sdi_capital.default_rwa_scope(),
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


# --- the dashboard's floor block: ONE authority, shared with the engine -------
#
# NEW-53. The Basel overview used to read Tier 1 / CET1 / leverage minima off the
# latest STORED RegulatoryRun's ``threshold_min``. That is a record of what was
# applied when the run executed, and it is absent entirely before a bank's first
# official capital run — so the page showed a green Tier 1 KPI, "this run carries
# no Tier 1 minimum · NOT ASSESSED" and a passing Tier 1 validation at once.
# ``_buffers_or_409`` now publishes the same floors the engine is handed, so the
# three panels cannot disagree. These pin that they are the SAME dict, and that
# an SDI reports the structurally-excluded sub-tier floors as absent rather than
# as register rows the s.29 engine never applies.


def _bank_thresholds() -> dict[str, Decimal]:
    return {
        "bia_alpha_pct": Decimal("15"),
        "car_critical": Decimal("9"),
        "car_early_warning": Decimal("13.5"),
        "car_min": Decimal("13"),
        "cet1_min": Decimal("6.5"),
        "fx_charge_pct": Decimal("10"),
        "leverage_min": Decimal("6"),
        "rwa_multiplier": Decimal("1250"),
        "tier1_min": Decimal("8"),
        "tier2_gp_cap_pct_credit_rwa": Decimal("1.25"),
    }


def test_bank_buffers_publish_the_same_floors_the_engine_applies() -> None:
    active = _ActiveCapitalParams(risk_weights={}, thresholds=_bank_thresholds())
    buffers = _buffers_or_409(active, Decimal("15.83"))
    params = _engine_params(active)

    assert buffers.car_min_pct == params.car_min_pct
    assert buffers.cet1_min_pct == params.cet1_min_pct
    assert buffers.tier1_min_pct == params.tier1_min_pct
    assert buffers.leverage_min_pct == params.leverage_min_pct


def test_sdi_buffers_report_the_basel_sub_tier_floors_as_absent() -> None:
    # Even when the tenant's register happens to carry Basel rows, s.29 excludes
    # them: absence renders as absence, never as a floor nothing is judged against.
    thresholds = _bank_thresholds()
    active = _sdi_active(thresholds=thresholds)
    buffers = _buffers_or_409(active, Decimal("14.00"))

    assert buffers.car_min_pct == Decimal("13")
    assert buffers.cet1_min_pct is None
    assert buffers.tier1_min_pct is None
    assert buffers.leverage_min_pct is None


def test_buffers_refuse_rather_than_guess_a_missing_car_ladder() -> None:
    thresholds = _bank_thresholds()
    del thresholds["car_early_warning"]
    active = _ActiveCapitalParams(risk_weights={}, thresholds=thresholds)
    with pytest.raises(HTTPException) as exc:
        _buffers_or_409(active, Decimal("15.83"))
    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "missing_parameter"  # type: ignore[index]
