"""Macro-forecast translation: Bloomberg values -> canonical market_index (§6.4).

Each :class:`MacroObservation` becomes one canonical ``market_index`` record
(via :class:`IndexRecord`), tagged with the scenario and horizon the forecast
carries (§5.2). The ``index_code`` is derived from the scope
(``MACRO_GHANA_GDP_FORECAST`` -> ``GHANA_GDP_FORECAST``) so the canonical code
stays vendor-agnostic; ``source_reference`` names the specific Bloomberg
security/field. Macro values pass through untouched — ``market_index`` stores
the value in its native unit (a percent growth/inflation/rate or an index
level), and no calculation module reinterprets it here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.market_data.pull_runner import IndexRecord, MarketDataBundle

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.adapters.market_data.bloomberg.extractors.macro_series import MacroObservation
    from app.adapters.market_data.scope_taxonomy import DataScope

_MACRO_PREFIX = "MACRO_"


def _horizon_label(horizon_months: int | None) -> str:
    if horizon_months is None:
        return "point"
    if horizon_months % 12 == 0:
        return f"{horizon_months // 12}Y"
    return f"{horizon_months}M"


def macro_bundle(scope: DataScope, observations: Sequence[MacroObservation]) -> MarketDataBundle:
    """One macro-forecast scope's observations as a persistable bundle."""
    index_code = scope.value.removeprefix(_MACRO_PREFIX)
    records = []
    samples: dict[str, str] = {}
    for observation in observations:
        records.append(
            IndexRecord(
                index_code=index_code,
                value=observation.value,
                scenario=observation.scenario,
                horizon_months=observation.horizon_months,
                source_reference=f"{observation.security}/{observation.field}",
            )
        )
        label = (
            f"{index_code} ({observation.scenario}, {_horizon_label(observation.horizon_months)})"
        )
        samples[label] = f"{observation.value}"
    return MarketDataBundle(indices=records, sample_values=samples)
