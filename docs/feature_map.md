# AequorOS feature map

A catalogue of what each surface does, with one stable heading per feature. A
development spec names a feature by its heading ("change the **Data Engine
upload** entry"), and the
[verification skill](../.agents/skills/verification/SKILL.md) uses the same
entries to reach each feature and check it on a running surface.

Verification recipes use these fields:

- **Reach**: the precondition that puts you at the feature's entry point.
- **Control**: what you click, type or call.
- **Check**: the observable result that proves it worked.
- **Traps**: failures that are not product bugs.
- **Depends on**: the routes, pages, test ids and files the entry relies on, as labelled
  clauses (see [Checked references](#checked-references)).

Entries that skip an operation give the reason under **Verification** and list
the dependencies separately.

Headings are identifiers. Rename one only together with every reference to it.
A change that adds, renames or removes a feature updates its entry in the same
change. Prefer a control's accessible role or name over quoted copy, and point to
the source or spec that owns the detail instead of restating it.

Shared conventions, which the entries do not repeat:

- Use local test data only: a disposable database, synthetic files and
  placeholders such as `BK-XXXXXXXX` and `OR-XXXXXXXX`. The
  [verification skill](../.agents/skills/verification/SKILL.md#local-test-data-only)
  owns the rules and how each surface is started.
- Bank financial data enters only through the Data Engine. A bank that has not
  ingested data shows empty or unavailable states; that is the expected result,
  not a fault.
- A refusal (`401`, `403`, or `404` for a sibling bank) is the expected result
  for a principal without the grant.
- Prefix tenant API routes with `/api/v1` unless the path already starts with
  `/api/`. Operator API paths that start with `/operator/` are already complete.
  `{bank_id}` is a `BK-` platform ID and `{org_id}` an `OR-` platform ID.

## Checked references

`backend/tests/architecture/test_feature_map_references.py` runs in the "Unit
tests" CI job and locally with
`cd backend && uv run pytest tests/architecture/test_feature_map_references.py`. It
checks dependency tokens, repository-relative links and Markdown heading anchors,
and literal backticked paths under the configured repository directories. It
requires every route group and page group it inventories to have an entry or a
reason under [Not mapped](#not-mapped).
Tenant groups use route prefixes, with bank-scoped routes grouped by the first
segment after the bank ID. Operator groups use the tags on registered routes.

On pull requests, the job uses the guards path selector in
[the Risk service workflow](../.github/workflows/risk-service.yml). For a
recognized rename, the selector sees the destination path. A move out of a
watched directory can therefore skip the check.

A **Depends on** line is a list of clauses separated by semicolons. Each clause
starts with a label and lists backticked tokens:

- `tenant API` and `operator API`: a route, optionally with a leading HTTP method
  and a trailing `/*` for a subtree. Tenant paths may omit the `/api/v1` prefix.
  Use the full path for tenant routes outside that prefix, such as health probes.
- `pages`: a path in the app tree of the surface the entry sits under.
- `test ids`: a literal `data-testid` declaration in that surface's component source.
- `files`: a repository path that Git tracks.

`{name}` and `[name]` both stand for a path parameter. Use the running surface
to confirm that referenced selectors render and the recipe works.

## Tenant API

The bank-facing FastAPI app in `backend/app/main.py` on :8000. Reach it as in
[the tenant API reference](../.agents/skills/verification/references/tenant-api.md).

### Tenant API health

Reports whether the process is alive and whether its dependencies are ready.

- **Reach**: the API process is running.
- **Control**: `GET /api/health/live`, then `GET /api/health/ready`.
- **Check**: `live` answers `200`. Readiness answers `200` with status `ok` or
  `degraded` and component checks, or `503` when the readiness probe fails.
- **Traps**: computation cases need `app.worker` running. The worker check
  detects overdue jobs as a sign of degradation. Check the process separately.
- **Depends on**: tenant API `/api/health/live`, `/api/health/ready`.

### Tenant API sign-in

Exchanges a local account's credentials for an access token and reports the
caller's identity and authority.

- **Reach**: a local account exists (the provisioned administrator or a member
  granted scoped access).
- **Control**: `POST /auth/login` with `{"email", "password"}`; send the
  returned `access_token` as a bearer value. `POST /auth/refresh` and
  `POST /auth/logout` each take `{"refresh_token": ...}`; refresh returns a
  replacement refresh token, so log out with the replacement.
- **Check**: `GET /auth/me` returns the caller; `GET /auth/effective-authority`
  returns the scoped grants the rest of the API enforces. After logout, refreshing
  with that same replacement token answers `401`.
- **Traps**: authority comes from scoped bindings, not the role label or token
  claims, so confirm effective authority for the bank and action before a
  capture. Pass credentials by environment variable and keep them out of
  evidence.
- **Depends on**: tenant API `POST /auth/login`, `POST /auth/refresh`,
  `POST /auth/logout`, `GET /auth/me`, `GET /auth/effective-authority`.

### Live summary, freshness and alerts

Reads the figures the worker computed for a bank, how current they are and the
findings raised. This is the tenant-health surface.

- **Reach**: for reads, sign in with bank access. Populated results need a Data
  Engine load and a running worker. Refresh requires confidential Data View
  and Run. Official runs require confidential Run for each applicable module
  and restricted Regulatory View.
- **Control**: `GET /banks/{bank_id}/live-summary`, `GET /banks/{bank_id}/freshness`,
  `GET /banks/{bank_id}/alerts`. `POST /banks/{bank_id}/refresh` and
  `POST /banks/{bank_id}/official-runs` enqueue work. Send `as_of_date` and a
  nonempty `reason` in each POST body.
- **Check**: `live-summary` returns `modules[]` with `status`, `metrics` and
  `pipeline_state` (`ready`, `blocked` or `failed`), plus `is_stale` and an
  optional `reconciliation` verdict. `freshness` returns per-module `is_stale`
  against the last official run. `alerts` returns `total`, `by_severity` and
  `items[]`. `refresh` and `official-runs` answer `202`.
- **Traps**: a bank with no ingested data returns empty modules, not figures. A
  `blocked` module was computed on a book that fails the balance-sheet identity;
  nothing derived from it may be filed. An official-run refusal is the fail-closed
  design, not a fault. Never derive facts yourself to populate a screen.
- **Depends on**: tenant API `GET /banks/{bank_id}/live-summary`,
  `GET /banks/{bank_id}/freshness`, `GET /banks/{bank_id}/alerts`,
  `POST /banks/{bank_id}/refresh`, `POST /banks/{bank_id}/official-runs`.

### Module dashboards

Serves each risk module's computed headline payload.

- **Reach**: a signed-in principal holding the module's grant; a loaded book for
  populated results. Scenario runs need the module's Run authority, which the
  read-only dashboard grant does not carry.
- **Control**: `GET /banks/{bank_id}/liquidity/dashboard`,
  `/capital/dashboard`, `/irr/dashboard`, `/fx/dashboard`, `/ftp/dashboard`,
  `/credit/dashboard`; scenario runs are `POST` to each module's
  `/run-all-scenarios` with the `reporting_period_id` of an existing reporting
  period in the JSON body.
- **Check**: each answers with the module's `metrics` for the live book, matching
  what its dashboard screen shows: `lcr_pct` and `nsfr_pct` (liquidity), `car_pct`
  and `cet1_ratio_pct` (capital), `worst_eve_change_pct_tier1` (IRR),
  `nop_pct_tier1` (FX), `portfolio_nim_pct` (FTP) and `npl_ratio_pct` (credit).
- **Traps**: "no data yet" or "not configured" answers `200` with
  `{"available": false, "error_code", "reason"}`, not a `4xx`. Treat that as the
  unavailable state to verify, not as an error.
- **Depends on**: tenant API `GET /banks/{bank_id}/liquidity/dashboard`,
  `GET /banks/{bank_id}/capital/dashboard`, `GET /banks/{bank_id}/irr/dashboard`,
  `GET /banks/{bank_id}/fx/dashboard`, `GET /banks/{bank_id}/ftp/dashboard`,
  `GET /banks/{bank_id}/credit/dashboard`,
  `POST /banks/{bank_id}/liquidity/run-all-scenarios`,
  `POST /banks/{bank_id}/capital/run-all-scenarios`,
  `POST /banks/{bank_id}/irr/run-all-scenarios`,
  `POST /banks/{bank_id}/fx/run-all-scenarios`,
  `POST /banks/{bank_id}/ftp/run-all-scenarios`,
  `POST /banks/{bank_id}/credit/run-all-scenarios`.

### Data Engine upload

Parses a spreadsheet or CSV into an ingestion batch with row-level findings.

- **Reach**: a principal with bank-scoped upload and batch-run authority, and
  an active mapping for the file layout.
- **Control**: `POST /banks/{bank_id}/ingestion-uploads` with the `file` form
  part. Send the returned `location`, `source_system: EXCEL_CSV`, `as_of_date`
  and `reason` to `POST /banks/{bank_id}/ingestion-batches`. Use the returned
  `batch.id` to read the batch and its translation failures.
- **Check**: upload and batch creation each answer `201`. The batch reports
  extracted, translated, accepted and error counts. A rejected row carries its
  source reference and finding.
- **Traps**: the upload stages in the bank's object storage, so a local MinIO
  must be running. A legacy `.xls` is refused by format with re-save guidance;
  an empty file answers `422` and an oversized file `413`. Non-blocking row
  errors can yield `accepted_with_warnings`; a `BLOCKER` rejects the batch.
- **Depends on**: tenant API `POST /banks/{bank_id}/ingestion-uploads`,
  `POST /banks/{bank_id}/ingestion-batches`, `GET /banks/{bank_id}/ingestion-batches`,
  `GET /banks/{bank_id}/ingestion-batches/{batch_id}`,
  `GET /banks/{bank_id}/ingestion-batches/{batch_id}/translation-failures`,
  `GET /banks/{bank_id}/ingestion-summary`, `GET /banks/{bank_id}/mapping-configs`,
  `GET /banks/{bank_id}/data-activations`.

### Data Engine API push

Loads data from an institution's middleware through three calls, running the
same pipeline as an upload.

- **Reach**: a data push integration key issued for the bank. See
  [Integration keys](#integration-keys).
- **Control**: open a push batch, stage synthetic records and commit it using
  the [push flow](API_INTEGRATION.md#2-the-three-call-flow) in the integration guide.
- **Check**: the commit returns an ingestion batch with `source_system`
  `API_PUSH`. Repeat the commit and confirm it returns the same ingestion
  batch with `reused: true`.
- **Traps**: the integration guide covers authentication and idempotency.
  Keep the integration key out of evidence.
- **Depends on**: tenant API `/banks/{bank_id}/push-batches/*`; files
  `backend/scripts/ingest_push.py`, `docs/API_INTEGRATION.md`.

### Regulatory returns

Generates, validates, certifies and files regulatory return packages.

- **Reach**: a bank with a computed live book and a return that supports
  `orass_sandbox`, sandbox and attestation signing configured, and distinct
  accounts authorized to prepare, approve and validate the package.
- **Control**: with preparation authority, pick a date with
  `GET /banks/{bank_id}/return-anchors?return_code={return_code}`, then send
  `return_code` and `reporting_date` to `POST /banks/{bank_id}/regulatory-packages`
  and run `validate`. Each person required to sign calls `attestation/step-up` with
  `signing_role` and their step-up proof, then `attestation/certify` with the
  same `signing_role`, returned `authorization_token` and returned
  `certification_digest` as `expected_certification_digest`. Certification at
  preparation starts the chain and certification at approval records approval.
  Follow `workflow/decisions` for any remaining review decisions. Use
  `workflow/hand-off` only to advance stages whose `transmit_on_approve` is
  false, such as handing off from approval to validation. Once approved and fully
  certified, use validation authority to call `submit` directly with
  `{"channel":"orass_sandbox"}`. The transmitting stage's hand-off uses the
  registry default, so skip that hand-off in this recipe.
- **Check**: a package reports its validation results, approval workflow state
  and filed artifacts; its version chain and submission events are readable.
- **Traps**: reporting dates come from the return definition, not the bank's
  reporting periods. Without signing configured the API logs that no return can be
  certified or filed; that is the unconfigured state, not a fault. Verify filing
  only against the simulated channel.
- **Depends on**: tenant API `/banks/{bank_id}/regulatory-packages/*`,
  `GET /banks/{bank_id}/return-anchors`, `GET /banks/{bank_id}/regulatory-runs`.

### Access grants and members

Lists the organization's members and administers scoped access grants.

- **Reach**: an account with an active organization ownership binding. Account
  administration alone is insufficient and the API returns `403`.
- **Control**: `GET /organization/members`; `POST /authorization/bindings/preview`
  then `POST /authorization/bindings` to grant; `POST
/authorization/bindings/{binding_id}/revoke`; requests go through
  `/authorization/access-requests`.
- **Check**: a preview reports separation-of-duties conflicts before the grant;
  the new binding then appears in effective authority; a revoked binding stops
  granting.
- **Traps**: a grant that conflicts with an existing one is refused by design.
  Two banks of one organization share a tenant, so verify a sibling bank is
  refused at the query, not only hidden in the UI.
- **Depends on**: tenant API `GET /organization/members`,
  `POST /authorization/bindings`, `POST /authorization/bindings/preview`,
  `POST /authorization/bindings/{binding_id}/revoke`,
  `/authorization/access-requests/*`.

### Integration keys

Issues and revokes bank-scoped machine keys for the push API.

- **Reach**: an account with authority to administer accounts and manage integrations.
- **Control**: `POST /integration-keys` (label, bank), `GET /integration-keys`,
  `POST /integration-keys/{key_id}/revoke`.
- **Check**: the secret is returned once at creation and never again; a revoked
  key is listed as revoked and its writes are refused.
- **Traps**: only data push keys can be issued; a reader key is refused with
  `422`. Never record the key value in evidence.
- **Depends on**: tenant API `GET /integration-keys`, `POST /integration-keys`,
  `POST /integration-keys/{key_id}/revoke`.

### Business intelligence

Defines measures, dashboards and alerts over a bank's data.

- **Reach**: enable `BI_ENABLED` on the API and sign in with the BI grants.
- **Control**: `GET /banks/{bank_id}/bi/measures`, `/bi/dashboards`,
  `/bi/alerts`, with the matching `POST`, `PUT` and `DELETE` routes.
- **Check**: created measures and dashboards are listed with their versions and
  shares.
- **Traps**: with BI disabled the registered BI routes return `404` through the
  router-level feature gate.
- **Depends on**: tenant API `/banks/{bank_id}/bi/*`.

### Bank audit log

Reads the bank's attributable security and compliance events.

- **Reach**: an account with a whole-institution binding to view the audit log
  through audit, security oversight or compliance oversight authority. Perform a
  loggable action such as a grant.
- **Control**: `GET /banks/{bank_id}/audit-log?limit=20`; add `event_type`, `actor`
  or `outcome` to filter and follow `next_cursor` for the next page.
- **Check**: events list newest first and the action you performed appears with its
  actor; a second page continues from `next_cursor`.
- **Traps**: `start` and `end` must carry a timezone or the API answers `422`. An
  account without the required audit binding is refused with `403`.
- **Depends on**: tenant API `GET /banks/{bank_id}/audit-log`.

## Staff operator API

The staff control plane in `backend/app/operator/` on :8100, never mounted on the
tenant API. Reach it as in
[the operator API reference](../.agents/skills/verification/references/operator-api.md).

### Operator health and sign-in

Reports liveness and signs in a local staff account.

- **Reach**: the operator API is running; for sign-in, an operator account made
  with `backend/scripts/create_operator.py`.
- **Control**: `GET /operator/health`; `POST /operator/auth/login` with email and
  password.
- **Check**: health answers `200` without a token; login returns
  `access_token`, `expires_at` and the `operator` identity.
- **Traps**: password sign-in needs `OPERATOR_JWT_SECRET`, a different random
  value from `AUTH_JWT_SECRET`. The dev token path needs
  `OPERATOR_DEV_AUTH_ENABLED=1` and is refused outside an undeployed environment.
- **Depends on**: operator API `GET /operator/health`, `POST /operator/auth/login`.

### Tenant provisioning

Creates a tenant's organization, bank and first administrator as one saga.

- **Reach**: a signed-in operator; a disposable database.
- **Control**: `POST /operator/v1/tenants` with the institution details;
  `GET /operator/v1/tenants` and `/tenants/{org_id}` read the result.
- **Check**: each saga step reports its own outcome, and the tenant is listed with
  its `OR-` and `BK-` IDs; the audit log records the action.
- **Traps**: provisioning writes setup records and no bank financial data. A step
  failure rolls back what preceded it. The generated administrator password is
  shown once.
- **Depends on**: operator API `POST /operator/v1/tenants`, `GET /operator/v1/tenants`,
  `GET /operator/v1/tenants/{org_id}`, `GET /operator/v1/audit`.

### Tenant diagnostics and remediation

Reads a tenant's metrics, ingestion, findings and storage, and runs operator
fixes.

- **Reach**: a signed-in operator who opened an active inspection session for
  the provisioned tenant (see [Inspector sessions](#inspector-sessions)).
  Without that session, these reads and fixes return `403 inspection_required`.
- **Control**: `GET /operator/v1/tenants/{org_id}/metrics`, `/ingestion`,
  `/ingestion/{batch_id}`, `/findings`, `/config`, `/storage`, `/users`; fixes
  are `POST /operator/v1/tenants/{org_id}/fix/recompute`, `/fix/official-run`,
  `/fix/rerun-ingestion`, `/fix/redrive-dedup` and `/fix/config`. Send a nonempty
  `note` in every fix's JSON body. Include `bank_id` for recompute and official
  run in a multi-bank tenant, `batch_id` for rerun and redrive, and `kind`,
  `target_id` and `value` for config.
- **Check**: reads return the tenant's own records; a fix answers with its
  outcome (the enqueued job for `recompute` and `official-run`) and writes an
  audit entry.
- **Traps**: fixes mutate tenant state, so run them only against the disposable
  database. Whether a fix completes depends on the worker running.
- **Depends on**: operator API `GET /operator/v1/tenants/{org_id}/metrics`,
  `GET /operator/v1/tenants/{org_id}/ingestion`,
  `GET /operator/v1/tenants/{org_id}/ingestion/{batch_id}`,
  `GET /operator/v1/tenants/{org_id}/findings`,
  `GET /operator/v1/tenants/{org_id}/config`,
  `GET /operator/v1/tenants/{org_id}/storage`,
  `GET /operator/v1/tenants/{org_id}/users`, `POST /operator/v1/tenants/{org_id}/fix/*`,
  `GET /operator/v1/jobs`, `GET /operator/v1/worker-health`.

### Inspector sessions

Opens a time-boxed, audited, read-only inspection of one tenant.

- **Reach**: an operator with inspector authority; the same
  `IMPERSONATION_JWT_SECRET` on both APIs and `BANK_APP_BASE_URL` set to the
  running dashboard origin.
- **Control**: `POST /operator/v1/inspector/sessions` (`organization_id`, `bank_id`,
  `reason`, `support_reference`, `mode`, `ttl_minutes`); `POST .../{session_id}/act-token` hands off to the
  dashboard; `POST .../{session_id}/end` closes it.
- **Check**: `GET /operator/v1/inspector/sessions` lists the session you opened as active;
  the act-token response carries a `dashboard_url` matching the running
  dashboard; ending it moves it out of the active list.
- **Traps**: the act token and any URL fragment carrying it are credentials; keep
  them out of evidence. Confirm `dashboard_url` is the loopback dashboard before
  opening it.
- **Depends on**: operator API `POST /operator/v1/inspector/sessions`,
  `GET /operator/v1/inspector/sessions`,
  `POST /operator/v1/inspector/sessions/{session_id}/act-token`,
  `POST /operator/v1/inspector/sessions/{session_id}/end`.

### Operator accounts

Creates staff accounts and manages their role and activation.

- **Reach**: sign in with authority to administer staff accounts.
- **Control**: `GET` and `POST /operator/v1/operators`; `POST
/operator/v1/operators/{email}/deactivate`, `/reactivate`, `/reset-password`.
- **Check**: the account appears with its role and `is_active`; a reset returns a
  one-time password; each change is in the audit log.
- **Traps**: staff sessions without account administration authority receive
  `403`. Rank rules also limit which accounts an administrator may change.
- **Depends on**: operator API `GET /operator/v1/operators`,
  `POST /operator/v1/operators`, `POST /operator/v1/operators/{email}/deactivate`,
  `POST /operator/v1/operators/{email}/reactivate`,
  `POST /operator/v1/operators/{email}/reset-password`, `GET /operator/v1/audit`.

### Regulatory parameters

Governs the control-plane thresholds banks are measured against, with four-eyes
approval.

- **Reach**: a signed-in operator.
- **Control**: `GET` and `POST /operator/v1/regulatory-parameters`; a second
  operator then calls `POST /operator/v1/regulatory-parameters/{param_id}/approve`
  with `{}` or an object holding `change_rationale`.
- **Check**: a proposed parameter stays pending until a different operator
  approves it; the approval is audited.
- **Traps**: the proposer cannot approve their own change.
- **Depends on**: operator API `GET /operator/v1/regulatory-parameters`,
  `POST /operator/v1/regulatory-parameters`,
  `POST /operator/v1/regulatory-parameters/{param_id}/approve`.

### Markets desk determinations

Builds, approves and publishes the market-data determinations that fan out to
banks.

- **Reach**: two different operators with desk authority and observations loaded
  for the curves in scope.
- **Control**: the maker creates the determination with
  `POST /operator/v1/desk/determinations`, calls `compute` and calls `submit`
  for review. The checker calls `approve`, then publishes with `publish`.
  These actions are under `/operator/v1/desk/determinations/{determination_id}`.
  `GET /operator/v1/desk/publications` lists what was published.
- **Check**: the determination advances through its states and a published one
  appears in publications.
- **Traps**: a vendor error never reaches a bank-facing surface; an unavailable
  source shows as unavailable. Publishing writes shared market data, so use the
  disposable database.
- **Depends on**: operator API `POST /operator/v1/fx-forward/construct`,
  `/operator/v1/desk/*`, `/operator/v1/curves/*`.

### Operating environment assessments

Computes, reviews, approves and publishes the operating-environment score that
feeds the implied-rating model.

- **Reach**: two different signed-in operators, a disposable database and loaded
  observable inputs.
- **Control**: `POST /operator/v1/operating-environment/compute-preview`, then
  `POST /operator/v1/operating-environment/assessments` to stage a draft and
  `POST .../assessments/{assessment_id}/submit`; the second operator calls
  `approve` and then `publish` on the same path.
- **Check**: the assessment moves through draft, pending review, approved and
  published, and `GET /operator/v1/operating-environment/assessments` lists it.
- **Traps**: the proposer cannot approve. Publishing writes shared market data to
  every tenant, so use the disposable database only. A per-tenant failure is
  recorded rather than rolled back, and publishing again heals it.
- **Depends on**: operator API `GET /operator/v1/operating-environment/assessments`,
  `POST /operator/v1/operating-environment/compute-preview`,
  `POST /operator/v1/operating-environment/assessments`,
  `POST /operator/v1/operating-environment/assessments/{assessment_id}/submit`,
  `POST /operator/v1/operating-environment/assessments/{assessment_id}/approve`,
  `POST /operator/v1/operating-environment/assessments/{assessment_id}/publish`.

## Staff console

The operator UI in `console/` on :3002. It reaches the operator API through its
`/api/op` proxy only. It has no browser e2e and no `data-testid`, so the entries
name controls by their visible label and point to the component that owns the
copy. Reach it as in
[the console reference](../.agents/skills/verification/references/console.md).

### Console sign-in

Signs a staff member in to the console.

- **Reach**: `/login` with the console and operator API running.
- **Control**: fill **Email** and **Password** and submit; in development builds
  only, expand **Local dev** and submit a dev token.
- **Check**: the URL becomes `/tenants` and the shell navigation appears; a wrong
  credential shows an alert and keeps you on `/login`.
- **Traps**: the same secrets and dev-auth conditions as
  [Operator health and sign-in](#operator-health-and-sign-in). Workforce SSO is for
  deployed environments and is not a verification path. Error copy is owned by
  `console/app/login/page.tsx`.
- **Depends on**: pages `/login`, `/api/auth/*`, `/api/op/*`; operator API
  `POST /operator/auth/login`; files `console/app/login/page.tsx`.

### Console home

Summarises the fleet, items needing attention, connections and desk status.

- **Reach**: signed in; open `/`.
- **Control**: open **Home** in the rail.
- **Check**: the fleet snapshot, overview figures and attention list render from
  `GET /operator/v1/overview` and `GET /operator/v1/tenants`.
- **Traps**: an empty deployment shows empty states, not zeros.
- **Depends on**: pages `/`; operator API `GET /operator/v1/overview`,
  `GET /operator/v1/tenants`.

### Console tenants

Lists every onboarded institution and opens one for diagnostics.

- **Reach**: signed in; open `/tenants`. The detail page loads its ingestion and
  live-module figures only while a session you opened for that tenant is active
  (see [Console inspector](#console-inspector)).
- **Control**: filter with the **All**, **Live**, **Empty** and **Stale** toggles
  or the search box, use **Export CSV**, and open a row for `/tenants/{orgId}`.
- **Check**: the count line reads "N of M shown" and the tiles agree with
  `GET /operator/v1/tenants`; the detail page shows jurisdiction, currency,
  licence, ingestion and live-module figures for that tenant.
- **Traps**: with no tenants the table shows its empty state. Diagnostics traps are
  in [Tenant diagnostics and remediation](#tenant-diagnostics-and-remediation).
- **Depends on**: pages `/tenants`, `/tenants/{orgId}`; operator API
  `GET /operator/v1/tenants`.

### Console onboarding

Provisions a tenant through a form, a review step and a result panel.

- **Reach**: signed in; open `/onboard`.
- **Control**: fill every required field (jurisdiction prefills the currency and
  institution type is a closed list), then **Review**, then **Provision tenant**.
- **Check**: the "Provisioning steps" panel reports each step, then the result
  shows the new `BK-` and `OR-` identifiers; the one-time password stays hidden
  until revealed.
- **Traps**: nothing is created until the review step is confirmed. Do not reveal
  the password in a screenshot. Bank key fields become required when the operator
  health read reports `bank_key_required` (`console/lib/onboarding.ts`). The saga
  traps are in [Tenant provisioning](#tenant-provisioning).
- **Depends on**: pages `/onboard`; operator API `POST /operator/v1/tenants`; files
  `console/lib/onboarding.ts`.

### Console inspector

Starts and ends audited inspection sessions and opens a tenant's dashboard.

- **Reach**: signed in with inspector authority; open `/admin/inspector`.
- **Control**: click **Start session**, choose the **Organization** (it also fixes
  the examination bank), fill **Reason** and **Support reference**, choose **Mode**
  and **Session duration**, and submit the dialog. On that tenant's page click
  **Open bank dashboard**, then **Open in new tab**. Back on `/admin/inspector`,
  set the session-state filter to active only and click **End** on the row.
- **Check**: the active-sessions tile and list include the session; the dashboard
  opens at `/inspect`; after **End** it leaves the active list. The audit log
  records `inspector.session.start` and `inspector.session.end`.
- **Traps**: break-glass mode needs staff administration authority. Pause at the
  button's navigation call and keep the token and URL fragment out of evidence, as
  the console reference describes. API traps are in
  [Inspector sessions](#inspector-sessions).
- **Depends on**: pages `/admin/inspector`; operator API
  `POST /operator/v1/inspector/sessions`, `GET /operator/v1/inspector/sessions`; files
  `console/components/admin/InspectorView.tsx`,
  `console/components/tenants/OpenBankDashboardButton.tsx`.

### Console operators

Manages staff accounts.

- **Reach**: sign in with authority to administer staff accounts and open `/admin/operators`.
- **Control**: **Create operator**, fill **Email**, **Display name** and **Role**,
  and submit; each row offers **Reset** and **Deactivate** or **Reactivate**, each
  confirmed in a dialog.
- **Check**: the new account lists with its role and active state; create and reset
  each show a one-time password dialog; the audit log records `operators.create`,
  `operators.reset_password`, `operators.deactivate` and `operators.reactivate`.
- **Traps**: staff without account administration authority see the access explainer.
  Rank rules also limit which accounts an administrator may change, as in
  [Operator accounts](#operator-accounts).
- **Depends on**: pages `/admin/operators`; operator API `GET /operator/v1/operators`;
  files `console/components/admin/OperatorsView.tsx`.

### Console regulatory parameters

Proposes and approves governed parameters.

- **Reach**: signed in as two different operators; open
  `/admin/regulatory-parameters`.
- **Control**: as the first operator click **Propose parameter**, fill the form
  (what it applies to, parameter code, jurisdiction, value, unit, effective date,
  source and reason) and submit the proposal. As the second operator open the
  proposal under "Awaiting approval", click **Review and approve** and confirm.
  The form fields are in `console/components/admin/RegulatoryParametersView.tsx`.
- **Check**: the proposal leaves "Awaiting approval" and the register shows it in
  force from its effective date; the audit log records
  `regulatory_parameter.propose` and `regulatory_parameter.approve`.
- **Traps**: the proposer sees "Needs a second operator" on their own row instead
  of an approve button. Approval enqueues live refreshes, so a worker is needed for
  downstream recompute but not for the state change.
- **Depends on**: pages `/admin/regulatory-parameters`; operator API
  `GET /operator/v1/regulatory-parameters`; files
  `console/components/admin/RegulatoryParametersView.tsx`.

### Console audit log

Shows the operator action trail.

- **Reach**: sign in with staff administration authority and open `/admin/audit`.
- **Control**: filter by **Operator email**, **Target org** or **Action prefix**
  and click **Apply**; page with **Prev** and **Next**.
- **Check**: filtering by the prefix of an action you caused, such as
  `tenants.provision`, lists it with its actor and time.
- **Traps**: mutating calls write here, so entries from other cases on the same
  database are expected, and reading the log writes an `audit.read` entry.
- **Depends on**: pages `/admin/audit`; operator API `GET /operator/v1/audit`; files
  `console/components/admin/AuditView.tsx`.

### Console markets desk

Works through determinations, curves, observations and publications.

- **Reach**: use separate maker and checker accounts with desk access, and open
  `/desk/determinations`.
- **Control**: as the maker click **New determination**, choose the date, open the
  draft, compute it and submit it for review. As the checker open the submitted
  determination, approve it and publish it. Inspect inputs and results under
  **Curves**, **Observations** and **Publications**.
- **Check**: a determination moves from draft through pending review and approved
  to published, and a published one is listed under `/desk/publications`. The audit
  log records `desk.determination.*` actions.
- **Traps**: the draft needs an approved methodology and observations on or before
  the date. Publishing writes shared data, so use the disposable database; the
  other traps are in [Markets desk determinations](#markets-desk-determinations).
- **Depends on**: pages `/desk/*`; operator API
  `POST /operator/v1/fx-forward/construct`, `/operator/v1/desk/*`,
  `/operator/v1/curves/*`; files
  `console/app/(shell)/(desk)/desk/determinations/page.tsx`.

### Console operations

Shows data engines, connections and background jobs.

- **Reach**: signed in; open `/operations`.
- **Control**: open **Operations** in the rail; filter the connections table by
  organization, engine and status.
- **Check**: the tiles, connections and jobs agree with
  `GET /operator/v1/overview`, `GET /operator/v1/data-engines` and
  `GET /operator/v1/jobs`; a stuck job is a job row with a non-terminal status and
  a rising attempts count.
- **Traps**: job progress depends on the worker running.
- **Depends on**: pages `/operations`; operator API `GET /operator/v1/overview`,
  `GET /operator/v1/data-engines`, `GET /operator/v1/jobs`,
  `GET /operator/v1/worker-health`.

## Bank dashboard

The authenticated bank product UI in `backend/dashboard/` on :3001. Every figure
comes from the tenant API through the generated client. Reach it as in
[the dashboard reference](../.agents/skills/verification/references/dashboard.md);
routes and what they read are in
[the dashboard README](../backend/dashboard/README.md#scope). Controls are named by
the accessible role and name the Playwright specs under `backend/dashboard/tests/`
already use; the shared helpers are in `backend/dashboard/e2e/support/`. Each module
entry names the response fields its screen figures are compared with.

### Dashboard sign-in

Signs a bank user in and ends the session.

- **Reach**: `/login` on the interactive stack; the hermetic stack mints a
  signed-in state per fixture role and skips this form.
- **Control**: fill **Email** and **Password** and click **Sign in**; to sign out,
  open the account button in the header and choose **Sign out**. The exact steps
  are in `backend/dashboard/tests/session/session-lifecycle.e2e.spec.ts`.
- **Check**: the URL becomes `/` and the account button shows the user; after
  sign-out the URL is `/login` and replaying the old refresh token to
  `POST /auth/refresh` answers `401`.
- **Traps**: an unauthenticated visit to a protected route redirects to `/login`.
  Use the same loopback origin throughout to keep the browser session. The
  credential traps are in [Tenant API sign-in](#tenant-api-sign-in).
- **Depends on**: pages `/login`, `/api/auth/*`; tenant API `POST /auth/login`,
  `POST /auth/refresh`; files `backend/dashboard/e2e/support/mint.ts`,
  `backend/dashboard/tests/session/session-lifecycle.e2e.spec.ts`.

### Dashboard overview

Summarises cross-module posture, live-engine freshness and the filing calendar.

- **Reach**: signed in; open `/`.
- **Control**: open **Command Center** in the sidebar; each module tile is a link
  named for its module.
- **Check**: with data, each module tile shows its live status from
  `GET /banks/{bank_id}/live-summary`; without data the page shows its "No computed
  data yet" state with a link to the Data Engine.
- **Traps**: the empty state is correct for a bank that has not ingested data, as
  in [Live summary, freshness and alerts](#live-summary-freshness-and-alerts).
  Historical reporting periods are optional for daily work.
- **Depends on**: pages `/`; tenant API `GET /banks/{bank_id}/analytics/window`,
  `GET /banks/{bank_id}/live-snapshots`, `GET /banks/{bank_id}/live-summary`; files
  `backend/dashboard/features/home/routes/command-center.tsx`.

### Dashboard insights

Shows the platform's computed statements for one reporting date and the analysis
catalogue the reader may use.

- **Reach**: `BI_ENABLED` on the API and BI grants for the signed-in user; open
  `/insights`.
- **Control**: change the date in the filter bar.
- **Check**: the statements equal the response of
  `GET /banks/{bank_id}/bi/insights?as_of=<date>`, and the catalogue lists only what
  `GET /banks/{bank_id}/bi/catalogue` allows the reader.
- **Traps**: with BI disabled the page shows its not-available state and the API
  answers `404`, as in [Business intelligence](#business-intelligence). A date with
  nothing computed says so rather than showing zeros.
- **Depends on**: pages `/insights`; tenant API `GET /banks/{bank_id}/bi/insights`,
  `GET /banks/{bank_id}/bi/catalogue`.

### Dashboard module navigation

Moves between a module's tabs and between modules.

- **Reach**: signed in; any module route.
- **Control**: click a link in the "Module sections" navigation, or call
  `openTab(page, "<tab name>")` from
  `backend/dashboard/e2e/support/navigation.ts`; move between modules with the
  sidebar, or open the command palette with `Control+k`.
- **Check**: the URL becomes the tab's route and the page heading changes.
- **Traps**: a first compile of a route can outlast a short wait; wait for the
  destination URL before asserting figures. A tab the user has no grant for is
  absent, and a module without a grant shows an access notice, not an error.
- **Depends on**: files `backend/dashboard/e2e/support/navigation.ts`,
  `backend/dashboard/components/shell/ModuleTabs.tsx`.

### Dashboard liquidity

Shows the LCR, NSFR, buffer, monitoring, early warnings, contingency plan and
liquidity stress.

- **Reach**: signed in with liquidity authority; a loaded book for figures.
- **Control**: open `/liquidity`, then the **NSFR**, **Buffer**, **Monitoring**,
  **Stress** and forecast tabs.
- **Check**: against `GET /banks/{bank_id}/liquidity/dashboard`, the LCR tile equals
  `metrics.lcr_pct`, HQLA equals `metrics.hqla_total_ghs` and 30-day net outflows
  equals `metrics.net_outflows_30d_ghs`; on the NSFR tab the NSFR tile equals
  `metrics.nsfr_pct` and available and required stable funding equal
  `metrics.asf_total_ghs` and `metrics.rsf_total_ghs`. The tiles are asserted in
  `backend/dashboard/tests/liquidity/liquidity-risk-workflows.e2e.spec.ts`.
- **Traps**: no data shows an unavailable state, never zero, as in
  [Module dashboards](#module-dashboards). Ratios are tested against governed
  thresholds, not literals.
- **Depends on**: pages `/liquidity/*`; tenant API
  `GET /banks/{bank_id}/analytics/cashflow-window`,
  `GET /banks/{bank_id}/cashflow-forecast`, `GET /banks/{bank_id}/cashflow-history`,
  `GET /banks/{bank_id}/liquidity-haircuts`, `GET /banks/{bank_id}/liquidity/dashboard`,
  `GET /banks/{bank_id}/liquidity-monitoring`; files
  `backend/dashboard/tests/liquidity/liquidity-risk-workflows.e2e.spec.ts`.

### Dashboard Basel capital

Shows risk-weighted assets, capital structure, adequacy and capital stress.

- **Reach**: signed in with capital authority.
- **Control**: open `/basel`, then `/basel/rwa`, `/basel/structure`, `/basel/stress`
  and `/basel/planning`.
- **Check**: against `GET /banks/{bank_id}/capital/dashboard`, the capital adequacy,
  Tier 1, CET1 and leverage tiles equal `metrics.car_pct`, `metrics.tier1_ratio_pct`,
  `metrics.cet1_ratio_pct` and `metrics.leverage_ratio_pct`, and total RWA equals
  `metrics.total_rwa_ghs`; on `/basel/rwa` the credit, market and operational RWA
  tiles equal `credit_rwa_ghs`, `market_rwa_ghs` and `operational_rwa_ghs` from
  `GET /banks/{bank_id}/capital/rwa`. The tiles are asserted in
  `backend/dashboard/tests/basel/basel-risk-workflows.e2e.spec.ts`.
- **Traps**: the bank's booked allowance is the capital figure of record; modelled
  expected loss is a what-if and is never substituted in Tier 2. A ratio whose
  minimum is not on file shows no compliance claim.
- **Depends on**: pages `/basel/*`; tenant API `GET /banks/{bank_id}/capital-plan`,
  `GET /banks/{bank_id}/capital/dashboard`, `GET /banks/{bank_id}/capital/rwa`,
  `GET /banks/{bank_id}/capital/structure`; files
  `backend/dashboard/tests/basel/basel-risk-workflows.e2e.spec.ts`.

### Dashboard interest-rate risk

Shows repricing gap, EVE and earnings sensitivity, limits and scenarios.

- **Reach**: signed in with IRR authority.
- **Control**: open `/irr`, then `/irr/gaps`, `/irr/sensitivity`, `/irr/limits` and
  `/irr/scenarios`.
- **Check**: against `GET /banks/{bank_id}/irr/dashboard`, the worst ΔEVE tile equals
  `metrics.worst_eve_change_pct_tier1`, the duration gap equals
  `metrics.duration_gap` and the ±200bp NII tile equals `metrics.ear_up_200_ghs` and
  `metrics.ear_down_200_ghs`; on `/irr/sensitivity` the ΔNII tiles equal `ear_up`
  and `ear_down` from `GET /banks/{bank_id}/irr/ear-analysis` for the same horizon.
  The tiles are asserted in
  `backend/dashboard/tests/irr/irrbb-risk-workflows.e2e.spec.ts`.
- **Traps**: a stress input outside the supported boundaries is refused rather than
  computed; that is the expected result. The desk-horizon read is a separate
  computation from the stored regulatory figure.
- **Depends on**: pages `/irr/*`; tenant API `GET /banks/{bank_id}/irr/dashboard`,
  `GET /banks/{bank_id}/irr/ear-analysis`; files
  `backend/dashboard/tests/irr/irrbb-risk-workflows.e2e.spec.ts`.

### Dashboard FX

Shows net open position, value at risk, hedges and forwards.

- **Reach**: signed in with FX authority.
- **Control**: open `/fx`, then `/fx/var`, `/fx/hedges`, `/fx/forwards`, `/fx/limits`
  and `/fx/scenarios`.
- **Check**: against `GET /banks/{bank_id}/fx/dashboard`, the aggregate NOP tile
  equals `metrics.nop_pct_tier1`, net open position equals `metrics.nop_ghs` and the
  largest single currency equals `metrics.single_ccy_max_pct`; on `/fx/var` the
  portfolio VaR equals `metrics.var_99_1d_ghs` and stressed VaR equals
  `metrics.stressed_var_ghs`. The tiles are asserted in
  `backend/dashboard/tests/fx/fx-risk-workflows.e2e.spec.ts`.
- **Traps**: without a loaded book or the market data a figure needs, the screen
  shows that figure as unavailable, never as zero, as in
  [Module dashboards](#module-dashboards).
- **Depends on**: pages `/fx/*`; tenant API `GET /banks/{bank_id}/fx/dashboard`; files
  `backend/dashboard/tests/fx/fx-risk-workflows.e2e.spec.ts`.

### Dashboard funds transfer pricing

Shows the transfer curve and product and branch profitability.

- **Reach**: signed in with FTP authority.
- **Control**: open `/ftp`, then `/ftp/products`, `/ftp/lines`, `/ftp/rules`,
  `/ftp/scenarios` and `/ftp/expost`.
- **Check**: against `GET /banks/{bank_id}/ftp/dashboard`, on `/ftp` the blended
  assigned FTP, portfolio NIM, weighted asset yield and weighted funding credit
  tiles equal `metrics.blended_assigned_ftp_pct`, `metrics.portfolio_nim_pct`,
  `metrics.weighted_asset_yield_pct` and `metrics.weighted_funding_credit_pct`; on
  `/ftp/products` the products-below-floor tile equals
  `metrics.products_below_min_margin` of `metrics.total_products`. The tiles are
  asserted in `backend/dashboard/tests/ftp/ftp-risk-workflows.e2e.spec.ts`.
- **Traps**: without a loaded book the pages show their empty state, as in
  [Module dashboards](#module-dashboards).
- **Depends on**: pages `/ftp/*`; tenant API `GET /banks/{bank_id}/ftp/dashboard`; files
  `backend/dashboard/tests/ftp/ftp-risk-workflows.e2e.spec.ts`.

### Dashboard forecasting

Projects the balance sheet and runs scenarios, what-ifs and reverse stress.

- **Reach**: signed in with Forecasting Run authority, a selected reporting period
  and governed assumptions in place.
- **Control**: open `/forecasting` and click **Run forecast**, then open
  `/forecasting/assumptions`, `/forecasting/nii`, `/forecasting/scenario`,
  `/forecasting/whatif` and `/forecasting/optimizer`. On `/forecasting/reverse-stress`
  click **Run reverse stress**.
- **Check**: the run lists under `GET /banks/{bank_id}/forecast/runs`, and its
  year-5 CAR and average ROE equal `summary.year5_car_pct` and `summary.avg_roe_pct`
  from `GET /banks/{bank_id}/forecast/runs/{run_id}`. Compare `liquidity_axis` and
  `capital_axis` independently against `GET /banks/{bank_id}/reverse-stress/latest`.
  A breached axis with a multiplier shows a `reverse-stress-breach-value` marker
  with its `breach_multiplier` formatted to two decimals followed by ×. Otherwise
  the axis has no marker and shows that it holds to the search limit.
- **Traps**: a book that cannot be forecast shows a not-computable state, which is
  the expected result. Reverse stress also needs financial facts for the period and
  configured liquidity and capital stress scenarios.
- **Depends on**: pages `/forecasting/*`; tenant API
  `GET /banks/{bank_id}/forecast/runs`, `GET /banks/{bank_id}/forecast/runs/{run_id}`,
  `POST /banks/{bank_id}/forecast/runs`, `POST /banks/{bank_id}/reverse-stress/runs`,
  `GET /banks/{bank_id}/reverse-stress/latest`; test ids `reverse-stress-frontier`,
  `reverse-stress-headlines`, `reverse-stress-breach-value`.

### Dashboard credit

Shows asset quality, delinquency, concentration, vintages and activity.

- **Reach**: signed in with credit authority.
- **Control**: open `/credit`, then `/credit/book`, `/credit/delinquency`,
  `/credit/concentration`, `/credit/vintages` and `/credit/activity`.
- **Check**: against `GET /banks/{bank_id}/credit/dashboard`, the gross loan book,
  NPL ratio, NPL exposure and provision coverage equal `metrics.gross_loans_ghs`,
  `metrics.npl_ratio_pct`, `metrics.npl_exposure_ghs` and
  `metrics.provision_coverage_pct`, and PAR 30+ equals the `par_30` item of
  `metrics.portfolio_at_risk` times 100; on `/credit/concentration` the book total
  equals `total_book_ghs` and each HHI equals `dimensions[].hhi` from
  `GET /banks/{bank_id}/credit/concentration`.
- **Traps**: credit register access is scoped by grant. Compare the visible
  register with the signed-in account's grant scope.
- **Depends on**: pages `/credit/*`; tenant API `GET /banks/{bank_id}/credit/dashboard`,
  `GET /banks/{bank_id}/credit/concentration`, `GET /banks/{bank_id}/credit/loans`.

### Dashboard markets

Shows published curves, rates, FX and indices with source and freshness details.

- **Reach**: sign in with Markets published-view authority and open `/markets`.
- **Control**: select the **Curves** or **Market data** tab and set the **As of**
  date.
- **Check**: the request carries `as_of` for the chosen date and the response
  `as_of_date` equals it; each curve's `curve_name` and its `points[].rate` per
  `tenor_months` match the Curves tab, and the source chip agrees with
  `attribution.source_system` and `attribution.stale` from
  `GET /banks/{bank_id}/market-data/views`.
- **Traps**: a date without published coverage can show an empty view.
- **Depends on**: pages `/markets`; tenant API `GET /banks/{bank_id}/market-data/views`.

### Dashboard behavioral models

Shows model estimates and training coverage for the bank's behavioral assumptions.

- **Reach**: sign in with Behavioral aggregated-view authority and open `/behavioral`.
- **Control**: open **NMD Duration**, **Loan Prepayment** or **Deposit Stability**.
- **Check**: against `GET /banks/{bank_id}/behavioral/{model}` (camelCase keys), the
  training-data tile equals `accuracy.sampleCount`, its months and method equal
  `accuracy.monthCoverage` and `method`, the holdout error equals `accuracy.cvRmse`,
  and the average tile equals the mean of `products[].value`.
- **Traps**: a bank without enough ingested history may have no trained estimate,
  and the first request can show a training state before figures.
- **Depends on**: pages `/behavioral/*`; tenant API
  `GET /banks/{bank_id}/behavioral/{model}`.

### Dashboard reports

Collects board packs, saved analyses and persisted regulatory runs.

- **Reach**: sign in, select a bank with fresh, available live evidence and open
  `/reports`.
- **Control**: open **Board pack** and click **Print / Save as PDF**.
- **Check**: each executive-summary tile equals the headline metric of its module in
  `GET /banks/{bank_id}/live-summary` (`lcr_pct`, `car_pct`, `npl_ratio_pct`,
  `worst_eve_change_pct_tier1`, `nop_pct_tier1`, `portfolio_nim_pct`), its source
  line equals `source_as_of_date`, `calculation_generation` and `computed_at`, and
  printing opens the print dialog. The spec is
  `backend/dashboard/tests/reports/board-pack.spec.tsx`.
- **Traps**: stale evidence withholds figures until recomputation. Printing can
  remain enabled if the stale response still contains modules.
- **Depends on**: pages `/reports/*`; tenant API
  `GET /banks/{bank_id}/reports/comparison`, `GET /banks/{bank_id}/submissions/bsd2`,
  `GET /banks/{bank_id}/submissions/bsd3`, `GET /banks/{bank_id}/live-summary`,
  `GET /banks/{bank_id}/regulatory-runs`; files
  `backend/dashboard/tests/reports/board-pack.spec.tsx`.

### Dashboard positions

Shows the canonical position book behind every module.

- **Reach**: signed in with data authority; a loaded book.
- **Control**: open `/positions` and filter by search text, position type or
  currency, then page the blotter.
- **Check**: the positions count equals `total` from
  `GET /banks/{bank_id}/canonical-positions/facets`; with a filter applied the
  matching count and the footer equal `total` from
  `GET /banks/{bank_id}/canonical-positions` for the same filter. The screen shows
  row counts, not monetary totals.
- **Traps**: an empty book shows an empty table.
- **Depends on**: pages `/positions`; tenant API `GET /lineage/{lineage_id}`,
  `GET /banks/{bank_id}/canonical-positions`,
  `GET /banks/{bank_id}/canonical-positions/facets`.

### Dashboard scenario workbench

Lists each module's stress scenario definitions and its stored scenario results.

- **Reach**: signed in with the module's authority and a selected reporting period;
  open `/irr/scenarios`.
- **Control**: open the scenario page of a module: `/irr/scenarios`,
  `/fx/scenarios`, `/ftp/scenarios`, `/basel/stress` or `/liquidity/stress`.
- **Check**: the scenario definitions and stored results agree with
  `GET /banks/{bank_id}/scenario-workbench/{module}/scenarios` and
  `GET /banks/{bank_id}/scenario-workbench/{module}/analyses`.
- **Traps**: reading the registry creates no runs. A module the user lacks run
  authority for shows its notice instead of a run control.
- **Depends on**: pages `/irr/scenarios`, `/fx/scenarios`, `/ftp/scenarios`,
  `/basel/stress`, `/liquidity/stress`; tenant API
  `GET /banks/{bank_id}/scenario-workbench/{module}/scenarios`,
  `GET /banks/{bank_id}/scenario-workbench/{module}/analyses`.

### Dashboard enterprise stress

Runs an approved macro scenario against a bank's book and shows capital and
liquidity results, the run registry and management actions.

- **Reach**: signed in with stress view and run authority and IRR confidential run
  authority, a reporting period with an ingested book; open `/irr/enterprise-stress`.
- **Control**: in the scenario-and-run tab select an approved scenario, enter the run
  reason and click **Run enterprise stress**; open the run registry.
- **Check**: the results tab shows the stressed capital adequacy and its erosion; the
  run lists in `GET /banks/{bank_id}/enterprise-stress/runs` and reopens from the
  registry.
- **Traps**: only an approved scenario can be selected and its creator cannot approve
  it (`409`). Sign-off attestation needs SSO step-up, so skip it locally.
- **Depends on**: pages `/irr/enterprise-stress`; tenant API
  `POST /banks/{bank_id}/enterprise-stress/runs`,
  `GET /banks/{bank_id}/enterprise-stress/runs`, `POST /macro-scenarios`,
  `POST /management-action-plans`.

### Dashboard Data Engine upload

Uploads a spreadsheet or CSV and reviews the batch it creates.

- **Reach**: signed in with upload and batch-run authority, plus mapping
  activation authority if needed. Start local MinIO and open `/data-engine/excel-csv`.
- **Control**: on a mapping template click **Activate mapping**; in the "Upload &
  ingest" section choose the source file and the as-of date, click **Upload &
  ingest**, then open the batch from the **Batch detail** link. The steps and
  helpers are in `backend/dashboard/tests/data-engine/data-engine-ingestion.e2e.spec.ts`.
- **Check**: the outcome card shows the batch state and its extracted, translated,
  accepted and error counts, and `/data-engine/batches/{batchId}` lists the
  validation findings and untranslatable rows with row references. The synthetic
  fixture and its expected counts are in `backend/dashboard/tests/data-engine/fixtures/`.
- **Traps**: the upload and format traps are in
  [Data Engine upload](#data-engine-upload). Use synthetic files only.
- **Depends on**: pages `/data-engine/excel-csv`, `/data-engine/batches/{batchId}`;
  tenant API `POST /banks/{bank_id}/ingestion-uploads`,
  `POST /banks/{bank_id}/ingestion-batches`; files
  `backend/dashboard/tests/data-engine/data-engine-ingestion.e2e.spec.ts`.

### Dashboard Data Engine connections

Configures API push, database-direct, core-banking adapter and market-data loads.

- **Reach**: signed in with data authority.
- **Control**: open `/data-engine/api`, `/data-engine/database`, `/data-engine/t24`, `/data-engine/adapters` or
  `/data-engine/market-data`.
- **Check**: each page shows its connection state and the guide or form for that
  route; the overview at `/data-engine` lists recent ingestion across sources.
- **Traps**: a reader without the grant sees a restricted notice. Reconciliation
  exceptions follow checker independence: the maker cannot approve their own
  proposal.
- **Depends on**: pages `/data-engine/*`; tenant API
  `POST /banks/{bank_id}/database-direct/connections`,
  `POST /banks/{bank_id}/temenos/connections`, `GET /market-data/templates/{kind}`; test
  ids `market-data-connections-restricted`.

### Dashboard reconciliation exceptions

Proposes, approves and revokes bounded reconciliation exceptions.

- **Reach**: two different signed-in users, a proposer with Data create authority and
  an approver with Data approve authority; open `/data-engine`.
- **Control**: as the proposer fill the reason, the maximum gap fraction and the dates
  and click **Record proposal**. As the approver enter an approval label and click
  **Approve exception**, or revoke with a reason.
- **Check**: the row moves from pending to approved or revoked and appears in
  `GET /banks/{bank_id}/reconciliation/exceptions`.
- **Traps**: the proposer cannot approve their own proposal (the API answers `409`).
  A pending exception never changes a filing gate, and a gap above the ceiling still
  blocks.
- **Depends on**: pages `/data-engine`; tenant API
  `GET /banks/{bank_id}/reconciliation/exceptions`,
  `POST /banks/{bank_id}/reconciliation/exceptions`,
  `POST /banks/{bank_id}/reconciliation/exceptions/{exception_id}/approve`,
  `POST /banks/{bank_id}/reconciliation/exceptions/{exception_id}/revoke`.

### Dashboard submissions

Prepares, validates, certifies and files regulatory returns.

- **Verification**: dashboard filing requires a local sandbox because the hand-off can transmit to a real channel. This map skips the filing run.
- **Depends on**: pages `/submissions/*`.

### Dashboard reporting calendar and templates

Shows the regulatory reporting obligations and the return templates.

- **Reach**: signed in with a bank; open `/submissions/calendar` and
  `/submissions/templates`.
- **Control**: open both pages and page through the obligations.
- **Check**: the overdue, due-soon and on-track counts and the rows agree with
  `GET /banks/{bank_id}/reporting-obligations`; the templates page lists
  `GET /regulatory-reporting/templates`.
- **Traps**: dates come from the return definitions, never from the bank's reporting
  periods. Reading these pages files and transmits nothing.
- **Depends on**: pages `/submissions/calendar`, `/submissions/templates`; tenant API
  `GET /banks/{bank_id}/reporting-obligations`, `GET /regulatory-reporting/templates`.

### Dashboard ICAAP

Builds the internal capital adequacy assessment in a per-cycle workspace.

- **Reach**: signed in with ICAAP authority; open `/icaap`.
- **Control**: create or open a cycle, then work through its overview, risks,
  appetite, stress, Pillar 2, sections, attachments, review and filing pages.
- **Check**: each page for the cycle at `/icaap/{cycleId}/...` shows its content
  and the filing page shows its chain and omissions.
- **Traps**: when signing is not configured, filing shows a suspended notice
  (`data-testid` `icaap-signing-suspended`).
- **Depends on**: pages `/icaap/*`; tenant API `/banks/{bank_id}/icaap/cycles/*`; test
  ids `icaap-signing-suspended`.

### Dashboard institution profile

Shows the institution's legal identity, authorisation, ownership and registers.

- **Reach**: signed in; open `/institution`.
- **Control**: open the profile, then outlets, products and licences, parties,
  registers and name history.
- **Check**: against `GET /banks/{bank_id}/institution-profile`, institution type,
  legal entity structure, authorisation date, approved capital and local and
  foreign ownership equal `profile.institution_type`,
  `profile.legal_entity_structure`, `profile.authorisation_date`,
  `profile.approved_capital`, `profile.ownership_local_pct` and
  `profile.ownership_foreign_pct`; tax and registration identifiers appear only when
  `restricted_visible` is true.
- **Traps**: the screen does not show the platform ID, which is the token used in
  API paths and in the push contract; read it from the request path.
- **Depends on**: pages `/institution/*`; tenant API
  `GET /banks/{bank_id}/institution-profile`.

### Dashboard institution registers

Adds and edits outlets, products, licences, related parties and name history.

- **Reach**: signed in with Institution configure authority for the bank; open
  `/institution/outlets`, `/institution/products`, `/institution/parties` or
  `/institution/history`.
- **Control**: click the page's add button, fill the synthetic details and a reason,
  and save.
- **Check**: the new row appears in the register and in
  `GET /banks/{bank_id}/institution-profile`, and the change is audited.
- **Traps**: every write needs a reason or the API refuses it. Related-party rows are
  personal data, so use invented names.
- **Depends on**: pages `/institution/outlets`, `/institution/products`,
  `/institution/parties`, `/institution/history`; tenant API
  `POST /banks/{bank_id}/outlets`, `POST /banks/{bank_id}/products`,
  `POST /banks/{bank_id}/related-parties`, `POST /banks/{bank_id}/licenses`,
  `POST /banks/{bank_id}/name-history`.

### Dashboard Board registers

Shows and edits the Board-adopted liquidity and credit registers with approval
evidence.

- **Reach**: signed in; open `/institution/registers`. Cards render only for
  principals with the module's view authority, and editing needs configure authority.
- **Control**: on a register card click **Edit register**, change a value, fill the
  effective date, approver and reason, and click **Record generation**.
- **Check**: the card lists the new generation with its approver and effective date,
  and `GET /banks/{bank_id}/liquidity-thresholds?as_of=<date>` returns it as the
  active generation.
- **Traps**: a view-only principal sees a disabled edit control with its reason.
  Capital authority is refused on the credit registers. Values travel as strings to
  keep decimal precision.
- **Depends on**: pages `/institution/registers`; tenant API
  `GET /banks/{bank_id}/liquidity-thresholds`,
  `PUT /banks/{bank_id}/liquidity-thresholds`, `GET /banks/{bank_id}/credit-thresholds`,
  `GET /banks/{bank_id}/crm-haircuts`, `GET /banks/{bank_id}/ecl-assumptions`,
  `GET /banks/{bank_id}/concentration-limits`.

### Dashboard explore

Builds ad hoc questions and views over the book.

- **Reach**: BI enabled on the API and BI grants for the signed-in user. Open
  `/explore`. For **Ask a question**, also enable `BI_NLQ_ENABLED` on the API.
- **Control**: tick a **Measure** and a **Break down by** option, switch to the full
  grid, or open **Export** and pick a format. For **Ask a question**, a tab that is
  absent unless `BI_NLQ_ENABLED` is on, enter a question, click **Work out my
  question**, review the proposal and click **Yes, run this question**. The steps and
  fixtures are in `backend/dashboard/tests/bi/bi-explore.e2e.spec.ts` and
  `backend/dashboard/tests/bi/bi-ask.e2e.spec.ts`.
- **Check**: the grid rows reconcile with the source figures from
  `GET /banks/{bank_id}/bi/*`, and a confirmed question shows its answer heading.
- **Traps**: with BI disabled the API's router-level feature gate returns `404`, as
  in [Business intelligence](#business-intelligence). Disabling `BI_NLQ_ENABLED`
  hides **Ask a question**.
- **Depends on**: pages `/explore/*`, `/dashboards/*`; tenant API
  `/banks/{bank_id}/bi/*`; files `backend/dashboard/tests/bi/bi-explore.e2e.spec.ts`,
  `backend/dashboard/tests/bi/bi-ask.e2e.spec.ts`.

### Dashboard risk and alerts

Shows the limit wall, findings and pipeline alerts.

- **Reach**: signed in; open `/risk` or `/alerts`.
- **Control**: on `/risk` choose the limit wall or validation checks and filter the
  wall with the **All limits**, **Breaches** and **Amber** pills. On `/alerts` group
  the stream with **By module** or **By severity**.
- **Check**: on `/alerts`, start with the `items` returned by
  `GET /banks/{bank_id}/alerts?limit=200`. Keep alerts for modules whose rules are
  in force. When liquidity rules are out of force, exclude the forecast rules
  `year5_lcr_above_minimum` and `year5_nsfr_above_minimum`. From the resulting list,
  count all items, critical items, high items and distinct modules for the four
  tiles. The `/risk` limit counts come from the module dashboards.
- **Traps**: an empty filtered list shows an empty state. The response aggregates
  may include findings outside the returned page or the applicable rules.
- **Depends on**: pages `/risk`, `/alerts`; tenant API `GET /banks/{bank_id}/alerts`;
  files `backend/dashboard/lib/api/regulatoryRules.ts`.

### Dashboard members and grants

Lists members and grants or revokes scoped access.

- **Reach**: sign in with an active organization ownership binding and open
  `/access/members`. Account administration alone is insufficient.
- **Control**: on the member's row click **Add grant**, set the role bundle,
  institution coverage, module, sensitivity and reason fields, then **Review grant**,
  **Grant access** and **Done**. To revoke, open the member with **View**, click
  **Revoke this access** on the grant, fill the reason and confirm. The steps are in
  `backend/dashboard/tests/access/grant-administration.e2e.spec.ts`.
- **Check**: a "Grant created" notice appears with its summary sentence and the
  member's grant count rises; after revoke the revoked user's next call returns
  `401`.
- **Traps**: a conflicting grant turns **Review grant** into **Cannot be granted**;
  that is the separation-of-duties rule in
  [Access grants and members](#access-grants-and-members), not a fault. A member
  without owner authority is shown an access-denied page.
- **Depends on**: pages `/access/members`, `/access/my-access`,
  `/access/authentication`; tenant API `GET /organization/members`,
  `POST /authorization/bindings`; files
  `backend/dashboard/tests/access/grant-administration.e2e.spec.ts`.

### Dashboard integration keys

Issues and revokes data push keys.

- **Reach**: sign in with account administration authority and open `/access/integration-keys`.
- **Control**: type a **Key label**, click **Generate data push key**, then dismiss
  the one-time secret; click **Revoke** on a listed key and answer the browser prompt
  with a reason. The key is issued for the active bank, which is shown read-only.
  The steps are in
  `backend/dashboard/tests/integration-keys/integration-key-machine-principal.e2e.spec.ts`.
- **Check**: the secret shows once, the key then lists as active without it and a
  revoked key lists as revoked.
- **Traps**: keep the secret out of screenshots and captions; the key rules are in
  [Integration keys](#integration-keys).
- **Depends on**: pages `/access/integration-keys`; tenant API `GET /integration-keys`,
  `POST /integration-keys`; files
  `backend/dashboard/tests/integration-keys/integration-key-machine-principal.e2e.spec.ts`.

### Dashboard settings and appearance

Sets theme, profile and account preferences.

- **Reach**: signed in; open `/settings/profile`.
- **Control**: under appearance choose the **Light**, **Dark** or **System** radio.
  The steps are in `backend/dashboard/tests/settings/settings-shell.e2e.spec.ts`.
- **Check**: `<html>` carries `data-theme="light"` or `"dark"` to match the choice,
  and the choice survives a reload.
- **Traps**: with **System**, the theme follows the browser's `prefers-color-scheme`.
- **Depends on**: pages `/settings/*`; files
  `backend/dashboard/components/shell/ThemeProvider.tsx`,
  `backend/dashboard/tests/settings/settings-shell.e2e.spec.ts`.

### Dashboard tenant inspector

Opens a read-only dashboard for a staff inspection session.

- **Reach**: a staff-issued act token for an active session; see
  [Inspector sessions](#inspector-sessions) and
  [Console inspector](#console-inspector).
- **Control**: open the `/inspect` URL the console hands off.
- **Check**: the dashboard opens scoped to the inspected bank with the
  impersonation banner shown.
- **Traps**: the act token travels in the URL fragment; keep it out of evidence.
- **Depends on**: pages `/inspect`, `/api/impersonation/*`.

### Dashboard health

Reports whether the dashboard process is alive.

- **Reach**: the dashboard server is running.
- **Control**: `GET /api/health/live` on the dashboard origin, with no sign-in.
- **Check**: `200` with `{"service":"aequoros-dashboard","status":"ok"}`.
- **Traps**: it does not call the tenant API, so it stays `200` when the API is down.
  The API has its own [Tenant API health](#tenant-api-health) entry.
- **Depends on**: pages `/api/health/live`; files
  `backend/dashboard/app/api/health/live/route.ts`.

## Marketing site

The public site in `frontend/` on :3000. It is static content with a contact form
and has no authentication or tenant data. Reach it as in
[the marketing site reference](../.agents/skills/verification/references/marketing-site.md).

### Marketing pages

Presents the product, company, security, investors and contact pages.

- **Reach**: the dev server is running.
- **Control**: open `/`, `/product`, `/company`, `/security`, `/investors` and
  `/contact`; use the navigation links.
- **Check**: each route answers `200` and renders in both desktop and narrow
  viewports; `/sitemap.xml` and `/robots.txt` are served.
- **Traps**: the product pages show static captures, not live tenant data.
- **Depends on**: pages `/`, `/product`, `/company`, `/security`, `/investors`,
  `/contact`, `/sitemap.xml`, `/robots.txt`; files `frontend/components/Navigation.tsx`.

### Marketing contact form

Sends a demo request through the site's contact API.

- **Verification**: contact submission requires a local delivery fixture because it can email a real recipient. This map skips form submission.
- **Depends on**: pages `/contact`, `/api/contact/*`; files
  `frontend/components/ContactForm.tsx`.

## Not mapped

Groups the check finds that have no entry, each with the reason. Add an entry and
remove the line when a group gains a recipe that can run on a local stack.

### Tenant API

- `/cases`, `/assessments`, `/assessment-runs`, `/assessment-types`, `/documents`,
  `/findings`, `/risk-types`, `/taxonomies`: the retired legacy case vertical, kept
  only for old clients (see `backend/AGENTS.md`).
- `/banks`, `/banks/{bank_id}/reporting-periods`: thin reads behind the bank and
  period selectors that every dashboard entry uses.
- `/banks/{bank_id}/sdi`, `/banks/{bank_id}/loan-classification`: regime-specific
  panels inside the capital, liquidity and credit dashboards, shown only for the
  small deposit-taking regime.
- `/banks/{bank_id}/implied-rating`: needs a published operating-environment score
  and market inputs, so the recipe is not verifiable on a bare local stack.
- `/banks/{bank_id}/system-of-record`, `/banks/{bank_id}/system-of-record-assessment`:
  governance that needs two data officers and a duplicated-book diagnosis, with no
  dashboard surface.
- `/banks/{bank_id}/canonical-withdrawals`,
  `/banks/{bank_id}/canonical-withdrawal-scope`, `/banks/{bank_id}/position-snapshots`:
  data-retirement and override flows that change a loaded book; skipped rather than
  driven on a shared book.
- `/banks/{bank_id}/encryption-key`: connecting and rotating a bank key needs a key
  management service (see `backend/docs/bank_encryption.md`).
- `/banks/{bank_id}/examiner`: read only through an inspection session and needs
  completed official runs.
- `/banks/{bank_id}/filing-workflow-templates`, `/banks/{bank_id}/regulatory-reporting`,
  `/banks/{bank_id}/regulatory-artifacts`, `/banks/{bank_id}/regulatory-artifact-versions`,
  `/attestation`: filing channels, approval chains, signing and filed artifacts, which
  belong to the filing flow this map skips because it can transmit.
- `/notifications`: no route creates a notification; rows come only from reporting
  workflows, so a local recipe cannot produce one.
- `/jobs`, `/feature-flags`, `/storage`: polling, gating and signed-download
  endpoints used by other entries, with no standalone entry point.

### Staff operator API

- `operator-bank-encryption`: key custody operations require a key management service.
- `operator-reporting-gateway`: enrolment needs a running AequorOS Reporting Gateway
  (`gateway/`) and its operator client.
- `operator-bi`: backfill requires a bank with BI history and a worker to process it.
- `operator-activity`: the inspection-session feed combines records produced by
  other workflows.

### Bank dashboard

- `/api/attestation`, `/api/stress`: single sign-on step-up proxies for return
  certification and stress sign-off, which need an identity provider.

### Marketing site

- `/opengraph-image`: a generated share image with no content to verify.
