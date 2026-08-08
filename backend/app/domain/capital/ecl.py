"""IFRS 9 expected-credit-loss engine (product.md §Phase 2 item 8).

Pure PD x LGD x EAD computation over staged exposures with forward-looking
scenario conditioning: ECL is the probability-weighted average across the
configured macro scenarios (IFRS 9 ¶5.5.17 requires an unbiased
probability-weighted amount), where each scenario scales the through-the-
cycle PD/LGD assumptions. Stage 3 exposures are credit-impaired — PD is 100%
by definition regardless of the assumption row.

Assumptions resolve per (segment, stage) with an ``ALL`` segment fallback;
an exposure whose segment+stage has no assumption is reported as uncovered
rather than silently priced at zero — the caller decides whether that blocks
the run or falls back to ingested provisions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

ALL_SEGMENTS = "ALL"
STAGE_3 = 3

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_MONEY_Q = Decimal("0.0001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q)


@dataclass(frozen=True)
class EclExposure:
    """One staged exposure bucket (a loan family at one IFRS 9 stage)."""

    segment: str
    stage: int
    ead: Decimal


@dataclass(frozen=True)
class EclAssumption:
    """Board-approved PD/LGD for one segment+stage (12m PD for stage 1,
    lifetime PD for stage 2; stage 3 rows only contribute their LGD)."""

    segment: str
    stage: int
    pd_pct: Decimal
    lgd_pct: Decimal


@dataclass(frozen=True)
class EclScenario:
    """One forward-looking macro scenario: weight + PD/LGD conditioning."""

    code: str
    weight_pct: Decimal
    pd_multiplier: Decimal = Decimal("1")
    lgd_multiplier: Decimal = Decimal("1")


BASE_SCENARIO = EclScenario(code="base", weight_pct=Decimal("100"))


@dataclass(frozen=True)
class EclItem:
    segment: str
    stage: int
    ead: Decimal
    pd_pct: Decimal
    lgd_pct: Decimal
    ecl: Decimal


@dataclass(frozen=True)
class EclResult:
    items: tuple[EclItem, ...]
    stage_totals: Mapping[int, Decimal]
    #: Stages 1+2 — the general (performing-book) allowance that competes for
    #: the Tier 2 general-provisions slot.
    general_ecl: Decimal
    #: Stage 3 — specific allowances against credit-impaired exposures.
    specific_ecl: Decimal
    total_ecl: Decimal
    #: (segment, stage) pairs with EAD but no assumption row — never priced.
    uncovered: tuple[tuple[str, int], ...] = field(default=())


class EclComputationError(Exception):
    pass


def _resolve(
    assumptions: Mapping[tuple[str, int], EclAssumption], segment: str, stage: int
) -> EclAssumption | None:
    return assumptions.get((segment, stage)) or assumptions.get((ALL_SEGMENTS, stage))


def _capped_pct(value: Decimal) -> Decimal:
    return min(max(value, _ZERO), _HUNDRED)


def compute_ecl(
    exposures: Sequence[EclExposure],
    assumptions: Sequence[EclAssumption],
    scenarios: Sequence[EclScenario] = (BASE_SCENARIO,),
) -> EclResult:
    """Probability-weighted PD x LGD x EAD across the conditioning scenarios."""
    weight_total = sum((scenario.weight_pct for scenario in scenarios), _ZERO)
    if weight_total != _HUNDRED:
        raise EclComputationError(
            f"Scenario weights must sum to 100% (got {weight_total}%)."
        )
    lookup = {(row.segment, row.stage): row for row in assumptions}
    items: list[EclItem] = []
    uncovered: list[tuple[str, int]] = []
    stage_totals: dict[int, Decimal] = {}
    for exposure in exposures:
        assumption = _resolve(lookup, exposure.segment, exposure.stage)
        if assumption is None:
            uncovered.append((exposure.segment, exposure.stage))
            continue
        weighted = _ZERO
        weighted_pd = _ZERO
        weighted_lgd = _ZERO
        for scenario in scenarios:
            pd_pct = (
                _HUNDRED
                if exposure.stage == STAGE_3
                else _capped_pct(assumption.pd_pct * scenario.pd_multiplier)
            )
            lgd_pct = _capped_pct(assumption.lgd_pct * scenario.lgd_multiplier)
            weight = scenario.weight_pct / _HUNDRED
            weighted += exposure.ead * pd_pct / _HUNDRED * lgd_pct / _HUNDRED * weight
            weighted_pd += pd_pct * weight
            weighted_lgd += lgd_pct * weight
        ecl = _money(weighted)
        items.append(
            EclItem(
                segment=exposure.segment,
                stage=exposure.stage,
                ead=_money(exposure.ead),
                pd_pct=weighted_pd.quantize(Decimal("0.000001")),
                lgd_pct=weighted_lgd.quantize(Decimal("0.000001")),
                ecl=ecl,
            )
        )
        stage_totals[exposure.stage] = _money(stage_totals.get(exposure.stage, _ZERO) + ecl)
    general = _money(stage_totals.get(1, _ZERO) + stage_totals.get(2, _ZERO))
    specific = stage_totals.get(STAGE_3, _ZERO)
    return EclResult(
        items=tuple(items),
        stage_totals=stage_totals,
        general_ecl=general,
        specific_ecl=specific,
        total_ecl=_money(general + specific),
        uncovered=tuple(sorted(set(uncovered))),
    )
