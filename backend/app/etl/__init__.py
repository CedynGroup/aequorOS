"""AequorOS ML-ETL layer.

Sits between Layer 1 (source adapters) and Layer 2 (canonical model) in the Data Engine
(``data_engine.md`` §3). Cleans data where cleaning is audit-sanctioned, flags it where it
is not, and deduplicates entities across sources / time / within a source — producing
linkage records, never destroying source records.

The framework (contracts, guard, audit/lineage ports) plus the deterministic preprocessing
stages, the deduplication stages, and the pure :func:`run_etl` orchestrator are wired here.
See ``README.md`` for the integration map and build order.
"""

from __future__ import annotations

from app.etl.contracts import (
    REGULATORY_CRITICAL_FIELDS,
    AnomalyDetector,
    Deduplicator,
    Disposition,
    ETLAuditSink,
    ETLLineageSink,
    ETLOperation,
    ETLOperationType,
    ETLProvenance,
    ETLResult,
    ETLValidationError,
    LinkageRecord,
    MatchType,
    Preprocessor,
    guard_sanctioned,
)
from app.etl.pipeline import EtlConfig, etl_summary, run_etl
from app.etl.resolve import canonical_view, is_regulatory_critical, resolve_concept

__all__ = [
    "REGULATORY_CRITICAL_FIELDS",
    "AnomalyDetector",
    "Deduplicator",
    "Disposition",
    "ETLAuditSink",
    "ETLLineageSink",
    "ETLOperation",
    "ETLOperationType",
    "ETLProvenance",
    "ETLResult",
    "ETLValidationError",
    "EtlConfig",
    "LinkageRecord",
    "MatchType",
    "Preprocessor",
    "canonical_view",
    "etl_summary",
    "guard_sanctioned",
    "is_regulatory_critical",
    "resolve_concept",
    "run_etl",
]
