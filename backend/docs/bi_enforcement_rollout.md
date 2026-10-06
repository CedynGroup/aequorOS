# BI scoped-binding enforcement rollout

**ENFORCED IN CODE, OFF IN EVERY DEPLOYMENT.** As of this commit the six read
routes exist and are mounted (`app/features/read_bi.py`,
`app/api/router.py`), the `require_bi_read` dependency is registered in
`deps.MUTATION_ROLE_DEPENDENCY_NAMES`, and every one of them is gated by
`BI_ENABLED`, which defaults to `False` and is set in no deployment — with the
flag off each route answers 404, as if it did not exist. So nothing in this
document changes what any principal can reach TODAY; it becomes live the moment
a deployment sets `BI_ENABLED=1`.

`app/services/bi/authorization.py::authorize_query` is the decision every BI
surface consults (`tests/api/test_bi_authorization_matrix.py`,
`tests/services/bi/test_bi_authorization.py`), and every data route calls it
before it reads anything (`tests/api/test_bi_routes.py`).

Run the inventory against each target deployment immediately before the release
that TURNS THE FLAG ON — not before the commit that mounts the routes, which
grants nobody anything. Store the dated output with the deployment record. Do not
copy production identities into this repository.

## Why BI is shaped differently from every other rollout

Every enforcement contract before this one names a sentence per ROUTE:
`GET /fx/dashboard` requires FX/`aggregated`/`view`, and the dependency is
declared on the handler. BI cannot work that way. One `POST .../bi/query` can
read twenty-five measures over twelve dimensions drawn from six modules at three
sensitivities, and which ones is decided by the client's body, not the URL. So
the sentence is declared on the CATALOGUE — every member carries its own
`module` and `sensitivity` (decision D-028) — and the route requires every
sentence the submitted query needs.

Three consequences the operator must understand before granting anything:

1. **A filter is a read.** "Total exposure WHERE counterparty name = 'X'" reveals
   that obligor as surely as grouping by the name would, so filters, the Top-N
   dimension, the pivot axis, sort keys and every member a measure is COMPOSED
   from (a ratio's numerator and denominator, a weighted average's weight, a
   concentration measure's `over` dimension, each level of a hierarchy) are
   authorized exactly like a requested measure.
2. **Sensitivity is exact, never a ladder.** A CREDIT/`restricted` sentence does
   not serve a CREDIT/`aggregated` measure, and vice versa. A principal who is
   to see the loan blotter AND the NPL trend needs two independently complete
   rows. This is the evaluator's existing rule (`app/core/authorization.py`),
   not a BI rule.
3. **One denied member denies the whole query.** There is no partial result and
   no silent column drop: the response is 403 naming the member ids that caused
   it, with no rows. Nothing is served from a query the principal is not
   completely authorized for.

## Affected surfaces

The wave-3 surfaces are bank-scoped under `/api/v1/banks/{bank_id}/bi/…` and
mounted with `BANK_ROUTE_DEPENDENCIES`, so a `BK-*` belonging to another tenant
is 404 before any authorization runs (the bank-route existence rule,
`docs/rbac.md` §4).

| Surface                      | Permission                                    | Module / sensitivity required                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ---------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET …/bi/catalogue`         | `view`                                        | None of its own, and no tenant data: the response is the platform's own metadata, filtered member by member through the same decision the query path makes, so a principal is never offered a member they cannot query. A principal holding NO pair receives an empty dictionary (200) with a `withheld_members` COUNT — never the withheld ids                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `POST …/bi/query`            | `view`                                        | Every distinct `(module, sensitivity)` pair the submitted query touches                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `POST …/bi/grid`             | `view`                                        | Same, at the record grain — these queries reach `confidential` members                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `POST …/bi/drill`            | `view`                                        | Same, plus every level the drill path descends into                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `POST …/bi/explain`          | `view`                                        | Same as the query it explains                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `GET …/bi/trust`             | —                                             | **REMOVED BY FOUNDER DECISION 2026-09-29.** BI carries no reconciliation verdict against the regulatory returns (`docs/bi.md` §Founder decision 2026-09-29); the route, the `trust` badge on every BI payload and `read_bi.CHECK_DISCLOSURES` go with it. The row is kept because its history is real: until removal the route required every pair the reported checks disclosed (`credit`/`aggregated` + `liq`/`aggregated` + `risk`/`aggregated`) because the payload was figures — R2's `lhs` was the institution's total loans, R3's its total deposits, R9's its total assets and funding — and audit A6-01 found it authorizing nothing and serving another institution's totals. That defect was fixed before the route was removed. When the code lands, re-derive the read-route count in this document's header from `read_bi.py` (six at HEAD `884e3797`) |
| `POST …/bi/export` (Phase 2) | `view` for summary, `export` for record-level | The member's own module and sensitivity                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |

No BI surface infers authority from `users.role`, the token's `roles[]`, a
different module, a different sensitivity, or two partial bindings combined.

### The pairs the catalogue declares today

Catalogue version 2.1.0 — 1,525 members (1,456 measures, 69 dimensions, 13
hierarchies) over thirteen pairs, counted from `catalogue()` on 2026-09-29. The
measure count is dominated by target variants: 174 certified engine measures,
48 portfolio base measures, and 1,234 `.actual` / `.target` / `.variance` /
`.variance_pct` / `.attainment_pct` variants, each inheriting its base's pair.
A grant covers a pair, so this table is what an institution is actually choosing
between.

| Module scope | Sensitivity scope | Members | Entitlement slug | Examples                                                                              |
| ------------ | ----------------- | ------- | ---------------- | ------------------------------------------------------------------------------------- |
| `credit`     | `aggregated`      | 562     | `credit`         | `loans.npl_ratio_pct`, `loans.arrears_share_pct`, `engine.npl_ratio_pct.crd.official` |
| `credit`     | `confidential`    | 2       | `credit`         | `loan.employer`, `event.position_id`                                                  |
| `credit`     | `restricted`      | 5       | `credit`         | `counterparty.name`, `counterparty.group`, `loans.largest_single_name_share_pct`      |
| `risk`       | `aggregated`      | 129     | `risk`           | `positions.balance_rc`, `branch.region`, `time.date`, `gl.branch_ytd_rc`              |
| `risk`       | `confidential`    | 2       | `risk`           | `position.id`, `position.source_reference`                                            |
| `risk`       | `restricted`      | 1       | `risk`           | `position.officer_code` (Phase 5 optional field)                                      |
| `cap`        | `aggregated`      | 200     | `capital`        | `engine.car_pct.crd.official`                                                         |
| `liq`        | `aggregated`      | 175     | `liquidity`      | `engine.lcr_pct.crd.official`, `deposits.balance_rc`                                  |
| `irrbb`      | `aggregated`      | 123     | `irrbb`          | `engine.eve_base_ghs.crd.official`                                                    |
| `markets`    | `aggregated`      | 120     | `markets`        | `engine.pit_pd_point_pct.advisory_internal.official`                                  |
| `fcst`       | `aggregated`      | 96      | `forecasting`    | `engine.year5_car_pct.advisory_internal.official`                                     |
| `ftp`        | `aggregated`      | 60      | `ftp`            | `engine.portfolio_nim_pct.advisory_internal.official`                                 |
| `fx`         | `aggregated`      | 50      | `fx`             | `engine.nop_ghs.crd.official`                                                         |

Two notes on this table. **`restricted` is exactly the members that name a single
person** — the counterparty id, name, source reference and group, the
largest-single-name share (a ratio you can invert into one exposure) and, since
Phase 5, the officer code. It is the smallest set the spec's own rule allows and
it must not be widened by moving a name into `confidential` to make a screen
easier to reach.
**Designation is not authority** (D-022): an `advisory_only` or
`supervisory_monitoring` engine measure is authorized exactly like a filed one
and is merely badged differently in the UI. A grant never turns an advisory
figure into a certified one.

### Entitlement is separate from the grant

Before any binding is evaluated, the member's module must be in the
institution's licence-class module set
(`institution_types.get_type(db, bank).default_modules`) — the same gate
`require_module_access` applies to the FX, FTP, IRRBB, credit, forecasting and
behavioral routers. A savings-&-loans institution is not entitled to `fx` or
`ftp`, so an FX measure is refused for it **even under an organization-wide
`all`/`all` binding**, with reason `module_not_entitled`. Do not grant around
this: it is a licence question, not an access question. An institution whose
licence class does not resolve at all fails closed with 409, exactly as every
other regime-selecting surface does.

### What the route layer does (all four landed in this commit)

The authorization decision is complete on its own; these four are the handler's,
and each is now implemented and pinned by `tests/api/test_bi_routes.py` /
`tests/api/test_bi_query_log.py`:

1. **404 before 403.** Resolve the institution through `resolve_tenant_bank` /
   `scoped_authorization.resolve_bank` so another tenant's `BK-*` is "not found"
   and never reaches a permission check. A bank of THIS tenant that the principal
   holds no coverage for is a 403 from `authorize_query` (matrix row
   `institution-exact-target-does-not-cover-a-sibling`).
2. **Write `bi_query_log`** from `BiAuthorization.member_ids`, `denied_members`
   and the decision, for allowed AND denied queries, committed before the
   response and never swallowed (a read that cannot be recorded is not served).
   The log stores member ids and a one-way query hash, never a filter value for a
   restricted member. A request refused as malformed is recorded too, naming no
   member, so the read budget bounds that path as well (audit A6-06). Every
   surface now writes its own row: the `surface` CHECK (widened by migrations
   `202609270070` and `202609280077`) names `query`, `grid`, `drill`,
   `explain`, `export`, `feed`, `trust`, `catalogue`, `packs`, `insights` and
   `nlq`, so `catalogue` — which at Phase 1 could not be recorded without
   misnaming it — is metered and logged like the data surfaces. `trust` names
   the surface REMOVED BY FOUNDER DECISION on 2026-09-29: no new row may carry
   it. The value stays in the CHECK on purpose — migration `202609290080`
   (working tree at the time of writing) declines to narrow an append-only
   log's vocabulary, because the trust reads that happened are evidence.
3. **ETag** = hash(query, build fingerprint, principal id,
   `matching_binding_ids`, `authv`). A revoked or re-scoped grant bumps `authv`
   in the same transaction, so old tokens 401 and cached responses miss.
4. **Deny machine and impersonated credentials at the dependency too.** The
   decision already refuses both (D-026); keeping the route out of
   `IMPERSONATION_READ_ONLY_ROUTES` is what makes the mandatory query-log write
   consistent with the operator view's "persists nothing" promise.

## Dashboard access

BI navigation and deep links follow the server's effective-authority projection,
like every other module: the `bi` module key appears for an institution when the
principal holds at least one structurally eligible BI capability, and a denied
deep link renders the dashboard's not-found view without issuing a BI query.

Inside the product the rule is narrower than a nav check, because a dashboard
tile is a query: a saved dashboard or a subscription whose query names a member
the viewer is not authorized for shows that tile's unavailable state naming the
missing grant — it does not silently drop the column, and sharing a dashboard
transfers no data authority. Explore's member picker is the catalogue response,
so a principal is never offered a member they cannot query. After any grant
change the user must sign in again to obtain the new authorization version.

## Inventory

### 1. Projection diff (run BEFORE and AFTER the release)

```
cd backend && uv run python scripts/authorization_access_impact.py --organization OR-XXXXXXXX
```

The script projects every active human through the same evaluator the API uses,
reports per institution which modules they may view and WHICH SLICE each view
capability reads (the `data scope` column, added 2026-09-29), and flags
`no_bindings | no_product_view | account_plane_only | scoped_reader`. Mounting
the BI routes grants nobody anything, so **both runs must be identical**,
`data scope` column included: a diff means something else changed in the
release. Keep both outputs with the record (`--json` for a machine diff). The
script takes a database URL (`WORKER_DATABASE_URL` then `DATABASE_URL`), has no
hermetic mode, and excludes service users — machine principals are covered by
the SQL below instead. Hermetic coverage:
`tests/scripts/test_authorization_access_impact.py` (the data-scope column is
not yet asserted there).

### 2. Per-pair inventory (read-only)

Run as a role that can see every tenant user, institution and binding. It
enumerates human AND machine principals over each pair the catalogue declares,
and infers nothing from scalar roles.

```sql
WITH bi_pairs (module_scope, sensitivity_scope) AS (
    VALUES
        ('credit', 'aggregated'),
        ('credit', 'confidential'),
        ('credit', 'restricted'),
        ('risk', 'aggregated'),
        ('risk', 'confidential'),
        ('risk', 'restricted'),
        ('cap', 'aggregated'),
        ('liq', 'aggregated'),
        ('irrbb', 'aggregated'),
        ('markets', 'aggregated'),
        ('fcst', 'aggregated'),
        ('fx', 'aggregated'),
        ('ftp', 'aggregated')
),
active_principals AS (
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
        b.name AS institution_name,
        -- cast to jsonb: the column is json, which has no equality operator and
        -- so cannot appear in the GROUP BY below
        (t.default_modules)::jsonb AS entitled_modules,
        pair.module_scope,
        pair.sensitivity_scope
    FROM active_principals AS p
    JOIN banks AS b ON b.organization_id = p.organization_id
    LEFT JOIN institution_types AS t ON t.type_code = b.institution_type
    CROSS JOIN bi_pairs AS pair
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
        t.module_scope,
        t.sensitivity_scope,
        t.entitled_modules,
        COALESCE(bool_or(
            a.principal_type = t.principal_type
            AND a.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver', 'validator')
            AND a.module_scope IN (t.module_scope, 'all')
            AND a.sensitivity_scope IN (t.sensitivity_scope, 'all')
            AND (
                (a.institution_scope = 'institution'
                 AND a.institution_id = t.institution_id)
                OR
                (a.institution_scope = 'organization'
                 AND a.institution_id IS NULL)
            )
        ), FALSE) AS pair_view
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
        t.scalar_role,
        t.module_scope,
        t.sensitivity_scope,
        t.entitled_modules
)
SELECT
    *,
    CASE
        WHEN principal_type = 'machine' THEN
            'denied: BI reads require an interactive human binding'
        WHEN entitled_modules IS NULL THEN
            'denied: institution licence class does not resolve (409)'
        WHEN NOT jsonb_exists(
            entitled_modules,
            CASE module_scope
                WHEN 'cap' THEN 'capital'
                WHEN 'liq' THEN 'liquidity'
                WHEN 'fcst' THEN 'forecasting'
                ELSE module_scope
            END
        ) THEN
            'denied: module not entitled for this licence class'
        WHEN pair_view THEN
            'allowed: every member of this pair is queryable'
        ELSE
            'denied: no active exact binding for this pair'
    END AS bi_result
FROM authority
ORDER BY organization_id, institution_id, principal_type, email, module_scope, sensitivity_scope;
```

`entitled_modules` is the institution-type registry's `default_modules`, a JSON
array cast to `jsonb` (hence `jsonb_exists` rather than `= ANY`, and rather than
the `?` operator, which many drivers read as a bind placeholder; the cast is also
what lets the column sit in the `GROUP BY`). The `CASE` maps the three
authorization module values whose entitlement slug differs from the module name. Review the output with each institution and list every
human and machine principal that will be denied on each pair. Machine principals
are included so the release record proves no integration was assumed to have BI
read authority on the INTERACTIVE routes, which refuse every machine credential.
The Power BI Stage B feed (`GET …/bi/feeds/{dataset}`, `app/features/read_bi_feeds.py`,
built 2026-09-27) is the one machine-facing BI route: it requires a `bi_reader`
binding (migration `202609270074`; `{view}` only, disjoint from
`integration_writer`'s `{ingest}`, machine-only by CHECK) issued through the
integration-key flow with `purpose=reader`, and it is inventoried by its own
contract, `backend/docs/powerbi_stage_b.md`. The SQL above still reports every
machine principal as denied, which is correct for the surfaces it inventories.

## Exact binding rows

Create no backfill. Mounting the BI routes grants nobody anything — BI reads the
same bindings the module pages already require, so an institution that has
granted its Treasurer LIQUIDITY/`aggregated` already has BI liquidity measures
under that row. Anything further is an institution decision made through
`grant_administration.create_scoped_grant`, so `authv` advances and refresh-token
families are revoked in the same transaction.

| Need                                                                                  | `principal_type` | `role_bundle`                                                   | `institution_scope`                      | `institution_id`                             | `module_scope`      | `sensitivity_scope`      | `data_scope_kind` / `data_scope_values`                                                               |
| ------------------------------------------------------------------------------------- | ---------------- | --------------------------------------------------------------- | ---------------------------------------- | -------------------------------------------- | ------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------- |
| Aggregate BI for one engine (dashboards, trends, Explore aggregates)                  | `human`          | `viewer`, `auditor`, `analyst`, `approver`, or `validator`      | `institution` or explicit `organization` | exact `BK-*` or `NULL` for organization-wide | the engine's module | `aggregated`             | `all` / NULL — institution-grain figures (ratios, bank-wide totals) are refused to any narrower scope |
| The same, for one branch network or region only (portfolio slices; a branch manager)  | `human`          | same                                                            | `institution`                            | exact `BK-*`                                 | `credit`            | `aggregated`             | `branch` / `["B001", …]` or `region` / `["Northern", …]` — a non-empty list, or the row is refused    |
| Record-level grids without obligor names (`position.id`, `position.source_reference`) | `human`          | same                                                            | same                                     | same                                         | `risk`              | `confidential`           | `all` / NULL                                                                                          |
| Named obligors: counterparty name / group / reference, largest-single-name share      | `human`          | same                                                            | same                                     | same                                         | `credit`            | `restricted`             | `all`, or the same slice as the aggregate row                                                         |
| Record-level BI export                                                                | `human`          | `analyst` (the only bundle carrying `export`)                   | same                                     | same                                         | the member's module | the member's sensitivity | same as the read row it accompanies                                                                   |
| Power BI Stage B feed pull                                                            | `machine`        | `bi_reader` (issued with the integration key, `purpose=reader`) | `institution`                            | exact `BK-*`                                 | `all`               | `aggregated`             | See [feed-key issuance](../../docs/API_INTEGRATION.md#1-authentication)                               |

The common least-privilege credit analyst therefore needs two independently
complete rows — the aggregate portfolio and the named obligors are separate
decisions:

```json
[
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
  },
  {
    "principal_user_id": "<same confirmed human user UUID>",
    "role_bundle": "viewer",
    "institution_scope": "institution",
    "institution_id": "<same exact BK-*>",
    "module_scope": "credit",
    "sensitivity_scope": "restricted",
    "reason_category": "<structured reason category>",
    "reason_detail": "<institution-approved reason naming why this reader needs obligor names>",
    "expected_authority_sentence": "<server preview response>"
  }
]
```

Do not replace these with incomplete rows, combine fields across rows, copy
production users into code, or widen a scope to `all` to compensate for a missing
decision. An `all`/`all` row makes every BI member in every entitled module
queryable, obligor names included.

### Branch and region scope IS enforced; desk and currency are not

The [foundation contract](authorization_foundation.md#whole-institution-figures-and-credit-only-narrowing)
owns which modules accept narrowing and the migration for unsupported grants.
The rules below describe how BI applies a supported scope.

> Corrected 2026-09-29 (audit A360-7 S1). Until then this section said the
> opposite — that the binding carried no data-scope columns and that a branch
> named in a grant was decorative. That was true for Phase 1 and false from
> migration `202609270073` (2026-09-27) on. An operator who read the old text
> would have granted wider than intended or refused a narrowing that works.

Since `202609270073` a binding carries `data_scope_kind` (`all | branch |
region`, server default `all`) and `data_scope_values`, and the row is refused
by CHECK unless `all` carries NULL and a `branch`/`region` row carries a
NON-EMPTY list — so "no branches" is storable only as a scope that returns no
rows, never as an absent filter. Three places make that sentence enforcement
rather than annotation:

1. **Reduction.** `authorize_query` (`app/services/bi/authorization.py`) loads
   the effective grants that matched EACH (module, sensitivity) pair through
   `authorization.load_effective_grants` and reduces each pair with
   `reduce_data_scope` — no grants → nothing; any `all` → the whole
   institution; otherwise the union of the branch and region lists — then
   combines the pairs narrowest-wins. Reducing the ids of DIFFERENT pairs in
   one pass was audit blocker A10-01 (an `all` on liquidity would have erased a
   branch restriction on credit). The machine feed's authorizer
   (`services/bi/feeds/authorization.py`) carried the same union until audit
   A360 H8 (latent — no tenant route mints a second `view` binding on a machine
   principal); the 2026-09-29 remediation moves it onto the shared
   `authorization.combine_pair_scopes`, the helper `authorize_query` uses, so
   both surfaces reduce per pair through one function (in the working tree,
   uncommitted at the time of writing).
2. **Resolution.** `app/services/bi/data_scope.py::resolve` turns the declared
   scope into the ONE filter for this institution: the declared branch codes
   plus every `bi_dim_branch` row in a declared region, scoped to the exact
   `(organization, bank)`. A region no ingested branch belongs to, or a code the
   Data Engine has never seen, resolves to a filter that matches no row — never
   to `()`.
3. **Injection.** The filter is handed to `compile_query` BESIDE the `BiQuery`
   (`read_bi.injected_filters`), so the compiler ANDs it with the client's own
   predicates and no request can reach, remove or widen it; a request filtering
   `branch.code = 'B2'` under a `B1` grant is served the intersection. Seven
   modules call `resolve`: `features/read_bi.py` (the read routes and packs),
   `services/bi/insights/assemble.py`, `exports/runner.py` and `exports/jobs.py`,
   `alerts.py`, `subscriptions.py` and `feeds/authorization.py`. An
   institution-grain measure (a capital ratio, a bank-wide figure) is refused to
   a scoped principal outright (`REASON_INSTITUTION_GRAIN`,
   `REASON_BANK_WIDE_FIGURE`) rather than served as a wrong number with a
   right-looking name.

The scope is part of the answer's identity: `ResolvedDataScope.fingerprint`
enters the ETag, `data_scope` rides every `BiQueryResult`, the export
provenance block prints the slice (`Branches: B001, B002`; `Regions: Northern ·
4 branches in scope`), the feed states it in `X-Bi-Feed-Data-Scope`, and
`/auth/me` projects it per capability (`EffectiveCapabilityRead.data_scope`) so
the dashboard's coverage notice can say what a figure covers.

**Grant it as scope, never as prose.** The Members composer's book-coverage
control writes `data_scope_kind` / `data_scope_values`; a branch named only in
`grant_reason` is NOT enforced and never was. Desk, portfolio and currency
remain absent from the vocabulary (`docs/rbac.md` §7.3) — do not promise them.

**The gate reports it.** `scripts/authorization_access_impact.py` carries a
`data scope` column and a `scoped_reader` flag per principal (added 2026-09-29;
audit A360-7 S2 found the Phase 4 cutover had shipped without it). The JSON
output lists every `view` capability's kind and values, reduced per capability
from the bindings that matched it — the same reduction as above, never a union
across pairs — so a before/after diff shows a slice that widened (a
disclosure) or narrowed (a lost figure). Run it before and after any release
that touches bindings, the evaluator or the resolver. Its hermetic test does
not yet assert the new column (owed).

Pinned by: `tests/services/bi/test_data_scope.py`, `tests/api/test_bi_data_scope.py`,
`tests/api/test_data_scope_grants.py`, the A10-01 cases in
`tests/services/bi/test_bi_authorization.py`, `tests/api/test_bi_feeds.py` and,
for the credit blotter, `tests/api/test_credit_route_authorization.py`.

Turning BI on in production is an ordered sequence with its own hazards; it is
recorded in [`bi_turn_on_runbook.md`](bi_turn_on_runbook.md).

## Release record

Before the deployment that mounts the BI routes, attach:

1. The dated `authorization_access_impact.py` output BEFORE and AFTER, for every
   organization, with confirmation that the two are identical.
2. The dated per-pair inventory output for every organization and institution.
3. The exact list of human and machine principals denied on each pair, and the
   confirmation that the denial set is what the institution intends.
4. Every institution-approved binding row created for BI before release, with its
   reason, its `data_scope_kind` / `data_scope_values`, and the server's
   authority sentence.
5. Confirmation that every branch or region the institution was promised is a
   STORED scope on a binding (it appears in the `data scope` column of the
   projection diff), and that no desk, portfolio or currency scope was promised.
6. Confirmation that the interactive routes deny machine and impersonated
   credentials, that the feed accepts only `bi_reader` keys, that every surface
   writes `bi_query_log` for allowed and denied reads alike, and that the ETag is
   keyed on `authv` and the resolved scope's fingerprint.
7. Confirmation that every user whose `authv` changed signed in again.

## Pinned by

| Guarantee                                                                                                            | Test                                                                                                  |
| -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| The matrix: principal kind × bundle × module × sensitivity × institution × query shape                               | `tests/api/test_bi_authorization_matrix.py`                                                           |
| Pair collection (filters, hierarchies, ratio components, `over`, Top-N, pivot, sort), call budget, fail-closed edges | `tests/services/bi/test_bi_authorization.py`                                                          |
| Every catalogue member's module and sensitivity resolves to a platform enum value                                    | `tests/architecture/test_bi_catalogue_authority.py`                                                   |
| Nothing a client sends becomes SQL                                                                                   | `tests/architecture/test_bi_compiler_injection.py`                                                    |
| Cross-tenant 404 on every `/bi/*` route, and the route dependency inventory                                          | wave-3 `tests/api/test_bi_routes.py`, `tests/architecture/test_bi_authorization.py` (not yet written) |

## Rehearsal record

The projection diff was rehearsed on 2026-09-22 against a throwaway hermetic
database (two humans, one CREDIT/`aggregated` institution row, one with no
bindings): the script reports `credit` in the product-view column for the granted
reader, `no_bindings,no_product_view` for the other, and the before/after outputs
are byte-identical — which is the expected result for a release that mounts BI
routes and grants nothing. Raw output: `.ai/bi_test_results/p1_g2_access_impact_*.txt`
(local, gitignored). The per-pair inventory SQL above has NOT been executed
against a Postgres deployment: the only Postgres this repository's agents may
reach is the shared primary, which is out of bounds. The release operator runs it
first, against the target deployment.
