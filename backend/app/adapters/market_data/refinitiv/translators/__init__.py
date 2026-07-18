"""Refinitiv translators (§7.3): typed intermediates -> canonical records.

Translators produce the vendor-agnostic record shapes the pull runner
persists (``CurveRecord``, ``FxRateRecord``, ``RatingRecord``), bundled with
human-readable sample values for the onboarding test step (§9.2 step 5).
``source_reference`` is always the RIC(s) the value came from (§13.2);
``source_system`` stamping happens in the pull runner.
"""

from app.adapters.market_data.refinitiv.translators.curve_to_canonical import (
    curve_to_bundle,
    percent_to_fraction,
    tenor_label,
)
from app.adapters.market_data.refinitiv.translators.fx_to_canonical import (
    fx_forward_to_bundle,
    fx_to_bundle,
)
from app.adapters.market_data.refinitiv.translators.macro_to_canonical import (
    macro_to_bundle,
)
from app.adapters.market_data.refinitiv.translators.rating_to_canonical import (
    ratings_to_bundle,
)

__all__ = [
    "curve_to_bundle",
    "fx_forward_to_bundle",
    "fx_to_bundle",
    "macro_to_bundle",
    "percent_to_fraction",
    "ratings_to_bundle",
    "tenor_label",
]
