# AequorOS Audit Evidence Package — Index

**Repository:** `aequorOS` · **Branch:** `eric` · **Baseline commit:** `f33e869`
**Alembic head (repository):** `202608230039` · **Alembic revision (production primary, re-measured read-only at the close of 2026-08-22):** `202608230039` — **level with the head.** It was four migrations behind earlier the same day; all four were applied and verified **by effect**, not by version string (`§WS-A12-R2 §R2-13`)
**Prepared:** 2026-08-22 · **Header revisions corrected 2026-08-22 (WS-A12, then the WS-A12
execution pass)**

> ## READ FIRST — the execution pass, and what it changed
>
> The first verification pass over this package **ran no tests**: every `VERIFIED` in it meant
> *"the fix is in the source at the line cited"*, never *"a test proves it"*. A second pass
> (`remediation_master_register.md` **§WS-A12-R2**) ran the suites. Results:
>
> | | of the 50 claimed-closed findings | of all 56 verified |
> |---|---:|---:|
> | **Confirmed by a test executed** | **41** | **44** |
> | **No test pins it** | **9** | **12** |
> | **Failed** | **0** | **0** |
>
> **Three things a reader must carry into every document below:**
>
> 1. **"In force in the repository" and "in force in production" are different claims, and
>    this package must always say which.** Four migrations were unapplied on the primary for
>    most of 2026-08-22 — `D-14` (tenant RLS on the last two tenant tables), `D-17` (governance
>    append-only) and `D-18` (run parameter provenance) were closed in code and open in
>    production. **All four were applied at the close of the day and are now in force**,
>    verified by effect: both tables `rowsecurity=True forced=True policies=1` with a
>    fail-closed predicate, both governance triggers present, the provenance column present.
>    The `organization_id` RLS gap is now exactly the three documented cross-tenant-by-design
>    tables. **The gap was found only because someone queried the primary rather than reading a
>    header, and no automated check yet fails when the primary falls behind the head.**
> 2. **Four things are filed or published on no registered authority.** A test is red on
>    purpose naming each: the metrics `car_pct_end` (a stressed CAR compared against a
>    threshold) and `diversification_benefit_ghs`; the sealed line item
>    `fx_var:diversification_benefit`; and **`app/services/implied_rating.py`, an entire
>    module publishing 22 live figures** — PDs, rating grades, a sovereign ceiling, a DDEP
>    eligibility determination — with no declared basis. These are founder decisions, not bugs.
> 3. **`D-11` is fully open and unowned**: three dashboard sites render fabricated regulatory
>    floors, and the static guard that scans them reports clean because two evade its rules
>    syntactically and the third sits in an excluded directory. The guard has **no negative
>    control**, though CI states it does.
>
> 4. **Tenant row-level security is proven DECLARED, not proven to WORK.** The DDL-level
>    completeness gate passes 3/3 against a migrated Postgres; every suite that would show a
>    policy actually isolating one tenant from another either skipped or failed, because the
>    only available test-database role holds `BYPASSRLS`. And `AUD-1`'s append-only proof
>    errors inside its own fixture and has never asserted anything.
>
> **No certification of any kind is held or claimed** — no SOC 2, no ISO, no regulatory
> approval.

> ## ⚠ This package is UNTRACKED and does not ship with the repository
>
> `.gitignore:42` is `/docs/`. Verified: `git ls-files docs/` → **0 files**. **An examiner who
> clones this repository receives none of these documents, and every link below is dead
> outside this checkout.** The exclusion is deliberate — the rule is "local documentation and
> client collateral" — so it is a founder decision, recorded as `NEW-47` and restated at the
> head of `remediation_master_register.md`. **An evidence package that does not ship is not
> evidence.**

---

## What this package is

A sixteen-document set describing the controls, calculation authorities, data lineage and
known limitations of the AequorOS regulatory platform, prepared for review by a supervisory
examiner or an acquirer's technical due-diligence team.

**Completion status (2026-08-22): all sixteen documents exist**, plus this index and three
supporting records — `remediation_master_register.md` (the programme's living ledger, including
its own corrections and withdrawn claims), `workstream_dependencies.md`, and `claims_audit.md`
(every customer-facing and internal claim, its location, verdict and evidence).

## Evidence rule

**Every claim in this package cites a file path, a test name, an Alembic revision id, or a
command with its recorded output.** A statement that could not be pointed at was deleted.
Where verification was not possible from this environment, the text says `UNVERIFIED` and
states what would establish it.

Production measurements were taken **read-only** against the primary database under libpq
`options=-c default_transaction_read_only=on`, verified by a probe write being refused with
`ReadOnlySqlTransaction` before any query ran. Nothing in production was modified.

## What is deliberately *not* claimed

- **No certification of any kind.** No SOC 2, no ISO, no regulatory approval, no
  "audit-proof", no "compliant". The strongest formulation used anywhere in this package is:
  *all repository-verifiable controls identified by the audits have been implemented and
  verified; the system is ready for independent regulatory/compliance assessment.*
- **No live regulatory filing.** No return has ever been submitted to the Bank of Ghana.
  Measured: 53 packages, exactly **2** certified (both `LCR-NSFR`, carrying no capital
  figures), and all 7 submission events against a simulation channel. See §06 and §15.
- **No object-storage restore, no backup schedule, no measured RPO.** A database restore drill
  was executed and passed on 2026-08-22 with a measured RTO floor of ~123 s; **RPO is
  unbounded** because no backup interval is configured, and **filed-artifact recoverability is
  unproven**. See §13 and §14 §3.7.
- **No live core-banking or market-data vendor connectivity.** No adapter has ever run against
  a bank's core or a vendor contract. The Oracle thin driver **does** ship in the deployment
  image; SQL Server/ODBC, generic JDBC and Snowflake are built and tested but are **not**
  installed in it. See §05 §4.1.
- **No published Bank of Ghana LCR or NSFR requirement.** Any 100% floor, run-off rate, inflow
  rate or inflow cap in use is a Basel default (BCBS 238) standing in for a rule the regulator
  has not issued. See `backend/docs/bog_parameter_sources.md` §2.6.
- **Several instruments the platform builds against are exposure drafts**, not law: the
  Liquidity Monitoring Tools Directive, the Liquidity Risk Management Directive, the Directive
  on Stress Testing and the ICAAP guideline (all February 2026, stated effective 1 January
  2027), and the IRRBB guideline.
- **The suite is not green.** One architecture-inventory test fails on the settled tree. See
  §12 §1.

## Contents

| # | Document | Subject |
|---|---|---|
| 01 | [System architecture](01_system_architecture.md) | Entrypoints, calculation planes, deployment topology, boundaries and the guards that hold them |
| 02 | [Calculation authority registry](02_calculation_authority_registry.md) | The single authority per (metric, regime, methodology); 78 registered, 47 filable, 40 needing external verification |
| 03 | [Metric lineage](03_metric_lineage.md) | Filing → package → approval → run → engine → policy → parameter → fact → batch, traced end to end on a real certified return |
| 04 | [Policy and parameter governance](04_policy_governance.md) | Control plane, four-eyes, tightening clamp, citation discipline, and where `confirmed` is not justified |
| 05 | [Data lineage](05_data_lineage.md) | Ingestion → canonical → fact derivation → official plane, with a worked example and the GL-loader defects |
| 06 | [Regulatory reporting lineage](06_regulatory_reporting_lineage.md) | Return registry, template authority, eligibility, export artifacts, submission channels |
| 07 | [Security controls](07_security_controls.md) | Authentication, session revocation, throttling, egress control, secrets, e-signature trust chain |
| 08 | [Tenant isolation](08_tenant_isolation.md) | RLS posture measured on the primary, dependency boundary, impersonation invariant, operator plane |
| 09 | [Maker-checker](09_maker_checker.md) | Separation of duties across filings, signing, parameters and exceptions |
| 10 | [Calculation versioning](10_calculation_versioning.md) | Engine/schema versions, value-based hashing, immutability, the BIA correction, restatement position |
| 11 | [Reconciliation controls](11_reconciliation_controls.md) | Balance-sheet identity, source-book overlap, governed exceptions, and the hole that remains |
| 12 | [Test evidence](12_test_evidence.md) | What the suite proves, measured; what it does not; CI coverage gaps |
| 13 | [Backup and restore evidence](13_backup_restore_evidence.md) | The executed drill, its result, and everything it does not cover |
| 14 | [Production readiness](14_production_readiness.md) | Gate-by-gate status against measured evidence |
| 15 | [Known limitations](15_known_limitations.md) | Complete disclosure register, including withdrawn claims and unresolved items |
| 16 | [External verification schedule](16_external_verification_schedule.md) | The 40 calculation authorities requiring external regulatory verification, itemised: value in question, what Bank of Ghana source would settle it, and which 14 can reach a filed return today |
| — | [Claims audit](claims_audit.md) | Every customer-facing and internal claim, its location, verdict and evidence |
| — | [Remediation master register](remediation_master_register.md) | The programme's living ledger, including its own corrections |

The lineage pair — **§03 and §05** — is the heart of the package. Together they trace one
reported number from a signed filing back to the ingestion batch that produced its inputs.

## Source audits referenced

| Document | Finding ID prefix |
|---|---|
| `backend/docs/AEQUOROS_ENTERPRISE_PLATFORM_AUDIT_2026-08-20.md` | `P0-n` |
| `backend/docs/forensic_calculation_audit_2026-08-21.md` | narrative |
| `backend/docs/FORENSIC_CALCULATION_ARCHITECTURE_AUDIT_2026-08-21.md` | `CF-n` |
| `backend/docs/INDEPENDENT_FORENSIC_REAUDIT_2026-08-22.md` | `D-n` |
| `backend/docs/bog_parameter_sources.md` | citation dossier |
| `docs/audit/remediation_master_register.md` | `ARCH-n`, `NEW-n`, `INF-n`, `OPS-n`, `AUD-n`, `S-n`, `L-n`, `D-n` |

## Measurement corrections made during preparation

Five figures repeated in the source material did not reproduce when measured against the code
for this package. The measured values are used throughout; the earlier ones are recorded here
so the difference is not silent. **These are counting differences, not disagreements about
substance.**

| Claim in the source material | Measured for this package | Command |
|---|---|---|
| 41 authorities require external regulatory verification | **40** | `REGISTRY.requiring_external_verification()` |
| 57 seeded regulatory parameters | **70** at runtime (54 literal rows + 16 generated LMTD floors). The "57" counts textual `ParamSpec(` occurrences: 54 data rows + 2 loop-body constructions + 1 class statement | `len(SEED_PARAMETERS)` |
| 54 parameters, 40 confirmed / 14 pending (citation dossier §6) | **70**, **56 confirmed / 14 pending** — the dossier counted the literal rows only; all 16 generated LMTD rows are `confirmed` | as above |
| ~93 outstanding Ruff findings | **60** in `backend/` (the register's figure was measured mid-programme with several workstreams editing concurrently) | `uv run ruff check .` |
| 24 BoG workbooks | **23** — the 24th file is a Word guide, not a workbook | file count under `docs/reporting/` |

## Substantive corrections to earlier findings

Recorded because acting on the withdrawn version would cause harm. Each is documented in full
where it belongs.

| Withdrawn claim | Corrected position | Where |
|---|---|---|
| *"The `LCR-NSFR` return applies no inflow cap."* | **False.** Both LCR methodologies cap inflows; the divergence is aggregate-vs-per-currency and governed-vs-hardcoded | §02 §5.2 |
| *"There is no `car_min` seeded for `institution_class / bank`."* | The row exists. The finding came from a regex over source that dropped multi-line calls | §04 §5 |
| *"Act 930 s.29 is an enabling provision only."* | **Refuted** — s.29(2) states a 10% statutory floor | §04 §5 |
| *"A current 2026-06-30 GL row of 0.00 was overridden by a stale value."* | There is no `0.00` row. The **entire** current generation carried NULL balances and May's ledger was served as June's | §05 §6.2 |
| *"The live books do not balance; the bank should reconcile its general ledger."* | **Withdrawn.** The control's output was validated without validating its inputs. On the one provably clean book it reported 16.5% | §05 §6.6, §11 §2 |
| *"Sample Bank's GL balances to one pesewa."* | Arithmetically true, analytically wrong — it balanced by ignoring impairment | §11 §3 |
| *"Per-source-system supersession is a platform defect."* | **Deliberate design.** Cross-source supersession would delete a legitimate second source's book | §11 §5 |
| *"`CanonicalPosition.superseded_by` is never assigned anywhere."* | It is assigned by same-source replacement. The accurate gap is that **no withdrawal path exists** | §11 §6, §15 §5.2 |
| *"DB-direct is non-functional in the shipped image."* | Too broad. **Oracle thin mode ships**; SQL Server, JDBC and Snowflake do not | §05 §4.1, §15 §1.1 |
| *"The ML test failure means the ML story needs re-examination."* | **Withdrawn** — a stale expected constant; the substantive assertions pass | §12 §4.5 |
| *"There is no tested backup or restore anywhere."* | Superseded by an executed drill on 2026-08-22. What remains absent is schedule, retention, PITR and object-storage restore | §13, §14 §3.7 |
