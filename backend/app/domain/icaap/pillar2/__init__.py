"""Pillar 2 quantification methods (pure).

Each module here is one method: bank figures and RESOLVED governed parameters
in, a :class:`~app.domain.icaap.pillar2.types.MethodResult` out. No module
reads a database, a session or a request, and no module holds a regulatory
number — a value that is not passed in is a typed
:class:`~app.domain.icaap.pillar2.types.MissingParameter`, which the service
turns into ``missing_parameter`` naming the console row to fill (D-024).

``METHOD_REGISTRY`` says what each method needs before it is worth calling: the
blocks that must be bound, whether it is scenario-based, and whether it can run
from bound engine output at all. A consolidated cycle cannot, because the
engines produce solo figures (D-018), so it uses ``manual_with_evidence``.

The P5 methods are declared here with ``phase="P5"`` and no implementation, so
a framework may name them and the service can answer "not available yet"
precisely, rather than the key silently meaning nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.icaap.methods import PILLAR2_METHODS
from app.domain.icaap.pillar2.types import (
    InputMode,
    MethodResult,
    MethodSpec,
    MethodStatus,
    MissingParameter,
    ParameterUse,
)

BOUND_BLOCKS: InputMode = "bound_blocks"
MANUAL: InputMode = "manual_with_evidence"
BOTH_MODES: tuple[InputMode, ...] = (BOUND_BLOCKS, MANUAL)

_SPECS: tuple[tuple[str, bool, tuple[InputMode, ...], tuple[str, ...]], ...] = (
    ("not_capitalised", False, BOTH_MODES, ("ilaap",)),
    ("judgemental", False, (MANUAL,), ()),
    ("benchmark_mapped", False, BOTH_MODES, ("concentration", "pillar1_rwa")),
    ("hhi_proportional_heuristic", False, BOTH_MODES, ("concentration", "pillar1_rwa")),
    ("granularity_adjustment", False, BOTH_MODES, ("concentration", "pillar1_rwa")),
    ("sovereign_stress_addon", True, BOTH_MODES, ("sovereign_exposures", "pillar1_rwa")),
    ("irrbb_interim_delta_eve", True, BOTH_MODES, ("irrbb", "capital_position")),
    ("irrbb_standardised_framework", True, BOTH_MODES, ("irrbb_sf", "capital_position")),
    ("fx_nop_addon", True, BOTH_MODES, ("fx_position", "pillar1_rwa")),
    ("operational_scenario_net_p1", True, BOTH_MODES, ("pillar1_rwa", "financials")),
)

METHOD_REGISTRY: Mapping[str, MethodSpec] = MappingProxyType(
    {
        key: MethodSpec(
            key=key,
            label=PILLAR2_METHODS[key].title,
            phase=PILLAR2_METHODS[key].phase,
            scenario_based=scenario_based,
            supported_input_modes=modes,
            requires_blocks=blocks,
        )
        for key, scenario_based, modes, blocks in _SPECS
    }
)

#: P5 methods whose implementation has LANDED. ``phase`` says which phase builds
#: a method and never changes; this says whether the thing it names exists yet,
#: and the two are deliberately separate — a method stays phase P5 for the rest
#: of its life, and a reader asking "can a bank select this?" must not have to
#: infer the answer from a build-plan label.
IMPLEMENTED_P5_METHODS: frozenset[str] = frozenset(
    {"granularity_adjustment", "irrbb_standardised_framework"}
)

#: Declared, but not built — the service answers ``method_not_available``.
DEFERRED_METHODS: frozenset[str] = frozenset(
    key
    for key, spec in METHOD_REGISTRY.items()
    if spec.phase == "P5" and key not in IMPLEMENTED_P5_METHODS
)

__all__ = [
    "BOTH_MODES",
    "BOUND_BLOCKS",
    "DEFERRED_METHODS",
    "IMPLEMENTED_P5_METHODS",
    "MANUAL",
    "METHOD_REGISTRY",
    "InputMode",
    "MethodResult",
    "MethodSpec",
    "MethodStatus",
    "MissingParameter",
    "ParameterUse",
]
