"""Optional workforce sign-in controls, independent of tenant SSO and passwords.

Google Directory is an online authorization boundary: no membership cache or
fallback survives a failed lookup. Group roles cap, rather than rewrite, the
explicit staff row. Assurance is issuer evidence, never a local MFA assertion.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote

import jwt
import requests
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from app.core.config import OperatorSettings
from app.operator.models import OPERATOR_ROLE_RANK

type WorkforceRole = Literal["developer", "operator_admin", "super_admin"]
_GOOGLE_ISSUER = "https://accounts.google.com"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GROUP_SCOPE = "https://www.googleapis.com/auth/admin.directory.group.member.readonly"


class WorkforceDenied(Exception):
    """A safe, operator-facing refusal without provider errors or credentials."""


class GroupsUnavailable(Exception):
    """The directory could not supply authoritative membership evidence."""


class GroupLookup(Protocol):
    def has_member(self, group: str, email: str) -> bool: ...


class _ServiceAccount(BaseModel):
    model_config = ConfigDict(strict=True)
    client_email: str
    private_key: str


class _AccessToken(BaseModel):
    model_config = ConfigDict(strict=True)
    access_token: str


class _Membership(BaseModel):
    model_config = ConfigDict(strict=True)
    is_member: bool = Field(alias="isMember")


class GoogleDirectoryGroups:
    """Delegated service-account access to Directory members.hasMember.

    Fixed HTTPS endpoints, bounded requests, no redirects, and strict response
    models keep failure distinct from non-membership. One instance is used per
    operator request; the access token is never persisted or sent to the console.
    """

    def __init__(self, settings: OperatorSettings) -> None:
        self._settings = settings
        self._access_token: str | None = None

    def _token(self) -> str:
        if self._access_token is not None:
            return self._access_token
        path = self._settings.google_credentials_file
        admin = self._settings.google_delegated_admin
        if not path or not admin:
            raise GroupsUnavailable
        try:
            account = _ServiceAccount.model_validate_json(Path(path).read_bytes())
            now = int(time.time())
            assertion = jwt.encode(
                {
                    "iss": account.client_email,
                    "sub": admin,
                    "scope": _GROUP_SCOPE,
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
                raise GroupsUnavailable
            token = _AccessToken.model_validate_json(response.content).access_token
            if not token:
                raise GroupsUnavailable
        except (OSError, ValueError, jwt.PyJWTError, requests.RequestException) as exc:
            raise GroupsUnavailable from exc
        self._access_token = token
        return token

    def has_member(self, group: str, email: str) -> bool:
        token = self._token()
        try:
            response = requests.get(
                "https://admin.googleapis.com/admin/directory/v1/groups/"
                f"{quote(group, safe='')}/hasMember/{quote(email, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise GroupsUnavailable
            return _Membership.model_validate_json(response.content).is_member
        except (requests.RequestException, ValidationError) as exc:
            raise GroupsUnavailable from exc


def group_role(
    email: str,
    row_role: WorkforceRole,
    settings: OperatorSettings,
    lookup: GroupLookup,
) -> WorkforceRole:
    """Intersect directory grants with the provisioned staff role on each request."""
    if not settings.oidc_group_roles:
        return row_role
    if settings.oidc_issuer != _GOOGLE_ISSUER:
        raise WorkforceDenied("Workspace group access requires the Google issuer.")
    try:
        roles: list[WorkforceRole] = [
            role
            for group, role in settings.oidc_group_roles.items()
            if lookup.has_member(group, email)
        ]
    except GroupsUnavailable as exc:
        raise WorkforceDenied(
            "Workspace group data is unavailable. Operator access denied."
        ) from exc
    if not roles:
        raise WorkforceDenied("Workspace group membership is required for operator access.")
    granted = sorted(roles, key=lambda role: OPERATOR_ROLE_RANK[role])[-1]
    return row_role if OPERATOR_ROLE_RANK[row_role] <= OPERATOR_ROLE_RANK[granted] else granted


def numeric_date(value: JsonValue | None) -> int | None:
    """Only an integral, nonnegative NumericDate is usable authentication evidence."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def verify_claims(claims: dict[str, JsonValue], settings: OperatorSettings) -> None:
    """Validate opt-in domain, assurance and session-age policy on verified claims."""
    if settings.oidc_require_hosted_domain and claims.get("hd") != settings.oidc_allowed_domain:
        raise WorkforceDenied("The workforce hosted domain is missing or does not match.")
    now = int(time.time())
    auth_time = numeric_date(claims.get("auth_time"))
    if settings.oidc_require_assurance:
        amr = claims.get("amr")
        required = settings.oidc_required_amr
        acr = settings.oidc_required_acr
        amr_matches = not required or (
            isinstance(amr, list) and all(method in amr for method in required)
        )
        acr_matches = acr is None or claims.get("acr") == acr
        if (
            (not required and not acr)
            or not amr_matches
            or not acr_matches
            or auth_time is None
            or auth_time > now
        ):
            raise WorkforceDenied(
                "Workforce sign-in lacks the required assurance evidence. "
                "Re-authenticate or contact your workforce identity administrator."
            )
    limit = settings.oidc_session_max_age_seconds
    if limit:
        issued = numeric_date(claims.get("iat"))
        if issued is None or not 0 <= now - issued < limit:
            raise WorkforceDenied("Your workforce session has expired. Sign in again.")


def require_recent_authentication(evidence: dict[str, JsonValue], max_age: int) -> None:
    """Sensitive OIDC writes require fresh auth_time, following signing step-up."""
    if not max_age:
        return
    authenticated = numeric_date(evidence.get("auth_time"))
    if authenticated is None or not 0 <= int(time.time()) - authenticated <= max_age:
        raise WorkforceDenied(
            "This operator action requires re-authentication. "
            "Open /api/auth/login?reauth=1 in the console, then retry."
        )
