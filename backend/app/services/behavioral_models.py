"""Per-tenant behavioral-model service: lazy-train + cache + apply-as-assumptions.

Mirrors ``cashflow_forecast`` but keyed by ``(org, bank, model)``: each model
trains on the bank's own canonical history on first request, caches the result
in memory (and as ``estimates.json`` for restart reuse), and can be re-run on
demand. ``apply_estimates`` writes reviewed estimates as a new accepted
``behavioral_assumptions`` batch — preserving the OTHER models' current rows,
since all three share that one reference dataset — which the ALM engines then
consume unchanged on the next recompute.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.ml.behavioral import deposit_stability, nmd_duration, prepayment
from app.ml.behavioral.config import (
    ASSUMPTION_TYPE,
    MODEL_SLUGS,
    BehavioralTrainingConfig,
    ModelResult,
)
from app.ml.behavioral.history import available_as_of_dates
from app.models import Bank
from app.models.canonical import CanonicalReferenceRow
from app.models.ingestion import IngestionBatch, LineageRecord

_MODULES = {
    "nmd-duration": nmd_duration,
    "prepayment": prepayment,
    "deposit-stability": deposit_stability,
}
_DATASET_KIND = "behavioral_assumptions"

_cache: dict[tuple[UUID, UUID, str], ModelResult] = {}
_cache_lock = threading.Lock()
_key_locks: dict[tuple[UUID, UUID, str], threading.Lock] = {}


def reset_cache() -> None:
    """Test hook: drop the in-memory cache."""
    with _cache_lock:
        _cache.clear()
        _key_locks.clear()


def _validate_model(model: str) -> None:
    if model not in MODEL_SLUGS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown behavioral model '{model}'. Expected one of {list(MODEL_SLUGS)}.",
        )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _artifacts_path(org_id: UUID, bank_id: UUID, model: str) -> Path:
    root = get_settings().behavioral.artifacts_dir
    return Path(root) / model / str(org_id) / str(bank_id) / "estimates.json"


def _key_lock(key: tuple[UUID, UUID, str]) -> threading.Lock:
    with _cache_lock:
        return _key_locks.setdefault(key, threading.Lock())


def get_estimates(
    db: Session, ctx: TenantContext, bank_id: UUID, model: str, *, refresh: bool = False,
) -> ModelResult:
    """Train-on-first-request (or reuse cached/persisted) per-product estimates."""
    _validate_model(model)
    _get_bank_or_404(db, ctx, bank_id)
    key = (ctx.organization_id, bank_id, model)

    if not refresh:
        with _cache_lock:
            cached = _cache.get(key)
        if cached is not None:
            return cached
        loaded = _load_artifact(key)
        if loaded is not None:
            with _cache_lock:
                _cache[key] = loaded
            return loaded

    with _key_lock(key):
        if not refresh:
            with _cache_lock:
                cached = _cache.get(key)
            if cached is not None:
                return cached
        result = _compute(db, ctx, bank_id, model)
        with _cache_lock:
            _cache[key] = result
        _save_artifact(key, result)
        return result


def _compute(db: Session, ctx: TenantContext, bank_id: UUID, model: str) -> ModelResult:
    cfg = BehavioralTrainingConfig.from_settings(get_settings().behavioral)
    dates = available_as_of_dates(db, ctx, bank_id)
    if not dates:
        # No canonical history at all — return an empty baseline result.
        from app.ml.behavioral.config import MODEL_VERSIONS, Accuracy  # noqa: PLC0415

        return ModelResult(
            model_id=MODEL_VERSIONS[model], model_version=MODEL_VERSIONS[model],
            method="baseline", as_of_date=None,
            accuracy=Accuracy(cv_rmse=None, cv_mae=None, sample_count=0, month_coverage=0,
                              method="baseline"),
            products=[],
        )
    as_of = dates[-1]
    result = _MODULES[model].estimate(db, ctx, bank_id, as_of, cfg)
    return dataclasses.replace(result, as_of_date=as_of.isoformat())


# --------------------------------------------------------------------------
# Apply-as-assumptions
# --------------------------------------------------------------------------

def apply_estimates(  # noqa: PLR0913
    db: Session, ctx: TenantContext, bank_id: UUID, model: str, rows: list[dict],
    *, commit: bool = True,
) -> dict:
    """Write reviewed estimates as a new accepted ``behavioral_assumptions`` batch.

    Preserves the current rows of the OTHER assumption types (shared dataset) and
    replaces only THIS model's assumption_type. Returns the applied-batch summary.
    """
    _validate_model(model)
    bank = _get_bank_or_404(db, ctx, bank_id)
    this_type = ASSUMPTION_TYPE[model]
    actor = ctx.actor_user_id
    dates = available_as_of_dates(db, ctx, bank_id)
    as_of_date = dates[-1] if dates else utc_now().date()

    from app.ml.behavioral.config import MODEL_VERSIONS  # noqa: PLC0415

    kept = [
        p for p in _latest_behavioral_payloads(db, ctx, bank_id)
        if str(p.get("assumption_type")) != this_type
    ]
    reviewed_at = utc_now().isoformat()
    new_rows = [
        {
            "assumption_type": this_type,
            "product_code": r["product_code"],
            "value": r["value"],
            "unit": r.get("unit", ""),
            "provenance": {
                "source": "ML_MODEL",
                "model_id": model,
                "model_version": MODEL_VERSIONS[model],
                "confidence": r.get("confidence"),
                "reviewed_by": str(actor) if actor else None,
                "reviewed_at": reviewed_at,
            },
        }
        for r in rows
    ]

    batch_id = new_uuid7()
    lineage_id = new_uuid7()
    db.add(IngestionBatch(
        id=batch_id, organization_id=ctx.organization_id, bank_id=bank.id,
        source_system="API_PUSH", adapter_version="behavioral_ml_v1", extraction_mode="full",
        status="accepted", as_of_date=as_of_date, created_by=actor,
    ))
    db.add(LineageRecord(
        id=lineage_id, organization_id=ctx.organization_id, ingestion_batch_id=batch_id,
        operation_type="ENRICHMENT",
        operation_ref=f"behavioral_ml/{model}/{as_of_date.isoformat()}",
    ))
    db.flush()  # batch + lineage must exist before their reference rows (composite FK)
    all_payloads = kept + new_rows
    for idx, payload in enumerate(all_payloads, start=1):
        db.add(CanonicalReferenceRow(
            id=new_uuid7(), organization_id=ctx.organization_id, bank_id=bank.id,
            ingestion_batch_id=batch_id, as_of_date=as_of_date, dataset_kind=_DATASET_KIND,
            row_index=idx, payload=payload,
            source_reference=f"behavioral_ml/{model}/{as_of_date.isoformat()}#{idx}",
            lineage_id=lineage_id,
        ))
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "ingestion_batch_id": str(batch_id),
        "as_of_date": as_of_date.isoformat(),
        "applied_rows": len(new_rows),
        "total_rows": len(all_payloads),
    }


def _latest_behavioral_payloads(db: Session, ctx: TenantContext, bank_id: UUID) -> list[dict]:
    """Payloads of the current latest accepted behavioral_assumptions batch."""
    batch_rows = db.execute(
        select(
            CanonicalReferenceRow.ingestion_batch_id,
            func.max(CanonicalReferenceRow.created_at),
        )
        .where(
            CanonicalReferenceRow.organization_id == ctx.organization_id,
            CanonicalReferenceRow.bank_id == bank_id,
            CanonicalReferenceRow.dataset_kind == _DATASET_KIND,
        )
        .group_by(CanonicalReferenceRow.ingestion_batch_id)
    ).all()
    if not batch_rows:
        return []
    # newest created_at, UUIDv7 text tie-break (matches _load_canonical)
    winner = max(batch_rows, key=lambda r: (r[1], str(r[0])))[0]
    return list(db.scalars(
        select(CanonicalReferenceRow.payload)
        .where(
            CanonicalReferenceRow.organization_id == ctx.organization_id,
            CanonicalReferenceRow.bank_id == bank_id,
            CanonicalReferenceRow.dataset_kind == _DATASET_KIND,
            CanonicalReferenceRow.ingestion_batch_id == winner,
        )
        .order_by(CanonicalReferenceRow.row_index)
    ).all())


# --------------------------------------------------------------------------
# Artifact persistence (the ModelResult JSON is the artifact)
# --------------------------------------------------------------------------

def _save_artifact(key: tuple[UUID, UUID, str], result: ModelResult) -> None:
    path = _artifacts_path(*key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dataclasses.asdict(result)))
    except OSError:
        pass  # a non-writable artifacts dir must not break serving


def _load_artifact(key: tuple[UUID, UUID, str]) -> ModelResult | None:
    path = _artifacts_path(*key)
    if not path.exists():
        return None
    try:
        return _result_from_dict(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError):
        return None


def _result_from_dict(d: dict) -> ModelResult:
    from app.ml.behavioral.config import Accuracy, ProductEstimate  # noqa: PLC0415

    return ModelResult(
        model_id=d["model_id"], model_version=d["model_version"], method=d["method"],
        as_of_date=d.get("as_of_date"),
        accuracy=Accuracy(**d["accuracy"]),
        products=[ProductEstimate(**p) for p in d["products"]],
    )
