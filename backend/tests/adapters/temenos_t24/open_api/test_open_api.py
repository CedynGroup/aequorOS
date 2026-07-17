"""Open API mode: SourceAdapter contract (offline) + T24 end-to-end contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.temenos_t24.adapter import TemenosT24Adapter
from app.domain.ingestion.adapter import SourceAdapter
from tests.adapters.contract import AdapterContractSuite
from tests.adapters.temenos_t24.contract import TemenosContractSuite

from .conftest import FIXTURES_DIR, MODE


@pytest.fixture
def adapter() -> SourceAdapter:
    return TemenosT24Adapter()


class TestOpenApiSourceAdapterContract(AdapterContractSuite):
    """The generic SourceAdapter conformance suite, run against Open API fixtures."""


class TestOpenApiEndToEnd(TemenosContractSuite):
    """The T24 stage -> ingest -> persist journey, Open API mode."""

    @pytest.fixture
    def mode(self) -> str:
        return MODE

    @pytest.fixture
    def fixtures_dir(self) -> Path:
        return FIXTURES_DIR

    @pytest.fixture
    def enabled_domains(self) -> list[str] | None:
        return None
