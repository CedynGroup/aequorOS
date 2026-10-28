from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import TypeAdapter
from sqlalchemy import select

from app.core.key_management import reencryption
from app.core.key_management.schemas import (
    BankKeyConnect,
    BankKeyRead,
    BankKeyRotate,
    ReencryptionRead,
)
from app.models.job import Job
from app.operator.deps import OperatorAdmin, OperatorDb, record_operator_action
from app.operator.inspection import require_active_inspection
from app.operator.services import bank_encryption
from app.services.public_ids import normalize_public_id

router = APIRouter(
    prefix="/tenants/{org_id}/banks/{bank_id}/encryption-key", tags=["operator-bank-encryption"]
)


@router.get("", response_model=BankKeyRead)
def get_bank_encryption_key(
    org_id: str, bank_id: str, db: OperatorDb, operator: OperatorAdmin
) -> BankKeyRead:
    org_id = normalize_public_id(org_id)
    bank_id = normalize_public_id(bank_id)
    inspection = require_active_inspection(db, operator, org_id)
    result = bank_encryption.get_key(db, org_id, bank_id)
    record_operator_action(
        db,
        operator,
        action="bank_key.read",
        target_org=org_id,
        detail={
            "bank_id": bank_id,
            "key_id": result.key_id,
            "status": result.status.value,
            "session_id": str(inspection.id),
        },
    )
    db.commit()
    return result


@router.put("", response_model=BankKeyRead)
def connect_bank_encryption_key(
    org_id: str,
    bank_id: str,
    payload: BankKeyConnect,
    db: OperatorDb,
    operator: OperatorAdmin,
) -> BankKeyRead:
    org_id = normalize_public_id(org_id)
    bank_id = normalize_public_id(bank_id)
    inspection = require_active_inspection(db, operator, org_id)
    result = bank_encryption.connect_key(db, org_id, bank_id, payload)
    record_operator_action(
        db,
        operator,
        action="bank_key.connected",
        target_org=org_id,
        detail={
            "bank_id": bank_id,
            "key_id": result.key_id,
            "status": result.status.value,
            "session_id": str(inspection.id),
        },
    )
    db.commit()
    return result


@router.post("/rotate", response_model=BankKeyRead)
def rotate_bank_encryption_key(
    org_id: str,
    bank_id: str,
    payload: BankKeyRotate,
    db: OperatorDb,
    operator: OperatorAdmin,
) -> BankKeyRead:
    org_id = normalize_public_id(org_id)
    bank_id = normalize_public_id(bank_id)
    inspection = require_active_inspection(db, operator, org_id)
    rotation = bank_encryption.rotate_key(db, org_id, bank_id, payload)
    result = rotation.key
    record_operator_action(
        db,
        operator,
        action="bank_key.rotated",
        target_org=org_id,
        detail={
            "bank_id": bank_id,
            "key_id": result.key_id,
            "old_key_id": rotation.old_key_id,
            "rewrapped_objects": rotation.rewrapped_objects,
            "reason": payload.reason,
            "session_id": str(inspection.id),
        },
    )
    db.commit()
    return result


@router.post("/retire", status_code=204)
def authorize_bank_key_retirement(
    org_id: str,
    bank_id: str,
    payload: BankKeyRotate,
    db: OperatorDb,
    operator: OperatorAdmin,
) -> None:
    org_id = normalize_public_id(org_id)
    bank_id = normalize_public_id(bank_id)
    inspection = require_active_inspection(db, operator, org_id)
    bank_encryption.authorize_retirement(db, org_id, bank_id, payload)
    record_operator_action(
        db,
        operator,
        action="bank_key.retirement_authorized",
        target_org=org_id,
        detail={
            "bank_id": bank_id,
            "key_id": payload.key_id,
            "reason": payload.reason,
            "session_id": str(inspection.id),
        },
    )
    db.commit()


@router.post("/reencrypt", response_model=ReencryptionRead, status_code=202)
def start_bank_reencryption(
    org_id: str,
    bank_id: str,
    db: OperatorDb,
    operator: OperatorAdmin,
) -> ReencryptionRead:
    org_id, bank_id = normalize_public_id(org_id), normalize_public_id(bank_id)
    inspection = require_active_inspection(db, operator, org_id)
    _ = bank_encryption.get_key(db, org_id, bank_id)
    job = reencryption.enqueue(db, org_id, bank_id)
    record_operator_action(
        db,
        operator,
        action="bank_key.reencryption_requested",
        target_org=org_id,
        detail={"bank_id": bank_id, "job_id": str(job.id), "session_id": str(inspection.id)},
    )
    db.commit()
    return ReencryptionRead(
        job_id=job.id,
        status=job.status,
        progress=TypeAdapter(dict[str, object]).validate_python(job.progress),
    )


@router.get("/reencrypt", response_model=ReencryptionRead)
def get_bank_reencryption(
    org_id: str,
    bank_id: str,
    db: OperatorDb,
    operator: OperatorAdmin,
) -> ReencryptionRead:
    org_id, bank_id = normalize_public_id(org_id), normalize_public_id(bank_id)
    inspection = require_active_inspection(db, operator, org_id)
    _ = bank_encryption.get_key(db, org_id, bank_id)
    job = db.scalar(
        select(Job)
        .where(
            Job.organization_id == org_id,
            Job.bank_id == bank_id,
            Job.job_type == reencryption.JOB_TYPE,
        )
        .order_by(Job.queued_at.desc(), Job.id.desc())
        .limit(1)
    )
    if job is None:
        raise HTTPException(status_code=404, detail="No re-encryption job exists for this bank.")
    record_operator_action(
        db,
        operator,
        action="bank_key.reencryption_read",
        target_org=org_id,
        detail={"bank_id": bank_id, "job_id": str(job.id), "session_id": str(inspection.id)},
    )
    db.commit()
    return ReencryptionRead(
        job_id=job.id,
        status=job.status,
        progress=TypeAdapter(dict[str, object]).validate_python(job.progress),
    )
