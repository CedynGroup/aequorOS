# Storage Persistence & Retention — Current-State Review

**Status:** Review v1.0 (current-state, code-grounded)
**Date:** 2026-07-18
**Prepared for:** Dela Anthonio (CTO), Eric Inkoom Danso (CEO)
**Prepared by:** Engineering (Claude Code), under human review
**Scope of evidence:** `eric` branch backend + `docs/storage.md`
**Companion documents:** `storage.md` (the spec this reviews against), `data_engine.md`, `ARCHITECTURE.md`

---

## 1. Purpose

This memo answers a governance question raised during the Data Engine build: **when a bank's data is ingested (e.g. a live core-database sync), what gets saved to our centralized object storage versus the database, and what retention/governance policy actually applies?**

It is a *current-state* review: it measures what the code does today against the policy in `storage.md`, and is explicit about where the two diverge. Every claim is anchored to a code path so it is verifiable and defensible in a vendor risk assessment.

Runtime configuration confirmed at time of review: **backing store `minio`, environment `mvp`, `STORAGE_RETIRE_AFTER = 2027-01-14`, storage configured and live.**

---

## 2. Executive summary

- **Ingested data *is* saved to centralized object storage**, per institution, per tier — not "just the DB." A sync writes the raw source snapshot, the staged pull bundle, and the validation report to object storage; the *queryable canonical model* is persisted to Postgres.
- **The backing store is MinIO (S3-compatible), sanctioned for synthetic MVP data only.** A hard startup gate (`STORAGE_RETIRE_AFTER = 2027-01-14`) forces migration to managed GCS/S3 before any real bank data or the first paying customer. This is policy working as designed, not a gap.
- **A retention policy exists** (`storage.md §6`) and is *partially* enforced in code: `temp` auto-deletes at 30 days, retained tiers are versioned and logical-delete-only. The longer-horizon controls (7-year archival tiering, legal hold, per-institution keys/IAM isolation) are written as policy but **phased for later, not yet enforced**.
- **Three divergences worth an explicit decision** (§7): canonical bank data lives in Postgres rather than the object `canonical` tier; managed-cloud migration + per-tenant KMS/IAM are pending; several retention controls are documented but not yet provisioned.

---

## 3. What is persisted, and where

A single ingestion (the DB-direct sync is representative of all adapters) produces four persisted artifacts. Three go to object storage; one goes to Postgres.

| Artifact | Destination | Tier | Code path |
|---|---|---|---|
| Staged pull bundle (extracted source rows, serialized) | Object storage | `temp` | `upload_source` — `app/services/ingestion.py` |
| Immutable raw source snapshot | Object storage | `raw` | `_persist_raw_artifact` — `app/services/ingestion.py` |
| Validation report | Object storage | `outputs` | `_persist_report_artifact` — `app/services/ingestion.py` |
| Canonical financial records (positions, snapshots, GL, counterparties, products) | **Postgres** | — (RDBMS) | `_persist_canonical` — `app/services/ingestion.py` |

Object paths and buckets follow the bucket-per-institution-per-tier convention (`storage.md §3`), so each institution's artifacts are physically scoped to its own buckets.

**Concrete evidence:** the live FLEXCUBE syncs run during this build returned HTTP `202` after writing the ~167k-row staged bundle (`temp`), the raw snapshot (`raw`), and the validation report (`outputs`) into the Sample Bank institution's MinIO buckets, with the 150k+ canonical positions committed to Postgres.

### 3.1 The canonical-tier divergence (important)

`storage.md §1.3` maps "Layer 2: Canonical Model → `canonical` tier." In the code, the object-storage `canonical` tier is used **only by the market-data cache** (`app/adapters/market_data/cache.py`); **bank canonical financial data is persisted to Postgres, not to the object `canonical` tier.**

Consequence for the `storage.md §9.5` reproducibility guarantee: point-in-time reproduction currently rests on **(a)** Postgres immutable/supersession rows, **(b)** the `raw`-tier source snapshot, and **(c)** the value-based `input_hash`, rather than on versioned canonical *objects*. This is a reasonable MVP choice (Postgres is the operational query store and already carries provenance + lineage), but it is a divergence from the spec and should be an explicit, recorded decision rather than an accident.

---

## 4. Centralized storage: backing store and the MinIO gate

We *do* have and use centralized storage; the reasons it is MinIO rather than managed AWS/GCP today are deliberate policy, not omission:

- **One abstraction, swappable backend.** All application code depends on the `StorageClient` interface (`storage.md §4`); MinIO speaks the S3 API, so MinIO → GCS/S3 is a configuration change plus per-backend auth wiring, not a rewrite (`app/storage/s3_compatible.py`, `factory.py`).
- **MinIO is sanctioned for synthetic MVP data only** (`storage.md` principle #9, §5.1). No real bank data may land on it.
- **Enforced, not just documented.** `enforce_retirement()` (`app/storage/config.py`) refuses to initialize storage when `env=mvp` and today is past `STORAGE_RETIRE_AFTER` (2027-01-14), or when the date is unset. This guardrail exists specifically to stop MVP infrastructure from silently becoming production infrastructure. (It is also why CI failed with a `503` until the retirement date was configured.)

**Migration to managed GCS (preferred) or S3 is required before the first paying bank customer** (`storage.md §11`), and is a data-move behind the unchanged abstraction, not a code rewrite.

---

## 5. Retention & lifecycle: policy vs. enforced

The retention policy is `storage.md §6`. Honest enforcement status in the current code:

### 5.1 Enforced today
- **`temp` → 30-day auto-delete.** A lifecycle rule is provisioned per temp bucket (`TEMP_EXPIRY_DAYS = 30`, `_ensure_temp_lifecycle` in `app/storage/provisioning.py`).
- **Versioning on retained tiers.** `raw`, `canonical`, `outputs` have versioning enabled at provisioning (`provisioning.py`).
- **Logical-delete-only for retained tiers.** `RETAINED_TIERS = (raw, canonical, outputs)` delete via marker; only `temp` permits physical delete (`app/storage/client.py`, `s3_compatible.py`).
- **Per-institution-per-tier buckets**, access logging, and the retirement gate.

### 5.2 Policy exists, not yet enforced in code (phased, `storage.md §12`)
- **7-year archival / cost-tiering** and non-current-version cleanup on `raw`/`canonical`/`outputs`. Today these tiers simply retain everything (no expiration rule) — which *satisfies* the "7+ years minimum" trivially by never deleting, but the Glacier/Nearline transitions are deferred (Phase 3).
- **Legal hold** workflow (`§6.2`) and **institution offboarding** retention (`§6.4`).
- **Per-institution CMEK/KES keys** (`§7`) and **IAM impersonation isolation** (`§8`). MVP uses one platform encryption key plus application-layer tenant scoping; true per-tenant KMS + IAM assume-role is Phase 2.
- **Break-glass access** (`§7.5`), hash-chained audit at the storage layer (`§9.3`), cross-region replication (`§10`).

---

## 6. Required vs. not required (by phase)

- **Required now — MVP / Phase 1 (in place):** per-institution-per-tier buckets, versioning, `temp` lifecycle, retirement gate, access logging, encryption at rest (platform key).
- **Required before the first paying bank — Phase 2 (not yet done):** migrate off MinIO to managed GCS/S3; per-institution CMEK; IAM impersonation isolation; legal-hold workflow; cross-region replication.
- **Not required yet — Phase 3–4:** archival cost-tiering, break-glass, SOC 2 Type II, BYOK, the S3 (vs GCS) backend, Azure.

---

## 7. Gaps and recommendations

Three items warrant an explicit decision or tracked follow-up:

1. **Canonical persistence location.** Decide and record whether bank canonical data should also be written to the object `canonical` tier (per `storage.md §1.3`) for object-versioned reproducibility, or whether Postgres-plus-`raw`-snapshot-plus-`input_hash` is the accepted reproducibility basis for MVP. Update `storage.md` or the code to match the decision — do not leave them silently divergent.
2. **Managed-cloud migration readiness.** The MinIO retirement gate is 2027-01-14 and migration is required before the first paying customer. Stand up the GCS backend + per-institution CMEK + IAM impersonation ahead of that trigger, not at it (`storage.md §11`, §12.2).
3. **Retention control enforcement.** The 7-year archival tiering, non-current-version cleanup, legal-hold workflow, and storage-layer hash-chained audit are policy but not yet provisioned. Sequence these with the Phase 2 migration so retained-tier governance is enforced by infrastructure, not by the absence of a delete path.

None of these block the MVP; all should be closed before real bank data lands.

---

## Appendix A: Evidence (code references)

| Claim | Reference |
|---|---|
| Staged bundle → `temp` | `app/services/ingestion.py` (`upload_source`) |
| Raw snapshot → `raw` | `app/services/ingestion.py` (`_persist_raw_artifact`) |
| Validation report → `outputs` | `app/services/ingestion.py` (`_persist_report_artifact`) |
| Canonical → Postgres | `app/services/ingestion.py` (`_persist_canonical`), `app/models/canonical.py` |
| Object `canonical` tier used only by market data | `app/adapters/market_data/cache.py` |
| Retirement gate | `app/storage/config.py` (`enforce_retirement`, `STORAGE_RETIRE_AFTER`) |
| `temp` 30-day lifecycle + versioning | `app/storage/provisioning.py` (`TEMP_EXPIRY_DAYS`, `_ensure_temp_lifecycle`) |
| Logical-delete-only for retained tiers | `app/storage/client.py`, `app/storage/s3_compatible.py` (`RETAINED_TIERS`) |
| Backing store abstraction / backend swap | `app/storage/factory.py`, `app/storage/s3_compatible.py` |

*End of review v1.0. Supersede or revise as the Phase 2 managed-cloud migration proceeds.*
