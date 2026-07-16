"""Vendor catalog loading and scope-to-request translation (§6.2 / §7.2).

Each vendor adapter ships a YAML catalog mapping ``DataScope`` names to
concrete vendor request specifications (Bloomberg securities/fields,
Refinitiv RICs). Catalogs are ground truth for what an adapter will pull:
the bank sees "Ghana yield curve"; the adapter pulls whatever the catalog
says, and the catalog is versioned alongside the adapter.

The request specs stay opaque normalized dicts on purpose — only the vendor
adapter's extractors interpret them, so vendor field vocabulary never leaks
past this module's callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.adapters.market_data.scope_taxonomy import DataScope


@dataclass(frozen=True)
class CatalogEntry:
    """One scope's vendor mapping.

    ``supported`` defaults to False when absent (§16.9): when a vendor
    catalog entry is unknown or uncertain, the scope is not offered — the
    framework never fakes support. ``requests`` is the normalized list of
    opaque vendor request specs derived from either the Bloomberg shape
    (``security``/``field``/``fields``/``tenor_months`` + ``data_source``)
    or the Refinitiv shape (``ric``/``rics``/``field``/``fields``/
    ``tenor_months``).
    """

    scope: DataScope
    supported: bool
    quota_units_per_pull: int
    requests: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Catalog:
    """A parsed, validated vendor catalog."""

    source_path: str
    entries: dict[DataScope, CatalogEntry]


class CatalogError(ValueError):
    """The catalog file is malformed. Fails loudly at load time, never at pull time."""


_INSTRUMENT_KEYS = ("security", "ric")
_REQUEST_LIST_KEYS = ("fields", "rics")
# Entry-level keys that are catalog bookkeeping, not request payload.
_META_KEYS = ("supported", "quota_units_per_pull")


def _normalize_requests(scope_name: str, entry: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Flatten either vendor catalog shape into a list of request dicts.

    Shapes handled (per the §6.2 / §7.2 illustrative excerpts):
    - ``fields``/``rics`` as a list of dicts -> each dict is one request.
    - ``fields`` as a list of strings + a top-level instrument
      (``security``/``ric``) -> one request per field string.
    - top-level instrument + single ``field`` -> exactly one request.
    Entry-level context keys (e.g. ``data_source``) are merged into every
    request so extractors receive self-contained specs.
    """
    context = {
        key: value
        for key, value in entry.items()
        if key not in _META_KEYS and key not in _REQUEST_LIST_KEYS and key != "field"
    }
    instrument = next(
        (entry[key] for key in _INSTRUMENT_KEYS if isinstance(entry.get(key), str)), None
    )

    items: list[Any] = []
    for list_key in _REQUEST_LIST_KEYS:
        value = entry.get(list_key)
        if value is None:
            continue
        if not isinstance(value, list):
            msg = f"Catalog entry {scope_name!r}: {list_key!r} must be a list."
            raise CatalogError(msg)
        items.extend(value)

    requests: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            requests.append({**context, **item})
        elif isinstance(item, str):
            if instrument is None:
                msg = (
                    f"Catalog entry {scope_name!r}: field list contains bare field "
                    f"{item!r} but no top-level instrument (security/ric)."
                )
                raise CatalogError(msg)
            requests.append({**context, "field": item})
        else:
            msg = f"Catalog entry {scope_name!r}: unsupported request item {item!r}."
            raise CatalogError(msg)

    if not requests and instrument is not None and isinstance(entry.get("field"), str):
        requests.append({**context, "field": entry["field"]})

    return tuple(requests)


def load_catalog(path: Path | str) -> Catalog:
    """Parse and validate a vendor catalog YAML file.

    Unknown scope names fail loudly (a typo'd catalog key must never
    silently drop coverage). Entries missing the ``supported`` flag default
    to unsupported (§16.9).
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        msg = f"Catalog {path} must be a mapping of DataScope names to entries."
        raise CatalogError(msg)

    entries: dict[DataScope, CatalogEntry] = {}
    for scope_name, entry in raw.items():
        try:
            scope = DataScope[str(scope_name)]
        except KeyError:
            known = ", ".join(sorted(member.name for member in DataScope))
            msg = f"Catalog {path}: unknown scope name {scope_name!r}. Known scopes: {known}."
            raise CatalogError(msg) from None
        if not isinstance(entry, dict):
            msg = f"Catalog {path}: entry for {scope_name!r} must be a mapping."
            raise CatalogError(msg)

        supported = entry.get("supported", False)
        if not isinstance(supported, bool):
            msg = f"Catalog {path}: entry {scope_name!r} 'supported' must be a boolean."
            raise CatalogError(msg)

        quota_units = entry.get("quota_units_per_pull", 0)
        if isinstance(quota_units, bool) or not isinstance(quota_units, int) or quota_units < 0:
            msg = (
                f"Catalog {path}: entry {scope_name!r} 'quota_units_per_pull' must be a "
                "non-negative integer."
            )
            raise CatalogError(msg)

        entries[scope] = CatalogEntry(
            scope=scope,
            supported=supported,
            quota_units_per_pull=quota_units,
            requests=_normalize_requests(str(scope_name), entry),
        )

    return Catalog(source_path=str(path), entries=entries)


def supported_scopes(catalog: Catalog) -> list[DataScope]:
    """Scopes the catalog marks supported, in stable name order. This backs
    ``list_available_scopes`` — coverage is defined by catalogs at runtime,
    not by documentation tables (§5.4)."""
    return sorted(
        (scope for scope, entry in catalog.entries.items() if entry.supported),
        key=lambda scope: scope.value,
    )


def requests_for(catalog: Catalog, scope: DataScope) -> list[dict[str, Any]]:
    """The normalized vendor request specs for one scope."""
    try:
        entry = catalog.entries[scope]
    except KeyError:
        msg = f"Catalog {catalog.source_path} has no entry for scope {scope.value!r}."
        raise LookupError(msg) from None
    return [dict(request) for request in entry.requests]


def quota_units(catalog: Catalog, scopes: list[DataScope]) -> int:
    """Total quota units one pull of ``scopes`` consumes per the catalog.

    Scopes absent from the catalog contribute zero: quota estimation is
    advisory (§16.5) and must never turn a missing catalog entry into a
    hard failure — scope availability is enforced separately.
    """
    return sum(
        catalog.entries[scope].quota_units_per_pull for scope in scopes if scope in catalog.entries
    )
