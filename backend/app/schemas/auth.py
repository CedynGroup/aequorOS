"""API schemas for authentication (login, refresh, current user)."""

from __future__ import annotations

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
    # The Auth0 id_token NextAuth obtained; the backend verifies it against Auth0's
    # JWKS (zero-trust) before issuing app tokens.
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
