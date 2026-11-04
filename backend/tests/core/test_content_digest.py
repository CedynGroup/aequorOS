from __future__ import annotations

import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.content_digest import (
    ContentReadOverrun,
    DeclaredDigestError,
    digest_matches,
    normalize_sha256,
    sha256_of_chunks,
)

_DIGEST = hashlib.sha256(b"declared bytes").hexdigest()


def test_normalize_accepts_a_hex_digest_in_any_case() -> None:
    assert normalize_sha256(_DIGEST) == _DIGEST
    assert normalize_sha256(_DIGEST.upper()) == _DIGEST


def test_normalize_refuses_transport_whitespace() -> None:
    for value in (f" {_DIGEST}", f"{_DIGEST} ", f"{_DIGEST}\n", f"\t{_DIGEST}"):
        with pytest.raises(DeclaredDigestError):
            normalize_sha256(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        _DIGEST[:-1],
        _DIGEST + "0",
        _DIGEST[:-1] + "g",
        _DIGEST[:-1] + " ",
        "sha256:" + _DIGEST,
        # A base64 body digest is a natural mistake and must not be recorded as
        # if it were interchangeable with the hex form.
        "+/Z6yR0lcnN0YW5jZX5kZWNsYXJlZCBieXRlc4+/Z6yR0lcnN0YW5jZX5kZWNs",
        "\u0661" * 64,
        "a" * 32,
        "a" * 128,
    ],
)
def test_normalize_refuses_anything_that_is_not_a_hex_sha256(value: str) -> None:
    with pytest.raises(DeclaredDigestError):
        normalize_sha256(value)


@given(st.text(alphabet="0123456789abcdefABCDEF", min_size=1, max_size=70))
def test_normalize_accepts_exactly_sixty_four_hex_characters(value: str) -> None:
    if len(value) == 64:
        assert normalize_sha256(value) == value.lower()
    else:
        with pytest.raises(DeclaredDigestError):
            normalize_sha256(value)


def test_digest_matches_compares_case_insensitively() -> None:
    assert digest_matches(_DIGEST, _DIGEST.upper())
    assert not digest_matches(_DIGEST, hashlib.sha256(b"other bytes").hexdigest())


def test_sha256_of_chunks_hashes_incrementally_across_chunk_boundaries() -> None:
    payload = b"a" * 3 + b"b" * 4096 + b"c" * 7
    chunks = [payload[:3], payload[3:4099], payload[4099:]]

    assert sha256_of_chunks(chunks, max_bytes=len(payload)) == (
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )


def test_sha256_of_chunks_hashes_an_empty_object() -> None:
    assert sha256_of_chunks([], max_bytes=0) == (0, hashlib.sha256(b"").hexdigest())


def test_sha256_of_chunks_refuses_a_body_that_outgrows_its_bound() -> None:
    # The bound is what keeps a verification read finite: an object that keeps
    # arriving past its declared size must stop the read, not extend it.
    with pytest.raises(ContentReadOverrun):
        sha256_of_chunks([b"x" * 4, b"y" * 4], max_bytes=7)


def test_sha256_of_chunks_refuses_an_oversized_single_chunk() -> None:
    with pytest.raises(ContentReadOverrun):
        sha256_of_chunks([b"x" * 8], max_bytes=1)
