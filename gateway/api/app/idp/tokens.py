"""ID tokens issued by the gateway's own identity provider, and their verification key."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from app.core.context import AppContext
from app.core.jcs import canonicalize
from app.keys.jws import sign_compact
from app.keys.provider import KeyProvider

ID_TOKEN_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    """The claims of a staff account, read out of the session before minting."""

    subject: str
    name: str
    given_name: str | None
    family_name: str | None
    email: str


class TokenSubject(Protocol):
    """Who an ID token is about: a staff account, or a synthetic person in tests."""

    @property
    def subject(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def given_name(self) -> str | None: ...
    @property
    def family_name(self) -> str | None: ...
    @property
    def email(self) -> str: ...


def mint_id_token(
    ctx: AppContext,
    who: TokenSubject,
    *,
    groups: tuple[str, ...] | list[str],
    mfa: bool,
    nonce: str | None = None,
    lifetime: int = ID_TOKEN_SECONDS,
) -> str:
    """Sign an ID token with the identity provider's key (ES256)."""
    now = int(ctx.clock.now().timestamp())
    claims: dict[str, JsonValue] = {
        "iss": ctx.settings.idp_issuer,
        "sub": who.subject,
        "aud": ctx.settings.oidc_audience,
        "iat": now,
        "exp": now + lifetime,
        "jti": secrets.token_hex(12),
        "name": who.name,
        "email": who.email,
        "groups": list(groups),
        "amr": ["pwd", "mfa"] if mfa else ["pwd"],
    }
    if who.given_name:
        claims["given_name"] = who.given_name
    if who.family_name:
        claims["family_name"] = who.family_name
    if nonce:
        claims["nonce"] = nonce
    return sign_compact(ctx.keys.current("devidp"), canonicalize(claims), typ="JWT")


class IdpKeyResolver:
    """Resolve the identity provider's verification key in-process (no self HTTP call)."""

    def __init__(self, keys: KeyProvider) -> None:
        self._keys: KeyProvider = keys

    def key_for(self, kid: str | None, alg: str) -> object | None:
        if alg != "ES256":
            return None
        for key in self._keys.public_keys("devidp"):
            if kid is None or key.kid == kid:
                return key.public_key
        return None
