"""An upload is refused by an amount it is willing to read, not after the fact.

Why this exists (security audit, 2026-10-28 — issue #701)
---------------------------------------------------------
``content = await file.read()`` materialized the whole multipart body, then
compared its length to the 50 MB cap. The cap therefore bounded what the
platform accepted, never the memory the request used: an authenticated tenant
user posting multi-gigabyte bodies drove the shared API process to exhaustion
before a single byte was rejected.

The assertion that matters is not "the oversized upload was refused" — the old
code refused it too, eventually. It is how much was read before the refusal, so
each oversized case asserts the body's own read position afterwards.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from app.data_engine.api.upload_seam import MAX_UPLOAD_BYTES, read_upload_within_cap

_CHUNK = 1024 * 1024


def _upload(body: io.BytesIO, name: str = "positions.xlsx") -> UploadFile:
    return UploadFile(file=body, filename=name)


def test_a_legitimate_upload_arrives_whole() -> None:
    payload = b"xlsx-bytes" * 1_000
    content = asyncio.run(read_upload_within_cap(_upload(io.BytesIO(payload))))

    assert content == payload


def test_an_oversized_body_is_refused_having_read_only_the_cap() -> None:
    """The old read-then-check consumed all of it; this consumes the cap plus a chunk."""
    limit = 2 * _CHUNK
    body = io.BytesIO(b"x" * (64 * _CHUNK))

    with pytest.raises(HTTPException) as refusal:
        asyncio.run(read_upload_within_cap(_upload(body), limit=limit))

    assert refusal.value.status_code == 413
    assert "limit" in str(refusal.value.detail)
    # The read position is what the handler actually took from the body.
    assert body.tell() <= limit + _CHUNK
    assert body.tell() < 64 * _CHUNK


def test_a_body_exactly_at_the_cap_is_accepted() -> None:
    """The boundary the old comparison used (``>``) is preserved, not tightened."""
    payload = b"x" * (2 * _CHUNK)
    content = asyncio.run(read_upload_within_cap(_upload(io.BytesIO(payload)), limit=len(payload)))

    assert len(content) == len(payload)


def test_an_empty_upload_is_unprocessable() -> None:
    with pytest.raises(HTTPException) as refusal:
        asyncio.run(read_upload_within_cap(_upload(io.BytesIO(b""))))

    assert refusal.value.status_code == 422
    assert "empty" in str(refusal.value.detail)


def test_the_default_cap_is_the_documented_fifty_megabytes() -> None:
    assert MAX_UPLOAD_BYTES == 50_000_000
