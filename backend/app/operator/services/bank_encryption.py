from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.key_management import registry
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.schemas import BankKeyConnect, BankKeyRead, BankKeyRotate
from app.core.key_management.types import KeyStatus, KeyUnavailableError
from app.identity.public import Bank
from app.models.operator import TenantStorage


@dataclass(frozen=True)
class RotationResult:
    key: BankKeyRead
    old_key_id: str
    rewrapped_objects: int


def _bank(db: Session, organization_id: str, bank_id: str) -> Bank:
    row = db.scalar(select(Bank).where(Bank.id == bank_id, Bank.organization_id == organization_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Bank not found in this organization.")
    return row


def _read(row: BankEncryptionKey) -> BankKeyRead:
    return BankKeyRead(
        bank_id=row.bank_id,
        provider=row.provider,
        key_id=row.key_id,
        region=row.region,
        owner_account=row.owner_account,
        status=KeyStatus(row.status),
        checked_at=row.checked_at,
    )


def _update_storage_reference(db: Session, row: BankEncryptionKey) -> None:
    storage = db.scalar(
        select(TenantStorage).where(TenantStorage.organization_id == row.organization_id)
    )
    if storage is not None and any(
        f"-{row.storage_slug}-" in bucket for bucket in storage.bucket_names
    ):
        storage.kms_key_arn = row.key_id


def connect_key(  # noqa: PLR0913 - scoped operator mutation
    db: Session,
    organization_id: str,
    bank_id: str,
    payload: BankKeyConnect,
    providers: registry.ProviderFactory | None = None,
) -> BankKeyRead:
    providers = providers or registry.provider_for
    bank = _bank(db, organization_id, bank_id)
    if (
        db.scalar(
            select(BankEncryptionKey).where(
                BankEncryptionKey.bank_id == bank_id,
                BankEncryptionKey.organization_id == organization_id,
            )
        )
        is not None
    ):
        raise HTTPException(status_code=409, detail="Use rotation to replace a connected bank key.")
    if bank.storage_slug is None:
        raise HTTPException(status_code=409, detail="Bank storage must be provisioned first.")
    try:
        row = registry.register(
            db,
            bank_id=bank_id,
            organization_id=organization_id,
            storage_slug=bank.storage_slug,
            key=payload.reference(),
            providers=providers,
        )
    except KeyUnavailableError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _update_storage_reference(db, row)
    return _read(row)


def get_key(db: Session, organization_id: str, bank_id: str) -> BankKeyRead:
    _bank(db, organization_id, bank_id)
    try:
        row = registry.scoped_key(db, bank_id=bank_id, organization_id=organization_id)
    except KeyUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _read(row)


def rotate_key(
    db: Session,
    organization_id: str,
    bank_id: str,
    payload: BankKeyRotate,
    providers: registry.ProviderFactory | None = None,
) -> RotationResult:
    providers = providers or registry.provider_for
    _bank(db, organization_id, bank_id)
    try:
        row = registry.scoped_key(db, bank_id=bank_id, organization_id=organization_id)
        old_key_id = row.key_id
        count = registry.rotate(db, row, payload.reference(), providers)
    except KeyUnavailableError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _update_storage_reference(db, row)
    return RotationResult(_read(row), old_key_id, count)


def authorize_retirement(
    db: Session,
    organization_id: str,
    bank_id: str,
    payload: BankKeyRotate,
) -> None:
    _bank(db, organization_id, bank_id)
    try:
        row = registry.scoped_key(db, bank_id=bank_id, organization_id=organization_id)
        registry.authorize_retirement(db, row, payload.reference())
    except KeyUnavailableError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
