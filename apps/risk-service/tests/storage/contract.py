"""Reusable StorageClient contract suite (storage.md §4.5).

Every backend implementation subclasses :class:`StorageContractSuite` and
provides ``client``, ``slug``, and ``access_log`` fixtures. A test passing on
one backend and failing on another means the abstraction is leaking — fix the
backend, never the test.
"""

from __future__ import annotations

import io
import urllib.request
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from app.storage.access_log import HashChainedAccessLog, verify_chain
from app.storage.client import (
    ObjectMetadata,
    StorageClient,
    StorageLocation,
    StorageNotFoundError,
    StorageValidationError,
)


def metadata_for(slug: str, tier: str, content: bytes, **overrides: str) -> ObjectMetadata:
    values: dict[str, object] = {
        "institution_slug": slug,
        "tier": tier,
        "checksum_sha256": sha256(content).hexdigest(),
        "written_at": datetime.now(UTC),
        "written_by": "contract-suite",
        "lineage_node_id": "lin-contract-test",
    }
    values.update(overrides)
    return ObjectMetadata(**values)  # type: ignore[arg-type]


class StorageContractSuite:
    def fetch_presigned(self, url: str) -> bytes | None:
        """Fetch a presigned read URL; backends without HTTP return None to skip."""
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
            return response.read()

    # -- round trip -----------------------------------------------------------

    def test_write_then_read_preserves_bytes_and_metadata(
        self, client: StorageClient, slug: str
    ) -> None:
        content = b"canonical snapshot bytes"
        location = StorageLocation(slug, "canonical", "positions/2026-06-30/b-1/loans.parquet")
        written = client.write(
            location, io.BytesIO(content), metadata_for(slug, "canonical", content)
        )
        assert written.version_id, "retained tiers must be versioned"
        assert written.metadata.checksum_sha256 == sha256(content).hexdigest()

        descriptor, stream = client.read(location)
        assert stream.read() == content
        assert descriptor.metadata.lineage_node_id == "lin-contract-test"
        assert descriptor.metadata.written_by == "contract-suite"

    def test_missing_object_raises_not_found(self, client: StorageClient, slug: str) -> None:
        with pytest.raises(StorageNotFoundError):
            client.read(StorageLocation(slug, "canonical", "does/not/exist"))

    def test_exists_reflects_reality(self, client: StorageClient, slug: str) -> None:
        location = StorageLocation(slug, "outputs", "reports/2026-06-30/r-1/report.json")
        assert not client.exists(location)
        content = b"{}"
        client.write(location, io.BytesIO(content), metadata_for(slug, "outputs", content))
        assert client.exists(location)

    # -- metadata validation ---------------------------------------------------

    def test_incomplete_metadata_fails_before_any_bytes_move(
        self, client: StorageClient, slug: str
    ) -> None:
        location = StorageLocation(slug, "canonical", "rejected/no-checksum")
        bad = metadata_for(slug, "canonical", b"x", checksum_sha256="")
        with pytest.raises(StorageValidationError, match="checksum_sha256"):
            client.write(location, io.BytesIO(b"x"), bad)
        assert not client.exists(location)

    def test_metadata_location_mismatch_is_rejected(self, client: StorageClient, slug: str) -> None:
        location = StorageLocation(slug, "canonical", "rejected/wrong-tier")
        with pytest.raises(StorageValidationError, match="tier"):
            client.write(location, io.BytesIO(b"x"), metadata_for(slug, "raw", b"x"))

    # -- idempotency and versioning ---------------------------------------------

    def test_identical_rewrite_is_a_noop(self, client: StorageClient, slug: str) -> None:
        content = b"same bytes"
        location = StorageLocation(slug, "canonical", "idempotent/object.bin")
        first = client.write(
            location, io.BytesIO(content), metadata_for(slug, "canonical", content)
        )
        second = client.write(
            location, io.BytesIO(content), metadata_for(slug, "canonical", content)
        )
        assert second.version_id == first.version_id
        assert len(list(client.list_versions(location))) == 1

    def test_changed_content_creates_a_new_version_and_keeps_the_old(
        self, client: StorageClient, slug: str
    ) -> None:
        location = StorageLocation(slug, "canonical", "restated/object.bin")
        original, restated = b"original figures", b"restated figures"
        first = client.write(
            location, io.BytesIO(original), metadata_for(slug, "canonical", original)
        )
        second = client.write(
            location, io.BytesIO(restated), metadata_for(slug, "canonical", restated)
        )
        assert second.version_id != first.version_id

        _, latest = client.read(location)
        assert latest.read() == restated
        _, historical = client.read(location, version_id=first.version_id)
        assert historical.read() == original  # §9.5 reproducibility guarantee

    # -- listing ---------------------------------------------------------------

    def test_list_honors_prefix_and_limit(self, client: StorageClient, slug: str) -> None:
        for index in range(3):
            content = f"row {index}".encode()
            client.write(
                StorageLocation(slug, "raw", f"excel/2026-06-30/e-1/file{index}.csv"),
                io.BytesIO(content),
                metadata_for(slug, "raw", content),
            )
        listed = list(client.list(slug, "raw", prefix="excel/2026-06-30/"))
        assert len(listed) == 3
        assert all(item.metadata.written_by == "contract-suite" for item in listed)
        assert len(list(client.list(slug, "raw", prefix="excel/", limit=2))) == 2
        assert list(client.list(slug, "raw", prefix="nothing/here/")) == []

    # -- deletion semantics -------------------------------------------------------

    def test_retained_tier_delete_is_logical(self, client: StorageClient, slug: str) -> None:
        content = b"must survive deletion"
        location = StorageLocation(slug, "canonical", "retained/object.bin")
        written = client.write(
            location, io.BytesIO(content), metadata_for(slug, "canonical", content)
        )
        client.delete(location)
        with pytest.raises(StorageNotFoundError):
            client.read(location)
        _, historical = client.read(location, version_id=written.version_id)
        assert historical.read() == content

    def test_temp_tier_delete_is_physical(self, client: StorageClient, slug: str) -> None:
        content = b"scratch"
        location = StorageLocation(slug, "temp", "job-1/step-1/scratch.bin")
        client.write(location, io.BytesIO(content), metadata_for(slug, "temp", content))
        client.delete(location)
        assert list(client.list_versions(location)) == []

    # -- presigned URLs -----------------------------------------------------------

    def test_presigned_read_url_serves_the_object_without_credentials(
        self, client: StorageClient, slug: str
    ) -> None:
        content = b"presigned payload"
        location = StorageLocation(slug, "outputs", "presign/report.bin")
        client.write(location, io.BytesIO(content), metadata_for(slug, "outputs", content))
        url = client.presigned_url(location, "read", expires_in_seconds=120)
        assert url
        fetched = self.fetch_presigned(url)
        if fetched is not None:
            assert fetched == content

    # -- health and audit -----------------------------------------------------------

    def test_health_check_reports_healthy(self, client: StorageClient) -> None:
        assert client.health_check().healthy

    def test_every_operation_landed_in_an_intact_hash_chain(
        self, access_log: HashChainedAccessLog
    ) -> None:
        operations = {entry.operation for entry in access_log.entries}
        assert {"write", "read", "list", "delete.logical", "delete.physical"} <= operations
        intact, detail = verify_chain(access_log.export_jsonl())
        assert intact, detail
