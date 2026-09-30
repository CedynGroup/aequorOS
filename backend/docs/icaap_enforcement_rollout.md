# ICAAP workspace enforcement rollout

The ICAAP workspace is part of the product for every bank tenant. It is served
only to institutions inside the ICAAP regime, and only to principals holding one
complete scoped binding. Run this inventory against each target deployment
immediately before release, and store the dated output with the deployment
record. Do not copy production identities into this repository.

## The gates, in the order they run

Every `/api/v1/banks/{bank_id}/icaap/*` route applies the following gates (the
object gate applies where an object is referenced), and
the order is the policy. There is no deployment switch: the licence class and
the caller's authority are what decide eligibility.

1. **Is the institution this tenant's?** An institution in another organization
   answers **404** and emits a cross-tenant telemetry record.
2. **Is the institution inside the regime?** ICAAP applies to banks and
   financial holding companies. Anything else answers **404** — not 403, which
   would imply the surface might apply. An unresolvable licence class raises its
   own **409** unchanged (fail-closed).
3. **Does the caller hold the authority?** Only then does a missing binding
   become **403**: the caller's own institution existing is not a secret from
   them.
4. **Is the object this institution's?** Every object in the path or body is
   resolved under the organization **and** the institution named by the route.
   Another tenant's object, a sibling institution's object, and an object nobody
   holds get the same answer — **404** for a path reference — so the refusal
   reveals nothing. A pre-authorization condition (four eyes, maker-checker)
   resolves its object the same way; an object it cannot resolve passes the
   condition and leaves the route to answer its 404.

## Required authority

Every route requires one complete binding on **CAPITAL** at sensitivity
**CONFIDENTIAL** for that exact institution, with the permission below. A
CAP/aggregated binding is not sufficient; neither is a binding on a sibling
institution, and no route infers authority from `users.role`, token `roles[]`,
a different module or a different sensitivity.

| Permission | Surfaces |
|---|---|
| `view` | framework list and detail, block-type catalogue, cycle list and detail, readiness, section list/detail/versions, block list/detail/bindings, attachment list and download |
| `create` | creating a cycle; re-basing a cycle onto a newer framework version |
| `edit` | cycle update and archive; section autosave, version commit and requirement state; block create, refresh, pin, unpin, manual table and retire; attachment upload and withdrawal |
| `export` | the draft PDF and Word exports |

For the permissions in this table: **ANALYST** satisfies view/create/edit/export
(the full preparer role); **APPROVER**, **AUDITOR** and **VIEWER** satisfy view
only. Review and approval authorities are described
[below](#review-freeze-and-filing-p3-the-separated-authorities).

`require_icaap_create` and `require_icaap_edit` are registered in
`MUTATION_ROLE_DEPENDENCY_NAMES`, so the impersonation route sweep classifies
the write routes correctly. `require_icaap_view` and `require_icaap_export`
guard reads and are deliberately not listed.

## Examiner access

An operator acting as examiner reaches ICAAP through the one explicit branch in
`require_icaap_view`. It reads no binding, is unavailable to every other
permission, and returns only cycles that have been **frozen** — a workspace
before a freeze holds text nobody has signed and figures nobody has reviewed.
Every ICAAP read goes through `services/icaap/guards.get_cycle_or_404`, which is
where that limit lives; P3 must keep routing new reads through it.

Impersonated sessions remain structurally incapable of any unsafe method.

## Deployment

- There is no workspace switch; `app/core/config.py::IcaapSettings` holds the
  settings below.
- `ICAAP_FRAMEWORKS_ENABLED` (default `bog_icaap`) is the per-framework switch.
  A framework may be published as reference data long before the platform can
  file to that regulator.
- `ICAAP_MAX_ATTACHMENT_BYTES` (default 25 MB) caps evidence uploads.
- `ICAAP_SIGNING_ENABLED` gates the signing ceremony on the ICAAP report, never
  the workspace.

## Before onboarding a tenant

Run the access-impact inventory and read the output before the tenant's
preparers are expected to use the workspace:

```
uv run python scripts/authorization_access_impact.py
```

Confirm for the target tenant that the principals who will prepare the ICAAP
hold a CAP/confidential binding with `create` and `edit` on the institution,
and that the reviewers hold one with `view`. A tenant whose members hold only
CAP/aggregated bindings will see the navigation entry hidden and every route
answer 403.

## Governed parameters

The workspace resolves its deadline, amber window, materiality thresholds and
checklist horizons from the regulatory-parameter control plane
(`app/services/icaap/parameters.py` lists the codes; `202609190055` seeds them).
A missing code is a typed `missing_parameter` refusal naming the code, never a
default. Confirm the seeded rows are present for the target jurisdiction, and
that staff know the values are editable in Admin → Regulatory Parameters.

## Risk and capital (P2): the four additional authorities

The risk register, the appetite statement and the Pillar 2 register are part of
the ICAAP surface and take the SAME CAP/confidential bindings as the rest of it
— `view` to read, `create`/`edit` to change. Four actions need something more,
and each separation is the point of the action:

| Action | Authority | Why it is not `edit` |
|---|---|---|
| Approve a Pillar 2 figure (`approveIcaapPillar2Item`) | CAP/confidential `approve`, plus a maker-checker condition that the caller did not author the revision being approved | The author of a capital figure approving it is not a second pair of eyes |
| Confirm or withdraw a supervisory add-on | CAP/confidential `approve`, plus four eyes against the person who recorded the letter | The database enforces `confirmed_by <> created_by`; this layer answers with an authorization decision instead of a constraint error |
| Record the independent review (`createIcaapAuditReview` and its siblings) | ICAAP `view` **and** an ORGANISATION-wide `Audit`/confidential `create` binding, with a maker-checker condition that the caller is not a participant of this cycle | Internal audit must not hold the authority it is reviewing. An "Analyst on Audit, organisation-wide, confidential" grant expresses the IA function; the AUDITOR bundle is view-only and therefore cannot record a review |
| Propose a capital-plan update (`proposeIcaapCapitalPlanUpdate`) | ICAAP `edit` **and** the capital plan's own drafting authority (`create` for a new version, `edit` for an existing draft) | The proposal writes a capital-plan DRAFT through `capital_plan.put_capital_plan`; without this an ICAAP editor could author a plan they are not entitled to author |

| Request, insert or discard an AI draft (`requestIcaapAiDraft`, `acceptIcaapAiDraft`, `rejectIcaapAiDraft`) | CAP/confidential `edit`, **plus** the AI egress gates: the deployment kill-switch, the tenant's Organisation-Owner consent, the named feature, the current consent version, and — in a deployed environment — a committed approved configuration | `edit` says the caller may change this report; it does not say this organisation has agreed to send its figures to an external service. The two answers are unrelated, so they are asked separately. An APPROVER may READ a draft (those routes stay on `view`) but may never request or insert one, which keeps maker and checker apart at the route |
| Change the organisation's AI settings (`getAiCommentarySettings`, `updateAiCommentarySettings`) | The explicit **Org Owner** binding (`require_ai_settings_administration`, the same authority as grant administration) | Deliberately NOT scoped Account administration. A scoped Account administrator configures account surfaces; deciding that this organisation's figures may leave the platform is the one decision its Owner makes personally |

Two notes on the AI rows. The deployment kill-switch answers **404** before any
tenant is resolved, so a platform with AI switched off presents no AI surface and
answers identically to everyone; the tenant-level gates answer **403** with a
machine code, because "your Owner has not switched this on" is something the
tenant can act on. And every gate is re-checked when the job RUNS, not only when
it is enqueued — a request can sit in the queue across a toggle change, and a
queued request must never be the one call that still goes out.

## Before enabling AI drafting

`AI_COMMENTARY_ENABLED` defaults off and
`app/services/ai/approved_configurations.json` ships EMPTY, so a deployed
environment refuses every request until a reviewed commit adds the exact
`(feature, prompt_version, model, effort, app_env)` tuple. The full checklist —
data-protection opinion, provider terms and DPA, counsel review of the consent
text, the offline eval thresholds, the security review, and deploying the AI
worker as its own Coolify app so `ANTHROPIC_API_KEY` never enters the core
containers — is in `.ai/icaap/agent-reports/P4.md` §9.

"Participant of this cycle" is a fact gathered from the rows themselves
(`services/icaap/participants.py`): whoever created the cycle, committed or
edited a section, bound a figure, uploaded evidence, scored a risk, saved an
appetite metric, authored a Pillar 2 revision, computed or explained a
reconciliation line, saved an allocation, or answered a challenge. The
reviewer's own report attachment is excluded, so filing the report does not
make the reviewer ineligible. P3 appends the stage deciders.

## Before relying on the risk and capital half

The twenty Pillar 2 parameter codes (`202609190056`) must be present for the
target jurisdiction in addition to P1's eight. `GET …/cycles/{id}/parameters`
lists every code the workspace reads at the cycle's as-of date, with its
provenance, and names the missing ones — read it for the target tenant before
its preparers rely on a Pillar 2 figure.

Fifteen of those rows are REPRESENTATIVE calibrations (their citation begins
`REPRESENTATIVE:`) and every one ships `pending`. Outputs label them, readiness
warns on any figure that rests on one, and the founder or the regulator has to
confirm them before a real filing relies on them (D-039).

## Review, freeze and filing (P3): the separated authorities

The review chain, the freeze, the workflow templates, the disclosure and the
revision clone take the same CAP/confidential bindings. Every write that changes
who has signed off carries a separation condition as well, evaluated before the
route runs so the refusal is an authorization decision with a trace:

| Action | Authority | Separation |
|---|---|---|
| Submit for review; draft or submit the disclosure; update or submit a workflow template | `edit` | — |
| Propose a workflow template; clone a cycle as a revision or an update | `create` | — |
| Decide a review stage (`decideIcaapStage`) | `review` | The decider neither prepared the report nor decided another stage of this round |
| Decide an approval stage (`decideIcaapStage`) | `approve` | As above. An unresolvable stage takes `approve` |
| Freeze (`freezeIcaapCycle`) | `edit` | Nobody who reviewed or approved the round (D-030): freezing makes the actor the package's preparer of record |
| Send a frozen ICAAP back (`returnIcaapCycle`) | `review` | Not the officer who froze it |
| Decide a workflow template (`decideIcaapWorkflowTemplate`) | `approve` | Not its proposer |
| Approve the disclosure (`decideIcaapDisclosure`) | `approve` | Not whoever chose what to publish |

The current **APPROVER** bundle carries both `review` and `approve`; there is
no separate Reviewer bundle. The stage permission split alone therefore does
not separate officers: the conditions above enforce that separation.

These conditions follow the institution-scoped resolution rule in
[the gates above](#the-gates-in-the-order-they-run).

## Object references

Every ICAAP route that carries an object identifier is in the object-reference
census (`backend/tests/fixtures/object_reference_routes.py`). The coverage layer
(`backend/tests/api/test_authorization_object_reference_coverage.py`) refuses
each one on the cross-organization, sibling-institution and single-foreign-child
layouts, and `test_icaap_refusal_is_the_unknown_object_answer` holds ICAAP to
the stricter shape in
[the verification contract](authorization_foundation.md#executable-verification).
A foreign attachment named in a request BODY is refused with the same **422**
(`evidence_required` or `unknown_attachment`) an unknown attachment gets; it is
not a 404 because the object addressed by the path does exist.
