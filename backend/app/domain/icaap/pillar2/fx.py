"""FX add-on: the revaluation LOSS under a governed shock, net of Pillar 1.

The method this replaces charged ``Tier 1 × max(stressed NOP% − base NOP%, 0)``
— the CHANGE in a ratio, which is not a loss and can be zero for a bank whose
open position is large but steady (audit M8). What a bank actually loses when
the reporting currency moves is the revaluation of its open positions, so that
is what is computed here.

Two directions, because an open book is not symmetric:

* the reporting currency DEPRECIATES by s (foreign currency dearer): a SHORT
  position loses — ``L_dep = Σ max(0, −net·s/100)``;
* it APPRECIATES by a: a LONG position loses — ``L_app = Σ max(0, net·a/100)``.

The loss is the worse of the two, and there is **no cross-currency offset**: a
gain on one currency is not netted against a loss on another. That mirrors the
IRRBB aggregation BoG uses and is deliberately conservative — a bank that wants
offset can evidence a hedge rather than assume it.

Pillar 1 already charges capital for the FX position (market RWA × the minimum
ratio). Only the excess is a Pillar 2 need: ``max(0, loss − Pillar 1)``.

The shocks are a governed table (``fx_p2_shock_pct``), per currency with a
default. A currency with neither is ``incomplete``, never zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.domain.icaap.pillar2.types import (
    MethodResult,
    MethodStatus,
    MissingParameter,
    ParameterUse,
    detail_of,
    text,
)
from app.domain.icaap.units import HUNDRED, ZERO, Basis, amount, pillar1_capital

PARAM_FX_SHOCK = "fx_p2_shock_pct"
PARAM_CAR_MIN = "car_min"
METHOD = "fx_nop_addon"

Direction = Literal["depreciation", "appreciation"]
DEPRECIATION: Direction = "depreciation"
APPRECIATION: Direction = "appreciation"
BOTH_DIRECTIONS: tuple[Direction, ...] = (DEPRECIATION, APPRECIATION)


@dataclass(frozen=True)
class CurrencyPosition:
    """A net open position in one currency, in reporting-currency equivalent."""

    currency: str
    #: Positive = long the foreign currency, negative = short.
    net: Decimal


class MissingShockError(ValueError):
    """No shock is governed for this currency in this direction."""

    def __init__(self, currency: str, direction: str):
        self.currency = currency
        self.direction = direction
        super().__init__(f"fx_shock_missing:{currency}:{direction}")


@dataclass(frozen=True)
class FxShockSet:
    """The governed shock table, per currency with an optional default."""

    depreciation_pct: Mapping[str, Decimal]
    appreciation_pct: Mapping[str, Decimal]
    default_depreciation_pct: Decimal | None = None
    default_appreciation_pct: Decimal | None = None
    #: Which directions to evaluate. A stress scenario shocks one way only.
    directions: tuple[Direction, ...] = BOTH_DIRECTIONS

    def shock(self, currency: str, direction: Direction) -> Decimal:
        table = self.depreciation_pct if direction == DEPRECIATION else self.appreciation_pct
        default = (
            self.default_depreciation_pct
            if direction == DEPRECIATION
            else self.default_appreciation_pct
        )
        value = table.get(currency, default)
        if value is None:
            raise MissingShockError(currency, direction)
        return value

    @classmethod
    def single_direction(cls, direction: Direction, pct: Decimal) -> FxShockSet:
        """One scenario's one-way shock — what a stress overlay applies."""
        return cls(
            depreciation_pct={},
            appreciation_pct={},
            default_depreciation_pct=pct if direction == DEPRECIATION else None,
            default_appreciation_pct=pct if direction == APPRECIATION else None,
            directions=(direction,),
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> FxShockSet:
        """Read the governed ``value_json`` body. Holds no value of its own."""
        if payload is None:
            raise MissingParameter(PARAM_FX_SHOCK)
        if not isinstance(payload, Mapping):
            raise MissingParameter(PARAM_FX_SHOCK, detail="malformed")
        tables: dict[str, dict[str, Decimal]] = {}
        defaults: dict[str, Decimal | None] = {}
        for direction in BOTH_DIRECTIONS:
            raw = payload.get(direction) or {}
            if not isinstance(raw, Mapping):
                raise MissingParameter(PARAM_FX_SHOCK, detail=direction)
            table: dict[str, Decimal] = {}
            default: Decimal | None = None
            for key, value in raw.items():
                try:
                    parsed = Decimal(str(value))
                except InvalidOperation as error:
                    raise MissingParameter(PARAM_FX_SHOCK, detail=f"{direction}:{key}") from error
                if key == "default":
                    default = parsed
                else:
                    table[str(key)] = parsed
            tables[direction] = table
            defaults[direction] = default
        return cls(
            depreciation_pct=tables[DEPRECIATION],
            appreciation_pct=tables[APPRECIATION],
            default_depreciation_pct=defaults[DEPRECIATION],
            default_appreciation_pct=defaults[APPRECIATION],
        )


@dataclass(frozen=True)
class RevaluationLoss:
    """The loss each direction produces, and which one is worse."""

    by_currency: Mapping[tuple[str, str], Decimal]
    depreciation_loss: Decimal | None
    appreciation_loss: Decimal | None
    worst_direction: str | None
    worst_loss: Decimal


def revaluation_losses(
    positions: Sequence[CurrencyPosition], shocks: FxShockSet
) -> RevaluationLoss:
    """Per-currency revaluation losses, with no cross-currency offset."""
    by_currency: dict[tuple[str, str], Decimal] = {}
    totals: dict[str, Decimal] = {}
    for direction in shocks.directions:
        total = ZERO
        for position in positions:
            shock = shocks.shock(position.currency, direction)
            change = position.net * shock / HUNDRED
            loss = max(ZERO, -change) if direction == DEPRECIATION else max(ZERO, change)
            by_currency[(position.currency, direction)] = amount(loss)
            total += loss
        totals[direction] = amount(total)

    depreciation = totals.get(DEPRECIATION)
    appreciation = totals.get(APPRECIATION)
    candidates = [(value, name) for name, value in totals.items()]
    if candidates:
        worst_loss, worst_direction = max(candidates)
    else:  # pragma: no cover - a shock set always names a direction
        worst_loss, worst_direction = ZERO, None
    return RevaluationLoss(
        by_currency=by_currency,
        depreciation_loss=depreciation,
        appreciation_loss=appreciation,
        worst_direction=worst_direction,
        worst_loss=worst_loss,
    )


@dataclass(frozen=True)
class FxAddOn:
    """The revaluation loss, the Pillar 1 charge, and the excess."""

    loss: RevaluationLoss
    pillar1_fx_capital: Decimal
    addon: Decimal


def fx_revaluation_addon(
    positions: Sequence[CurrencyPosition],
    shocks: FxShockSet,
    pillar1_fx_capital: Decimal,
) -> FxAddOn:
    """``max(0, revaluation loss − the Pillar 1 FX charge)``."""
    loss = revaluation_losses(positions, shocks)
    return FxAddOn(
        loss=loss,
        pillar1_fx_capital=amount(pillar1_fx_capital),
        addon=amount(max(ZERO, loss.worst_loss - pillar1_fx_capital)),
    )


def fx_nop_addon(
    *,
    positions: Sequence[CurrencyPosition],
    shocks: FxShockSet | None,
    market_rwa: Decimal | None,
    car_min_pct: Decimal | None,
    overlay_stressed_addon: Decimal | None = None,
) -> MethodResult:
    """The Pillar 2 FX method: governed shocks, net of the Pillar 1 charge."""
    if shocks is None:
        raise MissingParameter(PARAM_FX_SHOCK)
    if car_min_pct is None:
        raise MissingParameter(PARAM_CAR_MIN)

    uses = (
        ParameterUse(PARAM_FX_SHOCK, "revaluation shocks"),
        ParameterUse(PARAM_CAR_MIN, "pillar 1 fx charge"),
    )
    rwa = market_rwa if market_rwa is not None else ZERO
    pillar1 = pillar1_capital(rwa, car_min_pct)
    detail: dict[str, str | None] = {
        "market_rwa": text(market_rwa),
        "pillar1_fx_capital": text(pillar1),
    }

    try:
        result = fx_revaluation_addon(positions, shocks, pillar1)
    except MissingShockError as error:
        return MethodResult(
            method=METHOD,
            method_version="v1",
            status=MethodStatus.INCOMPLETE,
            baseline_derivation="not_applicable",
            stressed_derivation="not_applicable",
            detail=detail_of(detail),
            reasons=(f"fx_shock_missing:{error.currency}:{error.direction}",),
            parameters_used=uses,
        )

    detail["depreciation_loss"] = text(result.loss.depreciation_loss)
    detail["appreciation_loss"] = text(result.loss.appreciation_loss)
    detail["worst_direction"] = result.loss.worst_direction
    detail["revaluation_loss"] = text(result.loss.worst_loss)
    for (currency, direction), value in sorted(result.loss.by_currency.items()):
        detail[f"loss:{currency}:{direction}"] = text(value)

    if overlay_stressed_addon is None:
        stressed = result.addon
        stressed_derivation = "same_as_baseline"
    else:
        stressed = amount(max(result.addon, overlay_stressed_addon))
        stressed_derivation = "max_of_baseline_and_scenario"
    detail["overlay_stressed_addon"] = text(overlay_stressed_addon)

    return MethodResult(
        method=METHOD,
        method_version="v1",
        status=MethodStatus.COMPUTED,
        basis=Basis.ABSOLUTE,
        basis_value=result.addon,
        baseline_amount=result.addon,
        stressed_amount=stressed,
        baseline_derivation="method",
        stressed_derivation=stressed_derivation,
        scenario_definition={
            "name": "Reporting-currency revaluation shock",
            "directions": list(shocks.directions),
            "source": "governed_parameter",
            "offset": "none_across_currencies",
        },
        detail=detail_of(detail),
        parameters_used=uses,
    )


__all__ = [
    "APPRECIATION",
    "BOTH_DIRECTIONS",
    "DEPRECIATION",
    "METHOD",
    "PARAM_CAR_MIN",
    "PARAM_FX_SHOCK",
    "CurrencyPosition",
    "Direction",
    "FxAddOn",
    "FxShockSet",
    "MissingShockError",
    "RevaluationLoss",
    "fx_nop_addon",
    "fx_revaluation_addon",
    "revaluation_losses",
]
