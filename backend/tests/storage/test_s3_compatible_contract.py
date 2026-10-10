"""StorageClient contract suite bound to the live S3-compatible backend.

Provisions a throwaway institution on the configured MinIO, runs the shared
suite, and deprovisions on teardown. Runs whenever S3_* credentials are
configured (locally via apps/risk-service/.env, per the everything-on-managed-MinIO
decision) and skips cleanly without credentials, so CI stays hermetic.
"""

from __future__ import annotations

import io as io_module
from typing import ClassVar, cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.types import KeyReference
from app.storage.access_log import HashChainedAccessLog
from app.storage.api import router
from app.storage.client import StorageClient, StorageLocation
from app.storage.config import StorageEngineSettings
from app.storage.encryption import ObjectEncryption
from app.storage.factory import get_storage_client
from app.storage.provisioning import deprovision_institution, provision_institution
from app.storage.s3_compatible import S3CompatibleStorageClient
from tests.storage.contract import StorageContractSuite, metadata_for
from tests.support.key_envelopes import MemoryEnvelopeStore

pytestmark = pytest.mark.skipif(
    not StorageEngineSettings().configured,
    reason="S3_* storage credentials are not configured.",
)


class TestS3CompatibleStorageContract(StorageContractSuite):
    _live_client: ClassVar[StorageClient | None] = None

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
        key = KeyReference("local", "contract-bank-key", "local", "contract-bank")
        provider = LocalKeyProvider()
        provider.add_key(key)
        storage = S3CompatibleStorageClient(
            settings,
            access_log=access_log,
            encryption=ObjectEncryption(MemoryEnvelopeStore(slug, key, provider)),
        )
        type(self)._live_client = storage
        provision_institution(storage._s3, settings, slug)  # noqa: SLF001 - shares the connection
        yield storage
        deprovision_institution(storage._s3, settings, slug)  # noqa: SLF001

    def test_backend_identifies_as_minio(self, client) -> None:
        assert client.health_check().backend == "minio"

    def expected_kms_key(self) -> str | None:
        return "contract-bank-key"

    def fetch_presigned(self, url: str) -> bytes:
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_storage_client] = lambda: self._live_client
        with TestClient(app) as client:
            response = client.get(url)
            assert response.status_code == 200, response.text
            return response.content

    def read_audit_segment(self, client: StorageClient, segment_path: str) -> str:
        bucket, _, key = segment_path.partition("/")
        s3 = cast("S3CompatibleStorageClient", client)._s3  # noqa: SLF001
        response = s3.get_object(Bucket=bucket, Key=key)
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
