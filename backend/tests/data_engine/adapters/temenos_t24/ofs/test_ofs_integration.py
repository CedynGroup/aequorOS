"""OFS mode: the SourceAdapter contract (offline) plus the T24 end-to-end
contract (staged pull -> ingestion spine -> canonical rows).

This module's other-suite cases live in ``test_ofs.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.data_engine.adapters.temenos_t24.contract import TemenosContractSuite

from .conftest import FIXTURES_DIR


class TestOfsEndToEnd(TemenosContractSuite):
    """The T24 stage -> ingest -> persist journey, OFS mode."""

    @pytest.fixture
    def mode(self) -> str:
        return "OFS"

    @pytest.fixture
    def fixtures_dir(self) -> Path:
        return FIXTURES_DIR

    @pytest.fixture
    def enabled_domains(self) -> list[str] | None:
        return None  # all supported OFS domains
