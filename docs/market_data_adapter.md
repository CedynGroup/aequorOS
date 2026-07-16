# AequorOS Market Data Adapter

## Implementation Specification

**Status:** Draft v1.0
**Owner:** Dela Anthonio (CTO), Eric Inkoom Danso (CEO)
**Audience:** Engineering. Written to be implementable by an AI coding assistant (Claude Fable 5) under human review, and by human engineers directly.
**Companion documents:** `data_engine.md`, `storage.md`
**Purpose:** Define the Market Data Adapter framework that lives inside the AequorOS Data Engine, covering the unified market data abstraction, vendor-specific implementations for Bloomberg and Refinitiv (LSEG), the credential management model, the business-scope taxonomy, the plug-and-play authentication UI flow, quota management, caching and freshness, and integration with the canonical model.

---

## 1. Context and Scope

### 1.1 Relationship to the Data Engine

This specification extends the adapter framework defined in `data_engine.md` section 5. Market data adapters are a specialization of the base `SourceAdapter` interface. They obey every principle in `data_engine.md` (source-agnostic canonical model, mandatory metadata, immutable snapshots, lineage) and add market-data-specific capabilities (credential management, vendor-scope translation, quota tracking).

### 1.2 What Market Data Adapters Are For

The AequorOS calculation modules (IRRBB, Liquidity, FX, Basel Capital, FTP, Balance Sheet Forecasting) consume market data as inputs: yield curves for gap analysis and EVE, FX rates for currency translation and VaR, credit ratings for RWA weighting, macro forecasts for scenario projections. Without a way to plug institutional-grade market data into these modules, the modules can only run against manually-uploaded data or public sources.

The Market Data Adapter framework lets a bank connect its existing Bloomberg or Refinitiv (LSEG) subscription to AequorOS through a controlled authentication flow. Once connected, market data flows automatically into the canonical model, and the calculation modules consume it without knowing or caring which vendor produced it.

### 1.3 Auth and Licensing Model (Locked)

The bank holds the vendor subscription. AequorOS does not hold a Bloomberg or Refinitiv redistribution license. The bank provides AequorOS with scoped credentials that authorize AequorOS to pull data on the bank's behalf, using the bank's subscription quota. This is the per-bank-subscription model (Option A from prior design discussions), and it is the only sanctioned licensing pattern in this specification.

Implications:
- Credentials are per-institution, stored per the credential management pattern in `storage.md` section 7.
- Data pulled under the bank's subscription is used only for that bank's canonical model. It is not shared, aggregated, or resold to other institutions.
- The bank pays their vendor costs directly to Bloomberg or LSEG. AequorOS does not intermediate billing.
- Quota consumption is the bank's cost. AequorOS surfaces quota usage transparently so the bank can budget.

### 1.4 What This Document Specifies

- The `MarketDataAdapter` interface extending `SourceAdapter` (section 4)
- The canonical business-scope taxonomy hiding vendor field names from users (section 5)
- Bloomberg adapter implementation (section 6)
- Refinitiv (LSEG) adapter implementation (section 7)
- Manual upload fallback adapter (section 8)
- The plug-and-play authentication UI flow (section 9)
- Credential lifecycle management (section 10)
- Quota, caching, and freshness (section 11)
- Error handling and bank-facing messaging (section 12)
- Integration with the canonical model and storage (section 13)
- Phasing (section 14)

### 1.5 Non-Goals

- **Real-time / streaming market data.** ALM does not require tick-level data. End-of-day pulls are the default. Real-time is a Phase 3+ consideration if customer demand emerges.
- **Trading book market data for execution.** AequorOS is ALM software, not an OMS. Trading book positions can be consumed as inputs, but AequorOS does not consume real-time execution feeds.
- **Vendor-specific analytical models (e.g., BVAL analytics beyond the surfaced values).** Adapters extract data. Analytical enrichment lives in the Data Engine's enrichment layer per `data_engine.md` section 7.
- **Multi-tenant data pooling.** No data pulled under a bank's subscription is shared with any other bank's canonical model, ever.

---

## 2. Non-Negotiable Design Principles

These principles govern every implementation decision. They extend the principles in `data_engine.md` section 2 and `storage.md` section 2.

1. **Vendors are hidden from the bank user.** The bank Treasury operator never sees Bloomberg field mnemonics, Refinitiv RIC codes, or vendor-specific error messages in the AequorOS UI. All bank-facing surfaces speak in AequorOS business-level scopes.

2. **Credentials are the bank's property, held in escrow.** AequorOS stores credentials to make calls on the bank's behalf. Credentials are encrypted per-institution, retrievable only by the bank's service account, revocable by the bank at any time.

3. **Every market data pull is attributable.** Every canonical market data record traces to the specific vendor call that produced it, the credential used, the timestamp, and the quota consumed. Full lineage per `data_engine.md` section 8.

4. **Quotas are first-class.** The framework knows before every pull how much quota it will consume, tracks actual consumption post-pull, and enforces institution-level caps to prevent cost surprises.

5. **Cached data is authoritative when fresh, transparent when stale.** Calculations consume cached market data when it is within freshness bounds; when stale, either refresh or clearly attribute the staleness to the calculation output.

6. **The vendor certification process is separate from the software.** Building the adapter does not require completed vendor certification. Turning on production pulls against a real bank's credentials may require it. This is a business process concern, tracked separately, not a code concern.

7. **Fallback to manual upload always works.** If no vendor is connected, or the vendor connection fails, the Manual Upload adapter (section 8) covers the same canonical scopes. The bank is never blocked from operating AequorOS by a market data vendor issue.

8. **Adapter behavior is testable without vendor access.** Every vendor adapter ships with fixture-based tests using recorded (anonymized where necessary) vendor responses. Development does not require live Bloomberg or Refinitiv connections.

---

## 3. Architectural Overview

### 3.1 Where Market Data Fits in the Data Engine

Recall the Data Engine layers from `data_engine.md` section 3:

```
Layer 6: Metadata, Lineage, Audit
Layer 5: Analytical Store & Calculation Engines
Layer 4: Enrichment & Behavioral Overlay
Layer 3: Validation & Reconciliation
Layer 2: Canonical Data Model
Layer 1: Source Adapters              <-- Market Data Adapters live here
```

Market Data Adapters are Layer 1 adapters. They extract from Bloomberg / Refinitiv / manual upload and translate into the canonical `yield_curve`, `yield_curve_point`, `fx_rate`, `market_index` entities defined in `data_engine.md` section 4.2.

Downstream, Layer 3 validation ensures curves are complete and rates are within plausible bounds. Layer 4 enrichment may derive interpolated curve points at non-standard tenors. Layer 5 calculation engines consume the canonical market data to compute EVE, LCR, RWA, and everything else.

### 3.2 Directory Structure

```
/adapters/market_data/
  __init__.py
  base.py                    # MarketDataAdapter ABC (extends SourceAdapter)
  scope_taxonomy.py          # Canonical business-scope definitions
  scope_translator.py        # Translates scopes to vendor requests via catalogs
  credential_manager.py      # Vault-backed credential retrieval per storage.md
  quota_tracker.py           # Per-institution quota accounting
  cache.py                   # Market data cache with freshness rules
  scheduler.py               # Pull scheduling per institution
  errors.py                  # Bank-facing error classification

  /bloomberg/
    adapter.py               # Bloomberg MarketDataAdapter implementation
    auth.py                  # Bloomberg SVP credential handling
    field_catalog.yaml       # Scope-to-Bloomberg-field mappings
    extractors/
      curves.py
      fx.py
      security_master.py
      credit_data.py
      macro_series.py
    translators/
      curve_to_canonical.py
      fx_to_canonical.py
      security_to_canonical.py
      rating_to_canonical.py
      macro_to_canonical.py
    tests/
      fixtures/              # Recorded Bloomberg responses for offline testing
      test_extractors.py
      test_translators.py
      test_contract.py

  /refinitiv/
    adapter.py               # Refinitiv (LSEG) MarketDataAdapter implementation
    auth.py                  # OAuth 2.0 client credentials flow
    ric_catalog.yaml         # Scope-to-RIC mappings
    extractors/
      curves.py
      fx.py
      security_master.py
      credit_data.py
      macro_series.py
    translators/
      curve_to_canonical.py
      fx_to_canonical.py
      security_to_canonical.py
      rating_to_canonical.py
      macro_to_canonical.py
    tests/
      fixtures/
      test_extractors.py
      test_translators.py
      test_contract.py

  /manual_upload/
    adapter.py               # Fallback: Excel/CSV market data upload
    templates/               # Downloadable templates matching canonical scopes
      yield_curve_template.xlsx
      fx_rates_template.xlsx
      credit_ratings_template.xlsx
    tests/
      fixtures/
      test_upload.py
      test_contract.py
```

### 3.3 How a Pull Happens End-to-End

1. **Scheduler** determines a pull is due for institution X (e.g., end-of-day GHS yield curve pull).
2. **Credential Manager** retrieves institution X's vendor credentials from Vault.
3. **Quota Tracker** estimates the pull's cost against institution X's remaining quota. If insufficient, either delay, throttle, or alert the bank per configured policy.
4. **Adapter** (Bloomberg or Refinitiv) authenticates using the credentials, calls the vendor API for the requested scopes, receives raw vendor response.
5. **Extractor** parses the raw response into intermediate typed structures.
6. **Translator** produces canonical `yield_curve` / `fx_rate` / `market_index` records, stamped with mandatory metadata per `data_engine.md` section 4.3.
7. **Storage Layer** writes raw response to `raw` tier, canonical records to `canonical` tier per `storage.md` bucket-per-institution-per-tier pattern.
8. **Cache** is updated with the new fresh values.
9. **Quota Tracker** records actual consumption.
10. **Lineage** graph gets updated per `data_engine.md` section 8.
11. **Audit log** entry written per `storage.md` section 9.

Each step is separately testable. Each step handles its own errors and surfaces them through the error classification in section 12.

---

## 4. The `MarketDataAdapter` Interface

### 4.1 Interface Definition

`MarketDataAdapter` extends `SourceAdapter` from `data_engine.md` section 5.1. It adds market-data-specific capabilities.

```python
from abc import abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional
from uuid import UUID

# from adapters.base import SourceAdapter (per data_engine.md section 5.1)


class DataScope(Enum):
    """
    Canonical business-scope taxonomy. Vendor-agnostic.
    Every vendor adapter must be able to translate these to its own field references.
    Section 5 of this document defines the full taxonomy.
    """
    YIELD_CURVE_GHS = "YIELD_CURVE_GHS"
    YIELD_CURVE_USD = "YIELD_CURVE_USD"
    YIELD_CURVE_EUR = "YIELD_CURVE_EUR"
    YIELD_CURVE_GBP = "YIELD_CURVE_GBP"
    YIELD_CURVE_NGN = "YIELD_CURVE_NGN"
    YIELD_CURVE_KES = "YIELD_CURVE_KES"
    YIELD_CURVE_ZAR = "YIELD_CURVE_ZAR"

    FX_SPOT_USD_GHS = "FX_SPOT_USD_GHS"
    FX_SPOT_EUR_GHS = "FX_SPOT_EUR_GHS"
    FX_SPOT_GBP_GHS = "FX_SPOT_GBP_GHS"
    FX_SPOT_USD_NGN = "FX_SPOT_USD_NGN"
    # (full list per section 5)

    FX_FORWARD_USD_GHS_1M = "FX_FORWARD_USD_GHS_1M"
    FX_FORWARD_USD_GHS_3M = "FX_FORWARD_USD_GHS_3M"
    FX_FORWARD_USD_GHS_6M = "FX_FORWARD_USD_GHS_6M"
    FX_FORWARD_USD_GHS_12M = "FX_FORWARD_USD_GHS_12M"

    SECURITY_MASTER_GOG_BONDS = "SECURITY_MASTER_GOG_BONDS"
    SECURITY_MASTER_GOG_TBILLS = "SECURITY_MASTER_GOG_TBILLS"

    CREDIT_RATING_GHANA_SOVEREIGN = "CREDIT_RATING_GHANA_SOVEREIGN"
    CREDIT_RATING_NIGERIA_SOVEREIGN = "CREDIT_RATING_NIGERIA_SOVEREIGN"

    MACRO_GHANA_GDP_FORECAST = "MACRO_GHANA_GDP_FORECAST"
    MACRO_GHANA_INFLATION_FORECAST = "MACRO_GHANA_INFLATION_FORECAST"
    MACRO_GHANA_POLICY_RATE_PATH = "MACRO_GHANA_POLICY_RATE_PATH"


class PullFrequency(Enum):
    ON_DEMAND = "ON_DEMAND"
    HOURLY = "HOURLY"
    END_OF_DAY = "END_OF_DAY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


@dataclass(frozen=True)
class CredentialSet:
    """Vendor credentials for a specific institution. Retrieved from Vault."""
    institution_id: str
    vendor: str  # 'bloomberg' | 'refinitiv' | 'manual'
    credentials: dict  # vendor-specific opaque credential structure
    issued_at: datetime
    expires_at: Optional[datetime]  # None if credentials do not expire


@dataclass(frozen=True)
class AuthResult:
    success: bool
    session_token: Optional[str]  # short-lived session, not persisted
    expires_at: Optional[datetime]
    error_code: Optional[str]  # from BankFacingErrorCode enum in section 12
    error_message: Optional[str]  # bank-facing, actionable


@dataclass(frozen=True)
class QuotaEstimate:
    scopes: list[DataScope]
    frequency: PullFrequency
    estimated_units_per_pull: int
    estimated_monthly_units: int
    current_monthly_consumption: int
    monthly_cap: Optional[int]
    within_cap: bool


@dataclass(frozen=True)
class TestPullResult:
    """Small representative pull for the onboarding UI 'test' step."""
    success: bool
    sample_values: dict[str, str]  # human-readable, e.g. "GHS 3M: 15.80%"
    error: Optional[str]


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
    def pull(
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
```

### 4.2 What Every Concrete Adapter Must Do

Every implementation of `MarketDataAdapter`:

1. Implements every abstract method.
2. Consumes credentials only through `CredentialSet` obtained via the Credential Manager (section 10). Does not accept credentials from any other path.
3. Writes raw vendor responses to `raw` tier storage per `storage.md`, preserving the full response for lineage and audit.
4. Produces canonical records that satisfy `data_engine.md` section 4.3 mandatory metadata columns.
5. Reports quota consumption honestly to the Quota Tracker.
6. Surfaces errors through the error classification in section 12, not through raw vendor error messages.
7. Ships with contract tests that verify interface conformance across all methods.
8. Ships with fixture-based tests that do not require live vendor access.

### 4.3 Contract Tests

A single contract test suite runs against every `MarketDataAdapter` implementation. Any test that passes for one adapter but fails for another indicates the interface is leaking implementation details; fix the interface, not the test.

Contract test categories:
- **Authentication behavior:** invalid credentials produce `AuthResult(success=False)` with bank-facing error, not exceptions.
- **Scope coverage:** every scope returned by `list_available_scopes` is pullable by `test_pull` and `pull`.
- **Quota accounting:** post-pull quota consumption matches or exceeds `estimate_quota_cost`.
- **Canonical output:** every canonical record produced has the mandatory metadata columns populated.
- **Idempotency:** re-running the same pull with the same as-of-date produces canonical records that supersede rather than duplicate.
- **Error surfacing:** vendor errors are mapped to `BankFacingErrorCode` (section 12) and not leaked as raw messages.

---

## 5. Canonical Business-Scope Taxonomy

### 5.1 Principle

The bank Treasury operator selects "Ghana yield curve" in the UI, not "GHS0003M Curncy PX_LAST via BVAL." The taxonomy is the vocabulary the UI, calculation modules, and validation layer all speak. Vendor field names live only inside adapter catalogs (sections 6.2 and 7.2).

### 5.2 Scope Categories

**Yield curves.** `YIELD_CURVE_{CURRENCY}` where currency is one of GHS, USD, EUR, GBP, NGN, KES, ZAR (extensible as AequorOS expands). A yield curve pull produces canonical `yield_curve` and `yield_curve_point` records at standard tenors (1M, 3M, 6M, 12M, 24M, 36M, 60M, 84M, 120M) plus any additional tenors the vendor natively provides.

**FX spot rates.** `FX_SPOT_{CCY1}_{CCY2}` where the pair is denominated CCY1 per unit CCY2 (per the convention in the canonical `fx_rate` entity in `data_engine.md`). Primary pairs cover institution's reporting currency against all currencies present in its position book.

**FX forward rates.** `FX_FORWARD_{CCY1}_{CCY2}_{TENOR}` for standard tenors (1M, 3M, 6M, 12M). Used by hedge effectiveness testing and by forward-looking FX exposure modeling.

**Historical FX.** Not a scope; historical FX is derived from persisted spot pulls over time. The framework automatically retains 365+ business days of spot pulls for VaR calculations, per the retention rules in `storage.md` section 6.

**Security master.** `SECURITY_MASTER_{ISSUER_CATEGORY}` where category is one of GOG_BONDS, GOG_TBILLS, GOG_INFLATION_LINKED, CBN_BONDS, KES_BONDS, ZAR_BONDS, and category-specific extensions. Produces reference data for securities held or eligible: identifiers (CUSIP, ISIN), issuer, maturity, coupon, day count convention, callable flags.

**Credit ratings.** `CREDIT_RATING_{ISSUER}` for sovereigns and major corporate issuers relevant to the institution's counterparty book. Produces canonical `counterparty_rating` records with rating, agency, watch status, and date.

**Macroeconomic forecasts.** `MACRO_{COUNTRY}_{INDICATOR}` where indicator is one of GDP_FORECAST, INFLATION_FORECAST, POLICY_RATE_PATH, UNEMPLOYMENT_FORECAST, EXCHANGE_RATE_FORECAST. Produces canonical `market_index` records tagged with scenario (base, adverse, severely adverse) and horizon.

### 5.3 Adding New Scopes

Adding a new scope is a coordinated change:

1. Add the enum value to `DataScope` in `scope_taxonomy.py`.
2. Add the canonical mapping into `data_engine.md` section 4.2 entities if a new entity type is required (rare).
3. Add catalog entries in each vendor adapter's field/RIC catalog.
4. Add contract tests for the new scope.
5. Surface the new scope in the onboarding UI.

New scopes should be driven by real customer need. Do not preemptively add coverage for markets or instruments not on the roadmap; the taxonomy stays clean by being disciplined.

### 5.4 Scope-to-Vendor Coverage Matrix

Not every scope is supported by every vendor. The framework tracks coverage:

| Scope | Bloomberg | Refinitiv | Manual Upload |
|---|---|---|---|
| YIELD_CURVE_GHS | Yes (via BVAL) | Yes | Yes |
| YIELD_CURVE_USD | Yes | Yes | Yes |
| YIELD_CURVE_NGN | Yes | Yes | Yes |
| FX_SPOT_USD_GHS | Yes | Yes | Yes |
| FX_FORWARD_USD_GHS_* | Yes | Yes | Yes |
| SECURITY_MASTER_GOG_BONDS | Yes | Yes | Yes |
| CREDIT_RATING_GHANA_SOVEREIGN | Yes | Yes | Yes |
| MACRO_GHANA_GDP_FORECAST | Yes | Partial | Yes |

Coverage is defined authoritatively by each adapter's `list_available_scopes` at runtime, not by this table. The UI queries adapters live to know what to offer.

---

## 6. Bloomberg Adapter

### 6.1 Vendor Auth Pattern

Bloomberg subscriptions relevant to AequorOS integration are B-PIPE (enterprise data feed) and Bloomberg Data License with API access. Both use application-identifier-based auth: the bank provisions permissions on their subscription for a specific application identifier, and credentials scoped to that identifier are provided to AequorOS.

The credential structure stored in Vault (per `storage.md` section 7) contains:
- `application_identifier`: the Bloomberg-assigned identifier for the AequorOS application accessing this bank's subscription
- `serial_number`: subscription serial number
- `authentication_endpoint`: Bloomberg auth endpoint for this subscription
- `certificate`: SSL client certificate (Bloomberg uses cert-based auth for enterprise APIs)
- `subscription_tier`: which datasets the bank's subscription covers
- `contact_admin`: the bank's Bloomberg administrator (surfaced in error messages so the bank knows who to contact for credential issues)

Credential provisioning at the bank side is done through Bloomberg's admin portal. AequorOS provides step-by-step documentation in the onboarding UI (section 9); AequorOS does not automate the Bloomberg-side provisioning because the bank must approve permissions themselves.

### 6.2 Field Catalog

The Bloomberg field catalog (`bloomberg/field_catalog.yaml`) maps every supported `DataScope` to concrete Bloomberg data requests. Illustrative excerpt:

```yaml
YIELD_CURVE_GHS:
  data_source: BVAL
  fields:
    - security: "GHGGB1M Index"
      field: "PX_LAST"
      tenor_months: 1
    - security: "GHGGB3M Index"
      field: "PX_LAST"
      tenor_months: 3
    - security: "GHGGB6M Index"
      field: "PX_LAST"
      tenor_months: 6
    - security: "GHGGB12M Index"
      field: "PX_LAST"
      tenor_months: 12
    - security: "GHGGB2Y Index"
      field: "PX_LAST"
      tenor_months: 24
    - security: "GHGGB5Y Index"
      field: "PX_LAST"
      tenor_months: 60
    - security: "GHGGB10Y Index"
      field: "PX_LAST"
      tenor_months: 120
  quota_units_per_pull: 7
  supported: true

FX_SPOT_USD_GHS:
  security: "GHSUSD Curncy"
  field: "PX_LAST"
  quota_units_per_pull: 1
  supported: true

CREDIT_RATING_GHANA_SOVEREIGN:
  security: "GHANA Govt"
  fields:
    - "RTG_MDY_LT_LC_ISSUER_CREDIT"
    - "RTG_SP_LT_LC_ISSUER_CREDIT"
    - "RTG_FITCH_LT_LC_ISSUER_CREDIT"
  quota_units_per_pull: 3
  supported: true
```

Catalog entries are ground truth for what the adapter will pull. The bank sees "Ghana yield curve" in the UI; the adapter pulls seven Bloomberg securities per the catalog. Bank does not see security tickers.

Catalog is versioned. When Bloomberg deprecates a security or introduces a better data source, the catalog is updated; downstream canonical records remain unchanged in shape.

### 6.3 Extractors

Each extractor in `bloomberg/extractors/` handles one scope category. Extractors:
- Take an authenticated Bloomberg session
- Look up the scope in the catalog to find the request specification
- Make the vendor call
- Return typed intermediate structures
- Do not translate to canonical (that is the translator's job)
- Do not persist to storage (that happens after translation in the adapter's `pull` method)

### 6.4 Translators

Translators convert Bloomberg-shaped intermediate structures into canonical entities per `data_engine.md` section 4.2. Every canonical record produced has:
- `source_system = 'BLOOMBERG'`
- `source_reference` populated with the specific Bloomberg security or field reference
- `ingested_at` timestamp
- `ingestion_batch_id`
- `lineage_id` referencing the graph node for this pull
- All other mandatory metadata

### 6.5 Testing Without Live Access

Every extractor and translator ships with fixture-based tests. Fixtures are recorded Bloomberg responses stored under `bloomberg/tests/fixtures/`. Recording is a one-time (and infrequent) operation done against a Bloomberg dev environment when catalog entries are added or vendor response formats change. Day-to-day development, CI, and Fable 5 implementation work against fixtures without any live Bloomberg dependency.

---

## 7. Refinitiv (LSEG) Adapter

### 7.1 Vendor Auth Pattern

Refinitiv Data Platform (RDP) uses OAuth 2.0 client credentials flow. The bank creates an application in their Refinitiv account, generates a client ID and client secret scoped to specific data permissions, and provides them to AequorOS.

The credential structure stored in Vault contains:
- `client_id`: Refinitiv OAuth application identifier
- `client_secret`: OAuth client secret (encrypted per `storage.md` section 7)
- `scope`: OAuth scope defining which datasets are accessible
- `subscription_type`: RDP tier level
- `refresh_token`: for token renewal
- `token_endpoint`: Refinitiv OAuth endpoint
- `contact_admin`: bank's Refinitiv administrator

OAuth tokens are short-lived (typically 4 hours). The adapter refreshes tokens as needed within a pull cycle. Refresh token expiration is tracked and surfaced in the credential lifecycle monitoring (section 10).

### 7.2 RIC Catalog

The Refinitiv RIC catalog (`refinitiv/ric_catalog.yaml`) maps `DataScope` values to Refinitiv Instrument Codes (RICs) and data field references. Illustrative excerpt:

```yaml
YIELD_CURVE_GHS:
  rics:
    - ric: "GH1M="
      field: "TR.MidYield"
      tenor_months: 1
    - ric: "GH3M="
      field: "TR.MidYield"
      tenor_months: 3
    - ric: "GH6M="
      field: "TR.MidYield"
      tenor_months: 6
    - ric: "GH1Y="
      field: "TR.MidYield"
      tenor_months: 12
    - ric: "GH2Y="
      field: "TR.MidYield"
      tenor_months: 24
    - ric: "GH5Y="
      field: "TR.MidYield"
      tenor_months: 60
    - ric: "GH10Y="
      field: "TR.MidYield"
      tenor_months: 120
  quota_units_per_pull: 7
  supported: true

FX_SPOT_USD_GHS:
  ric: "USDGHS=R"
  field: "TR.MidPrice"
  quota_units_per_pull: 1
  supported: true

CREDIT_RATING_GHANA_SOVEREIGN:
  ric: "GH="
  fields:
    - "TR.MoodysIssuerRating"
    - "TR.SPIssuerRating"
    - "TR.FitchIssuerRating"
  quota_units_per_pull: 3
  supported: true
```

Same principle as Bloomberg: catalog is authoritative, bank sees scopes, adapter translates.

### 7.3 Extractors and Translators

Structurally identical to Bloomberg's setup. Extractors take an authenticated RDP session and pull scope data. Translators produce canonical records with `source_system = 'REFINITIV'` and appropriate `source_reference` values.

### 7.4 Testing

Same fixture-based approach as Bloomberg. RDP responses are recorded to fixtures. Development and CI work against fixtures without live RDP access.

---

## 8. Manual Upload Fallback Adapter

### 8.1 Purpose

Not every bank has Bloomberg or Refinitiv. Some banks will bring market data through Excel or CSV uploads, either because they use another data source (e.g., BoG for GHS curves, which is common in Ghana) or because their vendor subscription doesn't cover a scope AequorOS needs.

The Manual Upload adapter provides the same canonical output as Bloomberg and Refinitiv, sourced from operator-uploaded files matching AequorOS's provided templates.

### 8.2 Templates

Downloadable templates are shipped in `manual_upload/templates/`, one per scope category:
- `yield_curve_template.xlsx`
- `fx_rates_template.xlsx`
- `credit_ratings_template.xlsx`
- `macro_forecasts_template.xlsx`

Each template has:
- A header row with the exact column names the adapter expects
- A single example row showing format
- A comment or legend explaining each column
- Data validation (currency codes limited to ISO 4217, dates in ISO 8601, etc.)

The templates match the structure of the canonical entities they produce, translated into human-readable columns.

### 8.3 Upload Flow

The bank operator downloads the template, populates it (typically with data pulled from their own sources: BoG website for GHS curves, ECB for EUR curves, etc.), and uploads through the AequorOS UI. The adapter validates the file structure, translates to canonical records, and treats it as a pull with `source_system = 'MANUAL_UPLOAD'`.

Manual pulls do not consume vendor quota (there is no vendor) but do produce lineage and audit records identical to vendor pulls.

### 8.4 Why This Exists Even For Vendored Banks

Even banks with active Bloomberg or Refinitiv connections may need Manual Upload for scopes their subscription doesn't cover, or during vendor outages as a fallback. The Manual Upload adapter is always available in every institution, never disabled.

---

## 9. Plug-and-Play Authentication UI Flow

### 9.1 Design Principle

The bank Treasury operator experiences the same flow regardless of vendor. The flow hides vendor mechanics behind a consistent UX. AequorOS's job is to make first-time connection as smooth as possible; the flow must be honest about what the bank needs to do at the vendor side, but should never expose vendor implementation details.

### 9.2 The Flow

**Step 1: Choose data source.**

UI heading: "Connect a market data source."
Options presented as cards:
- **Bloomberg** — "Connect your existing Bloomberg B-PIPE or Data License subscription."
- **Refinitiv (LSEG)** — "Connect your Refinitiv Data Platform subscription."
- **Manual upload** — "Upload market data files directly. Use if you don't have Bloomberg or Refinitiv, or as a backup source."

Bank selects one. Multi-source is supported (a bank can have Bloomberg for FX and Manual Upload for GHS curves from BoG); the flow can be repeated to add more sources.

**Step 2: Verify subscription.**

For vendor sources, one confirmation screen: "You'll need an active Bloomberg B-PIPE or Data License subscription with API access permissions. Do you have one?" With a "Yes, continue" button and a "No, use manual upload instead" fallback.

For Manual Upload, skip this step.

**Step 3: Provide credentials.**

Vendor-specific. Documented in step-by-step form:

For Refinitiv:
- "Create an OAuth application in your Refinitiv Data Platform account. [Detailed guide with screenshots linked.]"
- Two input fields: Client ID, Client Secret
- Field hint: "These are provided when you create your OAuth application. If you don't have them, contact your Refinitiv account administrator."

For Bloomberg:
- "Contact your Bloomberg administrator to authorize AequorOS access to your subscription. Your Bloomberg admin will need to provision an application identifier. [Detailed guide.]"
- Multiple input fields per the Bloomberg credential structure (section 6.1)
- Upload for SSL client certificate

For Manual Upload: no credential collection; skip to Step 5.

Credentials are validated in real time as the operator enters them. Immediately upon submission, AequorOS calls `validate_credentials` on the adapter to confirm they work before proceeding.

**Step 4: Select data scope.**

UI heading: "Which market data do you want AequorOS to pull?"

Presented as grouped checkboxes:

**Yield curves:**
- [x] Ghana (GHS)
- [ ] United States (USD)
- [ ] Euro (EUR)
- [ ] Nigeria (NGN)
- (based on adapter's `list_available_scopes` and institution's active currencies from position data)

**FX rates:**
- [x] USD/GHS (spot + forwards)
- [x] EUR/GHS (spot + forwards)
- [ ] GBP/GHS
- (defaults auto-checked based on currencies present in institution's position book)

**Credit ratings:**
- [x] Ghana sovereign
- [ ] Nigeria sovereign

**Macro forecasts:**
- [ ] Ghana GDP forecast
- [ ] Ghana inflation forecast

For each selection, the UI shows the quota impact: "Selected scopes will consume approximately 42 units per daily pull, roughly 900 units per month against your subscription."

**Step 5: Test.**

AequorOS runs `test_pull` for the selected scopes and displays sample values:

"Test successful. Here's a sample of what we pulled:
- GHS 3-month rate: 15.80%
- USD/GHS spot: 12.85
- Ghana sovereign rating (S&P): CCC+

Does this look correct?"

Buttons: "Yes, activate this connection" / "Something looks wrong, let me adjust."

**Step 6: Schedule.**

UI heading: "When should AequorOS refresh this data?"

Defaults per scope category, editable:
- Yield curves: End of day (17:00 UTC, corresponding to end of GHS trading)
- FX spot: Hourly during market hours
- Credit ratings: Weekly
- Macro forecasts: Monthly

Timezone is the institution's timezone from onboarding config.

**Step 7: Activate.**

Connection is stored in the institution's config with credentials in Vault. First scheduled pull runs at the next scheduled time. Bank sees a confirmation: "Bloomberg connection active. First scheduled pull: today at 17:00."

### 9.3 Post-Onboarding Management

After onboarding, the bank operator has a "Market Data Sources" page in the AequorOS UI where they can:
- View active connections
- See last successful pull time per connection
- See quota consumption per connection
- Add or remove scopes
- Rotate credentials
- Add a new source (repeat the flow)
- Test any active connection at any time
- Temporarily disable a source (with fallback to another source if one is connected for the same scopes)

---

## 10. Credential Lifecycle Management

### 10.1 Credential Storage

Per `storage.md` section 7, credentials live in Vault at:
```
vault://institutions/{institution_id}/vendor_credentials/{vendor}/{credential_type}
```

Retrieved only by the institution's service account. Encrypted with the institution's CMEK. Access logged.

### 10.2 Credential States

A credential is in one of these states at any time:
- `ACTIVE`: valid, in use
- `EXPIRING_SOON`: will expire within warning threshold (default 30 days)
- `EXPIRED`: no longer valid
- `REVOKED`: bank has revoked at the vendor side
- `INVALID`: credentials fail authentication for reasons other than expiration
- `TESTING`: newly entered, not yet activated

State transitions are tracked. Every transition produces an audit log entry.

### 10.3 Health Checks

Every credential is health-checked daily:
- `validate_credentials` on the appropriate adapter
- Result classified into a state
- If state changes, alerts fire per notification config

For OAuth-based credentials (Refinitiv), refresh token expiration is tracked explicitly and warnings escalate as expiration approaches:
- 30 days out: informational notification to bank
- 7 days out: warning notification with call to action
- 1 day out: urgent notification, offer to guide through rotation
- Expired: block scheduled pulls, fall back to cache or manual upload for critical calculations, notify bank

### 10.4 Rotation Flow

The bank operator can initiate credential rotation from the Market Data Sources page. Rotation flow:
1. Bank generates new credentials at the vendor
2. Bank enters new credentials in AequorOS UI (same fields as initial setup)
3. AequorOS validates new credentials via `validate_credentials`
4. On success, AequorOS atomically swaps stored credentials
5. Old credentials are retained for a grace period (7 days) in case rollback is needed
6. Confirmation shown to bank

Old credentials in the grace period are marked as `REPLACED_PENDING_DELETION`; after 7 days they are cryptographically deleted.

### 10.5 Revocation

The bank can revoke a credential at any time:
- From AequorOS UI: marks credential as `REVOKED`, disables scheduled pulls using that credential, retains credential record for audit
- From vendor side directly: next `validate_credentials` fails, credential auto-marked `INVALID`, alerts fire

Revocation does not delete historical canonical data pulled with that credential. Historical data remains valid; the credential is simply no longer usable for new pulls.

### 10.6 Break-Glass

If AequorOS needs to execute a critical calculation (e.g., regulatory submission with a hard deadline) and credentials are unavailable, the framework falls back through this hierarchy:
1. Fresh data from any other active vendor connection covering the required scopes
2. Cached data from most recent successful pull, tagged as `STALE` with age
3. Manual upload prompt to the bank operator
4. Calculation blocks with clear attribution: "Cannot compute LCR: no fresh market data available for GHS yield curve."

The framework never silently substitutes stale data; every use of stale data is attributed in the calculation output.

---

## 11. Quota, Caching, and Freshness

### 11.1 Quota Tracking

Every institution has a quota model in the `quota_tracker`:
- Vendor-side subscription quota (as reported by the vendor or as configured by the bank based on their subscription tier)
- Monthly cap (configurable by bank, defaults to full subscription quota)
- Current month consumption (updated after every pull)
- Historical consumption by day, week, month

Displayed to the bank: "This month you've used 847 Bloomberg units. Your monthly cap is 5,000. Estimated end-of-month consumption at current pace: 1,240."

### 11.2 Quota Enforcement

Before every scheduled pull, the framework estimates cost via `estimate_quota_cost` and checks against remaining monthly quota. Behaviors:

- **Within cap:** proceed normally
- **Would exceed cap by <10%:** proceed with warning notification to bank
- **Would exceed cap by 10-25%:** pause pull, notify bank, offer to increase cap or defer pull
- **Would exceed cap by >25%:** block pull, notify bank urgently, fall back to cache
- **Vendor-side quota exhausted (subscription limit hit):** block pulls until vendor resets, notify bank

Bank can override cap enforcement per pull with explicit approval, logged in audit trail.

### 11.3 Caching

Every canonical market data record is cached with:
- Value
- As-of date
- Fresh-until timestamp (per freshness rules below)
- Source (which pull produced it)

Cache lives in the institution's `canonical` tier per `storage.md`. Access is via the same `StorageClient` interface; the "cache" is not a separate system, just a well-known storage location for the latest fresh values.

### 11.4 Freshness Rules

| Scope Category | Default Freshness | Rationale |
|---|---|---|
| Yield curves | Until end of next business day | Curves move daily |
| FX spot | 1 hour during market hours, until next open otherwise | Intraday movement matters for larger banks |
| FX forwards | Until end of next business day | Less volatile than spot |
| Security master | 7 days | Reference data rarely changes |
| Credit ratings | Daily | Watch/downgrade events are episodic but consequential |
| Macro forecasts | Monthly | Updated on quarterly or monthly cycles |

Freshness thresholds are configurable per institution.

### 11.5 Cache Behavior in Calculations

When a calculation module requests market data:
1. Look up canonical record for scope at required as-of date
2. Check freshness
3. If fresh, use it
4. If stale but a scheduled pull is imminent (within 1 hour), wait for the pull
5. If stale and no pull scheduled, trigger on-demand pull
6. If on-demand pull fails, use stale data with `stale = true` flag propagated into calculation output metadata

Calculation output metadata always records the freshness state of every market data input used. Regulatory reports produced from stale data are clearly attributed.

---

## 12. Error Handling and Bank-Facing Messaging

### 12.1 Error Classification

Vendor errors are classified into `BankFacingErrorCode` values. Each has:
- A code
- A bank-facing message template
- Recommended actions
- Escalation policy (informational, warning, urgent)

```python
class BankFacingErrorCode(Enum):
    CREDENTIAL_INVALID = "CREDENTIAL_INVALID"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    CREDENTIAL_REVOKED = "CREDENTIAL_REVOKED"
    SUBSCRIPTION_LAPSED = "SUBSCRIPTION_LAPSED"
    SCOPE_NOT_PERMITTED = "SCOPE_NOT_PERMITTED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    VENDOR_UNAVAILABLE = "VENDOR_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN_INSTRUMENT = "UNKNOWN_INSTRUMENT"
    STALE_DATA = "STALE_DATA"
    NETWORK_ERROR = "NETWORK_ERROR"
```

### 12.2 Sample Bank-Facing Messages

**CREDENTIAL_INVALID:**
"Your {vendor} credentials failed authentication. This usually means the credentials have been rotated or the permissions have changed at your end. Please check your {vendor} account or contact your {vendor} administrator. AequorOS will use cached market data (last updated: {timestamp}) until this is resolved."

Actions offered: "Update credentials," "View last successful pull," "Switch to manual upload."

**SUBSCRIPTION_LAPSED:**
"Your {vendor} subscription appears to have lapsed or been suspended. Please contact your {vendor} account manager. AequorOS will continue using cached data and manual upload for market data until your subscription is restored."

Actions offered: "Contact vendor," "Switch to manual upload," "Contact AequorOS support."

**QUOTA_EXHAUSTED:**
"You've reached your {vendor} monthly quota. Additional pulls will incur overage charges from {vendor}. AequorOS has paused automatic pulls to avoid unexpected costs. You can review your quota consumption and adjust your cap, or approve an override for critical pulls."

Actions offered: "Review quota," "Increase cap," "Approve override," "Contact vendor."

**SCOPE_NOT_PERMITTED:**
"Your {vendor} subscription does not include access to {scope}. Options: (1) upgrade your subscription to include this dataset, (2) remove {scope} from AequorOS data scope, (3) use manual upload for {scope}."

Actions offered: "Contact vendor about upgrade," "Remove from scope," "Use manual upload."

### 12.3 What Never Reaches the Bank

Raw vendor error messages, HTTP status codes, stack traces, internal identifiers, or debug information never appear in bank-facing surfaces. All of these are logged internally for AequorOS engineering debugging, but the bank sees only the classified, actionable message.

### 12.4 Escalation

Errors escalate through configured notification channels:
- In-app notification (always)
- Email to designated Treasury contact (per severity threshold)
- Slack/Teams webhook (if configured)
- SMS (for urgent codes, opt-in)

Notification frequency is throttled: the same error does not fire repeatedly. State changes fire; steady-state errors are summarized in a daily digest.

---

## 13. Integration with Canonical Model and Storage

### 13.1 Canonical Entities Produced

Market Data adapters produce canonical records in the following entities from `data_engine.md` section 4.2:
- `yield_curve` and `yield_curve_point`
- `fx_rate`
- `market_index`
- `counterparty_rating` (a specialized entity extending `counterparty` for rating time-series)

Extension entities specific to market data may be added over time; they must integrate cleanly with the canonical model per `data_engine.md` section 4.4.

### 13.2 Mandatory Metadata on Every Record

Every canonical market data record carries the mandatory metadata columns per `data_engine.md` section 4.3:
- `institution_id`
- `as_of_date`
- `ingested_at`
- `source_system` (e.g., 'BLOOMBERG', 'REFINITIV', 'MANUAL_UPLOAD')
- `source_reference` (vendor-specific identifier: Bloomberg security, RIC, or filename)
- `ingestion_batch_id`
- `validation_status`
- `lineage_id`

Records failing any of these are rejected at the adapter boundary; the framework does not silently accept malformed metadata.

### 13.3 Storage Layout

Per `storage.md` section 3.4:

**Raw tier** (`aequoros-{env}-{institution_id}-raw`):
```
market_data/{vendor}/{as_of_date}/{batch_id}/{scope}.json
```
Full raw vendor response preserved. Enables re-translation if translator logic is updated, and provides examination-grade audit evidence.

**Canonical tier** (`aequoros-{env}-{institution_id}-canonical`):
```
market_data/{entity_type}/{as_of_date}/{batch_id}/{records}.parquet
```
For example:
```
market_data/yield_curves/2026-04-30/b-2026-04-30-eod-001/GHS.parquet
market_data/fx_rates/2026-04-30/b-2026-04-30-eod-001/spot.parquet
```

**Outputs tier**: not used by market data adapters directly; downstream calculations that consume market data may write outputs here per their own rules.

### 13.4 Lineage

Every canonical market data record's `lineage_id` points to a lineage node capturing:
- The extraction operation (which adapter, which credentials, which pull)
- The vendor response (link to raw tier storage location)
- The translation operation (which translator version)
- The pull batch context

When an examiner asks "reproduce the yield curve used in the December 2027 LCR calculation," the lineage graph walks from LCR output back through canonical curve records back to the raw Bloomberg or Refinitiv response, at the exact version used.

### 13.5 Validation Integration

Market data records pass through the standard validation framework per `data_engine.md` section 6:
- **Structural:** required fields present, types correct
- **Business rules:** rates within plausible bounds (e.g., 0 <= yield <= 1.0 for interest rates)
- **Cross-source reconciliation:** if two adapters cover the same scope, values within tolerance (or explicit disagreement flagged)
- **Temporal:** rate movements from prior period within expected bounds; unusual movements flagged for operator review

Validation failures on market data follow the same severity levels (INFO / WARNING / ERROR / BLOCKER) as all other validation.

---

## 14. Phasing

### 14.1 MVP Phase (Months 1-9 of `data_engine.md` phasing)

**Goal:** framework and interface established; Manual Upload adapter production-ready; Bloomberg and Refinitiv adapters implemented as complete code with fixture-based testing but not yet connected to live vendor endpoints in production.

Deliverables:
- `MarketDataAdapter` interface and `DataScope` taxonomy
- `Manual Upload` adapter fully implemented and integrated with UI onboarding flow
- `Bloomberg` adapter implemented with catalog covering priority scopes (GHS curve, USD spot, GHS credit rating); tested against fixtures
- `Refinitiv` adapter implemented with catalog covering priority scopes; tested against fixtures
- Credential Manager integrated with Vault per `storage.md`
- Quota Tracker skeleton (tracking only, no enforcement policies beyond warning)
- Cache implemented on the `canonical` storage tier
- Onboarding UI flow (section 9) built for all three source types
- Contract tests passing across all three adapters
- Error classification and bank-facing messages implemented

Deferred:
- Live production pulls against Bloomberg and Refinitiv (waits on the bank-side subscription authorization for the first pilot bank that provides credentials)
- Advanced quota policies (cap enforcement, override workflow)
- Full macro forecast coverage
- Streaming / real-time pulls

### 14.2 Phase 2 (Months 9-15 of `data_engine.md` phasing)

Aligned with first paying bank customer:
- First live Bloomberg or Refinitiv connection activated for a real bank
- Advanced quota policies implemented
- Credential rotation UI hardened based on first-customer feedback
- Additional scope coverage as customer needs emerge

### 14.3 Phase 3+ (Months 15+)

- Additional vendor adapters as customer base expands (potentially LSEG-specific expansions, regional data providers)
- Streaming / real-time pulls if customer demand emerges
- Multi-source consensus for critical scopes (pull from both Bloomberg and Refinitiv, cross-check, alert on divergence)
- Historical data backfill capabilities for banks joining AequorOS with existing historical needs

---

## 15. Non-Goals and Explicit Deferrals

Things this document deliberately does not specify:
- **Vendor-specific analytical models** (e.g., Bloomberg BVAL surface analytics beyond the surfaced values). Adapters extract values; analysis lives in the Data Engine enrichment layer.
- **Trading applications.** AequorOS is ALM; execution feeds are out of scope.
- **Multi-vendor arbitration** for divergent values on the same scope. Basic behavior in Phase 1 is that the most-recently-refreshed source wins; sophisticated consensus is Phase 3.
- **Vendor billing integration.** AequorOS does not process vendor invoices or reconcile vendor charges.

Things this document is explicit about *not* wanting:
- **Vendor-specific concepts leaking into UI, calculations, or canonical model.** If a scope requires vendor-specific handling, that handling stays in the adapter.
- **Long-lived vendor credentials in application memory.** Credentials retrieved per pull cycle, discarded after use.
- **Silent fallback to stale data without attribution.** Every stale-data usage is tagged in calculation output.

---

## 16. Implementation Guidance for AI-Assisted Coding

Extends the guidance in `data_engine.md` section 17 and `storage.md` section 14.

1. **Build the `MarketDataAdapter` interface and `DataScope` taxonomy first.** Before any concrete adapter, the abstractions must be locked. Then Manual Upload (simplest), then Refinitiv (cleaner auth), then Bloomberg (most complex auth).

2. **Contract tests are the highest priority.** Same test suite runs against every adapter. Any divergence between adapters on the same test indicates the abstraction is leaking.

3. **Fixtures over live connections.** Every extractor and translator has fixture-based tests. Live connection tests are separate, run less frequently, and gated on vendor access. Do not require live connections for CI or day-to-day development.

4. **Do not invent vendor field names.** Bloomberg field mnemonics and Refinitiv RICs are documented by the vendors. If a specific field is uncertain, mark with a TODO and defer to Dela or Eric for verification. Do not populate the catalogs with guessed field names.

5. **Do not implement quota enforcement policies beyond warning in MVP.** Quota tracking (counting units) is MVP. Enforcement policies (blocking, capping, overriding) are Phase 2.

6. **Do not implement streaming or real-time pulls.** End-of-day scheduled pulls and on-demand pulls are the only sanctioned patterns. Streaming is Phase 3+ if it lands at all.

7. **Every canonical record produced has mandatory metadata populated.** Adapter code that produces records without full metadata is a bug, not a feature to be added later.

8. **Bank-facing messages are pre-authored text templates, not runtime-formatted vendor errors.** Do not let vendor errors reach the UI in raw form.

9. **When a scope's vendor catalog entry is unknown or uncertain, mark the scope as `supported: false` in the catalog.** The UI will not offer it. Do not fake support.

10. **When in doubt, defer to `data_engine.md` and `storage.md`.** If a market data design conflicts with either, the parent document wins. Surface the conflict.

---

## Appendix A: Terminology

- **Adapter.** Code that translates a specific vendor's data into the canonical model. Extends `SourceAdapter` from `data_engine.md`.
- **Catalog.** Vendor-specific YAML config mapping `DataScope` values to vendor field references (Bloomberg securities, Refinitiv RICs).
- **DataScope.** A canonical business-level identifier for a class of market data (e.g., `YIELD_CURVE_GHS`), independent of vendor.
- **Quota.** Vendor-side accounting unit consumed by pulls. Bloomberg and Refinitiv both meter subscriptions by units, though the specific units differ.
- **RIC.** Refinitiv Instrument Code. The identifier Refinitiv uses for a specific instrument.
- **Scope.** Shorthand for `DataScope`.
- **Vendor.** Bloomberg or Refinitiv (LSEG) as the source of market data. Manual Upload is treated architecturally like a vendor but has no external counterparty.

## Appendix B: Related Documents

- `data_engine.md` — parent Data Engine specification
- `storage.md` — storage layer specification (credential storage, canonical persistence)
- `/schema/canonical_v1/` — DDL for canonical entities produced by market data adapters
- `/product/onboarding_playbook.md` — customer onboarding process, of which market data connection is one step

---

**End of Market Data Adapter Specification v1.0**
