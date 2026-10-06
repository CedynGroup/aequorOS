# Turning BI on in production — the ordered sequence

**Audience:** whoever flips the BI flags on a real deployment. **Status:** derived by
audit A360 (2026-09-29) from the code and the deployment files, with the two unsafe
points it found named where they sit and what closed them. This is the committed
home of the sequence; `docs/deployment.md` (a local, gitignored checklist) points here.

BI ships with every switch off: the six `BiSettings` booleans (`BI_ENABLED`,
`BI_MART_ENQUEUE_ENABLED`, `BI_SCHEDULER_ENABLED`, `BI_ALERTS_ENABLED`,
`BI_SUBSCRIPTIONS_ENABLED`, `BI_NLQ_ENABLED`) default to `False` and are set in no
deployment file — a grep across `backend/docker-compose*.yml`, `deploy/`,
`backend/mise.toml`, `backend/.env.example` and `.github/` on 2026-09-29 found one
comment and no value. Turning it on is ORDERED, because the API and every worker
poll ONE `jobs` table: a job enqueued for a lane nobody runs strands in `queued`
(the `notification_email_mirror` orphan, D-008).

## The ten steps

1. **Deploy the release; `risk-migrate` applies `202609220066` … `202609290079`**
   (fourteen BI revisions; single head, `alembic heads` — HEAD's count: a fifteenth,
   `202609290080_reconciliation_leaves_bi.py`, which drops `bi_reconciliation_results`
   under the founder's 2026-09-29 decision, is in the working tree at the time of
   writing; re-derive the head before the release). The last two close audit
   A360: `0078` makes the aggregate sums nullable (an absent figure stops being a
   zero) and `0079` makes a partition child be VERIFIED before it is adopted —
   without it, a child created by hand carries no RLS and leaks across tenants
   when named directly. Before it runs, confirm
   nobody pre-created a `bi_*` table on the primary by hand — an existing table
   makes `risk-migrate` exit 1 and takes prod down (the surgical-DDL incident).
   The `bi_ensure_*` / `bi_drop_*` `SECURITY DEFINER` functions are granted
   `EXECUTE` to the migrating role and to every `BYPASSRLS` login role that
   exists at `0066` time ONLY (migration `202609220066`, the
   `rolbypassrls AND rolcanlogin` loop); a worker role created later needs
   `GRANT EXECUTE` by hand or every mart build fails with
   `permission denied for function bi_ensure_month_partition`.
2. **Deploy `risk-worker-bi`** (`WORKER_JOB_TYPES=lane:bi`, `docker-compose.prod.yml`),
   flags still off. The `bi` lane is seven job types (`job_queue.JOB_LANES`):
   `bi_mart_refresh`, `bi_mart_backfill`, `bi_retention`, `bi_export`,
   `bi_alert_evaluate`, `bi_subscription_scan`, `bi_subscription_run`.
3. **Run the gate BEFORE:** `cd backend && uv run python scripts/authorization_access_impact.py`
   (table) and `--json` (for a machine diff); keep both, dated. **Was UNSAFE** —
   until 2026-09-29 the script could not report the Phase 4 data-scope dimension,
   so a release could widen a branch-scoped reader to the whole book with no diff
   showing it (A360-7 S2). It now carries a `data scope` column and a
   `scoped_reader` flag; compare that column, not only the module columns.
4. **`BI_MART_ENQUEUE_ENABLED=1`** in the shared Coolify `.env` (never `${}` in a
   compose file), redeploy API + workers. Ingestion, withdrawals, the pipelines and
   the register triggers now enqueue `bi_mart_refresh`; the handler re-checks the
   flag, so flipping it off drains the backlog as `skipped`.
5. **Per tenant, `POST /operator/v1/tenants/{org}/bi/backfill`** (operator session,
   audited; `app/operator/features/bi_backfill.py`). Without it only FUTURE
   ingestions build marts; a dashboard opened on an unbuilt date must read "needs
   data", never zero. There is no console button for this (A360-2 M4) — it is an
   API call.
6. **`BI_SCHEDULER_ENABLED=1`** — the recovery sweep and retention join the hourly
   tick. **Kill every local `lane:bi` worker first**
   (`ps -eo pid,lstart,command | grep -E "app\.worker"`): they poll the same shared
   `jobs` table and would run old builder code against production marts.
7. **`BI_ENABLED=1`** mounts the tenant routers; the dashboard shows Insights /
   Dashboards / Explore. **Was UNSAFE** — this is the step that exposed
   `bi_enforcement_rollout.md`'s scope section, which stated the opposite of the
   code until 2026-09-29 (A360-7 S1; corrected). Two of the three caveats this step
   carried are now CLOSED: the module-level insight strip and KPI explain drawer ARE
   mounted on the Command Center and every module landing page (A360-7 S3), and the
   integration-key screen now offers the analytics-feed purpose, so the Stage B feed
   is reachable from the product (A360 H4). The chart trust badge that was mounted
   beside them is **REMOVED BY FOUNDER DECISION 2026-09-29**: BI carries no
   reconciliation verdict against the regulatory returns (treasury/ALM and the
   regulatory spine are different planes — `docs/bi.md` §Founder decision
   2026-09-29). Nothing in this sequence changes: `bi_mart_builds` and the build
   fingerprint (freshness) stay, and no step ever depended on a reconciliation result.
   **Still open, so tell tenants:** AI commentary has routes and no dashboard caller
   (A360-2 M1), and `POST /bi/drill` has no component caller, so a drill whose slice
   is not exactly carryable offers nothing (A360-2 M2).
8. **Org Owners grant BI sentences.** Nothing is backfilled — a bank that granted
   its Treasurer LIQUIDITY/`aggregated` already has BI liquidity under that row;
   choose scopes from the [supported BI grant table](bi_enforcement_rollout.md#exact-binding-rows).
   Store any supported slice on the binding, never in the reason. Run the gate AFTER and diff
   every column, `data scope` included.
9. **Optionally** `BI_ALERTS_ENABLED=1` (evaluated by a succeeded mart build; owns
   no tick) and `BI_SUBSCRIPTIONS_ENABLED=1` (owns a tick branch; needs the
   in-country SMTP relay — there is ONE global relay config, wrong for
   multi-country tenants). A360-2 M3: alerts and subscriptions can be created while
   their flag is off and the UI then blames the bank's data; do not leave these
   off for long after step 7 without saying so.
10. **NOT NOW: `BI_NLQ_ENABLED`.** It needs, in order: `risk-worker-ai` deployed
    (`docker-compose.ai.prod.yml`, `lane:ai` = `icaap_ai_draft`, `bi_commentary`,
    `bi_nlq_translate`), `AI_COMMENTARY_ENABLED=1`, an entry for `bi_nlq` in
    `app/services/ai/approved_configurations.json` (**still empty** — this is the
    remaining blocker), then the tenant Owner's consent in the AI settings.
    The consent step is DONE: the text was amended to `ai-consent-2026-09-v2` on
    2026-09-29 and `bi_nlq` added to `CONSENT_COVERED_FEATURES`. Note that raising
    the consent version switches AI assistance OFF for every organisation until each
    Owner accepts the new text — including tenants already using ICAAP drafting, who
    will be asked again. That is the mechanism working, but plan for the support
    traffic rather than being surprised by it. Flipped early it yields refusals (409 on ask;
    `configuration_not_approved`), not leaks.

`GET /api/v1/feature-flags` projects all SIX booleans to the dashboard —
`bi_enabled`, `bi_mart_enqueue_enabled`, `bi_scheduler_enabled`,
`bi_alerts_enabled`, `bi_subscriptions_enabled`, `bi_nlq_enabled`. The last two
were added to the projection by audit A360-2 M3: without them the alerts page
said "Waiting for figures" about a shut flag, blaming the bank's data for a
deployment decision it could not see.

## Related contracts

- Authorization and the release record: [`bi_enforcement_rollout.md`](bi_enforcement_rollout.md).
- The feed a report server pulls: [`powerbi_stage_b.md`](powerbi_stage_b.md); file exports into
  Power BI Desktop: [`powerbi_stage_a.md`](powerbi_stage_a.md).
- Credit routes on scoped bindings: [`credit_enforcement_rollout.md`](credit_enforcement_rollout.md).
