"""BI's interface for other features: re-exports only, no logic."""

from app.bi.service.versions import BUILDER_VERSION

__all__ = ["BUILDER_VERSION"]
