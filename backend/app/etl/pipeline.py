"""ML-ETL orchestrator: ``run_etl`` — resolve, preprocess, deduplicate.

This is the single entry point the ingestion layer calls between ``adapter.extract`` and
``adapter.translate`` (see ``README.md``). It is a **pure function**: no database, no
side effects, no I/O. It consumes an :class:`ExtractionResult` plus the institution
:class:`MappingConfig` and returns an :class:`ETLResult` — the cleaned extraction plus the
complete audit trail (operations, linkages, flags). Persisting lineage/audit and threading
the cleaned extraction onward is the ingestion layer's job, which is what keeps this layer
unit-testable and reproducible.

Pipeline per the build brief:

1. **Resolve** — every stage keys on canonical concepts (:mod:`app.etl.resolve`) so a value
   under a raw alias (``balance_ghs``) is still guarded as its concept (``balance``).
2. **Preprocess** — deterministic sanctioned stages (normalize -> coerce -> resolve
   references), each threading its SANCTIONED rewrites into the working record so later
   stages see cleaned input. Regulatory-critical value *changes* are FLAGGED, never applied.
3. **Deduplicate** — cross-source counterparty matching, cross-time/within-source position
   deduplication, and fingerprint anomaly detection. Linkages preserve every source record;
   anomaly flags never modify a value.

The cleaned extraction always has the **same record count** as the input — preprocessing
transforms fields in place, it never adds or drops records.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.etl.contracts import Disposition, ETLResult, MatchType
from app.etl.deduplication import (
    CounterpartyMatcher,
    FingerprintAnomalyDetector,
    PositionDeduplicator,
)
from app.etl.preprocessing import (
    CountryNormalizer,
    CurrencyNormalizer,
    DateNormalizer,
    ReferenceResolver,
    TextNormalizer,
    TypeCoercer,
)
from app.etl.resolve import resolve_concept

if TYPE_CHECKING:
    from app.domain.ingestion.contracts import ExtractionResult, MappingConfig, RawRecord
    from app.etl.contracts import ETLOperation, LinkageRecord, Preprocessor


@dataclass(frozen=True)
class EtlConfig:
    """Thresholds and stage toggles for one ETL pass (sane defaults; dedup on by default)."""

    normalize: bool = True
    coerce_types: bool = True
    resolve_references: bool = True
    deduplicate: bool = True
    detect_anomalies: bool = True
    # Counterparty matcher thresholds (forwarded verbatim).
    auto_confirm_threshold: float = 0.90
    review_floor: float = 0.55
    # Fingerprint anomaly cutoff (forwarded verbatim).
    anomaly_score_threshold: float = 0.75
    # How many example rows ``etl_summary`` embeds per collection.
    summary_sample_limit: int = 5


def run_etl(
    extraction: ExtractionResult,
    mapping: MappingConfig,
    *,
    config: EtlConfig | None = None,
) -> ETLResult:
    """Run the full ML-ETL pass over one extraction batch (pure; no side effects)."""
    cfg = config or EtlConfig()
    stages = _build_stages(mapping, cfg)

    operations: list[ETLOperation] = []
    cleaned_records: list[RawRecord] = []
    for record in extraction.records:
        record_ops, cleaned = _preprocess_record(record, stages)
        operations.extend(record_ops)
        cleaned_records.append(cleaned)

    cleaned_extraction = extraction.model_copy(update={"records": cleaned_records})

    linkages: list[LinkageRecord] = []
    if cfg.deduplicate:
        linkages.extend(
            CounterpartyMatcher(
                auto_confirm_threshold=cfg.auto_confirm_threshold,
                review_floor=cfg.review_floor,
            ).link(cleaned_records)
        )
        linkages.extend(PositionDeduplicator().link(cleaned_records))

    if cfg.detect_anomalies:
        operations.extend(
            FingerprintAnomalyDetector(score_threshold=cfg.anomaly_score_threshold).score(
                cleaned_records
            )
        )

    flags = [op for op in operations if op.disposition is Disposition.FLAGGED]
    return ETLResult(
        cleaned=cleaned_extraction,
        operations=operations,
        linkages=linkages,
        flags=flags,
    )


def _build_stages(mapping: MappingConfig, cfg: EtlConfig) -> list[Preprocessor]:
    """Assemble the ordered preprocessing stages enabled by ``cfg``.

    Order matters: whitespace/unicode first (so downstream stages see trimmed text), then
    the ISO normalizers, then type coercion (which reads the normalized strings), then
    reference resolution (which reads the resolved product code).
    """
    stages: list[Preprocessor] = []
    if cfg.normalize:
        stages.extend(
            [TextNormalizer(), CurrencyNormalizer(), CountryNormalizer(), DateNormalizer()]
        )
    if cfg.coerce_types:
        stages.append(TypeCoercer())
    if cfg.resolve_references:
        stages.append(ReferenceResolver(dict(mapping.product_mappings)))
    return stages


def _preprocess_record(
    record: RawRecord, stages: list[Preprocessor]
) -> tuple[list[ETLOperation], RawRecord]:
    """Run every stage over one record, threading SANCTIONED rewrites into the working copy.

    Only SANCTIONED operations mutate the cleaned record; FLAGGED operations leave the value
    untouched (a human decides). The returned cleaned record always corresponds one-to-one
    with the input record — never added, never dropped.
    """
    data = dict(record.data)
    ops: list[ETLOperation] = []
    working = record
    for stage in stages:
        stage_ops = stage.apply(working)
        if not stage_ops:
            continue
        mutated = False
        for op in stage_ops:
            ops.append(op)
            if op.disposition is Disposition.SANCTIONED:
                data[op.field_name] = op.after
                mutated = True
        if mutated:
            working = record.model_copy(update={"data": dict(data)})
    cleaned = record.model_copy(update={"data": data})
    return ops, cleaned


def etl_summary(result: ETLResult, *, sample_limit: int = 5) -> dict[str, Any]:
    """Compact, JSON-serialisable summary of an :class:`ETLResult`.

    Suitable for a lineage node's ``details`` payload and for a batch report: aggregate
    counts plus a few illustrative samples of each collection. Values are stringified so the
    payload is safe to serialise regardless of the underlying cell types.
    """
    operations = result.operations
    flags = result.flags
    sanctioned = [op for op in operations if op.disposition is Disposition.SANCTIONED]

    op_by_type: dict[str, int] = {}
    for op in operations:
        key = op.provenance.operation_type.value
        op_by_type[key] = op_by_type.get(key, 0) + 1

    link_by_type: dict[str, int] = {mt.value: 0 for mt in MatchType}
    auto_confirmed = 0
    for link in result.linkages:
        link_by_type[link.match_type.value] = link_by_type.get(link.match_type.value, 0) + 1
        if link.auto_confirmed:
            auto_confirmed += 1

    return {
        "record_count": len(result.cleaned.records),
        "operation_count": len(operations),
        "sanctioned_count": len(sanctioned),
        "flagged_count": len(flags),
        "linkage_count": len(result.linkages),
        "operations_by_type": op_by_type,
        "linkages_by_match_type": link_by_type,
        "auto_confirmed_linkages": auto_confirmed,
        "sample_operations": [_op_sample(op) for op in sanctioned[:sample_limit]],
        "sample_flags": [_flag_sample(op) for op in flags[:sample_limit]],
        "sample_linkages": [_link_sample(link) for link in result.linkages[:sample_limit]],
    }


def _op_sample(op: ETLOperation) -> dict[str, Any]:
    return {
        "record_id": op.record_id,
        "field": op.field_name,
        "concept": resolve_concept(op.field_name),
        "disposition": op.disposition.value,
        "operation_type": op.provenance.operation_type.value,
        "before": _stringify(op.before),
        "after": _stringify(op.after),
        "confidence": op.provenance.confidence,
    }


def _flag_sample(op: ETLOperation) -> dict[str, Any]:
    return {
        "record_id": op.record_id,
        "field": op.field_name,
        "concept": resolve_concept(op.field_name),
        "operation_type": op.provenance.operation_type.value,
        "before": _stringify(op.before),
        "reason": op.reason,
        "confidence": op.provenance.confidence,
    }


def _link_sample(link: LinkageRecord) -> dict[str, Any]:
    return {
        "match_type": link.match_type.value,
        "canonical_winner_id": link.canonical_winner_id,
        "linked_source_ids": list(link.linked_source_ids),
        "combined_confidence": link.combined_confidence,
        "auto_confirmed": link.auto_confirmed,
        "signals": link.signals,
    }


def _stringify(value: Any) -> str | None:
    return None if value is None else str(value)
