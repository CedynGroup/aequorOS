"""The AequorOS market research desk as an internal market data vendor."""

from app.adapters.market_data.aequor_desk.adapter import (
    ADAPTER_VERSION,
    VENDOR,
    AequorDeskAdapter,
    build_extraction,
    determination_scopes,
)

__all__ = [
    "ADAPTER_VERSION",
    "VENDOR",
    "AequorDeskAdapter",
    "build_extraction",
    "determination_scopes",
]
