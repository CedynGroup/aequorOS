# Workflow state machines and property coverage

The [shared testing standard](../../CODEBASE_CONVENTIONS.md#behavior-properties-and-state-machines-backend-and-dashboard)
governs these tests. This inventory records workflow commands rather than treating
every `status` column as a writable workflow. Imported account/watch statuses and
computed compliance/validation results are observations; they need input/output
properties, not invented transition commands. Code remains the authority when a
transition changes; update its table and independent test model in the same change.

## Coverage and order

| Workflow                         | Public interface / transition authority                                                         | Stateful coverage                                                                                  |
| -------------------------------- | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Filing review and submission     | `app/reporting/api/manage_filing_workflow.py`; `app/reporting/service/workflow.py`              | [Filing machine](../tests/reporting/service/test_filing_state_machine.py)                          |
| ICAAP review, freeze and return  | `app/icaap/service/{workflow,freeze,post_freeze,cycles}.py`                                     | [Freeze machine](../tests/icaap/service/test_freeze_state_machine.py)                              |
| Maker-checker workflow templates | `app/reporting/service/filing_workflow/templates.py`; `app/icaap/service/workflow_templates.py` | Next priority; example tests cover each service                                                    |
| Scoped access requests           | `app/identity/api/manage_authorization.py`                                                      | Next priority; [grant administration examples](../tests/identity/api/test_grant_administration.py) |
| Grants and session invalidation  | `app/identity/service/{authentication,authorization,grant_administration}.py`                   | [Existing authorization machines](../tests/identity/api/test_authorization_state_machine.py)       |
| Jobs                             | `app/services/job_queue.py`; `app/worker.py`                                                    | Queue/worker examples; generated retries and stale claims pending                                  |
| Ingestion batches                | `app/services/ingestion.py`                                                                     | Ingestion examples; generated extraction/validation/retry sequences pending                        |
| BI alert verdicts                | `app/services/bi/alerts.py`                                                                     | Alert examples; generated missing-data/breach/recovery sequences pending                           |

The first two machines use real services and persisted audit/submission events.
Their models do not import production transition tables or maker-checker helpers.
Route-level bundle enforcement remains covered by reporting/ICAAP API tests:
passing a context to a service does not manufacture a scoped authorization grant.
Cross-bank tests use two banks of the same organization as well as another tenant;
RLS alone cannot distinguish the first pair.

## Filing review and submission

The default chain is Preparer (1), Approver (2), Validator (3). The stage and round
are part of the state: status alone cannot distinguish an approval awaiting hand-off
from a return waiting for a decision. Approved decisions do not hand off a return.

| Source                                                 | Command / actor                                      | Guard                                                                                                                       | Destination                                       | Observable effect                                          |
| ------------------------------------------------------ | ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- | ---------------------------------------------------------- |
| Generated/validated, preparation                       | Send for approval / EDIT                             | Checks passed; fresh digest                                                                                                 | Pending approval, stage 2                         | Pin stages; preparer's submitted decision                  |
| Pending approval, stage 2                              | Approve / APPROVE                                    | Fresh round/digest; not maker; required title                                                                               | Same stage, awaiting hand-off                     | Append approval; no transmission                           |
| Stage 2 decided                                        | Hand off / APPROVE                                   | Current stage decided                                                                                                       | Pending approval, stage 3                         | Holder changes; prior decision remains                     |
| Pending approval, stage 3                              | Approve / SUBMIT                                     | Fresh round/digest; neither maker nor earlier checker                                                                       | Approved, stage 3                                 | Chain complete; still unfiled                              |
| Stage 2 or 3, before filing                            | Return / stage authority                             | Named earlier target; comment; fresh round/digest; four eyes                                                                | Generated at target 1, otherwise pending approval | Round increments; decisions at/after target cease to stand |
| Approved, complete chain                               | Submit / SUBMIT                                      | Reconciled snapshot; channel, signing and family gates at channel entry                                                     | Submitted                                         | Submission receipt/event; status-change audit              |
| Submitted                                              | Record regulator outcome / authorized channel caller | Outcome is acknowledged, rejected or declined                                                                               | Named outcome                                     | Outcome event; supervisor comments retained                |
| Submitted/acknowledged                                 | Request resubmission                                 | No pending request; channel permits request                                                                                 | Package unchanged, request requested              | Request history                                            |
| Requested resubmission                                 | Regulator grant/deny                                 | Pending request                                                                                                             | Granted/denied request                            | Decision history; grant permits a new version, not an edit |
| Generated/validated/pending/approved/rejected/declined | Regenerate / generation authority                    | Applicable return, computed position, reconciliation; granted resubmission additionally required for submitted/acknowledged | Old version superseded; new version generated     | New snapshot/version; old content preserved                |
| Submitted downtime email                               | Re-upload via ORASS / SUBMIT                         | Pending ORASS re-upload, prior email submission                                                                             | Submitted                                         | New receipt; clear pending re-upload, same filed snapshot  |

The service status table also admits draft → generated/superseded and generated ↔
validated (failed checks project generated). Revalidation is refused outside
generated/validated. Acknowledged and superseded have no ordinary status exits;
rejected/declined can only be superseded by a new version. Submitted is terminal
for the **review chain**, but still awaits the regulator. A governed correction
never rewrites a filed snapshot. Duplicate approval is idempotent; transmission
retries are separate. A chain-only final hand-off replay has no status/content
effect; the API additionally executes the guarded channel submission.

The filing machine generates successful and refused review/send-back/retry actions,
stale digests, maker/prior-checker attempts, incomplete submission, repeated
regulator outcomes, and cross-bank/tenant reads and mutations. It checks standing
decisions, stage/round/status, immutable snapshot and submission events. Channel
transport, signing, resubmission authorization and regeneration are separate
existing test suites; this machine does not replace them.

## ICAAP workspace, freeze and filing

The complete test framework has preparation, CRO review, approval/freeze and Board
attestation stages. Production stages come from the pinned framework/template.

| Source                                                    | Command / actor                          | Guard                                                                              | Destination                               | Observable effect                                                                                 |
| --------------------------------------------------------- | ---------------------------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------- |
| None                                                      | Create / ICAAP EDIT                      | Applicable governed framework/date                                                 | Draft                                     | Cycle and framework digest                                                                        |
| Draft/returned                                            | Save/commit section / EDIT               | Editable; current revision                                                         | Same status                               | Working revision and immutable committed version                                                  |
| Draft/returned                                            | Submit for review / EDIT                 | Committed content; framework and review digests match                              | In review at stage 2                      | Submitted decision; narrative locks                                                               |
| Current review/approval stage                             | Decide / REVIEW or APPROVE               | Correct stage, round, digest, title; not content author or earlier checker         | Next stage                                | Reviewed/approved decision                                                                        |
| Current review/approval stage                             | Return / stage authority                 | Earlier target, comment; same freshness/separation guards                          | Returned at target 1, otherwise in review | New round; downstream decisions invalidated                                                       |
| Approval passed, awaiting freeze                          | Freeze / EDIT                            | Ready framework/figures/evidence; fresh digest; freezer did not review/approve     | Frozen                                    | Atomic package mint + cycle seal + frozen decision                                                |
| Frozen                                                    | Board certification / signature ceremony | Required recipients/signatures and framework filing rules                          | Board approved                            | Certification; sealed content unchanged                                                           |
| Board approved                                            | Submit / SUBMIT                          | Complete chain, attestation, attachments, reconciliation, channel gates            | Submitted                                 | Package/cycle advance together; receipt                                                           |
| Submitted                                                 | Regulator acknowledgement                | Authorized channel outcome                                                         | Acknowledged                              | Package/cycle advance together                                                                    |
| Frozen/board approved; submitted only if package rejected | Governed post-freeze return / APPROVE    | Fresh round/digest; not freezer; freeze-stage title; target at/before freeze stage | Returned or in review, next round         | Unlink old package; clear seal timestamps; invalidate decisions/signatures; old snapshot retained |
| Returned/in review after correction                       | Review and freeze again                  | All original gates apply again                                                     | Frozen with next package version          | Supersede predecessor without changing its content                                                |
| Draft/returned with no linked package                     | Archive / EDIT                           | No active package                                                                  | Archived                                  | Recorded retirement; no further editing/review/freezing                                           |
| Editable cycle                                            | Rebase / EDIT                            | Unfrozen and editable; permitted framework carry                                   | Old cycle superseded; new cycle draft     | Preserve old versions and explicit carry history                                                  |

Narrative locks at submission, attachments lock at freeze. Board attestation is a
signature, never an ordinary stage decision. Archived/superseded cycles are
terminal; a frozen cycle is sealed **until a governed return**, not permanently
terminal. Package rejection alone does not quietly unlock workspace content.

The freeze machine exercises review ordering, stale rounds/digests, officer-title
and maker-checker refusals, duplicate freezes, edits in locked/sealed/archived
states, governed returns, archival, and bank/tenant isolation. Every frozen version
keeps its original snapshot and both digests through reopening and later freezes.
Board signatures, channel submission, rebase and regulator hooks retain their
dedicated example tests; this machine concentrates on the freeze boundary.

## Maker-checker templates and access requests

| Workflow / source                   | Command / actor                                         | Guard                                                                             | Destination / effect                                      |
| ----------------------------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Filing or ICAAP template: none      | Propose / bank-scoped EDIT                              | Valid stages; open-proposal rules                                                 | Draft version                                             |
| Draft                               | Edit / EDIT                                             | Editable version                                                                  | Same status; proposed stages change                       |
| Draft                               | Submit / EDIT                                           | Editable version                                                                  | Pending approval                                          |
| Pending approval                    | Decide / APPROVE                                        | Different officer from proposer                                                   | Approved or rejected; decision audit                      |
| Prior approved template             | Approve replacement / APPROVE                           | Replacement passes four-eyes guards                                               | Prior superseded atomically; new template approved        |
| Approved/superseded filing template | Edit/submit/decide                                      | No legal command                                                                  | Refusal; pinned package stages unchanged                  |
| Access request: none                | Request / active member                                 | Recognized route/permission and tenant institution                                | Pending; duplicate pending scope returns existing request |
| Pending request                     | Approve / grant administrator                           | Requested institution/module/sensitivity preserved; grant policy/expiry/SoD valid | Approved + scoped binding, in one transaction             |
| Pending request                     | Create a grant satisfying request / grant administrator | Matching effective complete scoped grant                                          | Approved with resolution evidence                         |
| Pending request                     | Reject / grant administrator                            | Pending tenant-owned request                                                      | Rejected with decision reason                             |
| Approved/rejected request           | Decide again                                            | No legal command                                                                  | Not found; resolved request immutable                     |

SSO admission is a distinct user workflow: pending inactive → active via explicit
scoped approval, or pending → rejected. A later valid SSO request may reopen a
rejected admission; it is not a transition of the resolved scoped access-request
row. Grants are active → revoked; expiration is derived from validity time rather
than a mutation to a resolved access request. Session invalidation advances the
authorization version and revokes stale refresh families. These are separate
models with separate terminality rules.

## Asynchronous and event-derived workflows

| Workflow / source                  | Command / actor                         | Guard                                                    | Destination / effect                                                                |
| ---------------------------------- | --------------------------------------- | -------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Job: none                          | Enqueue / authorized caller             | Registered job type; tenant envelope; coalescing         | Queued, or reuse coalesced queued job                                               |
| Queued                             | Claim / worker                          | Due time and job-type lane; row lock                     | Running; start timestamp committed                                                  |
| Running                            | Complete / owning handler               | Handler succeeds                                         | Succeeded; completion time, clear interim error                                     |
| Running                            | Fail / owning handler                   | Attempts below maximum                                   | Queued with incremented attempt and backoff                                         |
| Running                            | Fail / owning handler                   | Exhausted attempt budget                                 | Failed with completion/error history                                                |
| Running                            | Reclaim / reaper                        | Job-specific stale window                                | Queued/backoff or failed at exhaustion                                              |
| Succeeded/failed                   | Claim                                   | Not queued                                               | No claim; terminal job history                                                      |
| Ingestion: none                    | Start / bank-scoped ingestion caller    | Valid mapping, supported adapter                         | Created then extracting                                                             |
| Extracting                         | Extract/translate / ingestion pipeline  | Extraction and identity locks succeed                    | Translating then validating; lineage recorded                                       |
| Validating                         | Persist validation / ingestion pipeline | Validation result                                        | Accepted, accepted_with_warnings or rejected; rejected canonical writes rolled back |
| Active ingestion                   | Failure / ingestion pipeline            | Boundary/configuration failure                           | Failed; reason/error history                                                        |
| Accepted/accepted_with_warnings    | Retry same inputs / ingestion caller    | Same tenant/bank/source/date/content/mapping fingerprint | Reuse accepted batch, no duplicate canonical writes                                 |
| Rejected/failed                    | Retry / ingestion caller                | Inputs/mapping supplied anew                             | New batch; old result remains history                                               |
| Alert: no prior verdict or cleared | Evaluate / scoped BI worker             | Real authorized figure breaches threshold                | Breached event + notification                                                       |
| Alert: breached                    | Evaluate / scoped BI worker             | Real authorized figure no longer breaches                | Cleared event + notification                                                        |
| Any last alert verdict             | Evaluate                                | Same fingerprint already recorded                        | Already recorded; no duplicate event                                                |
| Any last alert verdict             | Evaluate                                | Missing figure/threshold/authority                       | Not evaluated event; last breach/clear verdict remains                              |
| Unchanged alert verdict            | Evaluate                                | Fresh fingerprint, same threshold verdict                | Unchanged; no new breach/clear event                                                |

Queue functions assume the worker owns the claimed job; row-lock/concurrency
properties need Postgres tests in addition to sequence tests. Alert verdicts are
derived from append-only events, not a writable status column. Maker-checker is
inapplicable to autonomous job execution, batch progression and alert evaluation;
authorization belongs at enqueue/start/read boundaries. Tenant scoping and
idempotency still apply. Frozen-report immutability concerns the downstream filing,
not an ingestion batch's validation report or an alert's last observed figure.

## Remaining status-bearing families

These model owners are the census for the next inventory passes, not a claim of
stateful coverage. Record their command/guard/effect tables before adding machines;
do not infer legal transitions solely from a column CHECK constraint.

| Family                                                  | Model / owning interface pointers                                                                                                                                                                   |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Other governed approvals and freezes                    | `models/{regulatory_parameter,system_of_record,capital_plan,liquidity_cfp,canonical_withdrawal,stress,market_desk,market_desk_curves,desk_operating_environment}.py`; corresponding service modules |
| Attestation and signing requests                        | `attestation/models.py`; `attestation/service/`                                                                                                                                                     |
| ICAAP model/override and AI review                      | `icaap/models/{risk_capital,ai,workspace}.py`; `icaap/service/`                                                                                                                                     |
| Forecast assumptions and scenario review                | `forecasting/models.py`; `models/scenario.py`; `forecasting/api/`; `services/scenarios.py`                                                                                                          |
| Calculation/assessment execution and evidence           | `models/{calculation,capital,regulatory_run,regulatory,risk,implied_rating}.py`; feature calculation services and jobs                                                                              |
| Live engine publication and ingestion mappings          | `models/live.py`; `models/ingestion.py`; `services/{live_state,live_view,ingestion}.py`                                                                                                             |
| Connections, key custody and pulls                      | `models/{database_connection,temenos,market_data}.py`; `core/key_management/`; connector/pull services                                                                                              |
| BI commentary, outbox/deliveries and notification state | `models/{bi_commentary,bi_notifications,bi_content}.py`; `services/bi/`                                                                                                                             |
| Identity profiles, grants, ownership and entitlements   | `identity/models/{institution_profile,authorization}.py`; `models/entitlements.py`; `identity/service/`                                                                                             |
| Legacy findings and document parsing                    | `models/{financial,risk}.py`; feature APIs and worker handlers                                                                                                                                      |

## Bounded, reproducible execution

`tests/conftest.py` registers and loads Hypothesis profile `ci`: 25 examples,
20 stateful steps, no wall-clock deadline, `derandomize=True`, `print_blob=True`.
`HYPOTHESIS_PROFILE` may select an explicitly registered profile. There is no
scheduled property-test CI job, so no unbounded or larger nightly profile is added.
The filing/freeze machines lower examples to 12 because each builds real computed
positions and/or frozen reports; they retain 20 generated steps. Failure output
contains a minimized command sequence and reproduction blob.

Each machine example owns a connection savepoint and a separate session whose
service commits release only session savepoints. Teardown closes that session and
rolls back the example, including audit rows and earlier successful commits. The
outer pytest fixture remains isolated; Postgres uses its disposable schema. Never
point these mutating machines at the primary database.

Setup runs in a Hypothesis initializer so failures still receive teardown. Refused
commands roll back the request transaction before comparing persisted evidence,
matching the API session boundary without discarding earlier committed actions.

## Defects retained as regression examples

- Filing: submit an approved return manually, then submit it again. The second
  attempt must refuse without adding a receipt. Governed downtime-email → ORASS
  re-upload remains a separate channel operation.
- ICAAP: freeze, return to preparation or an approval stage, complete the new
  round and freeze again. A return belongs to the round it closes; recording it
  in the new round invalidated that round's fresh approvals. Persisted legacy
  returns are interpreted using their package's frozen decision at the shared
  state-loading boundary. The compatibility regression completes fresh approvals,
  refreezes, and performs another return/refreeze while preserving the original
  decision row, its recorded round, and every sealed snapshot.
- ICAAP: freeze, then return to the freeze stage itself. This is a legal governed
  return, but the database CHECK admitted only strictly earlier targets. The
  migrated constraint permits same-stage returns only when they name a sealed
  package; in-review returns and forward returns remain refused.

The freeze regression covers each return target and checks the old sealed
snapshot after refreeze. `tests/db/test_icaap_filing_migration.py` exercises the
constraint against a migrated PostgreSQL schema, including its negative cases.

```sh
cd backend
CASHFLOW_FAST_TEST=1 uv run pytest tests/reporting/service/test_filing_state_machine.py tests/icaap/service/test_freeze_state_machine.py
AQS_LOCAL_SERVICES=native uv run python scripts/local_services.py run --postgres -- uv run pytest tests/reporting/service/test_filing_state_machine.py tests/icaap/service/test_freeze_state_machine.py
```
