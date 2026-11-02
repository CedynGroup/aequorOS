"""Google workforce sign-in policy, independent of tenant SSO and passwords.

Google Directory is an online authorization boundary: no membership or status
cache survives a failed lookup. Directory 2SV flags describe account policy,
not the authentication methods of an individual token or session.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from time import monotonic
from typing import Protocol
from urllib.parse import quote

import jwt
import requests
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from app.core.config import OperatorSettings

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DIRECTORY_SCOPES = (
    "https://www.googleapis.com/auth/admin.directory.group.member.readonly "
    "https://www.googleapis.com/auth/admin.directory.user.readonly"
)
_TOKEN_CACHE_LIMIT = 8
_TOKEN_CACHE_LOCK = threading.Lock()
# The fingerprint makes credential rotation invalidate reuse at the same path.
_TOKEN_CACHE: dict[tuple[str, str, str], tuple[str, float]] = {}


class WorkforceDenied(Exception):
    """A safe, operator-facing refusal without provider errors or credentials."""


class DirectoryUnavailable(Exception):
    """The directory could not supply authoritative account or membership evidence."""


class DirectoryLookup(Protocol):
    def has_member(self, group: str, subject: str) -> bool: ...

    def user_status(self, subject: str) -> WorkspaceUserStatus: ...


class WorkspaceUserStatus(BaseModel):
    """Strict Directory account evidence; absent 2SV flags never imply approval."""

    model_config = ConfigDict(strict=True, frozen=True)
    suspended: bool
    enrolled_in_2sv: bool | None = Field(default=None, alias="isEnrolledIn2Sv")
    enforced_in_2sv: bool | None = Field(default=None, alias="isEnforcedIn2Sv")


class _ServiceAccount(BaseModel):
    model_config = ConfigDict(strict=True)
    client_email: str
    private_key: str


class _AccessToken(BaseModel):
    model_config = ConfigDict(strict=True)
    access_token: str
    expires_in: int = Field(gt=0)


class _Membership(BaseModel):
    model_config = ConfigDict(strict=True)
    is_member: bool = Field(alias="isMember")


class GoogleDirectory:
    """Delegated service-account access to Directory.

    Fixed HTTPS endpoints, bounded requests, no redirects, and strict response
    models keep failure distinct from non-membership. Access tokens are reused
    until shortly before expiry; membership and status are always fetched live.
    Tokens are never persisted or sent to the console.
    """

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings
        self._cache_key: tuple[str, str, str] | None = None

    def _token(self) -> str:
        path = self._settings.google_credentials_file
        admin = self._settings.google_delegated_admin
        if not path or not admin:
            raise DirectoryUnavailable
        try:
            credential = Path(path).read_bytes()
            account = _ServiceAccount.model_validate_json(credential)
            key = (path, admin, hashlib.sha256(credential).hexdigest())
            self._cache_key = key
            # Serialize token acquisition to avoid a stampede on concurrent API calls.
            with _TOKEN_CACHE_LOCK:
                moment = monotonic()
                for expired in [k for k, (_, until) in _TOKEN_CACHE.items() if until <= moment]:
                    del _TOKEN_CACHE[expired]
                cached = _TOKEN_CACHE.get(key)
                if cached is not None:
                    return cached[0]
                token = self._exchange(account, admin)
                if len(_TOKEN_CACHE) >= _TOKEN_CACHE_LIMIT:
                    del _TOKEN_CACHE[next(iter(_TOKEN_CACHE))]
                lifetime = max(0, min(token.expires_in, 3600) - 60)
                _TOKEN_CACHE[key] = (token.access_token, moment + lifetime)
                return token.access_token
        except (OSError, ValueError, jwt.PyJWTError, requests.RequestException) as exc:
            raise DirectoryUnavailable from exc

    @staticmethod
    def _exchange(account: _ServiceAccount, admin: str) -> _AccessToken:
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": account.client_email,
                "sub": admin,
                "scope": _DIRECTORY_SCOPES,
                "aud": _TOKEN_URL,
                "iat": now,
                "exp": now + 300,
            },
            account.private_key,
            algorithm="RS256",
        )
        response = requests.post(
            _TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=5,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise DirectoryUnavailable
        token = _AccessToken.model_validate_json(response.content)
        if not token.access_token:
            raise DirectoryUnavailable
        return token

    def _forget_token(self, token: str) -> None:
        with _TOKEN_CACHE_LOCK:
            cached = _TOKEN_CACHE.get(self._cache_key) if self._cache_key is not None else None
            if cached is not None and cached[0] == token and self._cache_key is not None:
                del _TOKEN_CACHE[self._cache_key]

    def _get(self, path: str) -> bytes:
        token = self._token()
        try:
            response = requests.get(
                f"https://admin.googleapis.com/admin/directory/v1/{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
                allow_redirects=False,
            )
            if response.status_code in {401, 403}:
                self._forget_token(token)
            if response.status_code != 200:
                raise DirectoryUnavailable
            return response.content
        except requests.RequestException as exc:
            raise DirectoryUnavailable from exc

    def has_member(self, group: str, subject: str) -> bool:
        try:
            return _Membership.model_validate_json(
                self._get(f"groups/{quote(group, safe='')}/hasMember/{quote(subject, safe='')}")
            ).is_member
        except ValidationError as exc:
            raise DirectoryUnavailable from exc

    def user_status(self, subject: str) -> WorkspaceUserStatus:
        try:
            return WorkspaceUserStatus.model_validate_json(
                self._get(f"users/{quote(subject, safe='')}")
            )
        except ValidationError as exc:
            raise DirectoryUnavailable from exc


def verify_directory_access(
    subject: JsonValue | None,
    settings: OperatorSettings,
    lookup: DirectoryLookup,
) -> None:
    if not settings.uses_google_policy or not (
        settings.oidc_allowed_groups or settings.google_require_2sv
    ):
        return
    if (
        not isinstance(subject, str)
        or not subject
        or len(subject) > 255
        or not subject.isascii()
        or not subject.isprintable()
        or any(character.isspace() for character in subject)
        or "@" in subject
    ):
        raise WorkforceDenied("A valid Google account subject is required for operator access.")
    try:
        account = lookup.user_status(subject)
        if account.suspended:
            raise WorkforceDenied("Your Workspace account is suspended. Operator access denied.")
        if settings.google_require_2sv:
            if account.enrolled_in_2sv is None or account.enforced_in_2sv is None:
                raise DirectoryUnavailable
            if not account.enrolled_in_2sv or not account.enforced_in_2sv:
                raise WorkforceDenied(
                    "Workspace 2-Step Verification enrollment and enforcement are required."
                )
        if settings.oidc_allowed_groups and not any(
            lookup.has_member(group, subject) for group in settings.oidc_allowed_groups
        ):
            raise WorkforceDenied("Workspace group membership is required for operator access.")
    except DirectoryUnavailable as exc:
        raise WorkforceDenied(
            "Workspace directory data is unavailable. Operator access denied."
        ) from exc


def numeric_date(value: JsonValue | None) -> int | None:
    """Only an integral, nonnegative NumericDate is usable authentication evidence."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def verify_claims(claims: dict[str, JsonValue], settings: OperatorSettings) -> None:
    """Require Workspace identity and apply the optional signed session-age cap."""
    if not settings.uses_google_policy:
        return
    if claims.get("hd") != settings.oidc_allowed_domain:
        raise WorkforceDenied("The workforce hosted domain is missing or does not match.")
    now = int(time.time())
    limit = settings.oidc_session_max_age_seconds
    if limit:
        issued = numeric_date(claims.get("iat"))
        if issued is None or not 0 <= now - issued < limit:
            raise WorkforceDenied("Your workforce session has expired. Sign in again.")
