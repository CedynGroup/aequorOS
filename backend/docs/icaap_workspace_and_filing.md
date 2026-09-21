# ICAAP workspace and filing — architecture and decision record

This document is the committed home for the reasoning behind the ICAAP
workspace, its Pillar 2 engines, its filing path and the AI drafting lane. It
exists because the build's working ledger lives under `.ai/`, which is
gitignored: without this file the eighty-odd recorded decisions, four audit
reports and the deviation register would not survive the commit that lands the
code.

It is **not** a specification and not a rollout runbook. Authority stays where
it already is:

| For | Read |
|---|---|
| who may reach which ICAAP route | [`icaap_enforcement_rollout.md`](icaap_enforcement_rollout.md) |
| the scoped-binding model those gates evaluate | [`authorization_foundation.md`](authorization_foundation.md) |
| where a BoG parameter's value came from | [`bog_parameter_sources.md`](bog_parameter_sources.md) |
| the shape of the filing plane a package lands in | `ARCHITECTURE.md` §3d |
| what the code does | the code, which wins over this file |

What follows is the part a future session cannot reconstruct from the code:
**why** each boundary is where it is, and which of them look like tidying
opportunities but are load-bearing.

---

## 1. The shape of the thing

Four planes, deliberately separate.

**The workspace** (`app/domain/icaap/`, `app/services/icaap/`,
`app/features/manage_icaap*.py`) is a narrative document under review: cycles,
sections of ProseMirror text, data blocks bound to computed evidence, a
requirement checklist, a stage/decision chain, attachments.

**The Pillar 2 engines** (`app/domain/icaap/pillar2/`, plus
`app/domain/credit/`, `app/domain/irr/standardised*.py`) are pure. They take
governed parameters and a book, and return typed results or typed refusals.

**The filing plane** is the existing regulatory-reporting plane, unchanged in
kind: one package table, one submission gate, one validation pipeline, one
exporter, one attestation chain.

**The AI lane** (`app/domain/ai/`, `app/services/ai/`) drafts section text. It
is a suggestion engine that a human accepts or rejects; it never computes a
figure and never writes to the filing plane.

The three seams between them are §2, §3 and §7.

---

## 2. There is a SECOND package-mint site, and it is a peer

`generation.generate_frozen_package` is **not** a variant of
`generate_package`. It is a peer entry point, used by the ICAAP freeze and by
the ¶82 disclosure approval, and which gates live on which side of the seam has
already gone wrong twice.

The division: the **caller** owns everything about WHAT is in the snapshot; the
mint site owns everything about what a package IS — versioning, supersession,
the content and register digests, the audit event. That is what makes an ICAAP
filing the same kind of object as every other return, read, exported, signed
and submitted by exactly the same code.

Two deliberate differences from `generate_package`, both recorded in
`registry.py`:

* **`effective_date` is not gated on this side.** A commencement date is not a
  generation gate: blocking on it would stop a bank preparing and dry-running a
  return before its first live filing. Every other blocking dimension — licence
  class, jurisdiction, supervisor — still applies.
* **The reporting period is resolved by the caller**, because a cycle's as-of
  date is the fiscal year end it was opened for, not a book that happened to
  arrive.
* **`commit` may be False**, so a caller minting the package as one step of a
  larger transaction (a freeze that also writes stage decisions and copies
  attachments) can roll the whole thing back. Nothing is half-frozen.

**The rule that cost two defects (D-069, and architecture audit M1): a filing
path must not lose its gates at a seam.** `generate_frozen_package` did not run
the reporting-period and reconciliation gates that the generic mint site runs,
so they had vanished from the ICAAP path entirely. *Either* owner may hold a
gate; **neither may drop it**. The two resolutions went in opposite directions,
deliberately:

* the **reporting-period and reconciliation** gates sit in `freeze_cycle`
  (`app/services/icaap/freeze.py`), because they need the cycle's as-of date and
  produce ICAAP-named 409s (`no_computed_position`, `filing_reconciliation`);
* the **withdrawn-evidence** gate sits at the MINT
  (`_assert_evidence_current`), because a freeze-minted filing binds engine runs
  exactly as a generated one does — so the next freeze-minted family inherits
  the refusal instead of having to remember it.

The test to write when you move one is "can this path reach a filing without
this gate?". An unreconciled book must be unable to freeze, and that is pinned.
This is the same class as the `derive_facts` fail-closed rule in AGENTS.md: the
refusal IS the feature.

When you add a gate to one mint site, the question is not "does the other one
need it?" but "which of the two owns it, and is that written down?".

---

## 3. `family_hooks` is the ONE seam a return family may use

`app/services/regulatory_reporting/family_hooks.py`. The regulatory reporting
plane is family-agnostic by construction. ICAAP is the first family that
genuinely needs more: a package minted by freezing a workspace cycle, a Board
resolution as a submission precondition, a document rendered from a frozen
narrative rather than a tabular template.

The wrong answer is `if package.return_family == "icaap"` scattered through
five services. Every generic service asks `for_family` once; a family that
declares no hooks changes nothing. Two properties are deliberate and easy to
"simplify" away:

* **Dispatch is lazy** (`importlib` at call time) because the hook
  implementations live in the family's own service package, which imports the
  generic plane. A module-level import closes the cycle.
* **Every hook has a no-op default**, so a family implements only what it
  changes and a hook added later cannot break an existing implementation.

If a new family needs generic-plane behaviour to differ, the change belongs in
a hook, not in a branch.

---

## 4. Governed parameters: no regulatory number lives in code

Founder directive (D-024), and the single most invasive rule in this build.

1. Every regulatory or methodological number — floors, buffers, caps, shocks,
   buckets, bands, coefficients, tolerances, deadlines, NMD caps, granularity
   parameters, materiality thresholds — is resolved at runtime through
   `app/services/regulatory_parameters.py`. Never a literal in engine, service,
   template or frontend code.
2. Numbers appear only in the seed catalogue and the migrations that pin it, as
   the initial approved rows. After that the database, edited in the operator
   console under maker-checker with effective dating, is the source of truth.
3. **Framework JSON references parameter CODES, never values** — including
   dates. `ICAAP-REPORT`'s commencement date and its submission window are
   governed rows (D-032).
4. A missing parameter is a typed `missing_parameter` refusal that names the
   code. **There is never a code fallback.**
5. Values with no published regulatory basis are seeded
   `confirmation_status="pending"` and every output says so (D-039). A
   representative calibration that presents as confirmed is worse than no
   calibration.

Guards: `tests/architecture/test_icaap_governed_literals.py`,
`test_icaap_no_regulatory_literals.py`, and — for the values a bank actually
files — `tests/services/test_regulatory_parameters_p5.py`, which holds the
shock magnitudes as a deliberateness gate: a change to a number a bank files
has to be made in a test that says so.

**Three copies of a number is the recurring failure.** The shock table exists
in the service catalogue, in the migration and in the domain test fixture. An
assertion that compares one of them with a constant in its own file proves
nothing. Compare copies against each other
(`tests/domain/irr/test_sf_fixtures.py::test_the_fixture_reproduces_the_seed_table`
compares the fixture to the seeded catalogue row by row; it caught a unit drift
the day it was written).

---

## 5. The digest / provenance boundary

`RegulatoryRun.parameter_provenance` is **not collected by the engine**. It is
drained from a session-scoped ledger (`regulatory_parameters._CONSUMED`) at the
moment a run row is built. So a run's provenance was really "everything this
`Session` resolved since the last run was sealed", not "everything this run's
calculation consumed".

That is how adding `ICAAP-REPORT` to the registry changed an unrelated
**liquidity** package's content digest (D-078). `generate_package` passes
through `eligibility.resolve_eligibility`, which — to decide which returns
exist for an institution — resolves **every distinct `effective_from_parameter`
in the whole registry, across every family**. Package generation seals no run,
so those rows sat in the ledger until the next engine run swallowed them.

**The boundary: the DISPATCH plane must not write to the CALCULATION plane's
ledger.** `PrefetchedParameterResolver` declares its plane at construction and
`load()` **requires the keyword — there is no default to fall through**. The
two registry-driven sites (`eligibility.parameter_resolver()`,
`calendar._governed_deadlines`) pass `record=False`; the engines pass
`record=True`. The ICAAP report plane seals no run at all and writes its own
governed-row record onto the frozen snapshot, so every ICAAP read goes through
`app/services/icaap/parameters.py` — the plane's one non-recording door, pinned
by `tests/architecture/test_icaap_boundaries.py`.

Nothing special-cases ICAAP or any parameter code. A future registry entry —
Nigeria, Kenya, an SDI pack — can only reach the ledger through that one
non-recording resolver, so there is no per-entry step for anyone to remember.

**Residual, flagged rather than silently widened:** module-level `try_resolve`
/ `resolve` still record unconditionally, and `icaap/snapshot.py`'s
`_parameter_provenance` uses them for its own report block. Same defect class,
but not registry-driven (no `ReturnDefinition` can reach it), so it cannot
reproduce that failure. Closing it properly means giving every engine an
explicit provenance window instead of an ambient ledger.

**Digests stay value-based**, the same rule AGENTS.md states for `input_hash`.
`content_digest` strips `metadata.generated_at` and the execution-identity keys
on each `provenance.source_runs` entry; the stored snapshot keeps the full
provenance untouched, because run identity, timing and actor are the evidence a
supervisor asks for. Never add a volatile field to a digest input, and never
remove a key from `VOLATILE_METADATA_KEYS` — that would change every historical
digest.

---

## 6. Freeze gates, and why the workspace fails closed

**Freeze is an explicit act** (D-030). The preparer freezes after the Board
Risk Committee stage approves; the spec's "freeze runs when BRC approves" was
rejected because anyone who decided a review or approval stage in the round
cannot freeze, and that is what keeps every reviewer eligible to sign. The
package's generator identity is the freezing preparer.

A freeze must satisfy, in one transaction:

* every framework section bound and sound — readiness returns no `blocking`
  item;
* the reporting-period and reconciliation gates (§2);
* the review digest the approval was taken on, still matching the text;
* the framework digest, still matching the published version;
* the framework's own attachment requirements, notably the Board resolution.

**The Board resolution is required by the FRAMEWORK, not by the signing
policy** (D-031). `ATTESTATION_ESIGN_REQUIRED=0` suspends signatures; it must
never also suspend a document the regulator requires the return to carry
(D-074). Relaxing a signature slot never relaxes an attachment.

**A published framework version is never edited in place.** A corrected
extraction ships as a new version with a `section_key_map`, and cycles move by
rebase. The detection (`framework_digest_mismatch`, severity blocking) works;
the process it protects was not followed during development, which is how a
day-old demo cycle became unfreezable.

**The failure mode to watch is soft gates in series.** A missing `data_block`
is a `warning`, and the validation rules that would catch a bad figure are
conditional on the block being present — so two warnings in series were not a
gate, and a report could freeze with no capital figures at all. Absence is now
an ERROR. When you add a validation rule, ask what it does when its input is
missing entirely.

---

## 7. The signing chain

Signing is required for every return by default; see AGENTS.md's attestation
entry for the platform rule. Three ICAAP-specific facts:

1. **Required signers come from the return's signing policy, not from a
   role-to-field map** (D-007). The lock chain changed so that only the FINAL
   signer locks all fields, which is what makes a three-signer chain possible
   at all.
2. **The Board slot is built and DEFAULTED OFF** (D-043). The regulatory matrix
   requires Board resolutions to accompany the submission (¶71), Board
   challenge and approval (¶45) and a Board attestation on stress (Stress ¶20)
   — but nothing in the recovered text requires the Board to e-sign the filed
   PDF. A bank turns the slot on per return in Settings, which is an audited
   change. Board approval is evidenced regardless of the slot, by the in-platform
   BRC and Board stage decisions, the challenge log with responses, and the
   mandatory `board_resolution` attachment.
3. **Existing two-signer returns are byte-identical.** That was the acceptance
   condition for the whole redesign and is pinned by the pre-existing
   attestation tests passing unmodified. DocMDP means every field must exist
   before the first signature, so the preparer places the approver's (and the
   Board's) boxes too.

---

## 8. Rehearsal: the rules, and why they are not a relaxation

A rehearsal cycle runs the **full lifecycle** — freeze, package, validate,
sign, attach, submit (D-029, upheld as D-068 against a schema that forbade it).
The reasoning matters, because "a rehearsal should not be able to freeze" looks
like the safe reading and is the wrong one:

> Both designs share the intent that a rehearsal must never become a filing.
> Two CHECK constraints enforced it by preventing the SAFE act (rehearsing).
> D-029 enforces it by blocking the DANGEROUS act (filing a rehearsal) at the
> point it would happen, behind five independent mechanisms. The second is
> strictly better: it buys the same safety and keeps the only thing rehearsal
> exists for while the BoG text is pending — letting a bank dry-run freeze,
> signature and submission, which are the riskiest steps in the regime.

The five mechanisms: the package carries `is_rehearsal=true`; it is watermarked
"REHEARSAL — not a regulatory filing" on every page and in DOCX; its submission
is recorded on a non-transmitting channel; the calendar and obligations never
count it; `ICAAP-DISCLOSURE` refuses a rehearsal package. A rehearsal can never
supersede or satisfy a real `ICAAP-REPORT` obligation.

Two things a rehearsal does **not** relax:

* a non-rehearsal freeze still refuses while any section is
  `pending_primary_text` (D-006) — that is about the FRAMEWORK's
  incompleteness, which a bank cannot fix;
* a missing data block still blocks, because that is something a bank CAN bind,
  and relaxing it would let the dry run skip the step it exists to rehearse.

Migration `202609190062` replaced the two rehearsal CHECKs with `is_rehearsal`
plus three CHECKs and service guards. Running it surfaced a defect one layer
down: `uq_regulatory_packages_current` did not carry `is_rehearsal` in its key,
so a bank filing a year it had previously rehearsed would have hit a duplicate
key **at the moment of filing**. Found by executing the migration rather than
reasoning about it.

---

## 9. Jurisdiction is data — including for ICAAP

A jurisdiction's ICAAP framework is JSON under
`app/domain/icaap/frameworks/<code>/`, with no `if jurisdiction ==` anywhere
(D-076). Ghana's four-month window, Nigeria's absent Table 5, Kenya's absent
disclosure, an absent return family and an absent method mandate are each data,
each with a test. This is the same rule AGENTS.md states for the jurisdictions
registry, applied one layer up.

### The sourcing caveats — read these before a customer relies on a framework

**Ghana (`bog_icaap`).** The ICAAP Guideline PDF is **not in the checkout**;
bog.gov.gh reset every connection during the build. Framework checklist items
are built only from sourced paragraphs; sections without sourced detail carry
the ¶49 heading plus sourced cross-instrument items and are tagged
`source_status="pending_primary_text"` — 15 of Ghana's 17 sections, at the time
of writing. Readiness says "checklist incomplete — pending BoG text" and a
non-rehearsal freeze refuses while any section is pending. `frameworks/gh/SOURCES.md`
is the citation manifest and a test pins every citation to it. Nothing is
invented and nothing is copied from another regulator.

**Nigeria (`cbn_srp_icaap_2021_09`) and Kenya (`cbk_icaap_gn_2016_11`) —
NEITHER PRIMARY TEXT WAS READ.** Neither PDF is in the checkout. The manifests
were built from a design session's extraction record. Both files carry a
`## Provenance of this manifest` section stating who read the text, that the
PDF is not committed so **the sha256 cannot be re-verified from a checkout**,
and exactly which edits were made; a test pins that section so it cannot be
quietly deleted. That is the right handling of second-hand provenance, but it
is **not** verified provenance.

> Before any customer uses the NG or KE framework, the primary texts must be
> obtained, committed or checksummed, and the citations verified. The CBK's
> 2026-09-10 consultation archive was never downloaded.

Also open by design, and visible rather than hidden: NG and KE have **no
governed parameter rows at all** (6 and 8 codes drafted). That gap is pinned as
a shrinkable ceiling in `tests/domain/icaap/test_framework_param_refs.py`, so it
cannot grow unnoticed.

### One thing that is NOT a jurisdiction leak

Currency codes as keys in the IRRBB shock table (D-064). The framework
prescribes a different shock per currency and prints them as a table whose row
keys are ISO codes. A currency code used as a DATA KEY is not a claim that any
bank reports in that currency — the same reading AGENTS.md gives a
`bog_`-prefixed identifier. Dropping the keys would destroy the regulator's own
table; resolving them from the bank would be a category error, because the
table is about the currency of the POSITION, not of the reporter. The
jurisdiction-neutrality guard exempts that one construct **by name, not by
file**, and a separate test proves a leak anywhere else in the same module is
still caught.

---

## 10. The AI lane

`icaap_ai_draft` is the only member of the `ai` job lane, and the lane exists
for one reason: **the model credential**.

* The AI worker runs from its own compose file
  (`backend/docker-compose.ai.prod.yml`) so `ANTHROPIC_API_KEY` never enters
  the core containers' shared `.env`.
* `WORKER_JOB_TYPES` accepts `lane:core` or `lane:ai`. **The default lane
  excludes AI by construction**, and `app/worker.py::resolve_job_types` refuses
  a process that mixes lanes — the process holding the model key runs nothing
  else, so a compromise of one handler cannot reach it. Each worker reaps only
  its own job types.
* The AI health check never 503s core readiness. An AI outage is not a platform
  outage.

This is the kind of rule someone "simplifies" into one worker with a longer job
list. Doing so puts the model credential in every core container.

Production gate (D-027): in staging and production, AI requests are refused
unless `AI_PRODUCTION_APPROVAL_REF` is set **and** the exact (prompt version,
model, effort) triple is listed in a committed `approved_configurations.json`,
which ships empty. Descriptors-only is the default mode — amounts and dates are
never sent as values — and users cannot add free-text instructions. Decisions
are append-only.

`app/domain/ai/` and `app/services/ai/` are a shared foundation, not an ICAAP
component, so BI commentary can reuse them.

---

## 11. Standing traps

* **Two schema classes may not share a NAME across modules.** FastAPI then
  emits `app__schemas__x__Name` component keys the TypeScript generator cannot
  map back, and client generation fails outright (D-047:
  `IcaapRequirementRead` existed twice; the Pillar 2 one is now
  `IcaapCapitalRequirementRead`).
* **A `Decimal` form field** is typed `number | string` by Pydantic and its
  generated alias lands in an operation request interface, not in
  `src/models/` (D-048). The client fixer handles it now; the shape is still
  worth knowing.
* **`multipart/form-data` has no types** — every field arrives as text, so a
  form-posted decimal or boolean must be parsed explicitly (D-050).
* **ICAAP is always available; there is no feature flag** (D-046, founder
  directive). The old `ICAAP_WORKSPACE_ENABLED` gate described in
  `icaap_enforcement_rollout.md` step 1 was removed.
* **The REVIEW/APPROVE ladder does not separate duties on its own.** `APPROVER`
  is the only bundle containing `REVIEW`, so `REVIEW ⟹ APPROVE` for every
  principal. What actually keeps a reviewer off the freeze stage is
  `domain.checkers`. Do not read a stage-derived permission split as separation
  of duties.
* **ΔNII is dimensionally correct only at the seeded 12-month horizon.**
  `standardised.py` computes `gap × Δr × (H − t)/H` where the identity is
  `gap × Δr × (H − t)` years. `irrbb_sf_nii_horizon_months` is operator-editable;
  an edit to 24 months silently halves every ΔNII and no test fails today.
* **The platform tells the filer that the framework prescribes no post-shock
  rate floor.** BCBS d368 §132 does prescribe one. Saying the CODE applies no
  floor is honest; saying the FRAMEWORK prescribes none is a factual claim about
  a supervisory standard, and it is wrong under Basel. Materiality is nil for a
  GHS book and real for foreign-currency legs.
* **`_SF_SHOCKS_BY_CURRENCY`'s non-GHS columns diverge from BCBS d368 in 8 of
  15 cells**, and the long-shock row's citation counts three divergences where
  the audit found the count had been stale at one. Either the caveat is still
  behind the table or the table was not transcribed from what it claims. The
  numbers are governed rows and unchanged; resolving this is a REGULATORY
  question (is this BoG's own calibration, or a transcription error?), not a
  code change, and it needs the primary text that is not in the checkout.

---

## 12. Where the rest of the reasoning lives

The full working ledger — every numbered decision D-001…D-084, the deviation
register DV-001…, the phase status and the four audit reports (architecture,
regulatory, security, independent) — was written under `.ai/icaap/` during the
build. `.ai/` is gitignored and stays that way: alongside the reasoning it
holds a **production hostname**, a security audit that is an inventory of
unremediated weaknesses, ~10 MB of raw test output, checkpoint patches and
tarballs, and superseded drafts. This repository is public, and `.gitignore`
says in so many words that `/docs/` is ignored by default for exactly those
reasons, with a named allow-list for anything publishable. Re-including `.ai/`
— even narrowly — would publish what that allow-list exists to keep out.

This document carries the decisions that a future engineer would otherwise
re-litigate or silently break. Decision IDs are quoted throughout so the
working ledger stays cross-referenceable for anyone who has it. What
deliberately stays behind: per-phase build plans, agent work logs, checkpoint
patches, raw test output, and the audit reports' full finding-by-finding text —
each finding that changed the code is reflected either here, in the code, or in
the test that now pins it.
