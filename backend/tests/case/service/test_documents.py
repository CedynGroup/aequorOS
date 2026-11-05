"""Tests for test_documents."""

from __future__ import annotations

from app.integrations.storage.base import StoredObjectHead
from tests.conftest import FakeStorage


def test_fake_storage_refuses_missing_and_deleted_snapshot_keys(fake_storage: FakeStorage) -> None:
    fake_storage.head = StoredObjectHead(byte_size=4)
    fake_storage.body = b"body"
    key = fake_storage.finalize_upload(bucket="test", object_key="staged")
    assert key is not None
    assert fake_storage.head_object(bucket="test", object_key=key) is not None
    assert fake_storage.read_object_digest(bucket="test", object_key=key, max_bytes=4) is not None
    for missing in (key, "staged/finalized/unknown"):
        fake_storage.delete_object(bucket="test", object_key=key)
        assert fake_storage.head_object(bucket="test", object_key=missing) is None
        assert (
            fake_storage.read_object_digest(bucket="test", object_key=missing, max_bytes=4) is None
        )
    fake_storage.delete_object(bucket="test", object_key="staged")
    assert fake_storage.finalize_upload(bucket="test", object_key="staged") is None
