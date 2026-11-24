"""Compact JWS (ES256) over arbitrary payload bytes.

Creation goes through ``GatewayKey.sign`` so a KMS-held key works the same as a local
one; verification uses PyJWT's ``PyJWS``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric import ec
from jwt.api_jws import PyJWS
from jwt.exceptions import InvalidTokenError
from pydantic import JsonValue

from app.core.encoding import b64url_decode, b64url_encode
from app.core.jsonvalue import NotJsonError, loads_object
from app.keys.provider import GatewayKey


class JwsError(ValueError):
    pass


@dataclass(frozen=True)
class JwsParts:
    header: dict[str, JsonValue]
    payload: bytes


def sign_compact(key: GatewayKey, payload: bytes, *, typ: str) -> str:
    header: dict[str, JsonValue] = {"alg": "ES256", "kid": key.kid, "typ": typ}
    header_b64 = b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_b64}.{b64url_encode(payload)}".encode("ascii")
    return f"{signing_input.decode('ascii')}.{b64url_encode(key.sign(signing_input))}"


def unverified_header(token: str) -> dict[str, JsonValue]:
    parts = token.split(".")
    if len(parts) != 3:
        raise JwsError("not a compact JWS")
    try:
        return loads_object(b64url_decode(parts[0]))
    except (NotJsonError, ValueError) as exc:
        raise JwsError("the protected header is not a JSON object") from exc


def verify_compact(
    token: str, public_key: ec.EllipticCurvePublicKey, *, expected_typ: str | None = None
) -> JwsParts:
    header = unverified_header(token)
    if header.get("alg") != "ES256":
        raise JwsError("only ES256 is accepted")
    if expected_typ is not None and header.get("typ") != expected_typ:
        raise JwsError(f"typ must be {expected_typ}")
    try:
        decoded = PyJWS().decode_complete(token, key=public_key, algorithms=["ES256"])
    except InvalidTokenError as exc:
        raise JwsError("signature verification failed") from exc
    payload = decoded.get("payload")
    if not isinstance(payload, bytes):
        raise JwsError("payload missing")
    return JwsParts(header=header, payload=payload)


def header_kid(header: Mapping[str, JsonValue]) -> str | None:
    kid = header.get("kid")
    return kid if isinstance(kid, str) else None


def decode_signed_json(
    token: str, public_key: object, *, algorithms: list[str]
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """Verify a JWS/JWT signature and return ``(header, payload object)``.

    Claim checks (expiry, audience, issuer) are the caller's, against the injected clock.
    """
    header = unverified_header(token)
    alg = header.get("alg")
    if not isinstance(alg, str) or alg not in algorithms:
        raise JwsError("algorithm not accepted")
    try:
        decoded = PyJWS().decode_complete(token, key=public_key, algorithms=[alg])  # pyright: ignore[reportArgumentType]
    except InvalidTokenError as exc:
        raise JwsError("signature verification failed") from exc
    payload = decoded.get("payload")
    if not isinstance(payload, bytes):
        raise JwsError("payload missing")
    try:
        return header, loads_object(payload)
    except NotJsonError as exc:
        raise JwsError("the payload is not a JSON object") from exc
