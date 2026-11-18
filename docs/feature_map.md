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
- **Depends on**: the routes, endpoints and selectors the entry relies on.

Entries that skip an operation give the reason under **Verification** and list
the dependencies separately.

An entry that describes work not yet merged is labelled `pending-until-merged`
at the start of its description. Headings are identifiers: rename one only
together with every reference to it.

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
- **Depends on**: `/api/health/live`, `/api/health/ready`.

### Tenant API sign-in

Exchanges a local account's credentials for an access token and reports the
caller's identity and authority.

- **Reach**: a local account exists (the provisioned administrator or a member
  granted scoped access).
- **Control**: `POST /auth/login` with `{"email", "password"}`; send the
  returned `access_token` as a bearer value. `POST /auth/refresh` rotates a
  session and `POST /auth/logout` revokes it.
- **Check**: `GET /auth/me` returns the caller; `GET /auth/effective-authority`
  returns the scoped grants the rest of the API enforces. A refresh token
  replayed after logout answers `401`.
- **Traps**: authority comes from scoped bindings, not the role label or token
  claims, so confirm effective authority for the bank and action before a
  capture. Pass credentials by environment variable and keep them out of
  evidence.
- **Depends on**: `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/me`,
  `/auth/effective-authority`.

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
- **Depends on**: `/banks/{bank_id}/live-summary`, `/freshness`, `/alerts`,
  `/refresh`, `/official-runs`.

### Module dashboards

Serves each risk module's computed headline payload.

- **Reach**: a signed-in principal holding the module's grant; a loaded book for
  populated results.
- **Control**: `GET /banks/{bank_id}/liquidity/dashboard`,
  `/capital/dashboard`, `/irr/dashboard`, `/fx/dashboard`, `/ftp/dashboard`,
  `/credit/dashboard`; scenario runs are `POST` to each module's
  `/run-all-scenarios`.
- **Check**: each answers with the module's payload for the live book, matching
  what its dashboard screen shows.
- **Traps**: "no data yet" or "not configured" answers `200` with
  `{"available": false, "error_code", "reason"}`, not a `4xx`. Treat that as the
  unavailable state to verify, not as an error.
- **Depends on**: the `/dashboard` and `/run-all-scenarios` routes above.

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
- **Depends on**: `POST /banks/{bank_id}/ingestion-uploads`,
  `POST /banks/{bank_id}/ingestion-batches`, `GET /banks/{bank_id}/ingestion-batches[/{batch_id}]` and
  `GET /banks/{bank_id}/ingestion-batches/{batch_id}/translation-failures`,
  plus `/ingestion-summary`, `/mapping-configs` and `/data-activations`.

### Data Engine API push

Loads data from an institution's middleware through three calls, running the
same pipeline as an upload.

- **Reach**: a data push integration key issued for the bank (see
  [Integration keys](#integration-keys)); the contract is
  [API_INTEGRATION.md](API_INTEGRATION.md).
- **Control**: `POST /banks/{bank_id}/push-batches` (open, with `as_of_date`,
  `idempotency_key`, `reason`), `POST .../{push_id}/records` (stage pages),
  `POST .../{push_id}/commit`; `GET .../{push_id}` reports staging status.
- **Check**: open answers `201`; commit answers `201` and yields an ingestion
  batch with `source_system` `API_PUSH`; reopening with the same key returns the
  same batch.
- **Traps**: a key works only on the push routes and the bank it was issued for;
  other routes answer `401` and another bank answers `404`. A human access token
  answers `403` here. Reusing a key with a different `as_of_date` is `409`.
- **Depends on**: `/banks/{bank_id}/push-batches` and its `records` and `commit`
  routes; `backend/scripts/ingest_push.py`.

### Regulatory returns

Generates, validates, certifies and files regulatory return packages.

- **Reach**: a bank with a computed live book and a return that supports
  `orass_sandbox`, sandbox and attestation signing configured, and distinct
  authorized preparer, approver and validator accounts.
- **Control**: as preparer, pick a date with `GET /banks/{bank_id}/return-anchors?return_code={return_code}`, then send
  `return_code` and `reporting_date` to `POST /banks/{bank_id}/regulatory-packages`
  and run `validate`. Each required signer calls `attestation/step-up` with
  `signing_role` and their step-up proof, then `attestation/certify` with the
  same `signing_role`, returned `authorization_token` and returned
  `certification_digest` as `expected_certification_digest`. Preparer
  certification starts the chain and approver certification records approval.
  Follow `workflow/decisions` for any remaining review decisions. Use
  `workflow/hand-off` only to advance stages whose `transmit_on_approve` is
  false, such as handing off from the approver to the validator. Once approved
  and fully certified, file as validator directly through `submit` with
  `{"channel":"orass_sandbox"}`. The transmitting stage's hand-off uses the
  registry default, so skip that hand-off in this recipe.
- **Check**: a package reports its validation results, approval workflow state
  and filed artifacts; its version chain and submission events are readable.
- **Traps**: reporting dates come from the return definition, not the bank's
  reporting periods. Without signing configured the API logs that no return can be
  certified or filed; that is the unconfigured state, not a fault. Verify filing
  only against the simulated channel.
- **Depends on**: `/banks/{bank_id}/regulatory-packages[/{package_id}]` and its
  `validate`, `workflow`, `attestation/step-up`, `attestation/certify` and
  `submit` routes, `/return-anchors`, `/regulatory-runs`.

### Access grants and members

Lists the organization's members and administers scoped access grants.

- **Reach**: an Org Owner or an account administrator.
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
- **Depends on**: `/organization/members`, `/authorization/bindings`,
  `/authorization/bindings/preview`, `/authorization/access-requests`.

### Integration keys

Issues and revokes bank-scoped machine keys for the push API.

- **Reach**: an account administrator with integration authority.
- **Control**: `POST /integration-keys` (label, bank), `GET /integration-keys`,
  `POST /integration-keys/{key_id}/revoke`.
- **Check**: the secret is returned once at creation and never again; a revoked
  key is listed as revoked and its writes are refused.
- **Traps**: only data push keys can be issued; a reader key is refused with
  `422`. Never record the key value in evidence.
- **Depends on**: `/integration-keys`, `/integration-keys/{key_id}/revoke`.

### Business intelligence

Defines measures, dashboards and alerts over a bank's data.

- **Reach**: enable `BI_ENABLED` on the API and sign in with the BI grants.
- **Control**: `GET /banks/{bank_id}/bi/measures`, `/bi/dashboards`,
  `/bi/alerts`, with the matching `POST`, `PUT` and `DELETE` routes.
- **Check**: created measures and dashboards are listed with their versions and
  shares.
- **Traps**: with BI disabled the registered BI routes return `404` through the
  router-level feature gate.
- **Depends on**: `/banks/{bank_id}/bi/*`.

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
- **Depends on**: `/operator/health`, `/operator/auth/login`.

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
- **Depends on**: `/operator/v1/tenants`, `/operator/v1/tenants/{org_id}`,
  `/operator/v1/audit`.

### Tenant diagnostics and remediation

Reads a tenant's metrics, ingestion, findings and storage, and runs operator
fixes.

- **Reach**: a signed-in operator and a provisioned tenant.
- **Control**: `GET /operator/v1/tenants/{org_id}/metrics`, `/ingestion`,
  `/ingestion/{batch_id}`, `/findings`, `/config`, `/storage`, `/users`; fixes
  are `POST /operator/v1/tenants/{org_id}/fix/recompute`, `/fix/official-run`,
  `/fix/rerun-ingestion`, `/fix/redrive-dedup` and `/fix/config`.
- **Check**: reads return the tenant's own records; a fix answers with its
  outcome (the enqueued job for `recompute` and `official-run`) and writes an
  audit entry.
- **Traps**: fixes mutate tenant state, so run them only against the disposable
  database. Whether a fix completes depends on the worker running.
- **Depends on**: the `/operator/v1/tenants/{org_id}/*` routes above, `/jobs`,
  `/worker-health`.

### Inspector sessions

Opens a time-boxed, audited, read-only inspection of one tenant.

- **Reach**: an operator with inspector authority; the same
  `IMPERSONATION_JWT_SECRET` on both APIs and `BANK_APP_BASE_URL` set to the
  running dashboard origin.
- **Control**: `POST /operator/v1/inspector/sessions` (`organization_id`, `bank_id`,
  `reason`, `support_reference`, `mode`, `ttl_minutes`); `POST .../{session_id}/act-token` hands off to the
  dashboard; `POST .../{session_id}/end` closes it.
- **Check**: `GET /operator/v1/inspector/sessions` lists the session as active;
  the act-token response carries a `dashboard_url` matching the running
  dashboard; ending it moves it out of the active list.
- **Traps**: the act token and any URL fragment carrying it are credentials; keep
  them out of evidence. Confirm `dashboard_url` is the loopback dashboard before
  opening it.
- **Depends on**: `/operator/v1/inspector/sessions` and its `act-token` and `end`
  routes.

### Operator accounts

Creates staff accounts and manages their role and activation.

- **Reach**: sign in with authority to administer staff accounts.
- **Control**: `GET` and `POST /operator/v1/operators`; `POST
/operator/v1/operators/{email}/deactivate`, `/reactivate`, `/reset-password`.
- **Check**: the account appears with its role and `is_active`; a reset returns a
  one-time password; each change is in the audit log.
- **Traps**: staff sessions without account administration authority receive
  `403`. Rank rules also limit which accounts an administrator may change.
- **Depends on**: `/operator/v1/operators`, `/operator/v1/audit`.

### Regulatory parameters

Governs the control-plane thresholds banks are measured against, with four-eyes
approval.

- **Reach**: a signed-in operator.
- **Control**: `GET` and `POST /operator/v1/regulatory-parameters`; `POST
/operator/v1/regulatory-parameters/{param_id}/approve` from a second operator.
- **Check**: a proposed parameter stays pending until a different operator
  approves it; the approval is audited.
- **Traps**: the proposer cannot approve their own change.
- **Depends on**: `/operator/v1/regulatory-parameters`.

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
- **Depends on**: `/operator/v1/desk/*`, `/operator/v1/curves/*`.

## Staff console

The operator UI in `console/` on :3002. It reaches the operator API through its
`/api/op` proxy only. Reach it as in
[the console reference](../.agents/skills/verification/references/console.md).

### Console sign-in

Signs a staff member in to the console.

- **Reach**: `/login` with the console and operator API running.
- **Control**: type an email and password into the **Email** and **Password**
  fields and click **Sign in**; in development builds, open **Local dev**, paste
  the token into the field with placeholder `Paste OPERATOR_DEV_TOKEN` and click
  **Sign in with dev token**.
- **Check**: the URL leaves `/login` for `/` and the shell navigation appears; a
  wrong credential shows an alert and keeps you on `/login`; a rejected dev token
  reads "Token rejected by the operator API."
- **Traps**: the dev disclosure exists only in development builds and needs dev
  auth enabled on the operator API. Workforce SSO is for deployed environments
  and is not a verification path.
- **Depends on**: `/login`, `/api/auth/password-login`, `/api/op/*`; the operator
  API's `/operator/auth/login`.

### Console home

Summarises the fleet, items needing attention, connections and desk status.

- **Reach**: signed in at `/`.
- **Control**: open **Home** in the rail.
- **Check**: the fleet snapshot, overview figures and attention list render from
  the operator API's overview and tenant reads.
- **Traps**: an empty deployment shows empty states, not zeros.
- **Depends on**: `/`; `/operator/v1/overview`, `/operator/v1/tenants`.

### Console tenants

Lists every onboarded institution and opens one for diagnostics.

- **Reach**: signed in; open `/tenants`.
- **Control**: filter with **All**, **Live**, **Empty** or **Stale**; **Export
  CSV**; open a row for `/tenants/{orgId}`.
- **Check**: the count line reads "N of M shown"; the detail page shows
  jurisdiction, currency, licence, ingestion and live-module figures for that
  tenant.
- **Traps**: with no tenants the table shows "No tenants on this deployment yet".
- **Depends on**: `/tenants`, `/tenants/[orgId]`; `/operator/v1/tenants`.

### Console onboarding

Provisions a tenant through a form, a review step and a result panel.

- **Reach**: signed in; open `/onboard`.
- **Control**: fill **Organization name**, **Bank name**, **License type**,
  **Institution type**, **Jurisdiction**, **Reporting currency**, **Admin email**
  and **Admin full name**, click **Review**, then **Provision tenant**.
- **Check**: the "Provisioning steps" panel reports each step, then "Tenant
  provisioned" shows the new identifiers and a one-time password.
- **Traps**: nothing is created until the review step is confirmed. The panel
  holds the generated password, so keep it out of screenshots. When the bank key
  fields are required, the form asks for key details before it enables **Review**.
- **Depends on**: `/onboard`; `POST /operator/v1/tenants`.

### Console inspector

Starts and ends audited inspection sessions and opens a tenant's dashboard.

- **Reach**: signed in with inspector authority; open `/admin/inspector`.
- **Control**: under "Start inspection session" choose **Organization**,
  **Examination bank**, **Reason**, **Support reference**, **Mode** and
  **Session duration**, then click **Start session**. On that tenant's page
  click **Open bank dashboard**, then **Open in new tab** in the modal.
  Return to `/admin/inspector`, select **Active only** and click **End** on the
  session row.
- **Check**: the session appears in the active list and count. The dashboard
  opens at `/inspect`, and ending the session removes it from the active list.
- **Traps**: pause at the button's navigation call and keep the token and URL
  fragment out of evidence, as the console reference describes.
- **Depends on**: `/admin/inspector`, `components/tenants/OpenBankDashboardButton.tsx`;
  `/operator/v1/inspector/sessions`.

### Console operators

Manages staff accounts.

- **Reach**: sign in with authority to administer staff accounts and open
  `/admin/operators`.
- **Control**: **Create operator**, fill **Email**, **Display name** and **Role**,
  then confirm; row actions deactivate, reactivate or reset a password.
- **Check**: the new account lists with its role; a reset shows a one-time
  password dialog.
- **Traps**: the page requires account administration authority, and rank rules
  limit which accounts an administrator may change.
- **Depends on**: `/admin/operators`; `/operator/v1/operators`.

### Console regulatory parameters

Proposes and approves governed parameters.

- **Reach**: signed in; open `/admin/regulatory-parameters`.
- **Control**: propose a change, then approve it as a different operator.
- **Check**: the parameter moves from pending to approved and the audit log
  records both steps.
- **Traps**: the proposer cannot approve their own change.
- **Depends on**: `/admin/regulatory-parameters`;
  `/operator/v1/regulatory-parameters`.

### Console audit log

Shows the operator action trail.

- **Reach**: signed in; open `/admin/audit`.
- **Control**: scroll or page the list.
- **Check**: an action performed earlier in the run, such as a provisioning or an
  operator creation, appears with its actor and time.
- **Traps**: mutating calls write here, so entries from other cases on the same
  database are expected.
- **Depends on**: `/admin/audit`; `/operator/v1/audit`.

### Console markets desk

Works through determinations, curves, observations and publications.

- **Reach**: use separate maker and checker accounts with desk access. Open
  `/desk/market` or a desk page from the rail.
- **Control**: in **Determinations**, the maker creates, computes and submits
  for review. The checker opens the submitted determination, approves it and
  publishes it. Use **Curves**, **Observations** and **Publications** to inspect
  the inputs and results.
- **Check**: a determination moves through its states and a published one is
  listed under **Publications**.
- **Traps**: an unavailable source is shown as unavailable; publishing writes
  shared data, so use the disposable database.
- **Depends on**: `/desk/*`; `/operator/v1/desk/*`, `/operator/v1/curves/*`.

### Console operations

Shows data engines, connections and background jobs.

- **Reach**: signed in; open `/operations`.
- **Control**: open **Operations** in the rail.
- **Check**: engines, connections and jobs list from the operator API; a stuck
  job shows in the dedup view.
- **Traps**: job progress depends on the worker running.
- **Depends on**: `/operations`; `/operator/v1/data-engines`, `/operator/v1/jobs`,
  `/operator/v1/worker-health`.

## Bank dashboard

The authenticated bank product UI in `backend/dashboard/` on :3001. Every figure
comes from the tenant API through the generated client. Reach it as in
[the dashboard reference](../.agents/skills/verification/references/dashboard.md);
routes and what they read are in
[the dashboard README](../backend/dashboard/README.md#scope).

### Dashboard sign-in

Signs a bank user in and ends the session.

- **Reach**: `/login` on the interactive stack; the hermetic stack mints a
  signed-in state per fixture role and skips this form.
- **Control**: type into **Email** and **Password** and click **Sign in**; open
  the account button in the header and choose **Sign out**.
- **Check**: the URL becomes `/` and the account button shows the user's name and
  role; after sign-out the URL is `/login` and a stale refresh token answers
  `401`.
- **Traps**: an unauthenticated visit to a protected route redirects to `/login`.
  Use the same loopback origin throughout to keep the browser session.
- **Depends on**: `/login`, `/api/auth/*`; the tenant API's `/auth/login`,
  `/auth/refresh`; fixture roles in `e2e/support/mint.ts`.

### Dashboard overview

Summarises cross-module posture, live-engine freshness and the filing calendar.

- **Reach**: signed in at `/`.
- **Control**: open the home page.
- **Check**: with data, the "Treasury Command Center" shows each module's live
  status; without data it shows "No computed data yet" with an **Open the Data
  Engine** link.
- **Traps**: the empty state is correct for a bank that has not ingested data.
  Historical reporting periods are optional for daily work.
- **Depends on**: `/`, `features/home/routes/command-center.tsx`;
  `/banks/{bank_id}/live-summary`.

### Dashboard module navigation

Moves between a module's tabs and between modules.

- **Reach**: signed in; any module route.
- **Control**: click a tab link in the "Module sections" navigation, or use the
  command palette.
- **Check**: the URL becomes the tab's route and the page heading changes.
- **Traps**: a first compile of a route can outlast a short wait; wait for the
  destination URL before asserting figures. A module the user has no grant for
  shows an access notice, not an error.
- **Depends on**: `navigation` landmark named "Module sections";
  `e2e/support/navigation.ts`.

### Dashboard liquidity

Shows the LCR, NSFR, buffer, monitoring, early warnings, contingency plan and
liquidity stress.

- **Reach**: signed in with liquidity authority; a loaded book for figures.
- **Control**: open `/liquidity` ("Liquidity Cockpit"), then the **NSFR**,
  **Buffer**, **Monitoring**, **Stress** and forecast tabs.
- **Check**: LCR and NSFR agree with `GET /banks/{bank_id}/liquidity/dashboard`;
  NSFR lines show available and required stable funding.
- **Traps**: no data shows an unavailable state, never zero. Ratios are tested
  against governed thresholds, not literals.
- **Depends on**: `/liquidity`, `/liquidity/nsfr`, `/liquidity/buffer`,
  `/liquidity/monitoring`, `/liquidity/stress`, `/liquidity/cfp`;
  `/banks/{bank_id}/liquidity/dashboard`, `/liquidity-monitoring`.

### Dashboard Basel capital

Shows risk-weighted assets, capital structure, adequacy and capital stress.

- **Reach**: signed in with capital authority.
- **Control**: open `/basel`, then `/basel/rwa`, `/basel/structure`,
  `/basel/stress` and `/basel/planning`.
- **Check**: figures agree with `GET /banks/{bank_id}/capital/dashboard`, `/rwa`
  and `/structure`.
- **Traps**: the bank's booked allowance is the capital figure of record; modelled
  expected loss is a what-if and is never substituted in Tier 2.
- **Depends on**: `/basel/*`; `/banks/{bank_id}/capital/*`.

### Dashboard interest-rate risk

Shows repricing gap, EVE and earnings sensitivity, limits and scenarios.

- **Reach**: signed in with IRR authority.
- **Control**: open `/irr`, then `/irr/gaps`, `/irr/sensitivity`, `/irr/limits`
  and `/irr/scenarios`.
- **Check**: headline figures agree with `GET /banks/{bank_id}/irr/dashboard` and
  `/irr/ear-analysis`.
- **Traps**: a stress input outside the supported boundaries is refused rather
  than computed; that is the expected result.
- **Depends on**: `/irr/*`; `/banks/{bank_id}/irr/*`.

### Dashboard FX

Shows net open position, value at risk, hedges and forwards.

- **Reach**: signed in with FX authority.
- **Control**: open `/fx`, then `/fx/var`, `/fx/hedges`, `/fx/forwards`,
  `/fx/limits` and `/fx/scenarios`.
- **Check**: figures agree with `GET /banks/{bank_id}/fx/dashboard`.
- **Traps**: without a loaded book or the market data a figure needs, the screen
  shows that figure as unavailable, never as zero.
- **Depends on**: `/fx/*`; `/banks/{bank_id}/fx/dashboard`.

### Dashboard funds transfer pricing

Shows the transfer curve and product and branch profitability.

- **Reach**: signed in with FTP authority.
- **Control**: open `/ftp`, then `/ftp/products`, `/ftp/lines`, `/ftp/rules`,
  `/ftp/scenarios` and `/ftp/expost`.
- **Check**: figures agree with `GET /banks/{bank_id}/ftp/dashboard`.
- **Traps**: without a loaded book the pages show their empty state.
- **Depends on**: `/ftp/*`; `/banks/{bank_id}/ftp/dashboard`.

### Dashboard forecasting

Projects the balance sheet and runs scenarios, what-ifs and reverse stress.

- **Reach**: signed in with forecasting authority; governed assumptions in place.
- **Control**: open `/forecasting`, then `/forecasting/assumptions`, `/nii`,
  `/scenario`, `/whatif`, `/optimizer` and `/reverse-stress`.
- **Check**: a run lists under `GET /banks/{bank_id}/forecast/runs` and its
  figures render; reverse stress shows its frontier and breach value.
- **Traps**: a book that cannot be forecast shows a not-computable state, which
  is the expected result.
- **Depends on**: `/forecasting/*`; `/banks/{bank_id}/forecast/runs`,
  `/reverse-stress`; `data-testid` `reverse-stress-frontier` and
  `reverse-stress-headlines`.

### Dashboard credit

Shows asset quality, delinquency, concentration, vintages and activity.

- **Reach**: signed in with credit authority.
- **Control**: open `/credit`, then `/credit/book`, `/credit/delinquency`,
  `/credit/concentration`, `/credit/vintages` and `/credit/activity`.
- **Check**: figures agree with `GET /banks/{bank_id}/credit/dashboard` and
  `/credit/loans`.
- **Traps**: credit register access is scoped by grant, so a narrower principal
  sees less than an administrator; compare against that principal's authority.
- **Depends on**: `/credit/*`; `/banks/{bank_id}/credit/*`.

### Dashboard markets

Shows published curves, rates, FX and indices with source and freshness details.

- **Reach**: sign in with Markets published-view authority and open `/markets`.
- **Control**: select **Curves** or **Market data** and change **As of**.
- **Check**: the curves and source details match the publication available for the
  selected date.
- **Traps**: a date without published coverage can show an empty view.
- **Depends on**: `/markets`, **Curves**, **As of** and
  `GET /banks/{bank_id}/market-data/views`.

### Dashboard behavioral models

Shows model estimates and training coverage for the bank's behavioral assumptions.

- **Reach**: sign in with Behavioral aggregated-view authority and open `/behavioral`.
- **Control**: open **NMD Duration**, **Loan Prepayment** or **Deposit Stability**.
- **Check**: the model workspace reports the estimate and its training coverage
  for the selected model.
- **Traps**: a bank without enough ingested history may have no trained estimate.
- **Depends on**: `/behavioral/{nmd-duration|prepayment|deposit-stability}` and
  `GET /banks/{bank_id}/behavioral/{model}`.

### Dashboard reports

Collects board packs, saved analyses and persisted regulatory runs.

- **Reach**: sign in, select a bank with fresh, available live evidence and
  open `/reports`.
- **Control**: open **Board pack** and select **Print / Save as PDF**.
- **Check**: the pack renders the bank's available live module figures and
  opens the print dialog.
- **Traps**: stale evidence withholds figures until recomputation. Printing
  can remain enabled if the stale response still contains modules.
- **Depends on**: `/reports/board-pack`, `/reports/analyses`,
  **Print / Save as PDF** and `GET /banks/{bank_id}/regulatory-runs` for the run registry.

### Dashboard positions

Shows the canonical position book behind every module.

- **Reach**: signed in with data authority; a loaded book.
- **Control**: open `/positions` and filter, sort or page the blotter.
- **Check**: rows and totals match `GET /banks/{bank_id}/canonical-positions`.
- **Traps**: an empty book shows an empty table.
- **Depends on**: `/positions`; `/banks/{bank_id}/canonical-positions`,
  `/canonical-positions/facets`.

### Dashboard Data Engine upload

Uploads a spreadsheet or CSV and reviews the batch it creates.

- **Reach**: signed in with upload and batch-run authority, plus mapping
  activation authority if needed. Start local MinIO and open `/data-engine/excel-csv`.
- **Control**: on a mapping template click **Activate mapping**; in the "Upload &
  ingest" section set **Source files (.xlsx / .csv)** and **As-of date**, then
  click **Upload & ingest**; open **Batch detail**.
- **Check**: the outcome card shows the batch state and the Extracted,
  Translated, Accepted and Errors counts; `/data-engine/batches/{batchId}` lists
  "Validation findings" and "Untranslatable rows" with row references. For
  `tests/data-engine/fixtures/positions.csv` the counts are 4, 3, 2 and 1.
- **Traps**: a legacy `.xls` is refused with re-save guidance. Non-blocking row
  errors can yield "accepted with warnings"; a blocker rejects the batch.
  Use synthetic files only.
- **Depends on**: `/data-engine/excel-csv`, `/data-engine/batches/[batchId]`;
  `POST /banks/{bank_id}/ingestion-uploads` and `POST /banks/{bank_id}/ingestion-batches`.

### Dashboard Data Engine connections

Configures API push, database-direct, core-banking adapter and market-data loads.

- **Reach**: signed in with data authority.
- **Control**: open `/data-engine/api`, `/database`, `/t24`, `/adapters` or
  `/market-data`.
- **Check**: each page shows its connection state and the guide or form for that
  route; the overview at `/data-engine` lists recent ingestion across sources.
- **Traps**: a reader without the grant sees a restricted notice. Reconciliation
  exceptions follow checker independence: the maker cannot approve their own
  proposal.
- **Depends on**: `/data-engine/*`; `data-testid`
  `market-data-connections-restricted`.

### Dashboard submissions

Prepares, validates, certifies and files regulatory returns.

- **Verification**: dashboard filing requires a local sandbox because the hand-off can transmit to a real channel. This map skips the filing run.
- **Depends on**: `/submissions/returns`, `/approvals`, `/history`, `/instruments`.

### Dashboard ICAAP

Builds the internal capital adequacy assessment in a per-cycle workspace.

- **Reach**: signed in with ICAAP authority; open `/icaap`.
- **Control**: create or open a cycle, then work through its overview, risks,
  appetite, stress, Pillar 2, sections, attachments, review and filing pages.
- **Check**: each page for the cycle at `/icaap/{cycleId}/...` shows its content
  and the filing page shows its chain and omissions.
- **Traps**: when signing is not configured, filing shows a suspended notice
  (`data-testid` `icaap-signing-suspended`).
- **Depends on**: `/icaap/*`; `/banks/{bank_id}/icaap/cycles/*`.

### Dashboard institution profile

Shows the institution's legal identity, authorisation, ownership and registers.

- **Reach**: signed in; open `/institution`.
- **Control**: open the profile, then outlets, products, parties, registers and
  history.
- **Check**: the "Institution Profile" heading and the bank's platform ID match
  `GET /banks/{bank_id}/institution-profile`.
- **Traps**: the platform ID shown is the one used in API paths and in the push
  contract.
- **Depends on**: `/institution/*`; `/banks/{bank_id}/institution-profile`.

### Dashboard explore

Builds ad hoc questions and views over the book.

- **Reach**: BI enabled on the API and BI grants for the signed-in user. Open
  `/explore`. For **Ask a question**, also enable `BI_NLQ_ENABLED` on the API.
- **Control**: shape a grid with **Measure** and **Break down by**, **Export**, or
  use the **Ask a question** tab and **Work out my question**.
- **Check**: the grid rows reconcile with the source figures and a confirmed
  question shows "The question you confirmed".
- **Traps**: with BI disabled the API's router-level feature gate returns `404`.
  Disabling `BI_NLQ_ENABLED` hides **Ask a question**.
- **Depends on**: `/explore/*`, `/dashboards/*`; `/banks/{bank_id}/bi/*`.

### Dashboard risk and alerts

Shows the limit wall, findings and pipeline alerts.

- **Reach**: signed in; open `/risk` or `/alerts`.
- **Control**: open the page and filter.
- **Check**: alert counts and items agree with
  `GET /banks/{bank_id}/alerts`.
- **Traps**: with no findings the page shows an empty state, not a hidden one.
- **Depends on**: `/risk`, `/alerts`; `/banks/{bank_id}/alerts`.

### Dashboard members and grants

Lists members and grants or revokes scoped access.

- **Reach**: signed in as an Org Owner; open `/access/members`.
- **Control**: **View `<member name>`**, **Grant access**, set **Module**, **Role
  bundle**, **Sensitivity**, **Institution coverage** and **Reason**, **Review
  grant**, **Add grant**, **Done**; **Revoke access** then **Revoke this access**.
- **Check**: the grant notice "Grant created" appears and the member's active
  access lists the new grant; revocation shows its reason and who revoked it.
- **Traps**: a conflicting grant is shown as **Cannot be granted**; that is the
  separation-of-duties rule, not a fault. A member without owner authority is
  shown an access-denied page.
- **Depends on**: `/access/members`, `/access/my-access`, `/access/authentication`;
  `/authorization/bindings`, `/organization/members`.

### Dashboard integration keys

Issues and revokes data push keys.

- **Reach**: signed in as an account administrator; open
  `/access/integration-keys`.
- **Control**: type a **Key label**, choose the bank, click **Generate data push
  key**, then **I have stored it securely — dismiss**; **Revoke** on a listed key.
- **Check**: "Data push key generated — copy it now" shows the secret once; the
  key lists afterwards without it and a revoked key reads "Revoked".
- **Traps**: keep the secret out of screenshots and captions.
- **Depends on**: `/access/integration-keys`; `/integration-keys`.

### Dashboard settings and appearance

Sets theme, profile and account preferences.

- **Reach**: signed in; open `/settings/profile`.
- **Control**: under **Appearance** choose the **Light**, **Dark** or **System**
  radio.
- **Check**: `<html>` carries `data-theme="light"` or `"dark"` to match the
  choice.
- **Traps**: dark is the default. With **System**, the theme follows the browser's
  `prefers-color-scheme`.
- **Depends on**: `/settings`, `/settings/profile`; `data-theme` on `<html>`;
  `components/shell/ThemeProvider.tsx`.

### Dashboard tenant inspector

Opens a read-only dashboard for a staff inspection session.

- **Reach**: a staff-issued act token for an active session; see
  [Inspector sessions](#inspector-sessions) and
  [Console inspector](#console-inspector).
- **Control**: open the `/inspect` URL the console hands off.
- **Check**: the dashboard opens scoped to the inspected bank with the
  impersonation banner shown.
- **Traps**: the act token travels in the URL fragment; keep it out of evidence.
- **Depends on**: `/inspect`, `/api/impersonation`.

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
- **Depends on**: `frontend/app/*/page.tsx`; `components/Navigation.tsx`.

### Marketing contact form

Sends a demo request through the site's contact API.

- **Verification**: contact submission requires a local delivery fixture because it can email a real recipient. This map skips form submission.
- **Depends on**: `/contact`, `/api/contact`; `components/ContactForm.tsx`.
