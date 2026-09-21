# Markets scoped-binding enforcement rollout

This is the deployment gate for the Markets cutover: market-data views, implied
ratings, private curve overlays, manual uploads, and the connection metadata
reads. It does not authorize a backfill. Operators must inventory real
principals, confirm duties with the institution, and create each approved
binding explicitly before deployment.

Markets authority is split by what the data **is**, not by which page shows it:

- **published** — vendor and desk market data: the Markets hub views, source
  preferences and planes, forward grids, the scope catalog and quota ledger,
  upload templates, and the manual upload that writes canonical market data;
- **confidential** — material derived from the bank's own book: implied-rating
  runs and the bank's private curve overlays;
- **restricted** — credential-bearing connection metadata (fingerprint, expiry,
  validation status) in the connection list.

## Affected surfaces

| Surface                                                                                                    | Required complete binding                    |
| ---------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| `GET …/market-data/views`, `source-preferences`, `planes`, `curves/{name}/forward-grid`, `scopes`, `quota` | MARKETS / `published` / `view`               |
| Private components, adjusted values, and overlay delta previews in `views` / `planes`                      | Additional MARKETS / `confidential` / `view` |
| `GET /market-data/templates/{kind}?bank_id=` (organization-level path, institution named by the query)     | MARKETS / `published` / `view`               |
| `POST …/market-data/uploads` (manual market-data upload)                                                   | MARKETS / `published` / `create`             |
| `GET …/implied-rating/runs`, `GET …/implied-rating/runs/{id}`                                              | MARKETS / `confidential` / `view`            |
| `POST …/implied-rating/runs`                                                                               | MARKETS / `confidential` / `run`             |
| `GET …/market-data/overlays`                                                                               | MARKETS / `confidential` / `view`            |
| `POST …/market-data/overlays` (new or superseding version)                                                 | MARKETS / `confidential` / `create`          |
| `POST …/market-data/overlays/{id}/end`                                                                     | MARKETS / `confidential` / `edit`            |
| `GET …/market-data/connections`                                                                            | MARKETS / `restricted` / `view`              |

The views and planes response-projection boundaries evaluate the additional confidential
view decision on the exact bank (or explicit organization-wide coverage). A denial or
evaluator failure preserves published base curves but omits private components,
adjusted points, and delta previews; scalar roles and partial bindings never suffice.

Every institution route resolves the tenant-owned bank first: an unknown or
foreign bank is 404 before any permission is evaluated. An overlay or rating run
that belongs to another bank returns 404 from the authorized bank's route, and
each list serves only the named bank's rows. A denied mutation cannot start the
rating engine, stage an upload, create an ingestion batch, write an overlay, or
record an audit event.

**Endpoint owner for the manual upload.** The upload is owned by Markets, not
Data Engine: it runs through the market-data adapter as a manual pull into the
market-data canonical state (`pull_runner.execute_pull`), never into the bank's
financial facts, and its template sits beside the other Markets surfaces. A
DATA `create` grant therefore does not upload market data, and a MARKETS
`create` grant does not ingest a bank book.

**Template download.** The route keeps its organization-level path
(`/market-data/templates/{kind}`) but names the target institution through the
required `bank_id` query. The effective-authority projection carries no
organization-wide Markets capability, so an organization-target check would
leave every institution-scoped analyst unable to fetch the template they are
authorized to upload. The download is decided by the same exact
MARKETS/`published`/`view` grant the Markets hub needs, on the named bank.

Held for the configuration-authority cutover (unchanged, legacy analyst gate):
market-data connection create / validate / test / update / disable / enable /
revoke, and `PUT …/market-data/source-preferences`.

Out of scope and unchanged: the live rating card on the Markets overview reads
`GET …/live-summary` (shared live projection) and the Data Engine console's
other tabs, which are owned by their own cutovers.

No scalar role, token role, tenant membership, or binding for another module or
sensitivity grants Markets authority. Data Engine grants do not open Markets;
Markets grants do not open the Data Engine.

## Dashboard controls

`/markets` requires MARKETS/`published`/`view`; without it the navigation entry stays visible but disabled with
“Requires Markets · Published · View. Ask your organization owner or admin to
grant it.” A deep link resolves as 404, and no market-data request is issued. Within the hub:

- **Edit spreads** (curve board and curves explorer) needs
  MARKETS/`confidential`/`view`. Without it the control stays visible and
  disabled with “Requires Markets · Confidential · View. Ask your organization
  owner or admin to grant it.”, and the spread editor is never opened.
- In the spread editor, **Save spread** needs MARKETS/`confidential`/`create`
  and **End today** needs MARKETS/`confidential`/`edit`; each stays visible and
  disabled with its own sentence.

Data Engine → Market Data reads the same projection: the connection list is
replaced by the MARKETS/`restricted`/`view` sentence when that grant is missing,
template downloads need MARKETS/`published`/`view`, and **Upload** needs
MARKETS/`published`/`create` — visible and disabled with “Requires Markets ·
Published · Create …” otherwise. The held connection-lifecycle stepper still
lists the scope catalog, so connecting a source needs the published view too.
Settings → Data & compute and the Data Engine overview's connection-health
panel request market-data connections only with the restricted view.

The shared permission-only disabled-control policy is defined in
[the RBAC guide](../../docs/rbac.md); native disabled controls expose their
explanation through a keyboard-focusable wrapper.

## Deny-impact inventory

Run the inventory read-only with a role that can see every tenant. Store the
dated result with the release evidence. Do not copy names or identifiers from
another environment into a grant request.

```sql
SELECT
    u.organization_id,
    u.id AS principal_user_id,
    u.email,
    u.auth_provider,
    u.is_active,
    b.id AS institution_id,
    ab.id AS binding_id,
    ab.principal_type,
    ab.role_bundle,
    ab.institution_scope,
    ab.institution_id AS binding_institution_id,
    ab.module_scope,
    ab.sensitivity_scope,
    ab.status,
    ab.valid_from,
    ab.valid_until,
    ab.revoked_at
FROM users AS u
CROSS JOIN banks AS b
LEFT JOIN authorization_bindings AS ab
  ON ab.organization_id = u.organization_id
 AND ab.principal_user_id = u.id
 AND ab.principal_type = 'human'
 AND ab.status = 'active'
 AND ab.revoked_at IS NULL
 AND ab.valid_from <= now()
 AND (ab.valid_until IS NULL OR ab.valid_until > now())
 AND ab.module_scope IN ('markets', 'all')
 AND (
      (ab.institution_scope = 'institution' AND ab.institution_id = b.id)
      OR
      (ab.institution_scope = 'organization' AND ab.institution_id IS NULL)
 )
WHERE b.organization_id = u.organization_id
ORDER BY u.organization_id, b.id, u.email, ab.role_bundle, ab.sensitivity_scope;
```

Who this cutover affects, beyond the obvious Markets readers:

- anyone who uploads market-data workbooks or downloads the templates
  (Data Engine → Market Data), whose pre-cutover access relied on authentication
  for templates and the scalar `analyst` gate for uploads;
- anyone who runs or reads implied-rating runs through the API;
- anyone who maintains private curve spreads;
- anyone whose Data Engine overview or Settings page lists market-data
  connections — they keep every other row and lose only that one until a
  restricted view row exists.

For every active human principal who uses an affected surface, record:

1. the organization and exact institution;
2. the user and confirmed Markets duty;
3. the affected surface and its sensitivity tier;
4. whether one complete active row already grants the required tuple; and
5. whether deployment will deny the user until a row is approved.

Record machine principals separately. Integration keys hold only the
`integration_writer` bundle and gain no Markets authority from this cutover;
market-data uploads are a human action. Scheduled vendor pulls do not
impersonate a tenant user and are unaffected.

## Exact binding rows

Create only rows approved from the inventory through the authorization service.
Follow the foundation's
[authorization-version and session transition contract](authorization_foundation.md#authorization-version-and-deployment-transition).

| Duty                                                      | `principal_type` | `role_bundle`                                 | `institution_scope` | `institution_id` | `module_scope` | `sensitivity_scope` |
| --------------------------------------------------------- | ---------------- | --------------------------------------------- | ------------------- | ---------------- | -------------- | ------------------- |
| Read the Markets hub, source planes, quota, and templates | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution`       | exact `BK-*`     | `markets`      | `published`         |
| Upload market-data workbooks                              | `human`          | `analyst`                                     | `institution`       | exact `BK-*`     | `markets`      | `published`         |
| Read implied-rating runs and private spreads              | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution`       | exact `BK-*`     | `markets`      | `confidential`      |
| Run implied ratings; add and end private spreads          | `human`          | `analyst`                                     | `institution`       | exact `BK-*`     | `markets`      | `confidential`      |
| Read connection metadata (fingerprint, expiry, status)    | `human`          | `viewer`, `auditor`, `analyst`, or `approver` | `institution`       | exact `BK-*`     | `markets`      | `restricted`        |

If the institution explicitly approves coverage across all its banks, replace
only `institution_scope` with `organization` and `institution_id` with `NULL`.
Do not widen module or sensitivity scope to compensate for a missing row.
Published, confidential, and restricted access are separate rows unless the
institution explicitly approves the broad `all` sensitivity.

Example least-privilege request for a market-data uploader:

```json
{
  "principal_user_id": "<confirmed human user UUID>",
  "role_bundle": "analyst",
  "institution_scope": "institution",
  "institution_id": "<exact BK-* ID>",
  "module_scope": "markets",
  "sensitivity_scope": "published",
  "reason": "<institution-approved reason>",
  "expected_authority_sentence": "<server preview response>"
}
```

Do not infer rows from scalar roles, combine partial rows, grant machine
principals, or reuse authority approved for another module.

## Release record

Attach all of the following before deployment:

1. the dated inventory output for every organization and institution;
2. the exact human and machine principals affected, including everyone denied;
3. the institution-approved duty for every new binding;
4. the exact rows created, including grantor, reason, validity, and scope;
5. confirmation that users whose `authv` changed signed in again; and
6. browser evidence for an unbound user, a published-only reader with the
   spread editor and upload disabled, a confidential reader with save/end
   disabled, and an exactly Markets-bound analyst completing an upload and a
   spread.

The opt-in real-data API suite (`REAL_DATA_DATABASE_URL`) exercises these
routes as the primary's real administrator; it now requires that identity to
hold the Markets rows above, exactly as a deployment does.
