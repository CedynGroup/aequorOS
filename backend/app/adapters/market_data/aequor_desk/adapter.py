"""The AequorOS desk market data adapter (spec §2 "desk-as-vendor").

Architecturally a vendor with no external counterparty — the same doctrine
as manual upload: the "credential" is a resolved handle to an approved desk
determination (``credentials={"determination_id": "<uuid>"}``), there is no
network, no quota (empty catalog, zero units), and the adapter is never
scheduled (``market_data_jobs`` excludes it alongside ``manual_upload``).
Pulls translate a determination's ``derived_values`` into the pull runner's
record shapes and delegate persistence to ``execute_pull`` with
``vendor="aequor_desk"`` — lineage, raw-tier preservation, supersession,
cache update, and the pipeline-refresh trigger are identical to vendor pulls.

Curve names are the proprietary AEQ codes (spec §8: ``AEQ.GHS.SOV.ZERO``,
``AEQ.GHS.SOV.FWD``, ``AEQ.GHS.OIS``) so desk-published curves COEXIST with
vendor curves — supersession is keyed (currency, curve_name), and publishing
under a vendor's curve name would silently overwrite vendor rows.

``derived_values`` contract this adapter translates (set by the desk
calculation pipeline; rates are PERCENT on the desk side and converted to
decimal fractions here, because the canonical CHECK is -1..1)::

    {
      "curves": {
        "AEQ.GHS.SOV.ZERO": {"curve_type": "zero",
                              "points": [{"tenor_months": 3, "rate_pct": 24.5}, ...]},
        ...
      },
      "reference_rates": {"GHS.MPR": 15.0, "GHS.GRR": 21.3},
      "fx_rates": {"USD/GHS": 12.85}
    }
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.adapters.market_data.base import (
    AuthResult,
    CredentialSet,
    MarketDataAdapter,
    MarketDataPullResult,
    QuotaEstimate,
    TestPullResult,
    register_market_data_adapter,
)
from app.adapters.market_data.errors import (
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)
from app.adapters.market_data.pull_runner import (
    CurvePoint,
    CurveRecord,
    FxRateRecord,
    IndexRecord,
    MarketDataBundle,
    ScopeExtraction,
    execute_pull,
)
from app.adapters.market_data.quota_tracker import estimate
from app.adapters.market_data.scope_taxonomy import DataScope, PullFrequency
from app.adapters.market_data.scope_translator import Catalog
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
from app.models import DeskDetermination

if TYPE_CHECKING:
    from uuid import UUID as UuidType

    from sqlalchemy.orm import Session

    from app.models import Bank

VENDOR = "aequor_desk"
ADAPTER_VERSION = "1"

_VENDOR_LABEL = "AequorOS desk"
# CREDENTIAL_INVALID's template renders "last updated: {timestamp}"; the desk
# has no cached-vendor history to point at.
_NO_CACHE_TIMESTAMP = "not available"

# Desk publications consume no vendor quota: the estimator runs against an
# empty catalog, so every scope contributes zero units and no cap applies
# (the manual-upload precedent).
_EMPTY_CATALOG = Catalog(source_path="<aequor_desk>", entries={})

# A determination in one of these states is publishable/re-publishable.
_PULLABLE_STATUSES = ("approved", "published")

# One FX pair this phase (Ghana-first); the map extends per expansion market.
_FX_SCOPE_BY_PAIR = {("USD", "GHS"): DataScope.FX_SPOT_USD_GHS}
_CURVE_SCOPE_BY_CURRENCY = {"GHS": DataScope.YIELD_CURVE_GHS}
# GHS.MPR / GHS.GRR ride the policy-rate-path scope: the closest taxonomy
# scope for administered/reference rates until a dedicated scope lands.
_REFERENCE_RATE_SCOPE = DataScope.MACRO_GHANA_POLICY_RATE_PATH

_PCT = Decimal("100")


# ---------------------------------------------------------------------------
# Determination -> pull-runner translation (shared with the publication
# service, which fans one determination out to every bank).
# ---------------------------------------------------------------------------


def _decimal(value: Any, *, context: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MarketDataError(
            render_bank_facing(
                BankFacingErrorCode.UNKNOWN_INSTRUMENT,
                vendor=_VENDOR_LABEL,
                scope=context,
            ),
            internal_detail=f"non-numeric desk value {value!r} at {context}",
        ) from exc


def _curve_currency(curve_code: str) -> str:
    # AEQ.GHS.SOV.ZERO -> GHS (spec §8 grammar: ISSUER.CURRENCY.SECTOR.TYPE).
    parts = curve_code.split(".")
    if len(parts) < 2 or len(parts[1]) != 3:  # noqa: PLR2004 - ISO 4217 length
        msg = f"desk curve code {curve_code!r} does not follow ISSUER.CURRENCY.*"
        raise MarketDataError(
            render_bank_facing(
                BankFacingErrorCode.UNKNOWN_INSTRUMENT,
                vendor=_VENDOR_LABEL,
                scope=curve_code,
            ),
            internal_detail=msg,
        )
    return parts[1]


def determination_scopes(determination: DeskDetermination) -> list[DataScope]:
    """The taxonomy scopes a determination's derived_values can serve."""
    derived = determination.derived_values or {}
    scopes: set[DataScope] = set()
    for curve_code in derived.get("curves", {}):
        scope = _CURVE_SCOPE_BY_CURRENCY.get(_curve_currency(curve_code))
        if scope is not None:
            scopes.add(scope)
    if derived.get("reference_rates"):
        scopes.add(_REFERENCE_RATE_SCOPE)
    for pair_key in derived.get("fx_rates", {}):
        base, _, quote = pair_key.partition("/")
        scope = _FX_SCOPE_BY_PAIR.get((base, quote))
        if scope is not None:
            scopes.add(scope)
    return sorted(scopes, key=lambda scope: scope.value)


def _curve_records(determination: DeskDetermination, currency: str) -> MarketDataBundle:
    bundle = MarketDataBundle()
    source_reference = f"desk:determination:{determination.id}"
    for curve_code, spec in (determination.derived_values or {}).get("curves", {}).items():
        if _curve_currency(curve_code) != currency:
            continue
        points = tuple(
            CurvePoint(
                tenor_months=int(point["tenor_months"]),
                # Desk works in percent; the canonical CHECK is -1..1.
                rate=_decimal(point["rate_pct"], context=f"{curve_code} point") / _PCT,
            )
            for point in spec.get("points", ())
        )
        bundle.curves.append(
            CurveRecord(
                currency=currency,
                curve_name=curve_code,
                curve_type=str(spec.get("curve_type", "")),
                source_reference=source_reference,
                points=points,
            )
        )
        if points:
            bundle.sample_values[f"{curve_code} {points[0].tenor_months}M"] = (
                f"{points[0].rate * _PCT:.2f}%"
            )
    return bundle


def _reference_rate_records(determination: DeskDetermination) -> MarketDataBundle:
    bundle = MarketDataBundle()
    for index_code, value in (determination.derived_values or {}).get(
        "reference_rates", {}
    ).items():
        rate = _decimal(value, context=index_code)
        bundle.indices.append(
            IndexRecord(
                index_code=index_code,
                value=rate,
                scenario="base",
                horizon_months=None,
                source_reference=f"desk:determination:{determination.id}/{index_code}",
            )
        )
        bundle.sample_values[index_code] = f"{rate:.2f}%"
    return bundle


def _fx_records(determination: DeskDetermination, scope: DataScope) -> MarketDataBundle:
    bundle = MarketDataBundle()
    for pair_key, value in (determination.derived_values or {}).get("fx_rates", {}).items():
        base, _, quote = pair_key.partition("/")
        if _FX_SCOPE_BY_PAIR.get((base, quote)) is not scope:
            continue
        rate = _decimal(value, context=pair_key)
        bundle.fx_rates.append(
            FxRateRecord(
                base_currency=base,
                quote_currency=quote,
                rate_type="spot",
                tenor_months=None,
                rate=rate,
                source_reference=f"desk:determination:{determination.id}/{base}{quote}",
            )
        )
        bundle.sample_values[f"{base}/{quote}"] = f"{rate:.4f}"
    return bundle


def build_extraction(determination: DeskDetermination, scope: DataScope) -> ScopeExtraction:
    """Translate one scope's slice of a determination into pull-runner shapes.

    Raises a bank-facing :class:`MarketDataError` when the determination
    carries nothing for the scope — at publication time scopes are derived
    from content, so this only fires for mismatched explicit pulls.
    """
    if scope in _CURVE_SCOPE_BY_CURRENCY.values():
        currency = next(c for c, s in _CURVE_SCOPE_BY_CURRENCY.items() if s is scope)
        bundle = _curve_records(determination, currency)
    elif scope is _REFERENCE_RATE_SCOPE:
        bundle = _reference_rate_records(determination)
    elif scope in _FX_SCOPE_BY_PAIR.values():
        bundle = _fx_records(determination, scope)
    else:
        bundle = MarketDataBundle()
    if bundle.record_count == 0:
        raise MarketDataError(
            render_bank_facing(
                BankFacingErrorCode.UNKNOWN_INSTRUMENT,
                vendor=_VENDOR_LABEL,
                scope=scope.value,
            ),
            internal_detail=(
                f"determination {determination.id} carries no derived values for {scope.value}"
            ),
        )
    raw_payload = {
        "determination_id": str(determination.id),
        "cob_date": determination.cob_date.isoformat(),
        "methodology_code": determination.methodology_code,
        "methodology_version": determination.methodology_version,
        "input_digest": determination.input_digest,
        "scope": scope.value,
        "derived_values": determination.derived_values,
        "qa_results": determination.qa_results,
    }
    return ScopeExtraction(raw_payload=raw_payload, bundle=bundle)


# ---------------------------------------------------------------------------
# The adapter.
# ---------------------------------------------------------------------------


class AequorDeskAdapter(MarketDataAdapter):
    """Serves desk-approved determinations into one bank's canonical store.

    Constructed per bank like every market data adapter; the publication
    service instantiates it (or calls ``execute_pull`` with the shared
    translation helpers) once per bank during fan-out.
    """

    def __init__(
        self,
        db: Session,
        bank: Bank,
        bank_slug: str,
        actor_user_id: UuidType | None = None,
    ) -> None:
        self._db = db
        self._bank = bank
        self._bank_slug = bank_slug
        self._actor_user_id = actor_user_id

    # -- SourceAdapter (data_engine.md §5.1) ---------------------------------

    def identify(self) -> AdapterIdentity:
        return AdapterIdentity(name=VENDOR, version=ADAPTER_VERSION, source_system="AEQUOR_DESK")

    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        try:
            determination = self._resolve_determination_id(config.location)
        except MarketDataError as exc:
            return ConnectionStatus(ok=False, detail=exc.bank_facing.message)
        return ConnectionStatus(
            ok=True,
            detail=f"determination {determination.id} ({determination.status})",
        )

    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        _ = config  # determinations are structured records, not tabular files
        return SourceSchema(tables=())

    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        raise NotImplementedError("market data adapters ingest via pull()")

    def translate(
        self,
        raw_records: ExtractionResult,
        mapping_config: MappingConfig,
    ) -> CanonicalRecords:
        raise NotImplementedError("market data adapters ingest via pull()")

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True, detail="aequor desk adapter ready")

    # -- MarketDataAdapter (market_data_adapter.md §4.1) ----------------------

    def vendor_name(self) -> str:
        return VENDOR

    def authenticate(self, credentials: CredentialSet) -> AuthResult:
        """There is no vendor to authenticate against: validity means the
        determination handle resolves to a publishable determination."""
        try:
            self._resolve_staged(credentials)
        except MarketDataError as exc:
            return AuthResult(
                success=False,
                session_token=None,
                expires_at=None,
                error_code=exc.bank_facing.code.value,
                error_message=exc.bank_facing.message,
            )
        return AuthResult(
            success=True,
            session_token=None,
            expires_at=None,
            error_code=None,
            error_message=None,
        )

    def validate_credentials(self, credentials: CredentialSet) -> AuthResult:
        return self.authenticate(credentials)

    def list_available_scopes(self) -> list[DataScope]:
        """The scopes the desk's Ghana-first golden copy can serve (spec §13
        Stage 1/2): the sovereign curve family, the reference-rate set, and
        the USD/GHS anchor. Grows with expansion markets."""
        return sorted(
            {
                *_CURVE_SCOPE_BY_CURRENCY.values(),
                _REFERENCE_RATE_SCOPE,
                *_FX_SCOPE_BY_PAIR.values(),
            },
            key=lambda scope: scope.value,
        )

    def estimate_quota_cost(
        self,
        scopes: list[DataScope],
        frequency: PullFrequency,
        institution_id: str,
    ) -> QuotaEstimate:
        _ = institution_id  # no per-institution vendor consumption to look up
        return estimate(_EMPTY_CATALOG, scopes, frequency, current_consumption=0, cap=None)

    def test_pull(
        self,
        credentials: CredentialSet,
        scopes: list[DataScope],
    ) -> TestPullResult:
        """Translate the determination and surface human-readable sample
        values for the requested scopes, persisting nothing."""
        try:
            determination = self._resolve_staged(credentials)
            sample_values: dict[str, str] = {}
            for scope in scopes:
                sample_values.update(build_extraction(determination, scope).bundle.sample_values)
        except MarketDataError as exc:
            return TestPullResult(success=False, sample_values={}, error=exc.bank_facing.message)
        return TestPullResult(success=True, sample_values=sample_values, error=None)

    def pull(  # noqa: PLR0913 - spec §4.1 signature
        self,
        credentials: CredentialSet,
        scopes: list[DataScope],
        as_of_date: date,
        institution_id: str,
        batch_id: str,
    ) -> MarketDataPullResult:
        """Translate the determination and hand the bundles to the pull runner.

        ``institution_id``/``batch_id`` come with the spec signature; the
        adapter is already bound to its bank and the runner mints the
        authoritative batch id, so both are informational here.
        """
        _ = institution_id, batch_id
        determination = self._resolve_staged(credentials)
        return execute_pull(
            self._db,
            organization_id=self._bank.organization_id,
            bank=self._bank,
            bank_slug=self._bank_slug,
            vendor=VENDOR,
            adapter_version=ADAPTER_VERSION,
            scopes=scopes,
            as_of_date=as_of_date,
            extract=lambda scope: build_extraction(determination, scope),
            quota_units=0,
            actor_user_id=self._actor_user_id,
        )

    # -- internals -------------------------------------------------------------

    def _resolve_staged(self, credentials: CredentialSet) -> DeskDetermination:
        raw = credentials.credentials
        handle = raw.get("determination_id") if isinstance(raw, dict) else None
        if not isinstance(handle, str) or not handle.strip():
            keys = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
            raise self._handle_error(
                f"credentials are missing a 'determination_id' handle (got: {keys})"
            )
        return self._resolve_determination_id(handle)

    def _resolve_determination_id(self, handle: str) -> DeskDetermination:
        try:
            determination_id = UUID(handle)
        except (ValueError, AttributeError, TypeError) as exc:
            raise self._handle_error(
                f"determination handle {handle!r} is not a UUID"
            ) from exc
        determination = self._db.get(DeskDetermination, determination_id)
        if determination is None:
            raise self._handle_error(f"determination {handle} does not exist")
        if determination.status not in _PULLABLE_STATUSES:
            raise self._handle_error(
                f"determination {handle} is {determination.status!r}, not approved/published"
            )
        return determination

    @staticmethod
    def _handle_error(internal_detail: str) -> MarketDataError:
        """The determination handle IS this adapter's credential; a handle
        that does not resolve to a publishable determination is a credential
        failure. ``internal_detail`` never reaches a bank-facing surface."""
        return MarketDataError(
            render_bank_facing(
                BankFacingErrorCode.CREDENTIAL_INVALID,
                vendor=_VENDOR_LABEL,
                timestamp=_NO_CACHE_TIMESTAMP,
            ),
            internal_detail=internal_detail,
        )


register_market_data_adapter(VENDOR, AequorDeskAdapter)
