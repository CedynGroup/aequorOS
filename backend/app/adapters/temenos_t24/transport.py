"""Transport seam for the Temenos T24 adapter: one domain request, one raw
response bundle.

The transport is the ONLY place a live connection to a bank's core would exist,
and it is fetch-only — it pulls raw payloads and returns them untouched.
Everything about interpreting those payloads (OFS marker parsing, AA property
classes, LCY selection, position typing) lives in the extractors, so the
network layer stays swappable and offline-testable.

MVP ships two implementations, neither of which touches the network:

- :class:`FixtureTransport` replays recorded T24 responses keyed by domain
  (recorded once against a Temenos dev environment, then anonymized).
- :class:`UnavailableTransport` (the default when none is injected) classifies
  every request as ``CORE_UNAVAILABLE`` — the live OFS/IRIS/Open-API transports
  plug in behind :class:`T24Transport` without touching extractors or catalogs.

A raw response never reaches a bank: transports raise classified
:class:`TemenosError` on failure, and the raw payloads travel only into the
staged temp artifact for offline extraction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from app.adapters.temenos_t24.errors import (
    TemenosError,
    TemenosErrorCode,
    render_bank_facing,
)

if TYPE_CHECKING:
    from datetime import date

    from app.adapters.temenos_t24.auth import TemenosSession
    from app.adapters.temenos_t24.catalog import CatalogEntry

_NO_CACHE_TIMESTAMP = "not available"
_UNKNOWN_CORE = "your core banking system"


@dataclass(frozen=True)
class DomainRequest:
    """A fully-resolved request for one domain in one mode.

    Built from a :class:`CatalogEntry` with placeholders (``{company}`` /
    ``{as_of}``) already filled. ``fields`` is the T24 field list to select
    (the catalog ``field_map`` keys). Which coordinates a transport uses depends
    on its mode: ``application`` + ``enquiry`` for OFS, ``endpoint`` for REST.
    """

    domain: str
    mode: str
    application: str | None
    enquiry: str | None
    endpoint: str | None
    selection: dict[str, Any]
    fields: tuple[str, ...]
    id_field: str | None
    page_size: int
    company: str | None
    as_of: date


@dataclass(frozen=True)
class RawDomainResponse:
    """Raw payloads for one domain, exactly as the core returned them.

    ``records`` are opaque to the transport: ``str`` OFS response blocks for the
    OFS mode, JSON ``dict`` objects for REST modes. The extractor for the mode
    parses them. ``source`` records the enquiry/endpoint actually queried, for
    lineage. This whole object is JSON-serializable so it can be staged to the
    temp tier and re-read for offline extraction.
    """

    domain: str
    mode: str
    records: list[Any]
    source: str
    warnings: list[str] = field(default_factory=list)

    @property
    def record_count(self) -> int:
        return len(self.records)

    def to_bundle_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "mode": self.mode,
            "source": self.source,
            "records": self.records,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_bundle_dict(cls, data: dict[str, Any]) -> RawDomainResponse:
        return cls(
            domain=str(data["domain"]),
            mode=str(data["mode"]),
            records=list(data.get("records", [])),
            source=str(data.get("source", "")),
            warnings=list(data.get("warnings", [])),
        )


class T24Transport(Protocol):
    """Fetches one domain's raw payloads against an authenticated session.

    Faults are raised as classified :class:`TemenosError` — never as raw
    transport exceptions, and never carrying core-internal text into a
    bank-facing surface.
    """

    def fetch(self, session: TemenosSession, request: DomainRequest) -> RawDomainResponse: ...


class UnavailableTransport:
    """Default transport: live core connectivity is not configured.

    Every fetch classifies as ``CORE_UNAVAILABLE`` so an attempted pull records
    an actionable failed batch instead of a silent no-op. The live OFS / IRIS /
    Open-API transports replace this without any change to callers.
    """

    def __init__(self, *, core_system: str = _UNKNOWN_CORE) -> None:
        self._core_system = core_system

    def fetch(self, session: TemenosSession, request: DomainRequest) -> RawDomainResponse:
        raise TemenosError(
            render_bank_facing(
                TemenosErrorCode.CORE_UNAVAILABLE,
                core_system=self._core_system,
                timestamp=_NO_CACHE_TIMESTAMP,
            ),
            internal_detail=(
                f"live T24 transport not configured for mode {request.mode!r} "
                f"(domain {request.domain!r})"
            ),
        )


class FixtureTransport:
    """Replays recorded T24 responses from a fixtures directory.

    Payloads are keyed by domain name: ``request.domain`` resolves through
    ``filenames`` (falling back to ``<DOMAIN>.json`` then ``<DOMAIN>.ofs``)
    inside ``fixtures_dir``. A ``.json`` fixture is either a list of records or
    ``{"records": [...], "source": "..."}``; a ``.ofs`` fixture is raw OFS text,
    one response block per non-empty line. A missing recording classifies as
    ``NO_DATA_RETURNED`` — the simulated core cannot serve what was never
    recorded.
    """

    def __init__(
        self,
        fixtures_dir: Path | str,
        filenames: dict[str, str] | None = None,
        *,
        core_system: str = _UNKNOWN_CORE,
    ) -> None:
        self._fixtures_dir = Path(fixtures_dir)
        self._filenames = dict(filenames or {})
        self._core_system = core_system

    def fetch(self, session: TemenosSession, request: DomainRequest) -> RawDomainResponse:
        domain = request.domain
        explicit = self._filenames.get(domain)
        candidates = (
            [self._fixtures_dir / explicit]
            if explicit
            else [self._fixtures_dir / f"{domain}.json", self._fixtures_dir / f"{domain}.ofs"]
        )
        path = next((p for p in candidates if p.is_file()), None)
        if path is None:
            raise TemenosError(
                render_bank_facing(
                    TemenosErrorCode.NO_DATA_RETURNED,
                    core_system=self._core_system,
                    domain=domain,
                    timestamp=request.as_of.isoformat(),
                ),
                internal_detail=(
                    f"no recorded fixture for domain {domain!r}; tried "
                    f"{', '.join(str(p) for p in candidates)}"
                ),
            )

        if path.suffix == ".ofs":
            records: list[Any] = [
                line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            source = request.enquiry or request.application or domain
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and "records" in payload:
                records = list(payload["records"])
                source = str(payload.get("source", request.endpoint or request.enquiry or domain))
            elif isinstance(payload, list):
                records = list(payload)
                source = request.endpoint or request.enquiry or domain
            else:
                raise TemenosError(
                    render_bank_facing(
                        TemenosErrorCode.RESPONSE_MALFORMED,
                        core_system=self._core_system,
                        domain=domain,
                    ),
                    internal_detail=(
                        f"fixture {path} is neither a list nor a {{records: [...]}} object"
                    ),
                )

        return RawDomainResponse(domain=domain, mode=request.mode, records=records, source=source)


def build_domain_request(
    entry: CatalogEntry,
    *,
    as_of: date,
    company: str | None,
    mode: str = "",
) -> DomainRequest:
    """Resolve a catalog entry into a concrete :class:`DomainRequest`.

    Placeholders ``{company}`` and ``{as_of}`` in the selection template are
    filled. The field list is the catalog ``field_map`` keys (the T24 fields the
    extractor needs), which is what an OFS enquiry / REST projection selects.
    """
    resolved: dict[str, Any] = {}
    for key, value in entry.source.selection.items():
        if isinstance(value, str):
            resolved[key] = value.replace("{company}", company or "").replace(
                "{as_of}", as_of.isoformat()
            )
        else:
            resolved[key] = value
    return DomainRequest(
        domain=entry.domain.name,
        mode=mode,
        application=entry.source.application,
        enquiry=entry.source.enquiry,
        endpoint=entry.source.endpoint,
        selection=resolved,
        fields=tuple(entry.field_map),
        id_field=entry.id_field,
        page_size=entry.source.page_size,
        company=company,
        as_of=as_of,
    )
