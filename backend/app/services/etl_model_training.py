"""Per-tenant training for the ML-ETL models (governance: no cross-tenant spillover).

Each bank's counterparty matcher and anomaly detector are trained on THAT bank's own
canonical data and persisted under ``artifacts/etl_models/{org}/{bank}/``. A model
trained for one bank is never trained on, or served to, another. A bank with too little
canonical data yet is skipped (its ingestion keeps using the deterministic fallback,
which only ever sees that bank's own batch — per-tenant too).

Call :func:`train_bank_etl_models` after a bank has ingested canonical data (an onboarding
step or a scheduled retrain), mirroring the behavioral-model retrain seam.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.ingestion.contracts import RawRecord
from app.etl import model_loading
from app.etl.models.anomaly_detection_model import training as anomaly_training
from app.etl.models.counterparty_matching_model import training as counterparty_training
from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
)

logger = logging.getLogger(__name__)

_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")
# Below these a per-tenant model would overfit a handful of rows; the bank keeps
# using the deterministic fallback until it has ingested more of its own data.
_MIN_COUNTERPARTIES = 10
_MIN_POSITIONS = 20


def _counterparty_rows(db: Session, ctx: TenantContext, bank_id: UUID) -> list[dict[str, str]]:
    counterparties = db.scalars(
        select(CanonicalCounterparty).where(
            CanonicalCounterparty.organization_id == ctx.organization_id,
            CanonicalCounterparty.bank_id == bank_id,
            CanonicalCounterparty.superseded_by.is_(None),
            CanonicalCounterparty.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
    ).all()
    rows: list[dict[str, str]] = []
    for cp in counterparties:
        if not cp.name:
            continue
        row = {"counterparty_id": cp.source_reference, "counterparty_name": cp.name}
        if cp.counterparty_type:
            row["counterparty_type"] = cp.counterparty_type
        if cp.country_code:
            row["country"] = cp.country_code
        rows.append(row)
    return rows


def _position_records(db: Session, ctx: TenantContext, bank_id: UUID) -> list[RawRecord]:
    pairs = db.execute(
        select(CanonicalPosition, CanonicalPositionSnapshot)
        .join(
            CanonicalPositionSnapshot,
            CanonicalPositionSnapshot.position_id == CanonicalPosition.id,
        )
        .where(
            CanonicalPosition.organization_id == ctx.organization_id,
            CanonicalPosition.bank_id == bank_id,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
    ).all()
    records: list[RawRecord] = []
    for position, snapshot in pairs:
        data: dict[str, Any] = {
            "source_reference": position.source_reference,
            "position_type": position.position_type,
            "currency": position.currency,
        }
        if snapshot.balance is not None:
            data["balance_ghs"] = str(snapshot.balance)
        if snapshot.interest_rate is not None:
            data["interest_rate"] = str(snapshot.interest_rate)
        if snapshot.rate_type:
            data["rate_type"] = snapshot.rate_type
        if snapshot.contractual_maturity is not None:
            data["contractual_maturity"] = snapshot.contractual_maturity.isoformat()
        records.append(
            RawRecord(
                entity_type="position",
                source_locator=position.source_reference,
                data=data,
            )
        )
    return records


def train_bank_etl_models(db: Session, ctx: TenantContext, bank_id: UUID) -> dict[str, Any]:
    """Train + persist this bank's counterparty and anomaly models on its own data.

    Returns a per-model summary; a model with too little data is reported as skipped
    and left absent (the ingestion pipeline falls back to the deterministic path).
    """
    org_id = ctx.organization_id
    summary: dict[str, Any] = {"organization_id": str(org_id), "bank_id": str(bank_id)}

    rows = _counterparty_rows(db, ctx, bank_id)
    if len(rows) >= _MIN_COUNTERPARTIES:
        report = counterparty_training.train_and_validate(
            rows=rows, training_data_ref=f"canonical:{org_id}:{bank_id}:counterparties"
        )
        path = report.model.save(model_loading.counterparty_artifact_path(org_id, bank_id))
        summary["counterparty_matching_model"] = {
            "artifact": str(path), "trained_on": len(rows), "metrics": report.metrics,
        }
    else:
        summary["counterparty_matching_model"] = {"skipped": f"only {len(rows)} counterparties"}

    records = _position_records(db, ctx, bank_id)
    if len(records) >= _MIN_POSITIONS:
        report = anomaly_training.train_and_validate(
            records=records, training_data_ref=f"canonical:{org_id}:{bank_id}:positions"
        )
        path = report.model.save(model_loading.anomaly_artifact_path(org_id, bank_id))
        summary["anomaly_detection_model"] = {
            "artifact": str(path),
            "trained_on": len(records),
            "injected_recall": report.injected_recall,
            "clean_false_positive_rate": report.clean_false_positive_rate,
        }
    else:
        summary["anomaly_detection_model"] = {"skipped": f"only {len(records)} positions"}

    model_loading.reset_cache()  # so the next ingestion loads the freshly trained models
    logger.info("Trained per-tenant ML-ETL models for bank %s: %s", bank_id, summary)
    return summary
