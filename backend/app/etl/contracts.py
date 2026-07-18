"""ML-ETL layer contracts (framework only — no models).

The ML-ETL layer sits between Layer 1 (adapters) and Layer 2 (canonical model) in
the Data Engine's six-layer architecture (``data_engine.md`` §3). It operates on the
adapter's post-``extract`` output (``ExtractionResult``) — a source-agnostic shape —
and never reaches into source-system semantics, so ``data_engine.md`` §2.1 ("adapters
know sources, nothing else does") holds (see ``questions/Q05``).

Every ML-ETL operation produces, per the build brief:
  * the transformed value (or a linkage record) — or, for non-sanctioned cases, a flag,
  * a confidence score,
  * a lineage entry (``data_engine.md`` §8.2), via :class:`ETLLineageSink`,
  * an audit-log entry (``storage.md`` §9), via :class:`ETLAuditSink`.

Discipline enforced here (``data_engine.md`` §12.5 / §7.4):
  * regulatory-critical values are never silently modified — only flagged
    (:data:`REGULATORY_CRITICAL_FIELDS`, :func:`guard_sanctioned`);
  * every transform is reversible via lineage (the original is always retained);
  * every ML output carries confidence and model version and is human-overridable.

This module defines only contracts (dataclasses + ABC ports/stages). Concrete
normalizers, matchers, detectors, models, the ``ETLPipeline`` orchestrator, and the
audit/lineage sink implementations live in sibling modules and are added in later steps.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # avoid runtime coupling; these are the adapter's neutral shapes
    from app.domain.ingestion.contracts import ExtractionResult, RawRecord


class ETLOperationType(StrEnum):
    """The kind of ML-ETL operation, for lineage ``operation_type`` tagging."""

    NORMALIZE = "NORMALIZE"  # ISO codes, case/whitespace, unicode
    TYPE_COERCE = "TYPE_COERCE"  # "15.5%"->0.155, "N/A"->null, Excel serial dates
    REFERENCE_RESOLVE = "REFERENCE_RESOLVE"  # bank product code -> canonical product id
    DEDUP_LINK = "DEDUP_LINK"  # cross-source / cross-time / within-source linkage
    ANOMALY_FLAG = "ANOMALY_FLAG"  # isolation-forest fingerprint anomaly


class Disposition(StrEnum):
    """Whether an operation transformed the data or only flagged it.

    Sanctioned operations may rewrite the value (original retained in lineage).
    Flagged operations must never modify the value (``data_engine.md`` §12.5).
    """

    SANCTIONED = "SANCTIONED"
    FLAGGED = "FLAGGED"


class MatchType(StrEnum):
    """Deduplication linkage kind (build brief §4, Deduplication)."""

    CROSS_SOURCE = "CROSS_SOURCE"  # same entity across T24 + upload + LOS
    CROSS_TIME = "CROSS_TIME"  # same position across snapshots (legitimate vs bug)
    WITHIN_SOURCE = "WITHIN_SOURCE"  # near-duplicate inside one extract


# Fields whose values feed regulatory calculations directly. A sanctioned transform
# MUST NOT rewrite these — data quality issues on them are flagged for a human, never
# silently corrected (build brief: "Non-sanctioned operations" + data_engine.md §12.5).
REGULATORY_CRITICAL_FIELDS: frozenset[str] = frozenset(
    {
        "balance",
        "notional",
        "outstanding_amount",
        "principal_amount",
        "interest_rate",
        "rate_spread",
        "counterparty_id",
        "counterparty_reference",
        "product_id",
        "regulatory_category",
        "currency",
        "ifrs9_stage",
        "risk_weight",
        "gl_balance",
        "capital_amount",
    }
)


class ETLValidationError(Exception):
    """A sanctioned transform attempted a non-sanctioned modification."""


@dataclass(frozen=True)
class ETLProvenance:
    """Provenance stamped on every ML-ETL operation (``data_engine.md`` §7.3).

    ``model_id``/``model_version`` are ``None`` for deterministic rule-based ops
    (sanctioned normalizers), populated for ML-backed ops (matcher, anomaly model).
    """

    operation_type: ETLOperationType
    operation_ref: str  # e.g. "iso4217_normalizer/v1" or "counterparty_matcher/v0.1"
    model_id: str | None = None
    model_version: str | None = None
    confidence: float | None = None  # [0,1]; None only for exact-deterministic ops
    as_of: datetime | None = None


@dataclass(frozen=True)
class ETLOperation:
    """A single ML-ETL transformation (or flag) with full auditability.

    For ``Disposition.SANCTIONED``: ``before`` != ``after`` and the original is
    recoverable from ``before`` (lineage). For ``Disposition.FLAGGED``:
    ``after is None`` and ``reason`` explains why a human must decide.
    """

    record_id: str  # source_reference of the affected record
    field_name: str
    disposition: Disposition
    before: Any
    after: Any | None  # None when FLAGGED (value left untouched)
    provenance: ETLProvenance
    lineage_input_ids: tuple[str, ...] = ()
    reason: str | None = None  # required for FLAGGED

    def __post_init__(self) -> None:
        if self.disposition is Disposition.SANCTIONED:
            guard_sanctioned(self.field_name)
        elif self.after is not None:  # FLAGGED must not modify
            msg = f"FLAGGED op on {self.field_name!r} must leave value untouched (after=None)."
            raise ETLValidationError(msg)


@dataclass(frozen=True)
class LinkageRecord:
    """A deduplication linkage — a winner + the source records it subsumes.

    Regulatory data is never destroyed on dedup (build brief): source records are
    preserved; this record links them and names the canonical winner, with the
    combined multi-signal confidence and the per-signal scores that produced it.
    """

    match_type: MatchType
    canonical_winner_id: str
    linked_source_ids: tuple[str, ...]
    signals: dict[str, float]  # e.g. {"jaro_winkler": 0.94, "metaphone": 1.0, "nid": 1.0}
    combined_confidence: float
    provenance: ETLProvenance
    auto_confirmed: bool = False  # True only above the auto-link threshold; else human review


@dataclass
class ETLResult:
    """Output of a full ML-ETL pass over one extraction batch.

    ``cleaned`` is the (normalized, coerced, reference-resolved) extraction handed to
    ``adapter.translate``. ``operations``/``linkages``/``flags`` are the audit trail;
    every one is also emitted to the lineage + audit sinks by the pipeline.
    """

    cleaned: ExtractionResult
    operations: list[ETLOperation] = field(default_factory=list)
    linkages: list[LinkageRecord] = field(default_factory=list)
    flags: list[ETLOperation] = field(default_factory=list)  # Disposition.FLAGGED subset

    @property
    def sanctioned_count(self) -> int:
        return sum(1 for op in self.operations if op.disposition is Disposition.SANCTIONED)


def guard_sanctioned(field_name: str) -> None:
    """Raise if ``field_name`` is regulatory-critical and thus not sanctioned to rewrite."""
    if field_name in REGULATORY_CRITICAL_FIELDS:
        msg = (
            f"{field_name!r} is regulatory-critical; ML-ETL may FLAG it but must never "
            f"silently modify it (data_engine.md §12.5)."
        )
        raise ETLValidationError(msg)


# --- Ports: audit + lineage sinks (concrete impls bind to services/audit.py +
#     models/ingestion.py::LineageRecord in the next step) -----------------------------


class ETLAuditSink(Protocol):
    """Records every ML-ETL operation to the append-only audit log (``storage.md`` §9)."""

    def record(self, operation: ETLOperation | LinkageRecord) -> None: ...


class ETLLineageSink(Protocol):
    """Writes a lineage node per operation into the lineage graph (``data_engine.md`` §8.2)."""

    def emit(
        self,
        operation: ETLOperation | LinkageRecord,
        *,
        batch_id: str,
    ) -> str:  # returns the new lineage node id
        ...


# --- Stage contracts (concrete stages implemented in preprocessing/ and deduplication/) --


class Preprocessor(abc.ABC):
    """A sanctioned, per-field cleaner (normalizer / type-coercer / reference-resolver)."""

    operation_type: ETLOperationType

    @abc.abstractmethod
    def apply(self, record: RawRecord) -> list[ETLOperation]:
        """Return the operations this preprocessor performs on one raw record.

        Sanctioned rewrites go in as ``Disposition.SANCTIONED``; anything touching a
        regulatory-critical field that looks wrong is emitted ``Disposition.FLAGGED``.
        """


class Deduplicator(abc.ABC):
    """Produces linkage records across/within sources and time (never destroys records)."""

    match_type: MatchType

    @abc.abstractmethod
    def link(self, records: list[RawRecord]) -> list[LinkageRecord]: ...


class AnomalyDetector(abc.ABC):
    """Flags illegitimate near-duplicates / outliers via record-fingerprint anomaly scoring."""

    @abc.abstractmethod
    def score(self, records: list[RawRecord]) -> list[ETLOperation]: ...
