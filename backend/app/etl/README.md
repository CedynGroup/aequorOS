# ML-ETL Layer (`app/etl`)

The machine-learning-assisted preprocessing + deduplication layer between the source
adapters (Layer 1) and the canonical model (Layer 2) of the Data Engine
(`docs/data_engine.md` §3). New component per the enterprise Data Engine build brief;
governed by `data_engine.md` §12.5 / §7.4 model discipline and `storage.md` §9 audit.

## Where it plugs in (confirmed seam)

`app/services/ingestion.py`:

```
adapter.extract()  ─►  [ app.etl.ETLPipeline ]  ─►  adapter.translate()  ─►  _persist_canonical()
     (raw)              preprocess + dedup            (canonical)              (validate/enrich/persist)
```

The ETL layer consumes the adapter's post-`extract` `ExtractionResult` (a source-agnostic
shape) and returns a cleaned `ExtractionResult`. It never imports adapter/source internals,
so `data_engine.md` §2.1 ("adapters know sources, nothing else does") holds — see
`questions/Q05`.

## Invariants (enforced in `contracts.py`)

1. **Never silently modify a regulatory-critical value.** `REGULATORY_CRITICAL_FIELDS`
   (balance, notional, rate, counterparty_id, currency, ifrs9_stage, risk_weight, …) may
   be **flagged** but never rewritten. `guard_sanctioned()` raises on violation.
2. **Every operation is reversible via lineage.** The original (`before`) is always
   retained; sanctioned ops carry `before != after`, flags carry `after is None`.
3. **Every ML output carries confidence + model version and is human-overridable**
   (`ETLProvenance`). Deterministic rule ops carry `model_id=None`.
4. **Dedup never destroys records.** It emits `LinkageRecord`s (winner + subsumed source
   ids + per-signal scores), preserving all source rows for audit.
5. **Every op emits lineage (`ETLLineageSink`) + audit (`ETLAuditSink`).**

## Directory map

```
etl/
  contracts.py                     # ✅ step 1: interfaces, guard, ports (this commit)
  pipeline.py                      # ⬜ step 1 (cont.): orchestrates preprocess → dedup
  audit.py                         # ⬜ step 1 (cont.): sinks → services/audit.py + LineageRecord
  preprocessing/
    normalizers/                   # ⬜ step 1: ISO 4217/3166, ISO 8601, case/whitespace, unicode
    type_coercion/                 # ⬜ step 1: "15.5%"→0.155, "N/A"→null, Excel serial dates
    reference_resolution/          # ⬜ step 1: bank product code → canonical product id (MappingConfig)
  deduplication/
    counterparty_matcher/          # ⬜ step 4: fuzzy + phonetic + national-id + address + RF classifier
    position_deduplicator/         # ⬜ step 4: cross-time legitimate-vs-bug via lineage/source_reference
    fingerprint_detector/          # ⬜ step 4: Isolation Forest on record fingerprints
  models/
    counterparty_matching_model/   # ⬜ step 4: sklearn RandomForest match-probability (versioned, MRM)
    anomaly_detection_model/       # ⬜ step 4: sklearn IsolationForest (versioned, MRM)
```

Sanctioned model types (build brief): TF-IDF + edit distance, Soundex/Metaphone/Double
Metaphone, RandomForest match probability, IsolationForest anomaly, deterministic rules
where ML is overkill. **No deep learning** — it is not audit-defensible to a bank examiner.

## Build order (this component, within the larger brief)

1. **Framework** — contracts, guard, ports *(done)*; then `pipeline.py` + `audit.py` sinks
   wired to `services/audit.py` and `models/ingestion.py::LineageRecord`; then the
   deterministic `preprocessing/*` stages (sanctioned, no ML). Verified against `data/`.
2. Excel/CSV adapter re-verification end-to-end through this layer (ground truth).
3. ML models (`models/*`) trained + validated on Sample Bank Limited; wired into
   `deduplication/*`. MRM discipline: versioned artifacts, confidence, human override.
4. Contract tests: linkage correctness, reversibility, flag-not-modify, confidence+lineage
   on every op, and "no regulatory value silently modified."

## Reference dataset

Sample Bank Limited raw sources live in `data/` (the docs' `sample_bank_data/`), SBL-GH-001,
as-of 2026-04-30, deterministic seed `20260521` (`data/README.md`). The dataset carries
deliberate imperfections (GL-vs-subledger 3–5% drift, ~3 NMD accounts missing duration,
some unrated SME counterparties) that this layer must flag rather than mask, yielding
`ACCEPTED_WITH_WARNINGS`. See `questions/Q02` re: gitignore/CI reproducibility.
