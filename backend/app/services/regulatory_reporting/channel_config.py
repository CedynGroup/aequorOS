"""Per-bank submission-channel configuration with write-only credentials.

Follows the EncryptedDbVault pattern from
``app/adapters/market_data/credential_manager.py``: credential material is
AES-256-GCM encrypted with the ``CREDENTIAL_VAULT_MASTER_KEY``-derived key
before it touches the database, and API responses only ever expose the
SHA-256 fingerprint — never the plaintext, never the ciphertext.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.market_data.credential_manager import (
    credential_fingerprint,
    decrypt_credential_envelope,
    derive_master_key,
    encrypt_credential_envelope,
)
from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import RegulatoryChannelConfig
from app.schemas.regulatory_reporting import ChannelConfigPut, ChannelConfigRead
from app.services.audit import record_event
from app.services.regulatory_reporting.common import get_bank_or_404, require_actor

CHANNEL_CODES = ("orass_sandbox", "email", "manual")


def _read(config: RegulatoryChannelConfig) -> ChannelConfigRead:
    return ChannelConfigRead(
        channel=config.channel,  # type: ignore[arg-type]
        config=config.config,
        has_credentials=config.credential_ciphertext is not None,
        credential_fingerprint=config.credential_fingerprint,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def _config_row(
    db: Session, ctx: TenantContext, bank_id: UUID, channel: str
) -> RegulatoryChannelConfig | None:
    return db.scalar(
        select(RegulatoryChannelConfig).where(
            RegulatoryChannelConfig.organization_id == ctx.organization_id,
            RegulatoryChannelConfig.bank_id == bank_id,
            RegulatoryChannelConfig.channel == channel,
        )
    )


def get_channel_config(
    db: Session, ctx: TenantContext, bank_id: UUID, channel: str
) -> ChannelConfigRead:
    bank = get_bank_or_404(db, ctx, bank_id)
    config = _config_row(db, ctx, bank.id, channel)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No channel configuration exists for '{channel}' yet.",
        )
    return _read(config)


def put_channel_config(
    db: Session, ctx: TenantContext, bank_id: UUID, channel: str, payload: ChannelConfigPut
) -> ChannelConfigRead:
    require_actor(ctx)
    bank = get_bank_or_404(db, ctx, bank_id)
    config = _config_row(db, ctx, bank.id, channel)
    created = config is None
    if config is None:
        config = RegulatoryChannelConfig(
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            channel=channel,
            config={},
        )
        db.add(config)
    config.config = payload.config
    if payload.credentials is not None:
        config.credential_ciphertext = _encrypt(bank.id, channel, payload.credentials)
        config.credential_fingerprint = credential_fingerprint(payload.credentials)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="regulatory_channel_config.updated",
        entity_type="regulatory_channel_config",
        entity_id=config.id,
        details={
            "bank_id": str(bank.id),
            "channel": channel,
            "created": created,
            "credentials_rotated": payload.credentials is not None,
            "credential_fingerprint": config.credential_fingerprint,
        },
    )
    db.commit()
    return _read(config)


def channel_config_row(
    db: Session, ctx: TenantContext, bank_id: UUID, channel: str
) -> RegulatoryChannelConfig | None:
    """The raw per-bank channel config row, or None when never configured."""
    return _config_row(db, ctx, bank_id, channel)


def decrypt_channel_credentials(config: RegulatoryChannelConfig) -> dict[str, Any] | None:
    """Per-cycle credential retrieval (EncryptedDbVault discipline).

    Returns the plaintext credential dict, which the caller must use for one
    submission cycle only and then discard — never persist, log, or surface
    it. Returns None when no credential material is stored.
    """
    if config.credential_ciphertext is None:
        return None
    key_material = get_settings().market_data.credential_vault_master_key
    if not key_material:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "CREDENTIAL_VAULT_MASTER_KEY is not configured; stored channel "
                "credentials cannot be retrieved for this submission."
            ),
        )
    envelope = decrypt_credential_envelope(
        derive_master_key(key_material), config.credential_ciphertext
    )
    credentials = envelope.get("credentials")
    return credentials if isinstance(credentials, dict) else None


def _encrypt(bank_id: UUID, channel: str, credentials: dict[str, Any]) -> str:
    key_material = get_settings().market_data.credential_vault_master_key
    if not key_material:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "CREDENTIAL_VAULT_MASTER_KEY is not configured; channel credentials "
                "cannot be stored until the credential vault is operational."
            ),
        )
    return encrypt_credential_envelope(
        derive_master_key(key_material),
        institution_id=str(bank_id),
        vendor=f"regulatory_channel:{channel}",
        credentials=credentials,
        issued_at=datetime.now(UTC),
        expires_at=None,
    )
