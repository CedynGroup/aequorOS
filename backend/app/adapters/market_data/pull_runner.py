"""Shared pull persistence spine for market data adapters.

Every concrete ``MarketDataAdapter.pull`` delegates here so there is exactly
one writer of market-data canonical state (mirroring how ``ingestion.py`` is
the sole writer for position data). ``execute_pull`` performs the §3.3 steps
that come after vendor extraction:

  raw persistence -> translation hand-off -> validation -> canonical
  persistence (with supersession) -> quota accounting -> cache update ->
  lineage -> live-pipeline trigger

Adapters supply an ``extract`` callable that turns one ``DataScope`` into a
raw vendor payload plus a translated :class:`MarketDataBundle`; everything
vendor-specific stays behind that callable. A per-scope failure surfaces as a
bank-facing error string and does not abort the other scopes (partial success
is the contract, matching ``data_engine.md`` §5.4).

Auto-recalculation: an accepted pull enqueues the same debounced
``pipeline_refresh`` job ingestion uses, so dependent modules (IRR, FX, FTP,
liquidity, capital) recompute from the fresh market data without operator
action, and official-run freshness comparison flags staleness downstream.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.market_data.base import MarketDataPullResult
from app.adapters.market_data.cache import write_cache_entry
from app.adapters.market_data.errors import MarketDataError
from app.adapters.market_data.quota_tracker import record_consumption
from app.adapters.market_data.scope_taxonomy import DataScope
from app.core.config import get_settings
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.models import (
    Bank,
    CanonicalCounterpartyRating,
    CanonicalFxRate,
    CanonicalMarketIndex,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    IngestionBatch,
    LineageRecord,
)
from app.services import job_queue
from app.storage.client import ObjectMetadata, StorageLocation
from app.storage.factory import get_storage_client

logger = logging.getLogger(__name__)

_VENDOR_SOURCE_SYSTEMS = {
    "bloomberg": "BLOOMBERG",
    "refinitiv": "REFINITIV",
    "manual": "MANUAL_UPLOAD",
    "manual_upload": "MANUAL_UPLOAD",
}
_RATE_LOWER = Decimal("-1")
_RATE_UPPER = Decimal("1")


# ---------------------------------------------------------------------------
# Translated record shapes adapters hand to the runner (vendor-agnostic).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CurvePoint:
    tenor_months: int
    rate: Decimal  # decimal fraction (0.245, never 24.5) per data_engine.md §4.6


@dataclass(frozen=True)
class CurveRecord:
    currency: str
    curve_name: str
    curve_type: str
    source_reference: str
    points: tuple[CurvePoint, ...]


@dataclass(frozen=True)
class FxRateRecord:
    base_currency: str
    quote_currency: str
    rate_type: str  # 'spot' | 'forward'
    tenor_months: int | None  # None iff spot
    rate: Decimal
    source_reference: str


@dataclass(frozen=True)
class IndexRecord:
    index_code: str
    value: Decimal
    scenario: str
    horizon_months: int | None
    source_reference: str


@dataclass(frozen=True)
class RatingRecord:
    issuer: str
    agency: str
    rating: str
    watch_status: str | None
    rating_date: date
    source_reference: str


@dataclass
class MarketDataBundle:
    """Everything one scope translated into, ready for canonical persistence."""

    curves: list[CurveRecord] = field(default_factory=list)
    fx_rates: list[FxRateRecord] = field(default_factory=list)
    indices: list[IndexRecord] = field(default_factory=list)
    ratings: list[RatingRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sample_values: dict[str, str] = field(default_factory=dict)

    @property
    def record_count(self) -> int:
        points = sum(len(curve.points) for curve in self.curves)
        return len(self.curves) + points + len(self.fx_rates) + len(self.indices) + len(
            self.ratings
        )


@dataclass(frozen=True)
class ScopeExtraction:
    """One scope's raw vendor payload plus its translated bundle."""

    raw_payload: dict[str, Any]
    bundle: MarketDataBundle


ExtractFn = Callable[[DataScope], ScopeExtraction]


# ---------------------------------------------------------------------------
# The runner.
# ---------------------------------------------------------------------------


def execute_pull(  # noqa: PLR0913 - one call carries the full pull context
    db: Session,
    *,
    organization_id: UUID,
    bank: Bank,
    bank_slug: str,
    vendor: str,
    adapter_version: str,
    scopes: Sequence[DataScope],
    as_of_date: date,
    extract: ExtractFn,
    quota_units: int,
    actor_user_id: UUID | None = None,
) -> MarketDataPullResult:
    """Run the persistence half of a market data pull and commit it.

    ``extract`` is called once per scope; a :class:`MarketDataError` from it is
    recorded as that scope's bank-facing error while other scopes proceed. The
    batch is committed in a terminal state; the caller receives the spec §4.1
    ``MarketDataPullResult``.
    """
    source_system = _VENDOR_SOURCE_SYSTEMS[vendor]
    now = utc_now()
    batch = IngestionBatch(
        organization_id=organization_id,
        bank_id=bank.id,
        source_system=source_system,
        adapter_version=adapter_version,
        extraction_mode="full",
        status="extracting",
        as_of_date=as_of_date,
        started_at=now,
        created_by=actor_user_id,
    )
    db.add(batch)
    db.flush()

    extract_node = _lineage(
        db,
        organization_id,
        batch,
        operation_type="ADAPTER_EXTRACT",
        operation_ref=f"{vendor}_v{adapter_version}/market_data",
        inputs=(),
        details={"scopes": [scope.value for scope in scopes]},
    )

    warnings: list[str] = []
    errors: list[str] = []
    raw_prefix = f"market_data/{vendor}/{as_of_date.isoformat()}/{batch.id}"
    storage = get_storage_client()
    extractions = _extract_scopes(
        scopes,
        extract,
        vendor=vendor,
        bank_slug=bank_slug,
        batch=batch,
        extract_node_id=extract_node.id,
        source_system=source_system,
        as_of_date=as_of_date,
        raw_prefix=raw_prefix,
        storage=storage,
        warnings=warnings,
        errors=errors,
    )

    translate_node = _lineage(
        db,
        organization_id,
        batch,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref=f"{vendor}_v{adapter_version}/translators",
        inputs=(extract_node.id,),
        details={"scopes_translated": [scope.value for scope in extractions]},
    )
    validation_node = _lineage(
        db,
        organization_id,
        batch,
        operation_type="VALIDATION",
        operation_ref="market_data_rules_v1",
        inputs=(translate_node.id,),
        details={},
    )

    accepted = 0
    rejected_records: list[dict[str, str]] = []
    for scope, extraction in extractions.items():
        accepted += _persist_bundle(
            db,
            organization_id=organization_id,
            bank_id=bank.id,
            batch=batch,
            lineage_id=validation_node.id,
            source_system=source_system,
            as_of_date=as_of_date,
            bundle=extraction.bundle,
            rejected=rejected_records,
            scope=scope,
        )

    validation_node.details = {
        "records_accepted": accepted,
        "records_rejected": len(rejected_records),
        "failures": rejected_records[:200],
    }

    _finalize_batch(
        batch,
        extractions=extractions,
        accepted=accepted,
        rejected_records=rejected_records,
        warnings=warnings,
        errors=errors,
        raw_prefix=raw_prefix,
    )

    quota_consumed = 0
    if vendor not in ("manual", "manual_upload") and extractions:
        # Manual pulls consume no vendor quota (§8.3).
        quota_consumed = quota_units
        record_consumption(
            db,
            organization_id=organization_id,
            bank_id=bank.id,
            vendor=vendor,
            units=quota_consumed,
            when=now,
        )

    # Cache update is derived state: best-effort, never fails the pull.
    for scope, extraction in extractions.items():
        try:
            write_cache_entry(
                bank_slug,
                scope,
                as_of_date=as_of_date,
                values=extraction.bundle.sample_values,
                pulled_at=now,
                source_batch_id=str(batch.id),
                vendor=vendor,
                client=storage,
            )
        except Exception:  # noqa: BLE001 - cache is reconstructable
            warnings.append(f"{scope.value}: cache update failed (non-fatal)")
            logger.exception("Cache update failed for %s/%s", vendor, scope.value)

    if batch.status in ("accepted", "accepted_with_warnings") and accepted:
        settings = get_settings()
        job_queue.enqueue(
            db,
            organization_id,
            "pipeline_refresh",
            bank_id=bank.id,
            payload={"as_of_date": as_of_date.isoformat()},
            run_after=now + timedelta(seconds=settings.worker.pipeline_debounce_seconds),
            coalesce_key=f"refresh:{bank.id}:{as_of_date.isoformat()}",
        )

    db.commit()
    return MarketDataPullResult(
        batch_id=str(batch.id),
        institution_id=str(bank.id),
        scopes_pulled=[scope for scope in scopes if scope in extractions],
        canonical_records_produced=accepted,
        quota_consumed=quota_consumed,
        raw_storage_location=f"raw://{bank_slug}/{raw_prefix}",
        canonical_storage_location="canonical-db://market_data",
        pulled_at=now,
        warnings=warnings,
        errors=errors,
    )


def _extract_scopes(  # noqa: PLR0913 - internal seam of execute_pull
    scopes: Sequence[DataScope],
    extract: ExtractFn,
    *,
    vendor: str,
    bank_slug: str,
    batch: IngestionBatch,
    extract_node_id: UUID,
    source_system: str,
    as_of_date: date,
    raw_prefix: str,
    storage: Any,
    warnings: list[str],
    errors: list[str],
) -> dict[DataScope, ScopeExtraction]:
    """Extract each scope and preserve its raw vendor response.

    Raw tier: full vendor response preserved per §13.3 — audit evidence and
    re-translation source. A raw-write failure fails the pull; canonical rows
    must never exist without their raw counterpart. A per-scope extract
    failure records a bank-facing error and the other scopes proceed.
    """
    extractions: dict[DataScope, ScopeExtraction] = {}
    for scope in scopes:
        try:
            extraction = extract(scope)
        except MarketDataError as exc:
            errors.append(f"{scope.value}: {exc.bank_facing.message}")
            logger.warning(
                "Market data extract failed for %s/%s: %s",
                vendor,
                scope.value,
                exc.internal_detail,
            )
            continue
        extractions[scope] = extraction
        warnings.extend(f"{scope.value}: {note}" for note in extraction.bundle.warnings)
        body = json.dumps(extraction.raw_payload, sort_keys=True, default=str).encode("utf-8")
        location = StorageLocation(
            institution_slug=bank_slug, tier="raw", object_path=f"{raw_prefix}/{scope.value}.json"
        )
        storage.write(
            location,
            io.BytesIO(body),
            ObjectMetadata(
                institution_slug=bank_slug,
                tier="raw",
                checksum_sha256=hashlib.sha256(body).hexdigest(),
                written_at=datetime.now(UTC),
                written_by=f"market_data/{vendor}",
                as_of_date=as_of_date.isoformat(),
                ingestion_batch_id=str(batch.id),
                lineage_node_id=str(extract_node_id),
                source_system=source_system,
                source_reference=scope.value,
            ),
            content_type="application/json",
        )
    return extractions


def _finalize_batch(  # noqa: PLR0913 - internal seam of execute_pull
    batch: IngestionBatch,
    *,
    extractions: dict[DataScope, ScopeExtraction],
    accepted: int,
    rejected_records: list[dict[str, str]],
    warnings: list[str],
    errors: list[str],
    raw_prefix: str,
) -> None:
    batch.records_extracted = sum(e.bundle.record_count for e in extractions.values())
    batch.records_translated = batch.records_extracted
    batch.records_accepted = accepted
    batch.records_error = len(rejected_records)
    batch.validation_report = {
        "summary": {
            "records_extracted": batch.records_extracted,
            "records_accepted": accepted,
            "records_error": len(rejected_records),
            "scope_errors": errors,
        },
        "failures": rejected_records[:200],
    }
    if accepted == 0 and (errors or rejected_records):
        batch.status = "rejected"
    elif warnings or errors or rejected_records:
        batch.status = "accepted_with_warnings"
    else:
        batch.status = "accepted"
    batch.raw_artifact_path = raw_prefix
    batch.completed_at = utc_now()


# ---------------------------------------------------------------------------
# Canonical persistence with supersession.
# ---------------------------------------------------------------------------


def _persist_bundle(  # noqa: PLR0913 - full persistence scope in one call
    db: Session,
    *,
    organization_id: UUID,
    bank_id: UUID,
    batch: IngestionBatch,
    lineage_id: UUID,
    source_system: str,
    as_of_date: date,
    bundle: MarketDataBundle,
    rejected: list[dict[str, str]],
    scope: DataScope,
) -> int:
    meta: dict[str, Any] = {
        "organization_id": organization_id,
        "bank_id": bank_id,
        "as_of_date": as_of_date,
        "source_system": source_system,
        "ingestion_batch_id": batch.id,
        "validation_status": "accepted",
        "lineage_id": lineage_id,
        "created_by": batch.created_by,
    }
    accepted = 0

    for curve in bundle.curves:
        problems = _validate_curve(curve)
        if problems:
            rejected.extend(
                {"scope": scope.value, "record": curve.source_reference, "detail": p}
                for p in problems
            )
            continue
        row = CanonicalYieldCurve(
            **meta,
            source_reference=curve.source_reference,
            currency=curve.currency,
            curve_name=curve.curve_name,
            curve_type=curve.curve_type,
        )
        _supersede(
            db,
            CanonicalYieldCurve,
            row,
            (
                CanonicalYieldCurve.currency == curve.currency,
                CanonicalYieldCurve.curve_name == curve.curve_name,
            ),
            organization_id=organization_id,
            bank_id=bank_id,
            as_of_date=as_of_date,
        )
        accepted += 1
        for point in curve.points:
            db.add(
                CanonicalYieldCurvePoint(
                    **meta,
                    source_reference=f"{curve.source_reference}/{point.tenor_months}m",
                    yield_curve_id=row.id,
                    tenor_months=point.tenor_months,
                    rate=point.rate,
                )
            )
            accepted += 1

    for fx in bundle.fx_rates:
        problems = _validate_fx(fx)
        if problems:
            rejected.extend(
                {"scope": scope.value, "record": fx.source_reference, "detail": p}
                for p in problems
            )
            continue
        row = CanonicalFxRate(
            **meta,
            source_reference=fx.source_reference,
            base_currency=fx.base_currency,
            quote_currency=fx.quote_currency,
            rate_type=fx.rate_type,
            tenor_months=fx.tenor_months,
            rate=fx.rate,
        )
        _supersede(
            db,
            CanonicalFxRate,
            row,
            (
                CanonicalFxRate.base_currency == fx.base_currency,
                CanonicalFxRate.quote_currency == fx.quote_currency,
                CanonicalFxRate.rate_type == fx.rate_type,
                CanonicalFxRate.tenor_months.is_(None)
                if fx.tenor_months is None
                else CanonicalFxRate.tenor_months == fx.tenor_months,
            ),
            organization_id=organization_id,
            bank_id=bank_id,
            as_of_date=as_of_date,
        )
        accepted += 1

    for index in bundle.indices:
        row = CanonicalMarketIndex(
            **meta,
            source_reference=index.source_reference,
            index_code=index.index_code,
            value=index.value,
            scenario=index.scenario,
            horizon_months=index.horizon_months,
        )
        _supersede(
            db,
            CanonicalMarketIndex,
            row,
            (
                CanonicalMarketIndex.index_code == index.index_code,
                CanonicalMarketIndex.scenario == index.scenario,
                CanonicalMarketIndex.horizon_months.is_(None)
                if index.horizon_months is None
                else CanonicalMarketIndex.horizon_months == index.horizon_months,
            ),
            organization_id=organization_id,
            bank_id=bank_id,
            as_of_date=as_of_date,
        )
        accepted += 1

    for rating in bundle.ratings:
        row = CanonicalCounterpartyRating(
            **meta,
            source_reference=rating.source_reference,
            issuer=rating.issuer,
            agency=rating.agency,
            rating=rating.rating,
            watch_status=rating.watch_status,
            rating_date=rating.rating_date,
        )
        _supersede(
            db,
            CanonicalCounterpartyRating,
            row,
            (
                CanonicalCounterpartyRating.issuer == rating.issuer,
                CanonicalCounterpartyRating.agency == rating.agency,
            ),
            organization_id=organization_id,
            bank_id=bank_id,
            as_of_date=as_of_date,
        )
        accepted += 1

    return accepted


def _supersede(  # noqa: PLR0913 - natural-key supersession needs full scope
    db: Session,
    model: type,
    new_row: Any,
    key_clauses: tuple[Any, ...],
    *,
    organization_id: UUID,
    bank_id: UUID,
    as_of_date: date,
) -> None:
    """Idempotent re-pull semantics per §4.3: supersede, never duplicate.

    Prior current-generation rows for the same natural key are marked
    ``superseded_by`` the new row. Column defaults (``new_uuid7``) fire at
    flush time, so the new row's id is minted eagerly here — stamping the
    olds with a ``None`` id would leave them in the partial unique index and
    the insert would collide. The old rows are flushed out of the index
    before the new row is inserted, so re-running the same pull yields
    exactly one current record.
    """
    olds = list(
        db.scalars(
            select(model).where(
                model.organization_id == organization_id,
                model.bank_id == bank_id,
                model.as_of_date == as_of_date,
                model.superseded_by.is_(None),
                *key_clauses,
            )
        )
    )
    if olds and new_row.id is None:
        new_row.id = new_uuid7()
    for old in olds:
        old.superseded_by = new_row.id
    if olds:
        db.flush()
    db.add(new_row)
    db.flush()


# ---------------------------------------------------------------------------
# Business-rule validation (data_engine.md §6 category 2, market data rules).
# ---------------------------------------------------------------------------


def _validate_curve(curve: CurveRecord) -> list[str]:
    problems: list[str] = []
    if not curve.points:
        problems.append("curve has no points")
    tenors = [point.tenor_months for point in curve.points]
    if len(set(tenors)) != len(tenors):
        problems.append("duplicate tenors on curve")
    for point in curve.points:
        if point.tenor_months <= 0:
            problems.append(f"tenor {point.tenor_months} must be positive")
        if not (_RATE_LOWER <= point.rate <= _RATE_UPPER):
            problems.append(
                f"rate {point.rate} at {point.tenor_months}m outside [-1, 1] "
                "(rates are decimal fractions)"
            )
    return problems


def _validate_fx(fx: FxRateRecord) -> list[str]:
    problems: list[str] = []
    if fx.rate <= 0:
        problems.append(f"fx rate {fx.rate} must be positive")
    if (fx.rate_type == "spot") != (fx.tenor_months is None):
        problems.append("spot rates carry no tenor; forward rates require one")
    return problems


def _lineage(  # noqa: PLR0913 - mirrors ingestion's lineage writer
    db: Session,
    organization_id: UUID,
    batch: IngestionBatch,
    *,
    operation_type: str,
    operation_ref: str,
    inputs: tuple[UUID, ...],
    details: dict[str, Any],
) -> LineageRecord:
    node = LineageRecord(
        organization_id=organization_id,
        ingestion_batch_id=batch.id,
        operation_type=operation_type,
        operation_ref=operation_ref,
        input_lineage_ids=[str(input_id) for input_id in inputs],
        details=details,
    )
    db.add(node)
    db.flush()
    return node
