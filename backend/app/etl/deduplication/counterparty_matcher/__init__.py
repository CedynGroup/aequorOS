"""Cross-source counterparty deduplicator (multi-signal + RandomForest)."""

from __future__ import annotations

from app.etl.deduplication.counterparty_matcher.matcher import CounterpartyMatcher
from app.etl.deduplication.counterparty_matcher.signals import blocking_key, compute_signals

__all__ = ["CounterpartyMatcher", "blocking_key", "compute_signals"]
