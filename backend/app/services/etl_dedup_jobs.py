"""Out-of-band ML-ETL deduplication pass for large ingestion batches.

The inline ingestion pipeline (``app.services.ingestion``) skips the pairwise /
model-scored dedup + anomaly passes above ``_ETL_INLINE_DEDUP_MAX_RECORDS`` so a
core-banking-scale sync (100k+ rows) is not blocked for tens of minutes. Those
passes emit linkage / anomaly METADATA only — they never change which canonical
records persist — so the sole thing lost by skipping them inline is the
entity-resolution info in ``batch.etl_report`` and the dedup lineage / audit
detail. This module re-derives exactly that, out of band.

:func:`run_etl_dedup` is the ``etl_dedup`` worker handler. It reloads the target
batch (org-scoped), re-extracts the source the same way ingestion does — reading
the persisted raw artifact through the batch's stored mapping — re-runs the pure
:func:`app.etl.run_etl` pass with dedup + anomaly enabled, and MERGES the results
into ``batch.etl_report`` (never clobbering the inline preprocessing summary),
appending an ``ML_ETL_DEDUP`` lineage node and an ``ml_etl.dedup_completed``
audit event with the real counts. It is idempotent (guarded on the report's
``dedup_status`` marker) and never mutates canonical records.

It then runs the CROSS-SOURCE position pass
(:mod:`app.etl.deduplication.cross_source_positions`), which the pure per-batch
:func:`run_etl` structurally cannot do: duplicate books arrive in *different*
batches, sometimes a month apart, so finding them needs the current canonical
generation across every source system at one as-of. That pass is DETECTION only —
it emits linkage evidence and an advisory data-quality outcome, and picks no
winner (see the matcher's module docstring and ``app.services.system_of_record``).

Three reliability properties are load-bearing, each one an observed failure:

* **The connection is released across the long non-DB phase.** Re-extraction plus
  the pure ETL pass run for minutes to hours without issuing SQL. Holding a
  pooled connection idle across that is what killed job ``9d1574fb`` on the
  primary ("server closed the connection unexpectedly" on the first query
  afterwards): an idle socket that a firewall, NAT or server restart severs gets
  neither ``pool_pre_ping`` nor ``pool_recycle``, because both only apply on
  check-out. Committing before the heavy phase returns the connection to the
  pool, so the next use is a revalidated check-out.
* **The pairwise counterparty pass is bounded.** It is the only quadratic,
  model-scored stage and runs for hours on a core-banking counterparty file; three
  jobs were reclaimed mid-flight as "worker presumed dead" after exceeding the
  15-minute stale-job window, and the one that eventually succeeded took 2h02m —
  during which the reaper had already requeued it twice, so the same handler ran
  concurrently. Above :data:`_DEFERRED_COUNTERPARTY_MAX_RECORDS` the pass is
  skipped and the report SAYS which pass was skipped and why; it is never
  silently dropped.
* **Failure is terminal-but-visible, never invisible.** A raising handler used to
  leave ``dedup_status`` at ``"deferred"`` for ever — indistinguishable from
  "queued, will run shortly". The handler now marks the batch ``"failed"`` with
  the error and emits an ``ml_etl.dedup_failed`` audit event before re-raising,
  so the queue still retries and the batch still says what happened.

Deliberately NOT a filing gate: see :data:`DEDUP_STATUS_FAILED`.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

import app.adapters  # noqa: F401 - importing registers every shipped source adapter
from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.authority.outcomes import OutcomeDetail, OutcomeState
from app.domain.authority.outcomes import outcome as build_outcome
from app.domain.ingestion.adapter import get_adapter_class
from app.domain.ingestion.constants import DEDUP_STATUS_COMPLETED, DEDUP_STATUS_FAILED
from app.domain.ingestion.contracts import ENTITY_TYPES, MappingConfig, RawRecord
from app.etl import EtlConfig, ETLResult, etl_summary, model_loading, run_etl
from app.etl.contracts import ETLOperationType
from app.etl.deduplication import CounterpartyMatcher
from app.etl.deduplication.cross_source_positions import (
    CanonicalPositionRow,
    CrossSourcePositionMatcher,
    CrossSourceResult,
)
from app.models import (
    Bank,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    IngestionBatch,
    Job,
    LineageRecord,
    MappingConfigRecord,
)
from app.models.canonical import is_current_generation
from app.services import reconciliation
from app.services.audit import record_event
from app.services.ingestion import bank_slug, build_adapter_config
from app.storage.client import StorageLocation
from app.storage.factory import get_storage_client

if TYPE_CHECKING:
    from app.domain.ingestion.contracts import ExtractionResult

logger = logging.getLogger(__name__)

ETL_DEDUP = "etl_dedup"

_SAMPLE_LIMIT = 5

# ``etl_report["dedup_status"]`` values. ``deferred`` means "queued, not yet
# run"; ``failed`` means "ran and could not complete" — the distinction the batch
# could not previously express, which is why four batches on the primary read as
# pending for weeks after their jobs had exhausted every attempt. The three
# values now live in ``app.domain.ingestion.constants`` (one vocabulary for the
# inline writer, this out-of-band writer and the operator backlog board, which
# must be able to name a stuck pass without importing the ETL stack); the two
# this module writes are imported from there, and ``DEDUP_STATUS_DEFERRED`` is
# named at its definition site only.
#
# A permanently undeduplicated batch is VISIBLE, not blocking. The deferred pass
# emits linkage/anomaly METADATA only: no canonical value and no derived figure
# depends on it, so a return filed from this batch is byte-identical whether or
# not the pass ran, and refusing the filing would be a fail-closed gate on
# something that provably does not enter the filing. What genuinely threatens the
# figure — two systems carrying one book — is gated where it belongs, by the
# balance-sheet identity control (``reconciliation.assert_filing_reconciled``),
# which fires on duplicated exposure whether or not dedup ran. The converse
# matters just as much: a COMPLETED pass is not evidence of a clean book (it
# links, it resolves nothing), so letting "completed" satisfy a filing gate would
# license filing on a book known to be doubled.
#
# What a ``failed``/exhausted pass DOES need is a way back: the queue stops at
# ``max_attempts`` and nothing re-drives it. That surface is the operator's
# (``app.operator.services.inspector_fix.redrive_dedup``) and is deliberately
# manual — the four stranded batches failed for three unrelated reasons and a
# blind retry timer would have hidden every one of them.

#: Above this many counterparty records the deferred pass skips the pairwise,
#: model-scored counterparty matcher and says so in the report. Chosen against
#: measured cost (~370us per candidate pair through the full signal vector,
#: dominated by the per-pair TF-IDF fit): a core-banking counterparty file blocks
#: for hours, which is longer than any sane worker stale-job window
#: (``WORKER_STALE_JOB_SECONDS``, 900s by default), so the job is reclaimed as a
#: dead worker and eventually fails while still running. Bounding the pass is the
#: fix that does not require every deployment to widen its reaper window; a
#: deployment that WANTS the full pass raises both together, deliberately.
_DEFERRED_COUNTERPARTY_MAX_RECORDS = 5000

#: The row-level cross-source pass's metric id. Distinct from
#: ``reconciliation.SOURCE_OVERLAP_METRIC_ID`` (which sizes the duplication at
#: BOOK level) and from ``system_of_record.SYSTEM_OF_RECORD_METRIC_ID`` (which
#: attributes it): this one names the individual positions two systems both hold.
CROSS_SOURCE_METRIC_ID = "cross_source_position_overlap"
CROSS_SOURCE_CONTROL = "cross_source_position_overlap"

DEDUP_COMPLETED_EVENT = "ml_etl.dedup_completed"
DEDUP_FAILED_EVENT = "ml_etl.dedup_failed"

#: The validation statuses whose rows a calculation reads. Mirrors
#: ``fact_derivation._INCLUDED_VALIDATION_STATUSES`` so the row-level pass is
#: measured over exactly the population the balance sheet is built from.
_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")

#: How many matched position ids the outcome names inline. The full set lives
#: in the linkage evidence; an outcome carrying six figures of items is not an
#: outcome, it is a payload.
_ITEM_LIMIT = 50


class EtlDedupJobError(Exception):
    """An etl_dedup job could not run (missing batch, mapping, or artifact)."""



@dataclass(frozen=True)
class _BatchFacts:
    """Everything the long, DB-free phase needs, captured as plain values.

    The heavy phase (storage read, ``adapter.extract``, ``run_etl``) must issue
    NO SQL, so it cannot hold ORM instances: touching an expired attribute after
    the pre-phase commit would re-open a transaction and reinstate exactly the
    idle-connection window this structure exists to close.
    """

    batch_id: UUID
    organization_id: str
    bank_id: str
    slug: str
    source_system: str
    as_of_date: date
    raw_artifact_path: str
    content_hash: str | None
    mapping: MappingConfig


def run_etl_dedup(session: Session, job: Job) -> None:
    """Worker handler: run the deferred ML-ETL dedup pass for one batch.

    Payload: ``{"batch_id": ...}``. Idempotent — a batch whose report already
    reads ``dedup_status == "completed"`` is a no-op, so a retry or a coalesced
    re-enqueue never double-counts linkages or duplicates lineage / audit. A
    batch previously marked ``failed`` is retried: the marker records what
    happened, it does not bar the batch from ever completing.
    """
    batch = _batch_or_error(session, job)
    if (batch.etl_report or {}).get("dedup_status") == DEDUP_STATUS_COMPLETED:
        job.progress = {"batch_id": str(batch.id), "status": "already_completed"}
        return
    batch_id = batch.id
    try:
        _run(session, job, batch)
    except Exception as exc:
        # A raising handler is the queue's only failure signal, and the queue's
        # record of it is invisible from the batch. Stamp the batch before
        # re-raising so a permanently-failed pass cannot masquerade as pending.
        _mark_dedup_failed(session, batch_id, exc)
        raise


def _run(session: Session, job: Job, batch: IngestionBatch) -> None:
    """The dedup pass proper, split so :func:`run_etl_dedup` owns failure marking."""
    bank = _bank_or_error(session, batch)
    ctx = TenantContext(organization_id=batch.organization_id, actor_user_id=batch.created_by)
    facts = _capture(session, batch, bank)
    counterparty_model = model_loading.load_counterparty_model(ctx.organization_id, bank.id)
    anomaly_model = model_loading.load_anomaly_model(ctx.organization_id, bank.id)

    # Return the pooled connection BEFORE the long DB-free phase; the next use is
    # a fresh, pre-pinged check-out. See the module docstring.
    session.commit()

    extraction = _reextract(facts)
    match_counterparties = _counterparty_records(extraction) <= _DEFERRED_COUNTERPARTY_MAX_RECORDS
    result = run_etl(
        extraction,
        facts.mapping,
        config=EtlConfig(
            deduplicate=True,
            detect_anomalies=True,
            match_counterparties=match_counterparties,
            counterparty_model=counterparty_model,
            anomaly_model=anomaly_model,
        ),
    )

    overlay = _dedup_anomaly_overlay(result, match_counterparties=match_counterparties)
    cross_source = assess_cross_source_positions(
        session, ctx, facts.bank_id, facts.as_of_date
    ).report()
    overlay["cross_source"] = cross_source

    batch = _reload(session, facts)
    batch.etl_report = {**(batch.etl_report or {}), **overlay}

    anomaly_count = overlay["anomaly_count"]
    _record_dedup_lineage(
        session,
        ctx,
        batch,
        linkages=overlay["linkage_count"],
        anomalies=anomaly_count,
        content_hash_match=extraction.content_hash == facts.content_hash,
        cross_source=cross_source,
    )
    record_event(
        session,
        ctx,
        event_type=DEDUP_COMPLETED_EVENT,
        entity_type="ingestion_batch",
        entity_id=batch.id,
        details=batch.etl_report,
    )
    session.commit()
    job.progress = {
        "batch_id": str(batch.id),
        "status": "completed",
        "linkage_count": overlay["linkage_count"],
        "anomaly_count": anomaly_count,
        "records_extracted": len(extraction.records),
        "counterparty_matching": overlay["counterparty_matching"],
        "cross_source_linkage_count": cross_source["linkage_count"],
    }


def _capture(session: Session, batch: IngestionBatch, bank: Bank) -> _BatchFacts:
    """Snapshot the batch/bank/mapping into plain values for the DB-free phase."""
    if not batch.raw_artifact_path:
        msg = f"Batch {batch.id} has no raw artifact to re-extract for ML-ETL dedup."
        raise EtlDedupJobError(msg)
    return _BatchFacts(
        batch_id=batch.id,
        organization_id=batch.organization_id,
        bank_id=bank.id,
        slug=bank_slug(session, bank),
        source_system=batch.source_system,
        as_of_date=batch.as_of_date,
        raw_artifact_path=batch.raw_artifact_path,
        content_hash=batch.content_hash,
        mapping=_mapping_or_error(session, batch),
    )


def _reload(session: Session, facts: _BatchFacts) -> IngestionBatch:
    """Re-fetch the batch after the DB-free phase (its connection was released)."""
    batch = session.scalar(
        select(IngestionBatch).where(
            IngestionBatch.id == facts.batch_id,
            IngestionBatch.organization_id == facts.organization_id,
        )
    )
    if batch is None:  # pragma: no cover - the batch was loaded moments earlier
        msg = f"Ingestion batch {facts.batch_id} disappeared during the ML-ETL dedup pass."
        raise EtlDedupJobError(msg)
    return batch


def _counterparty_records(extraction: ExtractionResult) -> int:
    return sum(1 for record in extraction.records if record.entity_type == "counterparty")


def _mark_dedup_failed(session: Session, batch_id: UUID, exc: BaseException) -> None:
    """Stamp ``dedup_status="failed"`` on the batch and audit it, then let the raise stand.

    Runs on a rolled-back session because the failure may have left the
    transaction unusable, and best-effort because a marking failure must never
    replace the original error the operator needs to see.
    """
    try:
        session.rollback()
        batch = session.get(IngestionBatch, batch_id)
        if batch is None:  # pragma: no cover - defensive
            return
        report = dict(batch.etl_report or {})
        report["dedup_status"] = DEDUP_STATUS_FAILED
        report["dedup_error"] = f"{type(exc).__name__}: {exc}"[:2000]
        report["dedup_failed_at"] = utc_now().isoformat()
        batch.etl_report = report
        ctx = TenantContext(
            organization_id=batch.organization_id, actor_user_id=batch.created_by
        )
        record_event(
            session,
            ctx,
            event_type=DEDUP_FAILED_EVENT,
            entity_type="ingestion_batch",
            entity_id=batch.id,
            details={
                "dedup_status": DEDUP_STATUS_FAILED,
                "dedup_error": report["dedup_error"],
            },
        )
        session.commit()
    except Exception:  # pragma: no cover - never mask the original failure
        logger.exception("Could not mark ML-ETL dedup failure on batch %s", batch_id)
        session.rollback()


# ---------------------------------------------------------------------------
# Cross-source position pass (row-level; detection only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CounterpartyIdentity:
    """The identity fields the cross-source counterparty resolution reads."""

    reference: str
    source_system: str
    name: str | None
    country_code: str | None
    counterparty_type: str | None


@dataclass(frozen=True)
class CrossSourceAssessment:
    """Row-level cross-source evidence for one bank at one as-of date.

    The consumable handoff to the resolution layer. ``result`` carries EVERY
    linkage, not a sample — a withdrawal needs the full set of position ids that
    are provably held twice, and that is what makes withdrawing them something
    other than a guess. Nothing is persisted as linkage rows, deliberately: the
    resolution layer calls :func:`assess_cross_source_positions` on demand,
    exactly as ``system_of_record.assess`` calls
    ``reconciliation.detect_source_overlap`` rather than reading a stored
    verdict, so the evidence can never go stale against the book it describes.
    """

    bank_id: str
    as_of_date: date
    #: The book-level diagnosis this assessment is bounded by, carried through
    #: unmodified so a consumer sees the same contested set the row pass used.
    overlap: reconciliation.SourceOverlapOutcome
    contested_position_types: tuple[str, ...]
    result: CrossSourceResult
    detail: OutcomeDetail | None

    def report(self) -> dict[str, Any]:
        """The JSON payload merged into ``etl_report`` and the lineage node."""
        payload: dict[str, Any] = {
            "control": CROSS_SOURCE_CONTROL,
            "as_of_date": self.as_of_date.isoformat(),
            "book_overlap_control": reconciliation.CONTROL_SOURCE_BOOK_OVERLAP,
            "book_overlap_status": self.overlap.provenance()["status"],
            "contested_position_types": list(self.contested_position_types),
            "linkage_count": len(self.result.linkages),
            "linkages_by_match": self.result.by_match() if self.result.linkages else {},
            "coverage": (
                self.result.coverage.to_dict() if self.contested_position_types else None
            ),
            "sample_linkages": [
                _link_sample(link) for link in self.result.linkages[:_SAMPLE_LIMIT]
            ],
        }
        if self.detail is not None:
            payload["outcome"] = self.detail.to_dict()
        return payload


def assess_cross_source_positions(
    session: Session, ctx: TenantContext, bank_id: str, as_of: date
) -> CrossSourceAssessment:
    """Find positions that two source systems both carry, at ``as_of``.

    Read-only and side-effect free, like ``fact_derivation.diagnose_source_overlap``:
    it writes nothing, derives no fact and blocks nothing. The ``etl_dedup`` job
    calls it to record evidence; the resolution layer can call it to see exactly
    which rows a withdrawal would remove.

    Bounded by the BOOK-level detector: only position types
    ``reconciliation.detect_source_overlap`` reports as materially contested are
    examined. That is deliberate on both axes — it keeps one governed notion of
    "contested" and one materiality rule instead of inventing a second, and it
    stops the pass doing a six-figure row comparison on a bank whose sources
    genuinely partition the book (core banking owns loans, treasury owns
    securities), where every match it could report would be a false positive.
    """
    bank = session.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        msg = f"Cross-source assessment references unknown bank {bank_id}."
        raise EtlDedupJobError(msg)
    books = reconciliation.tally_source_books(_book_rows(session, ctx, bank_id, as_of))
    policy = reconciliation.load_policy(session, ctx.organization_id, bank, as_of)
    overlap = reconciliation.detect_source_overlap(books, tolerance=policy.tolerance)
    contested = tuple(contest.position_type for contest in overlap.contested)
    if not contested:
        # A partitioned or single-source book: nothing contested, so nothing to
        # match. "No contested type" is not the same claim as "no duplicates",
        # and the report keeps the two distinguishable.
        return CrossSourceAssessment(
            bank_id=bank_id,
            as_of_date=as_of,
            overlap=overlap,
            contested_position_types=(),
            result=CrossSourceResult(),
            detail=None,
        )

    rows = _load_cross_source_rows(session, ctx, bank_id, as_of, contested)
    result = CrossSourcePositionMatcher().link(rows)
    return CrossSourceAssessment(
        bank_id=bank_id,
        as_of_date=as_of,
        overlap=overlap,
        contested_position_types=contested,
        result=result,
        detail=_cross_source_detail(bank_id, ctx.organization_id, as_of, result),
    )


def _cross_source_detail(
    bank_id: str, organization_id: str, as_of: date, result: CrossSourceResult
) -> OutcomeDetail | None:
    """The WS-A data-quality outcome for the row evidence, or ``None`` when clean.

    ALWAYS ``advisory=True``, exactly like ``SourceOverlapOutcome.detail()`` and
    for the same reason: the balance-sheet identity control is the filing gate,
    and a detector that starts refusing filings on its own would make the gate
    ambiguous. It is ``DATA_QUALITY_BLOCK`` because the inputs exist and failed a
    quality test — the same book is present twice — not because anything is
    missing. A run that matched nothing produces NO outcome: a finding on a clean
    book teaches operators to ignore findings.
    """
    if not result.linkages:
        return None
    coverage = result.coverage
    # Name what MATCHED, never the whole contested population: a finding that
    # listed every contested type when only the deposit book paired would
    # overstate the evidence, and an overstated finding gets ignored.
    types = ", ".join(
        reconciliation.position_type_label(code) for code in coverage.matched_position_types
    )
    systems = ", ".join(
        reconciliation.source_system_label(code) for code in coverage.matched_source_systems
    )
    return build_outcome(
        OutcomeState.DATA_QUALITY_BLOCK,
        metric_id=CROSS_SOURCE_METRIC_ID,
        reason=(
            f"{coverage.matched_rows} individual positions in {types} were matched to the "
            f"same position in another source system ({systems}) for this date, across "
            f"{len(result.linkages)} matched groups, so those exposures are carried twice. "
            "Each source system supersedes only its own records, so no figure derived from "
            "them nets the duplication out. This names the specific positions; it does not "
            "decide which system owns them — confirm the book of record for these position "
            "types, then withdraw the other system's rows."
        ),
        items=tuple(
            f"position:{row_id}" for row_id in sorted(result.matched_row_ids)[:_ITEM_LIMIT]
        ),
        advisory=True,
        context={
            "bank_id": bank_id,
            "organization_id": organization_id,
            "as_of": as_of.isoformat(),
            "control": CROSS_SOURCE_CONTROL,
            "linkage_count": len(result.linkages),
            "linkages_by_match": result.by_match(),
            "coverage": result.coverage.to_dict(),
        },
    )


def _book_rows(
    session: Session, ctx: TenantContext, bank_id: str, as_of: date
) -> list[tuple[str, str, Any]]:
    """``(source_system, position_type, balance)`` for the current book at ``as_of``.

    The same population ``fact_derivation`` measures: current generation, in an
    included validation status. Amounts are the snapshot balances rather than
    base-currency conversions, so the amount axis of materiality is measured a
    little conservatively for a multi-currency book; the record-share axis
    (``reconciliation.MIN_CONTESTED_RECORD_SHARE``) is currency-blind and carries
    the case this pass actually cares about.
    """
    rows = session.execute(
        select(
            CanonicalPosition.source_system,
            CanonicalPosition.position_type,
            CanonicalPositionSnapshot.balance,
        )
        .join(
            CanonicalPosition,
            CanonicalPositionSnapshot.position_id == CanonicalPosition.id,
        )
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank_id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
            *is_current_generation(CanonicalPositionSnapshot),
            *is_current_generation(CanonicalPosition),
        )
    ).all()
    return [(row[0], row[1], row[2]) for row in rows]


def _load_cross_source_rows(
    session: Session,
    ctx: TenantContext,
    bank_id: str,
    as_of: date,
    contested: tuple[str, ...],
) -> list[CanonicalPositionRow]:
    """Flatten the contested types' current book into matcher rows."""
    rows = session.execute(
        select(
            CanonicalPosition.id.label("position_id"),
            CanonicalPosition.source_system.label("source_system"),
            CanonicalPosition.source_reference.label("source_reference"),
            CanonicalPosition.position_type.label("position_type"),
            CanonicalPosition.currency.label("currency"),
            CanonicalPosition.origination_date.label("origination_date"),
            CanonicalPositionSnapshot.contractual_maturity.label("contractual_maturity"),
            CanonicalPositionSnapshot.interest_rate.label("interest_rate"),
            CanonicalPositionSnapshot.balance.label("balance"),
            CanonicalProduct.product_code.label("product_code"),
            CanonicalCounterparty.id.label("counterparty_id"),
            CanonicalCounterparty.source_reference.label("counterparty_reference"),
            CanonicalCounterparty.name.label("counterparty_name"),
            CanonicalCounterparty.country_code.label("counterparty_country"),
            CanonicalCounterparty.counterparty_type.label("counterparty_type"),
        )
        .join(
            CanonicalPosition,
            CanonicalPositionSnapshot.position_id == CanonicalPosition.id,
        )
        .outerjoin(
            CanonicalProduct, CanonicalPositionSnapshot.product_id == CanonicalProduct.id
        )
        .outerjoin(
            CanonicalCounterparty,
            CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
        )
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank_id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPosition.position_type.in_(contested),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
            *is_current_generation(CanonicalPositionSnapshot),
            *is_current_generation(CanonicalPosition),
        )
    ).mappings().all()
    keys = _counterparty_keys(rows)
    return [
        CanonicalPositionRow(
            row_id=str(row["position_id"]),
            source_system=row["source_system"],
            source_reference=row["source_reference"],
            position_type=row["position_type"],
            currency=row["currency"],
            counterparty_key=keys.get(str(row["counterparty_id"])),
            product_code=row["product_code"],
            origination_date=row["origination_date"],
            contractual_maturity=row["contractual_maturity"],
            interest_rate=row["interest_rate"],
            balance=row["balance"],
        )
        for row in rows
    ]


def _counterparty_keys(rows: Sequence[RowMapping]) -> dict[str, str]:
    """Resolve each counterparty to a CROSS-SOURCE identity, in two tiers.

    Tier A — the bank's own customer identifier used by more than one source
    system. Exact, free, and at Sample Bank it resolves the whole population
    (``SBL-CUST-016878`` is the same string in every feed).

    Tier B — :class:`CounterpartyMatcher`, the layer's existing cross-source
    entity resolver, over whatever tier A left unresolved. Reused rather than
    re-implemented: this pass consumes its ``CROSS_SOURCE`` clusters as a join
    dimension and never scores a name itself.

    Tier B is BUDGETED. It is pairwise within a phonetic block at ~370us per pair,
    so an unbounded run on a core-banking customer file is the same hours-long
    stall that got three ``etl_dedup`` jobs reclaimed. Over budget it is skipped,
    the affected positions keep ``counterparty_key=None``, and the matcher counts
    them under ``unresolved_counterparty`` — a stated gap, never a silent one.
    """
    by_reference: dict[str, set[str]] = {}
    identity: dict[str, _CounterpartyIdentity] = {}
    for row in rows:
        cp_id = row["counterparty_id"]
        if cp_id is None:
            continue
        cid = str(cp_id)
        reference = (row["counterparty_reference"] or "").strip().upper()
        identity[cid] = _CounterpartyIdentity(
            reference=reference,
            source_system=row["source_system"],
            name=row["counterparty_name"],
            country_code=row["counterparty_country"],
            counterparty_type=row["counterparty_type"],
        )
        if reference:
            by_reference.setdefault(reference, set()).add(row["source_system"])

    keys: dict[str, str] = {}
    unresolved: list[str] = []
    for cid, party in identity.items():
        if party.reference and len(by_reference.get(party.reference, set())) > 1:
            keys[cid] = f"ref:{party.reference}"
        else:
            unresolved.append(cid)

    if not unresolved or len(unresolved) > _DEFERRED_COUNTERPARTY_MAX_RECORDS:
        return keys

    records = [
        RawRecord(
            entity_type="counterparty",
            source_locator=cid,
            source_table=identity[cid].source_system,
            data={
                "counterparty_id": cid,
                "counterparty_name": identity[cid].name or "",
                "country": identity[cid].country_code,
                "counterparty_type": identity[cid].counterparty_type,
            },
        )
        for cid in unresolved
    ]
    for link in CounterpartyMatcher().link(records):
        systems = {
            identity[mid].source_system for mid in link.linked_source_ids if mid in identity
        }
        if len(systems) < 2:
            continue  # a within-system name cluster is not a cross-source identity
        for member_id in link.linked_source_ids:
            keys[member_id] = f"cluster:{link.canonical_winner_id}"
    return keys


def _dedup_anomaly_overlay(result: ETLResult, *, match_counterparties: bool) -> dict[str, Any]:
    """The dedup + anomaly keys to merge into a deferred ``etl_report``.

    Only the entity-resolution / anomaly-derived keys are returned, so merging
    leaves the inline preprocessing summary (record / operation / flag counts,
    sample operations, sample preprocess flags) untouched. The linkage keys
    overwrite the zeroed placeholders the deferred report carried; anomaly counts
    are surfaced under their own keys — the inline pass ran no anomaly detector,
    so its ``flagged_count`` / ``sample_flags`` stay preprocessing-only.

    ``counterparty_matching`` names whether the pairwise pass ran. A skipped pass
    reporting zero counterparty linkages would be indistinguishable from a pass
    that ran and found none, which is the ambiguity the whole ``dedup_status``
    marker exists to prevent one level up.
    """
    summary = etl_summary(result, sample_limit=_SAMPLE_LIMIT)
    anomalies = [
        op
        for op in result.operations
        if op.provenance.operation_type is ETLOperationType.ANOMALY_FLAG
    ]
    return {
        "linkage_count": summary["linkage_count"],
        "linkages_by_match_type": summary["linkages_by_match_type"],
        "auto_confirmed_linkages": summary["auto_confirmed_linkages"],
        "sample_linkages": summary["sample_linkages"],
        "anomaly_count": len(anomalies),
        "sample_anomalies": [
            {
                "record_id": op.record_id,
                "field": op.field_name,
                "reason": op.reason,
                "confidence": op.provenance.confidence,
            }
            for op in anomalies[:_SAMPLE_LIMIT]
        ],
        "counterparty_matching": "completed" if match_counterparties else "skipped_over_budget",
        "counterparty_match_budget": _DEFERRED_COUNTERPARTY_MAX_RECORDS,
        "dedup_status": DEDUP_STATUS_COMPLETED,
    }


def _link_sample(link: Any) -> dict[str, Any]:
    return {
        "match_type": link.match_type.value,
        "linked_source_ids": list(link.linked_source_ids),
        "combined_confidence": link.combined_confidence,
        "signals": link.signals,
        # Named, not implied: a cross-source position linkage names no winner.
        "system_of_record": "undetermined",
    }


def _reextract(facts: _BatchFacts) -> ExtractionResult:
    """Reconstruct the batch's extraction from its persisted raw artifact.

    Mirrors ingestion's extract path: materialize the untouched source file from
    the ``raw`` tier into a scratch dir (its original name preserved so the
    adapter recognizes the format), then run the same adapter over the mapping's
    table-resolution config. The original ``adapter_options`` are not persisted
    on the batch, so they are not reconstructed — they tune extraction, not the
    canonical records, and the content-hash match recorded in lineage flags any
    divergence.

    Takes plain values, not a :class:`Session`: this is the long DB-free phase and
    it must not issue SQL (see the module docstring on the severed connection).
    """
    storage = get_storage_client()
    _, stream = storage.read(
        StorageLocation(
            institution_slug=facts.slug, tier="raw", object_path=facts.raw_artifact_path
        )
    )
    scratch = Path(tempfile.mkdtemp(prefix="aequoros-etl-dedup-"))
    local = scratch / (Path(facts.raw_artifact_path).name or "source")
    local.write_bytes(stream.read())

    adapter = get_adapter_class(facts.source_system)()
    adapter_config = build_adapter_config(str(local), facts.mapping)
    return adapter.extract(adapter_config, facts.as_of_date, list(ENTITY_TYPES))


def _record_dedup_lineage(  # noqa: PLR0913 - mirrors the inline lineage node's shape
    session: Session,
    ctx: TenantContext,
    batch: IngestionBatch,
    *,
    linkages: int,
    anomalies: int,
    content_hash_match: bool,
    cross_source: dict[str, Any],
) -> None:
    """Append an ML_ETL_DEDUP lineage node for the out-of-band pass.

    Chained onto the batch's inline ML_ETL_PREPROCESS node when present so the
    node sits in the same extract → preprocess → dedup graph the inline pass
    builds; the canonical records keep their VALIDATION lineage id either way.
    """
    preprocess_id = session.scalar(
        select(LineageRecord.id).where(
            LineageRecord.organization_id == ctx.organization_id,
            LineageRecord.ingestion_batch_id == batch.id,
            LineageRecord.operation_type == "ML_ETL_PREPROCESS",
        )
    )
    node = LineageRecord(
        organization_id=ctx.organization_id,
        ingestion_batch_id=batch.id,
        operation_type="ML_ETL_DEDUP",
        operation_ref="ml_etl/dedup",
        input_lineage_ids=[str(preprocess_id)] if preprocess_id is not None else [],
        details={
            "linkages": linkages,
            "anomalies": anomalies,
            "deferred_pass": True,
            "content_hash_match": content_hash_match,
            "cross_source_linkages": cross_source["linkage_count"],
            "cross_source_contested_types": cross_source["contested_position_types"],
        },
    )
    session.add(node)
    session.flush()


def _batch_or_error(session: Session, job: Job) -> IngestionBatch:
    raw_id = job.payload.get("batch_id")
    if not raw_id:
        msg = f"Job {job.id} payload carries no batch_id."
        raise EtlDedupJobError(msg)
    batch = session.scalar(
        select(IngestionBatch).where(
            IngestionBatch.id == UUID(str(raw_id)),
            IngestionBatch.organization_id == job.organization_id,
        )
    )
    if batch is None:
        msg = f"Job {job.id} references unknown ingestion batch {raw_id}."
        raise EtlDedupJobError(msg)
    return batch


def _bank_or_error(session: Session, batch: IngestionBatch) -> Bank:
    bank = session.scalar(
        select(Bank).where(Bank.id == batch.bank_id, Bank.organization_id == batch.organization_id)
    )
    if bank is None:
        msg = f"Ingestion batch {batch.id} references unknown bank {batch.bank_id}."
        raise EtlDedupJobError(msg)
    return bank


def _mapping_or_error(session: Session, batch: IngestionBatch) -> MappingConfig:
    if batch.mapping_config_id is None:
        msg = f"Batch {batch.id} has no mapping config to reconstruct its extraction."
        raise EtlDedupJobError(msg)
    record = session.scalar(
        select(MappingConfigRecord).where(
            MappingConfigRecord.id == batch.mapping_config_id,
            MappingConfigRecord.organization_id == batch.organization_id,
        )
    )
    if record is None:
        msg = f"Batch {batch.id} references unknown mapping config {batch.mapping_config_id}."
        raise EtlDedupJobError(msg)
    return MappingConfig.model_validate(record.config)
