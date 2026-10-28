"""Bank key setup is restricted to complete Account administration bindings."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, TenantContext, require_account_administration
from app.core.key_management.schemas import BankKeyConnect, BankKeyRotate
from app.identity.schemas.bank_encryption import (
    BankKeyScheduleConfirm,
    BankKeyStatusRead,
    BankKeyTestRead,
)
from app.identity.service import bank_encryption

router = APIRouter(prefix="/banks/{bank_id}/encryption-key", tags=["bank-encryption"])
Admin = Annotated[TenantContext, Depends(require_account_administration)]


@router.get("", operation_id="getBankKeyStatus", response_model=BankKeyStatusRead)
def get_status(bank_id: str, db: DbSession, ctx: Admin) -> BankKeyStatusRead:
    return bank_encryption.status(db, ctx, bank_id)


@router.post("/test", operation_id="testBankKeyConnection", response_model=BankKeyTestRead)
def test_connection(
    bank_id: str, payload: BankKeyConnect, db: DbSession, ctx: Admin
) -> BankKeyTestRead:
    return bank_encryption.test_connection(db, ctx, bank_id, payload)


@router.put("", operation_id="connectBankKey", response_model=BankKeyStatusRead)
def connect(bank_id: str, payload: BankKeyConnect, db: DbSession, ctx: Admin) -> BankKeyStatusRead:
    return bank_encryption.connect(db, ctx, bank_id, payload)


@router.post("/rotate", operation_id="rotateBankKey", response_model=BankKeyStatusRead)
def rotate(bank_id: str, payload: BankKeyRotate, db: DbSession, ctx: Admin) -> BankKeyStatusRead:
    return bank_encryption.rotate(db, ctx, bank_id, payload)


@router.post(
    "/schedule-confirmation",
    operation_id="confirmBankKeyRotationSchedule",
    response_model=BankKeyStatusRead,
)
def confirm_schedule(
    bank_id: str, payload: BankKeyScheduleConfirm, db: DbSession, ctx: Admin
) -> BankKeyStatusRead:
    return bank_encryption.confirm_schedule(db, ctx, bank_id, payload)
