# Filing workflow redesign: Preparer → Approver → Validator

**Status:** design, with steps 1, 3 and 4 BUILT.

**Step 1 (2026-09-20)** — `Permission.SUBMIT`, the `validator` bundle and the
submit/approve split, with no migration and nothing backfilled. Its as-built
contract is [filing_submit_authority_rollout.md](filing_submit_authority_rollout.md);
read that before changing anything in §3.3 or §6 step 1.

**Steps 3-4 (2026-09-20)** — the shared stage engine, the package-plane chain,
`checks_passed`, and the transmission gate at the choke point. As built:

- The engine is `app/domain/workflow/chain.py` (pure: stage-field parsing, the
  round rules, the D-067 scale normaliser) plus
  `app/services/filing_workflow/engine.py` (the blocker ladder, the officer-title
  check, the send-back rules, signer display). **Both planes run it.**
  `app/domain/icaap/workflow.py` and `app/services/icaap/workflow.py` were
  rewritten onto it with byte-identical refusal sentences; ICAAP keeps its own
  structural rules (a chain that starts with a preparation and ends at the Board,
  with exactly one freeze stage) and its own `Stage` shape, because
  `freeze_on_approve` is an ICAAP concept.
- The filing chain's shape is `app/domain/filing/workflow.py`. Its structural
  rules: exactly one preparation stage first, at least one reviewing stage after
  it, exactly one `transmit_on_approve` stage which must be LAST and must be an
  approval. `DEFAULT_STAGES` is Bernard's Preparer -> Approver -> Validator,
  seeded as the default and editable through `filing_workflow_templates` (per
  bank, versioned, maker-checker, sealed on approval) exactly as ICAAP's is.
- **`checks_passed` is the authority on machine validation.** `validation.py`
  writes it; `request_approval` and the attestation certification gate read it.
  The `validated` status survives only as a projection of "checks passed and the
  return has not been sent for approval yet", and `pending_approval`/`approved`
  are projections of the chain's position (`chain.project_status`).
- **The transmission gate lives in `regulatory_reporting.workflow.transition`**,
  the single writer of `-> submitted`, not on a route. Every channel, the manual
  record and the BG/FMD/2026/07 re-upload pass it.
- **SoD is re-checked under the row lock.** `get_package_or_404(..., for_update=True)`
  then `_decision_blocker` on the locked state. The stage names the authority:
  `stage_permission` returns `SUBMIT` for the transmitting stage, `REVIEW` for a
  review, `APPROVE` otherwise, so the `validator` bundle cannot take the
  Approver's stage and the approver bundle cannot take the Validator's.
- Migration `202609200064`. Terminal packages are untouched; in-flight
  (`pending_approval`/`approved`) packages have the default chain pinned and
  their existing `regulatory_package_approvals` rows projected into
  `package_stage_decisions`. No stored digest moves.

**Approving and releasing are TWO acts (founder decision 2026-09-20).** The
officer's row reads `Approve | Reject | Send to Validator`, and the third button
is unavailable until the approval exists — the Validator's row is the same
shape, ending in `Transmit to BoG`. So `chain.decide` records the decision and
**deliberately does not advance**; `chain.hand_off` is the only way forward, and
`awaiting_hand_off` (derived, not stored) is the state between them that the
screen reads to decide what to offer. The route — not the chain — performs the
transmission when the stage it hands off from is `transmit_on_approve`, because
filing is an outward act and belongs where the channel and its errors are.

The rule does not depend on attestation. A checker's SIGNATURE and their
approval remain one act (`chain.record_stage_approval`, same transaction), but
that act now also stops at the approval stage: a bank that signs would
otherwise get the collapsed behaviour back, with the return arriving at the
Validator on a signature nobody read as "send".

The cost was argued and accepted: a return can sit approved and un-sent. The
queue keeps such a return visible precisely so it cannot be lost.

**Open: the Validator does not appear on a SIGNED document.** With signing off,
the attestation page prints three record rows — "Prepared by", "Reviewed and
approved by", "Released for filing by" — from the chain's own decisions
(`exports/pdf.py::_RECORD_ROWS`, and the same rows on BoG's own grid in
`bog_forms/render_pdf.py`). With signing ON there are only two slots, because
the signing roles are `preparer|approver|board|witness` and there is no
`validator`. It cannot be patched at transmission time: DocMDP requires every
field to exist before the first signature, and the Validator decides after the
Approver has already sealed the document. Giving the Validator a row on a
signed return therefore means a `validator` signing role with its own field set
placed by the Preparer, alongside the others — design work, not a fix.

**Step 2 and step 5 remain design** (the UI rename and the chain view). The read
contract the dashboard binds to is `GET
/banks/{bank_id}/regulatory-packages/{package_id}/workflow`.
Written 2026-09-20 after the founder's review with Bernard.
**Requirement (Bernard):** three distinct roles. The Preparer prepares and cannot validate.
The Approver reviews, and may send back with comments. The Validator reviews, approves, and
**is the only role that transmits to BoG through ORASS** — and may send back to either the
Approver or the Preparer.

---

## 1. What exists today, and why the screen misleads

The package lifecycle is a linear status chain:

```
draft → generated → validated → pending_approval → approved → submitted → acknowledged
```

Four findings, each verified in code:

1. **`validated` is a machine result, not a person.** `validation.py:462` sets
   `package.status = "validated" if passed else "generated"` — it is the rules engine
   reporting that the return has no validation errors. The dashboard renders it as a step
   labelled **"Validated"** (`LifecycleStepper.tsx:17`), positioned before approval. A bank
   reads that as "the Validator signed off". It means neither that, nor at that point in the
   process.
2. **No Validator exists.** `SigningRole` is `preparer | approver | board | witness`, and no
   authorization bundle or stage models a third reviewer.
3. **Approval and transmission are ONE authority.** `require_package_approve` requires
   `Permission.APPROVE`; `require_package_submit` requires **the same permission**. An
   approver can therefore file to the regulator alone. This is a live segregation-of-duties
   gap, independent of the UI. **CLOSED 2026-09-20 (step 1).** It was worse than written
   here: on an ungated family — every BSD, liquidity, capital and FX return, i.e. every
   return that actually reaches ORASS — the submit dependency fell back to the scalar
   ladder, so the scalar `approver`/`admin` role alone was sufficient with no binding at
   all. Both halves are gone; see the rollout contract.
4. **Send-back is one coarse edge.** `pending_approval → generated` exists, but the package
   records no *target* for the return and carries no round counter, so a second rejection is
   indistinguishable from the first in the package's own history.

## 2. The principle: we already own this engine

P3 built, for ICAAP, precisely the shape this requires: `icaap_workflow_templates` (per bank,
versioned, maker-checker), a stage chain, `icaap_stage_decisions` with round and review
digest, return-to-a-named-stage, officer titles checked against `users.job_title`, and
separation of duties enforced server-side.

**The redesign generalises that engine to the BoG package plane rather than adding a third
hardcoded status.** Hardcoding "Validator" would repeat the mistake that produced this
problem: "Approver" was hardcoded, and the next bank's process did not fit it. Banks will
differ — three stages here, four elsewhere, "Compliance Sign-off" instead of "Validator".

**Ship Bernard's three as the seeded default template, not as the only possibility.**

## 3. Target model

### 3.1 Separate the three things the current design conflates

| Concern | Today | Redesign |
|---|---|---|
| Machine validation | a `validated` **status** in the middle of the chain | a package **attribute** (`checks_passed`) that gates ENTRY to the chain. UI says "Checks passed", never "Validated" |
| Human review | two statuses (`pending_approval`, `approved`) | a configurable **stage chain** with decisions, comments, rounds |
| Transmission to ORASS | `submit`, sharing `Permission.APPROVE` | its own authority, held only by the final stage |

### 3.2 The default chain (seeded, editable)

```
Preparer ──submit──▶ Approver ──approve──▶ Validator ──approve+transmit──▶ ORASS
   ▲                    │                      │
   └────send back───────┘                      │
   └───────────────send back (to either)───────┘
```

Every backward edge is a **named target plus a mandatory comment plus a round increment**.

### 3.3 Authority — defence in depth, not one check

Three independent layers, because this codebase's repeated failure mode is a gate lost at a
seam (D-069: a filing path lost its reconciliation gate where two owners met; the attestation
family gate was applied per route and missed six routes):

1. **A distinct permission verb for transmission.** Introduce `Permission.SUBMIT`. Reusing
   `APPROVE` for both approving and filing is exactly the conflation that created finding 3.
   A separate verb means that even if a stage check were ever bypassed or a new route added
   without it, an approver still cannot transmit. **Built.** It is carried by one bundle
   (`validator` = `view` + `submit`, never `approve`) over one gate for every family
   (REG / restricted / exact institution), and `SUBMIT` names maker/checker as REQUIRED
   context, so a future route that omits it is denied rather than allowed.
2. **Stage position.** Only the principal holding the chain's final stage may transmit, and
   only when every prior stage has approved in the current round.
3. **Separation of duties**, on ICAAP's C-9 pattern: whoever prepared this round cannot
   approve it; whoever approved cannot validate it. Re-checked under the row lock at the
   moment of the act, not only when the screen renders. **Partly built:** the preparer
   cannot file (a maker/checker condition on every family), and Approver + Validator on one
   identity is BLOCKED at assignment. The *per-object* "whoever approved this round cannot
   file it" check is this step's remaining work — when it lands, the assignment-time block
   may become a warning, matching Analyst/Approver. Never relax it earlier.

`Permission.SUBMIT` touches the authorization foundation (bundle definitions, an
`authorization_bindings` migration, an `authv` bump and refresh-family revocation). That cost
is the point: it is the difference between two roles and two *authorities*.

As built, the migration turned out to be the one part that should NOT exist: backfilling
transmission authority onto everyone who held approval authority would have encoded the
defect under the new verb, and this cutover changes no `users` row, so the pre-cutover set
stays queryable from the scalar role. Nobody is granted anything; the `authv` bump and
refresh-family revocation happen in the existing binding-creation primitive when an Org
Owner writes the Validator sentence. Measured consequence on the primary: one identity
could transmit before, zero can after, and no package anywhere was mid-filing.

### 3.4 Decisions carry evidence

Each stage decision records: actor, role, stage, **round**, decision
(`approved | returned`), the return target when returned, a mandatory comment, the officer
title asserted, and a **review digest** of what was decided against — so an approval expires
if the package changes underneath it. ICAAP already proved the sharp edge here: its digest
depended on SQLAlchemy's reload state, so `Decimal("1")` and `Decimal("1.000000")` produced
different digests and **a reviewer's approval expired because nothing had happened** (D-067).
The shared implementation must normalise scale, and the test that catches it must come with it.

### 3.5 Signing

The attestation design already treats "which officers sign which return" as configurable rows
(spec §8 C2), and `ordered_slots` exists. So a third signature slot for the Validator is a
**policy row, not code** — and `ICAAP_SIGNING_ENABLED` / `ATTESTATION_ESIGN_REQUIRED` continue
to govern whether signatures are demanded at all. Decide per return family whether the
Validator signs before transmitting or merely authorises; do not assume.

## 4. What changes, by surface

- **Backend:** a shared `filing_workflow` service lifted from `services/icaap/workflow.py`;
  `Permission.SUBMIT`; `require_package_submit` rewritten to it; stage/decision tables for the
  package plane; `checks_passed` replacing the `validated` status; send-back routing and rounds.
- **Lifecycle statuses:** `pending_approval`/`approved` become projections of the chain's
  position rather than the source of truth. **Existing sealed and submitted packages must not
  change** — the migration maps in-flight packages onto the default template and leaves
  terminal ones alone.
- **Dashboard:** the linear `LifecycleStepper` is replaced by a chain view — the stages, who
  holds it now, every prior decision with its comment and round, and **only the control the
  signed-in user can actually exercise**. The current stepper shows six fixed statuses that do
  not correspond to people, which is the visible half of this whole problem.
- **ORASS:** unchanged mechanically; the gate in front of it changes.

## 4b. The frontend, designed

Today `app/(app)/submissions/returns/page.tsx` renders **Generate, Validate, Approve and
Submit-to-ORASS in one page for everybody**, each with a disabled state and a blocked reason.
That superset screen is the mess: it shows a Preparer the machinery of filing to the regulator
and asks them to infer, from greyed-out controls, that none of it is theirs.

**Principle: the screen shows the user's own work, not the union of everyone's.**

### 4b.1 Absence vs disabled — the distinction that makes this coherent

- **Absent** when the surface is not this role's at all. A Preparer never sees ORASS, the
  channel picker, the downtime bundle, or the resubmission workflow. Not greyed — gone.
- **Disabled, with the reason** when the control IS this role's but it is not their turn, or a
  precondition is unmet ("two checks still failing", "waiting on the Approver"). The codebase
  already has the right primitive: `DisabledWithReason` renders `role="link"`/control with
  `aria-disabled="true"` and the reason on hover, and is already used by the navigation.

Getting this backwards in either direction is the current failure: everything is disabled, so
nothing communicates.

### 4b.2 The "Validate" button disappears

Machine validation is not a human act, so it stops being a button. Checks run as part of
generation, and the result is a **panel a Preparer must clear** — "3 errors, 1 warning", each
naming the rule and the line it failed on — not a lifecycle step someone presses. The word
"Validated" leaves the product entirely for this meaning; it belongs to the Validator.

If a manual re-run is genuinely needed, it lives beside Generate as "Re-run checks", because
that is what it does.

### 4b.3 Three surfaces, one workspace

| | Preparer | Approver | Validator |
|---|---|---|---|
| Generate / re-run checks | **yes** | no | no |
| Check results panel | **yes**, must clear | read | read |
| Send for approval | **yes** | — | — |
| Approve / send back + comment | no | **yes** | **yes** |
| Choose send-back target | — | — | **Approver or Preparer** |
| Transmit to ORASS, channel picker, downtime bundle, resubmission | **absent** | **absent** | **yes** |
| Chain panel (stages, holder, decisions, round) | read | read | read |

The role comes from the **projected capability and the package's current stage**, never from a
scalar session role — `AGENTS.md` is explicit that shell navigation and deep links consume the
`/auth/me` projection, and the ICAAP work already found a panel computing "is approver" from
`roles.includes('approver')`, which is how a Board member with a correct binding was never
offered the control they held.

**Fail closed and hidden-until-resolved.** Before the projection resolves, show nothing
actionable — not a Preparer default. A flash of the wrong role's controls is the same defect
in miniature.

### 4b.4 The chain panel replaces the stepper

`LifecycleStepper` shows six fixed statuses that do not correspond to people. It is replaced by
a panel that answers the three questions a filing officer actually has:

1. **Where is it, and who holds it now** — the stage, the role, and the named holder if the
   bank has assigned one.
2. **What has happened** — every decision in order: who, role, round, approved or returned, the
   comment, and the time. A send-back shows its target, so "returned to the Preparer" is legible
   rather than inferred from a status going backwards.
3. **What happens next** — the remaining stages, and for a Validator, that the next act
   transmits to the regulator. Transmission is stated in words before it is offered as a button.

Round changes are visible: round 2 does not look like round 1 with different content.

### 4b.5 Queues, not hunting

An Approver and a Validator each get a queue of what awaits **them** — the existing
`/submissions/approvals` and `/submissions/signatures` pattern — rather than opening returns one
at a time to discover whether it is their turn. The Preparer's equivalent is what has been sent
back to them, with the comment that sent it.

### 4b.6 What must be true when it ships

- A Preparer session contains no ORASS affordance anywhere on the returns surface — assert it in
  a test, the way `rehearsal.test.ts` asserts every package-identity surface carries the
  rehearsal marker.
- No control is rendered that the server would refuse for this principal at this stage; and every
  control that is rendered-but-disabled states why.
- The e2e journeys are rewritten to walk the three roles as three sessions. They encode today's
  two-step flow and must be **rewritten to assert the new rule, never loosened to pass**.

## 5. Risks, and what must not regress

- **In-flight packages.** A bank mid-approval when this ships must not be stranded. Backfill
  onto the default chain, with terminal states untouched.
- **ORASS parity.** `rejected` (returned for correction) and `declined` (final refusal) are the
  REGULATOR's outcomes and are not stages. Keep them distinct from an internal send-back, or the
  audit trail conflates "BoG rejected this" with "our Approver sent it back".
- **The e2e journeys** (`full-lifecycle`, the four attestation specs) encode today's two-step
  flow and will need rewriting to the new chain — **rewritten to assert the new rule, never
  loosened to pass**. Step 1 took the first bite: `scripts/e2e_bootstrap.py` seeds a
  `validator` fixture (read sentence + filing sentence) and `full-lifecycle` journeys 1–3
  now submit and poll in that third session instead of the preparer's. They have not been
  run — Playwright is not in CI (`risk-service.yml` gates the backend only), so treat the
  first run after this change as the verification.
- **Rehearsal packages** (D-068) must remain unfilable through every new path.

## 6. Recommended sequencing

1. **`Permission.SUBMIT` and the submit/approve split.** Smallest change, closes the live SoD
   hole, and is valuable even if nothing else ships. Do this first and independently.
   **DONE 2026-09-20** — [filing_submit_authority_rollout.md](filing_submit_authority_rollout.md).
   Each tenant needs a Validator grant before it can file again; the dashboard still needs
   a client regeneration plus `["validator", "Validator"]` in `lib/api/grants.ts` before the
   Members composer can offer it.
2. Rename the machine check in the UI — removes the active misreading in a day.
3. Lift the stage engine into the shared service; seed the three-stage default template.
4. Package-plane stages, decisions, rounds and send-back routing.
5. Replace the stepper with the chain view; rewrite the journeys.

Steps 1 and 2 are independently shippable and fix what is currently wrong. Steps 3–5 are the
enterprise capability and want their own phase, with the same discipline ICAAP got: a design
reviewed before implementation, audits after, and no test weakened to make it pass.
