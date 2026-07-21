"""API schemas for authentication (login, refresh, current user)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


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
    role: str


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
