"""Forecasting's interface for other features: re-exports only, no logic."""

from app.forecasting.service import regulatory_forecasting

__all__ = ["regulatory_forecasting"]
