"""Build the default T24 :class:`MappingConfig` from a mode catalog.

The extractor already emits records keyed by canonical field/attribute names, so
the default mapping is a near-identity layer — exactly like
``api_push.identity_mapping_config`` — with two additions sourced from the
catalog: ``attribute_columns`` (the canonical attribute keys each entity emits)
and ``enum_mappings`` (the raw-code -> canonical translations the extractor
deliberately left unresolved). ``product_mappings`` starts empty; a bank fills
it with its own product-code-to-regulatory-category rules during onboarding.

This is the onboarding deliverable seeded per bank; it is stored versioned and
passed to translate verbatim, so translation stays reproducible.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.adapters.temenos_t24.catalog import Catalog, load_mode_catalog
from app.adapters.temenos_t24.domains import DOMAIN_TO_ENTITY_TYPE
from app.domain.ingestion.contracts import (
    CounterpartyData,
    EntityMapping,
    EntityType,
    GlAccountData,
    MappingConfig,
    PositionData,
    ProductData,
    ReferenceMapping,
)

_DATA_MODELS: dict[EntityType, type[BaseModel]] = {
    "gl_account": GlAccountData,
    "counterparty": CounterpartyData,
    "product": ProductData,
    "position": PositionData,
}
# Copied verbatim into attributes, so never part of the identity field map.
_DICT_FIELDS = {"attributes", "external_identifiers"}


def _identity_fields(model: type[BaseModel]) -> dict[str, str | list[str]]:
    skip = {"source_locator", *_DICT_FIELDS}
    return {name: name for name in model.model_fields if name not in skip}


def default_t24_mapping_config(
    mode: str = "OFS", *, catalog: Catalog | None = None
) -> MappingConfig:
    """Construct the default mapping for a connection mode.

    ``attribute_columns`` and ``enum_mappings`` are unioned across the domains
    the catalog currently marks supported, so the mapping reflects live coverage
    (regenerating after new domains go live picks up their attributes/enums).
    """
    catalog = catalog or load_mode_catalog(mode)

    attribute_columns: dict[EntityType, set[str]] = {et: set() for et in _DATA_MODELS}
    enum_mappings: dict[str, dict[str, str]] = {}
    reference_mappings: dict[str, ReferenceMapping] = {}

    for domain, entry in catalog.entries.items():
        if not entry.supported:
            continue
        entity_type = DOMAIN_TO_ENTITY_TYPE[domain]
        if entity_type == "reference":
            if entry.dataset_key:
                reference_mappings[domain.name] = ReferenceMapping(
                    source_table=domain.name,
                    dataset_kind=entry.dataset_key,  # type: ignore[arg-type]
                )
            continue
        attribute_columns[entity_type].update(entry.attribute_keys)  # type: ignore[index]
        for field_name, mapping in entry.enum_mappings.items():
            enum_mappings.setdefault(field_name, {}).update(mapping)

    field_mappings: dict[EntityType, EntityMapping] = {
        entity_type: EntityMapping(
            source_table=entity_type,
            fields=_identity_fields(model),
            attribute_columns=sorted(attribute_columns[entity_type]),
        )
        for entity_type, model in _DATA_MODELS.items()
    }

    return MappingConfig(
        field_mappings=field_mappings,
        reference_mappings=reference_mappings,
        enum_mappings=enum_mappings,
    )
