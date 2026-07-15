"""StorageClient contract suite bound to the live S3-compatible backend.

Provisions a throwaway institution on the configured MinIO, runs the shared
suite, and deprovisions on teardown. Runs whenever S3_* credentials are
configured (locally via apps/risk-service/.env, per the everything-on-cedynhq
decision) and skips cleanly without credentials, so CI stays hermetic.
"""

from __future__ import annotations

import io as io_module
from uuid import uuid4

import pytest

from app.storage.access_log import HashChainedAccessLog
from app.storage.client import StorageLocation
from app.storage.config import StorageEngineSettings
from app.storage.provisioning import deprovision_institution, provision_institution
from app.storage.s3_compatible import S3CompatibleStorageClient
from tests.storage.contract import StorageContractSuite, metadata_for

pytestmark = pytest.mark.skipif(
    not StorageEngineSettings().configured,
    reason="S3_* storage credentials are not configured.",
)


class TestS3CompatibleStorageContract(StorageContractSuite):
    @pytest.fixture(scope="class")
    def settings(self) -> StorageEngineSettings:
        return StorageEngineSettings()

    @pytest.fixture(scope="class")
    def access_log(self) -> HashChainedAccessLog:
        return HashChainedAccessLog(identity="contract-suite")

    @pytest.fixture(scope="class")
    def slug(self) -> str:
        return f"ctest-{uuid4().hex[:8]}"

    @pytest.fixture(scope="class")
    def client(
        self,
        settings: StorageEngineSettings,
        access_log: HashChainedAccessLog,
        slug: str,
    ):
        storage = S3CompatibleStorageClient(settings, access_log=access_log)
        provision_institution(storage._s3, settings, slug)  # noqa: SLF001 - shares the connection
        yield storage
        deprovision_institution(storage._s3, settings, slug)  # noqa: SLF001

    def test_backend_identifies_as_minio(self, client) -> None:
        assert client.health_check().backend == "minio"

    def expected_kms_key(self) -> str | None:
        return StorageEngineSettings().kms_key_id

    def read_audit_segment(self, client, segment_path: str) -> str:
        bucket, _, key = segment_path.partition("/")
        response = client._s3.get_object(Bucket=bucket, Key=key)  # noqa: SLF001
        return response["Body"].read().decode()

    def test_objects_are_sse_kms_encrypted_at_rest(self, client, slug) -> None:
        """Live-only: the backend must stamp SSE-KMS on the stored object."""
        content = b"sse probe"
        location = StorageLocation(slug, "canonical", "encrypted/sse-probe.parquet")
        client.write(location, io_module.BytesIO(content), metadata_for(slug, "canonical", content))
        response = client._s3.get_object(  # noqa: SLF001
            Bucket=location.bucket_name("mvp"), Key=location.object_path
        )
        response["Body"].close()
        assert response.get("ServerSideEncryption") == "aws:kms"
        assert "aequoros-key" in response.get("SSEKMSKeyId", "")
