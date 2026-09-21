"""Sovereign add-on: a restructuring haircut on government exposures.

Pillar 1 risk-weights domestic-currency sovereign exposure at zero. That is a
regulatory convention, not a statement that the exposure is riskless, and the
stress guideline's own appendix names sovereign restructuring as a scenario a
bank must consider. A bank whose securities portfolio is a multiple of its
capital therefore has a real exposure with no Pillar 1 charge behind it — which
is precisely the gap Pillar 2 exists to close (audit M2).

The haircut grid is governed: by currency kind (reporting vs foreign) and tenor
bucket, in the console, REPRESENTATIVE until confirmed. Nothing here knows a
percentage.

Unclassified holdings are filled CONSERVATIVELY — an unknown currency takes the
worst currency for that tenor, an unknown tenor the worst tenor for that
currency, both unknown the worst cell in the grid — and the result says a fill
was applied, so the bank is told that entering the grid precisely would lower
the charge rather than being quietly over- or under-charged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.icaap.pillar2.types import (
    MethodResult,
    MethodStatus,
    MissingParameter,
    ParameterUse,
    detail_of,
    text,
)
from app.domain.icaap.units import HUNDRED, ZERO, Basis, amount, pillar1_capital

PARAM_HAIRCUTS = "sov_p2_haircut_pct"
PARAM_CAR_MIN = "car_min"
METHOD = "sovereign_stress_addon"

CURRENCY_REPORTING = "reporting"
CURRENCY_FOREIGN = "foreign"
CURRENCY_UNKNOWN = "unknown"
TENOR_UNKNOWN = "unknown"

FILL_EXACT = "exact"
FILL_CURRENCY_UNKNOWN = "currency_unknown"
FILL_TENOR_UNKNOWN = "tenor_unknown"
FILL_BOTH_UNKNOWN = "both_unknown"
FILL_BUCKET_NOT_IN_GRID = "bucket_not_in_grid"

HaircutGrid = Mapping[tuple[str, str], Decimal]


@dataclass(frozen=True)
class SovereignHolding:
    """One sovereign holding, with the Pillar 1 RWA it already carries."""

    key: str
    currency_kind: str
    tenor_bucket: str | None
    exposure: Decimal
    pillar1_rwa: Decimal = ZERO


#: The key the governed body files the grid itself under. The rest of the body
#: (``schema``, ``tenor_buckets``, ``currency_kinds``) is the console's own
#: declaration, which ``app/domain/policy/parameter_shapes._haircut_grid``
#: validates the grid against; this parser reads the grid, not the declaration.
BODY_GRID_KEY = "haircut_pct"


def parse_haircut_grid(payload: Mapping[str, Any] | None) -> dict[tuple[str, str], Decimal]:
    """Read the governed BODY and return ``{(currency_kind, tenor): pct}``.

    The argument is the whole ``value_json`` the console stores — the same
    object the shape validator accepted — and the grid is read out of its
    ``haircut_pct`` key. Passing the inner map instead used to look like it
    worked and did not: every shipped body also carries ``schema``, a string,
    and the loop below convicted it as a malformed currency kind, so the
    seeded, valid, operator-filled row surfaced to the bank as
    ``missing_parameter: sov_p2_haircut_pct`` (audit W2). Only the body is
    accepted now, so that mismatch cannot be re-introduced silently.
    """
    if payload is None:
        raise MissingParameter(PARAM_HAIRCUTS)
    if not isinstance(payload, Mapping):
        raise MissingParameter(PARAM_HAIRCUTS, detail="malformed")
    table = payload.get(BODY_GRID_KEY)
    if not isinstance(table, Mapping):
        raise MissingParameter(PARAM_HAIRCUTS, detail=BODY_GRID_KEY)
    grid: dict[tuple[str, str], Decimal] = {}
    for currency_kind, buckets in table.items():
        if not isinstance(buckets, Mapping):
            raise MissingParameter(PARAM_HAIRCUTS, detail=str(currency_kind))
        for tenor, value in buckets.items():
            try:
                grid[(str(currency_kind), str(tenor))] = Decimal(str(value))
            except InvalidOperation as error:
                raise MissingParameter(PARAM_HAIRCUTS, detail=f"{currency_kind}:{tenor}") from error
    if not grid:
        raise MissingParameter(PARAM_HAIRCUTS, detail="empty")
    return grid


def resolve_haircut(  # noqa: PLR0911 - one exit per fill rule, which is the rule set
    grid: HaircutGrid, currency_kind: str, tenor_bucket: str | None
) -> tuple[Decimal, str]:
    """The haircut for a holding, filling unknowns with the worst cell."""
    tenor = tenor_bucket or TENOR_UNKNOWN
    known_currency = currency_kind in {CURRENCY_REPORTING, CURRENCY_FOREIGN}
    known_tenor = tenor != TENOR_UNKNOWN

    if known_currency and known_tenor:
        exact = grid.get((currency_kind, tenor))
        if exact is not None:
            return exact, FILL_EXACT
        return max(grid.values()), FILL_BUCKET_NOT_IN_GRID
    if known_tenor:
        candidates = [value for (_kind, bucket), value in grid.items() if bucket == tenor]
        if candidates:
            return max(candidates), FILL_CURRENCY_UNKNOWN
        return max(grid.values()), FILL_BUCKET_NOT_IN_GRID
    if known_currency:
        candidates = [value for (kind, _bucket), value in grid.items() if kind == currency_kind]
        if candidates:
            return max(candidates), FILL_TENOR_UNKNOWN
        return max(grid.values()), FILL_BUCKET_NOT_IN_GRID
    return max(grid.values()), FILL_BOTH_UNKNOWN


def sovereign_stress_addon(
    *,
    holdings: Sequence[SovereignHolding],
    grid: HaircutGrid | None,
    car_min_pct: Decimal | None,
    grid_param_id: str | None = None,
) -> MethodResult:
    """``max(0, Σ exposure × haircut − the Pillar 1 charge on those holdings)``."""
    if grid is None or not grid:
        raise MissingParameter(PARAM_HAIRCUTS)
    if car_min_pct is None:
        raise MissingParameter(PARAM_CAR_MIN)

    uses = (
        ParameterUse(PARAM_HAIRCUTS, "restructuring haircut"),
        ParameterUse(PARAM_CAR_MIN, "pillar 1 sovereign charge"),
    )
    detail: dict[str, str | None] = {}
    reasons: list[str] = []
    loss = ZERO
    sovereign_rwa = ZERO
    fills: set[str] = set()

    for holding in holdings:
        if holding.exposure <= ZERO:
            continue
        haircut, fill = resolve_haircut(grid, holding.currency_kind, holding.tenor_bucket)
        fills.add(fill)
        holding_loss = amount(holding.exposure * haircut / HUNDRED)
        loss += holding_loss
        sovereign_rwa += holding.pillar1_rwa
        detail[f"exposure:{holding.key}"] = text(holding.exposure)
        detail[f"haircut_pct:{holding.key}"] = text(haircut)
        detail[f"fill:{holding.key}"] = fill
        detail[f"loss:{holding.key}"] = text(holding_loss)

    conservative = bool(fills - {FILL_EXACT})
    if conservative:
        reasons.append("conservative_fill")

    if not detail:
        return MethodResult(
            method=METHOD,
            method_version="v1",
            status=MethodStatus.NOT_COMPUTABLE,
            baseline_derivation="not_applicable",
            stressed_derivation="not_applicable",
            detail=detail_of({"conservative_fill": "false"}),
            reasons=("no_sovereign_exposure",),
            parameters_used=uses,
        )

    pillar1 = pillar1_capital(sovereign_rwa, car_min_pct)
    baseline = amount(max(ZERO, loss - pillar1))
    detail["restructuring_loss"] = text(amount(loss))
    detail["pillar1_sovereign_capital"] = text(pillar1)
    detail["conservative_fill"] = str(conservative).lower()

    return MethodResult(
        method=METHOD,
        method_version="v1",
        status=MethodStatus.COMPUTED,
        basis=Basis.ABSOLUTE,
        basis_value=baseline,
        baseline_amount=baseline,
        stressed_amount=baseline,
        baseline_derivation="method",
        # The haircut IS the severe scenario, so the stressed figure is the
        # same figure — declared, not silently copied.
        stressed_derivation="same_as_baseline",
        scenario_definition={
            "name": "Sovereign restructuring haircut",
            "grid_param_id": grid_param_id,
            "fill": sorted(fills),
        },
        detail=detail_of(detail),
        reasons=tuple(reasons),
        parameters_used=uses,
    )


__all__ = [
    "BODY_GRID_KEY",
    "CURRENCY_FOREIGN",
    "CURRENCY_REPORTING",
    "CURRENCY_UNKNOWN",
    "FILL_BOTH_UNKNOWN",
    "FILL_BUCKET_NOT_IN_GRID",
    "FILL_CURRENCY_UNKNOWN",
    "FILL_EXACT",
    "FILL_TENOR_UNKNOWN",
    "METHOD",
    "PARAM_CAR_MIN",
    "PARAM_HAIRCUTS",
    "TENOR_UNKNOWN",
    "HaircutGrid",
    "SovereignHolding",
    "parse_haircut_grid",
    "resolve_haircut",
    "sovereign_stress_addon",
]
