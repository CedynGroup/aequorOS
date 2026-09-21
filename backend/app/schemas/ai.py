"""Schemas for the tenant AI settings surface."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.ai.features import AI_FEATURES


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiConsentRead(ClosedModel):
    """The exact text an Organisation Owner accepts, and its version."""

    version: str
    text: str


class AiAvailabilityRead(ClosedModel):
    """What a non-owner needs to know: can I ask for a draft, and how often."""

    available: bool
    #: A gate code. The dashboard maps it to a sentence; it is never shown raw.
    reason: str
    descriptor_only: bool
    #: Server-provided so no polling interval literal lives in the browser.
    poll_after_seconds: int


class AiCommentarySettingsRead(ClosedModel):
    deployment_enabled: bool
    deployment_approved: bool
    consent: AiConsentRead
    enabled: bool
    enabled_features: list[str]
    descriptor_only: bool
    consent_version_accepted: str | None
    consent_current: bool
    consented_by_name: str | None
    consented_at: datetime | None
    updated_by_name: str | None
    updated_at: datetime | None


class AiCommentarySettingsUpdate(ClosedModel):
    enabled: bool
    enabled_features: list[str] = Field(default_factory=list)
    descriptor_only: bool = True
    #: Required when enabling; must equal the current deployment consent version.
    consent_version: str | None = None
    acknowledged: bool = False
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("enabled_features")
    @classmethod
    def known_features(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(AI_FEATURES))
        if unknown:
            message = f"Unknown AI features: {', '.join(unknown)}"
            raise ValueError(message)
        return sorted(set(value))


__all__ = [
    "AiAvailabilityRead",
    "AiCommentarySettingsRead",
    "AiCommentarySettingsUpdate",
    "AiConsentRead",
]
