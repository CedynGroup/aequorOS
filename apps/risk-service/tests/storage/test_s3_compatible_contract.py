"""StorageClient contract suite bound to the live S3-compatible backend.

Provisions a throwaway institution on the configured MinIO, runs the shared
suite, and deprovisions on teardown. Runs whenever S3_* credentials are
configured (locally via apps/risk-service/.env, per the everything-on-cedynhq
decision) and skips cleanly without credentials, so CI stays hermetic.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.storage.access_log import HashChainedAccessLog
from app.storage.config import StorageEngineSettings
from app.storage.provisioning import deprovision_institution, provision_institution
from app.storage.s3_compatible import S3CompatibleStorageClient
from tests.storage.contract import StorageContractSuite

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
