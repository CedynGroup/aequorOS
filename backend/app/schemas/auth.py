"""API schemas for authentication (login, refresh, current user)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

_BCP47_PATTERN = (
    r"^[A-Za-z]{2,3}(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|[0-9]{3}))?"
    r"(?:-(?:[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}))*$"
)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    # Optional tenant disambiguation when one email exists in more than one org.
    organization_id: UUID | None = None


class TokenRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class SsoLoginRequest(BaseModel):
    # The OIDC id_token NextAuth obtained from the bank's IdP; the backend verifies
    # it against the configured connection's issuer JWKS (zero-trust) before
    # issuing app tokens.
    id_token: str = Field(min_length=1)
    organization_id: UUID | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access-token lifetime, seconds


class MeResponse(BaseModel):
    user_id: UUID
    organization_id: UUID
    email: str
    display_name: str | None
    job_title: str | None
    locale: str | None
    timezone: str | None
    theme: Literal["light", "dark", "system"] | None
    role: str


class ProfileUpdateRequest(BaseModel):
    """Personal, non-security fields a signed-in user may change for themself."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    locale: str | None = Field(default=None, max_length=35, pattern=_BCP47_PATTERN)
    timezone: str | None = Field(default=None, max_length=255)
    theme: Literal["light", "dark", "system"] | None = None

    @field_validator("display_name", "job_title", "locale", "timezone", mode="before")
    @classmethod
    def strip_nullable_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA time zone") from exc
        return value


class SsoStatusResponse(BaseModel):
    """Public login-page probe: whether an SSO connection is enabled."""

    enabled: bool


class SsoConnectionUpdateRequest(BaseModel):
    """Admin upsert of the org's OIDC connection. The client secret is write-only:
    omit (None) to keep the stored one, send a value to replace it."""

    issuer: str = Field(min_length=8, max_length=512)
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str | None = Field(default=None, max_length=1024)
    allowed_email_domains: list[str] = Field(default_factory=list, max_length=32)
    enabled: bool = False
    # JIT: auto-create first-time sign-ins from allowed domains as read-only
    # viewers. Refused server-side unless allowed_email_domains is non-empty.
    jit_enabled: bool = False


class SsoConnectionResponse(BaseModel):
    issuer: str
    client_id: str
    client_secret_set: bool
    allowed_email_domains: list[str]
    enabled: bool
    jit_enabled: bool


class SsoAccessRequestRead(BaseModel):
    """A JIT sign-in awaiting admin approval (deactivated account stub)."""

    user_id: UUID
    email: str
    display_name: str | None
    requested_at: datetime


class SsoAccessRequestApprove(BaseModel):
    """Approval is the authorization act — the admin explicitly picks the role."""

    role: Literal["admin", "approver", "analyst", "viewer"] = "viewer"


class SsoClientConfigResponse(BaseModel):
    """Internal (dashboard server → backend) OIDC client config. Never exposed in
    the public OpenAPI schema; the route is gated by SSO_INTERNAL_KEY."""

    enabled: bool
    issuer: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
