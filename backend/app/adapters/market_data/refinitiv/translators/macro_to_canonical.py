"""Macro-forecast translation: RDP values -> canonical market_index records (§7.3).

Each :class:`MacroObservation` becomes one canonical ``market_index`` record
(via :class:`IndexRecord`), tagged with its scenario and horizon (§5.2). The
``index_code`` is derived from the scope (``MACRO_GHANA_INFLATION_FORECAST``
-> ``GHANA_INFLATION_FORECAST``) so the canonical code stays vendor-agnostic;
``source_reference`` is the RIC the value came from. Macro values pass through
in their native unit — ``market_index`` does not reinterpret them.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.adapters.market_data.pull_runner import IndexRecord, MarketDataBundle
from app.adapters.market_data.refinitiv.extractors.macro_series import MacroObservation
from app.adapters.market_data.scope_taxonomy import DataScope

_MACRO_PREFIX = "MACRO_"


def _horizon_label(horizon_months: int | None) -> str:
    if horizon_months is None:
        return "point"
    if horizon_months % 12 == 0:
        return f"{horizon_months // 12}Y"
    return f"{horizon_months}M"


def macro_to_bundle(scope: DataScope, observations: Sequence[MacroObservation]) -> MarketDataBundle:
    """Translate one macro-forecast scope's observations into a bundle."""
    index_code = scope.value.removeprefix(_MACRO_PREFIX)
    bundle = MarketDataBundle()
    for observation in observations:
        bundle.indices.append(
            IndexRecord(
                index_code=index_code,
                value=observation.value,
                scenario=observation.scenario,
                horizon_months=observation.horizon_months,
                source_reference=observation.ric,
            )
        )
        label = (
            f"{index_code} ({observation.scenario}, {_horizon_label(observation.horizon_months)})"
        )
        bundle.sample_values[label] = f"{observation.value}"
    return bundle
