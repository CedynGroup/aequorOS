"""Stage-then-ingest orchestration for T24 pulls.

A pull signs on to the core, fetches each enabled domain through the transport,
and bundles the raw payloads into one JSON document staged to the bank's temp
tier. Handing that ``temp://`` location to ``start_ingestion`` runs the offline
extract -> translate -> validate -> persist spine, so the network phase and the
deterministic phase are cleanly separated: nothing about parsing or persistence
depends on a live connection, and fixtures replace the transport wholesale.

The transport, session provider, and credentials are injected (from the bank's
connection in production, from fixtures in tests). Everything here is pure
plumbing over the seams built in Stage 0 — no T24 semantics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.adapters.temenos_t24.auth import SessionProvider, TemenosCredentials
from app.adapters.temenos_t24.catalog import Catalog, load_mode_catalog
from app.adapters.temenos_t24.domains import CoreBankingDomain
from app.adapters.temenos_t24.transport import (
    RawDomainResponse,
    T24Transport,
    build_domain_request,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.api.deps import TenantContext
    from app.schemas.ingestion import IngestionBatchStartRead, IngestionUploadRead
    from app.storage.client import StorageClient


@dataclass(frozen=True)
class StagedPull:
    """The staged bundle plus its temp-tier location."""

    location: str
    bundle: dict[str, Any]
    responses: tuple[RawDomainResponse, ...]

    @property
    def record_count(self) -> int:
        return sum(response.record_count for response in self.responses)


def _resolve_domains(catalog: Catalog, domains: list[str] | None) -> list[CoreBankingDomain]:
    """The domains to pull: the requested enabled subset, or every supported
    domain. A requested-but-unsupported domain is skipped, never faked."""
    supported = [d for d, e in catalog.entries.items() if e.supported]
    if domains is None:
        selected = supported
    else:
        requested = {CoreBankingDomain[name] for name in domains}
        selected = [d for d in supported if d in requested]
    return sorted(selected, key=lambda d: d.name)


def fetch_domains(  # noqa: PLR0913 - fetch coordinates are all required inputs
    catalog: Catalog,
    domains: list[CoreBankingDomain],
    transport: T24Transport,
    session: Any,
    *,
    as_of: date,
    company: str | None,
    mode: str,
) -> list[RawDomainResponse]:
    """Fetch each domain's raw payloads through the transport, in order."""
    responses: list[RawDomainResponse] = []
    for domain in domains:
        entry = catalog.entry(domain)
        request = build_domain_request(entry, as_of=as_of, company=company, mode=mode)
        responses.append(transport.fetch(session, request))
    return responses


def build_bundle(
    *,
    mode: str,
    as_of: date,
    company: str | None,
    responses: list[RawDomainResponse],
    catalog_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the staged bundle document from fetched domain responses."""
    return {
        "mode": mode,
        "as_of_date": as_of.isoformat(),
        "company": company,
        "catalog_overrides": catalog_overrides or {},
        "domains": [response.to_bundle_dict() for response in responses],
    }


def stage_extract(  # noqa: PLR0913 - a pull binds connection + transport + auth inputs
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    storage: StorageClient,
    *,
    mode: str,
    as_of: date,
    company: str | None,
    transport: T24Transport,
    session_provider: SessionProvider,
    credentials: TemenosCredentials,
    endpoint: str,
    domains: list[str] | None = None,
    catalog_overrides: dict[str, Any] | None = None,
) -> StagedPull:
    """Sign on, fetch enabled domains, and stage the raw bundle to temp tier.

    Returns the ``temp://`` location plus the assembled bundle. Fetch faults
    surface as classified :class:`~app.adapters.temenos_t24.errors.TemenosError`.
    """
    from app.services.ingestion import upload_source  # noqa: PLC0415 - avoid import cycle

    catalog = load_mode_catalog(mode)
    if catalog_overrides:
        from app.adapters.temenos_t24.catalog import apply_overrides  # noqa: PLC0415

        catalog = apply_overrides(catalog, catalog_overrides)
    selected = _resolve_domains(catalog, domains)
    session = session_provider.sign_on(mode, endpoint, credentials, company=company)
    responses = fetch_domains(
        catalog, selected, transport, session, as_of=as_of, company=company, mode=mode
    )
    bundle = build_bundle(
        mode=mode,
        as_of=as_of,
        company=company,
        responses=responses,
        catalog_overrides=catalog_overrides,
    )
    content = json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")
    filename = f"t24-{mode.lower()}-{as_of.isoformat()}.json"
    upload: IngestionUploadRead = upload_source(db, ctx, bank_id, storage, filename, content)
    return StagedPull(location=upload.location, bundle=bundle, responses=tuple(responses))


def pull_and_ingest(  # noqa: PLR0913 - a pull binds connection + transport + auth inputs
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    storage: StorageClient,
    *,
    mode: str,
    as_of: date,
    company: str | None,
    transport: T24Transport,
    session_provider: SessionProvider,
    credentials: TemenosCredentials,
    endpoint: str,
    reason: str,
    mapping_config_id: UUID | None = None,
    domains: list[str] | None = None,
    catalog_overrides: dict[str, Any] | None = None,
) -> IngestionBatchStartRead:
    """Stage a pull, then run it through the ingestion spine end-to-end."""
    from app.schemas.ingestion import IngestionBatchCreate  # noqa: PLC0415
    from app.services.ingestion import start_ingestion  # noqa: PLC0415

    staged = stage_extract(
        db,
        ctx,
        bank_id,
        storage,
        mode=mode,
        as_of=as_of,
        company=company,
        transport=transport,
        session_provider=session_provider,
        credentials=credentials,
        endpoint=endpoint,
        domains=domains,
        catalog_overrides=catalog_overrides,
    )
    payload = IngestionBatchCreate(
        source_system="T24",
        as_of_date=as_of,
        location=staged.location,
        mapping_config_id=mapping_config_id,
        reason=reason,
    )
    return start_ingestion(db, ctx, bank_id, payload, storage)
