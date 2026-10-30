"""Notifications's interface for other features: re-exports only, no logic."""

from app.notifications.service import notifications

__all__ = ["notifications"]
