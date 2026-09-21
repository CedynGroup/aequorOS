"""Reading and changing one organisation's AI consent and switches.

Two asymmetries are deliberate:

* **Enabling requires the CURRENT consent version and an explicit tick.**
  Bumping ``AI_CONSENT_VERSION`` therefore switches every tenant off until an
  Organisation Owner reads and accepts the new text — re-consent is not a
  reminder, it is a gate.
* **Disabling never requires anything and always succeeds.** A kill-switch that
  can be blocked by a validation error is not a kill-switch.

Every change writes an append-only audit event carrying the before/after state
and the reason. The row itself is mutable; the history is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import Settings, get_settings, is_undeployed_environment
from app.db.base import utc_now
from app.models import User
from app.models.ai import AiCommentarySettings
from app.schemas.ai import (
    AiAvailabilityRead,
    AiCommentarySettingsRead,
    AiCommentarySettingsUpdate,
    AiConsentRead,
)
from app.services.ai import gates
from app.services.ai.features import AiFeature
from app.services.audit import record_event

_CONSENT_DIR = Path(__file__).parent / "consent"


class ConsentTextMissingError(RuntimeError):
    """The configured consent version names no shipped text — a packaging fault."""


@lru_cache(maxsize=4)
def consent_text(version: str) -> str:
    path = _CONSENT_DIR / f"{version}.md"
    if not path.is_file():
        message = f"No consent text is shipped for version {version!r}."
        raise ConsentTextMissingError(message)
    return path.read_text(encoding="utf-8")


def consent(settings: Settings | None = None) -> AiConsentRead:
    settings = settings or get_settings()
    version = settings.ai.consent_version
    return AiConsentRead(version=version, text=consent_text(version))


@dataclass(frozen=True)
class _Actors:
    consented_by_name: str | None
    updated_by_name: str | None


def _actor_names(db: Session, row: AiCommentarySettings | None) -> _Actors:
    if row is None:
        return _Actors(consented_by_name=None, updated_by_name=None)
    wanted = {value for value in (row.consented_by, row.updated_by) if value is not None}
    if not wanted:
        return _Actors(consented_by_name=None, updated_by_name=None)
    names: dict[UUID, str | None] = {
        user_id: display_name
        for user_id, display_name in db.execute(
            select(User.id, User.display_name).where(User.id.in_(wanted))
        )
    }
    return _Actors(
        consented_by_name=names.get(row.consented_by) if row.consented_by else None,
        updated_by_name=names.get(row.updated_by) if row.updated_by else None,
    )


def _read(
    db: Session, row: AiCommentarySettings | None, settings: Settings
) -> AiCommentarySettingsRead:
    actors = _actor_names(db, row)
    deployment = gates.deployment_gate(settings)
    return AiCommentarySettingsRead(
        deployment_enabled=settings.ai.commentary_enabled,
        deployment_approved=deployment.allowed,
        consent=consent(settings),
        enabled=bool(row.enabled) if row else False,
        enabled_features=list(row.enabled_features or []) if row else [],
        # No row means no choice has been made, and the strict answer is the
        # safe one: descriptors, never values.
        descriptor_only=bool(row.descriptor_only) if row else True,
        consent_version_accepted=row.consent_version if row else None,
        consent_current=bool(row and row.consent_version == settings.ai.consent_version),
        consented_by_name=actors.consented_by_name,
        consented_at=row.consented_at if row else None,
        updated_by_name=actors.updated_by_name,
        updated_at=row.updated_at if row else None,
    )


def get(db: Session, ctx: TenantContext) -> AiCommentarySettingsRead:
    settings = get_settings()
    return _read(db, gates.tenant_row(db, ctx.organization_id), settings)


def _snapshot(row: AiCommentarySettings | None) -> dict[str, Any]:
    if row is None:
        return {
            "enabled": False,
            "enabled_features": [],
            "descriptor_only": True,
            "consent_version": None,
        }
    return {
        "enabled": bool(row.enabled),
        "enabled_features": list(row.enabled_features or []),
        "descriptor_only": bool(row.descriptor_only),
        "consent_version": row.consent_version,
    }


def update(
    db: Session, ctx: TenantContext, payload: AiCommentarySettingsUpdate
) -> AiCommentarySettingsRead:
    settings = get_settings()
    if ctx.actor_user_id is None:  # pragma: no cover - refused by the dependency
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )

    row = gates.tenant_row(db, ctx.organization_id)
    before = _snapshot(row)
    now = utc_now()

    if payload.enabled:
        _require_consent(payload, settings)

    if row is None:
        row = AiCommentarySettings(
            organization_id=ctx.organization_id,
            enabled=False,
            enabled_features=[],
            descriptor_only=True,
            updated_by=ctx.actor_user_id,
        )
        db.add(row)

    row.enabled = payload.enabled
    row.enabled_features = list(payload.enabled_features)
    row.descriptor_only = payload.descriptor_only
    row.updated_by = ctx.actor_user_id
    if payload.enabled:
        row.consent_version = settings.ai.consent_version
        row.consented_by = ctx.actor_user_id
        row.consented_at = _consent_timestamp(row, now)

    db.flush()
    record_event(
        db,
        ctx,
        event_type="ai.settings.updated",
        entity_type="ai_commentary_settings",
        entity_id=row.id,
        details={"before": before, "after": _snapshot(row), "reason": payload.reason},
    )
    db.commit()
    db.refresh(row)
    return _read(db, row, settings)


def _consent_timestamp(row: AiCommentarySettings, now: datetime) -> datetime:
    """Keep the original acceptance time when nothing about consent changed."""
    if row.consented_at is not None and row.consent_version == get_settings().ai.consent_version:
        return row.consented_at
    return now


def _require_consent(payload: AiCommentarySettingsUpdate, settings: Settings) -> None:
    if payload.consent_version != settings.ai.consent_version or not payload.acknowledged:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "consent_version_mismatch",
                "message": (
                    "Read and accept the current terms for AI drafting before switching it on."
                ),
                "current_version": settings.ai.consent_version,
            },
        )


def availability(
    db: Session, ctx: TenantContext, feature: AiFeature, *, prompt_version: str
) -> AiAvailabilityRead:
    """What a non-owner sees: can I request a draft, and in which mode."""
    settings = get_settings()
    decision = gates.evaluate(
        db,
        ctx.organization_id,
        feature,
        phase="enqueue",
        prompt_version=prompt_version,
        settings=settings,
    )
    return AiAvailabilityRead(
        available=decision.allowed,
        reason=decision.code,
        descriptor_only=gates.descriptor_only(db, ctx.organization_id),
        poll_after_seconds=settings.ai.client_poll_seconds,
    )


def assert_consent_text_available(settings: Settings | None = None) -> None:
    """Boot-time check: the configured consent version must name shipped text.

    WARN-only at boot (the e-sign lesson: a boot refusal locks out the person
    who could fix it); the settings route raises if it is genuinely missing.
    """
    settings = settings or get_settings()
    consent_text(settings.ai.consent_version)


def deployment_warning(settings: Settings | None = None) -> str | None:
    """One line for the boot log when AI is on but not approved here."""
    settings = settings or get_settings()
    if not settings.ai.commentary_enabled:
        return None
    if is_undeployed_environment(settings.app.app_env):
        return None
    if settings.ai.production_approval_ref is None:
        return (
            "AI commentary is switched on but this environment has no "
            "AI_PRODUCTION_APPROVAL_REF; every request will be refused."
        )
    return None


__all__ = [
    "ConsentTextMissingError",
    "assert_consent_text_available",
    "availability",
    "consent",
    "consent_text",
    "deployment_warning",
    "get",
    "update",
]
