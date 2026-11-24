"""Base64url and digest helpers."""

from __future__ import annotations

import base64
import hashlib

from pydantic import JsonValue

from app.core.jcs import canonicalize


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jcs_sha256(value: JsonValue) -> str:
    """``sha256(JCS(value))`` as lowercase hex."""
    return sha256_hex(canonicalize(value))
