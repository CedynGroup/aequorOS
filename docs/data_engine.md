# AequorOS Data Engine

## Implementation Specification

**Status:** Draft v1.0
**Owner:** Dela Anthonio (CTO), Eric Inkoom Danso (CEO)
**Audience:** Engineering. Written to be implementable by an AI coding assistant (Claude Fable 5) under human review, and by human engineers directly.
**Purpose:** Define the architecture, canonical data model, adapter framework, validation layer, and implementation phasing for the AequorOS Data Engine — the ingestion and canonicalization layer that feeds all six AequorOS calculation modules.

---

## 1. Context and Strategic Framing

### 1.1 Why the Data Engine Is the Product

AequorOS's differentiators from Finastra, MORS, and Algorithmics are not the regulatory calculations themselves. Basel III RWA, LCR, EVE, NSFR, and FTP methodologies are well-known and documented. What sets AequorOS apart is **deployment speed** (4-8 weeks vs. incumbent 6-18 months) and **cost** (10-30% of incumbent all-in cost).

Both differentiators depend almost entirely on the Data Engine. A calculation engine that assumes a perfect canonical dataset is trivial. A calculation engine that can be productively fed from a T24 export, a Finacle database, three Excel spreadsheets, and a manual override table within eight weeks of a bank saying "yes" is the actual product.

**Design principle:** the Data Engine is not a data pipeline that supports the product. It *is* the product's central competitive moat. Architectural decisions here compound over years and across institution categories.

### 1.2 Institution Categories the Engine Must Support Over Time

AequorOS's expansion strategy commits to serving nine categories of African regulated financial institutions over ten years, starting with mid-tier commercial banks and extending through microfinance, credit unions, DFIs, pension funds, insurance, asset managers, capital markets, fintech, and corporate treasury.

The Data Engine's canonical model must accommodate this trajectory from day one. Building a bank-only model and rebuilding for pensions later is the single largest architectural mistake to avoid. The canonical model is designed as a generalized "regulated financial institution balance sheet" with bank as its first specialization.

### 1.3 What This Document Specifies

- The layered architecture (six layers, section 3)
- The canonical data model (section 4)
- The adapter framework and contract (section 5)
- The validation and reconciliation framework (section 6)
- The enrichment and behavioral overlay layer (section 7)
- The metadata, lineage, and audit substrate (section 8)
- The Temenos T24 adapter (section 9, framework only; full implementation deferred to Temenos developer portal access)
- The Excel/CSV adapter as MVP-critical path (section 10)
- Additional adapter patterns for Finacle, FlexCube, database-direct, and API-generic (section 11)
- Intelligence layer: schema mapping assistance, anomaly detection, reconciliation assistance (section 12)
- African-specific data sources beyond core banking (section 13)
- Security, compliance, and operational concerns (section 14)
- Phasing and milestones (section 15)
- Non-goals and explicit deferrals (section 16)

---

## 2. Non-Negotiable Design Principles

These principles govern every implementation decision. Deviations require explicit written justification.

1. **Adapters know sources. Nothing else does.** The only code in AequorOS that understands T24, Finacle, FlexCube, or any specific source system schema lives in adapters. Everything downstream reads only the canonical model.

2. **The canonical model is generalized for regulated financial institutions, not banks.** Bank-specific fields are modeled as extensions or specializations, not as the root model. This preserves optionality for the Tier 2+ expansion.

3. **Every data point is traceable to its source.** Full lineage is not optional. This is what makes AequorOS auditable to BoG and other regulators. No point exists in the canonical model without provenance metadata.

4. **Validation is a first-class layer, not an afterthought.** Regulatory-grade software cannot ship data quality as a feature to be added later. The validation framework is present from the first commit and every ingestion pass runs through it.

5. **Regulatory calculations are deterministic and auditable.** ML lives in the ingestion, enrichment, and behavioral layers. It does not live inside the regulatory calculation engines themselves. A CAR figure must be reproducible from inputs, not "the model decided."

6. **Idempotent ingestion.** Re-running an ingestion pass with the same inputs must produce the same canonical state. Rebuilds must be possible from source.

7. **Immutability of accepted state.** Once a snapshot is accepted, it is immutable. Corrections produce new snapshots with clear supersession, not overwrites.

8. **Excel is a first-class data source, not a workaround.** Every mid-tier African bank will have material data in Excel. Designing Excel handling as "the fallback" produces a brittle product.

9. **Fail loudly on data quality.** Silent tolerance of bad data is the worst outcome. Ingestion halts on blocker-level validation failures. Warnings surface to a data quality dashboard visible to bank and AequorOS operators.

10. **Multi-tenancy with strict isolation.** Bank A's data is never visible to bank B's users, ever, under any code path. Multi-tenant learning aggregates behavioral statistics anonymously; it does not share transactional data.

---

## 3. Layered Architecture

The Data Engine is six layers. Data flows from source to canonical to calculation. Metadata flows across all layers.

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 6: Metadata, Lineage, Audit                          │
│  (spans all layers; every operation logged)                  │
├─────────────────────────────────────────────────────────────┤
│  Layer 5: Analytical Store & Calculation Engines            │
│  (read-only consumers of canonical model)                   │
├─────────────────────────────────────────────────────────────┤
│  Layer 4: Enrichment & Behavioral Overlay                   │
│  (behavioral maturities, product mappings, MTM, overrides)  │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: Validation & Reconciliation                       │
│  (balance rec, referential, business rules, temporal)       │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: Canonical Data Model                              │
│  (source-agnostic representation of the bank)               │
├─────────────────────────────────────────────────────────────┤
│  Layer 1: Source Adapters                                   │
│  (T24, Finacle, FlexCube, Excel/CSV, DB-direct, API-generic)│
└─────────────────────────────────────────────────────────────┘
```

Each layer has a strict contract with the layer above it. Adapters do not directly write to the analytical store. Calculation engines do not directly read raw source data.

---

## 4. Canonical Data Model

### 4.1 Design Approach

The canonical model is defined as a set of related entities with typed fields and explicit metadata. It is expressed in PostgreSQL DDL for the operational store and mirrored in Snowflake for analytical workloads. Every entity carries mandatory audit columns.

### 4.2 Core Entities

Below is the entity list with primary purpose. Full DDL is expanded in `/schema/canonical_v1.sql` (to be created; see section 4.5 for the DDL structure).

**Institution and Organization**
- `institution` — the tenant (a specific bank or, later, pension fund/insurer)
- `institution_type` — enum: `UNIVERSAL_BANK`, `RURAL_BANK`, `SAVINGS_LOANS`, `MICROFINANCE`, `PENSION_FUND`, `INSURANCE_LIFE`, `INSURANCE_PC`, `ASSET_MANAGER`, `CORPORATE_TREASURY`, etc.
- `business_unit` — branches, divisions, subsidiaries within an institution
- `reporting_entity` — the entity that a given calculation is reported for (may differ from institution for consolidated reporting)

**Chart of Accounts and Product Taxonomy**
- `gl_account` — the general ledger account (with hierarchy)
- `product` — product definitions (e.g., "5-year fixed corporate loan GHS")
- `product_category` — regulatory category mapping (e.g., "Corporate unrated 100% RW")
- `product_to_gl_mapping` — links products to GL accounts

**Counterparties**
- `counterparty` — the entity on the other side of any position (customer, bank, sovereign, etc.)
- `counterparty_type` — enum: `RETAIL_INDIVIDUAL`, `SME`, `CORPORATE`, `BANK_OECD`, `BANK_NON_OECD`, `SOVEREIGN`, `MULTILATERAL_DEV_BANK`, etc.
- `counterparty_rating` — credit rating (internal or external)
- `counterparty_relationship` — connection between counterparties (group exposure)

**Positions (the balance sheet)**
- `position` — a unified abstraction for anything on or off the balance sheet at a point in time
  - `position_type`: `LOAN`, `DEPOSIT`, `SECURITY_HOLDING`, `DERIVATIVE`, `CASH`, `INTERBANK_PLACEMENT`, `INTERBANK_BORROWING`, `LC_GUARANTEE`, `COMMITMENT_UNDRAWN`, etc.
  - Every position has: `institution_id`, `counterparty_id`, `product_id`, `currency`, `notional`, `balance`, `origination_date`, `contractual_maturity`, `next_repricing_date`, `interest_rate`, `rate_type` (`FIXED`/`FLOATING`), `rate_index`, `rate_spread`, `ifrs9_stage`, `collateral_id`, and full metadata
- `position_snapshot` — the position's state as of a specific `as_of_date` (this is the immutable record)
- `cashflow_schedule` — projected cashflows from a position (amortization schedules)
- `collateral` — collateral pledged against loans, with type, value, haircut

**Transactions**
- `transaction` — a money movement (disbursement, repayment, interest accrual, fee, FX conversion, etc.)
- `transaction_type` — enum
- Transactions link to positions and GL accounts

**Market Data**
- `yield_curve` — a full curve as of a date (base curve, credit spread curves, FX forward curves)
- `yield_curve_point` — tenor + rate points on a curve
- `fx_rate` — spot and forward rates
- `market_index` — reference index values (BoG policy rate, GHS-IBOR, etc.)

**Behavioral and Assumption Layer**
- `behavioral_assumption` — deposit stability, prepayment, credit conversion factors, non-maturity deposit duration
- `assumption_source` — enum: `POLICY`, `ML_MODEL`, `MANUAL_OVERRIDE`, `REGULATOR_MANDATED`
- `manual_adjustment` — off-model adjustments with user, timestamp, and business reason

**Capital and Regulatory**
- `capital_component` — CET1, AT1, Tier 2 components at institution level
- `regulatory_adjustment` — deductions (goodwill, DTA, etc.)
- `rwa_bucket` — risk-weighted asset assignments by exposure

**Snapshots and Time**
- Every entity above has a `snapshot` variant tied to `as_of_date`
- All calculations are performed against a snapshot; historical restatements produce new snapshots, never overwrites

### 4.3 Mandatory Metadata Columns

Every entity in the canonical model carries:

- `id` (UUID)
- `institution_id` (tenant scope)
- `as_of_date` (business date this record represents)
- `ingested_at` (system timestamp of ingestion)
- `source_system` (adapter that produced this record: `T24`, `EXCEL`, `MANUAL`, etc.)
- `source_reference` (the source system's own identifier for this record; e.g., T24 arrangement ID)
- `ingestion_batch_id` (links to the specific ingestion run)
- `validation_status` (`ACCEPTED`, `WARNING`, `ERROR`, `BLOCKED`)
- `lineage_id` (foreign key into the lineage table; see section 8)
- `superseded_by` (nullable; points to a newer version of this record if restated)
- `created_by` (system or user)
- `created_at` (row insertion timestamp)

Fields cannot be nullable that describe *what* a record is (e.g., `position_type`, `currency`). Fields can be nullable that describe *behavioral overlays* (e.g., `behavioral_maturity`) because those are enriched, not raw.

### 4.4 Extensibility for Non-Bank Institutions

The canonical model uses several patterns to accommodate future institution categories without schema rewrites:

- **`position_type` is an enum, not a fixed set of tables.** Adding `PENSION_LIABILITY` or `INSURANCE_RESERVE` later requires adding an enum value and any specific attributes as extension tables, not restructuring the position table.
- **Extension tables per institution type.** Bank-specific data (like Basel III capital tiers) lives in `position_extension_bank`. Pension-specific data (like actuarial liability projection assumptions) will live in `position_extension_pension`.
- **Behavioral assumptions are keyed by institution type.** Deposit run-off rates apply to banks; policyholder lapse rates will apply to insurers. Same table structure, different domain values.
- **The GL structure is deliberately loose.** Different institution categories will have very different chart-of-account structures. The GL hierarchy accommodates arbitrary depth.

### 4.5 DDL Structure

The DDL is organized into files under `/schema/`:

```
/schema/
  /canonical_v1/
    001_institution.sql
    002_gl_accounts.sql
    003_products.sql
    004_counterparties.sql
    005_positions.sql
    006_position_snapshots.sql
    007_transactions.sql
    008_market_data.sql
    009_behavioral.sql
    010_capital.sql
    011_extensions_bank.sql
    099_indexes.sql
    100_partitioning.sql
```

Each file is idempotent (uses `CREATE TABLE IF NOT EXISTS`) and includes:
- Table definition with all columns, types, constraints
- Foreign key relationships
- Check constraints for business rules that must always hold
- Comment blocks documenting the entity's purpose and expected use
- The mandatory metadata columns

Partitioning strategy: `position_snapshot` and `transaction` are partitioned by `institution_id` and `as_of_date` for performance at scale. Retention rules preserve at least 7 years of history per regulatory requirements.

### 4.6 Guidance for Implementation

When Fable 5 or a human engineer builds the canonical model:

- Do not shortcut the metadata columns for "speed." Every table gets them.
- Do not use bank-specific naming in the base tables. Reserve bank-specific naming for the `_extension_bank` tables.
- Use `NUMERIC(28, 6)` for monetary amounts. `DECIMAL(18, 2)` is common in accounting systems but insufficient for high-precision interim calculations. AequorOS reports at 2 dp but calculates at 6.
- Use `DATE` for business dates (`as_of_date`) and `TIMESTAMPTZ` for system timestamps (`ingested_at`).
- Use ISO 4217 for currency codes, ISO 3166-1 alpha-2 for country codes.
- Never store rate as a percentage. Always store as a decimal (`0.245` not `24.5`).

---

## 5. Adapter Framework

### 5.1 The Adapter Contract

Every adapter, regardless of source system, implements the same interface. This is what makes the platform source-agnostic.

The adapter interface (expressed in Python for clarity; final implementation in the chosen backend language):

```python
class SourceAdapter(ABC):
    """
    Contract every source adapter must satisfy.
    Adapters are the ONLY code that knows the source system's schema.
    """

    @abstractmethod
    def identify(self) -> AdapterIdentity:
        """Return adapter name, version, and supported source system."""

    @abstractmethod
    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        """Test that this adapter can reach and read the source with the given config."""

    @abstractmethod
    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        """
        Introspect the source and return what it looks like.
        For T24: return known tables and columns available.
        For Excel: return sheet names and column headers.
        For CSV: return headers.
        """

    @abstractmethod
    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        """
        Pull raw data from the source for the given business date and entity types.
        Returns raw records in the source's native shape.
        Does not translate. Does not validate business rules.
        """

    @abstractmethod
    def translate(
        self,
        raw_records: ExtractionResult,
        mapping_config: MappingConfig,
    ) -> CanonicalRecords:
        """
        Translate raw source records into canonical model records.
        Uses the bank-specific MappingConfig to know which source
        field maps to which canonical field.
        Returns canonical records that have NOT YET been validated.
        """

    @abstractmethod
    def health_check(self) -> HealthStatus:
        """Runtime health status of this adapter instance."""
```

### 5.2 Configuration Model

Each adapter is configured per-bank via a `MappingConfig`. This config is versioned, auditable, and stored in the AequorOS database, not in code.

```yaml
# Example MappingConfig for a hypothetical bank on T24
institution_id: "cd8f1e12-..."
adapter: "temenos_t24"
adapter_version: "1.0"
connection:
  type: "api"  # or "sftp_batch" as fallback
  endpoint: "https://bank-t24.internal/tafj"
  auth_ref: "vault://banks/xyz/t24_credentials"

field_mappings:
  position:
    source_table: "AA.ARRANGEMENT"
    fields:
      canonical.source_reference: "$.arrangementId"
      canonical.counterparty_id: "$.customerId"
      canonical.product_id: "$.productLine"
      canonical.currency: "$.currency"
      canonical.balance: "$.outstandingAmount"
      canonical.contractual_maturity: "$.maturityDate"
      canonical.interest_rate: "$.effectiveInterestRate"
      canonical.rate_type: "$.interestType"  # requires enum mapping

  # ... other entity mappings

enum_mappings:
  rate_type:
    "F": "FIXED"
    "V": "FLOATING"
    "FIXED": "FIXED"
    "FLOAT": "FLOATING"

product_mappings:
  # This bank's product codes to canonical product categories
  "LN.CORP.5Y": "CORPORATE_LOAN_UNRATED_100RW"
  "LN.RETAIL.MORT": "RESIDENTIAL_MORTGAGE_35RW"
  # ...

schedule:
  frequency: "DAILY"
  time: "03:00 UTC"
  retries: 3
```

The `MappingConfig` is the deliverable of onboarding. It captures every bank-specific translation rule in one place, versioned and reviewable.

### 5.3 Extraction Modes

Adapters support two extraction modes:

**Full extraction.** Pulls the complete state of the bank as of a business date. Used for initial load and periodic full reconciliation.

**Incremental extraction.** Pulls only what has changed since the last extraction. Used for daily operational runs. Adapters must handle deletes as well as inserts and updates (soft-delete markers in canonical).

Adapters are responsible for exposing whichever mode the source supports. Not all sources support incremental (Excel drops are typically full-refresh; T24 API supports incremental).

### 5.4 Error Handling

An adapter's contract for errors:

- Connection failures: retry with exponential backoff, then surface as `ConnectionStatus.FAILED` with actionable detail.
- Extraction failures for a single record: log, mark the record for review, continue processing the batch. Do not fail the entire batch on one bad record.
- Extraction failures for the entire source: fail the batch loudly. Notify operators. Do not silently produce a partial canonical state.
- Translation failures: any record that cannot be translated goes to a `translation_failures` table with the raw source record preserved. This is essential for onboarding, when mappings are being refined.

### 5.5 Adapter Testing

Every adapter ships with:

- **Contract tests.** Verify the adapter implements the `SourceAdapter` interface correctly.
- **Fixture-based tests.** Sample source data (real anonymized where available, synthetic otherwise) that exercises every canonical field the adapter can produce.
- **Contract-conformance tests.** Given a fixture, translate it, and verify the canonical records satisfy the canonical model's constraints.
- **Integration tests against a source sandbox.** For T24, this uses the Temenos developer sandbox (once available). For Excel, a library of synthetic Excel files representing common patterns.

---

## 6. Validation and Reconciliation Framework

### 6.1 Categories of Validation

Every ingestion pass runs canonical records through five validation categories, in order:

**Category 1: Structural.** Types, non-null constraints, enum values, referential integrity within the ingested batch (every position's counterparty exists, every collateral's loan exists, etc.).

**Category 2: Business rules.** Rates within plausible bounds (0% - 100% for interest rates; anything outside flagged). Tenors non-negative. Maturity dates in future for open positions. Currency codes valid ISO. Product mappings resolved.

**Category 3: Balance reconciliation.** GL balances from the accounting system reconcile with sub-ledger totals from the core banking system, within a configurable tolerance (default ± 0.1%).

**Category 4: Cross-source reconciliation.** When multiple sources exist (e.g., T24 sub-ledger and a separate accounting GL), balances must tie. Where they don't, the discrepancy is reported with drill-down to the account level.

**Category 5: Temporal.** Today's balances roll forward reasonably from yesterday's given known transactions. Sudden unexplained changes above threshold trigger review. New position IDs that don't correspond to known transactions raise warnings.

### 6.2 Severity Levels

Every validation check emits at one of four severity levels:

- **INFO.** Something worth noting. Ingestion proceeds. Surfaces to operator dashboard.
- **WARNING.** Something abnormal but not blocking. Ingestion proceeds. Calculations may still run but flag the affected data.
- **ERROR.** Serious data quality issue. The affected records are excluded from calculations. Ingestion continues for other records.
- **BLOCKER.** The entire ingestion batch is rejected. No calculations run against this batch. Requires human resolution before retry.

Severity thresholds are configurable per-bank via `ValidationConfig`, because different banks have different tolerances during onboarding.

### 6.3 Validation Rules as Configuration

Validation rules are expressed as configuration, not hard-coded. This is critical for onboarding where different banks have different data quality realities.

```yaml
# Example ValidationConfig
institution_id: "cd8f1e12-..."

rules:
  - name: "gl_subledger_reconciliation"
    category: "BALANCE_RECONCILIATION"
    tolerance_percent: 0.1
    severity: "BLOCKER"

  - name: "position_rate_bounds"
    category: "BUSINESS_RULES"
    condition: "position.interest_rate BETWEEN 0.0 AND 1.0"
    severity: "ERROR"

  - name: "unusual_balance_change"
    category: "TEMPORAL"
    condition: "abs(position.balance - previous.balance) / previous.balance > 0.4"
    severity: "WARNING"

  - name: "product_mapping_completeness"
    category: "STRUCTURAL"
    condition: "position.product_id IS NOT NULL AND product.regulatory_category IS NOT NULL"
    severity: "ERROR"
```

### 6.4 The Validation Report

Every ingestion batch produces a machine-readable validation report:

```json
{
  "batch_id": "b-2026-05-21-1a2b3c",
  "institution_id": "cd8f1e12-...",
  "as_of_date": "2026-05-20",
  "ingested_at": "2026-05-21T03:14:52Z",
  "summary": {
    "records_extracted": 145382,
    "records_translated": 145380,
    "records_accepted": 145127,
    "records_warning": 231,
    "records_error": 22,
    "records_blocked": 0,
    "overall_status": "ACCEPTED_WITH_WARNINGS"
  },
  "reconciliation": {
    "gl_vs_subledger": {
      "gl_total": "2405829145.32",
      "subledger_total": "2405831022.10",
      "difference": "-1876.78",
      "difference_percent": "-0.0000780",
      "within_tolerance": true
    }
  },
  "failures": [
    {
      "rule": "position_rate_bounds",
      "severity": "ERROR",
      "record_id": "pos-4a1e...",
      "source_reference": "AA.ARRANGEMENT/12983421",
      "detail": "interest_rate=1.75 exceeds bound 1.0"
    }
    // ... other failures
  ]
}
```

Every bank operator using AequorOS sees this dashboard. Every AequorOS operator monitoring the platform sees it. Every regulator examining AequorOS sees a version of it.

### 6.5 Reconciliation as a Product Feature

Reconciliation isn't just internal quality control. It is a product feature banks buy. Mid-tier banks routinely have unreconciled positions between their core and their GL and don't know it. AequorOS surfacing these is genuine value.

Reconciliation reports are user-facing artifacts, not just internal logs. The bank's CFO should be able to see, in the AequorOS UI, exactly where GL and sub-ledger disagree.

---

## 7. Enrichment and Behavioral Overlay Layer

### 7.1 What Gets Enriched

Raw source data is insufficient for ALM. The enrichment layer adds:

- **Behavioral maturity for non-maturity deposits.** Current accounts and savings accounts have no contractual maturity. Their effective duration must be estimated. This is either (a) a bank policy value, (b) an ML model output, or (c) a manual override. All three paths must be supported, with clear provenance.
- **Prepayment curves for term loans.** Historical prepayment behavior projected forward.
- **Product-to-regulatory-category mapping.** Each product mapped to the appropriate Basel III / BoG regulatory category with the correct risk weight.
- **Credit conversion factors for off-balance-sheet exposures.** LC, guarantees, undrawn commitments.
- **Mark-to-market for securities and derivatives.** Positions valued at current market rates.
- **Currency translation.** Multi-currency positions translated to the reporting currency at consistent rates.
- **IFRS 9 staging.** Loans classified into Stage 1, 2, or 3 based on credit quality signals.

### 7.2 Overlay Application Order

Enrichments apply in a defined order. This order matters because later enrichments may depend on earlier ones.

1. Currency translation (so all subsequent calculations can operate in reporting currency)
2. Product-to-regulatory-category mapping
3. Behavioral maturity assignment
4. Prepayment / lapse assumption overlay
5. Mark-to-market
6. IFRS 9 staging
7. Manual overrides (applied last so operators can override anything above)

### 7.3 Provenance Requirement

Every enriched field carries a `provenance` sub-record:

```json
{
  "value": "60_MONTHS",
  "source": "ML_MODEL",
  "model_id": "nmd_duration_v2.3",
  "confidence": 0.87,
  "as_of": "2026-05-21T00:00:00Z",
  "override": null
}
```

Or, if manually overridden:

```json
{
  "value": "48_MONTHS",
  "source": "MANUAL_OVERRIDE",
  "model_id": "nmd_duration_v2.3",
  "original_value": "60_MONTHS",
  "override": {
    "user_id": "u-...",
    "reason": "Bank policy: cap NMD duration at 4 years",
    "timestamp": "2026-05-21T14:22:11Z"
  }
}
```

This is what makes the enrichment layer auditable.

### 7.4 Behavioral ML Models

For behavioral assumptions with ML backing, models live in a dedicated `/models/` module separate from adapters and calculation engines. Each model:

- Has an explicit input contract (what canonical fields it reads)
- Has an explicit output contract (what values it produces, with confidence)
- Is versioned, with model versions preserved indefinitely
- Is subject to MRM discipline (documented, validated, monitored per SR 11-7 principles)
- Is opt-in per institution (some banks will require policy-based, not model-based)

Details of specific ML models (NMD duration, prepayment, deposit stability) are out of scope for this document and are covered in `/models/README.md`.

---

## 8. Metadata, Lineage, and Audit Substrate

### 8.1 Every Data Point Traces to Source

For any canonical record, the platform must be able to answer:

- Where did this come from? (source system, source table, source record ID)
- When was it ingested?
- What batch was it part of?
- What validation checks did it pass or fail?
- What enrichments were applied, in what order, by what versions of what models?
- Was it manually overridden? By whom, when, why?
- Has it been superseded by a later restatement? What restatement?

This is the audit trail. Without it, no BoG examiner accepts AequorOS. With it, AequorOS *is* the audit trail.

### 8.2 The Lineage Table

A dedicated `lineage` table stores a graph of transformations:

```
lineage_id | operation      | input_lineage_ids | operation_type | operation_ref     | timestamp
-----------|----------------|-------------------|----------------|-------------------|----------
lin-001    | extract        | []                | ADAPTER_EXTRACT| t24_v1.0/AA.ARR   | ...
lin-002    | translate      | [lin-001]         | ADAPTER_XLATE  | t24_v1.0/mapping  | ...
lin-003    | validate       | [lin-002]         | VALIDATION     | rules_v3          | ...
lin-004    | enrich_nmd_dur | [lin-003]         | ML_ENRICHMENT  | nmd_v2.3          | ...
lin-005    | manual_override| [lin-004]         | HUMAN_OVERRIDE | user u-123 ticket | ...
```

Every canonical record's `lineage_id` points into this graph. From any output figure (say, a CAR of 12.4%), an examiner can walk backward through lineage to the source T24 record and everything that happened to it.

### 8.3 Snapshots and Point-in-Time Reproducibility

Every canonical record belongs to an `as_of_date`. Snapshots are immutable. If yesterday's LCR was 105% and today we discover an error in yesterday's data, we do not modify yesterday's record. We create a new snapshot for the same `as_of_date` with a `restatement_reason`, and mark the old one as superseded. The original submission remains reproducible.

This means every regulatory report AequorOS produces can be regenerated years later exactly as filed, even if the underlying data has since been restated.

### 8.4 Audit Log

Separate from the lineage graph, a linear audit log records every user action:

- Login and logout
- Report generation
- Configuration changes (mapping, validation rules, assumptions)
- Manual overrides
- Data acceptance decisions (accepting a batch with warnings, forcing through an error, etc.)
- Model retraining events
- Any change to a canonical record

The audit log is append-only, tamper-evident (hash-chained records), and retained per regulatory requirement (7+ years).

---

## 9. Temenos T24 Adapter (Framework Only)

### 9.1 Status: Deferred Until Portal Access

Full T24 adapter implementation requires access to current Temenos API references. That access is pending Temenos Solution Provider approval (see AequorOS partnership track).

**Do not invent T24 API endpoints, table structures beyond what is documented in AequorOS product docs, or authentication mechanisms.** When this section is expanded post-approval, the implementation will use the actual Temenos developer portal specifications.

### 9.2 What Is Known

From AequorOS's own product documentation and publicly available information about T24:

- T24 uses a proprietary database (JBASE or Oracle depending on the deployment)
- Two primary integration mechanisms are described in AequorOS product docs:
  - **Real-time API via TAFJ web services** (REST/SOAP), preferred
  - **Batch file export** via T24 COB (Close of Business) process, sent to SFTP, as fallback
- Known table names referenced in AequorOS product specs include `AA.ARRANGEMENT`, `AA.ARRANGEMENT.ACTIVITY`, `AA.PRODUCT.DESIGNER`, `AA.INTEREST`, `AA.PRODUCT`, `ACCOUNT`, `ACCOUNT.RESTRICTION`, `SECURITY.POSITION`, `SECURITY.MASTER`, `MM.MONEY.MARKET`, `LIMIT.REFERENCE`, `LETTER.OF.CREDIT`, `PAYMENT.STOP`, `TELLER`, `SWAP.AGREEMENT`, `FOREX`, `OPTIONS`, `CUSTOMER`, `COLLATERAL`, `DEPT.ACCT.OFFICER`, `GENERAL.LEDGER`
- OFS (Open Financial Service) is T24's messaging format for external interactions

### 9.3 Adapter Skeleton (To Be Filled)

The T24 adapter should be scaffolded now with the correct interface implementation and the known table names, but with the API-specific extraction logic left as TODO markers to be filled once Temenos developer portal access is available.

```
/adapters/temenos_t24/
  __init__.py
  adapter.py           # Implements SourceAdapter interface
  connection.py        # TAFJ web service client (TODO: real endpoints)
  extractors/
    arrangements.py    # AA.ARRANGEMENT extraction
    accounts.py        # ACCOUNT extraction
    securities.py      # SECURITY.POSITION extraction
    derivatives.py     # SWAP, FOREX, OPTIONS extraction
    # ... one per major entity area
  translators/
    arrangement_to_position.py
    account_to_position.py
    # ... one per source-to-canonical translation
  ofs/
    parser.py          # OFS message parsing (fallback path)
    formatter.py
  batch/
    sftp_client.py     # Batch fallback path
    file_parser.py
  mappings/
    default_field_mappings.yaml   # Baseline mappings; per-bank overrides in ConfigDB
  tests/
    fixtures/          # Synthetic T24 records for testing
    ...
```

Every TODO in this adapter carries a comment specifying what portal document is needed to complete it. This makes it trivial for the Temenos-approved engineer (or Fable 5 with portal docs in context) to complete the implementation without re-discovering the requirements.

### 9.4 Integration Modes

The adapter supports both integration modes with the same canonical output:

**Mode A: Real-time API (Preferred).**
- Hourly incremental sync for positions
- Real-time push for material transactions
- Exponential backoff retry
- Dead-letter queue for failed messages

**Mode B: Batch File (Fallback).**
- Daily post-COB SFTP file drop
- Files land in an S3 bucket with KMS encryption
- MD5 hash validation
- Schema validation before parsing
- Full-refresh mode

The choice between A and B is per-bank configuration. Some banks won't (or can't) expose T24 APIs to a third party and will only support batch drops. Both must be supported.

### 9.5 Temenos Exchange Consideration

If AequorOS achieves Solution Provider status and lists on Temenos Exchange, the integration path may be materially simpler, since Exchange-listed integrations may have a standard integration mechanism. Reassess this section post-approval.

---

## 10. Excel and CSV Adapter (MVP-Critical Path)

### 10.1 Why This Is the First Adapter Built

Every mid-tier African bank can produce Excel or CSV, regardless of core banking system. Bank IT teams often prefer sending file drops rather than opening API access to third parties, especially in early relationship stages. Excel/CSV is also how banks manage the material data that doesn't live in the core: behavioral assumptions, product mappings, off-balance-sheet items, restatements.

An Excel-competent adapter lets AequorOS onboard any bank, of any core banking system, at a "minimum viable" configuration in the first months of engagement. The T24 adapter deepens the T24 experience later but does not gate initial deployment.

**Priority: build this first, build it robustly, treat it as production code, not a workaround.**

### 10.2 What Robustness Means for Excel

Excel from banks arrives in every possible imperfect state:

- Merged cells in headers
- Blank rows between data blocks
- Multiple tables in one sheet
- Data spread across many sheets in one workbook
- Column headers that vary between reporting cycles
- Data types coerced by Excel (leading zeros dropped, dates converted to numbers)
- Formulas instead of values
- Hidden rows and columns
- Text in numeric columns ("N/A", "-", "TBC")
- Currency symbols and thousand separators inside numeric cells
- Multiple currency columns per row without a clear currency field
- Passwords on workbooks

The Excel adapter must handle all of the above without crashing and, where possible, without human intervention.

### 10.3 Architecture

```
/adapters/excel_csv/
  __init__.py
  adapter.py           # Implements SourceAdapter interface
  workbook_reader.py   # Handles .xlsx, .xls, .csv, .tsv
  sheet_analyzer.py    # Detects tables within sheets, header rows, data blocks
  type_coercion.py     # Robust type parsing for money, dates, percentages, enums
  mapping_engine.py    # Applies bank-specific column-to-canonical mappings
  suggestion_engine.py # ML-assisted mapping suggestions for new files (see section 12)
  tests/
    fixtures/
      well_formed.xlsx
      merged_headers.xlsx
      multiple_tables_per_sheet.xlsx
      dirty_currency_cells.xlsx
      # ... many patterns
```

### 10.4 The Mapping Workflow

For each new bank, onboarding produces a Mapping Configuration for its Excel formats. This is the deliverable of onboarding for banks that stay on Excel long-term.

The workflow:

1. Bank sends a sample file.
2. AequorOS operator uploads it in the onboarding UI.
3. The mapping suggestion engine (section 12) proposes column-to-canonical mappings based on headers, content patterns, and mappings from similar banks.
4. Operator reviews and confirms or corrects.
5. The confirmed mapping is saved as the bank's `MappingConfig`.
6. Future files from this bank are processed with this mapping automatically.

The first mapping is time-intensive. Subsequent mappings for the same bank take minutes.

### 10.5 Validation Specific to Excel

Excel-source data gets extra scrutiny:

- Currency detection: if a numeric field lacks an explicit currency, either derive it from context (sheet name, header) or reject.
- Rate normalization: `24.5`, `24.5%`, `0.245` must all resolve correctly and consistently.
- Date parsing: Excel serial dates, ISO strings, and locale-specific formats all need robust handling.
- Formula resolution: values, not formulas, must be ingested. If a formula depends on other cells, resolve fully.

---

## 11. Additional Adapters

### 11.1 Finacle Adapter

Second-priority after T24. Finacle uses an Oracle backend and exposes data through Infosys FSDF (Finacle Service Delivery Framework) or database-direct extraction.

Scaffolding follows the same pattern as T24. Detailed implementation deferred until first Finacle bank engagement, or a Finacle documentation partnership becomes available.

### 11.2 FlexCube Adapter

Oracle's core banking product. Similar Oracle-backed database structure. Same scaffolding pattern.

### 11.3 Database-Direct Adapter

A generic adapter for banks that expose a read-only replicated view of their core banking database directly to AequorOS. Supports PostgreSQL, Oracle, SQL Server, and MySQL/MariaDB. Configuration specifies which tables map to which canonical entities.

This is a common integration pattern for smaller banks and rural banks whose core systems are less API-mature.

### 11.4 SFTP File-Drop Adapter

A generic adapter for banks that produce daily flat-file extracts (CSV, fixed-width, or bank-defined formats) and drop them to SFTP. This overlaps with the Excel adapter but is optimized for machine-generated files rather than human-authored spreadsheets.

### 11.5 API-Generic Adapter

For banks with custom APIs or non-mainstream core systems. Configuration-driven: define endpoints, authentication, pagination, and field mappings in YAML; the adapter reads the config and executes.

### 11.6 Manual-Entry "Adapter"

For data that only exists in someone's head: behavioral policy values, off-balance-sheet items not tracked in core, manual GL adjustments. The UI supports direct entry into canonical, with the same lineage and audit discipline as any other adapter.

---

## 12. Intelligence Layer

### 12.1 Where Intelligence Lives (and Doesn't)

Intelligence in the Data Engine lives at three points:

1. **Schema mapping assistance** — accelerates onboarding.
2. **Anomaly detection** — catches data quality issues rules miss.
3. **Reconciliation assistance** — helps operators find and explain breaks.

Intelligence explicitly does *not* live in:

- The regulatory calculation engines (they are deterministic)
- Data acceptance decisions (a human must approve batches with blockers)
- Manual override decisions (a human must justify)

### 12.2 Schema Mapping Assistance

When a bank submits a new file (Excel or CSV) with unfamiliar column headers, an ML-assisted mapper suggests canonical field mappings. Inputs to the suggestion:

- Column headers (exact and fuzzy matches to a header dictionary)
- Content patterns (a column with values like `12/05/2026` is likely a date; a column with GHS 5-digit values is likely a monetary amount)
- Prior mappings from banks with similar profiles
- The canonical model itself (target field types, cardinality, expected value ranges)

Output: for each source column, a ranked list of candidate canonical fields with confidence scores.

Human operator reviews and confirms. The confirmed mapping goes into the bank's `MappingConfig`. Over time, as the platform accumulates confirmed mappings across many banks, suggestions get more accurate.

**Multi-tenant learning is essential here.** Each new bank onboarded improves onboarding for future banks, because the mapping suggestion engine has more examples. But no bank's raw data is ever exposed to another; only the *mappings themselves* (column names to canonical fields) contribute to the shared model, and even those are aggregated statistically, not attributed.

### 12.3 Anomaly Detection

Rule-based validation catches known-bad patterns. Anomaly detection catches unknown-bad patterns.

Statistical process control on:

- Distribution of position sizes per product
- Distribution of interest rates per product category
- Deposit balance stability
- Transaction volumes by hour, day, day-of-month
- GL account movements

When a distribution shifts materially, an anomaly is flagged. Operators triage. Sometimes the anomaly reflects a real event (a big loan booking, a large deposit inflow). Sometimes it reflects a data quality problem the rules didn't catch.

Anomaly detection is per-institution, trained on that institution's own history. It does not benefit from multi-tenant training in the same way schema mapping does.

### 12.4 Reconciliation Assistance

When GL and sub-ledger don't tie, finding the cause manually is tedious. An ML-assisted diagnostic examines the difference and suggests likely locations:

- Which GL accounts are the largest contributors to the difference
- Whether the break is concentrated in a specific date range
- Whether the break correlates with specific transaction types
- Whether it looks like a known pattern (unposted accruals, FX revaluation timing, cutoff errors)

The tool doesn't resolve the break. It surfaces the most productive place for a human to look.

### 12.5 Guardrails on Intelligence

- Every ML output surfaces with confidence
- Every ML output can be overridden by a human
- No ML output silently modifies canonical data; it always goes through the same acceptance workflow as any other change
- Model versions are preserved; old outputs remain reproducible
- SR 11-7-style model governance applies (documentation, validation, monitoring)

---

## 13. African-Specific Data Sources

Beyond core banking, the Data Engine accommodates data sources characteristic of African banking realities. These are not add-ons; they're recognized in the canonical model and adapter framework from day one, even if specific integrations are built later.

### 13.1 Mobile Money Integration Data

Ghanaian banks, especially rural banks and mid-tier universal banks, have significant customer flows through MTN Mobile Money, Airtel Money, and Vodafone Cash. Some of this activity appears in the core banking system; some doesn't. Mobile-money-originated deposits behave differently than traditional deposits for LCR run-off assumptions.

The canonical model's `counterparty_type` and `product_category` accommodate mobile-money-linked accounts. Future adapters can pull directly from mobile money operator reporting where banks have such feeds.

### 13.2 Bank of Ghana Data Feeds

External market data pulled directly by AequorOS, not expected from the bank:

- BoG policy rate announcements
- BoG interbank market rates
- T-bill and government bond auction results
- GHS reference rates
- BoG prudential ratios (for peer comparison)

These flow into the `market_index` and `yield_curve` canonical entities.

### 13.3 GhIPSS and GhanaCard

The Ghana Interbank Payment and Settlement Systems (GhIPSS) processes interbank clearing. Some banks integrate this into their core; some maintain it as a separate feed. The GhanaCard national ID system is increasingly used for customer identity and KYC.

The canonical model's `counterparty` and `transaction` entities support these identifiers as extension fields.

### 13.4 Cocoa Board and Government Flows

Banks with significant agricultural customer bases see large seasonal flows tied to cocoa purchases (Cocobod payments to farmers), government contractor payments, and other public-sector cycles. These are highly predictable if modeled and highly disruptive if not.

The behavioral overlay layer supports seasonality-aware models for such cash flows.

### 13.5 Cross-Border Remittances

Banks with diaspora customer bases see significant remittance inflows through Western Union, MoneyGram, WorldRemit, and increasingly digital-first services. These affect FX exposure and liquidity dynamics.

### 13.6 SWIFT and Correspondent Banking Data

For banks with international operations, SWIFT message feeds provide interbank position and payment data. The canonical model accommodates SWIFT identifiers (BIC codes) on counterparties.

---

## 14. Security, Compliance, and Operations

### 14.1 Multi-Tenancy

- Every canonical record is stamped with `institution_id`.
- Every database query is scoped to `institution_id`.
- Row-level security enforced at the database level, not just the application.
- Cross-tenant queries impossible except through explicit, audited, aggregated-only administrative interfaces.

### 14.2 Encryption

- At rest: AES-256 via AWS KMS or GCP KMS with customer-managed keys.
- In transit: TLS 1.3 for all connections.
- Sensitive canonical fields (customer PII where present): field-level encryption in addition to database encryption.
- PII masking in non-production environments; synthetic data only for development and testing.

### 14.3 Access Control

- SSO integration with bank Active Directory / LDAP.
- MFA required for all users.
- Role-based access control with defined roles: CRO, Treasurer, ALCO member, risk analyst, IT admin, external auditor (read-only).
- Segregation of duties: maker/checker workflow for sensitive operations (mapping changes, validation rule changes, manual overrides above threshold).

### 14.4 Data Residency

- AWS Cape Town region for African customers requiring in-country residency.
- Configurable per-institution.
- Analytical/aggregated data may reside elsewhere if permitted; transactional data respects residency requirements.

### 14.5 Compliance Certifications

- SOC 2 Type II: target Year 2.
- ISO 27001: follow SOC 2.
- Ghana Data Protection Act compliance from day one.
- Banking secrecy compliance: no cross-institution data visibility, no data reuse without consent.

### 14.6 Business Continuity

- RPO ≤ 1 hour (backup frequency).
- RTO ≤ 4 hours (DR failover).
- Uptime SLA: 99.9%.
- Multi-AZ deployment with automatic failover.
- Quarterly DR drills with documented results.

---

## 15. Phasing and Milestones

### 15.1 Phase 1: MVP (Months 1-9)

**Goal:** Ingest from Excel/CSV, produce correct Basel III outputs for the first pilot banks.

- Canonical model implemented (all core entities from section 4)
- Excel/CSV adapter production-ready
- Validation framework operational
- Enrichment layer for the modules shipped in MVP (Basel Capital, Liquidity, Balance Sheet Forecasting)
- Manual-entry UI for behavioral overlays and manual GL adjustments
- Mapping configuration UI
- Lineage and audit substrate present from first commit

### 15.2 Phase 2: T24 Adapter (Months 9-15)

**Goal:** Native T24 integration operational at first T24 pilot bank.

- Post Temenos Solution Provider approval
- T24 adapter fully implemented per portal specifications
- Real-time API mode and batch mode both supported
- Existing pilot banks migrated from Excel to native T24 where they run T24
- Temenos Exchange listing pursued

### 15.3 Phase 3: Adapter Portfolio Expansion (Months 15-24)

**Goal:** Cover the majority of mid-tier African bank core systems.

- Finacle adapter
- FlexCube adapter
- Database-direct adapter for banks on less mainstream systems
- SFTP file-drop adapter mature
- Schema mapping assistance ML-enabled

### 15.4 Phase 4: Intelligence Layer Maturity (Months 24+)

**Goal:** Onboarding time down from 4-8 weeks to 2-4 weeks; anomaly detection production-ready.

- Multi-tenant mapping suggestion engine trained on cumulative onboarding data
- Anomaly detection running in production
- Reconciliation assistance surfacing breaks proactively
- Behavioral models re-trained on accumulated real bank data

### 15.5 Phase 5: Non-Bank Institution Categories (Year 3+)

**Goal:** Canonical model proves out for pension funds and insurance companies.

- Institution type extensions for pensions and insurance
- Adapters for pension administration systems
- Adapters for insurance policy administration systems
- Behavioral overlays for actuarial liabilities

---

## 16. Non-Goals and Explicit Deferrals

Things this document deliberately does not specify:

- **Specific ML model architectures** for behavioral overlays (see `/models/README.md`)
- **Regulatory calculation engine internals** (see `/calculations/README.md`)
- **UI/UX design** (see `/product/ux_spec.md`)
- **Deployment infrastructure specifics** beyond region and residency (see `/infrastructure/README.md`)
- **Specific T24 API endpoints and payloads** (deferred until Temenos developer portal access)
- **Pension and insurance adapter implementations** (deferred to Phase 5)

Things this document is explicit about *not* wanting:

- **A single monolithic ingestion service.** Adapters are separately deployable, testable, and versionable.
- **A shared code path between adapters.** The temptation to "consolidate" adapter code is dangerous. Each adapter's messiness stays contained.
- **Silent data quality tolerance.** Every quality issue surfaces. Fail loudly.
- **Bank-specific customization inside AequorOS core.** All bank-specific behavior lives in configuration, not code.

---

## 17. Implementation Guidance for AI-Assisted Coding

If this document is handed to Claude Fable 5 or another AI coding assistant to implement, the following instructions apply:

1. **Build in the order of sections.** Canonical model first (section 4). Adapter framework interface second (section 5.1). Validation framework third (section 6). Then Excel adapter (section 10). Then T24 skeleton (section 9). Then the rest.

2. **Do not invent T24 API details.** Section 9.3 defines a skeleton with TODO markers. Leave them as TODO with descriptive comments. Do not fabricate Temenos endpoints.

3. **Respect the metadata columns.** Every table gets the mandatory metadata columns from section 4.3. Do not shortcut this for "cleanliness."

4. **Do not merge adapters "for reuse."** Each adapter is separately deployable. The interface enforces the contract; the implementations are independent.

5. **Test coverage is non-negotiable for adapters and validation.** Every adapter ships with fixture-based tests (section 5.5). Every validation rule ships with positive and negative test cases.

6. **When in doubt, err on the side of explicitness.** Configuration over convention, explicit types over inferred, named fields over positional, documented enums over magic strings.

7. **When a design decision is unclear, surface it.** Do not silently choose a direction. Document the decision point and either propose an option with reasoning, or defer until Eric or Dela can decide.

8. **Do not implement intelligence layer features (section 12) in MVP.** The framework accommodates them, but MVP ships without ML-based mapping suggestion or anomaly detection. Those are Phase 4.

9. **Preserve the strategic framing.** This is not a data pipeline. It is the platform's central moat. Trade-offs favor correctness, auditability, and long-term flexibility over short-term convenience.

---

## Appendix A: Terminology

- **Adapter.** Code that translates a specific source system's data into the canonical model.
- **Canonical model.** AequorOS's source-agnostic representation of a regulated financial institution's balance sheet, transactions, and behavioral overlays.
- **Enrichment.** The layer that adds behavioral maturities, mappings, MTM, and other overlays to raw canonical data.
- **Institution.** A tenant of AequorOS. In Phase 1, a bank. Later, a pension fund, insurer, etc.
- **Lineage.** The graph of transformations from source to any canonical or output value.
- **Mapping configuration.** Per-institution configuration that specifies how a source's fields translate to canonical fields.
- **Snapshot.** The canonical state of an institution as of a specific business date. Immutable.
- **Validation.** The framework that runs canonical data through rules to identify quality issues before calculations run.

## Appendix B: Related Documents

- `/schema/canonical_v1/` — DDL for the canonical model
- `/adapters/*/README.md` — per-adapter implementation docs
- `/validation/rules_library.md` — full library of validation rules
- `/models/README.md` — ML model inventory and governance
- `/calculations/README.md` — regulatory calculation engine documentation
- `/infrastructure/README.md` — deployment and operations
- `/product/onboarding_playbook.md` — the onboarding process this architecture enables

---

**End of Data Engine Specification v1.0**

*Revisions expected as Temenos developer portal access is confirmed, as first pilot bank engagements reveal onboarding realities, and as the canonical model is stress-tested against non-bank institution categories.*
