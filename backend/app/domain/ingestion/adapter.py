"""The contract every source adapter must satisfy.

Adapters are the ONLY code that knows a source system's schema. They extract
raw records in the source's native shape and translate them into canonical
record data using a per-institution :class:`MappingConfig`. They do not
validate business rules and they do not touch the database — both belong to
downstream layers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from app.domain.ingestion.contracts import (
    AdapterConfig,
    AdapterIdentity,
    CanonicalRecords,
    ConnectionStatus,
    EntityType,
    ExtractionResult,
    HealthStatus,
    MappingConfig,
    SourceSchema,
)


class SourceAdapter(ABC):
    @abstractmethod
    def identify(self) -> AdapterIdentity:
        """Return adapter name, version, and supported source system."""

    @abstractmethod
    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        """Test that this adapter can reach and read the source."""

    @abstractmethod
    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        """Introspect the source: tables/sheets and their column headers."""

    @abstractmethod
    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        """Pull raw records for the business date in the source's native shape.

        Does not translate. Does not validate business rules. A failure on a
        single record is recorded as a warning and extraction continues; a
        failure of the whole source raises.
        """

    @abstractmethod
    def translate(
        self,
        raw_records: ExtractionResult,
        mapping_config: MappingConfig,
    ) -> CanonicalRecords:
        """Translate raw records into canonical record data.

        Untranslatable records land in ``CanonicalRecords.failures`` with the
        raw record preserved; translation never raises for bad data. The
        output has NOT been validated.
        """

    @abstractmethod
    def health_check(self) -> HealthStatus:
        """Runtime health of this adapter instance."""


_REGISTRY: dict[str, type[SourceAdapter]] = {}


def register_adapter(source_system: str, adapter_cls: type[SourceAdapter]) -> None:
    _REGISTRY[source_system] = adapter_cls


def get_adapter_class(source_system: str) -> type[SourceAdapter]:
    try:
        return _REGISTRY[source_system]
    except KeyError as exc:
        msg = f"No adapter registered for source system {source_system!r}."
        raise LookupError(msg) from exc


def registered_source_systems() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
