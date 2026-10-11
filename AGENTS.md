# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.
It carries only what nearly every session needs. Implementation detail lives in the
owning documents listed under [Where the detail lives](#where-the-detail-lives).

- **`docs/product.md` is the master product roadmap** (source of truth for build
  sequencing, Phase 0 as-built anchor → Phase 7 enterprise). Sub-docs (rbac.md,
  data_engine.md, ai_engine.md, market_data_adapter.md, regulatory_reporting.md)
  govern domain detail (storage.md and temenos_adapter.md are retired);
  product.md governs order;
  code wins over both. Phase numbers are per-document — cite `doc.md §N Phase X`,
  never a bare "Phase 2".
- **`/docs/` is private by default.** This repository is public; `.gitignore` ignores
  `/docs/**` and publishes a file only by naming it in its allow-list, so adding a
  file there publishes it. `docs/product.md` and `docs/bi.md` are not published.

## Repository map

| Path                         | What it is                                                                                                                             |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/`                   | FastAPI tenant API, calculation engines, Data Engine, background worker (`app/worker.py`), staff operator API (`app/operator/`, :8100) |
| `backend/dashboard/`         | Bank product UI (Next.js) → `bank.aequoros.com`                                                                                        |
| `console/`                   | Staff operator console (Next.js) → `console.aequoros.com`                                                                              |
| `frontend/`                  | Marketing site                                                                                                                         |
| `packages/risk-service-api/` | Generated TypeScript client — the API ⇄ UI contract, never hand-edited                                                                 |
| `deploy/`                    | Coolify compose stacks (OpenBao signing-key custody)                                                                                   |

Full layout: [README.md](README.md#repository-layout). System map and tenancy model:
[ARCHITECTURE.md](ARCHITECTURE.md); coding conventions:
[CODEBASE_CONVENTIONS.md](CODEBASE_CONVENTIONS.md).

Use the [pull request description template](.github/PULL_REQUEST_TEMPLATE.md) for every PR.

## Commands

```bash
mise run risk-service:check            # backend lint, typecheck and hermetic tests
mise run risk-service:test-postgres    # Postgres-gated tests (TEST_DATABASE_URL or an isolated local service)
mise run risk-service:openapi-client   # regenerate packages/risk-service-api after an API change
mise run risk-service:api-fresh        # prove the generated client is fresh (also the pre-push hook)
pnpm --filter @aequoros/dashboard typecheck   # also: lint, test, build, e2e
pnpm --filter @aequoros/console typecheck     # also: lint, test, build
```

`backend/scripts/local_services.py` owns native PostgreSQL 17 / MinIO lifecycle and
per-worktree isolation; the test tasks and dashboard `e2e` use it. Native setup,
overrides and shutdown are documented in
[the dashboard guide](backend/dashboard/README.md#local-services-without-docker-or-orbstack).

Every surface's gates and the CI workflows that enforce them:
[ARCHITECTURE.md §8](ARCHITECTURE.md#8-validation-commands).

## Hard invariants

Each rule is stated in full in the document the index names.

- **No seeded bank financial data — ever.** Every bank financial data point enters
  through the Data Engine (upload, core-banking adapters, API push). Staff provisioning
  creates tenant setup records (organization, bank, administrator, ownership,
  membership, SSO, storage, and required parameter register), but no bank financial
  data. There is no seeding route; never add one to the UI
  or re-add seed CLI scripts. `tests/identity/api/test_banks.py::test_seed_route_is_retired`
  pins it.
- **Institution identity is the platform ID.** `organizations.id` (`OR-…`) and
  `banks.id` (`BK-…`) are the primary key, API path token, `org` claim, RLS GUC
  value and UI identity. Never reintroduce UUID columns or a separate public id for
  either; every other entity keeps UUID primary keys.
- **Code is filed by feature.** The backend is moving to `app/<feature>/` packages that
  cross features only through `public.py` or pure `domain/`.
  `backend/tests/architecture/test_feature_boundaries.py` ratchets the import graph; its
  baseline only shrinks.
- **New backend code is basedpyright strict** (`reportAny` and `reportUnknown*` included).
  Legacy errors are counted in `backend/scripts/type_check_baseline.json`, which only shrinks;
  `mise run risk-service:typecheck` is the gate, not plain `basedpyright`.
- **`app/domain/*` stays pure.** A future corporate entity is a sibling of `banks`
  (`CO-` platform id), never a nullable-heavy `banks` row.
- **Every route that accepts an object id must be in the IDOR census.**
  Follow [the authorization verification contract](backend/docs/authorization_foundation.md#executable-verification)
  for catalogue entries, exclusions and defect quarantine.
  `backend/tests/architecture/test_object_reference_census.py` guards catalogue
  completeness and stale route decisions without Postgres.
  By-id lookups under `/banks/{bank_id}` must be bank-scoped at the query: two banks
  of one organization share an RLS tenant.
- **Authority comes from complete scoped bindings, never scalar roles or token
  claims.** Every role, scope, status or security mutation calls
  `authorization.invalidate_user_authorization` in its transaction; every new
  `RoleBundle` or `ModuleScope` value needs a CHECK-widening migration; gate every
  enforcement cutover with `backend/scripts/authorization_access_impact.py`.
- **Calculation hashes and digests are value-based.** Never put a row id
  (`fact.id`) or a volatile field into an `input_hash` snapshot or an attestation
  digest; the live engine re-derives facts with new UUIDs on every refresh.
- **Migrated financial engines use explicit data-truth types.** Parse boundary values
  into Decimal-backed numeric kinds; figures are `Value`, `Unavailable(reason)` or
  `NotApplicable(reason)`. Keep status handling exhaustive and extend the scoped CI guard
  with each engine migration. [CODEBASE_CONVENTIONS.md §1](CODEBASE_CONVENTIONS.md#data-truth-in-calculations)
  owns the contracts and migration gates.
- **The bank's booked IFRS 9 allowance is the capital figure of record.** Modelled ECL
  (`app/domain/capital/ecl.py`) is a what-if and stress estimate: never substitute it for
  booked general provisions in Tier 2; a stressed increase is a CET1 charge.
- **Periodic reporting dates are the regulator's.** They come from the `ReturnDefinition`
  through `app/services/regulatory_reporting/anchors.py`, never from
  `bank_reporting_periods`, and the snapshot match is exact for every cadence.
- **Read tenant health from what the platform computed** (`live_metrics`,
  `GET /banks/{id}/live-summary|freshness|alerts`); never call `derive_facts`
  yourself. An official-path refusal is the fail-closed design, not a fault.
- **Jurisdiction is data.** Never hardcode country, currency, locale or regulator
  identity in matching logic or display code. `bog_`-prefixed fact categories and
  the `refinitiv` vendor id are load-bearing keys, not leaks — never rename them.
- **Market data has one writer.** Persist only through `pull_runner.execute_pull`,
  read only through `app/services/market_data.py`, keep vendor credentials only in
  `EncryptedDbVault`, and never let a raw vendor error reach a bank-facing surface.
- **Connected bank keys govern object access.** Use `StorageClient` and
  `app/core/key_management`; unavailable connected keys or legacy plaintext refuse
  access. [The bank encryption contract](backend/docs/bank_encryption.md) owns
  optional rollout, onboarding, rotation and recovery catalogues.
- **SSO is AequorOS' own OIDC relying party.** Never reintroduce `AUTH0_*`, and never
  let JIT auto-activate an account.
- **A new job type ships with its enqueue site** and a test asserting the caller
  calls it (`backend/tests/architecture/test_job_enqueue_reachability.py`).
- **Never point mutating tests at the primary database.** The default suite is
  hermetic; Postgres-gated tests use disposable schemas via `TEST_DATABASE_URL`, and
  `backend/tests/live_data` is read-only.
- **Restart long-lived local processes after a code change.** The standalone worker
  has no `--reload` and writes `live_metrics` with whatever code it holds; find
  stale ones with
  `ps -eo pid,lstart,command | grep -E "fastapi dev|uvicorn|app\.main|app\.worker|app\.operator" | grep -v grep`.
- **Coolify deploy compose files** use no dollar-brace variable interpolation (bare
  `:?` build-arg guards excepted) and never bind-mount a repository file.

## Where the detail lives

When a note is implementation detail, add it to the owning document below and, for a
new topic, add a row here.

| Topic                                                          | Document                                                                                                                                 |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Product segments (subdomains) and the staff control plane      | [ARCHITECTURE.md](ARCHITECTURE.md#product-segments-and-the-staff-control-plane)                                                          |
| Institution platform IDs (`BK-`/`OR-`) and the ID epoch        | [ARCHITECTURE.md §2.2](ARCHITECTURE.md#22-institution-identity-is-the-platform-id)                                                       |
| Value-based `input_hash`                                       | [ARCHITECTURE.md §3](ARCHITECTURE.md#3-the-calculation-run-pattern-reuse-this-for-every-new-engine)                                      |
| Live engine, worker, job reclaim windows, tenant health        | [ARCHITECTURE.md §3b](ARCHITECTURE.md#live-engine-operating-rules)                                                                       |
| Market data adapters, vendor credentials, research desk        | [ARCHITECTURE.md §3c](ARCHITECTURE.md#market-data-standing-rules-and-the-research-desk)                                                  |
| BI plane                                                       | [ARCHITECTURE.md §3e](ARCHITECTURE.md#bi-working-rules)                                                                                  |
| Generated API client: regeneration and serializer hazards      | [ARCHITECTURE.md §6](ARCHITECTURE.md#generated-client-hazards)                                                                           |
| CI enforcement and E2E                                         | [ARCHITECTURE.md §8](ARCHITECTURE.md#ci-enforcement)                                                                                     |
| Liquidity, stress and capital extensions (product.md §Phase 2) | [ARCHITECTURE.md](ARCHITECTURE.md#liquidity-stress-and-capital-extensions)                                                               |
| Governed forecast assumptions                                  | [ARCHITECTURE.md](ARCHITECTURE.md#governed-forecast-assumptions)                                                                         |
| Authorization foundation, ownership, grants, filing authority  | [backend/docs/authorization_foundation.md](backend/docs/authorization_foundation.md#standing-rules-at-a-glance)                          |
| Integration keys as bank-scoped machine principals             | [backend/docs/integration_key_machine_principal_rollout.md](backend/docs/integration_key_machine_principal_rollout.md#standing-contract) |
| ICAAP workspace and filing                                     | [backend/docs/icaap_workspace_and_filing.md](backend/docs/icaap_workspace_and_filing.md#standing-rules-at-a-glance)                      |
| Reporting dates and anchor windows                             | [docs/regulatory_reporting.md §5b](docs/regulatory_reporting.md#5b-reporting-date-standing-rules)                                        |
| Official BoG BSD returns from the templates                    | [docs/bog_returns/00_full_return_registry.md §6](docs/bog_returns/00_full_return_registry.md#6-as-built-engine-rules)                    |
| SSO (own OIDC relying party)                                   | [docs/rbac.md §11.3](docs/rbac.md#as-built-the-oidc-relying-party)                                                                       |
| Attestation and e-signature                                    | [docs/attestation_esignature.md](docs/attestation_esignature.md#attestation-standing-rules)                                              |
| No seeded bank data                                            | [docs/data_engine.md](docs/data_engine.md#standing-order-no-seeded-bank-data)                                                            |
| Jurisdiction is data                                           | [CODEBASE_CONVENTIONS.md §4](CODEBASE_CONVENTIONS.md#4-jurisdiction-is-data)                                                             |
| Feature layout and the boundary ratchet                        | [CODEBASE_CONVENTIONS.md §5](CODEBASE_CONVENTIONS.md#5-feature-layout)                                                                   |
| Strict typing and the type-check baseline                      | [CODEBASE_CONVENTIONS.md §1](CODEBASE_CONVENTIONS.md#type-check-baseline)                                                                |
| Financial kinds, explicit result states and migration guard    | [CODEBASE_CONVENTIONS.md §1](CODEBASE_CONVENTIONS.md#data-truth-in-calculations)                                                         |
| Stale local processes                                          | [backend/README.md](backend/README.md#stale-local-processes)                                                                             |
| Test databases, the primary database, live-data suite          | [backend/README.md](backend/README.md#test-databases-and-the-primary-database)                                                           |
| Legacy case vertical (`/api/v1/cases`)                         | [backend/AGENTS.md](backend/AGENTS.md#legacy-case-vertical)                                                                              |
| TLS enforcement and bank-reviewable transport evidence         | [backend/docs/transport_security.md](backend/docs/transport_security.md)                                                                 |
| Bank-held keys, onboarding, rotation and backup recovery       | [backend/docs/bank_encryption.md](backend/docs/bank_encryption.md)                                                                       |
| Audit chains, filed-input seals and compliance retention      | [backend/docs/tamper_evident_records.md](backend/docs/tamper_evident_records.md)                                                           |
| Coolify deployment rules                                       | [deploy/README.md](deploy/README.md#coolify-compose-rules)                                                                               |
| Host change to `bank.aequoros.com`                             | [backend/dashboard/README.md](backend/dashboard/README.md#deploy-to-bankaequoroscom)                                                     |

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
