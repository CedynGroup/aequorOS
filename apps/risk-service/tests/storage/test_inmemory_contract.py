from __future__ import annotations

from uuid import uuid4

import pytest

from app.storage.access_log import HashChainedAccessLog
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
        return InMemoryStorageClient(access_log=access_log)

    def fetch_presigned(self, url: str) -> bytes | None:
        assert url.startswith("memory://")
        return None  # no HTTP surface; URL shape is the testable part
