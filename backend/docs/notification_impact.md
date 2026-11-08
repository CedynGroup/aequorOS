# Notifications/jobs cutover impact

`scripts/authorization_notification_impact.py` inventories who loses visibility
of in-app notifications and queued jobs when the #186 locator-view read filter
lands. It does not create grants, approve grant requests, change reads, or
touch the worker. Use the inventory for operator grant review before the
cutover: users who previously saw tenant notifications and jobs keep seeing a
row only while one current VIEW binding matches its persisted typed locator.

Run from `backend/` with an explicitly authorized, read-only connection:

```sh
uv run python -m scripts.authorization_notification_impact \
  --organization OR-XXXXXXXX \
  --output-dir /access-controlled/path/YYYYMMDD-notif-impact
```

The connection comes from `--database-url`, then `WORKER_DATABASE_URL`, then
`DATABASE_URL`. Do not paste its credentials into a shell command or shared
log. Omitting `--organization` inventories every tenant visible to the reader.
Without `--output-dir`, the command prints the combined private report to
standard output. Each PostgreSQL tenant assessment runs in one read-only,
repeatable-read transaction and sets the tenant RLS context.

The directory contains `notif-before.json`, `notif-after.json`, and
`notif-exact-grants.json`. Files are created with mode `0600`, new directories
use `0700`, and existing evidence is never overwritten. Keep the principal and
binding IDs private. The report never exports email addresses, credentials,
raw grant reasons, secrets, or tokens. Exit status 2 means input failed or a
grants evaluation failed; do not treat partial results as a complete
assessment.

## Inventory and assessment boundary

For every human and machine principal, the report counts the stored
notifications (recipient and tenant factors applied) and queue rows (tenant
factor applied) under each persisted locator tuple, before and after applying
the production `locator_visibility` predicate — the same predicate the read
paths execute. A row is newly hidden when the count drops. The measured delta
is exactly the new locator-view factor: session, tenant RLS, and the
pre-existing return-family factor for filing notifications are unchanged by
#186 and are not what the report measures.

Legacy locator-less rows and worker/system-provenance jobs are inventoried and
newly hidden for every principal, and deliberately receive no grant candidate:
no grant can restore them, and there is no backfill by inference. Partial
locators behave the same way. Organization-scoped rows become visible only
through organization-scope view bindings; institution-scoped rows need a grant
naming that bank.

`notif-exact-grants.json` lists operator-review candidates only: least-scope
`viewer`/`view` bindings whose simulated addition would actually restore the
hidden rows. Machine principals and inactive users receive no candidates. The
relevant tenant surfaces are `GET /api/v1/notifications`,
`POST /api/v1/notifications/{notification_id}/read`,
`POST /api/v1/notifications/read-all`, and `GET /api/v1/jobs/{job_id}`. Enqueue,
claim and execution ignore the locator, so workers keep processing rows no
tenant can poll. The existing `authorization_access_impact.py` dashboard
projection remains the full-access report; this sibling adds the
notifications/jobs deny-impact and grant inventory without changing that
response contract.
