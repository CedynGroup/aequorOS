"""The Refinitiv (LSEG) ``MarketDataAdapter`` implementation (§7).

Composition: :mod:`auth` acquires a short-lived RDP session token, the
injected :class:`RdpTransport` fetches raw RDP-shaped payloads (fixtures in
MVP; live wiring is Phase 2), category extractors parse them into typed
intermediates, translators produce vendor-agnostic record bundles, and the
shared pull runner persists everything (raw tier, canonical supersession,
quota, cache, lineage, pipeline refresh).

The adapter is constructed bound to one bank: the ``institution_id`` and
``batch_id`` arguments of :meth:`pull` are accepted for §4.1 interface
compatibility, but the bound bank is authoritative and the pull runner mints
the persisted batch id.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from app.adapters.market_data.base import (
    AuthResult,
    CredentialSet,
    MarketDataAdapter,
    MarketDataPullResult,
    QuotaEstimate,
    TestPullResult,
    register_market_data_adapter,
)
from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.pull_runner import ScopeExtraction, execute_pull
from app.adapters.market_data.quota_tracker import current_month_usage, estimate, month_key
from app.adapters.market_data.refinitiv.auth import (
    SimulatedTokenProvider,
    TokenProvider,
    acquire_session_token,
    authenticate_credentials,
)
from app.adapters.market_data.refinitiv.extractors import (
    extract_curve,
    extract_fx,
    extract_ratings,
)
from app.adapters.market_data.refinitiv.translators import (
    curve_to_bundle,
    fx_to_bundle,
    ratings_to_bundle,
)
from app.adapters.market_data.refinitiv.transport import (
    VENDOR_NAME,
    RdpTransport,
    UnconfiguredTransport,
    bank_facing_for,
)
from app.adapters.market_data.scope_taxonomy import (
    DataScope,
    PullFrequency,
    ScopeCategory,
    category_of,
)
from app.adapters.market_data.scope_translator import (
    Catalog,
    load_catalog,
    quota_units,
    requests_for,
    supported_scopes,
)
from app.db.base import utc_now
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
from app.models import Bank, MarketDataQuotaUsage

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

ADAPTER_VERSION = "1.0.0"

CATALOG_PATH = Path(__file__).with_name("ric_catalog.yaml")

_NOT_AN_INGESTION_ADAPTER = "market data adapters ingest via pull()"

_FX_CATEGORIES = (ScopeCategory.FX_SPOT, ScopeCategory.FX_FORWARD)


@lru_cache(maxsize=1)
def _ric_catalog() -> Catalog:
    """The parsed §7.2 RIC catalog; loaded once, fails loudly on malformation."""
    return load_catalog(CATALOG_PATH)


class RefinitivAdapter(MarketDataAdapter):
    """Refinitiv Data Platform market data adapter, bound to one bank."""

    def __init__(  # noqa: PLR0913 - constructor binds the full pull context
        self,
        db: Session,
        bank: Bank,
        bank_slug: str,
        token_provider: TokenProvider | None = None,
        transport: RdpTransport | None = None,
        actor_user_id: UUID | None = None,
    ) -> None:
        self._db = db
        self._bank = bank
        self._bank_slug = bank_slug
        self._token_provider = token_provider or SimulatedTokenProvider()
        self._transport = transport or UnconfiguredTransport()
        self._actor_user_id = actor_user_id
        self._catalog = _ric_catalog()

    # -- SourceAdapter (data_engine.md §5.1) --------------------------------

    def identify(self) -> AdapterIdentity:
        return AdapterIdentity(
            name="refinitiv_market_data",
            version=ADAPTER_VERSION,
            source_system="REFINITIV",
        )

    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        return ConnectionStatus(
            ok=False,
            detail=(
                "Market data adapters authenticate per pull cycle; use "
                "validate_credentials with a CredentialSet instead."
            ),
        )

    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        # Market data pulls are scope-driven, not table-driven: there is no
        # source schema to introspect.
        return SourceSchema(tables=())

    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        raise NotImplementedError(_NOT_AN_INGESTION_ADAPTER)

    def translate(
        self,
        raw_records: ExtractionResult,
        mapping_config: MappingConfig,
    ) -> CanonicalRecords:
        raise NotImplementedError(_NOT_AN_INGESTION_ADAPTER)

    def health_check(self) -> HealthStatus:
        return HealthStatus(
            healthy=True,
            detail=f"refinitiv adapter v{ADAPTER_VERSION} ({type(self._transport).__name__})",
        )

    # -- MarketDataAdapter (market_data_adapter.md §4.1) ---------------------

    def vendor_name(self) -> str:
        return VENDOR_NAME

    def authenticate(self, credentials: CredentialSet) -> AuthResult:
        return authenticate_credentials(self._token_provider, credentials)

    def validate_credentials(self, credentials: CredentialSet) -> AuthResult:
        # Lightweight token acquisition only (§4.1): the simulated provider
        # consumes no quota, and the Phase 2 live provider hits only the RDP
        # token endpoint, never a data endpoint.
        return authenticate_credentials(self._token_provider, credentials)

    def list_available_scopes(self) -> list[DataScope]:
        return supported_scopes(self._catalog)

    def estimate_quota_cost(
        self,
        scopes: list[DataScope],
        frequency: PullFrequency,
        institution_id: str,
    ) -> QuotaEstimate:
        consumption = current_month_usage(
            self._db, self._bank.organization_id, self._bank.id, VENDOR_NAME
        )
        return estimate(self._catalog, scopes, frequency, consumption, self._monthly_cap())

    def test_pull(
        self,
        credentials: CredentialSet,
        scopes: list[DataScope],
    ) -> TestPullResult:
        try:
            session_token, _ = acquire_session_token(self._token_provider, credentials)
            sample_values: dict[str, str] = {}
            for scope in scopes:
                extraction = self._extract_scope(session_token, scope, utc_now().date())
                sample_values.update(extraction.bundle.sample_values)
        except MarketDataError as exc:
            return TestPullResult(success=False, sample_values={}, error=exc.bank_facing.message)
        return TestPullResult(success=True, sample_values=sample_values, error=None)

    def pull(  # noqa: PLR0913 - §4.1 interface signature
        self,
        credentials: CredentialSet,
        scopes: list[DataScope],
        as_of_date: date,
        institution_id: str,
        batch_id: str,
    ) -> MarketDataPullResult:
        # A credential failure precedes any batch creation and raises the
        # classified MarketDataError (§4.3: bank-facing, never raw vendor).
        session_token, _ = acquire_session_token(self._token_provider, credentials)

        def extract(scope: DataScope) -> ScopeExtraction:
            return self._extract_scope(session_token, scope, as_of_date)

        return execute_pull(
            self._db,
            organization_id=self._bank.organization_id,
            bank=self._bank,
            bank_slug=self._bank_slug,
            vendor=VENDOR_NAME,
            adapter_version=ADAPTER_VERSION,
            scopes=scopes,
            as_of_date=as_of_date,
            extract=extract,
            quota_units=quota_units(self._catalog, scopes),
            actor_user_id=self._actor_user_id,
        )

    # -- Internals -----------------------------------------------------------

    def _extract_scope(
        self,
        session_token: str,
        scope: DataScope,
        default_rating_date: date,
    ) -> ScopeExtraction:
        """Fetch, parse, and translate one scope into a ScopeExtraction."""
        entry = self._catalog.entries.get(scope)
        if entry is None or not entry.supported:
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.SCOPE_NOT_PERMITTED, scope.value),
                internal_detail=f"scope {scope.value} is not supported by the RIC catalog",
            )
        specs = requests_for(self._catalog, scope)
        category = category_of(scope)
        if category is ScopeCategory.YIELD_CURVE:
            raw_payload, curve_observations = extract_curve(
                self._transport, session_token, scope, specs
            )
            bundle = curve_to_bundle(scope, curve_observations)
        elif category in _FX_CATEGORIES:
            raw_payload, fx_observations = extract_fx(self._transport, session_token, scope, specs)
            bundle = fx_to_bundle(scope, fx_observations)
        elif category is ScopeCategory.CREDIT_RATING:
            raw_payload, rating_observations = extract_ratings(
                self._transport, session_token, scope, specs
            )
            bundle = ratings_to_bundle(scope, rating_observations, default_rating_date)
        else:
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.SCOPE_NOT_PERMITTED, scope.value),
                internal_detail=f"no Refinitiv extractor for category {category.value}",
            )
        return ScopeExtraction(raw_payload=raw_payload, bundle=bundle)

    def _monthly_cap(self) -> int | None:
        """The bank's configured monthly cap for the current accounting month.

        Cap *configuration* is read when present; cap *enforcement* stays
        Phase 2 (§16.5) — the estimate only reports ``within_cap``.
        """
        row = (
            self._db.query(MarketDataQuotaUsage)
            .filter(
                MarketDataQuotaUsage.organization_id == self._bank.organization_id,
                MarketDataQuotaUsage.bank_id == self._bank.id,
                MarketDataQuotaUsage.vendor == VENDOR_NAME,
                MarketDataQuotaUsage.month == month_key(utc_now()),
            )
            .one_or_none()
        )
        return None if row is None else row.monthly_cap


register_market_data_adapter(VENDOR_NAME, RefinitivAdapter)
