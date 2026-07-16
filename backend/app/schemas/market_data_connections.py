"""Schemas for market data connection management (market_data_adapter.md §9/§10).

Credentials are WRITE-ONLY at this API: request bodies may carry a credential
dict, but no response model ever contains credential material — only the
lifecycle status, the SHA-256 fingerprint, and the expiry timestamp
(market_data_adapter.md §12.3/§15).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type MarketDataVendor = Literal["bloomberg", "refinitiv", "manual_upload"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarketDataConnectionCreate(ClosedModel):
    """Onboard one vendor connection (§9.2 steps 1-7).

    ``credentials`` is required for vendor sources and forbidden for
    ``manual_upload`` (which authenticates nothing). ``schedule`` maps scope
    category (or scope) names to ``PullFrequency`` names; omitted categories
    fall back to their §9.2 step-6 defaults.
    """

    vendor: MarketDataVendor
    display_name: str = Field(min_length=1, max_length=120)
    credentials: dict[str, Any] | None = Field(default=None, title="Connection Credentials")
    credential_expires_at: datetime | None = Field(
        default=None, title="Connection Credential Expires At"
    )
    scopes: list[str] = Field(default_factory=list)
    schedule: dict[str, str] | None = Field(default=None, title="Connection Schedule Input")


class MarketDataConnectionUpdate(ClosedModel):
    """Post-onboarding management (§9.3) and credential rotation (§10.4).

    When ``credentials`` is present the new set is validated against the
    vendor FIRST; only on success are the stored ciphertext, fingerprint, and
    expiry swapped in one transaction. On failure nothing changes and the
    bank-facing error is returned as a 422.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    scopes: list[str] | None = Field(default=None, title="Connection Scopes Update")
    schedule: dict[str, str] | None = Field(default=None, title="Connection Schedule Input")
    credentials: dict[str, Any] | None = Field(default=None, title="Connection Credentials")
    credential_expires_at: datetime | None = Field(
        default=None, title="Connection Credential Expires At"
    )


class MarketDataConnectionRead(ClosedModel):
    """One connection's bank-facing view. Never carries credential values —
    only status, fingerprint, and expiry (§10.1/§12.3)."""

    id: UUID
    vendor: MarketDataVendor
    display_name: str
    status: str
    scopes: list[str]
    schedule: dict[str, str] = Field(title="Connection Schedule")
    credential_fingerprint: str | None = Field(title="Connection Credential Fingerprint")
    credential_expires_at: datetime | None = Field(title="Connection Credential Expires At")
    last_validated_at: datetime | None = Field(title="Connection Last Validated At")
    last_pull_at: datetime | None = Field(title="Connection Last Pull At")
    last_pull_status: str | None = Field(title="Connection Last Pull Status")
    created_at: datetime
    # Bank-facing message from the most recent inline credential validation
    # (§12): populated when a create/enable validation fails, never raw vendor.
    validation_error: str | None = Field(default=None, title="Connection Validation Error")


class MarketDataConnectionListRead(ClosedModel):
    connections: list[MarketDataConnectionRead]
    total: int


class ScopeInfoRead(ClosedModel):
    """One taxonomy scope with its category, default pull frequency, the
    vendors whose catalogs serve it, and the per-pull quota impact (§9.2
    step 4). ``quota_units`` is the largest per-pull estimate across the
    supporting vendors (manual upload always contributes zero)."""

    scope: str
    category: str
    default_frequency: str
    quota_units: int
    supported_by: list[str]


class MarketDataScopeListRead(ClosedModel):
    scopes: list[ScopeInfoRead]


class QuotaSummaryRead(ClosedModel):
    """Current-month quota ledger for one vendor (§11.1)."""

    vendor: str
    month: str
    units_consumed: int
    monthly_cap: int | None
    pull_count: int


class MarketDataQuotaListRead(ClosedModel):
    vendors: list[QuotaSummaryRead]


class TestPullRead(ClosedModel):
    """Result of the §9.2 step-5 representative test pull: human-readable
    sample values on success, a bank-facing error otherwise."""

    success: bool
    sample_values: dict[str, str] = Field(title="Test Pull Sample Values")
    error: str | None = Field(title="Test Pull Error")
