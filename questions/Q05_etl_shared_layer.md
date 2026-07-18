# Q05 — ML-ETL as a new shared layer vs "adapters know sources, nothing else does"

**Deciding:** where the ML-ETL layer sits and how it stays consistent with the docs'
strongest principle.

**What the docs say:**
- `data_engine.md` §2.1: *"Adapters know sources. Nothing else does."* §16: *"No shared
  code path between adapters … Do not merge adapters for reuse."*
- But normalization/dedup are not source-specific: §6 (validation), §7 (enrichment), and
  §12 (intelligence) are all shared, source-agnostic layers that already sit *downstream*
  of adapters.

**The tension:** the brief introduces `/etl/` as a shared component "between Layer 1
(adapters) and Layer 2 (canonical)" — explicitly "a new component not fully specified in
existing documents." A shared pre-canonical layer is fine *only if* it operates on
already-extracted, source-agnostic records and never reaches back into source-system
semantics (which would violate §2.1).

**Confirmed integration seam (from the code):** `services/ingestion.py` runs
`adapter.extract()` (→ raw source-shaped records) → `adapter.translate()` (→ canonical) →
`_persist_canonical()`. The ETL layer inserts **after `extract`** (dedup/normalize operate
on raw records, which is where "ACME TRADING LTD" vs "Acme Trading Limited" must be caught)
and feeds `translate`. It sees only the adapter's declared record shape, not T24/Oracle
internals — so §2.1 holds: adapters still own source knowledge; ETL owns cross-source
normalization/dedup on the neutral post-extract shape.

**Default I'm proceeding on:** `backend/app/etl/` as a shared, source-agnostic layer
invoked between extract and translate; it consumes the adapter's neutral extraction output
and never imports adapter/source internals. Every op emits lineage (§8.2) + audit (§8.4 /
`storage.md` §9) and honors §12.5 governance.

**Need from Eric/Dela to lock:** confirm this seam (post-extract, pre-translate) and that a
shared source-agnostic ETL layer is acceptable given §2.1 (I read it as consistent, since
ETL never touches source semantics).
