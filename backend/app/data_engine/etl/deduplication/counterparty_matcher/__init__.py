"""Cross-source counterparty deduplicator (multi-signal + RandomForest)."""

from __future__ import annotations

from app.data_engine.etl.deduplication.counterparty_matcher.matcher import CounterpartyMatcher
from app.data_engine.etl.deduplication.counterparty_matcher.signals import (
    blocking_key,
    compute_signals,
)

__all__ = ["CounterpartyMatcher", "blocking_key", "compute_signals"]
