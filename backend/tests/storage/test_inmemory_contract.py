from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest

from app.storage.access_log import HashChainedAccessLog
from app.storage.client import StorageClient
from tests.storage.contract import StorageContractSuite
from tests.storage.inmemory import InMemoryStorageClient


class TestInMemoryStorageContract(StorageContractSuite):
    @pytest.fixture(scope="class")
    def access_log(self) -> HashChainedAccessLog:
        return HashChainedAccessLog(identity="contract-suite")

    @pytest.fixture(scope="class")
    def slug(self) -> str:
        return f"ctest-{uuid4().hex[:8]}"

    @pytest.fixture(scope="class")
    def client(self, access_log: HashChainedAccessLog) -> InMemoryStorageClient:
        return InMemoryStorageClient(access_log=access_log, kms_key_id="aequoros-key")

    def fetch_presigned(self, url: str) -> bytes | None:
        assert url.startswith("memory://")
        return None  # no HTTP surface; URL shape is the testable part

    def expected_kms_key(self) -> str | None:
        return "aequoros-key"

    def read_audit_segment(self, client: StorageClient, segment_path: str) -> str:
        return cast("InMemoryStorageClient", client).audit_segments[-1]
