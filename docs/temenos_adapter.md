# AequorOS Temenos T24 Core-Banking Adapter

## Implementation Specification

The Temenos T24 / Transact adapter connects a bank's live core banking system to
AequorOS's canonical model, so positions, balances, deals, cash flows,
counterparties, products and the general ledger flow automatically into all six
calculation modules (IRRBB, Liquidity, FX, Basel, FTP, Balance-Sheet
Forecasting) with no manual uploads. Most African and emerging-market banks run
Temenos, so this adapter is foundational for onboarding pilots quickly.

This document describes what was built, why, and where the single deliberate
completion seam is. It mirrors the structure and quality bar of
[`market_data_adapter.md`](market_data_adapter.md).

---

## 1. Context and Scope

### 1.1 T24 is an ingestion `SourceAdapter`, not a live-query engine

T24 data enters through the Data Engine's ingestion spine exactly like an Excel
upload or an API push. `TemenosT24Adapter` implements the same
`SourceAdapter` contract (`identify` / `validate_connection` / `discover_schema`
/ `extract` / `translate` / `health_check`) and persists through
`services/ingestion.py::start_ingestion`, which already provides batching,
lineage, supersession, validation-gating, content-hash idempotency, and the
automatic `pipeline_refresh` recompute. We add the *transport, catalog,
extraction, and connection-lifecycle* layers on top; we do not reimplement the
spine.

### 1.2 The stage-then-ingest pattern (load-bearing)

The ingestion spine is file-based: `start_ingestion` materializes
`config.location` (including `temp://{object_path}` staged objects) and reads the
bytes into the batch's raw artifact. So the network fetch does **not** live
inside `SourceAdapter.extract`. Instead:

```
temenos_pull job / on-demand trigger
  → pull.py::stage_extract
      sign on (auth seam) → per enabled domain: catalog → request → transport.fetch
      bundle the raw payloads (mode + per-domain blobs) → StorageClient.write(temp tier) → temp://…
  → ingestion.start_ingestion(source_system="T24", location="temp://…", mapping_config_id, as_of, reason)
      TemenosT24Adapter.extract    → parse the staged bundle OFFLINE → native RawRecords
      TemenosT24Adapter.translate  → generic MappingConfig (rename + enum + attributes)
      _persist_raw_artifact + supersession + pipeline_refresh
```

The **transport** is fetch-only and swappable; the **extractors** own every T24
semantic and run offline against the staged bundle; **translate** stays a thin,
generic, `api_push`-style layer that is fully reproducible from the recorded
mapping version. The live connection is confined to one swappable component.

### 1.3 Non-goals

- No write-back to T24. The adapter is read-only.
- No FX conversion in translate (it has no market data). The adapter reads
  T24's own **local-currency-equivalent (LCY)** fields for `balance_ghs` etc.;
  where the T24 LCY currency ≠ the reporting currency this is a documented risk
  (§13).
- Curves, FX rates, capital structure, and behavioral assumptions are **not**
  T24 domains — they arrive via the market-data / manual adapters and are read
  from `CanonicalReferenceRow` independently (§9).

---

## 2. Non-Negotiable Design Principles

1. **Never invent T24 endpoints, table structures, or auth mechanisms** beyond
   documented, standard T24 vocabulary (`KNOWN_TABLES` in `adapter.py`).
   Installation-specific details — enquiry names, IRIS/Open-API resource paths,
   the exact field JSON schema — live in the mode catalog as *overridable
   defaults*, and the single live-network submission is a portal-gated seam.
2. **Never fake support.** A domain is offered only when its catalog entry sets
   `supported: true`; a typo'd catalog key or domain name fails loudly at load,
   never silently drops coverage.
3. **T24 semantics live in extractors, not translate.** OFS marker parsing, LCY
   selection, field renaming, and position typing are the extractor's job;
   translate is a generic mapping layer with no T24 knowledge.
4. **Raw core text never reaches a bank.** OFS response text, REST bodies, T24
   error codes, and endpoints travel only in `TemenosError.internal_detail`
   (engineering logs); `str(error)` renders only the pre-authored bank-facing
   message. Enforced by a leak-canary contract test.

---

## 3. Architectural Overview

### 3.1 Directory structure — `backend/app/adapters/temenos_t24/`

```
adapter.py            # SourceAdapter: validate/discover/extract(offline bundle parse)/translate/health
domains.py            # CoreBankingDomain taxonomy + DomainCategory + category_of + DOMAIN_TO_ENTITY_TYPE + cadence
catalog.py            # load_mode_catalog + fail-loud validation + per-bank apply_overrides
catalogs/{ofs,iris,open_api}_catalog.yaml   # domain → app/enquiry/endpoint + field_map + attribute_keys + lcy_fields
transport.py          # T24Transport Protocol, DomainRequest, RawDomainResponse, UnavailableTransport, FixtureTransport
transports/{ofs,iris,open_api}.py           # live transports (request built; network submission portal-gated)
auth.py               # TemenosSession, SessionProvider Protocol, SimulatedSessionProvider, credential-shape checks
errors.py             # TemenosErrorCode + MESSAGE_TEMPLATES + render_bank_facing + TemenosError(bank_facing, internal_detail)
ofs.py                # OFS codec: build_ofs_message / build_enquiry_message / parse_ofs_response (@FM/@VM/@SM flatten)
extractors.py         # catalog-driven bundle → RawRecords (rename + LCY pick + constants; enums left raw)
translate.py          # generic MappingConfig translation (mirrors api_push; coercion + enum + attributes)
mappings/default.py   # default_t24_mapping_config(mode) built FROM the catalog (identity + attribute_columns + enum_mappings)
pull.py               # stage_extract / pull_and_ingest (sign-on → fetch → bundle → temp:// → start_ingestion)
credential_vault.py   # TemenosCredentialVault (AES-256-GCM over the connection row; reuses market-data crypto)
```

Operational + delivery layers outside the adapter package:

```
models/temenos.py                        # TemenosConnection (one per bank; RLS-scoped)
alembic/versions/…_temenos_connections.py# table + composite FK + CHECKs + uniques + ENABLE/FORCE RLS + policy
schemas/temenos_connections.py           # write-only credentials; ciphertext never in a response
services/temenos_connections.py          # create/validate/test/update-rotation/disable/enable/revoke + list_domains + trigger_pull/backfill
services/temenos_jobs.py                 # run_temenos_pull handler + enqueue_due_temenos_pulls + backfill + retry policy
features/manage_temenos_connections.py   # HTTP: /banks/{id}/temenos/connections… (registered in api/router.py)
```

### 3.2 How a pull happens end-to-end

1. A scheduled tick (`TEMENOS_PULL_ENABLED`) or an on-demand `POST …/pull`
   enqueues a coalesced `temenos_pull` job per `(connection, as_of)`.
2. `run_temenos_pull` decrypts credentials for one sign-on cycle, selects the
   live transport for the connection mode, and calls `pull_and_ingest`.
3. `pull_and_ingest` signs on, fetches each enabled domain, bundles the raw
   payloads, stages the bundle to the bank's temp tier, and hands the
   `temp://` location to `start_ingestion`.
4. `extract` parses the staged bundle offline into canonical-keyed
   `RawRecord`s; `translate` validates them into canonical record data; the
   spine persists with supersession and triggers the pipeline recompute.

---

## 4. The `SourceAdapter` Interface and Contract Tests

`TemenosT24Adapter` satisfies the standard `SourceAdapter` ABC. Two test
surfaces hold it to the bar:

- **`AdapterContractSuite`** (`tests/adapters/contract.py`) — the generic
  conformance suite every adapter passes (deterministic extract, locatable
  records, translate never raises for bad data, empty mapping fails records not
  the batch). Run per mode against fixture bundles.
- **`TemenosContractSuite`** (`tests/adapters/temenos_t24/contract.py`) — the
  T24 end-to-end journey over the real ingestion spine and an in-memory storage
  client: a fixture pull produces an accepted batch, canonical rows carry
  mandatory metadata (`source_system == "T24"`), `balance_ghs`/`notional_ghs`
  reach the position snapshots the engines read, derivative and off-balance
  positions carry the exact attribute keys `fact_derivation` consumes,
  re-staging an identical pull supersedes rather than duplicates, and the
  leak canary never appears on a bank-facing surface.

All three connection modes (OFS, IRIS, Open API) subclass these suites and pass.

---

## 5. Core-Banking Domain Taxonomy

`CoreBankingDomain` is a flat, vendor-agnostic vocabulary of the business data
domains AequorOS needs. Each domain maps to exactly one canonical entity type
(or `reference`) via `DOMAIN_TO_ENTITY_TYPE`, and to a category
(`category_of`, longest-prefix) that carries a default pull cadence.

| Domain | Entity | Position type | Lights up |
|---|---|---|---|
| `GL_BALANCES` | gl_account | — | balance_sheet |
| `POSITIONS_LOANS` | position | LOAN | loan_exposure, IRRBB, LCR |
| `POSITIONS_DEPOSITS` | position | DEPOSIT | deposits, NSFR/LCR |
| `POSITIONS_CURRENT_ACCOUNTS` | position | DEPOSIT | CASA, LCR |
| `POSITIONS_MM_PLACEMENTS` | position | INTERBANK_PLACEMENT | liquidity |
| `POSITIONS_MM_BORROWINGS` | position | INTERBANK_BORROWING | liquidity |
| `POSITIONS_FX_DEALS` | position | FX_HEDGE | fx_hedge |
| `POSITIONS_SWAPS` | position | INTEREST_RATE_SWAP | irr_swap |
| `SECURITIES_HOLDINGS` | position | SECURITY_HOLDING | HQLA, IRRBB |
| `OFF_BALANCE_LC` / `_GUARANTEES` / `_COMMITMENTS` | position | LC_GUARANTEE / COMMITMENT_UNDRAWN | off_balance (CCF) |
| `COUNTERPARTY_MASTER` | counterparty | — | counterparties |
| `PRODUCT_MASTER` | product | — | products, regulatory categories |
| `BUSINESS_UNITS` / `INSTITUTION` | reference | — | business_units / institution |
| `LIMITS` / `CASHFLOWS_SCHEDULED` / `HISTORICAL_BALANCES` | reference | — | staged (no MVP consumer yet) |

Coverage is defined by the catalogs at runtime, not by this table. The 16
entity + reference domains above ship `supported: true`; the last three are
staged (`supported: false`) until their reference-dataset consumers land.

---

## 6. Connection Modes

Three transport channels reach the same canonical model. The canonical output
is identical across modes; only the source field vocabulary and the network
protocol differ.

| Mode | Channel | Auth | Source vocabulary |
|---|---|---|---|
| `OFS` | Open Financial Service (TAFJ) | OFS sign-on / service user | dotted UPPER (`AA.ARRANGEMENT`, `LCY.AMOUNT`) |
| `IRIS` | Interaction Framework REST | OAuth2 bearer | camelCase JSON (`arrangementId`, `lcyAmount`) |
| `OPEN_API` | Transact Open APIs / Data Hub | client-credentials bearer | camelCase JSON |

Each mode has a catalog under `catalogs/`. The OFS catalog is authored by hand;
the IRIS and Open-API catalogs share the OFS canonical structure with REST
source names and published resource endpoints. Enquiry names / endpoints /
field names are **documented defaults, overridable per bank** via connection
`catalog_overrides` — they are installation-specific.

### 6.1 The OFS codec

`ofs.py` speaks the OFS wire format and nothing application-specific:
`build_enquiry_message` frames an `ENQUIRY.SELECT` request; `parse_ofs_response`
decodes the envelope and flattens multivalued / subvalued fields delimited by
the T24 Field / Value / Sub markers (ASCII 254 / 253 / 252, also written
`@FM`/`@VM`/`@SM`) into plain scalars / lists / lists-of-lists. Marker bytes
never survive into extractor-facing structures.

### 6.2 Live transports (portal-gated seam)

`transports/{ofs,iris,open_api}.py` build a faithful request (OFS enquiry via
the codec; REST URL + pagination params) and confine the actual network
submission to a single `_submit`/`_get` hook. Per §2.1 we do not fabricate the
exact TAFJ endpoint, IRIS provider path, or client-credentials token flow, so
those hooks classify as `CORE_UNAVAILABLE` until a Temenos-approved engineer
completes them with developer-portal access. The framework defaults to
`UnavailableTransport`; `FixtureTransport` replays recorded payloads and drives
the entire pipeline offline in tests. This mirrors how the market-data
Bloomberg adapter ships its live B-PIPE transport as a Phase-2 seam.

---

## 7. Extraction and the T24-vs-reference boundary

One catalog-driven engine (`extractors.py`) covers every domain: it renames
`field_map` T24 fields to canonical output keys, pulls `lcy_fields` into the
LCY-equivalent attribute keys (`balance_ghs`, `notional_ghs`, `mtm_ghs`),
injects `constants` (position_type, credit_conversion_factor, rating source),
and leaves enum-coded values RAW so the mapping resolves them. Reference domains
are preserved as whole stringified rows under their `dataset_kind`.

The extractor populates exactly the attribute keys `fact_derivation` reads:
`balance_ghs`, `branch_id`, `ecl_provision_ghs`, `notional_ghs`,
`credit_conversion_factor`, typed `interest_rate`/`rate_type`/
`contractual_maturity`/`next_repricing_date`/`ifrs9_stage`, product
`regulatory_category`, plus the FX-hedge terms (`buy_currency`,
`contract_rate`, `mtm_ghs`, `hedge_id`, `instrument`) and IRS terms
(`swap_id`, `direction`, `pay_rate_pct`, `receive_index`, `tenor_years`).

**Boundary.** T24 supplies the core book; `yield_curve`, `fx_rates`,
`capital_structure`, and `behavioral_assumptions` come from the market-data /
manual adapters and are read from `CanonicalReferenceRow` independently. The
T24 catalogs mark those domains out of scope.

---

## 8. Translation and the default mapping

`translate.py` mirrors `api_push`: it renames source keys via the versioned
`MappingConfig`, applies `enum_mappings`, coerces types (money/rate → Decimal;
ISO `YYYY-MM-DD` and packed `YYYYMMDD` → date; ifrs9_stage → int), copies
`attribute_columns` verbatim into the `attributes` payload, and derives a
product's regulatory category from `product_mappings`. It knows nothing about
OFS/IRIS/Open API — every T24 semantic was already resolved by the extractor,
so translation is reproducible from the recorded mapping.

`mappings/default.py::default_t24_mapping_config(mode)` builds the near-identity
mapping from the catalog: identity field maps over the canonical model fields,
`attribute_columns` unioned from the supported domains' `attribute_keys`, and
`enum_mappings` unioned from the catalog. `product_mappings` starts empty; a
bank fills it during onboarding. The mapping is seeded per bank on the first
connection create and auto-seeded by the pull job if absent, then stored
versioned so translation stays reproducible.

---

## 9. Credential Lifecycle

`TemenosConnection` holds one configured connection per bank. Credentials
(OFS service-user password, IRIS/Open-API client secret or API key) are never
stored in plaintext: `TemenosCredentialVault` encrypts them AES-256-GCM onto the
`credential_ciphertext` column, reusing the market-data credential crypto and
the same `CREDENTIAL_VAULT_MASTER_KEY` (zero market-data edits). Only the
SHA-256 fingerprint and expiry ever appear in a response or audit record.

`status` tracks the lifecycle: `TESTING → ACTIVE → EXPIRING_SOON → EXPIRED /
INVALID / REVOKED / DISABLED`. Create validates credential *shape* and signs on
structurally (no live core required); success activates, failure leaves the
connection `TESTING` with a bank-facing `validation_error`. Rotation validates
the new credentials first and swaps ciphertext atomically. Revocation wipes the
ciphertext but keeps the row for audit — it never deletes canonical data already
pulled with the credential. Every transition is audited.

### 9.1 HTTP surface (`/api/v1/banks/{bank_id}/temenos/…`)

`listTemenosConnections`, `createTemenosConnection`, `validateTemenosConnection`,
`testTemenosConnection`, `updateTemenosConnection`, `disableTemenosConnection`,
`enableTemenosConnection`, `revokeTemenosConnection`, `triggerTemenosPull`,
`triggerTemenosBackfill`, `listTemenosDomains`. Credentials are write-only.

---

## 10. Error Handling — what never reaches the bank

`TemenosError` carries a pre-authored `bank_facing` message and an
`internal_detail` that is logged for engineering and never surfaced. The
taxonomy (`TemenosErrorCode`) covers credential, session-limit, domain-not-
permitted, enquiry-not-found, core-unavailable, COB-in-progress, rate-limited,
response-malformed, no-data, network, and configuration faults. Every template
is placeholder-checked (a missing `{core_system}`/`{timestamp}`/`{domain}` fails
loudly) and free of OFS/wire vocabulary. A leak-canary test asserts a sentinel
placed in `internal_detail` never appears in `str(error)` or the bank-facing
message.

---

## 11. Storage, Lineage, Idempotency

Staged bundles land in the bank's temp tier via `StorageClient`; the spine
copies the raw bundle into the raw tier as the batch's artifact and records
`ADAPTER_EXTRACT → ADAPTER_TRANSLATE → VALIDATION` lineage. The batch
content-hash (SHA-256 over the bundle bytes) drives idempotency: re-staging an
identical pull with the same mapping reuses the accepted batch; changed content
supersedes prior canonical rows rather than duplicating.

---

## 12. Scheduling (EOD / COB / on-demand / backfill)

`enqueue_due_temenos_pulls` runs on the hourly `scheduled_tick` behind
`TEMENOS_PULL_ENABLED` (off by default). It applies the cheap expiry-based
credential health check, then enqueues one coalesced `temenos_pull` per
connection whose EOD/COB schedule slot has arrived (cadence derived per
domain-category from `schedule` overrides or defaults). On-demand pulls and
backfills (one job per as-of date, coalesced) are enqueued through the trigger
endpoints. `run_temenos_pull` applies the retry policy: transient core faults
(core unavailable, network, rate-limited, COB running, session limit) re-raise
for backoff; credential-class faults mark the connection and complete without
retry; everything else completes with a logged, non-retried failure.

---

## 13. African-Bank Risk Analysis

- **R18 vs Transact.** Field/enquiry names differ across the T24 R-series and
  Transact. The catalogs ship documented defaults; per-bank `catalog_overrides`
  cover the spread without a code change.
- **Multi-company.** A bank may run several T24 companies/entities; the
  connection carries a `companies` list. The MVP pulls the first company; a
  multi-company merge across companies is a documented Stage-2 hardening item.
- **LCY reliance.** The adapter reads T24's own LCY-equivalent fields for
  `*_ghs` amounts and does not FX-convert. Where the T24 LCY currency ≠ the
  reporting currency, magnitudes must be reconciled — a documented risk.
- **COB windows.** EOD pulls run post-COB via the hourly tick; a pull hitting a
  running COB classifies as `COB_IN_PROGRESS` and retries after the window.
- **Volume / pagination.** REST transports page until exhausted; OFS enquiries
  are scoped by company and selection criteria. Per-domain `page_size` is tuned
  in the catalog.

---

## 14. Phasing and Completion

**Shipped (fixture-tested, offline-complete):** all three connection modes with
catalogs, extractors, the default mapping, the credential-lifecycle connection
model (RLS-scoped, migration verified on Postgres), the pull/backfill jobs and
scheduler wiring, and the full contract + end-to-end + API + vault + jobs test
suites.

**Portal-gated completion (one seam per mode):** the live network submission in
`transports/{ofs,iris,open_api}.py::_submit`/`_get`. Until completed, an enabled
scheduled pull fails-retries with an actionable `CORE_UNAVAILABLE`; a
Temenos-approved engineer wires the documented endpoint + auth flow and the
whole pipeline behind it is already tested.

**Non-goals for MVP:** write-back, multi-company merge, `LIMITS` /
scheduled-cashflow reference consumers, and a 7-day rotation grace window.
