"""P-256 JWK conversion and raw ES256 signatures."""

from __future__ import annotations

from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from pydantic import JsonValue

from app.core.encoding import b64url_decode, b64url_encode


class JwkError(ValueError):
    pass


def public_jwk(public_key: ec.EllipticCurvePublicKey, kid: str) -> dict[str, JsonValue]:
    numbers = public_key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url_encode(numbers.x.to_bytes(32, "big")),
        "y": b64url_encode(numbers.y.to_bytes(32, "big")),
        "kid": kid,
        "use": "sig",
        "alg": "ES256",
    }


def public_key_from_jwk(jwk: Mapping[str, JsonValue]) -> ec.EllipticCurvePublicKey:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise JwkError("only EC P-256 keys are accepted")
    if "d" in jwk:
        raise JwkError("a private key was supplied where a public key is required")
    x_text = jwk.get("x")
    y_text = jwk.get("y")
    if not isinstance(x_text, str) or not isinstance(y_text, str):
        raise JwkError("x and y must be base64url strings")
    try:
        x_bytes = b64url_decode(x_text)
        y_bytes = b64url_decode(y_text)
    except ValueError as exc:
        raise JwkError("x and y must be base64url strings") from exc
    if len(x_bytes) != 32 or len(y_bytes) != 32:
        raise JwkError("x and y must be 32 bytes")
    numbers = ec.EllipticCurvePublicNumbers(
        int.from_bytes(x_bytes, "big"), int.from_bytes(y_bytes, "big"), ec.SECP256R1()
    )
    try:
        return numbers.public_key()
    except ValueError as exc:
        raise JwkError("the point is not on the P-256 curve") from exc


def jwk_kid(jwk: Mapping[str, JsonValue]) -> str | None:
    kid = jwk.get("kid")
    return kid if isinstance(kid, str) else None


def jwks_keys(jwks: Mapping[str, JsonValue]) -> list[Mapping[str, JsonValue]]:
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise JwkError("a JWKS needs a keys array")
    result: list[Mapping[str, JsonValue]] = []
    for item in keys:
        if not isinstance(item, dict):
            raise JwkError("every JWKS entry must be an object")
        result.append(item)
    return result


def sign_raw(private_key: ec.EllipticCurvePrivateKey, data: bytes) -> bytes:
    """ES256 signature in the JWS form: raw ``R || S``, 64 bytes."""
    der = private_key.sign(data, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def verify_raw(public_key: ec.EllipticCurvePublicKey, data: bytes, signature: bytes) -> bool:
    if len(signature) != 64:
        return False
    der = utils.encode_dss_signature(
        int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    )
    try:
        public_key.verify(der, data, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True
