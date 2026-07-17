"""Offline fixtures for the OFS adapter contract (extract/translate without a
database). The end-to-end fixtures live in the shared TemenosContractSuite."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.adapters.temenos_t24.mappings.default import default_t24_mapping_config
from app.domain.ingestion.contracts import AdapterConfig, MappingConfig
from tests.adapters.temenos_t24.contract import staged_entity_bundle

MODE = "OFS"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def ofs_fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def as_of() -> date:
    return date(2026, 6, 30)


@pytest.fixture
def valid_config(tmp_path: Path) -> AdapterConfig:
    return staged_entity_bundle(MODE, FIXTURES_DIR, tmp_path)


@pytest.fixture
def broken_config(tmp_path: Path) -> AdapterConfig:
    return AdapterConfig(location=str(tmp_path / "does-not-exist.json"))


@pytest.fixture
def mapping_config() -> MappingConfig:
    return default_t24_mapping_config(MODE)
