from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.case.schemas.documents import UploadRequest

_DIGEST = hashlib.sha256(b"declared bytes").hexdigest()


def _request(**overrides: object) -> UploadRequest:
    fields: dict[str, object] = {
        "case_id": uuid4(),
        "filename": "financials.pdf",
        "content_type": "application/pdf",
        "byte_size": 1024,
    }
    fields.update(overrides)
    return UploadRequest.model_validate(fields)


@pytest.mark.parametrize(
    "declaration",
    [
        "",
        "not-a-digest",
        _DIGEST[:-1],
        _DIGEST + "0",
        "sha256:" + _DIGEST,
        "a" * 128,
        "\u0661" * 64,
    ],
)
def test_a_digest_declaration_that_is_not_a_sha256_is_refused(declaration: str) -> None:
    # The declaration is an attestation made before anything is sent. Recording
    # one that no object could ever match is worse than refusing it.
    with pytest.raises(ValidationError):
        _ = _request(sha256=declaration)


def test_a_digest_declaration_is_recorded_as_lowercase_hex() -> None:
    assert _request(sha256=_DIGEST.upper()).sha256 == _DIGEST


def test_a_digest_declaration_with_whitespace_is_refused() -> None:
    for value in (f" {_DIGEST}", f"{_DIGEST} ", f"{_DIGEST}\n", f"\t{_DIGEST}"):
        with pytest.raises(ValidationError):
            _ = _request(sha256=value)


def test_a_declaration_without_a_digest_keeps_it_absent() -> None:
    assert _request().sha256 is None
