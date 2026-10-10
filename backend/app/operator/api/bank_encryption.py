from __future__ import annotations

from fastapi import APIRouter

from app.core.key_management.schemas import BankKeyConnect, BankKeyRead, BankKeyRotate
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
