"""Reusable contract-conformance suite for source adapters.

Every adapter test module subclasses :class:`AdapterContractSuite` and
provides the fixtures below; the suite then enforces the behaviors the
:class:`~app.domain.ingestion.adapter.SourceAdapter` contract promises,
so all adapters are held to the same bar:

- ``adapter``: the adapter instance under test
- ``valid_config``: an AdapterConfig the adapter can read from
- ``broken_config``: an AdapterConfig pointing at a missing/unreadable source
- ``mapping_config``: a MappingConfig that translates the fixture source
- ``as_of``: the business date the fixture source represents
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.ingestion.adapter import SourceAdapter
from app.domain.ingestion.contracts import (
    ENTITY_TYPES,
    AdapterConfig,
    AdapterIdentity,
    CanonicalRecords,
    ConnectionStatus,
    ExtractionResult,
    HealthStatus,
    MappingConfig,
    SourceSchema,
)


class AdapterContractSuite:
    def test_identify_is_stable_and_complete(self, adapter: SourceAdapter) -> None:
        identity = adapter.identify()
        assert isinstance(identity, AdapterIdentity)
        assert identity.name
        assert identity.version
        assert identity == adapter.identify()

    def test_health_check_reports(self, adapter: SourceAdapter) -> None:
        health = adapter.health_check()
        assert isinstance(health, HealthStatus)

    def test_validate_connection_accepts_valid_source(
        self, adapter: SourceAdapter, valid_config: AdapterConfig
    ) -> None:
        status = adapter.validate_connection(valid_config)
        assert isinstance(status, ConnectionStatus)
        assert status.ok

    def test_validate_connection_rejects_broken_source_without_raising(
        self, adapter: SourceAdapter, broken_config: AdapterConfig
    ) -> None:
        status = adapter.validate_connection(broken_config)
        assert isinstance(status, ConnectionStatus)
        assert not status.ok
        assert status.detail

    def test_discover_schema_reports_tables_and_columns(
        self, adapter: SourceAdapter, valid_config: AdapterConfig
    ) -> None:
        schema = adapter.discover_schema(valid_config)
        assert isinstance(schema, SourceSchema)
        assert schema.tables
        for table in schema.tables:
            assert table.name
            assert table.columns

    def test_extract_returns_locatable_records_for_requested_entities(
        self, adapter: SourceAdapter, valid_config: AdapterConfig, as_of: date
    ) -> None:
        result = adapter.extract(valid_config, as_of, list(ENTITY_TYPES))
        assert isinstance(result, ExtractionResult)
        assert result.as_of_date == as_of
        assert result.records
        for record in result.records:
            assert record.entity_type in ENTITY_TYPES
            assert record.source_locator
            assert record.data

    def test_extract_is_deterministic(
        self, adapter: SourceAdapter, valid_config: AdapterConfig, as_of: date
    ) -> None:
        first = adapter.extract(valid_config, as_of, list(ENTITY_TYPES))
        second = adapter.extract(valid_config, as_of, list(ENTITY_TYPES))
        assert first.records == second.records
        assert first.content_hash == second.content_hash

    def test_translate_produces_unvalidated_canonical_records(
        self,
        adapter: SourceAdapter,
        valid_config: AdapterConfig,
        mapping_config: MappingConfig,
        as_of: date,
    ) -> None:
        extraction = adapter.extract(valid_config, as_of, list(ENTITY_TYPES))
        translated = adapter.translate(extraction, mapping_config)
        assert isinstance(translated, CanonicalRecords)
        assert translated.record_count > 0
        extracted_locators = {record.source_locator for record in extraction.records}
        for group in (
            translated.gl_accounts,
            translated.counterparties,
            translated.products,
            translated.positions,
        ):
            for record in group:
                assert record.source_locator in extracted_locators
                assert record.source_reference

    def test_translate_is_deterministic(
        self,
        adapter: SourceAdapter,
        valid_config: AdapterConfig,
        mapping_config: MappingConfig,
        as_of: date,
    ) -> None:
        extraction = adapter.extract(valid_config, as_of, list(ENTITY_TYPES))
        assert adapter.translate(extraction, mapping_config) == adapter.translate(
            extraction, mapping_config
        )

    def test_translate_with_empty_mapping_fails_records_not_the_batch(
        self, adapter: SourceAdapter, valid_config: AdapterConfig, as_of: date
    ) -> None:
        extraction = adapter.extract(valid_config, as_of, list(ENTITY_TYPES))
        translated = adapter.translate(extraction, MappingConfig())
        assert translated.record_count == 0
        assert len(translated.failures) == len(extraction.records)
        for failure in translated.failures:
            assert failure.error_code
            assert failure.raw_record

    def test_translate_preserves_raw_records_on_failure(
        self,
        adapter: SourceAdapter,
        valid_config: AdapterConfig,
        as_of: date,
    ) -> None:
        extraction = adapter.extract(valid_config, as_of, list(ENTITY_TYPES))
        translated = adapter.translate(extraction, MappingConfig())
        extracted_by_locator = {record.source_locator: record.data for record in extraction.records}
        for failure in translated.failures:
            assert failure.raw_record == extracted_by_locator[failure.source_locator]


@pytest.fixture
def as_of() -> date:
    return date(2026, 6, 30)
