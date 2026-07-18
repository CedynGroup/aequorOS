# Q01 — The brief overrides the reference docs' explicit phasing

**Deciding:** whether to build ML-ETL (dedup/anomaly), the database-direct adapter, and
hardened live vendor adapters *now*, at enterprise grade.

**What the docs say (they explicitly defer exactly this work):**
- `data_engine.md` §17.8: *"Do not implement intelligence layer features (section 12) in
  MVP. … Those are Phase 4."* §12 is precisely the ML dedup / anomaly / mapping-assist
  layer the brief calls for.
- `data_engine.md` §15.3 places the **database-direct adapter** in Phase 3 (Months 15–24);
  §15.4 places anomaly detection / ML mapping in Phase 4 (Months 24+).
- `market_data_adapter.md` §14.1 defines the MVP as *"Bloomberg and Refinitiv adapters
  implemented as complete code with fixture-based testing but **not yet connected to live
  vendor endpoints**,"* and §16.10: *"If a market data design conflicts, the parent
  document wins. Surface the conflict."*

**The conflict:** the docs' own conflict-resolution rule says *the parent doc wins*; the
brief says *the brief wins* ("this … overrides any language in the reference documents
suggesting a lighter MVP approach for the Data Engine specifically").

**Default I'm proceeding on:** the brief is the later, human-issued instruction and
explicitly anticipates and overrides this. I build the Data Engine at enterprise scope
now, **while honoring the docs' architecture and discipline** — the adapter contract
(§5), canonical metadata (§4.3), validation (§6), lineage + audit (§8), and especially the
§12.5 / §7.4 model-governance guardrails (versioned, confidence-scored, human-overridable,
no silent modification of regulatory values). Only the *timing/phasing* language is
overridden; none of the *architecture* is.

**Need from Eric/Dela to lock:** confirm this reading — enterprise scope now, docs'
architecture intact. If instead you want ML-ETL/DB-direct built but kept behind a
feature-flag/Phase gate (dark-launched), say so; it changes wiring, not design.
