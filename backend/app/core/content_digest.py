"""Declared-versus-actual content digests for staged uploads.

An upload is a two-step protocol. The client first *declares* what it is about
to send (a byte size and, optionally, a SHA-256) and receives a presigned URL;
it then PUTs whatever it likes to that URL. Nothing in a presigned PUT ties the
body to the declaration, so a declaration is only worth recording once
completion has checked it against the object that actually landed.

Two halves live here: validating a declaration when it is made, and comparing
it to the observed object when the upload completes.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable

_HEX_SHA256 = re.compile(r"\A[0-9a-fA-F]{64}\Z")


class DeclaredDigestError(ValueError):
    """A client-declared SHA-256 is not shaped like one."""


class ContentReadOverrun(ValueError):
    """A verification read observed more bytes than the object declared."""


def normalize_sha256(value: str) -> str:
    """Return exactly 64 hexadecimal characters as lowercase hex."""
    if not _HEX_SHA256.match(value):
        raise DeclaredDigestError("sha256 must be 64 hexadecimal characters.")
    return value.lower()


def digest_matches(declared: str, observed: str) -> bool:
    """Compare two hex digests without leaking where they first differ."""
    return hmac.compare_digest(normalize_sha256(declared), normalize_sha256(observed))


def sha256_of_chunks(chunks: Iterable[bytes], *, max_bytes: int) -> tuple[int, str]:
    """Hash ``chunks``, refusing to read past ``max_bytes``.

    Returns the observed length and its hex digest. The bound is what makes a
    verification read safe: the caller knows how large the object claims to be,
    so a body that keeps arriving past that point is either a lie about the size
    or a stream that will never end, and both are a refusal rather than a
    reason to keep reading.
    """
    digest = hashlib.sha256()
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > max_bytes:
            raise ContentReadOverrun("Object is larger than its declared size.")
        digest.update(chunk)
    return total, digest.hexdigest()
