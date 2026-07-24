"""Platform identifiers — THE identity of organizations and banks.

These short codes are the primary keys (no UUID aliases): what the UI shows,
what API paths take, what banks integrate with, and what the auth claims and
RLS GUC carry. Every creation path (ingestion, seeding, provisioning) gets
one from this generator at row insert, so the sandbox tenant is treated
exactly like a real institution.

Format: ``<prefix>-XXXXXXXX`` — 8 characters of Crockford base32 (digits and
uppercase letters, excluding the ambiguous I, L, O, U). 32^8 ≈ 1.1e12 values,
so random draws are collision-safe at platform scale; the unique PK index is
the backstop.
"""

from __future__ import annotations

import re
import secrets
from typing import Final

CROCKFORD_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PUBLIC_ID_LENGTH: Final = 8

BANK_PUBLIC_ID_PREFIX: Final = "BK"
ORGANIZATION_PUBLIC_ID_PREFIX: Final = "OR"

_PUBLIC_ID_PATTERN: Final = re.compile(
    rf"^(?:{BANK_PUBLIC_ID_PREFIX}|{ORGANIZATION_PUBLIC_ID_PREFIX})"
    rf"-[{CROCKFORD_ALPHABET}]{{{PUBLIC_ID_LENGTH}}}$"
)


def generate_public_id(prefix: str) -> str:
    body = "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(PUBLIC_ID_LENGTH))
    return f"{prefix}-{body}"


def new_bank_public_id() -> str:
    return generate_public_id(BANK_PUBLIC_ID_PREFIX)


def new_organization_public_id() -> str:
    return generate_public_id(ORGANIZATION_PUBLIC_ID_PREFIX)


def is_public_id(value: str) -> bool:
    """True if the value is a well-formed platform public identifier."""
    return bool(_PUBLIC_ID_PATTERN.fullmatch(value.strip().upper()))


def normalize_public_id(value: str) -> str:
    """Canonical stored form: trimmed, uppercase."""
    return value.strip().upper()
