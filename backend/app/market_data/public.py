"""Market data's interface for other features: re-exports only, no logic."""

from app.services.market_data_sources import preferred_fx_spot

__all__ = ["preferred_fx_spot"]
