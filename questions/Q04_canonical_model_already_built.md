# Q04 — Canonical model already exists as SQLAlchemy, not the docs' `/schema/*.sql` DDL

**Deciding:** whether to author the raw-DDL canonical model the docs describe, or treat
the built one as authoritative.

**What the docs say:** `data_engine.md` §4.5 specifies the canonical model as PostgreSQL
DDL files under `/schema/canonical_v1/*.sql`, mirrored in Snowflake.

**What actually exists (and is good):** the canonical model is implemented as SQLAlchemy
models in `backend/app/models/canonical.py` + Alembic migrations, with provenance metadata
owned by [Data Engine §4.3](../docs/data_engine.md#43-mandatory-metadata-columns),
immutable supersession, `NUMERIC(28,6)` money, rates-as-fractions.
There is no `/schema/` tree and no Snowflake mirror.

**Default I'm proceeding on:** the existing SQLAlchemy model **is** the realized §4
canonical model. I do **not** rewrite it into raw DDL or add Snowflake — that would be
destructive and pointless, and the built model already satisfies §4.3. New ETL/dedup
artifacts (linkage records, ETL lineage nodes) will be added as SQLAlchemy models +
Alembic migrations, consistent with the existing pattern.

**Need from Eric/Dela to lock:** confirm SQLAlchemy-as-canonical (not raw DDL / Snowflake)
for this build. Snowflake mirroring, if still wanted, is an analytical-store concern to
schedule separately.
