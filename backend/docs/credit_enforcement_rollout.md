# Credit scoped-binding enforcement rollout

Credit enforcement replaces scalar role checks with independently complete
authorization bindings on the `credit` module. It ships in two releases, and
this one document is the contract for both:

- **Phase 1 (enforced now, 2026-09-22):** `Module.CREDIT` exists, a mirror
  migration widens the database to match it, and the credit rows of the SHARED
  live surfaces are gated the way liquidity, IRRBB, FX and FTP rows already were.
- **Phase 4 (enforced in code, 2026-09-27):** the direct `/credit/*` routes have
  moved from the scalar `Tenant` / `MutationTenant` gates onto per-route scoped
  bindings, and the loan blotter, its facets and the activity grid apply the
  reader's `data_scope`. The code, the tests and the grants are described below.
  **Nothing is deployed until the release record at the end of the Phase 4 section
  is attached**, and two migrations (`202609270073`, `202609270074`) must be
  applied before the gate script can even run — see the deployment order there.

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

Shared-feed whole-institution enforcement, including the Capital and rating
policies and reconciliation visibility, follows
[the foundation contract](authorization_foundation.md#whole-institution-figures-and-credit-only-narrowing).

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
  "reason_category": "<structured reason category>",
  "reason_detail": "<institution-approved reason>",
  "expected_authority_sentence": "<server preview response>"
}
```

The reason fields follow the
[structured reason contract](authorization_foundation.md#structured-grant-reasons).
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

## Phase 4: direct credit route cutover (enforced in code 2026-09-27)

> **Status: enforced in code, not yet released.** Every `/credit/*` route now runs
> on a named `_require_institution_permission` dependency over `Module.CREDIT`,
> and the three row-level surfaces apply the reader's data scope. The scalar
> `Tenant` / `MutationTenant` gates are gone from this router. `require_module_access("credit")`
> stays, because per-tenant entitlement is a different question from authority.

### What was wrong

Before this release, the entire direct credit surface was served to any
authenticated tenant user:

| Route                            | Gate BEFORE                                  | What that admitted                                                                                           |
| -------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| every `GET /credit/*`            | `Tenant` + `require_module_access("credit")` | Any authenticated user of the tenant — including the loan blotter, which returns `counterparty_name` per row |
| `POST /credit/run-all-scenarios` | `MutationTenant` + the same entitlement      | Any token carrying the scalar `analyst` role or higher; it mints immutable `RegulatoryRun` rows              |

`require_module_access` is a per-tenant CONFIGURATION gate: it asks whether this
institution's licence class is entitled to the module, never whether this person
holds a credit sentence for this institution. And the blotter's `total` counted
the whole institution's book, so `branch` was a client's suggestion rather than a
boundary.

### The authority table (enforced)

Sensitivity is declared from what the RESPONSE discloses, per route, not once for
the module. Two orthogonal axes are decided per route: the permission tuple, and
whether the surface refuses a narrowed data scope or applies it.

| Route                                                   | Module / sensitivity / permission | Data scope        | Dependency                          |
| ------------------------------------------------------- | --------------------------------- | ----------------- | ----------------------------------- |
| `GET /api/v1/banks/{bank_id}/credit/dashboard`          | CREDIT / `aggregated` / `view`    | whole institution | `require_credit_aggregated_view`    |
| `GET /api/v1/banks/{bank_id}/credit/migration`          | CREDIT / `aggregated` / `view`    | whole institution | `require_credit_aggregated_view`    |
| `GET /api/v1/banks/{bank_id}/credit/vintages`           | CREDIT / `aggregated` / `view`    | whole institution | `require_credit_aggregated_view`    |
| `GET /api/v1/banks/{bank_id}/credit/pd`                 | CREDIT / `aggregated` / `view`    | whole institution | `require_credit_aggregated_view`    |
| `GET /api/v1/banks/{bank_id}/credit/concentration`      | CREDIT / `restricted` / `view`    | whole institution | `require_credit_concentration_view` |
| `GET /api/v1/banks/{bank_id}/credit/loans`              | CREDIT / `restricted` / `view`    | **applied**       | `require_credit_blotter_view`       |
| `GET /api/v1/banks/{bank_id}/credit/loans/facets`       | CREDIT / `restricted` / `view`    | **applied**       | `require_credit_blotter_view`       |
| `GET /api/v1/banks/{bank_id}/credit/activity`           | CREDIT / `confidential` / `view`  | **applied**       | `require_credit_activity_view`      |
| `POST /api/v1/banks/{bank_id}/credit/run-all-scenarios` | CREDIT / `confidential` / `run`   | whole institution | `require_credit_run`                |

The reasoning, route by route:

- **Dashboard, migration, vintages, PD — `aggregated`.** None of them names a
  counterparty or lists a facility: the dashboard is grade buckets, one NPL ratio
  and the resolved prudential limit; migration is a transition matrix and roll
  rates; vintages are cohort PAR30+ curves; PD is a pooled hazard. Aggregate
  figures are `aggregated`.
- **Concentration — `restricted`, and it resolves Phase 4 open decision 1.** The
  `single_name` dimension buckets on `cp:<counterparty name>` /
  `group:<reference>` (`credit_concentration._group_key`) and the `breaches` list
  carries those keys, so the payload names obligors. The platform's own member
  rule says a single obligor is `restricted`; the older route line said
  `aggregated`. **Decided: `restricted`** — the narrower of the two, taken because
  the alternative (projecting `single_name` buckets and single-name breaches out
  for aggregated viewers) changes the numbers on a supervisory screen, which is a
  product decision and not one an authorization cutover may make silently. The
  consequence is stated plainly: **a reader holding only CREDIT/`aggregated`
  loses the concentration monitor** and needs the blotter sentence for it. If the
  institution wants an obligor-free aggregated monitor, that is a catalogue change
  to design, not a sensitivity to widen.
- **Blotter and facets — `restricted`.** The rows carry `counterparty_name`. The
  facets are the blotter's own filter counts over the same rows, so they carry the
  same sensitivity: a facet at a lower sensitivity would be a count of names.
- **Activity — `confidential`, resolving Phase 4 open decision 2.** It is a
  record-level grid — one row per restructure, write-off and recovery, with
  `source_reference`, `position_source_reference` and the amount — and it carries
  no counterparty name. The member rule makes a record-level grid without an
  obligor identity `confidential`. **Decided: `confidential`, not folded into the
  blotter's `restricted`.** Sensitivity is exact-or-`all`, never a ladder, so this
  is a deliberate third sentence rather than a convenience: neither the blotter
  reader nor the dashboard reader gets activity, and the activity reader gets
  neither of the others. That is the cost of naming the disclosure honestly.
- **`run-all-scenarios` — `run`, not `view`.** It mints `RegulatoryRun` rows — the
  provenance a filed credit figure cites. `run` is the permission the other
  engines' run gates use and only the `analyst` bundle carries it, so a Viewer,
  Auditor or Approver cannot seal a baseline.

### Why four surfaces refuse a narrowed data scope

`require_whole_institution` on a dependency is a REFUSAL, not a narrowing, and it
answers 403 with `institution_grain_requires_whole_institution` — the same reason
string the BI plane uses for its institution-grain measures, so one telemetry
query covers both.

The rule is: **a route that presents a figure as the institution's requires a
binding covering the whole institution.** The dashboard measures its NPL ratio
against the resolved prudential ceiling and the concentration monitor measures
each bucket against a Board limit expressed as a share of the institution's
capital; a branch slice compared to an institution limit is a wrong number with a
right-looking name. Migration, vintage and PD rates carry no statement in their
payload of the population they were computed over, so a silently sliced curve
would read as the institution's. And a sealed run over one branch would be an
immutable filing record claiming to be the institution's book.

The honest alternative — computing each of these over the branch subset and
DISCLOSING the population in the payload — is a product decision about what a
branch-level credit dashboard is, with its own limit set. It is deliberately not
attempted here; refusing is the deny-by-default answer and it leaves the decision
open rather than pre-empting it with a plausible-looking number.

The [whole-institution contract](authorization_foundation.md#whole-institution-figures-and-credit-only-narrowing)
owns the shared gates and the requirements for opting out. Credit's row-filtered
surfaces are described below.

### How the scope is applied where it IS applied

- The scope is the reduction of the bindings that MATCHED the allowing decision
  (`authorization.effective_data_scope` over `decision.matching_binding_ids`),
  re-read from the database inside the same request. The caller's id list is a
  selector, never the grant.
- `ResolvedDataScope.branch_codes is None` is the ONLY value meaning "do not
  filter". Every narrowed kind carries a concrete set, and an EMPTY set is a
  legitimate answer that yields no rows — the two are different types so a reader
  cannot fall from "no codes" into "no filter".
- **A row stating no branch is outside every narrowed scope.** It is not
  attributable to a granted branch, so serving it would be the fail-open.
- **Region resolves through `business_units`, never `bi_dim_branch`.** The credit
  module is in the calculation plane and must not read `bi_*`. The
  `business_units` register is the only place a branch's region is declared (that
  schema's module docstring is explicit that no other source exists and that
  parsing an address would invent a board figure), and field names go through its
  `normalise_row` alias seam. Matching is exact after whitespace stripping — the
  same comparison the BI branch dimension stores, so one grant means one code set
  in both planes; casefolding would be WIDER than the grant.
- **A region no unit in the register belongs to yields no rows and says so.** The
  response's `data_scope.unresolved_regions` names it, because zero rows for a
  mis-typed or not-yet-declared region is a grant to correct, not a book to
  report, and the two must not look the same.
- **Every count is over the scoped set.** `total`, `filtered`, each facet tally
  and the activity grid's `disbursement_count` / `repayment_count` /
  `monthly_flows` are taken AFTER the filter. A total that counts rows the reader
  cannot see discloses the size of the book outside their scope as surely as the
  rows would; the `branches` facet would additionally enumerate every branch the
  institution has.
- **The client's `branch` filter intersects with the scope, never replaces it.** A
  request for an out-of-scope branch answers an empty page. It answers
  BYTE-IDENTICALLY to a request for a branch that does not exist, so a refusal
  never confirms that a branch is real.
- **The response discloses the scope.** `CreditLoansPageRead`,
  `CreditLoanFacetsRead` and `CreditActivityRead` each carry a
  `CreditDataScopeRead`. Without it, a scoped `total` reads as the institution's
  book and an empty page reads as "this bank has no loans".

#### Activity attribution, and its one real limitation

A loan event carries no branch. It is attributed through the facility it names by
its OWN `(source_system, position_source_reference)` — the identity D-018 fixes —
and then through that facility's latest computed position **on or before the event
date**, which is where the facility was when the event happened rather than where
it is now. That matters: a facility written off eight months ago has no current
position, and attributing its write-off to nothing would understate a branch's
losses.

Two events are therefore in NO narrowed scope, by design:

1. an event naming a facility from a different source system (never a
   cross-system guess on the bare reference); and
2. an event that predates the institution's earliest computed book — which
   includes every event before a bank's first ingested book, and the first
   month's events for a bank that ingests only month-end books.

Both are deny-by-default and both are invisible to the whole-institution reader,
who still sees every event. Case 2 is a genuine limitation of a branch- or
region-scoped activity view on a short history, not a fixture artefact; it is
pinned by `test_an_event_predating_the_first_computed_book_is_in_no_branch_scope`.
Closing it would mean attributing an event to a LATER snapshot's branch, which
can name a branch the facility only moved to afterwards — a leak — so it is left
refused rather than guessed.

### Who loses access at the cutover, and why that is correct

Measured read-only against the primary deployment on 2026-09-27
(`.ai/bi_test_results/p4c_10_credit_route_inventory.txt`; that deployment is at
`202609210066`, so neither the Phase 1 mirror `202609220067` nor the Phase 4
migrations are applied there yet). 12 active principal × institution pairs:

| Population                                                                 | Pairs | Before                                     | After              | Why correct                                                                                                           |
| -------------------------------------------------------------------------- | ----- | ------------------------------------------ | ------------------ | --------------------------------------------------------------------------------------------------------------------- |
| Holds an `all` / `all` operational row (scalar `admin` / `analyst`)        | 2     | every credit route                         | every credit route | `all` covers `credit` and every sensitivity; unchanged                                                                |
| Holds an `all` / `all` row but not `analyst` (`account_admin` scalar role) | 4     | every read; run already refused (see note) | every read         | unchanged                                                                                                             |
| `account_admin` with only an Account-plane row                             | 2     | every credit read                          | **nothing**        | the defect being fixed: administering the account is not reading the loan book, and the blotter returns obligor names |
| Service identities (`auth_provider = 'service'`)                           | 4     | nothing                                    | nothing            | integration keys are confined to API Push at the auth boundary and never reached a credit route                       |

Two facts worth stating precisely:

- **Nobody loses `run-all-scenarios`.** The old gate needed the scalar `analyst`
  rank or higher; `account_admin` is not in that ladder, so of the 8 human pairs
  only the 2 with an `all` row could seal a baseline before, and both still can.
  The route is nonetheless materially stricter: the 2 who keep it now keep it
  because they hold an `analyst` CREDIT-or-`all` `confidential` binding, not
  because of a role claim in a token.
- **No tenant on this deployment holds a `credit`-scoped binding at all** (the
  active distribution is in the same output). Every principal who keeps credit
  access does so through an `all` / `all` row. The 2 who lose it are the
  unowned-tenant compatibility population of `202609090051`, which was given
  organization-wide ACCOUNT/restricted `account_admin` authority and deliberately
  no product view.

**Nothing is backfilled.** The Phase 1 mirror (`202609220067`) remains the only
system-granted credit authority, and it grants `aggregated`-capable rows by
copying whatever sensitivity the source `risk` row carried — it creates no
`restricted` and no `confidential` row and no `analyst` bundle. So the blotter,
the concentration monitor, the activity grid and the run route each require a
grant an Org Owner makes deliberately. Backfilling them would re-encode exactly
the defect this cutover fixes: it would hand obligor names to everyone who had
them only because no route ever asked.

**The pre-cutover population stays queryable**, because no `users` row and no
binding row changed: the inventory SQL above reproduces it at any time.

### How an Org Owner grants what is needed

Access → Members, one indivisible sentence per row. Sensitivity is mandatory and
institution coverage is exact or explicitly organization-wide
(`app/features/manage_authorization.py`).

| Need                                              | `role_bundle`                                              | `module_scope` | `sensitivity_scope` | `data_scope_kind`           |
| ------------------------------------------------- | ---------------------------------------------------------- | -------------- | ------------------- | --------------------------- |
| Credit dashboard, migration, vintages, PD         | `viewer`, `auditor`, `analyst`, `approver`, or `validator` | `credit`       | `aggregated`        | `all` (required)            |
| Concentration monitor                             | the same set                                               | `credit`       | `restricted`        | `all` (required)            |
| Loan blotter and its facets, whole institution    | the same set                                               | `credit`       | `restricted`        | `all`                       |
| Loan blotter and its facets, one or more branches | the same set                                               | `credit`       | `restricted`        | `branch` + branch codes     |
| Loan blotter and its facets, one or more regions  | the same set                                               | `credit`       | `restricted`        | `region` + region names     |
| Loan activity grid                                | the same set                                               | `credit`       | `confidential`      | `all`, `branch` or `region` |
| Seal the credit baseline                          | `analyst`                                                  | `credit`       | `confidential`      | `all` (required)            |

An `analyst` CREDIT/`confidential` row grants `view`, `create`, `edit`, `run`,
`validate` and `export` at `confidential` ONLY: it opens the activity grid and the
run route and neither the aggregated dashboard nor the restricted blotter. The
least-privilege credit analyst who needs all four surfaces therefore holds three
independently complete rows (`aggregated`, `restricted`, `confidential`). Do not
combine fields across rows, and do not widen to `all` to save a row.

A branch or region MUST now go in `data_scope_kind` / `data_scope_values`, never in
the reason text. Before `202609270073` there was nowhere to put it and the Phase 1
section of this document said so; there is now.

### Executable verification

| Property                                                                                       | Pinned by                                                                                                                   |
| ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Each route names its dependency and carries no scalar gate                                     | `tests/architecture/test_credit_route_authorization.py`                                                                     |
| **Every** `/credit/` route is in the authority table (a new one cannot ship ungated)           | the same file, `test_every_credit_route_is_in_the_authority_table`                                                          |
| `require_credit_run` is a recognized mutation gate (impersonation boundary)                    | the same file                                                                                                               |
| The correct sentence is admitted, per route                                                    | `tests/api/test_credit_route_authorization.py`                                                                              |
| A scalar role alone is refused, per route, for five roles                                      | the same file, `test_a_scalar_role_alone_is_refused`                                                                        |
| A `risk`, `liquidity` or `capital` binding is refused, per route                               | the same file                                                                                                               |
| Every OTHER sensitivity is refused, per route; `all` admits every route                        | the same file                                                                                                               |
| A sibling institution's sentence does not reach this one, and vice versa                       | the same file (both directions, 403)                                                                                        |
| A bank of another tenant is 404, even with an organization-wide sentence                       | the same file (plus the real cross-tenant case)                                                                             |
| Institution-grain routes refuse a `branch` and a `region` scope                                | the same file, `test_an_institution_figure_refuses_a_narrowed_scope`                                                        |
| A branch-scoped reader sees only their branch, and `total` is scoped                           | the same file                                                                                                               |
| An out-of-scope `branch` answers the intersection, byte-identically to a nonexistent one       | the same file                                                                                                               |
| Pagination, facet counts and activity counts run over the scoped set                           | the same file                                                                                                               |
| A region resolves through the register, follows it forward, and discloses an unresolved region | the same file                                                                                                               |
| `mixed` unions branches and regions; one `all` row beside a branch row wins                    | the same file                                                                                                               |
| Activity attribution follows D-018 and refuses a cross-system or pre-book event                | the same file                                                                                                               |
| The blotter's pre-existing filter, date and refusal contract is unchanged                      | `tests/services/test_regulatory_credit.py`, `tests/api/test_regulatory_credit.py`                                           |
| Object references under `/credit/*` are catalogued and refuse a foreign object                 | `tests/fixtures/object_reference_routes.py` (auto-discovered) + `tests/api/test_authorization_object_reference_coverage.py` |

### Deployment order and release record (Phase 4)

The order is forced, because the gate script cannot run before the migration:

1. Apply `202609220067` (the Phase 1 mirror) if the target is still behind it, then
   `202609270073` and `202609270074`. Until `202609270073` is applied,
   `scripts/authorization_access_impact.py` fails with
   `column authorization_bindings.data_scope_kind does not exist` — the projection
   selects the whole binding row.
2. Run `scripts/authorization_access_impact.py` and keep the dated output.
3. Run the route-level inventory (§Who loses access) BEFORE and AFTER. This is the
   surface-specific evidence alongside the projection: keep the access-impact
   script's `--json` output to retain every view capability's module, sensitivity
   and data scope. Its compact table folds whole-institution capabilities together.
   Both outputs belong in the record.
4. Attach the exact list of principals who lose each credit surface.
5. Attach every institution-approved CREDIT row created before release, per the
   table above.
6. Confirm that every principal whose `authv` changed signed in again.
7. Regenerate the OpenAPI client (`mise run risk-service:openapi-client`) — the
   three scoped payloads gained `data_scope`, so the generated package is stale
   until it is regenerated, and the dashboard's credit surfaces should surface the
   scope rather than silently show a scoped total as the institution's.

### Official Credit execution authority

Direct Credit batches, data activation with `run_calculations=true`, requested
and scheduled official runs require whole-institution CREDIT/confidential `run`.
Activation and enqueue preflight the planned Credit engine before deriving facts
or creating jobs; the worker rechecks current authority before reading its period.
Scheduled runs select an actor with the same whole-institution Credit authority.
The Credit batch service independently enforces it before reading inputs or
persisting a run, including callers through `data_activation.run_official_modules`.
Branch- and region-limited Credit grants cannot satisfy any of these gates.
Derivation-only activation remains unchanged. No grants are backfilled or widened.

### Still open after Phase 4 (named, not fixed)

1. **Credit export.** Only the `analyst` bundle carries `export`, so a
   record-level blotter CSV would need CREDIT/`restricted` `export` and would be
   unavailable to Viewers, Auditors and Approvers without a bundle change. There is
   no credit export route today; decide before adding one.
2. **The credit registers** (`manage_credit_params.py`: thresholds, concentration
   limits, classification grids) stay on `Tenant` reads and `ApproverTenant`
   writes. They are recorded here so the omission does not read as an oversight.
3. **Branch-level credit figures.** §Why four surfaces refuse a narrowed data
   scope explains why a sliced dashboard, migration matrix, vintage curve or PD is
   refused rather than computed. If a bank wants them, they need their own limit
   set and their own payload statement of the population.
