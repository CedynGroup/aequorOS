"""The MarketDataAdapter contract (market_data_adapter.md §4).

``MarketDataAdapter`` extends the Data Engine's :class:`SourceAdapter`
(data_engine.md §5.1) with market-data specifics: credential-based vendor
auth, scope-based pulls, quota estimation, and bank-facing error surfacing.
Concrete implementations: BloombergAdapter, RefinitivAdapter,
ManualUploadAdapter — each registered by vendor name in the market-data
registry below, mirroring the ingestion adapter registry.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

from app.adapters.market_data.scope_taxonomy import DataScope, PullFrequency
from app.domain.ingestion.adapter import SourceAdapter


@dataclass(frozen=True)
class CredentialSet:
    """Vendor credentials for a specific institution. Retrieved from Vault."""

    institution_id: str
    vendor: str  # 'bloomberg' | 'refinitiv' | 'manual'
    credentials: dict  # vendor-specific opaque credential structure
    issued_at: datetime
    expires_at: datetime | None  # None if credentials do not expire


@dataclass(frozen=True)
class AuthResult:
    success: bool
    session_token: str | None  # short-lived session, not persisted
    expires_at: datetime | None
    error_code: str | None  # from BankFacingErrorCode enum (§12)
    error_message: str | None  # bank-facing, actionable


@dataclass(frozen=True)
class QuotaEstimate:
    scopes: list[DataScope]
    frequency: PullFrequency
    estimated_units_per_pull: int
    estimated_monthly_units: int
    current_monthly_consumption: int
    monthly_cap: int | None
    within_cap: bool


@dataclass(frozen=True)
class TestPullResult:
    """Small representative pull for the onboarding UI 'test' step."""

    # Not a test class despite the spec-mandated Test* name (pytest opt-out).
    __test__ = False

    success: bool
    sample_values: dict[str, str]  # human-readable, e.g. "GHS 3M: 15.80%"
    error: str | None


@dataclass(frozen=True)
class MarketDataPullResult:
    """Result of a full pull operation."""

    batch_id: str
    institution_id: str
    scopes_pulled: list[DataScope]
    canonical_records_produced: int
    quota_consumed: int
    raw_storage_location: str  # StorageLocation from storage.md
    canonical_storage_location: str
    pulled_at: datetime
    warnings: list[str]
    errors: list[str]


class MarketDataAdapter(SourceAdapter):
    """
    Extends SourceAdapter (per data_engine.md section 5.1) with market data specifics.
    Concrete implementations: BloombergAdapter, RefinitivAdapter, ManualUploadAdapter.
    """

    @abstractmethod
    def vendor_name(self) -> str:
        """'bloomberg' | 'refinitiv' | 'manual'"""

    @abstractmethod
    def authenticate(
        self,
        credentials: CredentialSet,
    ) -> AuthResult:
        """
        Perform vendor auth. Called before pulls.
        Session tokens (if returned) are used within a single pull cycle and discarded.
        Failure surfaces through error_code / error_message with bank-facing detail.
        """

    @abstractmethod
    def list_available_scopes(self) -> list[DataScope]:
        """
        Return the DataScope values this adapter can serve.
        Some scopes may be adapter-specific (e.g., Bloomberg-only if Refinitiv coverage
        is missing for a specific curve). The UI shows only supported scopes.
        """

    @abstractmethod
    def estimate_quota_cost(
        self,
        scopes: list[DataScope],
        frequency: PullFrequency,
        institution_id: str,
    ) -> QuotaEstimate:
        """
        Pre-flight quota estimation. Called before authorizing a scheduled pull
        and displayed to the bank at scope selection time.
        """

    @abstractmethod
    def test_pull(
        self,
        credentials: CredentialSet,
        scopes: list[DataScope],
    ) -> TestPullResult:
        """
        Small representative pull, used in the onboarding UI 'test' step (section 9).
        Returns human-readable sample values the bank operator can eyeball.
        Not persisted to canonical storage.
        """

    @abstractmethod
    def pull(  # noqa: PLR0913
        self,
        credentials: CredentialSet,
        scopes: list[DataScope],
        as_of_date: date,
        institution_id: str,
        batch_id: str,
    ) -> MarketDataPullResult:
        """
        Full pull operation. Executes:
          1. Vendor authentication with the supplied credentials
          2. Data extraction for the requested scopes
          3. Raw response persistence to raw tier storage
          4. Translation to canonical entities
          5. Canonical persistence with mandatory metadata
          6. Quota accounting
          7. Cache update
        Returns MarketDataPullResult summarizing what happened.
        """

    @abstractmethod
    def validate_credentials(
        self,
        credentials: CredentialSet,
    ) -> AuthResult:
        """
        Non-destructive credential validity check. Called by health checks
        and by credential lifecycle monitoring (section 10).
        Does NOT consume meaningful quota (uses a lightweight vendor endpoint).
        """


_MARKET_DATA_REGISTRY: dict[str, type[MarketDataAdapter]] = {}


def register_market_data_adapter(vendor: str, adapter_cls: type[MarketDataAdapter]) -> None:
    _MARKET_DATA_REGISTRY[vendor] = adapter_cls


def get_market_data_adapter_class(vendor: str) -> type[MarketDataAdapter]:
    try:
        return _MARKET_DATA_REGISTRY[vendor]
    except KeyError as exc:
        msg = f"No market data adapter registered for vendor {vendor!r}."
        raise LookupError(msg) from exc


def registered_vendors() -> tuple[str, ...]:
    return tuple(sorted(_MARKET_DATA_REGISTRY))
