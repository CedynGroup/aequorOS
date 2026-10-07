"""Forecasting's interface for other features: re-exports only, no logic."""

from app.forecasting.service import seed_starting_position

__all__ = ["seed_starting_position"]
