"""Pillar 2 quantification method vocabulary.

A framework JSON says which methods a risk component may be quantified with.
This module is the closed vocabulary those keys are checked against, so a typo
in the data cannot create a method nobody implements, and a method that is not
built yet is declared with the phase that builds it rather than silently
accepted.

The methods themselves live in ``app/domain/icaap/pillar2/`` from P2 onward.
Nothing here carries a coefficient, threshold or band: those are governed
parameters (D-024).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

MethodPhase = Literal["P2", "P5"]


@dataclass(frozen=True)
class MethodSpec:
    """One Pillar 2 quantification method."""

    key: str
    title: str
    phase: MethodPhase
    #: False only for ``not_capitalised``: the risk is assessed and managed but
    #: carries no Pillar 2 capital line (BoG treats liquidity risk that way).
    capitalising: bool


_METHODS: tuple[MethodSpec, ...] = (
    MethodSpec("not_capitalised", "Assessed, not capitalised", "P2", capitalising=False),
    MethodSpec("judgemental", "Board judgement with documented rationale", "P2", capitalising=True),
    MethodSpec(
        "benchmark_mapped",
        "Concentration metric mapped to a governed add-on band",
        "P2",
        capitalising=True,
    ),
    MethodSpec(
        "hhi_proportional_heuristic",
        "Proportional add-on from a concentration index (representative)",
        "P2",
        capitalising=True,
    ),
    MethodSpec(
        "granularity_adjustment",
        "Granularity adjustment on the single-risk-factor model",
        "P5",
        capitalising=True,
    ),
    MethodSpec(
        "sovereign_stress_addon",
        "Sovereign exposure haircut add-on",
        "P2",
        capitalising=True,
    ),
    MethodSpec(
        "irrbb_interim_delta_eve",
        "Interim IRRBB add-on from economic-value losses",
        "P2",
        capitalising=True,
    ),
    MethodSpec(
        "irrbb_standardised_framework",
        "IRRBB standardised framework",
        "P5",
        capitalising=True,
    ),
    MethodSpec(
        "fx_nop_addon",
        "Net open position shock add-on",
        "P2",
        capitalising=True,
    ),
    MethodSpec(
        "operational_scenario_net_p1",
        "Operational scenario losses net of the Pillar 1 charge",
        "P2",
        capitalising=True,
    ),
)

PILLAR2_METHODS: Mapping[str, MethodSpec] = MappingProxyType({m.key: m for m in _METHODS})
METHOD_KEYS: frozenset[str] = frozenset(PILLAR2_METHODS)
NOT_CAPITALISED = "not_capitalised"

__all__ = ["METHOD_KEYS", "NOT_CAPITALISED", "PILLAR2_METHODS", "MethodPhase", "MethodSpec"]
