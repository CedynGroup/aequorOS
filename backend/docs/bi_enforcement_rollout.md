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

| Surface | Permission | Module / sensitivity required |
| --- | --- | --- |
| `GET …/bi/catalogue` | `view` | None of its own, and no tenant data: the response is the platform's own metadata, filtered member by member through the same decision the query path makes, so a principal is never offered a member they cannot query. A principal holding NO pair receives an empty dictionary (200) with a `withheld_members` COUNT — never the withheld ids |
| `POST …/bi/query` | `view` | Every distinct `(module, sensitivity)` pair the submitted query touches |
| `POST …/bi/grid` | `view` | Same, at the record grain — these queries reach `confidential` members |
| `POST …/bi/drill` | `view` | Same, plus every level the drill path descends into |
| `POST …/bi/explain` | `view` | Same as the query it explains |
| `GET …/bi/trust` | `view` | **Every pair the reported checks disclose: `credit`/`aggregated` + `liq`/`aggregated` + `risk`/`aggregated`** (`read_bi.CHECK_DISCLOSURES` names one catalogue member per check). The payload is figures, not metadata — R2's `lhs` IS the institution's total loans, R3's its total deposits, R9's its total assets and funding, R7's `detail` its real branch codes — and the badge is one verdict over the whole book, so a principal missing any one of the three is refused the page rather than shown a partial verdict. Audit A6-01 found this route authorizing nothing and serving another institution's totals to a principal denied that institution's aggregates |
| `POST …/bi/export` (Phase 2) | `view` for summary, `export` for record-level | The member's own module and sensitivity |

No BI surface infers authority from `users.role`, the token's `roles[]`, a
different module, a different sensitivity, or two partial bindings combined.

### The pairs the catalogue declares today

Catalogue version 1.0.0 — 288 members over twelve pairs. A grant covers a pair,
so this table is what an institution is actually choosing between.

| Module scope | Sensitivity scope | Members | Entitlement slug | Examples |
| --- | --- | --- | --- | --- |
| `credit` | `aggregated` | 94 | `credit` | `loans.npl_ratio_pct`, `engine.npl_ratio_pct.crd.official` |
| `credit` | `confidential` | 2 | `credit` | `loan.employer`, `event.position_id` |
| `credit` | `restricted` | 5 | `credit` | `counterparty.name`, `counterparty.group`, `loans.largest_single_name_share_pct` |
| `risk` | `aggregated` | 43 | `risk` | `positions.balance_rc`, `branch.region`, `time.date` |
| `risk` | `confidential` | 2 | `risk` | `position.id`, `position.source_reference` |
| `cap` | `aggregated` | 36 | `capital` | `engine.car_pct.crd.official` |
| `liq` | `aggregated` | 27 | `liquidity` | `engine.lcr_pct.crd.official`, `deposits.balance_rc` |
| `irrbb` | `aggregated` | 23 | `irrbb` | `engine.eve_base_ghs.crd.official` |
| `markets` | `aggregated` | 20 | `markets` | `engine.pit_pd_point_pct.advisory_internal.official` |
| `fcst` | `aggregated` | 16 | `forecasting` | `engine.year5_car_pct.advisory_internal.official` |
| `fx` | `aggregated` | 10 | `fx` | `engine.nop_ghs.crd.official` |
| `ftp` | `aggregated` | 10 | `ftp` | `engine.portfolio_nim_pct.advisory_internal.official` |

Two notes on this table. **`restricted` is exactly the members that name a single
obligor** — the counterparty id, name, source reference and group, and the
largest-single-name share, which is a ratio you can invert into one exposure. It
is the smallest set the spec's own rule allows and it must not be widened by
moving a name into `confidential` to make a screen easier to reach.
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
   member, so the read budget bounds that path as well (audit A6-06). Two
   surfaces write NO row — `catalogue` and `trust` — because the table's
   `surface` CHECK names only the six data surfaces and recording them under
   another surface would put an event in an append-only audit table that did not
   happen; both are authorized and both are subject to the budget, and widening
   the vocabulary (`app/models/bi.py` + migration `202609220066`) is the
   follow-up that lets them refill it.
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

The script projects every active human through the same evaluator the API uses
and flags `no_bindings | no_product_view | account_plane_only`. Mounting the BI
routes grants nobody anything, so **both runs must be identical**: a diff means
something else changed in the release. Keep both outputs with the record. The
script takes a database URL (`WORKER_DATABASE_URL` then `DATABASE_URL`), has no
hermetic mode, and excludes service users — machine principals are covered by
the SQL below instead. Hermetic coverage:
`tests/scripts/test_authorization_access_impact.py`.

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
read authority: the Power BI feed of Phase 4 is a separate route with its own
machine dependency and its own bundle, and it does not exist yet.

## Exact binding rows

Create no backfill. Mounting the BI routes grants nobody anything — BI reads the
same bindings the module pages already require, so an institution that has
granted its Treasurer LIQUIDITY/`aggregated` already has BI liquidity measures
under that row. Anything further is an institution decision made through
`grant_administration.create_scoped_grant`, so `authv` advances and refresh-token
families are revoked in the same transaction.

| Need | `principal_type` | `role_bundle` | `institution_scope` | `institution_id` | `module_scope` | `sensitivity_scope` |
| --- | --- | --- | --- | --- | --- | --- |
| Aggregate BI for one engine (dashboards, trends, Explore aggregates) | `human` | `viewer`, `auditor`, `analyst`, `approver`, or `validator` | `institution` or explicit `organization` | exact `BK-*` or `NULL` for organization-wide | the engine's module | `aggregated` |
| Record-level grids without obligor names (`position.id`, `position.source_reference`) | `human` | same | same | same | `risk` | `confidential` |
| Named obligors: counterparty name / group / reference, largest-single-name share | `human` | same | same | same | `credit` | `restricted` |
| Record-level BI export | `human` | `analyst` (the only bundle carrying `export`) | same | same | the member's module | the member's sensitivity |

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
    "reason": "<institution-approved reason>",
    "expected_authority_sentence": "<server preview response>"
  },
  {
    "principal_user_id": "<same confirmed human user UUID>",
    "role_bundle": "viewer",
    "institution_scope": "institution",
    "institution_id": "<same exact BK-*>",
    "module_scope": "credit",
    "sensitivity_scope": "restricted",
    "reason": "<institution-approved reason naming why this reader needs obligor names>",
    "expected_authority_sentence": "<server preview response>"
  }
]
```

Do not replace these with incomplete rows, combine fields across rows, copy
production users into code, or widen a scope to `all` to compensate for a missing
decision. An `all`/`all` row makes every BI member in every entitled module
queryable, obligor names included.

### Branch, region and desk scope is NOT enforced

`authorization_bindings` carries no data-scope columns, so a BI grant cannot be
narrowed to one branch, region, desk or currency: an authorized principal reads
the whole institution. `BiAuthorization.data_scope` is always `all` in Phase 1
and exists only so the Phase 4 dimension (D-029: derived from the matched
bindings and injected as an unremovable filter, security row S18) lands without
changing the decision's shape. **Do not name a branch or a region in a grant
reason as if it were enforced scope** — the same prohibition every rollout since
FX has carried. When data scope does land, `scripts/authorization_access_impact.py`
must be extended to report it before the cutover.

## Release record

Before the deployment that mounts the BI routes, attach:

1. The dated `authorization_access_impact.py` output BEFORE and AFTER, for every
   organization, with confirmation that the two are identical.
2. The dated per-pair inventory output for every organization and institution.
3. The exact list of human and machine principals denied on each pair, and the
   confirmation that the denial set is what the institution intends.
4. Every institution-approved binding row created for BI before release, with its
   reason and the server's authority sentence.
5. Confirmation that no branch, region, desk or currency scope was promised.
6. Confirmation that the routes deny machine and impersonated credentials, write
   `bi_query_log` for allowed and denied queries alike, and key the ETag on
   `authv`.
7. Confirmation that every user whose `authv` changed signed in again.

## Pinned by

| Guarantee | Test |
| --- | --- |
| The matrix: principal kind × bundle × module × sensitivity × institution × query shape | `tests/api/test_bi_authorization_matrix.py` |
| Pair collection (filters, hierarchies, ratio components, `over`, Top-N, pivot, sort), call budget, fail-closed edges | `tests/services/bi/test_bi_authorization.py` |
| Every catalogue member's module and sensitivity resolves to a platform enum value | `tests/architecture/test_bi_catalogue_authority.py` |
| Nothing a client sends becomes SQL | `tests/architecture/test_bi_compiler_injection.py` |
| Cross-tenant 404 on every `/bi/*` route, and the route dependency inventory | wave-3 `tests/api/test_bi_routes.py`, `tests/architecture/test_bi_authorization.py` (not yet written) |

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
