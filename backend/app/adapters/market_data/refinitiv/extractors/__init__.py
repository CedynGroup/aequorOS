"""Refinitiv extractors (§7.3): raw RDP responses -> typed intermediates.

One module per supported scope category (§7.2 catalog): yield curves, FX,
and credit ratings. Extractors take a transport + session token + the
scope's catalog request specs, fetch the raw RDP-shaped payload, and parse
it into typed observation structures. They do not translate to canonical
(translators do) and do not persist (the pull runner does).
"""

from app.adapters.market_data.refinitiv.extractors.credit_data import (
    RatingObservation,
    extract_ratings,
)
from app.adapters.market_data.refinitiv.extractors.curves import (
    CurveObservation,
    extract_curve,
)
from app.adapters.market_data.refinitiv.extractors.fx import FxObservation, extract_fx

__all__ = [
    "CurveObservation",
    "FxObservation",
    "RatingObservation",
    "extract_curve",
    "extract_fx",
    "extract_ratings",
]
