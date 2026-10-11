"""Notifications's interface for other features: re-exports only, no logic."""

from app.services import notifications

__all__ = ["notifications"]
