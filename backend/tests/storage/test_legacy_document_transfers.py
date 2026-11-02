from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Protocol, cast
from urllib.parse import parse_qs, urlsplit

import boto3  # pyright: ignore[reportMissingTypeStubs]
import pytest
from botocore.client import BaseClient, Config  # pyright: ignore[reportMissingTypeStubs]
from moto import mock_aws

from app.core.content_digest import ContentReadOverrun
from app.integrations.storage.s3 import S3ObjectStorage

_BUCKET = "synthetic-documents"


def _storage() -> S3ObjectStorage:
    return S3ObjectStorage(
        region_name="us-east-1",
        endpoint_url=None,
        access_key_id="synthetic-access",
        secret_access_key="synthetic-secret",
        force_path_style=True,
    )


class _SeedClient(Protocol):
    def create_bucket(self, *, Bucket: str) -> object: ...

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> object: ...


def _seed_client() -> _SeedClient:
    factory = cast(Callable[..., BaseClient], boto3.client)
    return cast(
        _SeedClient,
        factory(
            "s3",
            region_name="us-east-1",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        ),
    )


def _put(object_key: str, body: bytes) -> None:
    client = _seed_client()
    _ = client.create_bucket(Bucket=_BUCKET)
    _ = client.put_object(Bucket=_BUCKET, Key=object_key, Body=body)


def test_legacy_document_upload_and_download_signing_remain_available() -> None:
    with mock_aws():
        storage = _storage()
        upload = storage.create_presigned_upload_url(
            bucket=_BUCKET,
            object_key="orgs/synthetic/original.pdf",
            content_type="application/pdf",
            expires_seconds=120,
        )
        assert upload.method == "PUT"
        assert upload.headers == {"Content-Type": "application/pdf"}
        assert upload.expires_in_seconds == 120
        assert urlsplit(upload.url).path.endswith("/orgs/synthetic/original.pdf")
        assert parse_qs(urlsplit(upload.url).query)["X-Amz-Expires"] == ["120"]
        download = storage.create_presigned_download_url(
            bucket=_BUCKET,
            object_key="orgs/synthetic/original.pdf",
            expires_seconds=60,
        )
        assert urlsplit(download).path.endswith("/orgs/synthetic/original.pdf")
        assert parse_qs(urlsplit(download).query)["X-Amz-Expires"] == ["60"]
        assert (
            parse_qs(urlsplit(upload.url).query)["X-Amz-Signature"]
            != parse_qs(urlsplit(download).query)["X-Amz-Signature"]
        )


def test_read_object_digest_hashes_what_was_stored_across_chunk_boundaries() -> None:
    body = b"legacy document body" * 200_000  # ~4 MB, spans several read chunks
    with mock_aws():
        storage = _storage()
        _put("orgs/synthetic/large.pdf", body)

        assert storage.read_object_digest(
            bucket=_BUCKET, object_key="orgs/synthetic/large.pdf", max_bytes=len(body)
        ) == (len(body), hashlib.sha256(body).hexdigest())


def test_read_object_digest_reports_a_missing_object() -> None:
    with mock_aws():
        storage = _storage()
        _put("orgs/synthetic/keep.pdf", b"keep")

        assert (
            storage.read_object_digest(
                bucket=_BUCKET, object_key="orgs/synthetic/absent.pdf", max_bytes=10
            )
            is None
        )


def test_finalized_upload_survives_replay_and_has_independent_metadata() -> None:
    with mock_aws():
        storage = _storage()
        source_key = "orgs/synthetic/original.pdf"
        body = b"verified body"
        _put(source_key, body)
        finalized_key = storage.finalize_upload(bucket=_BUCKET, object_key=source_key)
        assert finalized_key is not None
        assert finalized_key != source_key
        head = storage.head_object(bucket=_BUCKET, object_key=finalized_key)

        _ = _seed_client().put_object(Bucket=_BUCKET, Key=source_key, Body=b"replacement body")

        assert storage.head_object(bucket=_BUCKET, object_key=finalized_key) == head
        assert storage.read_object_digest(
            bucket=_BUCKET, object_key=finalized_key, max_bytes=len(body)
        ) == (len(body), hashlib.sha256(body).hexdigest())
        second_key = storage.finalize_upload(bucket=_BUCKET, object_key=source_key)
        assert second_key is not None
        assert second_key != finalized_key
        assert storage.read_object_digest(
            bucket=_BUCKET, object_key=second_key, max_bytes=32
        ) == (16, hashlib.sha256(b"replacement body").hexdigest())

        download = storage.create_presigned_download_url(
            bucket=_BUCKET, object_key=finalized_key, expires_seconds=60
        )
        assert urlsplit(download).path.endswith("/" + finalized_key)
        storage.delete_object(bucket=_BUCKET, object_key=finalized_key)
        assert storage.head_object(bucket=_BUCKET, object_key=finalized_key) is None
        assert storage.head_object(bucket=_BUCKET, object_key=second_key) is not None


def test_finalize_upload_refuses_a_missing_source() -> None:
    with mock_aws():
        storage = _storage()
        _put("orgs/synthetic/keep.pdf", b"keep")
        assert storage.finalize_upload(bucket=_BUCKET, object_key="absent") is None


def test_read_object_digest_stops_before_reading_a_body_past_its_bound() -> None:
    with mock_aws():
        storage = _storage()
        _put("orgs/synthetic/overrun.pdf", b"x" * 4096)

        with pytest.raises(ContentReadOverrun):
            _ = storage.read_object_digest(
                bucket=_BUCKET, object_key="orgs/synthetic/overrun.pdf", max_bytes=16
            )
