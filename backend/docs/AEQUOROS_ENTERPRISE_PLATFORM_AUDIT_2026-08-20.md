# AequorOS Enterprise Platform Audit

**Original audit:** 2026-08-20 (grep-based, single perspective)
**Superseded:** 2026-08-21 — full file-by-file audit against `main` @ `f33e869`
**This revision:** 2026-08-22 — every finding below re-verified against the current working tree after a twelve-workstream remediation. Status labels, line numbers and counts are as of that date.
**Scope:** the entire repository — backend source, tests, migrations, dashboard, operator console, marketing site, continuous integration, deployment, and the documentation set.

**Post-audit update (2026-08-30):** the statement below that the browser suite
runs in no workflow is historical. `.github/workflows/risk-service.yml` now has
a blocking `journeys` job that starts disposable MinIO with built-in KMS and
runs the dashboard Playwright suite. Its machine-readable reporter requires at
least 20 executed journeys and exactly eight expected failures caused by the
pre-existing regulator-anchor/fixture-snapshot mismatch tracked in issue #151;
non-quarantined failures, unexpected passes, and quarantine/discovery drift fail
the gate.

---

## 0. How to read this document

### 0.1 What this revision is

The 2026-08-21 edition was a coverage audit: thirteen partitions, a measured read count per partition, and a defect register. Its findings are reproduced here **with their current status**, because a defect register whose entries are never re-tested becomes fiction in both directions — it keeps claiming defects that were fixed, and it keeps claiming fixes that never executed.

Both failure modes have already occurred in this document's history, so the standard for this revision is deliberately narrow:

> **No fix is recorded as landed unless the code implementing it was read.** Every status carries a `file:line`. Where a claim could not be checked, it says **unverified** — which is a result, not an omission.

Three items in the remediation were found **present in source but never executed**: two governed-parameter fixes that no test and no linter had ever seen, one refusal path that would have raised `AttributeError` at the moment it tried to refuse, and an append-only proof that errored inside its own fixture and therefore asserted nothing from the day it was written. Code existing is not code working. That distinction is the reason for the standard above.

### 0.2 Evidence tiers

- **[V-A] Verified directly for this revision.** The cited code, migration, database row or continuous-integration log was read by the audit owner. Highest confidence.
- **[V-B] Verified by a delegated read.** A reader opened the file and cites `file:line`. Reliable; not independently re-checked.
- **[M] Measured.** A count produced by executing a query or a command, with the method stated at the finding.
- **[INF] Inferred.** Reasoned from code, not executed. Treat as a lead.
- **Negative claims** ("X does not exist") state the search performed.

### 0.3 What a reader outside the team can and cannot check

This repository is public. Two things follow, and both are limits on this document rather than on the platform:

- `/docs/` is excluded from version control in its entirety (`.gitignore:42`; `git ls-files docs/` returns zero files) [V-A]. Product specifications, the regulatory-source register's companions, the deployment guide and the Bank of Ghana return workbooks under `docs/reporting/` are therefore **not** in the public tree. Where a finding depends on one of them, this document states the finding and names the source, but a reader outside the team cannot reproduce the check.
- `backend/docs/` **is** public, and this file lives there.

**No certification is claimed anywhere in this document.** AequorOS holds no SOC 2 attestation, no ISO certification, and no regulatory approval or licence from any supervisor. Nothing below should be read as implying otherwise. Where this document says a control "holds", it means the code implementing it was read and its test was found; it does not mean an external party has assessed it.

### 0.4 What the 2026-08-21 edition got wrong

| Prior claim                                                                      | Status                                                                                                                                                                                                                                                                                        |
| -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P0-15: backend CI is dead "caused by commit `f33e869`"                           | **Half right, and the half it got wrong matters.** `f33e869` did cause the 2026-08-21 failure — that run recursed 3,476 times and died on `Resource temporarily unavailable (os error 11)`. But CI had already been failing since **2026-08-09**, for two entirely different reasons. See §5. |
| "RLS forced on 114 of 132 tables" (itself a correction of an earlier "85 of 89") | **Superseded by a live measurement.** Against the primary database: **123** tables carry `organization_id`; **120** have row-level security enabled _and_ forced; **3** are documented cross-tenant exceptions. See §1.1.                                                                     |
| "Both divergent `ARCHITECTURE.md` files describe a header-trust auth model"      | **Imprecise.** Only one `ARCHITECTURE.md` is version-controlled, and it does not describe that model (repo-root grep for `X-Org-Id`, `header trust`: no hits). The stale copy is the untracked `docs/ARCHITECTURE.md:39-41`, which is not public.                                             |
| "No test of `verify_step_up` exists"                                             | **No longer true.** `backend/tests/services/test_attestation_stepup_throttle.py` and `test_attestation_stepup_oidc_failure.py` both exercise it.                                                                                                                                              |
| "`httpx` is development-only" (§2.2, market-data row)                            | **False.** It ships transitively through the web framework's standard extra, and four application modules import it at module scope. The vendor conclusion it was offered in support of still stands on other evidence. See §4.2.                                                             |
| "Section layouts: 87"                                                            | **Does not reproduce.** The current total is **168** across 39 templates, of which 92 are hand-authored and 76 are generated one-per-official-sheet. Both 52 and 87 were counts of the hand-authored bucket at earlier moments. See §4.3.                                                     |

### 0.5 Overall assessment

**The financial mathematics remains the strongest part of this codebase.** The pure engines under `backend/app/domain/` are hand-verified against inline arithmetic, the Bank of Ghana template binding is exact and provable, and the craft is real.

**The dominant defect class named in the 2026-08-21 edition — _silent substitution_ — has been substantially closed, and closed in the right way.** Where an input is missing, the majority of the paths audited now raise a typed refusal carrying a machine-readable state (`MISSING_REQUIRED_INPUT`, `POLICY_UNRESOLVED`, `DATA_QUALITY_BLOCK`) instead of supplying a plausible number. The balance-sheet identity now blocks filing rather than plugging silently. The two board-facing "are we safe?" signals now fail closed. The institution-type discriminator, the capital floors, the high-quality-liquid-asset tiering and the stress scenario loader all refuse rather than assume.

**Three things temper that.** First, the remediation is **uncommitted**: 316 files changed, 464 paths in `git status`, and none of it is on `main`. Second, **continuous integration has not executed a backend test since 2026-08-03** — the hermetic suite passes locally with zero failures across 4,489 tests (§6.5), but no gate has run it, and the Postgres isolation gate has never executed under the restricted role that makes it meaningful. Third, the live data plane carries defects the code fixes do not reach — a risk-weight vocabulary the register cannot resolve, a currency-conversion contradiction nothing validates, and 117 sealed runs whose evidence has been withdrawn (§3).

**Readiness by stage:**

- **Demonstration:** ready. The "synthetic data" banner defect is fixed on the two surfaces the prior edition named; one remaining instance is listed in §4.1.
- **Controlled single-tenant pilot** (sandbox or manual submission, human validation of every figure): ready in principle, contingent on the work being committed and on continuous integration actually running.
- **Production go-live, any institution type:** not ready. See §9.

---

## 1. The P0 register — status as of 2026-08-22

Every entry names the original defect, then the current state.

### 1.1 Security and tenant isolation

**P0-1 — `current_financial_facts` had no row-level security. CLOSED.** [V-A][M]
The original creating migration still contains zero `ROW LEVEL SECURITY` statements, as it always did; the fix is a separate migration. `backend/alembic/versions/202608220027_current_financial_facts_rls.py:47-56` issues `ENABLE`, `FORCE`, and a tenant-isolation policy whose predicate is `NULLIF(current_setting('app.organization_id', true), '')` — it fails closed to NULL when no tenant is set, and performs no `::uuid` cast. `backend/tests/db/test_current_financial_facts_rls.py` asserts the catalogue state, cross-tenant read/update/delete/insert refusal, aggregate leakage, and downgrade reversibility.

The same wave closed two more: `backend/alembic/versions/202608230036_rls_implied_rating_and_entitlements.py:62,69-79` enables **and** forces row-level security on `implied_rating_runs` and `market_data_entitlements`, each with a policy.

**Measured against the primary database on 2026-08-22**, through a session opened with `options=-c default_transaction_read_only=on` (a `CREATE TEMP TABLE` probe was issued first and correctly refused):

| Population                           |                                                               Count |
| ------------------------------------ | ------------------------------------------------------------------: |
| Tables carrying `organization_id`    |                                                                 123 |
| — row-level security enabled         |                                                                 120 |
| — row-level security **forced**      |                                                                 120 |
| — documented cross-tenant exceptions |                                                                   3 |
| All tables in `public`               |                                                                 139 |
| — forced                             | 121 (the 121st is `organizations`, tenanted by its own primary key) |

Enabled-but-not-forced is the empty set. The three exceptions are `integration_keys`, `operator_inspector_sessions` and `tenant_storage`, each justified in `backend/tests/db/test_tenant_rls_completeness.py:64-81`. That test enumerates every `organization_id`-bearing table from the migrated catalogue and requires row security enabled, forced, and carrying at least one policy (`:118,135-147`); a companion test asserts the exception list names only real, genuinely unprotected tables, so the list cannot rot into a blanket waiver (`:188`).

The primary database's schema revision is `202608230039`, which equals the source head (`uv run alembic heads`) [V-A]. Migrations `202608230036`–`202608230039` were applied on 2026-08-22.

> **The gate only means something under a correctly-privileged role.** Measured 2026-08-22 and recorded in `.github/workflows/risk-service.yml:416-431`: the same `tests/db` run as the `postgres` superuser produces **5 failures**, every one an isolation assertion, because a superuser bypasses the policy and the "the other tenant's rows are invisible" checks see them. Under a `NOSUPERUSER NOBYPASSRLS` role it is **105 tests, 0 skipped**. A superuser does not weaken this gate, it **inverts** it — the tests that exist to prove isolation are the ones that go red, which invites a "fix" that relaxes the assertion. The workflow therefore creates a restricted role explicitly (`:436-441`), and the enforcement tests that need one guard for it: `backend/tests/db/test_current_financial_facts_rls.py:156-161` skips, `backend/tests/db/test_bog_return_recode_migration.py:176-179` fails outright. `test_tenant_rls_completeness.py` correctly carries no such guard — it reads catalogue state, which a superuser reads identically.
>
> The **105 passed / 0 skipped** figure is reported from the 2026-08-22 measurement, not re-executed for this document; `tests/db` collects exactly 105 tests, which is confirmed [M]. Re-running it requires a Postgres instance with that restricted role, and pointing it at the primary is prohibited.

**P0-2 — the read-only impersonation invariant was enforced in one dependency, not at the boundary. CLOSED.** [V-A]
`backend/app/api/deps.py:99-112` now performs the refusal inside `get_current_principal`, so it applies to every route regardless of which context dependency the route declares; `refuse_impersonated_mutation:66-96` fails closed — if the route cannot be identified, an unsafe method is refused rather than admitted. A second independent gate sits at `:291-300`. The exemption set is one entry (`:56-63`).

Re-counted across `backend/app/features/`: **167** routes use an unsafe HTTP method; **1** declares the read-only context — `backend/app/features/run_scenario_analysis.py:39`, whose service is documented and confirmed to write nothing (`backend/app/services/analysis_workbench.py:356`). All fourteen routes the prior edition named are pinned by name in `backend/tests/api/test_impersonation_boundary.py:66-81`, and the sweep carries a degeneracy guard so it cannot silently check nothing (`:228`).

**P0-3 — irreversible object-storage delete behind a read guard. CLOSED.** [V-B]
`backend/app/features/manage_documents.py:105` now declares the mutation context. `backend/app/services/documents.py:308-340` orders the delete before the commit deliberately, so a storage failure unwinds the transaction instead of committing a false success; both properties are pinned in `backend/tests/api/test_impersonation_boundary.py:387,415`.

**P0-4 — unthrottled password brute-force on the signing step-up. CLOSED.** [V-B]
`backend/app/services/attestation/stepup.py:239-255` checks the lockout **before** the hash comparison and records the failure through the shared primitive in `backend/app/services/auth_throttle.py`, which uses the same two `users` columns as sign-in, increments atomically in SQL, **commits** so a rolled-back transaction cannot erase the failure, and applies progressive backoff capped at one hour. The unknown-membership path burns an equalising hash so it is not a membership oracle. `backend/alembic/versions/202608230041_operator_login_lockout.py:42-55` gives the staff plane the same two columns.

The OIDC step-up path is deliberately **not** gated on the password lockout, with the reason stated at `stepup.py:171-176` and pinned by a test. That is a design decision, recorded here so it is not mistaken for a gap.

**P0-5 — refresh tokens could not be revoked. CLOSED, with the lifetime unchanged.** [V-B]
`backend/app/models/refresh_token.py` stores a SHA-256 digest and a family lineage; the row's identifier _is_ the token's `jti`. `backend/app/core/security.py:125-127` cannot mint a refresh token without one and `:167` requires it on decode, so a legacy token without a `jti` fails closed. `backend/app/services/authentication.py:568-652` implements rotation-on-use with **reuse detection**: a token presented after rotation and outside the grace window revokes the entire family. `set_password` now revokes (`:679-692`). Logout is idempotent and silent by design so it is not a validity oracle.

The refresh lifetime is **still fourteen days** (`backend/app/core/config.py:605-607`). What changed is that the token is now revocable, rotating and reuse-detecting, so the residual exposure after a compromise is one grace window rather than a fortnight — but the number itself did not move, and a shorter default should be considered on its own merits.

**P0-6 — server-side request forgery through tenant-configurable connection endpoints. CLOSED, with one residual stated in the code.** [V-B]
`backend/app/core/outbound.py` separates a non-resolving syntax check (for schema validation) from a **resolving, authoritative** check called immediately before each connect, and says explicitly that a syntax check alone is not a security control (`:18`). Coverage confirmed for IPv6-mapped IPv4, 6to4 and Teredo embeddings (`:300-327`, tests at `backend/tests/core/test_outbound.py:52-54`), and for redirects, which are re-checked hop by hop with relative `Location` resolution (`:502-532`). The guard is applied at the connection sites, not only in the schemas: `backend/app/services/database_connections.py:954`, `backend/app/services/temenos_connections.py:113`, `backend/app/services/regulatory_reporting/channels/orass_api.py:182`, and the OIDC discovery path at `backend/app/core/security.py:298`.

**Residual, admitted in-code rather than hidden:** DNS rebinding is not prevented. `check_host` resolves and validates every address and fails closed on NXDOMAIN, but the hostname is then handed to the driver, which resolves again. `OutboundTarget.addresses` exists so a transport _could_ pin, and no caller pins. The trade-off is stated at both call sites — pinning breaks TLS hostname verification for Oracle and SQL Server, which is judged the worse outcome (`backend/app/services/database_connections.py:920-931`).

**P0-7 — maker-checker was not enforced on the signing-certification path. CLOSED.** [V-A for the two enabling conditions, V-B for the release guard]
`backend/app/services/attestation/workflow.py:315-374` adds `ensure_checked_release`, and critically it reads **the signatures that exist, not the policy that asked for them** (`:328-331`): no checker signature refuses; a checker who also signed as preparer refuses, compared on both the user identifier and the signer identifier; and it then calls the same `ensure_decidable` primitive the bare approval path uses. It is wired at `:486`, **before** the status moves, and the approval is attributed to the checker rather than to whoever signed last (`:506`).

The two conditions that previously made the control optional are also closed. `_ensure_attested` is no longer a documented no-op when no signing policy is configured (`backend/app/services/regulatory_reporting/workflow.py:848-861`), and `ATTESTATION_ESIGN_REQUIRED` now defaults to **true** (`backend/app/core/config.py:157`). The deployment-wide kill switch still suspends the _signing_ requirement, but a test pins that it never suspends maker-checker (`backend/tests/services/test_attestation_maker_checker.py:403`).

### 1.2 Regulatory correctness

**P0-8 — the liquidity coverage ratio applied no Level-2 haircuts and no Level-2 caps. CLOSED.** [V-A for the seeded parameters, V-B for the engine]
`backend/app/domain/liquidity/engine.py:577-578` weights every high-quality-liquid-asset fact by a resolved haircut; `_hqla_haircut:472-477` raises rather than defaulting to zero when a consumed tier has no governed rate. `:600-614` resolves both caps and computes the Basel Annex-1 adjustment form, and `:639` applies it. An asset whose tier cannot be established raises `UnclassifiedHqlaError` (`:122-137,459-468`) instead of being counted at face value. No rate is written in the engine; the taxonomy lives in code and the numbers do not (`:57-62`).

The rates are governed parameters seeded by `backend/alembic/versions/202608220034_hqla_haircuts_and_level2_caps.py` from the single catalogue at `backend/app/services/regulatory_parameters.py:596-650`, and they enter the value-based `input_hash` for every tier the book actually consumes (`backend/app/services/regulatory_liquidity.py:1558-1573`, with the same shape in `regulatory_forecasting.py:1567-1573` and `enterprise_stress.py:1487-1505`). Two dedicated suites prove the caps bind (`backend/tests/domain/test_liquidity_hqla_haircuts_and_caps.py:195,236`) and that the parameters are hashed (`backend/tests/services/test_hqla_parameters_in_input_hash.py`).

Two things a reader must not skip. **The Level-2B haircut is seeded at 50%, not 15%** — Basel sets that rate by sub-class (25% for qualifying mortgage-backed securities, 50% for qualifying corporate debt and common equity), the canonical fact model carries only a tier and no sub-class, so the platform applies the most conservative rate in the range, marks the row `pending`, and records the sub-class split as follow-on work (`backend/app/services/regulatory_parameters.py:615-630`). And **every citation on these five rows is Basel, not the local supervisor** — see §7.1(f) and §7.2, because the local supervisor has published no liquidity coverage requirement at all.

**P0-9 — a broken macro scenario produced a passing stress result. CLOSED.** [V-B]
`backend/app/domain/stress/translation.py:506-576` now refuses: an empty `scenario_paths` and any absent required macro variable each raise `NotComputable` with `MISSING_REQUIRED_INPUT` (`:542-555,557-571`), and an unresolvable elasticity register raises with `POLICY_UNRESOLVED` (`:455-469`). The refusal type is defined at `backend/app/domain/authority/outcomes.py:355-386`. The `shocks.get(key, NEUTRAL)` reads in the consumers still exist and are now safe by construction — `translate` omits a key only when it genuinely equals neutral, and fails closed otherwise; `backend/app/domain/stress/projection.py:288-296` documents this explicitly.

**The "22 substitutions replaced" figure.** The remediation register's own inventory is 24 rows, of which 22 were newly closed in this wave and 2 pre-existed. A direct count of typed-refusal raise sites in `backend/app/domain/stress/*.py` finds **21** in the `NotComputable` family, plus one `ProjectionInputError` and nine `ManagementActionError` raises — refusals of a different type, not substitutions. The 22 figure and the 21 measured sites are not the same population and should not be quoted as if they were.

The one site the remediation register flagged as possibly untested — `appendix_ii.py:741-766`, which refuses a bottom-up loss decomposition that does not cover every stress year rather than silently mixing two methodologies inside one filed table — **is covered and does execute**: `backend/tests/domain/test_stress_substitution_fail_closed.py:387` asserts the refusal, and the file runs 23 tests green [M].

**P0-10 — the balance-sheet identity was plugged, never blocked. CLOSED.** [V-B]
`BALANCE_GAP_WARN_FRACTION` no longer exists in `backend/app/`. The official fact plane now runs the control **before** any period or fact is written and raises `ReconciliationBlockedError` (`backend/app/services/fact_derivation.py:367,643-650`) with the stable code `balance_sheet_identity_unreconciled` (`backend/app/services/reconciliation.py:138`). The filing gate `assert_filing_reconciled` (`reconciliation.py:1122-1149`) is wired at sixteen call sites spanning capital, liquidity, foreign exchange, interest-rate risk, funds transfer pricing, the pipeline, package generation, the approval and submission workflow, and signing.

The tolerance is a governed parameter (`balance_identity_tolerance_pct`), seeded at **0.10% of total assets** for both institution classes with status `pending` and the honest source label "AequorOS data-integrity control" (`regulatory_parameters.py:661-678`). A module default of the same value is stamped `source="module_default"` so a filing made under it is distinguishable from one made under a governed value. The live (non-filing) plane may still plug, but the outcome reports `blocks_filing` and the plug is stamped into fact provenance; the old sub-0.5% silence is gone (`fact_derivation.py:1786-1813`). The user-facing half is `backend/dashboard/components/live/UnreconciledBookBanner.tsx`, which states that the ratios below it were computed on a balance sheet forced to add up and are diagnostics rather than positions.

**P0-11 — validation status defaulted to `accepted`. CLOSED.** [V-B]
`backend/app/services/ingestion.py:1266,1582`: the default is now `"pending"`, which sits outside the included-status set, so no engine reads an unvalidated row. Unvalidated rows are counted per entity type and reported on the batch.

The docstring at `ingestion.py:1574-1580` records something worth repeating: the fix was **half true until 2026-08-22**. Only two of fourteen Bank of Ghana form source modules carried the accepted-status predicate, and the shared position resolver did not — so filed returns were reading rows the calculation engines refused. There is now one spelling of that scope, imported by both planes, pinned by `backend/tests/services/test_validation_status_fail_closed.py`.

**P0-12 — the institution-type discriminator silently defaulted to the universal-bank regime. CLOSED.** [V-B]
`backend/app/services/institution_types.py:126-137` raises `InstitutionTypeUnresolved` (`:57-73`) on a blank or unregistered code, with three distinct diagnostics. Every derived accessor — regulatory class, return family, capital regime, exposure limits, liquidity binding, default module set — routes through it. `try_get_type` exists for callers that must degrade and returns `None`, never a substitute. The sentinel constant survives as a registry key and is annotated "no longer a fallback and must never be reintroduced as one" (`:54`).

The module gate now **fails closed**: `backend/app/api/deps.py:424` resolves the type before testing membership, so an unresolved type raises rather than granting the universal-bank superset.

**P0-13 — invented assumptions produced a filed capital-adequacy assessment. PARTIALLY CLOSED.** [V-B]
The unbounded-input half is fixed: all ten plan assumptions in `backend/app/schemas/enterprise_stress.py:33-54` now carry range bounds, and the model forbids extra fields (`:31`).

The seven platform defaults **still exist, unchanged in value**, at `backend/app/services/enterprise_stress.py:235-241`. What changed is disclosure, not refusal, and the code says so: refusing every run without a plan would break the workbench's exploratory use, so each field is instead stamped with its provenance — institution plan, macro scenario, or platform default — plus a written basis, and the stamp is persisted on the immutable run and surfaced as `plan_fully_supplied_by_institution` (`:1253-1256,1274-1347,1901-1905`).

**This is why it is only partial.** `backend/app/services/enterprise_stress_signoff.py:325-380` checks the status transition and that the maker is not the checker; it does **not** read the provenance. A board can still attest an internal capital adequacy assessment whose base case is seven platform constants. The run says so; nothing stops it.

**P0-14 — two validations asserted rather than tested. CLOSED.** [V-B]
The projection-balance check now actually compares per-year asset and funding residuals against a tolerance and returns the offending period labels on failure (`backend/app/services/regulatory_forecasting.py:837,840-899`); the only remaining unconditional pass is the honest fewer-than-two-periods case, which says so. A test greps the source to prove the old sentence is unreachable (`backend/tests/services/test_reporting_validation_honesty.py:355-363`).

The movement rule no longer skips a zero-to-non-zero movement — it emits a warning at the same severity a large swing gets — and the clean-run statement now names how many totals were actually compared and how many could not be (`backend/app/services/regulatory_reporting/validation.py:205-220,255-272`). No blanket all-clear survives.

### 1.3 Board-facing safety signals

**P0-19 — a missing capital floor made every stressed ratio pass. CLOSED.** [V-B]
`backend/dashboard/components/stress/EnterpriseStressWorkbench.tsx:433-436` drops the zero-on-absence fallback and routes both the capital and liquidity floors through `assessAgainstFloor` (`backend/dashboard/lib/api/values.ts:65-76`), which treats only a positive floor as valid; `floorStatus:82-87` returns a warning tone for an unassessed floor and can never return the healthy tone. The display half is `backend/dashboard/components/basel/FloorNotAssessed.tsx`, used on five capital screens.

**P0-20 — "All limits compliant" when nothing was computable. CLOSED.** [V-B]
`backend/dashboard/components/home/BreachBanner.tsx:184-187` computes a three-way verdict. A wholly unassessed tenant renders "Limit compliance not assessed"; a partially assessed one renders "No breach in N of M modules · K not assessed" and names them; the healthy banner is reachable only after both branches (`:264`). Scope is filtered by the institution's module set so an institution's by-design inapplicable modules do not cry wolf. The rule is unit-tested at `backend/dashboard/lib/api/values.test.ts:126-146`.

**P0-21 — hardcoded regulatory floors decided breach versus compliant in the board comparison. CLOSED.** [V-B]
The two literals survive only inside an explanatory comment in `backend/dashboard/components/stress/ScenarioComparison.tsx:11`. Floors now come from the run payload (`:108,116`), an unassessed floor renders as an unshaded cell, and the caption reads "no floor configured" rather than a number. The stressed common-equity column is now null-gated for institution types that do not compute it (`:113,167`), so the "0.00%" artefact is gone.

**P0-22 — the capital bridge understated by a factor of 1,000. CLOSED.** [V-B]
`backend/dashboard/components/stress/charts/DriverWaterfall.tsx:41-44` converts thousands to units before the chart applies its own compaction, and every step value is wrapped. The subtitle no longer hardcodes a rate; it interpolates the run's own capital target and renders an explicit "no capital-adequacy requirement is assumed" card when none is resolvable (`:57-73,118`).

**P0-23 — the null-to-zero coercion across the regulatory user interface. CLOSED.** [V-B]
`backend/dashboard/lib/api/values.ts:34-41` adds a null-preserving variant in which an empty value becomes null while a measured zero survives as zero; `:22-26` documents the original as safe only for non-nullable figures. Both are pinned at `backend/dashboard/lib/api/values.test.ts:36-52`. Adoption was verified at both call sites the prior edition named: the projection chart draws no floor line unless one exists, and the liquidity monitoring view's remaining coercions are each guarded on an explicit null test.

The mechanism that keeps this closed is the more important artefact: `backend/dashboard/lib/api/fail-open-guard.test.ts` is a source scanner over 23 directories with named rules for the hardcoded-floor, zero-on-absence and null-to-zero patterns, plus a floor assertion so it cannot silently scan nothing. It is wired into the dashboard workflow as a blocking step (`.github/workflows/dashboard.yml:93`).

**P0-24 — the snapshot preview promised a unit it did not show. CLOSED on both sides.** [V-B]
Backend: `backend/app/services/regulatory_reporting/generation.py:458-465,468-484,494-509,520-543` normalises both unit vocabularies, infers a section unit from its rows (reporting "mixed" when they disagree), and emits the unit on **every** family's sections and totals, not only the Bank of Ghana forms. Frontend: `backend/dashboard/components/submissions/SnapshotPreview.tsx:169-181` derives the sentence from the payload — the unqualified promise is emitted only when every section declares a unit, otherwise the text states how many did and tells the preparer to confirm against the exported artifact.

### 1.4 Operations and release

**P0-15 — backend continuous integration. See §5.** The diagnosis in the prior edition was incomplete; the workflow has been rewritten but is uncommitted, and no backend test has run in continuous integration since 2026-08-03.

**P0-16 — the worker silently processed zero jobs on a misconfiguration. CLOSED.** [V-A]
`backend/docker-compose.prod.yml:83-88` gives the worker a healthcheck that asserts both that the role can claim a job and that the poll loop wrote a heartbeat inside the stale-job window; the comment above it states the failure mode in full. `backend/alembic/versions/202608220030_worker_heartbeats.py` provides the heartbeat storage, and `backend/app/operator/features/worker_health.py` exposes an authenticated fleet view.

**P0-17 — the readiness endpoint could not detect storage failure. CLOSED.** [V-A]
`backend/app/api/health.py` now calls the real storage probe rather than testing whether environment variables are non-empty, and the container healthcheck targets `/api/health/ready` rather than the liveness route (`backend/docker-compose.prod.yml:53-58`).

The same file records a second finding closed in passing: the readiness response is unauthenticated by necessity, and until 2026-08-22 it published the database **role name**, internal table names, the object-store backend and the live queue depth. The diagnosis now goes to the structured log and the authenticated operator board; the public probe answers only whether each subsystem is ready. Setting _names_ are deliberately still published, because an operator debugging a red probe during a deploy needs to know which settings to supply, and those names are already in `.env.example`.

**P0-18 — a known silently-no-op data migration shipped unfixed. CLOSED.** [V-A]
`backend/alembic/versions/202608220029_verify_bog_return_recode.py` looks for surviving legacy return codes, repairs them under suspended force-RLS, and then **asserts none remain** — so a database already stamped past the original migration is repaired rather than carrying the no-op forever, and a deploy that can do neither fails instead of shipping ambiguous reporting identity. The predicates are return-family-scoped so the official templates, which legitimately own the same codes under a different family, are untouched.

---

## 2. What the remediation did not close

These are open, verified, and carry no fix in the working tree.

### 2.1 Substitutions that remain

- **Swap floating leg priced at 0% on a curve-node miss.** `backend/app/services/regulatory_irr.py:1143,1148` — both branches read the curve with a zero default, and the curve is built only from base-curve shock rows (`:1226-1236`), so a swap midpoint with no seeded node prices the floating leg at zero. [V-A]
- **Foreign-currency cash flows summed at face value.** `backend/app/services/cashflow_forecast.py:359` falls back to the native balance when the reporting-currency attribute is absent, with no currency test anywhere in the file. The equivalent read is correctly guarded in the capital and liquidity modules. [V-A]
- **Foreign-exchange spot implied from the position book.** The 1.0 fallback is gone — `backend/app/services/fact_derivation.py:2735` returns `None` and the caller excludes the currency — but `:2727-2734` still derives an implied rate by dividing the reporting-currency leg by the native leg, and records it with a warning rather than refusing. See §3.2 for what that division does when the two legs disagree. [V-B]
- **Missing swap direction assumed to be pay-fixed, in the engine.** Derivation now excludes a directionless swap, but `backend/app/services/regulatory_irr.py:1130` still reads an absent `direction` key as pay-fixed; only an _unrecognised_ value raises. [V-B]
- **Paid-up capital minimum resolves to zero for a universal bank.** `backend/app/services/enterprise_stress.py:2152` — the board register is consulted, then the specialised-institution branch resolves the governed parameter, and the bank falls through to `return _ZERO`. The specialised path was fixed; the bank path retains the zero, documented as deliberate to preserve golden outputs. A deliberate zero in a capital minimum is still a zero. [V-A]
- **Unmapped loan categories default to a 100% risk weight.** See §3.1 — this is the largest of them and is measured against live data.

### 2.2 Tighten-only enforcement is broader but not complete

The clamp moved into `backend/app/domain/policy/resolver.py:520` and is now applied register-wide through `clamp_overrides` (`backend/app/services/regulatory_parameters.py:1131-1166`) at three sites: capital, forecasting (where the raw re-read is gone), and enterprise stress. The direction register now declares **26** codes (`resolver.py:466-505`). [V-B]

Three paths still read the tenant board register raw and unclamped, and one of them reaches a filed return: `backend/app/services/regulatory_reporting/le_generation.py:1248-1252` (a weakened board liquidity floor goes straight into the filed return), `backend/app/services/capital_plan.py:140-147`, and `backend/app/services/regulatory_ftp.py:1176-1181`.

### 2.3 Regulatory submission channel

`backend/app/services/regulatory_reporting/channels/orass_api.py:231-243` still posts metadata plus a checksum manifest — no artifact bytes are read or uploaded anywhere in the file — and the package then transitions to submitted. New since the prior edition: a real egress guard (`:172-192`), no redirect following (`:205-208`), and a provisional-contract note stamped on every status detail (`:56-60`). Transport-layer-security verification remains operator-disableable (`:197`, default true, no guard against a configured false). [V-B]

**No live filing has been performed and no submission contract has been certified with any supervisor or their technology provider.**

### 2.4 Electronic-signature evidence

Unchanged, and weaker than the prose around it. [V-B]

- The timestamp authority correctly fails closed (`backend/app/services/attestation/tsa.py:126-131`), but the caller swallows that as "a normal state" in two places (`backend/app/services/attestation_api.py:906-908,925-926`), so a signature can be produced with no RFC 3161 token.
- `backend/app/services/attestation/signing.py:369-371` timestamps only if a timestamper exists.
- `signing.py:405-409` still infers the signature method by substring, so a P-384 key is recorded as P-256 — despite an authoritative algorithm map existing at `backend/app/services/attestation/signers.py:98` and being used elsewhere.
- `signers.py:19-23`: the hardware-token backend is "written but never executed"; the key-management-service backend "not built — the stub raises". Both disclaimers are honest and both should be read before any claim about signing hardware.

### 2.5 Append-only coverage

The two tables the prior edition named are now guarded against **UPDATE and DELETE** (`backend/alembic/versions/202608220031_regulatory_event_append_only.py:32-53`), and a third "sealed" tier guards four governance tables against **UPDATE only**, with per-table reasoning for why a stricter trigger would break the product on its first approval (`backend/alembic/versions/202608230038_governance_append_only.py`). Eleven tables are now guarded. [V-B]

**The proof for the first pair had never asserted anything.** `backend/tests/db/test_regulatory_event_append_only.py:84-96` records it verbatim: two timestamp columns are `NOT NULL` with no server default, a raw `INSERT` bypasses the application layer that supplies them, and the resulting violation meant the module errored during setup and never reached an assertion — "the append-only guarantee it exists to prove went unasserted from the day it was written". It now asserts, and requires Postgres but correctly not a restricted role, because triggers fire for superusers.

Residual: `audit_events.organization_id` is still `ondelete="SET NULL"` (`backend/app/models/audit_event.py:23`), which orphans a deleted tenant's audit trail into permanent row-level-security invisibility.

### 2.6 Operator console

- **A single operator can still weaken a client's regulatory threshold.** `console/components/tenants/RemediationPanel.tsx` is byte-identical to the committed version: a five-character note minimum (`:51-56`), a numeric field with no floor, ceiling or sign check (`:407-413`, and the input carries no `min`), and a submit that fires directly with no second approver (`:434-438`). The backend validates numeric-ness and same-day collision only (`backend/app/operator/services/inspector_fix.py:406-466`); it records a real before-and-after in the audit payload but applies no plausibility floor and requires no second approver. [V-B]
- **Adjacent, and genuinely improved:** the governed regulatory-parameter register at `console/lib/regulatory-parameters.ts` keeps decimals as strings, renders absence as "Not set", and makes four-eyes eligibility legible; 27 unit tests cover back-dated effective dates, negative values and self-approval. The approve modal now shows value, confirmation, scope, proposer, source citation and reason, and blanks the numeric on supersede so a stale value cannot be re-approved unchanged. It still does not display the outgoing value beside the incoming one. [V-B]
- **"Read-only" is still false in three places.** `console/components/shell/ImpersonationBanner.tsx:20,55` hardcodes read-only for both modes; `console/components/admin/InspectorView.tsx:262,295` says all inspection is read-only; `console/components/tenants/InspectTenantButton.tsx:149` says a session has no ability to change anything — while the same modal correctly describes break-glass as read/write at `:142-146`. Meanwhile the remediation panel renders in **both** modes (its gate tests only for an active session, not the mode) and `console/components/tenants/OpenBankDashboardButton.tsx:41` mints an act token. [V-B]
- **No multi-factor or step-up authentication anywhere in the console.** A repository-wide sweep of `console/**` for the relevant parameters and terms returns only unrelated hits. The authorize request at `console/app/api/auth/login/route.ts:40-48` sends no `acr_values`, no `prompt=login`, no `max_age`; the password route is a plain POST. Provisioning a tenant, publishing rates to every tenant, and minting an impersonation token remain one authenticated call each. [V-B]

### 2.7 Schema and input validation

- 13 of 64 schema files still declare no `extra="forbid"`, and none inherits a closed base. [V-B]
- `backend/app/schemas/findings.py:41-42,53-56` still permits extra fields and pulls severity out of the extras with only a string check, so an invalid value surfaces as a server error. [V-B]
- `backend/app/schemas/operator.py:676` still accepts an operator-set board threshold as an unbounded number or boolean with no discrimination by kind. [V-B]
- `backend/app/schemas/liquidity_thresholds.py:57,93` still bind only the dictionary size, not the values. [V-B]
- **The password-length finding is withdrawn as stated and reframed.** The one-character minimum is real (`backend/app/schemas/auth.py:20`), but both occurrences are on _login_ requests; `set_password` has no callers in `backend/app/`, and every password the platform sets is machine-generated. There is no user-chosen-password path for a complexity policy to attach to. Adding one is a prerequisite for self-service credentials, not a live defect. [V-B]

### 2.8 Machine learning

Genuinely trained per-tenant models exist. The narrowing the prior edition asked for still applies: `backend/app/ml/behavioral/nmd_duration.py:62` regresses a closed-form function of features 0 and 1 of its own input row — the stability score is computed from the coefficient of variation and the minimum mean, which are exactly those two features (`backend/app/ml/behavioral/features.py:56-60`, `deposit_stability.py:55-57`). It is a formula wearing a gradient-boosted model. [V-B]

The stale-constant test failure is resolved by the two values now agreeing (`backend/tests/ml/test_model.py:24` and `backend/app/ml/config.py:15` both read the same version), not by removing the duplication. The substantive assertion — that the trained model beats its static baseline — passed throughout and stands.

Defensible claim: _trained per-tenant models where an institution's history supports them, with documented deterministic fallbacks otherwise._

---

## 3. Findings established against live data on 2026-08-22

These are new. Each was measured through a read-only session against the primary database, and each traces to code that is currently in the working tree. Institution identifiers are withheld from this public document; they are recorded internally.

### 3.1 The risk-weight vocabulary does not resolve, and the failure is invisible

**Measured [M]:** of 1,023 canonical product rows, **592 carry a risk-weight code and every one of them is a decimal string** — 17 distinct spellings including `0`, `0.00`, `0.0000`, `0.35`, `0.3500`, `0.85`, `1.0` and `1.00`. **222** of those carry a zero. The governed register is keyed on symbolic codes (`RW0`, `RW20`, `RW35`, `RW50`, `RW75`, `RW100`, `RW150`), so **not one of the 592 can be resolved**.

**Why nothing fails.** The engine's resolver is now strict and refuses in both directions — a missing code raises `MISSING_REQUIRED_INPUT` and an unresolvable code raises `POLICY_UNRESOLVED` (`backend/app/domain/capital/engine.py:1008-1039`) — but it never sees these values. Ingestion writes the product-level code (`backend/app/services/ingestion.py:1697`) onto a column the model declares (`backend/app/models/canonical.py:279`); **nothing reads it.** A repository-wide search finds no consumer of that column outside those two sites. The loan book's risk weight is instead derived from the product's regulatory _category_ by `backend/app/services/fact_derivation.py:1276-1291`, and an unmapped category silently becomes `corporate_unrated` at 100% with a warning appended to a list that gates nothing.

**What that costs, measured.** The category census shows **228 products classified as local-currency sovereign** and **76 as non-OECD bank** — neither category appears in the map at `fact_derivation.py:307-317`. Both therefore resolve to unrated-corporate at 100%. At the most recent business date, 17,206 current-generation loan exposures were derived through this path. A sovereign book that the source system marked at 0% is being risk-weighted at 100%.

The direction of the error is conservative — capital is overstated, not understated — which is why it produced no alarm. It is still wrong, it is still silent, and the strict resolver added in this wave does not reach it because the substitution happens upstream of the engine.

**This is the shape of the whole defect class in one finding:** the refusal was added at the boundary that already had the data, not at the boundary where the data goes missing.

### 3.2 Two periods carry a foreign-exchange position that contradicts itself

**Measured [M]:** exactly two reporting periods hold a US-dollar position whose native-currency net and reporting-currency net **disagree in sign**. In one, the native net is negative while the reporting-currency net is positive; in the other, the reverse. Both carry the same spot rate, so no rate movement explains it.

Those two periods are **precisely the only two periods in the database with a succeeded official foreign-exchange run** — 52 succeeded runs, all on those two periods; 133 periods carry position facts. There is no clean period to compare against, and the sign disagreement is not an outlier in a population of correct periods. It is the whole population.

**Why nothing catches it.** The two legs come from different ingested columns and are netted independently: the native net at `backend/app/services/fact_derivation.py:2614`, the reporting-currency net at `:2615`. Nothing compares them. Neither `backend/app/services/ingestion.py`, `backend/app/services/reconciliation.py` nor `backend/app/domain/ingestion/contracts.py` contains a single reference to the reporting-currency balance column — it is written and read, never validated. The long/short side is then derived from the reporting-currency leg alone (`:2654`), so one of these two periods reports a **long** position whose native net is negative.

The same unvalidated pair feeds the implied-spot fallback at `:2729`, which divides one leg by the other. On a sign-contradictory pair that division yields a **negative exchange rate**, and only a warning is emitted.

**Recommendation:** ingestion must assert that the reporting-currency leg agrees in sign with the native leg and is consistent with the ingested spot within a tolerance, and must refuse the row otherwise. This is the same control the balance-sheet identity now has, applied to the currency identity.

### 3.3 A governed withdrawal has orphaned 117 sealed runs

The governed withdrawal capability shipped in this wave (`backend/app/services/canonical_withdrawal.py`) and was exercised once: one applied withdrawal retiring **150,314 position snapshots** for a single source system and business date [M].

Applying the platform's own intersection rule (`backend/app/domain/authority/evidence.py`) to the primary database: **117 succeeded sealed runs across 8 modules** — capital, forecast, funds transfer pricing, foreign exchange, interest-rate risk, liquidity, reverse stress and what-if — plus 15 failed runs, **132 in total**, were sealed on data that has since been withdrawn [M].

The platform handles this correctly and the design is right: a sealed run is never mutated, its standing is **derived on every read** from two records the platform retains immutably, and the refusal lands on the _filing act_ rather than on the evidence (`backend/app/services/withdrawal_impact.py`). A reversal simply stops matching, with no un-stamping of anything.

**Zero packages are affected — because the primary database contains no regulatory packages at all** [M]. That is the honest form of the statement. It is not evidence that the gate held under pressure; the gate has not yet been under pressure. The first package generated for that institution and business date will exercise it.

### 3.4 No package in the Bank of Ghana return family can currently be approved

See §6.3. The chain is structural, not incidental, and it means the return-generation capability is currently unable to complete its own workflow.

---

## 4. Customer-facing claim corrections

Every item here is a claim we have made, or were about to make. The connector table has been substantially corrected since the prior edition; what remains is listed as open.

### 4.1 "Synthetic data" statements — mostly fixed, one instance remains

The two surfaces the prior edition named are fixed: the sidebar now reads "Institution workspace / Tenant-scoped data" (`backend/dashboard/components/shell/Sidebar.tsx:197-201`) and the settings paragraph ends without the disclaimer (`backend/dashboard/app/(app)/settings/page.tsx:586-588`). Neither is env-gated; the strings are simply gone. [V-B]

**Still present, unconditional, and on the board pack:** `backend/dashboard/app/(app)/reports/board-pack/page.tsx:230` renders "Synthetic demonstration dataset; no production bank data." — the same claim, on the document a board reads. Several data-engine surfaces also still name the sample dataset in help text (`TemplatesPanel.tsx:34,36`, `MappingPanel.tsx:68`, `ApiPushGuide.tsx:376`, `lib/api/ingestion.ts:291,293`) and one upload field is seeded with a hardcoded demonstration date (`UploadPanel.tsx:25`). The application's own metadata description still calls it an "interactive prototype" (`app/layout.tsx:32`, with indexing disabled at `:35-37`). [V-B]

Labelling a genuinely synthetic _proxy_ is legitimate and should stay — the discounting-proxy badge and the sandbox submission channel are correctly labelled.

### 4.2 Connector and integration claims

| Claim                                                | State on 2026-08-22                                                                                                                                                                                                                                                                                                                                                                                                                                              | Evidence                                                                                                                                                              |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Marketing site: database and core-banking connectors | **Corrected.** The product page now says Oracle read-only extraction in the standard deployment, with other backends needing a driver-carrying image; the home page says file upload and secure API push are available now and other integrations are enabled only after deployment and vendor onboarding are verified; the company page adds a card stating no institution is in production and no return has been filed with any supervisor.                   | [V-B] `frontend/app/product/page.tsx:77,173`; `frontend/app/page.tsx:133`; `frontend/app/company/page.tsx:22-24,37`                                                   |
| "Encryption in transit and at rest"                  | **Still unqualified. Open.** At-rest key management is off by default (`backend/app/core/config.py:789`), and `backend/.env.example:132-136` states the default provisions without per-tenant keys. The same page honestly disclaims certification, which makes this omission specific rather than systemic.                                                                                                                                                     | [V-B] `frontend/app/product/page.tsx:100`                                                                                                                             |
| Marketing site now under a gate                      | **New.** `frontend/.eslintrc.json` exists and `.github/workflows/web.yml` lints and builds the marketing site and type-checks, tests and builds the console. Its header states plainly that the console is not lint-gated and why — an honest gap statement rather than a fake gate. **Both files are uncommitted.**                                                                                                                                             | [V-A]                                                                                                                                                                 |
| Oracle connectivity                                  | **Defensible.** The driver is a core dependency, not an extra (`backend/pyproject.toml:19`), and the connect path is real — connect timeout, TLS keyword arguments, endpoint failover, wallet materialisation. **But there is still no core-banking-specific mapping**: the product name appears only in labels, one enumeration value and comments; no table or column mapping exists anywhere. Onboarding requires hand-authoring an extraction specification. | [V-B] `backend/app/adapters/database_direct/drivers/oracle.py:170-191`; `backend/app/domain/ingestion/constants.py:17,32,338-339`                                     |
| Temenos T24 connectivity                             | **Still false.** All three transports contain zero network code — no HTTP, socket or URL library is imported by any of them — and each raises a core-unavailable error unconditionally, with no branch. The production job path still wires them alongside a **simulated** session provider.                                                                                                                                                                     | [V-B] `backend/app/adapters/temenos_t24/transports/ofs.py:75-85`, `iris.py:73-84`, `open_api.py:74-85`; `backend/app/services/temenos_jobs.py:34,178`                 |
| Snowflake / SQL Server / JDBC                        | **Still not in the shipped image.** The build runs `uv sync --locked --no-dev --no-install-project` with no database-direct extra, and all four drivers sit under that optional group; the runtime stage copies only the resulting virtual environment. They are lazy-imported, so the failure lands at an institution's first real integration.                                                                                                                 | [V-B] `backend/Dockerfile:20,40`; `backend/pyproject.toml:63-69`                                                                                                      |
| Bloomberg / market-data vendors                      | **Still fixture-only, never connected.** Both vendor catalogues say so in terms: "no market data has ever been pulled with them". The Bloomberg client library is not a dependency — it appears only as a lazy import with an unavailable classification.                                                                                                                                                                                                        | [V-B] `backend/app/adapters/market_data/refinitiv/ric_catalog.yaml:45-46`; `bloomberg/field_catalog.yaml:45-46`; `bloomberg/auth.py:176,185`                          |
| "`httpx` is a development-only dependency"           | **This claim was wrong and is withdrawn.** It is declared in the development group, but it also ships transitively through the standard extra of the web framework, which is a core dependency — and four application modules import it at module scope, so the image could not start without it. The correction does not change the vendor conclusion above, which rests on the catalogues and the absence of the vendor client library.                        | [V-B] `backend/uv.lock:330`; `backend/pyproject.toml:13,74`; module-scope imports in the submission channel, the secrets client and two market-desk modules           |
| "Incremental extraction"                             | **Still not incremental.** The staging call omits the cursor argument the adapter accepts, and the returned cursors are never read back or persisted — the identifier does not appear in the service at all; only a test exercises it. The soft-delete column is declared in configuration and referenced nowhere in application or test code.                                                                                                                   | [V-B] `backend/app/services/database_connections.py:541`; `backend/app/adapters/database_direct/pull.py:45,113`; `backend/app/adapters/database_direct/config.py:242` |

### 4.3 Bank of Ghana return figures

Re-counted directly for this revision. The impressive figures are all exactly right, which is precisely what makes the wrong ones damaging. [M]

| Figure              | Quoted in customer material | Measured 2026-08-22                             | How counted                                                                                            |
| ------------------- | --------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Sheets              | 76                          | **76 ✓**                                        | Sum of sheet counts across the 23 committed layout files                                               |
| Formula cells       | 5,903                       | **5,903 ✓**                                     | Cell-kind tally across the same files                                                                  |
| Input cells         | 4,869                       | **4,869 ✓**                                     | Same pass (labels 3,235; total cells 14,007)                                                           |
| Registered forms    | 23                          | **23 ✓**                                        | Two independent counts agree: 23 layout files, and 23 entries in the form catalogue                    |
| **Workbooks**       | **24**                      | **23 ✗**                                        | 24 files in the source directory; 23 are spreadsheets and the 24th is a word-processed reporting guide |
| BSD2 sheets         | 19                          | **22 ✗**                                        | Sheet count in that form's layout file; the next largest form has 8                                    |
| Return families     | 13                          | **10 families / 39 codes ✗**                    | Tallied from the loaded registry                                                                       |
| **Section layouts** | 52, later corrected to 87   | **168 — and neither earlier figure reproduces** | Summed across all 39 templates                                                                         |

**The section-layout figure needs its split, not just its total.** Of 168 sections, **76 belong to the 23 official-template returns** — exactly one per official sheet — and **92 are hand-authored**. Summing the hand-authored templates in registry order reaches 52 after the first eight and 87 after the first fourteen, so both earlier numbers were counts of the hand-authored bucket taken at two earlier moments. The honest current statement is: **168 section layouts across 39 templates — 92 hand-authored, 76 generated from the official sheets.**

**"24 workbooks" must stop being used.** It is the one figure a counterparty could falsify by counting files. Note also that the source directory is excluded from version control (§0.3), so this count is reproducible only inside the team.

### 4.4 Claims about testing and assurance

- **"Exercised in continuous integration" must not be used.** No backend test has executed in continuous integration since 2026-08-03 (§5).
- **No certification, attestation or regulatory approval may be claimed.** There is none.
- **No live regulatory filing has been made.** The submission channel transmits metadata only (§2.3).

---

## 5. Continuous integration — measured

Every figure here comes from the workflow run logs, read directly. [V-A][M]

| Date       | Branch | Result      | Where it stopped                                                                                                 |
| ---------- | ------ | ----------- | ---------------------------------------------------------------------------------------------------------------- |
| 2026-08-03 | `main` | **success** | Full run. `2010 passed, 46 skipped` in 19m54s, plus both Postgres jobs                                           |
| 2026-08-09 | `main` | failure     | **Typecheck** — one type error in a test file                                                                    |
| 2026-08-10 | `main` | failure     | **Typecheck** — the same error                                                                                   |
| 2026-08-15 | `main` | failure     | **Lint** — 56 errors                                                                                             |
| 2026-08-17 | `main` | failure     | **Lint** — 29 errors                                                                                             |
| 2026-08-21 | `main` | failure     | **Dependency install** — 3,476 recursive task invocations, then `Resource temporarily unavailable (os error 11)` |

**Two corrections to the record.** First, the recursion is real but it is **only** the 2026-08-21 failure. `f33e869` moved `backend/mise.toml` to `backend/docs/mise.toml` (confirmed as a rename by `git show --find-renames`), the root task delegation then resolved to itself, and the run forked until the operating system refused. Every earlier failure was an ordinary red build. Second, the earlier failures mean the honest headline is not "CI broke on 2026-08-21" but **"the backend gate has been red since 2026-08-09 and has not executed a test since 2026-08-03"** — three consecutive causes, each masking the next.

**Current state of the fix.** `backend/mise.toml` is restored and the root task file no longer delegates to any mise task; `dir = "backend"` supplies the working directory directly, and `backend/tests/architecture/test_ci_task_wiring.py` pins the contract (no self-delegation, no nested config redefining a task name, every task a workflow invokes defined in one place). The workflow itself has been rewritten into eleven jobs, pins the mise version rather than floating it, creates a restricted `NOSUPERUSER NOBYPASSRLS` role for the Postgres jobs, and aggregates everything into a single always-reporting gate.

**None of it is committed.** `.github/workflows/risk-service.yml` and `dashboard.yml` are modified but unstaged; `web.yml` and `release.yml` are untracked. The last thing continuous integration actually did was fail.

**Branch protection makes this worse than it looks.** `main` requires exactly one status check — `gitleaks` — with administrator enforcement off (`GET /repos/.../branches/main/protection`, read 2026-08-22) [V-A]. Every backend, dashboard and web gate is therefore **advisory**: a red run does not block a merge, and the auto-merge path waits only on the required checks. Marking the new aggregate gates required is a repository-settings change and has not been made.

---

## 6. What the test suite proves

### 6.1 Size and shape

The backend suite collects **4,831 tests across 390 files** (`pytest --collect-only -q`, 2026-08-22) [M]. The prior edition's run reported 3,237 passed and 304 skipped, so the suite has grown by roughly a third in two days — almost entirely fail-closed and isolation coverage. `tests/db` collects 105.

The workflow's `unit` job is a bare `pytest` and the project configuration sets the test path to the whole tree, so that job **collects the entire backend suite**. The invariant worth pinning is not the total, which moves, but the equality: **files on disk must equal files collected**. A test file that collects nowhere is invisible, and that is precisely the failure mode that let three fixes ship unexecuted (§0.1). The workflow header records the same equality as its own gate inventory (`.github/workflows/risk-service.yml:30-38`).

**Suite execution for this revision: see §6.5.**

### 6.2 What still runs nowhere

- **25 modules gated on a real-database URL** — the regulatory-return, attestation-workspace, authentication and vendor-connection API surface. The workflow now contains a job for them (`.github/workflows/risk-service.yml:596`), but it is conditioned on a repository variable that is unset, and the job comment says so: "Skipped until a reachable primary-database URL is configured." Documented rather than deleted, which is the right call — but still not running.
- **The new equivalence suite** (`backend/tests/equivalence/`) is gated the same way. It is the closest thing the repository has to a golden-output control: it asserts that a package binds the same run the generator's own rule selects, and that every headline figure equals that run's metric exactly, with no tolerance granted. It runs nowhere.
- **No golden output file exists for any generated return** (repository-wide search for `*golden*` under `backend/tests/`: no matches) [M]. The engine suites verify arithmetic against hand-computed values, which is different from pinning a rendered artifact. Systematic drift that preserved the invariants would still ship silently.
- **Resolved 2026-08-30:** the end-to-end browser suite was in no workflow at
  the audit date; it now runs in the blocking `journeys` job described in the
  post-audit update above.
- **Tree-wide format checking** is not a gate anywhere, and the workflow names that explicitly so a future format gate is not a surprise.

### 6.3 The Bank of Ghana return workflow cannot complete

Verified end to end, and **double-locked** [V-A][V-B]:

1. `backend/app/services/regulatory_reporting/bog_forms/generation.py:220` writes `"totals": []` for every form in the family. That builder is the only one for the family (`:282,302`). The empty block is a consequence of a correct decision — the official templates carry their own roll-ups and the platform must never re-implement one — **but the line carries no comment saying so**. The only written rationale in the repository is a test comment that calls it an open defect (`backend/tests/services/bog_forms/test_unvalidated_book_disclosure.py:202-208`).
2. `backend/app/services/regulatory_reporting/validation.py:73-82` iterates a required-block list that includes `totals`, tests it for falsiness — and an empty list is falsy — and appends an **ERROR**-severity completeness finding.
3. `validate_package` computes `passed = error_count == 0` and sets the package status to `validated` only when it passes (`validation.py:320-333`), so such a package never leaves `generated`. `request_approval` refuses on that status alone (`backend/app/services/regulatory_reporting/workflow.py:188-196`), and refuses **again** on the non-zero error count (`:200-207`). The pending-approval state is therefore unreachable, and both routes to `approved` require it (`:266-285,331,363`).

So every form in the family generates, validates as failed, and cannot be approved. **The generator and the validator disagree about what an empty totals block means**, and the disagreement is in neither component — it is in the contract between them. The correct fix is scoped: a form whose roll-ups live in its template declares that, and the completeness rule reads the declaration. The wrong fix is to make the validator lenient about missing totals for every family.

No test asserts a package in this family reaching approval; the completion-proof suite does not touch validation or approval at all (searched for the relevant terms: no occurrences).

### 6.4 What the suite does prove, and this is real

The pure financial mathematics, verified against hand-computed values with the arithmetic shown inline; exact Bank of Ghana template binding; genuine PAdES cryptography with a working tamper detector; vendor payload parsing; job-queue mechanics; and a genuinely well-engineered hermetic environment. Added in this wave: a source-scanning guard against the fail-open user-interface patterns, a tenant-isolation completeness census, an architecture suite that pins the continuous-integration task wiring, and fail-closed suites for the stress translator, the derivation defaults, the reconciliation control and the maker-checker release gate.

### 6.5 Execution result — measured

**The suite was executed for this revision, not quoted.** [M] Hermetic default configuration, working tree, 2026-08-22:

```
4489 passed, 342 skipped, 2810 warnings in 1527.39s (0:25:27)
```

**Zero failures.** Three things follow, and the third is the one that matters.

First, the prior edition's single live failure is gone. That was a stale version constant in the machine-learning suite, not a model defect (§2.8), and the two values now agree.

Second, **4,489 passed plus 342 skipped equals 4,831 — exactly the collection count**, with no collection errors. Files on disk equals files collected equals tests accounted for. That equality is the cheapest available check against the specific failure this wave uncovered: a test file that collects nowhere is invisible, and three fixes shipped unexecuted behind exactly that (§0.1).

Third, and this is the limit: **the run was hermetic — SQLite, with row-level security absent.** The schema under test is built directly from the model metadata, and row-level security exists only in the migrations. So this result proves that 4,489 assertions execute and hold; it does **not** exercise the isolation controls in §1.1, which live in Postgres policies and require the restricted role. Those are proved by `tests/db` under a `NOSUPERUSER NOBYPASSRLS` role, which no continuous-integration run has yet performed (§5).

**What this changes for §1.** Every status there was recorded on the standard §0.1 sets — the implementing code read, its test located. This result adds the thing reading cannot establish: the located tests run and assert, on this tree, today. It remains a local measurement by the audit owner rather than a gated one, and 342 skips are still 342 things that did not run (§6.2 enumerates the classes).

---

## 7. Regulatory source register

The prior edition's §8 stated that **no finding had been verified against supervisory source text**. That gap is now partly closed by `backend/docs/bog_parameter_sources.md`, a per-parameter register that records, for every governed number, the instrument and paragraph it comes from and whether that citation is **confirmed**, **pending**, or **not found**.

The register's own status vocabulary is the important part. A parameter marked `pending` is one the platform applies because a calculation cannot proceed without it, while its supervisory basis is a defensible reading rather than a located instrument. A parameter marked `not found` means no instrument was located at all. `backend/docs/bog_parameter_sources.md` is currently **untracked**, so it is not yet public.

### 7.1 What the register establishes

Each row below was read in the register **and** in the seed catalogue at `backend/app/services/regulatory_parameters.py`. No instrument was consulted directly by this audit; "supported" means the register states it and cites an instrument, not that the audit read that instrument. [V-B]

**(a) The minimum capital adequacy ratio is time-varying — and the platform models it as a single undated scalar.** The register carries a dated series at `bog_parameter_sources.md:176-182`: **13% → 11.5% → 13% → 10% → 13%**, four changes across five regimes, driven by a constant 10% minimum plus a conservation buffer that moved (3.0 → 1.5 → 3.0 → 0 → 3.0). Instruments cited per step: the Capital Requirements Directive ¶71/¶81 with the ¶75 consolidated table for the standing position, a 2020 governor's notice for the pandemic relief, the 2022 Financial Stability Review for the restoration and subsequent removal, and the May 2026 Monetary Policy Report for the current restoration.

The seed catalogue holds **one** row: `regulatory_parameters.py:163-172` seeds `car_min = 13`, citation `"Basel CRD (10% + 3% CCB)"`, status `confirmed`, effective from a single anchor date shared by all 57 seeded rows (`:103,1183`). The storage model **does** support dated series — resolution filters on the effective date (`:800-806`) — and no migration seeds one. So the capability is wired and unused, and the citation string attributes to Basel a number the register attributes to the local directive. The register says exactly this about itself at `:1061-1063,1077`.

**(b), (c), (d) The tier-one, common-equity and leverage minima are local directive numbers, and none of them is seeded at all.** The register attributes tier-one 8.0% to ¶73(b), common-equity 6.5% to ¶73(a) with the ¶75 table, and leverage 6% to ¶90, each `VERIFIED` with the quoted text (`:209-217,223-228`). **None of the three appears in `regulatory_parameters.py`.** They exist only as clamp directions in `backend/app/domain/policy/resolver.py:469-471`, which the register itself flags as "wired but inert" (`:1119-1122`). Two cautions the register adds and this document repeats: the common-equity minimum was reduced to 5.5% during the same relief window as the capital ratio (`:189-192`), and ¶90's wording is permissive ("should be") where ¶71's is mandatory (`:230-235`).

**(e) The credit risk reserve — the citation is in the code, not in the register, and the line is computed nowhere.** The register rates the reserve `SECONDARY` evidence and states the supervisory instrument "was not reached" (`:784-808`), sourcing its treatment to two audited annual reports. The "§2.2.1" citation lives in the code, at `backend/app/domain/stress/appendix_ii.py:274,294`, and names _Guide for Financial Publication BSD/2017 §2.2.1(ii)-(iv) p.10, §2.5 item 5 p.44_, with the June 2018 directive ¶32 omitting the reserve from common-equity capital. The code's characterisation is **excluded from the adjusted capital base**, which is not identical to "deducted".

**It is applied nowhere.** The reserve does not appear in `backend/app/domain/capital/engine.py` or `backend/app/services/regulatory_capital.py` at all; `appendix_ii.py:551` hard-sets it to `None` with the comment "Deliberately unpopulated" and registers a not-computable outcome (`:301-314`). Until 2026-08-21 that line was fed the tier-two recognised general provisions — a different quantity with the opposite capital sign (`:277-286`).

**(f) No liquidity coverage or net stable funding requirement has been published.** The register's rows read `NOT FOUND` for both in the bank regime and `NOT APPLICABLE (VERIFIED)` for the deposit-taking regime (`:475-489`), with an independent corroboration from an April 2026 technical-assistance report stating there are no formal prudential liquidity requirements (`:513-517`). Any 100% coverage floor in use is a Basel number (`:237-241`). The code agrees and says so (`regulatory_parameters.py:594-596`).

**(g) Four directives the platform builds against are exposure drafts, none of them law.** The register records all four as posted 19 February 2026 under exposure-draft titles, with the liquidity directive effective 1 January 2027 (`:60-63,472-474,1132-1138`). **Naming correction:** the abbreviation LMTD in this repository means the **Liquidity Monitoring Tools Directive**, not "Liquidity Management and Treasury Directive" (`:444`; `regulatory_parameters.py:116-118`).

**Open defect:** `regulatory_parameters.py:141-157` seeds all sixteen of that directive's floors with the citation `"LMTD 2026 ¶9"` and status **`confirmed`**, undated — a provisional number presented as a settled floor. The register flags it (`:1090,1128-1131`). This is the same defect class as the capital-ratio citation in (a).

**(h) The deposit-taking licence classes were replaced on 27 January 2026, and the replacement prescribes capital only.** The register cites a January 2026 guideline, effective on issuance, `VERIFIED` (`:289-291`), and records that it prescribes paid-up capital figures per new class and sets no risk-weighting methodology at all (`:296,305-310,391-393`). The register's framing is worth carrying: this is a change of licence identity, not a threshold increase.

**(i) The risk-weighted-asset scope for specialised deposit-taking institutions has not been determined.** Three legs, two supported and one to correct:

- The primary directive **applies to banks only** — its ¶2 scope clause says so, quoted in the register at `:243-246` and restated at `:218,1007`. Supported.
- **No instrument exists** for the deposit-taking class. The register records `NOT FOUND` (`:255`) and documents the searches performed across the directives register, the notices register and the archive index (`:1154-1159`). It additionally warns that the risk-weight schedule the platform currently carries as its default for this class "is unsourced, and its resemblance to an official template is a trap rather than support" (`:410-412`). Supported.
- **The delegation citation does not match.** The code cites Act 930 **s.29(5)** (`backend/app/services/sdi_capital.py:628-631`); the register cites **s.29(3)(b)** (`:265-268`) and contains no reference to s.29(5) at all. One of the two is wrong. **Until that is resolved, neither subsection should be quoted externally.** A paragraph number that cannot be located in the register is not a citation.

### 7.2 The two consequences that are load-bearing

**The liquidity haircuts and caps are Basel numbers filling a local gap, and are not labelled that way where an institution can see them.** The seeded citations are Basel Committee paragraphs; the register states plainly that no local instrument establishes the requirement, and carries a still-open recommendation that these values "should be labelled that way on bank-facing surfaces" (`:1091`). The disclosure exists in the register and on one dashboard screen. It does not exist in the engine comments or on the seeded rows.

**The platform now refuses to mint a capital-adequacy ratio for an institution class whose methodology the supervisor has not defined.** [V-B] An official capital run for that class raises `SdiCapitalPolicyUnresolved` — a 409 carrying `POLICY_UNRESOLVED` (`backend/app/services/sdi_capital.py:209,221,229`) — from `assert_scope_filable:619`, on three branches: the scope is the code default, the scope is governed but marked pending, or a charge percentage within it is pending (`:655-696`). A sibling assertion covers the risk-weight taxonomy (`:698`). The gate is a no-op for a bank (`:726-739`) and is called at both official mint sites, after the reconciliation gate and before execution (`backend/app/services/regulatory_capital.py:310,324`). Filability requires a governed row **and** `confirmation_status == "confirmed"` **and** no pending charges (`:510-529`).

The refusal message is the product, and it is worth quoting: _"Which risk classes this institution's capital adequacy ratio must charge for has not been determined for it. The platform is applying its documented default — credit risk only — which is a placeholder, not a supervisory decision … An official capital run is filing evidence, so it is refused rather than sealed against an undetermined scope."_

The live, indicative view still computes and **labels**: the composition source is stamped `code_default`, a scope note states that the charge covers credit risk only and "must be read as" a narrower measure than a bank's, and the assessment status reads `provisional` while any input is unconfirmed (`sdi_capital.py:300,323-343,510-529`; `backend/app/schemas/sdi.py:144-149,163-164`).

**Refusing to produce a number the supervisor has not defined is the correct behaviour, and it is a product decision as much as an engineering one:** the institution sees no official ratio until the methodology exists. That must be communicated as a deliberate control, not experienced as a missing feature.

---

## 8. Internal-standard compliance

| Standard                                                 | Verdict                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| No seeded institution data                               | **Complies.** The seeding paths named in the standard were deleted in an earlier commit. `backend/scripts/load_bank_history.py:74` now requires an explicit confirmation flag; `backend/scripts/generate_sdi_dataset.py` carries no equivalent guard (search for an environment or confirmation guard: no hits) [V-A].                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Jurisdiction is data, never hardcoded                    | **Now complies.** All six defaults the prior edition named are removed: the parameter mixin and the facts table both declare their jurisdiction and currency columns non-null with no default (`backend/app/models/regulatory.py:157,177`), as do the parameter and core-banking-connection models; a migration drops the surviving server default and a test asserts both the schema rejection and the absence of any default (`backend/tests/test_temenos_currency.py:10,22`); the stress module resolves through the jurisdiction registry. A repository-wide search for the country and currency literals as fallbacks in `backend/app/` returns zero hits. The guard suite widened from 14 to 21 modules and its scan roots from two directories to four (`backend/tests/services/test_jurisdiction_neutrality.py:29-56,62`). [V-B] |
| `input_hash` stays value-based                           | **Complies.** No fact identifier in any snapshot. One drift persists: the liquidity module declares `bank-facts-v3` (`backend/app/services/regulatory_liquidity.py:116`) while capital, foreign exchange, funds transfer pricing, forecasting and interest-rate risk all declare `bank-facts-v2`. [V-A]                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Market data only through the adapter                     | **Unverified in this revision.** The 2026-08-21 edition found it compliant — all four adapters and both publication paths delegating to the single writer — and that finding was not re-read here.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| No regulatory number hardcoded                           | **Materially improved, not complete.** The clamp is register-wide over 26 codes and the universal-bank capital target literal is gone. Three unclamped board-register reads remain, one of which reaches a filed return (§2.2), and the stress elasticity register remains a code-resident table.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Code wins over documentation, then fix the documentation | **Still not being executed for the second half.** The specification that describes the specialised-deposit-taking capability still reads "Status: NOT BUILT" for a capability that shipped, and the untracked architecture document still describes a header-trust authentication model that was removed. Both files are outside version control, so neither is publicly visible — which makes the drift cheaper to ignore and no less misleading to a reader who has them. [V-A]                                                                                                                                                                                                                                                                                                                                                        |

---

## 9. Prioritized remediation

**Immediate.**

1. **Commit the work.** 316 files changed and 464 paths outstanding, including every fix in §1 and the workflow that would prove them. Nothing in this document is protected against loss, and none of it is on `main`.
2. **Get continuous integration green, then make the aggregate gates required.** The workflow rewrite is done; the branch-protection change is not, and without it every gate stays advisory (§5). The hermetic suite passes locally with zero failures (§6.5), so the remaining gap is not a broken suite — it is that **nothing is running it**, and that the Postgres isolation gate has never executed under the restricted role it needs to mean anything (§1.1).
3. **Fix the return-approval chain** so a Bank of Ghana form can complete its workflow — by letting a form declare that its roll-ups live in the template, not by making the completeness rule lenient (§6.3).
4. **Remove the remaining synthetic-data statement from the board pack** (§4.1).
5. **Correct the at-rest encryption claim** on the marketing site — it is the one connector-adjacent claim §4.2 still finds unqualified.
6. **Stop using "24 workbooks" and "87 section layouts"** in customer material; the measured figures are 23 workbooks and 168 section layouts (§4.3).

**Before any institution's data is loaded.**

7. **Validate the currency identity at ingestion** — reject a position whose reporting-currency leg contradicts its native leg (§3.2). This is the same control the balance-sheet identity now has.
8. **Resolve the risk-weight vocabulary** — either map the decimal spellings onto the governed register at ingestion, or refuse them. And close the upstream substitution: an unmapped loan category must refuse, not become unrated corporate at 100% (§3.1).
9. **Clamp the three remaining raw board-register reads**, starting with the one that reaches a filed return (§2.2).
10. **Gate board attestation of an internal capital assessment on whether the plan was actually supplied by the institution** (§1.2, P0-13).

**Regulatory-parameter hygiene — before any figure is shown to a supervisor.**

11. **Reconcile the seed catalogue with the source register (§7.1).** Four defects, all of the same class:
    - the capital minimum is seeded as one undated scalar with a Basel-flavoured citation, while the register carries a five-regime dated series attributed to local instruments;
    - the tier-one, common-equity and leverage minima are **not seeded at all** — they exist only as inert clamp directions;
    - sixteen liquidity floors are seeded `confirmed` from a directive that is an **exposure draft** and does not take effect until 2027;
    - the credit risk reserve is cited in code but computed nowhere, and its line is hard-set to null.
12. **Resolve the delegation-citation conflict.** The code cites one subsection of the banking act; the register cites a different one and does not contain the code's. Until that is settled, neither should be quoted externally (§7.1(i)).
13. **Label the Basel-sourced liquidity haircuts and caps as Basel-sourced** on the surfaces an institution sees, and render a `pending` parameter as pending (§7.2). **Communicate the specialised-institution capital refusal as a deliberate control**, in product copy, rather than letting it read as a missing number.

**Before production go-live.**

14. Multi-factor or step-up authentication on the operator console, and a second approver plus a plausibility floor on the regulatory-threshold write (§2.6).
15. Correct the console's "read-only" copy — three files currently assert it while two adjacent components write and mint act tokens (§2.6).
16. Close the electronic-signature gaps: make the timestamp authority mandatory in code, stop inferring the signature method by substring when an authoritative map exists, and either build or remove the two unbuilt signer backends (§2.4).
17. Transmit the actual artifact through the submission channel, and remove the operator's ability to disable transport-layer-security verification on a credential-bearing connection (§2.3).
18. Run the 25 gated modules and the equivalence suite in continuous integration against Postgres with row-level security on; add a pinned output for at least one return per family (§6.2).
19. **Actually run the restore drill.** Backup, restore, verification and drill tooling now exists (`backend/scripts/backup_database.py`, `restore_database.py`, `restore_drill.py`, `verify_restore.py`, `verify_recovery_target.py`, `dr_manifest.py`), and the drill measures recovery time directly and refuses to run against the production cluster at all. Its tests need a disposable cluster and are therefore in no workflow (`.github/workflows/risk-service.yml:122`). **A drill that has never been executed is a script, not a recovery capability.** Run it, record the measured recovery time, and state the recovery-point objective.
20. Production observability with reconciliation-break and failed-submission alerting. The condition vocabulary now exists (`backend/app/core/observability.py`) and is emitted as structured fields; nothing consumes it yet.
21. Shorten the refresh-token lifetime on its own merits (§1.1, P0-5), and add a password policy before any self-service credential path exists (§2.7).

---

## 10. Honest limits of this revision

- **The remediation is uncommitted.** Every status in §1 describes a working tree. A reader checking out `main` will find the defects, not the fixes.
- **The suite result in §6.5 is hermetic and local.** 4,489 passed with zero failures, but on SQLite with row-level security absent, executed on the audit owner's machine rather than by a gate. It does not exercise the Postgres isolation controls in §1.1, and 342 tests skipped.
- **The `tests/db` figure of 105 passed under a restricted role is reported, not re-executed here.** The collection count is confirmed; the pass count comes from the 2026-08-22 measurement recorded in the workflow.
- **No supervisory instrument was consulted directly by this audit, at any point.** §7 reports what the parameter register states and what the seed catalogue does, and the two were compared against each other. Every "supported" in §7.1 means "the register asserts it and names an instrument", not "the audit read that instrument". Anything in §7 that reaches a filed figure should be confirmed against the instrument itself before it is relied on.
- **The delegation citation in §7.1(i) is contradicted between code and register.** One of them is wrong and this audit did not determine which.
- **The workbook count cannot be checked from the public tree**, because the source directory is excluded from version control (§0.3). The figures derived from the committed layout files can be.
- **The stress-substitution count is reported, not independently enumerated.** §1.2 measures 21 typed-refusal sites against a register that claims 22 closures out of 24; the two are different populations and the reconciliation was not performed.
- **The live-data findings in §3 describe one institution's book.** They are not a general statement about the platform's behaviour on well-formed data; they are a statement about what the platform did with the data it actually has.
- **The stage-readiness judgments in §0.5 and §9 are analytical positions, not measurements.** They were formed by an author who had previously argued this platform should not be blocked from a specific commercial engagement. Review them for motivated reasoning.
