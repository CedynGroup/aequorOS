# Credit scoped-binding enforcement rollout

Credit enforcement replaces scalar role checks with independently complete
authorization bindings on the `credit` module. It ships in two releases, and
this one document is the contract for both:

- **Phase 1 (enforced now, 2026-09-22):** `Module.CREDIT` exists, a mirror
  migration widens the database to match it, and the credit rows of the SHARED
  live surfaces are gated the way liquidity, IRRBB, FX and FTP rows already were.
- **Phase 4 (NOT YET ENFORCED):** the direct `/credit/*` routes move from the
  scalar `Tenant` / `MutationTenant` gates onto scoped bindings. The route table
  below is the decision record for that cutover; nothing in it is live until the
  Phase 4 release record is attached.

Run the inventory against each target deployment immediately before each
release. Store the dated output with the deployment record. Do not copy
production identities into this repository.

## Why credit needed its own module

Credit had no authorization module of its own. The dashboard mapped the credit
navigation entry onto the `risk` module (`CAPABILITY_MODULES.risk` included
`credit`), but no backend route ever evaluated a `risk` binding — the credit
dashboard, blotter (counterparty names included), concentration monitor,
migration, vintages and PD were served to any tenant reader, and the run route
to any scalar analyst. Meanwhile the shared live surfaces filtered liquidity,
IRRBB, FX and FTP rows by binding and served credit (and capital) rows to
everyone (decision D-017). `Module.CREDIT` gives credit the same vocabulary as
every other engine; `risk` stays in the enum for whatever it was meant to become.

## Phase 1: module, mirror, shared-surface gating (enforced)

### Vocabulary

- `Module.CREDIT = "credit"` and `ModuleScope.CREDIT = "credit"` in
  `app/core/authorization.py`, placed after `CAPITAL` (the live-engine order).
- `credit` is an institution module: `_INSTITUTION_MODULES` and the cutover
  gate's `INSTITUTION_MODULES` both derive from the enum, so the effective-
  authority projection, `/banks` coverage and `scripts/authorization_access_impact.py`
  all carry it without a hand edit.
- Members composer label: **Credit**. A grant reads, for example,
  "Ama Mensah is a Viewer in Credit for Sample Bank, covering Aggregated data."
- `ModuleScope.ALL` rows already cover credit; they are neither duplicated nor
  rewritten.

### Mirror migration `202609220067`

The migration (owned by the models/migrations lane; this section states what
it does, read from `alembic/versions/202609220067_credit_module.py`, so the two
cannot disagree):

1. Rewrites `ck_authorization_bindings_module_scope` to the literal list that
   equals `tuple(ModuleScope)` — the hermetic schema derives the CHECK from the
   enum and already accepts `credit`; a migrated database rejects it until this
   runs (the same shape as the validator bundle constraint, `202609200065`).
   The BEFORE list is written out so the downgrade restores the historical shape.
2. Copies every ACTIVE, HUMAN, not-yet-expired `risk` binding
   (`valid_until IS NULL OR valid_until > now`) to an otherwise identical
   `credit` row: same principal, bundle, institution scope and id, sensitivity,
   `valid_from`/`valid_until`; `granted_by_type = 'system'` with a reason naming
   the migration and the source row id. Skipped: machine principals (binding
   `principal_type` and `users.auth_provider = 'service'`), the Account-plane
   bundles (`member`, `account_admin`, `org_owner`), and any principal ALREADY
   COVERED by an equivalent `credit` or `all` row — same bundle, same or
   organization-wide institution coverage, same or `all` sensitivity. **Coverage
   rule:** the covering row must itself be active and unexpired; an expired
   `all` or `credit` row does not mask the mirror.
3. In the same transaction, bumps `authorization_version` for every principal
   holding a `credit` row granted by this migration, revokes their open
   refresh-token families (`revoked_reason = 'authorization_changed'`), and
   writes one `authorization.binding_granted` audit event per new row with the
   scope and a system authority sentence.
4. Runs every statement inside `force_rls_suspended` naming FOUR FORCE-RLS
   tables — `authorization_bindings`, `users`, `refresh_tokens` and
   `audit_events` — with the per-organization GUC set for each tenant in turn
   (precedent `202609160053`). It verifies NO row counts: `_mirror_organization`
   and `_invalidate_mirrored_principals` discard `rowcount`. What prevents the
   silent zero-row no-op of a tenant-scoped Alembic role is
   `force_rls_suspended` itself, which raises `RlsBlindError` naming
   `WORKER_DATABASE_URL` when the role can neither bypass RLS nor lift FORCE
   (`app/db/session.py`). The before/after
   `authorization_access_impact.py` diff in the release record is the count
   check; the migration does not perform one.

Because no backend route ever consumed a `risk` binding, the mirror preserves
UI visibility for the people who held one; it transfers no authority that
existed on the server. A tenant that never granted `risk` gains no `credit`
rows and its readers lose the credit cards on the shared surfaces until an Org
Owner grants the sentence below — that is the visible cutover diff, and it is
the intended one.

### Affected surfaces (enforced)

| Surface                                                                                      | Required complete binding      | Behaviour without it                                              |
| -------------------------------------------------------------------------------------------- | ------------------------------ | ----------------------------------------------------------------- |
| `GET /api/v1/banks/{bank_id}/live-summary` — the `credit` live-metric row                    | CREDIT / `aggregated` / `view` | Row omitted; other modules unchanged                              |
| `GET /api/v1/banks/{bank_id}/alerts` — `credit` findings                                     | CREDIT / `aggregated` / `view` | Filtered in SQL before `total`, `by_module`, `by_severity`, limit |
| `GET /api/v1/banks/{bank_id}/analytics/window` — `credit` daily aggregates (`npl_ratio_pct`) | CREDIT / `aggregated` / `view` | Credit snapshots excluded before aggregation                      |
| `GET /api/v1/banks/{bank_id}/live-snapshots?module=credit`                                   | CREDIT / `aggregated` / `view` | 403                                                               |

The gate is the same one the other engines use: `scoped_authorization.evaluate_bank_permission`
per engine with `Permission.VIEW`, `Sensitivity.AGGREGATED`, evaluated against
the resolved bank. Each surface names its gate list in one tuple
(`_GATED_ENGINE_MODULES` in `app/services/live_view.py`, `alerts.py`,
`window_analytics.py`); `tests/architecture/test_credit_module.py` pins that
`("credit", Module.CREDIT)` is in every one of them.

**Capital is deliberately still ungated on these surfaces.** Its live-metric
row, findings and `car_pct` daily aggregate are served to every tenant reader,
exactly as before this release. Gating it is the capital contract's decision,
not a side effect of this one, and the existing capital, FX, IRRBB and FTP
regression tests use capital as the always-visible control row.

Sensitivity is exact: a CREDIT/`confidential` or CREDIT/`restricted` row (the
Phase 4 blotter sentence) does NOT unlock the aggregated shared feeds. Add the
aggregated row when the institution confirms dashboard access. A `risk` row
grants nothing on any credit surface.

Impersonated examiners and machine principals are denied on every gated row,
as on every other scoped-binding surface (`evaluate_bank_permission` returns
`None` for a non-human context).

### Dashboard access (Phase 1)

The effective-authority projection now emits `credit` capabilities. The
dashboard's `CAPABILITY_MODULES` is typed against the generated `Module` enum,
so the client regeneration (`mise run risk-service:openapi-client`) and the
`credit` entry in `CAPABILITY_MODULES`, `MODULE_ENTRY_REQUIREMENTS`
("Credit · Aggregated · View") and the composer's `MODULE_OPTIONS` ship
together with this backend change (`dashboard/lib/modules.ts`). The dashboard
admits the credit module through EITHER view: `CAPABILITY_MODULES.credit`
(the new sentence) or `CAPABILITY_MODULES.risk`, which deliberately keeps
`credit` so a tenant that has not yet run `202609220067` and holds only `risk`
rows does not lose the module from its navigation.

**`GET /api/v1/banks/{bank_id}/live-snapshots?module=credit` now returns 403
for a reader without CREDIT/`aggregated` `view`.** The Command Center pulse
requests one ladder per VISIBLE live module, and visibility is the either/or
above, so there is a window in which the two disagree: between deploying this
backend and running `202609220067`, a reader whose credit tile is visible only
through a `risk` grant will see that 403 in the browser console for the credit
ladder (and no credit row on live summary, alerts or window analytics). This
is the server denying correctly, not a security defect — the `risk` row never
was credit authority — and the mirror closes it by giving that reader the
`credit` row. Run the migration in the same deployment as this code (release
record item 5); after it, a reader holds the `credit` row or sees no credit
tile, and the console stays clean.

### Inventory (Phase 1)

Run `scripts/authorization_access_impact.py` BEFORE applying the migration and
AFTER it, and diff the `product view` column. It projects every active human
through the same evaluator the API uses; the `credit` module appears in the
per-institution module list automatically. Expect `no_product_view` to stay
unchanged (the shared surfaces are not a module page) and the module list to
gain `credit` exactly for principals holding an `all` row or a mirrored `risk`
row:

```
cd backend && uv run python scripts/authorization_access_impact.py --organization OR-XXXXXXXX
```

The script has no hermetic mode; it takes a database URL (`WORKER_DATABASE_URL`
then `DATABASE_URL`) and is run by the release operator against the target
deployment only. Its hermetic coverage is `tests/scripts/test_authorization_access_impact.py`.

The read-only SQL below is the deployment inventory for the shared surfaces.
Run it as a role that can see every tenant user, institution and binding. It
enumerates human AND machine principals without inferring any grant from
scalar roles.

```sql
WITH active_principals AS (
    SELECT
        u.organization_id,
        u.id AS principal_user_id,
        CASE
            WHEN u.auth_provider = 'service' THEN 'machine'
            ELSE 'human'
        END AS principal_type,
        u.email,
        u.auth_provider,
        u.role AS scalar_role
    FROM users AS u
    WHERE u.is_active IS TRUE
),
targets AS (
    SELECT
        p.*,
        b.id AS institution_id,
        b.name AS institution_name
    FROM active_principals AS p
    JOIN banks AS b ON b.organization_id = p.organization_id
),
active_bindings AS (
    SELECT *
    FROM authorization_bindings
    WHERE status = 'active'
      AND revoked_at IS NULL
      AND valid_from <= CURRENT_TIMESTAMP
      AND (valid_until IS NULL OR valid_until > CURRENT_TIMESTAMP)
),
authority AS (
    SELECT
        t.organization_id,
        t.institution_id,
        t.institution_name,
        t.principal_user_id,
        t.principal_type,
        t.email,
        t.auth_provider,
        t.scalar_role,
        COALESCE(bool_or(
            a.principal_type = t.principal_type
            AND a.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver', 'validator')
            AND a.module_scope IN ('credit', 'all')
            AND a.sensitivity_scope IN ('aggregated', 'all')
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS credit_aggregated_view,
        COALESCE(bool_or(
            a.principal_type = t.principal_type
            AND a.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver', 'validator')
            AND a.module_scope = 'risk'
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS held_risk_row
    FROM targets AS t
    LEFT JOIN active_bindings AS a
      ON a.organization_id = t.organization_id
     AND a.principal_user_id = t.principal_user_id
    GROUP BY
        t.organization_id,
        t.institution_id,
        t.institution_name,
        t.principal_user_id,
        t.principal_type,
        t.email,
        t.auth_provider,
        t.scalar_role
)
SELECT
    *,
    CASE
        WHEN principal_type = 'machine' THEN
            'denied: shared live surfaces require a human binding'
        WHEN credit_aggregated_view THEN
            'allowed: credit rows on live summary, alerts, window, snapshots'
        WHEN held_risk_row THEN
            'denied until the mirror runs: risk row present, no credit row'
        ELSE
            'denied: no active exact CREDIT binding'
    END AS credit_shared_result
FROM authority
ORDER BY organization_id, institution_id, principal_type, email;
```

Run it before the migration (expect `held_risk_row` without `credit_aggregated_view`
for mirrored principals) and after (expect the two columns to agree for every
mirrored principal). Machine principals are listed so the release record proves
no integration was silently assumed to read credit.

### Exact binding rows (Phase 1)

Create no backfill beyond the mirror. Operators create only institution-approved
rows through the authorization service so `authv` advances and refresh-token
families are revoked in the same transaction.

| Need                                                      | `principal_type` | `role_bundle`                                              | `institution_scope`                      | `institution_id`                             | `module_scope` | `sensitivity_scope` |
| --------------------------------------------------------- | ---------------- | ---------------------------------------------------------- | ---------------------------------------- | -------------------------------------------- | -------------- | ------------------- |
| Credit rows on live summary, alerts, window and snapshots | `human`          | `viewer`, `auditor`, `analyst`, `approver`, or `validator` | `institution` or explicit `organization` | exact `BK-*` or `NULL` for organization-wide | `credit`       | `aggregated`        |

```json
{
  "principal_user_id": "<confirmed human user UUID>",
  "role_bundle": "viewer",
  "institution_scope": "institution",
  "institution_id": "<exact BK-*>",
  "module_scope": "credit",
  "sensitivity_scope": "aggregated",
  "reason": "<institution-approved reason>",
  "expected_authority_sentence": "<server preview response>"
}
```

Do not widen to `all` to compensate for a missing decision, and do not encode a
branch, region or portfolio in the reason as if it were enforced scope — data
scope is a later, separately migrated dimension.

### Release record (Phase 1)

Before deployment, attach:

1. The dated `authorization_access_impact.py` output BEFORE and AFTER the
   migration, for every organization, with the `product view` diff.
2. The dated inventory SQL output before and after, for every institution.
3. The exact list of human and machine principals who will lose credit rows on
   the shared surfaces (those with neither an `all` row nor a mirrored `risk` row).
4. Every institution-approved CREDIT/`aggregated` row created before release.
5. Confirmation that migration `202609220067`, the client regeneration and the
   dashboard mapping all shipped in the same deployment as this backend — the
   `risk`-only reader's console 403 on the credit ladder (§Dashboard access)
   lasts exactly as long as the gap between the backend deploy and the migration.
6. Confirmation that every mirrored user (whose `authv` changed) signed in again.

## Phase 4: direct credit route cutover (NOT YET ENFORCED)

> **Status: not enforced.** Every `/credit/*` route still runs on `Tenant`
> (reads) and `MutationTenant` (run) plus the `require_module_access("credit")`
> entitlement gate. This section records the sentences the cutover will require
> so the grants can be inventoried and created before the enforcing release.

### Affected surfaces (planned)

Sensitivity is declared per catalogue member, not per route (decision D-028):
any member that exposes a single obligor is `restricted`; record-level grids are
`confidential`; every aggregate is `aggregated`. Applied to today's routes:

| Route                                                   | Module / sensitivity / permission | Basis                                                                   |
| ------------------------------------------------------- | --------------------------------- | ----------------------------------------------------------------------- |
| `POST /api/v1/banks/{bank_id}/credit/run-all-scenarios` | CREDIT / `confidential` / `run`   | D-028 names `run`; `confidential` matches every other engine's run gate |
| `GET /api/v1/banks/{bank_id}/credit/dashboard`          | CREDIT / `aggregated` / `view`    | D-028                                                                   |
| `GET /api/v1/banks/{bank_id}/credit/loans`              | CREDIT / `restricted` / `view`    | D-028 — the blotter carries `counterparty_name`                         |
| `GET /api/v1/banks/{bank_id}/credit/loans/facets`       | CREDIT / `restricted` / `view`    | D-028 — facets are the blotter's own filter counts                      |
| `GET /api/v1/banks/{bank_id}/credit/concentration`      | CREDIT / `aggregated` / `view`    | D-028 — **see open decision 1**                                         |
| `GET /api/v1/banks/{bank_id}/credit/activity`           | **undecided**                     | Not in D-028 — **see open decision 2**                                  |
| `GET /api/v1/banks/{bank_id}/credit/migration`          | CREDIT / `aggregated` / `view`    | D-028                                                                   |
| `GET /api/v1/banks/{bank_id}/credit/vintages`           | CREDIT / `aggregated` / `view`    | D-028                                                                   |
| `GET /api/v1/banks/{bank_id}/credit/pd`                 | CREDIT / `aggregated` / `view`    | D-028 (advisory designation is unchanged by authorization)              |

The cutover replaces the scalar dependencies with `_require_institution_permission`
dependencies on `Module.CREDIT` (named `require_credit_aggregated_view`,
`require_credit_restricted_view`, `require_credit_run`, registered in
`MUTATION_ROLE_DEPENDENCY_NAMES`), keeps `require_module_access("credit")`
(entitlement is orthogonal to authority), and moves `_get_bank_or_404` in
`app/services/regulatory_credit.py` onto the `scoped_authorization.resolve_bank`
404 path so a sibling-tenant bank stays hidden before any permission check. A
denied request must start no engine, create no run and write no audit event.
Impersonated examiners and machine principals are denied on every credit route,
consistent with every other scoped-binding module (D-026).

An architecture pin (`tests/architecture/test_credit_authorization.py`, pattern
`test_fx_authorization.py`) must assert each route's named dependency and the
absence of `get_mutation_tenant_context` and `require_role_*`. Route behaviour
lives in `tests/api/test_credit_authorization.py`, which already holds the
Phase 1 shared-surface matrix.

The credit registers (`manage_credit_params.py`: thresholds, concentration
limits, classification grids — `Tenant` reads, `ApproverTenant` writes) are NOT
in this table. They stay on the scalar gates in Phase 4 unless a decision moves
them; recording that here is what stops the omission from reading as an oversight.

### Open decisions before Phase 4 (do not guess)

1. **Concentration exposes single obligors.** The `single_name` dimension
   buckets on `cp:<counterparty name>` / `group:<reference>` and the `breaches`
   list carries those keys. D-028's route line says `aggregated`; D-028's own
   member rule says a single obligor is `restricted`. Either the route becomes
   CREDIT/`restricted`, or the `single_name` buckets and single-name breaches
   are projected out for `aggregated` viewers (the way FX projects its charge
   out of enterprise results). The same keys reach the ALERTS feed as
   `concentration_limit_single_name` finding messages ("cp:<name> is above its
   Board concentration limit"), which Phase 1 now serves at CREDIT/`aggregated`
   — strictly narrower than the everyone-can-read state before, but still a
   name at aggregated sensitivity. Decide once, per member, in the catalogue.
2. **Activity is record-level without names.** `/credit/activity` lists
   restructures, write-offs and recoveries per facility (`source_reference`,
   `position_source_reference`, amounts) with no counterparty name. By the
   D-028 rule a record-level grid is `confidential`; D-028 did not name the
   route. Confirm `confidential` or fold it into the blotter's `restricted`.
3. **Official runs and activation.** FX, FTP and IRRBB require their
   `confidential`/`run` sentence on `POST /official-runs` and on data activation
   with calculations when `module_scope.runs_module` includes them. Whether the
   credit engine's official run acquires the same requirement (and therefore
   whether the scheduler's actor selection must also hold CREDIT/`confidential`/`run`)
   is undecided. Capital does not carry it today either.
4. **Summary-vs-record export.** Only the `analyst` bundle carries `export`.
   Record-level credit export (blotter CSV) would require CREDIT/`restricted`
   `export`, which Viewers, Auditors and Approvers cannot hold without a bundle
   change. Do not widen a bundle silently; decide and record it here.

### Dashboard access (planned)

Credit navigation and deep links follow the effective-authority projection: an
entitled institution without CREDIT/`aggregated` `view` shows the link disabled
with a tooltip naming the grant; the blotter tab additionally requires
CREDIT/`restricted` `view` and hides itself otherwise; the run action requires
CREDIT/`confidential` `run`. Command Center, Risk and Board Pack request credit
data only when the aggregated capability is present.

### Exact binding rows (planned)

| Need                                                     | `principal_type` | `role_bundle`                                              | `institution_scope`                      | `institution_id`       | `module_scope` | `sensitivity_scope` |
| -------------------------------------------------------- | ---------------- | ---------------------------------------------------------- | ---------------------------------------- | ---------------------- | -------------- | ------------------- |
| Credit dashboard, concentration, migration, vintages, PD | `human`          | `viewer`, `auditor`, `analyst`, `approver`, or `validator` | `institution` or explicit `organization` | exact `BK-*` or `NULL` | `credit`       | `aggregated`        |
| Loan blotter and its facets (counterparty names)         | `human`          | `viewer`, `auditor`, `analyst`, `approver`, or `validator` | `institution` or explicit `organization` | exact `BK-*` or `NULL` | `credit`       | `restricted`        |
| Run the credit scenario batch                            | `human`          | `analyst`                                                  | `institution` or explicit `organization` | exact `BK-*` or `NULL` | `credit`       | `confidential`      |

An Analyst CREDIT/`confidential` row grants `view`, `create`, `edit` and `run`
at `confidential` only; it does not grant the aggregated dashboard or the
restricted blotter. The common least-privilege credit analyst therefore holds
three independently complete rows. Do not combine fields across rows.

### Inventory and release record (planned)

Repeat the Phase 1 gate: `authorization_access_impact.py` before and after,
plus the inventory SQL extended with `credit_restricted_view` and
`credit_confidential_run` columns on the same pattern. Attach the dated
outputs, the exact denied-principal list per credit surface (human and
machine), every institution-approved row, and the recorded answers to the four
open decisions above. Nothing is backfilled at the route cutover: the mirror in
Phase 1 is the only system-granted credit authority.
