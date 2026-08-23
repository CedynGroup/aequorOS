"""Schemas for Temenos core-banking connection management.

Credentials are WRITE-ONLY at this API: request bodies may carry a credential
dict (OFS service-user password, IRIS/Open-API client secret or API key), but no
response model ever contains credential material — only the lifecycle status,
the SHA-256 fingerprint, and the expiry timestamp. Mirrors the market-data
connection schemas.

``endpoint`` is an outbound destination an ``analyst`` chooses, so it is
screened here by :mod:`app.core.outbound` — the non-resolving half, which turns
``https://169.254.169.254`` or ``ofs://localhost`` into an immediate 422. It is
fast feedback, NOT the security boundary: the resolving check that gates egress
runs in the service immediately before sign-on/pull
(``temenos_connections.guard_endpoint``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.outbound import check_url_syntax

type ConnectionMode = Literal["OFS", "IRIS", "OPEN_API"]
type CoreSystem = Literal["T24", "FINACLE", "FLEXCUBE"]

# Schemes a Temenos endpoint may carry. ``ofs``/``ofss`` are the OFS-mode
# locator forms this codebase already writes (``ofs://sample-bank``); the REST
# modes (IRIS / Open API) are TLS-only. Plain ``http`` is deliberately absent —
# no transport in the tree needs it, and a core reached in clear text would
# carry the whole book unencrypted. A bare ``host``/``host:port`` (no scheme)
# is accepted and screened as a host.
TEMENOS_ENDPOINT_SCHEMES: frozenset[str] = frozenset({"https", "ofs", "ofss"})


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _OutboundEndpointField:
    """Shared egress screening for the create/update payloads."""

    @field_validator("endpoint")
    @classmethod
    def _screen_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return check_url_syntax(
            value, allowed_schemes=TEMENOS_ENDPOINT_SCHEMES, field="endpoint"
        )


class TemenosConnectionCreate(_OutboundEndpointField, ClosedModel):
    """Onboard one Temenos core-banking connection.

    ``credentials`` is required (the shape depends on ``connection_mode``: OFS
    wants username+password; IRIS/Open API want client_id+client_secret or an
    api_key). ``domains`` are ``CoreBankingDomain`` names to enable; omitted
    means every domain the mode catalog supports. ``schedule`` maps domain
    category to a ``PullCadence`` name.
    """

    connection_mode: ConnectionMode
    display_name: str = Field(min_length=1, max_length=120)
    endpoint: str = Field(min_length=1, max_length=255)
    core_system: CoreSystem = "T24"
    companies: list[str] = Field(default_factory=list)
    default_currency: str = Field(min_length=3, max_length=3)
    domains: list[str] = Field(default_factory=list)
    schedule: dict[str, str] | None = Field(default=None, title="Connection Schedule Input")
    catalog_overrides: dict[str, Any] | None = Field(
        default=None, title="Connection Catalog Overrides"
    )
    credentials: dict[str, Any] | None = Field(default=None, title="Connection Credentials")
    credential_expires_at: datetime | None = Field(
        default=None, title="Connection Credential Expires At"
    )


class TemenosConnectionUpdate(_OutboundEndpointField, ClosedModel):
    """Post-onboarding management and credential rotation.

    When ``credentials`` is present the new set is validated FIRST; only on
    success are the stored ciphertext, fingerprint, and expiry swapped in one
    transaction. On failure nothing changes and the error is returned as a 422.

    ``endpoint`` is re-screened on every edit: an ACTIVE connection may not be
    re-pointed at a blocked destination.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    endpoint: str | None = Field(default=None, min_length=1, max_length=255)
    companies: list[str] | None = Field(default=None, title="Connection Companies Update")
    default_currency: str | None = Field(default=None, min_length=3, max_length=3)
    domains: list[str] | None = Field(default=None, title="Connection Domains Update")
    schedule: dict[str, str] | None = Field(default=None, title="Connection Schedule Input")
    catalog_overrides: dict[str, Any] | None = Field(
        default=None, title="Connection Catalog Overrides"
    )
    credentials: dict[str, Any] | None = Field(default=None, title="Connection Credentials")
    credential_expires_at: datetime | None = Field(
        default=None, title="Connection Credential Expires At"
    )


class TemenosConnectionRead(ClosedModel):
    """One connection's bank-facing view. Never carries credential values —
    only status, fingerprint, and expiry."""

    id: UUID
    core_system: CoreSystem
    connection_mode: ConnectionMode
    display_name: str
    endpoint: str
    status: str
    companies: list[str]
    default_currency: str
    domains: list[str]
    schedule: dict[str, str] = Field(title="Connection Schedule")
    catalog_overrides: dict[str, Any] = Field(title="Connection Catalog Overrides")
    credential_fingerprint: str | None = Field(title="Connection Credential Fingerprint")
    credential_expires_at: datetime | None = Field(title="Connection Credential Expires At")
    last_validated_at: datetime | None = Field(title="Connection Last Validated At")
    last_pull_at: datetime | None = Field(title="Connection Last Pull At")
    last_pull_status: str | None = Field(title="Connection Last Pull Status")
    created_at: datetime
    # Bank-facing message from the most recent inline credential validation:
    # populated when a create/validate/enable check fails, never raw core text.
    validation_error: str | None = Field(default=None, title="Connection Validation Error")


class TemenosConnectionListRead(ClosedModel):
    connections: list[TemenosConnectionRead]
    total: int


class TemenosDomainInfoRead(ClosedModel):
    """One core-banking domain with its category, canonical entity type, default
    pull cadence, and whether the connection's mode catalog supports it."""

    domain: str
    category: str
    entity_type: str
    default_cadence: str
    supported: bool


class TemenosDomainListRead(ClosedModel):
    mode: str
    domains: list[TemenosDomainInfoRead]


class TemenosTestPullRead(ClosedModel):
    """Result of a connection test: a human-readable summary of what a pull
    would fetch on success, a bank-facing error otherwise."""

    success: bool
    sample_values: dict[str, str] = Field(title="Test Pull Sample Values")
    error: str | None = Field(title="Test Pull Error")


class TemenosPullTriggerRequest(ClosedModel):
    """On-demand pull for a single as-of date (defaults to today)."""

    as_of_date: date | None = Field(default=None, title="Pull As Of Date")


class TemenosBackfillRequest(ClosedModel):
    """Historical backfill: one pull per as-of date in the inclusive range."""

    start_date: date
    end_date: date


class TemenosPullTriggerRead(ClosedModel):
    """The enqueued pull job(s)."""

    job_ids: list[UUID]
    count: int
