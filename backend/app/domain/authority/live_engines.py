"""The one table of live calculation engines.

Every surface that keys off a live engine — the live read gate
(``live_access.FRESHNESS_VIEW_MODULES``), the regime scoping authority
(``module_scope.MODULE_SCOPE_KEY``), the BI catalogue's engine vocabulary
(``bi.domain.authority``), the interactive mint gate
(``live_view.MINT_RUN_ENGINES``) and the official data-activation subset
(``data_activation.OFFICIAL_MODULES``) — derives its map from the single tuple
below, so the surfaces cannot disagree about which engine answers to which
authorization module, entitlement slug and metric family.

The package stays pure: the module column carries the authorization ``Module``
enum VALUE (``app.core.authorization``), restated as a closed literal
vocabulary because the domain may not import ``app.core``. Consumers resolve
it through ``Module(...)``, which raises on an unknown value at import time —
a typo in this table fails loudly, never silently.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from app.domain.authority.registry import MetricFamily

#: The authorization ``Module`` enum values (``app.core.authorization``) this
#: table may name. Extending it is a CHECK-widening, migration-backed event.
AuthorizationModuleValue = Literal["liq", "cap", "credit", "irrbb", "fx", "ftp", "markets", "fcst"]


class LiveEngine(NamedTuple):
    """One live calculation engine and the authority that governs it."""

    #: The live-plane module key (``app.live.models.live.LIVE_MODULES``).
    engine: str
    #: The authorization ``Module`` enum value whose scoped bindings govern it.
    module: AuthorizationModuleValue
    #: The ``institution_types.default_modules`` slug that entitles it.
    entitlement_slug: str
    #: The metric family whose class authority the engine needs, when its
    #: outputs are authoritative only for a declared institution class.
    metric_family: MetricFamily | None


#: The canonical engine order: the live vocabulary order
#: (``app.live.models.live.LIVE_MODULES``, the DB CHECK vocabulary).
LIVE_ENGINES: tuple[LiveEngine, ...] = (
    LiveEngine("liquidity", "liq", "liquidity", None),
    LiveEngine("capital", "cap", "capital", None),
    LiveEngine("credit", "credit", "credit", MetricFamily.CREDIT),
    LiveEngine("irr", "irrbb", "irrbb", None),
    LiveEngine("fx", "fx", "fx", None),
    LiveEngine("ftp", "ftp", "ftp", None),
    LiveEngine("rating", "markets", "markets", None),
    LiveEngine("forecast", "fcst", "forecasting", MetricFamily.FORECAST),
)


#: ``implied_rating`` is the official-tier key for the Markets implied-rating
#: path: the same Markets entitlement the live ``rating`` engine carries, under
#: the name the official run scope (``data_activation``) runs it by.
ENTITLEMENT_SLUG_ALIASES: dict[str, str] = {"implied_rating": "markets"}
