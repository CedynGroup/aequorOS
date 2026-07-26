# Attestation & e-signature — as-built review and design

**Status: DESIGN, NOT LEGAL ADVICE.** This document is written to *satisfy* the
requirements of Ghana's Electronic Transactions Act, 2008 (Act 772), the Banks
and Specialised Deposit-Taking Institutions Act, 2016 (Act 930), and the Data
Protection Act, 2012 (Act 843). Nothing here is a representation that it *does*
satisfy them. Every point whose legal effect depends on a determination we are
not qualified to make is listed in **§7 Legal review register** and must be
confirmed by qualified Ghanaian counsel before this capability is used on a
live filing.

Four Bank of Ghana facts this design deliberately does **not** assume are listed
in **§8 Confirmation register** — whether a signed PDF is required at all, which
officers sign which of the thirteen returns, whether the daily return needs
signing, and what ICAAP's board attestation demands. All four are
configuration, not code.

**Built 2026-07-25.** §9 records every place the implementation diverged from
this design and the honest limitations of what was built — read it before
relying on any detail below it.

Scope: **all thirteen registered returns**, not only BSD-2 and BSD-3. They fall
into three attestation classes with materially different binding and ceremony
requirements (§1.7); a design that covers only the two engine-backed monthly
returns would silently produce an unbindable signature on the corporate packs.

---

## 1. Part 1 — The system as built today

Verified against branch `eric` @ `ab5c2a1`; primary database at alembic head
`202607240026`. File:line references are load-bearing — this section is a
review, not a recollection.

### 1.1 How a return is produced — all thirteen

Two different pipelines exist, and the difference is the single most important
fact in this review. **Engine-backed returns** (Class A/C, eight of the
thirteen) run four stages. **Master-data packs** (Class B, the five `LRT-*`
corporate returns) **skip stages 1 and 2 entirely** and start at stage 3. The
full inventory and the class definitions are in §1.7.

**Stage 1 — canonical facts** *(Class A/C only)*. Ingested data is normalised
into `bank_financial_facts`, re-derived from canonical state on every pipeline
refresh (`app/services/fact_derivation.py`). Fact rows get fresh UUIDs on every
re-derivation; this matters enormously (see 1.2).

**Stage 2 — the engine run** *(Class A/C only)*. `POST /banks/{bank_id}/regulatory-runs`
(per module) creates a `RegulatoryRun` (`app/models/regulatory_run.py:28`) — the
immutable unit, and the thing a signature can bind to. The module varies by
return (verified against the generators): `liquidity` for BSD3
(`generation.py:607`), `capital` for BSD2 (`:737`), `irr` for IRRBB-PILOT
(`:769, :855`), `fx` for FX-NOP (`:870, :945`) and for DBK-DAILY
(`dbk_generation.py:48` — the daily return is derived from the FX baseline),
`capital` for LE-MONTHLY with `liquidity` for its LMT tables
(`le_generation.py:414, :850`), and `forecast` plus the stress scenario runs
for ICAAP-STRESS (`:965`, with liquidity and capital headlines at `:1001`).
Several returns therefore bind **more than one** run, and the signature must
cover all of them — which the digest in §3.1 does. The run persists
`inputs` (the full snapshot), `input_hash`, `engine_version`,
`input_schema_version` (`bank-facts-v2`), plus child `RegulatoryLineItem`,
`RegulatoryMetricResult`, and `RegulatoryValidation` rows.

**Stage 3 — the package.** `POST /banks/{bank_id}/regulatory-packages`
→ `generate_package` (`app/services/regulatory_reporting/generation.py:95`),
which dispatches to one of the registered generators (`generation.py:1068`,
plus `le_generation.py`, `dbk_generation.py`, `lrt_generation.py`). All thirteen
produce the same `regulatory-package-v1` snapshot shape — envelope + sections +
totals + metadata — and all store `snapshot_sha256` (`generation.py:197`).
They differ in what feeds them and what they can cite as provenance:

| | Class A / C — engine-backed | Class B — master data (`LRT-*`) |
|---|---|---|
| Precondition | a **succeeded baseline run** for the period, else 409 `no_baseline_run` (`regulatory_liquidity.py:286`) | none — reads the institution-profile register directly |
| Reads | the run's persisted line items + metrics | `institution_profiles`, `related_parties`, `shareholdings`, `outlets`, `bank_products`, `bank_licenses`, `bank_name_history` |
| `source_runs` | `[{module, run_id, input_hash, engine_version}]` (`_source_run_entry`, `generation.py:392`) | **`[]`** (`lrt_generation.py:227, 295, 494, 586, 648`) |
| Reproducible provenance | yes — the value-based `input_hash` | **no** — see G16 |

Section content is per-return: BSD-3 is HQLA / outflows / inflows / LCR summary
/ ASF / RSF / NSFR summary; BSD-2 is CET1 / AT1 / Tier 2 / credit, market and
operational RWA / capital ratios (`generation.py:531` and `:614`); the LE, DBK,
ICAAP and LRT families have their own section sets in their respective
generators.

**Stage 4 — artifacts.** `export_package` renders xlsx (openpyxl), csv/zip, and
**pdf (reportlab)** — `app/services/regulatory_reporting/exports/`. A PDF
already exists and already renders an attestation block; the block is
**empty label text**, not a signature:

```python
# app/services/regulatory_reporting/templates.py:47-52
STANDARD_ATTESTATION_LINES = (
    "Prepared by (name / designation / signature / date): ",
    "Reviewed and approved by (name / designation / signature / date): ",
    "We attest that this return is complete and accurate ...",
)
```

Bytes are written to the `outputs` storage tier at
`bog_returns/{reporting_date}/{package_id}/{return_code}.{ext}`, with the
artifact row recording `checksum_sha256` and `size_bytes`.

### 1.2 The immutable run and its value-based input hash

This is the strongest part of the existing architecture and the foundation the
whole attestation design rests on.

**Construction.** Every module builds a snapshot dict and hashes it identically
(`regulatory_capital.py:1284`, `regulatory_liquidity.py:1095`, and peers):

```python
payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
return hashlib.sha256(payload.encode()).hexdigest()
```

**The value-based invariant.** The `facts` list excludes `fact.id` and is sorted
by each entry's own canonical JSON, so neither row identity nor DB return order
can influence the hash. This is deliberate and executably enforced:
`test_official_input_hash_survives_fact_rederivation`
(`tests/services/test_pipeline.py:181`) refreshes facts — destroying and
re-inserting every row with new UUIDs — and asserts the per-module baseline
hash set is unchanged.

**Immutability in practice.** After a run reaches `succeeded` or `failed`,
nothing writes to it. There is no `run.inputs =` or `run.input_hash =`
assignment outside the constructor anywhere in `app/`, and no PUT/PATCH/DELETE
route exists on any run resource. Reruns mint a new row; identical inputs
reproduce an identical hash (`tests/api/test_regulatory_liquidity.py:152`).

**Caveat.** Immutability is a property of *the code that exists*, not of the
schema — there are no triggers and no revoked privileges (see 1.5). And no test
pins a *literal* golden hash, so a future change to the canonicalisation recipe
would silently invalidate every previously filed hash without failing CI.

Distinct and easily confused: `CalculationRun` (`calculation-input-v1`, the
case/scenario family) is **id-bearing** and its snapshot is legitimately
rewritten once, pending → established (`app/services/calculations.py:319`).
Attestation binds to `RegulatoryRun` only.

### 1.3 The maker-checker workflow as it actually stands

`PACKAGE_STATUSES` (`app/models/regulatory_reporting.py:37`) and
`ALLOWED_TRANSITIONS` (`app/services/regulatory_reporting/workflow.py:80`):

| From | Permitted to |
|---|---|
| `draft` | `generated`, `superseded` |
| `generated` | `validated`, `superseded` |
| `validated` | `pending_approval`, `generated`, `superseded` |
| `pending_approval` | `approved`, `generated`, `superseded` |
| `approved` | `submitted`, `superseded` |
| `submitted` | `acknowledged`, `rejected`, `declined`, `submitted` |
| `acknowledged` | *(terminal — correction requires a granted resubmission)* |
| `rejected` / `declined` | `superseded` |
| `superseded` | *(terminal)* |

Role gating (`app/api/deps.py`; hierarchy `admin ⊃ approver ⊃ analyst ⊃ viewer`,
`app/core/security.py:33`):

| Action | Gate |
|---|---|
| generate, validate, request-approval, export, request-resubmission | `MutationTenant` (analyst+) |
| **decide-approval, submit, poll, decide-resubmission** | `ApproverTenant` (approver+) |
| reads, artifact download | `Tenant` (viewer+) |

**Segregation of duties exists in exactly one place:**

```python
# app/services/regulatory_reporting/workflow.py:245-252
if actor_user_id == package.generated_by:
    raise HTTPException(status_code=409, detail=(
        "Maker-checker: the approval decision must be made by a different user "
        "than the one who generated the package."))
```

That is the whole of it. There is no equivalent check on **submit**, on
**resubmission decisions**, or against `requested_by`. There is no
required-approver-count, quorum, or approval-policy concept anywhere in the
codebase — one approval releases the package. `RegulatoryPackageApproval`
records `actor_user_id`, `action ∈ (requested, approved, rejected)`, `reason`,
`occurred_at` — and nothing else about the person.

### 1.4 Identity: OIDC → platform user

The bank's own IdP is the authority. `verify_oidc_id_token`
(`app/core/security.py:199`) performs discovery, fetches the issuer JWKS, and
validates RS256/ES256 with issuer and audience taken **from the stored
connection row, never from the token**. Trusted claims: `sub`, `email`,
`email_verified`, `name`. Subject → user mapping is
`auth_provider='oidc' AND sso_subject=sub AND is_active`, with a first-login
fallback to exact email match on a pre-provisioned active account
(`app/services/authentication.py:110`). Opt-in JIT records a **deactivated**
stub and returns 403 until an admin approves with an explicit role.

Access tokens are HS256, 15-minute TTL, carrying `sub` (user UUID), `org`
(now the `OR-XXXXXXXX` platform code, post-epoch), `roles`, `email`, `name`
(`app/core/security.py:92`). `validate_tenant_context` re-checks on **every
request** that the user still exists in the org and is active
(`app/api/deps.py:153`), so deactivation kills live sessions immediately.

**Stable per-user identifiers available today:**

| Candidate | Stable? | Verdict for signing |
|---|---|---|
| `users.id` (UUID v4) | Never rewritten anywhere | Stable, but **exposed to the browser** and is an internal DB key — unsuitable as a published signer identifier |
| `users.email` | No API mutates it, but it is a natural key that changes in real life | Unsuitable |
| `users.sso_subject` | **Not guaranteed** — the issuer is not stored beside it, the unique index is global `(auth_provider, sso_subject)`, and Entra-style pairwise subjects change if `client_id` rotates | Unsuitable |
| Platform IDs (`OR-`, `BK-`) | Permanent, opaque | Exist for orgs and banks **only** — there is no per-user equivalent |

There is **no step-up authentication, MFA, ACR, or re-authentication concept in
the system at all** — an exhaustive search returns zero implementation hits in
backend and dashboard alike.

### 1.5 Where evidence is persisted — and how immutable it really is

`AuditEvent` (`app/models/audit_event.py`) records `organization_id`,
`actor_user_id`, `event_type`, `entity_type`, `entity_id` (text since the
epoch), `details`, `created_at`. `record_event` (`app/services/audit.py:12`)
does `db.add` only — the audit row lives or dies **inside the caller's business
transaction**. 126 call sites; the regulatory-reporting family covers
generation, supersession, validation, status changes, approvals, submissions,
exports, downloads and resubmissions.

**Append-only is a convention, not a guarantee.** Verified against the live
primary:

- No `UPDATE`/`DELETE` against `audit_events` in application code, and the seed
  cleanup allowlist deliberately excludes it.
- **Zero triggers exist in the entire database.**
- The application role owns the table with ACL `arwdDxt` — it holds **UPDATE,
  DELETE and TRUNCATE**. The worker role additionally has `BYPASSRLS`.
- The single RLS policy is `FOR ALL` — it scopes writes to the tenant, it does
  not forbid them. No restrictive policy, no `FOR SELECT`-only policy.
- `actor_user_id` is `ON DELETE SET NULL`, so deleting a user silently strips
  attribution from their history. Approval and package tables store the actor
  as a **bare UUID with no FK at all** and no name snapshot — the UI degrades to
  `userId.slice(0, 8)` when the user is gone.

Tenant isolation, by contrast, is genuinely strong: 85 of 89 tables are RLS
`ENABLE` **and** `FORCE`, the GUC is transaction-local and fails closed when
unset, and the app role is not `BYPASSRLS`.

Storage: buckets are versioned; only the `temp` tier has a lifecycle rule;
**no S3 Object Lock / WORM anywhere** — versioning is deletable by any holder
of the bucket credentials.

Time: every timestamp is `utc_now()` — the *application host's* wall clock.
`audit_events.created_at` has no `server_default`. There is **no trusted time
source, no RFC 3161, nothing** (exhaustive grep: zero hits).

### 1.6 Gap register — current flow vs. a defensible attestation flow

| # | Gap | Evidence | Severity |
|---|---|---|---|
| **G1** | Audit store is mutable — app role holds UPDATE/DELETE/TRUNCATE, no triggers, no restrictive RLS | live DB ACL `arwdDxt`; `information_schema.triggers` = 0 rows | **Blocker** |
| **G2** | Artifacts are **upserted in place** — `object_path`, `checksum_sha256`, `size_bytes` overwritten on re-export; the S3 `version_id` is never persisted, so "the artifact as filed" is not resolvable from the database | `exports/__init__.py:143-164`; unique `(org, package, kind)` | **Blocker** |
| **G3** | `snapshot_sha256` is written but **never verified** — the model comment claims exports verify it; no code does | `models/regulatory_reporting.py:167-168` vs. absent verification | **Blocker** |
| **G4** | No trusted time. Signing time would rest on an app host's unmonitored clock | no RFC 3161 anywhere | **Blocker** |
| **G5** | No step-up authentication — a 15-minute bearer token is the only thing between a stolen session and an approval | zero MFA/ACR implementation | **Blocker** |
| **G6** | Maker-checker covers only the approval decision; submit, resubmission-decide, and `requested_by` are unguarded | `workflow.py:245` is the sole check | High |
| **G7** | No permanent, opaque per-user identifier | `public_ids.py` has `BK`/`OR` only | High |
| **G8** | No WORM on `outputs`; versioning only | `provisioning.py` — no Object Lock | High |
| **G9** | **A `consolidated` package renders a PDF that says "Solo"** — `basis` is stamped into the snapshot but templates hard-code the solo string, and no generator branches on basis | `templates.py:40, 98`; `generation.py:116` | High — *signing a document that misstates its own basis is a defect in the instrument* |
| **G10** | Attribution is destroyed by user deletion (`SET NULL`) or degraded to a UUID fragment; no name snapshot at write time | `audit_event.py:26-30`; `hooks.ts:2056` | High |
| **G11** | No approval policy — one approver releases any return; officers/quorum are not modelled | grep: no `required_approvals` concept | High |
| **G12** | `reportlab` is unpinned; deterministic bytes depend on `invariant=1` behaviour | `pyproject.toml:27` | Medium |
| **G13** | `snapshot_sha256` embeds `metadata.generated_at`, so it seals *a version*, not *content identity* — two regenerations of identical data differ | `generation.py:365` | Medium (design must not assume otherwise) |
| **G14** | `_submission_revision` walks the version chain **without filtering `basis`** — a granted solo resubmission bumps the consolidated revision | `workflow.py:860-871` | Medium |
| **G15** | `scripts/create_user.py` is broken post-epoch (parses org id as UUID into a `String(16)` PK) — and it is the only human provisioning path | `create_user.py:64-72` | Medium (blocks signer provisioning) |
| **G16** | **The five corporate/LRT packs bind no engine run** (`source_runs=[]`), so they have no reproducible `input_hash`. A signature over them can bind content but not derivation; the register they read is live mutable master data with no re-derivation path | `lrt_generation.py:6, 227, 295, 494, 586, 648` | **High — a signature with nothing reproducible to bind to** |
| **G17** | `DBK-DAILY` is due 10:00 T+1, five days a week. A daily two-person step-up ceremony is operationally infeasible and would drive missed deadlines or shared credentials | `registry.py:244-258` | High (workflow design, resolved by C3) |
| **G18** | `ICAAP-STRESS` requires board resolutions and senior-management reports (`BOARD_ATTESTATION_LINES`) — more signer slots than preparer+approver, plus mandatory attachments, neither of which the platform models | `templates.py:53, 906` | Medium |

G1–G5 are **prerequisites**: a signature laid over a mutable audit trail, an
overwritable artifact, an unverified content seal and an untrusted clock is not
defensible, however good the cryptography is.

### 1.7 The full return inventory — and why the family matters

Thirteen returns are registered (`app/services/regulatory_reporting/registry.py`).
They do **not** share the properties a signature needs to bind to:

| Return | Family | Frequency | Engine runs bound | Fidelity |
|---|---|---|---|---|
| `BSD3` | liquidity | monthly | liquidity baseline | PARTIAL |
| `LMT` | liquidity | monthly | liquidity baseline | PARTIAL |
| `BSD2` | capital | monthly | capital baseline | REPRESENTATIVE |
| `IRRBB-PILOT` | irrbb | quarterly | `irr` baseline + scenarios | REPRESENTATIVE |
| `FX-NOP` | fx | monthly | `fx` baseline + scenarios | REPRESENTATIVE |
| `DBK-DAILY` | dbk | **daily**, due 10:00 T+1 | `fx` baseline | REPRESENTATIVE |
| `LE-MONTHLY` | large_exposures | monthly | `capital` (+ `liquidity` for LMT tables) | CONFIRMED |
| `ICAAP-STRESS` | icaap_stress | annual | `forecast` + stress scenarios | REPRESENTATIVE |
| `LRT-PROFILE` | corporate | annual, **event-driven** | **none — `source_runs=[]`** | CONFIRMED |
| `LRT-OUTLET` | corporate | annual, **event-driven** | **none** | CONFIRMED |
| `LRT-PARTY` | corporate | annual, **event-driven** | **none** | CONFIRMED |
| `LRT-CAPITAL` | corporate | annual, **event-driven** | **none** | CONFIRMED |
| `LRT-PRODUCT` | corporate | annual, **event-driven** | **none** | CONFIRMED |

**Class A — engine-backed periodic returns** (BSD3, LMT, BSD2, IRRBB-PILOT,
FX-NOP, LE-MONTHLY, ICAAP-STRESS). Each binds one or more immutable
`RegulatoryRun` rows carrying a reproducible value-based `input_hash`. This is
the class the §3 design was originally written for, and for these it holds.

**Class B — master-data packs** (the five `LRT-*` corporate packs). Generated
**exclusively from the institution-profile register** with
`source_runs = []` (`lrt_generation.py:6, 227, 295, 494, 586, 648`). There is
no engine run, therefore **no `input_hash`, therefore nothing reproducible for a
signature to bind to** beyond the package snapshot itself. The register they
read (`institution_profiles`, `related_parties`, `shareholdings`, `outlets`,
`bank_products`, `bank_licenses`, `bank_name_history`) is *live, mutable
master data* with reason-required audited mutations — it has no immutable-run
equivalent and no re-derivation path. This is **G16**, and it is a design
defect, not a scope decision.

**Class C — daily returns** (`DBK-DAILY`). Due at 10:00 the next business day,
five days a week. A two-person step-up-authenticated signing ceremony every
weekday morning is operationally infeasible; imposing one would predictably
produce either missed statutory deadlines or shared credentials — the exact
failure the ceremony exists to prevent. This is **G17**, and the resolution is
a confirmation question (§8, C3), not more engineering.

**ICAAP-STRESS additionally carries board attestation.** Its template uses
`BOARD_ATTESTATION_LINES` (`templates.py:53, 906`), which requires accompanying
board resolutions and senior-management reports — i.e. more signer slots than
preparer + approver, plus mandatory attachments (§4.5, and C4).

---

## 2. Part 2 — The signee identity layer

### 2.1 Format

```
SGN-XXXXXXXXXXXXXXXX          e.g.  SGN-7K4M9PQR2VWX3YZ8
```

`SGN-` prefix + 16 characters of Crockford base32 (80 bits). The alphabet is
the existing platform one — digits and uppercase letters excluding the
ambiguous `I`, `L`, `O`, `U` (`app/services/public_ids.py:20`) — so signer IDs
read aloud, transcribe onto paper, and survive a fax to the regulator. This is
deliberately the same visual family as `BK-` and `OR-`.

### 2.2 Generation

Deterministic derivation from the platform user ID, keyed by a server-side
pepper:

```
material   = HMAC-SHA256(key = SIGNER_ID_PEPPER_v{n}, msg = "aeq:signer:v1:" || user_uuid || collision_counter)
signer_id  = "SGN-" || crockford_base32(material[0:10])     # 80 bits
```

Four properties, each load-bearing:

- **Deterministic** — the same user UUID always derives the same ID, so the
  value is reconstructible in a disaster-recovery scenario from the user table
  plus the pepper.
- **Opaque** — HMAC output reveals nothing about the input without the key. The
  ID encodes no name, no email, no role, no tenant, no issue order. An outsider
  holding a filed PDF learns an identifier and nothing else.
- **Unique across tenants without leaking** — a single global unique index
  guarantees platform-wide uniqueness; because the ID carries no tenant
  component, two banks cannot correlate their staff, and possession of an ID
  from bank A tells you nothing about bank B. (A collision would surface as a
  unique-violation whose mere existence is a negligible one-bit signal at 80
  bits of entropy; it is handled internally by incrementing
  `collision_counter` and re-deriving, never surfaced to a caller.)
- **Permanent** — see below.

### 2.3 Storage and the permanence rule

```
signer_identities                     -- RLS-forced; global unique index on signer_id
  id                 uuid PK
  organization_id    String(16)  FK organizations.id      -- tenant at provisioning
  user_id            uuid        FK users.id  ON DELETE RESTRICT
  signer_id          String(24)  UNIQUE (global)
  derivation_version smallint                             -- pepper generation
  provisioned_at     timestamptz
  created_at         timestamptz
```

**Derivation seeds the value; persistence makes it permanent.** The row is
written once and never updated — there is no code path that rewrites
`signer_id`, and the table is covered by the same append-only enforcement as
the audit store (§3.6). Consequences, all intended:

- Rotating `SIGNER_ID_PEPPER` changes what *new* users would derive; it does
  **not** change any existing signer ID, because derivation runs exactly once.
  `derivation_version` records which pepper produced the row.
- Name, email, job title, role and even tenant-facing platform IDs may all
  change around it. The signer ID does not move.
- `ON DELETE RESTRICT` on `user_id` is the schema-level statement that a user
  who has ever been provisioned to sign cannot be deleted out from under their
  signatures. (This closes G10 at its root; the additional denormalisation in
  §4.3 closes it belt-and-braces.)

### 2.4 Provisioning at first access

A signer identity is minted **when platform access is provisioned** — not
lazily at first signature, so that the ID exists before it is ever needed and
an operator can print a signer roster in advance. Concretely, `ensure_signer_identity(db, ctx, user_id)`
is called from every path that grants a person access:

1. admin approval of an SSO access request (`approve_sso_access_request`,
   `app/services/authentication.py:284`);
2. CLI provisioning (`scripts/create_user.py`, which must be repaired first — G15);
3. a backfill migration for all existing active users.

It is idempotent: derive → insert → on unique violation, re-read and return the
existing row. **Service accounts (`auth_provider='service'`) are excluded** —
machines do not attest. The signing service refuses any principal without a
human `auth_provider` (`password` or `oidc`), which also means an integration
key can never produce a signature.

### 2.5 Display

The same string appears in exactly three places, and they must agree:

- **In-app**, directly beneath the rendered signature block, in monospace with
  a copy control — mirroring how `BK-`/`OR-` are presented in Settings.
- **In the audit and signature records** (`attestation_signatures.signer_id`).
- **Stamped into the signed PDF**, inside the visible signature appearance, so
  it travels with the filed document and is legible to a BoG examiner holding
  only a printout.

The PDF appearance renders exactly four lines:

```
Approved by
Ama Mensah — Chief Financial Officer
Signer ID: SGN-7K4M9PQR2VWX3YZ8
2026-07-31 14:02:11 GMT   (RFC 3161 timestamped)
```

### 2.6 Deprovisioning — what survives

When a user is deprovisioned (`is_active = false`, the only supported
offboarding):

| Artefact | Outcome |
|---|---|
| `signer_identities` row | **Retained forever.** No deactivation flag on the identity — the identity is a historical fact, not an entitlement. |
| Past signatures | **Remain valid and attributable.** The signature record holds a denormalised `signer_id`, the signer's display name *as it stood at signing*, the certificate chain as signed, and the RFC 3161 token. Verification needs none of the live user row. |
| Their signing certificate | Revoked (§3.4). Because every signature embeds a trusted timestamp and its validation material, revocation invalidates *future* use, not past signatures — this is precisely why the TSA is not optional. |
| New signatures | Impossible — the login path, `validate_tenant_context`, and the signing-authorisation gate each independently reject an inactive principal. |
| The signer ID itself | **Never reissued to anyone.** |

A permanently retired identity remains resolvable to a human name through
`signer_identities` → `users`; if that name must later be redacted under Act
843, the opaque ID keeps the signature attributable to *a determinate person*
even after the personal data is minimised (§6.3).

---

## 3. Part 3 — Cryptography (mandated libraries only)

**No custom cryptography.** Signature algorithms, PAdES construction, CMS
encoding, timestamping and certificate-path validation come from pyHanko and
`cryptography`. What we build is orchestration, key custody policy, binding,
and verification reporting.

### 3.1 What gets signed — two artefacts per signature

Every signature covers **the same binding digest**, computed once and reused,
so the PDF signature and the detached attestation are provably about the same
figures. The digest must work for all three return classes of §1.7, which the
first draft of this design did not — it assumed an engine run always exists.

**Two new digests are required** (the existing `snapshot_sha256` is retained
unchanged as the version seal — nothing already stored is invalidated):

```python
# 1. CONTENT digest — a true content fingerprint. Volatile metadata excluded,
#    so identical figures produce an identical digest across regenerations.
#    This is what fixes G13 for signing purposes.
VOLATILE_METADATA_KEYS = {"generated_at"}          # extend, never shrink

content_digest = sha256(canonical_json(
    strip_metadata_keys(package.snapshot, VOLATILE_METADATA_KEYS)
))

# 2. CERTIFICATION digest — what every signer signs.
certification_digest = sha256(canonical_json({
    "schema": "aequoros-attestation-v2",
    "organization_id": ..., "bank_id": ...,
    "package_id": ..., "package_version": ...,
    "return_code": "BSD3", "reporting_date": "2026-06-30",
    "basis": "solo",
    "content_digest": ...,                         # ALWAYS present — every class
    "binding_class": "engine_run" | "master_data",
    # Class A only — the reproducible engine inputs:
    "source_runs": sorted([{"module", "run_id", "input_hash"}], key=canonical_json),
    # Class B only — the master-data provenance analogue (see below):
    "register_state_digest": ... | None,
}))
```

Canonicalisation is the engines' recipe exactly — `sort_keys=True`,
`separators=(",", ":")`, `ensure_ascii=True`, **no `default=`** (all values
pre-stringified; note `generation.py:66` deviates by passing `default=str`, and
the attestation path must not inherit that laxity).

**Why `content_digest` is a sound binding for every class.** The package
snapshot is genuinely immutable in practice — there is no `.snapshot =`
assignment anywhere in `app/`, and regeneration mints a new row rather than
mutating one. So a digest over the snapshot proves *"these are the exact figures
and fields I signed"* for all thirteen returns.

**What Class B still lacks, and how §G16 is closed.** `input_hash` gives Class A
something stronger than immutability: **reproducibility** — an auditor can
re-derive it from canonical facts and confirm the figures were not merely
unchanged but *correct for that data*. Master-data packs cannot have that today
because the register is live and has no re-derivation path. The design therefore
adds the master-data analogue, computed at generation and stored on the package:

```python
register_state_digest = sha256(canonical_json({
    "schema": "aequoros-register-state-v1",
    "rows": sorted([                # only the register rows that fed this pack
        {"table": "shareholdings", "id": ..., "updated_at": ...,
         "values_digest": sha256(canonical_json(row_business_fields))}
        for row in contributing_rows
    ], key=canonical_json),
}))
```

This binds *which register state* produced the pack, so a later mutation of a
shareholding or an outlet is detectable against a signed pack even though the
register itself is mutable. It is weaker than `input_hash` — it proves state,
not derivation — and that difference is a legal question, not a technical one
(**L16**).

**Class C (daily).** Binding is identical to Class A; only the *ceremony* is in
question (G17 / C3). Nothing about the digest changes.

The per-signature payload adds who, in what capacity, and *what they were
shown*:

```python
attestation_payload = {
    "schema": "aequoros-signature-v1",
    "certification_digest": ...,
    "signer_id": "SGN-...",
    "signing_role": "preparer" | "approver",
    "officer_title": "Chief Financial Officer",
    "statement": "<the exact attestation wording rendered on screen>",
    "declared_at": "<ISO-8601, server>",
}
```

The `statement` field is what-you-see-is-what-you-sign: the signer's
cryptographic commitment covers the words they read, not just the numbers.

**Artefact A — the signed PDF** (pyHanko). **Artefact B — a detached
attestation** over `sha256(canonical_json(attestation_payload))`, independent
of any PDF, so the attestation survives even where BoG accepts a non-PDF
submission (§8, C1).

### 3.2 PDF signing — pyHanko

- **Profile: PAdES B-LTA.** B-B/B-T give a signature and a timestamp; B-LT
  embeds the validation material (certificates, OCSP responses, CRLs); B-LTA
  adds a document timestamp over that material so the whole thing remains
  verifiable for the regulatory retention period without re-fetching anything
  from a CA that may no longer exist. `pyhanko.sign.signers.PdfSigner` with a
  `PdfSignatureMetadata(subfilter=SubFilter.PADES, use_pades_lta=True)`, a
  `TimeStamper` (RFC 3161), and a `ValidationContext` supplying the trust
  roots.
- **Two signatures, one document, incremental updates.** The preparer signs
  first, into a pre-created field `Sig_Preparer`, as a **certification
  (DocMDP) signature at permission level 2** — "form filling and signing
  permitted, nothing else." The approver then signs field `Sig_Approver` as a
  standard approval signature, appended as an incremental update that leaves
  the preparer's byte range untouched. Each signature is independently
  verifiable: the preparer's covers revision 1, the approver's covers the whole
  file.
- **Field locking.** `SigFieldSpec(field_name="Sig_Approver", ...)` carries a
  `/Lock` (FieldMDP) that seals all fields after the approver signs, so no
  further form modification is possible without breaking the signature.
- **Visible appearance.** `stamp.TextStampStyle` with an explicit box on the
  attestation page, rendering the four lines of §2.5. The appearance is
  generated server-side from the signature record — never from client input.
- **Determinism.** Bytes must be reproducible, so `reportlab` gets pinned
  (G12) and the unsigned PDF is generated once and archived before signing;
  signing never re-renders the document.

### 3.3 Detached data signing — `cryptography`

`cryptography` handles canonical-payload hashing, certificate parsing and
chain-building, and **all verification**. For the raw signing operation in
production the private key never enters the process (§3.4), so the design
declares a narrow port:

```python
class RawSigner(Protocol):
    def sign_digest(self, digest: bytes, *, key_ref: str) -> bytes: ...
    def certificate(self, *, key_ref: str) -> x509.Certificate: ...
```

with three implementations: **PKCS#11** (production — the same HSM session
pyHanko uses), **cloud KMS** (alternative production backend), and
**soft-key via `cryptography`** — permitted in development and tests only, and
refused at startup when `APP_ENV=prod`. This is stated plainly because the
alternative — pretending `cryptography` talks to an HSM — would be false;
`cryptography` has no PKCS#11 backend.

Algorithm: ECDSA P-256 with SHA-256 (or RSA-2048/PSS where the bank's HSM or
CA requires it) — configurable per signer key, recorded in
`signature_method` on every record so verification never has to guess.

### 3.4 Key custody

**Private keys are never readable by anyone, including us.**

| Phase | Design |
|---|---|
| **Generation** | Generated **inside** the HSM / cloud KMS with `CKA_SENSITIVE=true`, `CKA_EXTRACTABLE=false`. No private key material ever exists in application memory, the database, a config file, or a backup. Only a key reference (PKCS#11 label / KMS key ARN) is stored. |
| **Certificate** | A CSR is produced from the HSM public key and signed by the issuing CA. Subject carries the **signer ID** (as a stable, opaque identifier) alongside the name and the institution; the certificate — not the database — is what a third party parses. Whether the CA may be our internal PKI or must be an accredited provider is **L4**. |
| **Storage** | `signer_keys`: `signer_id`, `key_ref`, `certificate_pem`, `certificate_sha256`, `not_before`, `not_after`, `status ∈ (active, rotated, revoked)`, timestamps. No secret material. |
| **Use — authorisation** | Possession of a session token is **not** sufficient. Signing requires a single-use **signing authorisation** minted only after step-up re-authentication, bound to `(user_id, package_id, certification_digest, signing_role)`, valid ~120 seconds, consumed on use. The backend opens a PKCS#11 session, signs, and closes it. No background job, no queue worker, and no other user's request can ever cause a key to be used. |
| **Step-up** | OIDC re-authentication against the bank's own IdP (`prompt=login`, `max_age=0`), recording `acr`/`amr`/`auth_time` from the fresh token into the signature record. This is new capability (G5) and it is the single most important control for the "sole control" argument — and simultaneously the point where that argument is weakest (**L1**). |
| **Rotation** | New key + new certificate; the prior certificate is retained with `status='rotated'`. Signatures reference the certificate by thumbprint and embed the chain, so rotation never disturbs history. |
| **Revocation** | On deprovisioning or suspected compromise: revoke via CA (CRL/OCSP) and disable the key object. Past signatures survive because PAdES B-LTA embeds a trusted timestamp proving the signature predates revocation, plus the validation material as it stood. |
| **Custody of the HSM credential** | The PKCS#11 PIN lives in the existing encrypted vault pattern (`CREDENTIAL_VAULT_MASTER_KEY`), retrieved per operation and discarded — the same discipline already used for market-data and ORASS credentials. |

### 3.5 Verification — designed with equal rigour to signing

`verify_attestation(package_id) -> AttestationVerificationReport` performs five
independent checks and reports each separately. A green overall verdict
requires all five.

1. **PDF cryptographic validity** — `pyhanko.sign.validation.validate_pdf_signature`
   per signature, with a `ValidationContext` pinned to the configured trust
   roots and revocation material taken from the embedded LTV data (no live
   fetching in production). Asserts intact byte ranges, a valid chain at the
   timestamped signing time, and a trusted RFC 3161 token.
2. **Tamper between signatures** — pyHanko's **diff analysis** compares each
   incremental revision. The modification level introduced between the
   preparer's and the approver's revisions must be at most `FORM_FILLING`, and
   the preparer's coverage must be `ENTIRE_REVISION` with the approver's
   `ENTIRE_FILE`. Anything else — a redrawn table, an edited number, an added
   page — is reported as tampering with the specific offending object. *This is
   the direct answer to "how would we know if the figures changed between the
   two signatures."*
3. **Detached attestation** — recompute `canonical_json(attestation_payload)`,
   verify the stored signature against the stored certificate's public key with
   `cryptography`, build and validate the chain, and confirm the RFC 3161 token
   covers the same digest inside the certificate's validity window.
4. **Content binding (the part cryptography cannot do)** — recompute
   `certification_digest` from the live package row and its source runs, and
   compare it to the digest that was signed. Then recompute
   `snapshot_content_hash(package.snapshot)` and compare to
   `package.snapshot_sha256` (closing G3), and recompute `_snapshot_hash(run.inputs)`
   for each source run and compare to `run.input_hash`. Any mismatch proves the
   stored figures diverged from the signed ones — independently of any PDF.
5. **Artifact binding** — re-hash the stored PDF bytes and compare to the
   artifact checksum recorded at signing, resolving the exact S3 `version_id`
   pinned in the signature record (which requires closing G2).

Surfaces: an in-app **Verification** panel on the package (any viewer), a
downloadable verification report (JSON + human-readable PDF), and an offline
CLI so an examiner or auditor can verify a filed document with the trust roots
and the file alone — no access to AequorOS required. Independent verifiability
is the whole point; a signature only we can check is not evidence.

### 3.6 Prerequisite hardening (G1–G5, G8)

Delivered before the first signature is possible:

- **Audit + signature append-only, enforced by the database**: `REVOKE UPDATE,
  DELETE, TRUNCATE` on `audit_events`, `attestation_signatures`,
  `signer_identities`, `regulatory_package_approvals`,
  `regulatory_submission_events` from the application and worker roles; a
  `BEFORE UPDATE OR DELETE` trigger that raises; restrictive RLS admitting
  `SELECT`/`INSERT` only; `created_at` gets `server_default now()` so the
  database, not an app host, stamps arrival.
- **Per-tenant hash chain** on the signature and audit tables, generalising the
  existing `HashChainedAccessLog` pattern
  (`app/storage/access_log.py:74`) — each row carries `prev_hash` and
  `entry_hash`, with a scheduled `verify_chain` job and periodic anchoring of
  the chain head (the current implementation has no scheduled verification
  despite its docstring, and flushes from exactly one call site).
- **Artifacts become append-only** (G2): a new immutable
  `regulatory_package_artifact_versions` row per export, persisting the S3
  `version_id`; the signature record pins the exact version it signed.
- **Object Lock (WORM)** in compliance mode on the `outputs` and audit buckets
  (G8) — noting this requires bucket re-creation on MinIO and is therefore a
  migration, not a toggle.
- **`snapshot_sha256` verified** at export, at download and at signing (G3).
- **Trusted time** (G4): an RFC 3161 TSA, used for both the PAdES timestamp and
  the detached attestation.

---

## 4. Part 4 — The attestation workflow

### 4.1 States

The existing `PACKAGE_STATUSES` are preserved. Attestation is a **parallel,
additive dimension** on the package, so nothing already built (supersession,
ORASS parity, resubmission, the regulator-outcome states) has to be re-proved:

```
attestation_state ∈ ( 'unsigned', 'preparer_certified', 'fully_certified', 'void' )
```

| # | Transition | Trigger | Guard |
|---|---|---|---|
| T1 | `unsigned → preparer_certified` | Preparer certifies | Package `validated`, validation `passed` with zero errors; signer holds a `preparer` slot in the policy; step-up authorisation valid; **run freeze applied atomically** |
| T2 | `preparer_certified → fully_certified` | Final required approver certifies | `certification_digest` recomputed **equals** the frozen one; signer ≠ every prior signer; signer holds an `approver` slot; step-up valid; package `pending_approval` |
| T3 | `preparer_certified → void` | Preparer withdraws, or approver rejects | Reason required; package returns to `generated`; all signatures marked superseded-with-reason but **never deleted** |
| T4 | `fully_certified → void` | Regulator rejects/declines, or a granted resubmission is consumed | Only before submission or via the existing resubmission path |
| T5 | *(no transition)* | Submission | `submit` requires `fully_certified` **and** every policy slot satisfied |

Package status and attestation state move together in one transaction:
certification T1 also performs `validated → pending_approval`; T2 also performs
`pending_approval → approved`. There is no window in which a package is
approved but uncertified, or certified but unapproved.

### 4.2 Freeze semantics — what is locked, and when

**On preparer certification (T1), atomically:**

- `certified_at`, `certification_digest` and `frozen_source_runs` are written to
  the package.
- **Regeneration is refused** for this `(bank, return_code, reporting_date, basis)`
  chain while a certified, unsubmitted package exists — extending the pattern
  the acknowledged-regeneration gate already establishes
  (`generation.py:133`). Correcting a certified return is an explicit,
  audited **void** (T3), never a silent supersession.
- **Artifact bytes are frozen**: the signed PDF version is pinned by
  `version_id`; further exports of the signed kind are refused.
- The engine runs are already immutable; the freeze binds *which* runs are in
  scope so a later run for the same period cannot be swapped in.

**Between the preparer's and the approver's signature**, tampering is prevented
by four independent mechanisms — belt, braces, and two more:

1. The server recomputes `certification_digest` at T2 and **409s on any
   difference** from the frozen value.
2. The PDF's DocMDP certification signature restricts permitted changes to form
   filling; pyHanko diff analysis reports anything else (§3.5 check 2).
3. Regeneration and re-export are refused while frozen.
4. Both signatures are over the same digest, so a mismatch is provable forever
   after, by anyone, offline.

The approver's UI states this explicitly: *"You are certifying the identical
figures the preparer certified — digest `a4f2…9c1b`, verified."*

### 4.3 The signature record

```
attestation_signatures                      -- append-only, DB-enforced
  id, organization_id, bank_id, package_id, package_version
  signing_role            'preparer' | 'approver' | <policy-defined>
  officer_title           -- from the policy slot, e.g. 'Chief Financial Officer'
  signer_id               -- DENORMALISED permanent signee ID
  signer_user_id          -- for live resolution while the user exists
  signer_display_name     -- name as it stood at signing (redactable, §6.3)
  certification_digest    -- the figures signed
  signed_source_runs      -- [{module, run_id, input_hash}] as signed
  snapshot_sha256         -- content seal as signed
  attestation_payload     -- the exact canonical payload (reproducible)
  statement               -- the wording the signer was shown
  signature_method        -- 'pades_b_lta' | 'ecdsa_p256_sha256' | ...
  signature_value         -- detached signature bytes
  certificate_pem, certificate_sha256
  tsa_token, tsa_time     -- trusted time (authoritative)
  declared_at             -- server clock (record-keeping only)
  auth_evidence           -- {acr, amr, auth_time, ip, user_agent}
  artifact_version_id     -- the exact signed PDF object version
  prev_hash, entry_hash   -- per-tenant chain
  created_at              -- server_default now()
```

Every field the user's brief requires is present and permanent: the signer's
**signee ID**, the **timestamp** (trusted, plus server), the **input hash(es)
signed**, and the **signing method**. Nothing in the record depends on the user
row continuing to exist.

### 4.4 Maker-checker enforcement

Enforced server-side at T2, not merely in the UI:

- **preparer ≠ approver** — compared on `signer_user_id` *and* on `signer_id`
  (the latter catches a user who was somehow re-provisioned).
- **Distinct approvers** where the policy requires more than one.
- The existing `generated_by` check (`workflow.py:245`) is retained and
  **extended to submit and resubmission-decide**, closing G6.
- Role gates: a `preparer` slot needs analyst+; an `approver` slot needs
  approver+; the policy's `officer_titles` are additionally matched against the
  signer's `job_title`, so "the CFO must sign" is enforceable rather than
  aspirational.
- Service accounts are structurally excluded (§2.4).

### 4.5 Required signers — configurable, not hardcoded

```
return_signing_policies
  organization_id, bank_id (NULL = org default)
  return_code            -- any of the 13 registered codes; NULL = family default
  return_family          -- 'liquidity' | 'capital' | 'corporate' | 'dbk' | ...
  basis (NULL = any)
  required_signatures    -- [{role, officer_titles[], min_count}]
  required_attachments   -- ['board_resolution', 'senior_management_report'] (G18)
  require_signed_pdf     -- boolean; C1 unconfirmed → CONFIGURED, default false
  require_signature      -- boolean; lets a family be exempt entirely (C3/C4)
  distinct_signers       -- default true
  effective_from, effective_to
  updated_by, reason     -- reason-required mutation, per house convention
```

Resolution order is most-specific-first: `(bank, return_code, basis)` →
`(bank, return_code)` → `(bank, family)` → `(org, family)` → platform default.
That is what makes "the CFO signs BSD-2 but the Head of Finance signs BSD-3, and
Bank X differs from Bank Y" expressible without code changes.

Seeded defaults **per class**, all deliberately conservative until confirmed:

| Class | Returns | Default policy |
|---|---|---|
| A — engine-backed periodic | BSD3, LMT, BSD2, IRRBB-PILOT, FX-NOP, LE-MONTHLY | 1 `preparer` + 1 `approver`, distinct; `require_signed_pdf=false` |
| A — annual with board attestation | ICAAP-STRESS | 1 `preparer` + 1 `approver` + board slot **disabled pending C4**; `required_attachments` empty pending C4 |
| B — master data | the five `LRT-*` | 1 `preparer` + 1 `approver`; `binding_class='master_data'` so the register-state digest is mandatory |
| C — daily | DBK-DAILY | **`require_signature = false`** pending C3 — the platform will not impose an infeasible daily ceremony on an assumption |

The submission gate reads the policy **in force at the reporting date**, so a
later policy change never retroactively invalidates a filed return.

### 4.6 UI — built entirely by AequorOS

pyHanko and `cryptography` are invoked **server-side only** and are never
user-facing. The React/TypeScript surfaces:

- **Preparer view** — the rendered return, the validation report, the figures
  digest, the attestation statement, and a "Certify and freeze" action behind
  step-up re-authentication.
- **Approver view** — the identical frozen return, an explicit
  "same figures as certified by the preparer" assertion with the digest, the
  preparer's signature block, and "Approve and certify".
- **Signature block** — name, officer title, role, trusted timestamp, and the
  **signee ID** beneath, in monospace with a copy control.
- **Routing / status** — who must still sign, per the policy in force.
- **Verification panel** — the five checks of §3.5, each pass/fail with detail,
  plus report download.
- **Audit view** — the append-only trail for the package, including every
  signature, void, and chain-verification result.

---

## 5. Part 5 — Ghana regulatory mapping

### 5.1 Electronic Transactions Act, 2008 (Act 772)

Act 772 gives legal recognition to electronic signatures and sets out when an
electronic signature is valid and when it is attributable to a person. The
statute's substantive conditions — and where this design meets them — map as
follows. **Exact section numbering and its interpretation are for counsel
(L2).**

| Statutory condition (substance) | Where the design addresses it | Depends on |
|---|---|---|
| **Uniquely linked to the signatory** | Per-signer asymmetric key generated in an HSM, bound to a certificate whose subject carries the permanent signee ID; the ID is never reissued | Correct one-key-per-person provisioning (**L3**) |
| **Capable of identifying the signatory** | Identity established by the bank's own OIDC IdP; signee ID + name + officer title in the certificate, the record, and the visible PDF appearance | Strength of the bank's IdP identity proofing (**L5**) |
| **Created using means under the signatory's sole control** | Non-exportable HSM key; use gated by a single-use authorisation minted only after step-up re-authentication bound to that exact package and digest; no background path can invoke a key | **The central legal question — L1.** A server-side HSM key is *not* physically in the signatory's possession |
| **Linked to the signed data so that any subsequent alteration is detectable** | PAdES byte-range coverage; DocMDP + FieldMDP; pyHanko diff analysis between revisions; and independently, the `certification_digest` binding over `snapshot_sha256` + every run `input_hash` | Nothing external — this one is fully within the design |
| **Reliability appropriate to the purpose** | Documented threat model, HSM custody, RFC 3161 trusted time, LTV, five-way independent verification, DB-enforced append-only evidence | A reliability determination is legal, not technical (**L6**) |
| **Intention to sign / consent to transact electronically** | Explicit certify action, WYSIWYS `statement` field covered by the signature, distinct wording for prepare vs. approve | Whether the bank's internal mandate authorises these officers to attest electronically (**L7**) |
| **Attribution to the person** | Verified OIDC authentication + step-up at signing, permanent signee ID, immutable trail, `auth_evidence` (acr/amr/auth_time/IP) | **L1**, **L5** |
| **Certification / accreditation of the signing infrastructure** | Design supports either an internal PKI or an external/accredited CA — the CA is configuration, not code | **L4 — whether Act 772 requires an accredited certification service provider (NITA regime) for this use** |

### 5.2 Banks and SDI Act, 2016 (Act 930) — confidentiality and secrecy

The design must not force the bank to disclose customer information to satisfy
a signature. It does not:

- **Only digests leave the perimeter.** An RFC 3161 TSA receives a *hash*, never
  content — this is the property that makes external timestamping compatible
  with banking secrecy. An OCSP responder learns a certificate serial, not a
  document.
- **No customer data in signature or audit records.** Signature records hold
  institution, return, period, digests, and signer identity. Return *content*
  stays in tenant-scoped, RLS-forced storage.
- **Tenant isolation is already strong** (85/89 tables RLS `ENABLE` + `FORCE`,
  fail-closed GUC, non-`BYPASSRLS` app role) and the new tables inherit it.
- **Act 930 s.93(3)** — already cited in the platform's attestation wording
  (`templates.py:51`) — is the reason the attestation statement must be shown
  and signed as read: it exposes both the institution *and* responsible key
  management personnel to penalty for an inaccurate or incomplete submission.
  The design's job is to make it unambiguous who attested to what.
- **Open dependency:** if the HSM/KMS or TSA is hosted outside Ghana, whether
  that constitutes a disclosure or a data-residency issue is **L8**.

### 5.3 Data Protection Act, 2012 (Act 843)

Personal data in scope: signer name, officer title, work email, IP address and
authentication evidence.

- **Minimisation** — signature records carry the signee ID, the name as shown,
  and the officer title. No national ID, no personal contact details, no
  biometrics.
- **Lawful basis** — processing is for compliance with a legal obligation
  (regulatory filing) rather than consent, so a signer cannot later withdraw
  consent and unpick a filed return. Confirmation of basis is **L9**.
- **Retention** — tied to the regulatory retention period, not indefinite by
  default; the storage design already treats the audit bucket as 7+ years with
  no lifecycle expiry.
- **Erasure vs. permanent attribution** — the tension is resolved *by design*:
  because the signee ID is opaque and carries no personal data, a valid erasure
  or minimisation request can redact `signer_display_name` while the signature
  remains attributable to a determinate person via `signer_identities`. This is
  the strongest argument for the opaque-ID design and should be put to counsel
  explicitly (**L10**).
- **Cross-border transfer** — an offshore KMS/TSA/CA triggers transfer rules
  (**L8**); **controller registration** with the Data Protection Commission is
  the bank's obligation and possibly ours (**L11**).

---

## 6. Build sequence

| Phase | Content | Gates closed |
|---|---|---|
| **0 — Evidential foundation** *(prerequisite; no signing yet)* | DB-enforced append-only + hash chains; artifact versioning with `version_id`; `snapshot_sha256` verification; **`content_digest` (volatile metadata excluded)**; **`register_state_digest` for master-data packs**; TSA integration; pin reportlab; fix the consolidated-basis defect; fix `create_user.py` | G1, G2, G3, G4, G8, G9, G12, G13, G15, G16 |
| **1 — Signee identity** | `signer_identities`, derivation, provisioning hooks, backfill, UI display | G7, G10 |
| **2 — Step-up authentication** | OIDC re-auth, signing authorisations, `auth_evidence` | G5 |
| **3 — Signing policy + workflow** | `return_signing_policies` with per-class defaults and most-specific-first resolution; attestation states; freeze; extended maker-checker; attachment requirements | G6, G11, G18 |
| **4 — Cryptographic signing** | PKCS#11 custody, pyHanko PAdES B-LTA, detached attestation, key lifecycle | — |
| **5 — Verification** | Five-check verifier, report, in-app panel, offline CLI | — |
| **6 — Counsel review** | §7 register answered; §8 confirmed; configuration set per bank and per return | — |

Phases 0–2 have standalone value even if the attestation capability were never
switched on: they harden the evidence the platform already produces.

---

## 7. Legal review register — for qualified Ghanaian counsel

**This design is not legal advice, and it must not be described as compliant.**
It is designed to meet the stated requirements, pending the determinations
below. Each item states what must be decided and what changes if the answer
differs from our assumption.

| ID | Determination required | Design impact if answered otherwise |
|---|---|---|
| **L1** | **Does a private key held in a server-side HSM under AequorOS's operational control, usable only after the signatory's step-up re-authentication, satisfy Act 772's "sole control" / attribution requirement?** This is the single most consequential question in the design. | If no: keys must move to signer-held tokens/smart cards or a qualified remote-signing service with a certified sole-control regime. The workflow, records and verification survive unchanged; only §3.4 custody is replaced. |
| **L2** | Exact sections of Act 772 governing validity, attribution and admissibility of these signatures, and their authoritative interpretation. | Mapping table §5.1 must be restated against the confirmed provisions. |
| **L3** | Is one key per natural person required, or may an institutional key be used with the signer identified in the signature metadata? | Institutional keying would materially simplify custody but weakens per-signer non-repudiation. |
| **L4** | Does Act 772 (and the NITA certification regime) require certificates from an accredited certification service provider for this use, or does an internal PKI suffice? | Changes the CA, the cost model, and onboarding lead time. Configuration-level, not architectural. |
| **L5** | Is OIDC authentication against the bank's own IdP sufficient identity assurance for a signatory, or is separate identity proofing required at enrolment? | May add an out-of-band enrolment ceremony before a signee ID can sign. |
| **L6** | Is the overall method "reliable as appropriate to the purpose" for prudential returns? | Reliability is a legal conclusion; may require a formal assessment or independent assurance. |
| **L7** | Does the bank's internal mandate/board authority permit these officers to attest returns electronically, and must a board resolution or delegated authority be recorded? | May add a mandate register and a check that the signer holds current authority at signing time. |
| **L8** | Do an offshore HSM/KMS, TSA or CA raise Act 930 secrecy or Act 843 cross-border transfer issues, given that only digests and certificate serials leave the perimeter? | May force in-country hosting of the HSM and/or TSA. |
| **L9** | Correct lawful basis under Act 843 for signature and audit personal data (legal obligation assumed). | Affects retention, erasure handling, and consent mechanics. |
| **L10** | Does redacting `signer_display_name` while retaining the opaque signee ID satisfy an Act 843 erasure/minimisation request **without** impairing the evidential value of a filed signature? | If not, retention policy and the personal-data footprint of signature records must change. |
| **L11** | Data Protection Commission controller/processor registration obligations for AequorOS and for the bank in respect of these records. | Registration and possibly a data-processing agreement before live use. |
| **L12** | Retention period for signed returns and attestation records, and whether the signature must remain *verifiable* (not merely stored) for that whole period. | Drives the PAdES profile choice (B-LTA already assumes long-term verifiability) and re-timestamping policy. |
| **L13** | Evidential weight and admissibility of the verification report in a Ghanaian proceeding — is an expert or a certified process required to prove a signature? | May require an independently reproducible verification tool, which §3.5's offline CLI anticipates. |
| **L14** | Whether an attestation record without a signed PDF (if BoG accepts authenticated submission — see C1) carries the same legal weight as a signed document. | Determines whether `require_signed_pdf` may ever be set false in production. |
| **L15** | Whether a **void** of a certified-but-unsubmitted return, and the retention of its superseded signatures, creates any exposure (e.g. an appearance of an unretracted attestation). | May require explicit void wording on retained signature records. |
| **L16** | **Does an attestation over master-data packs (the five `LRT-*` corporate returns) carry the same weight as one over engine-backed returns?** Class A signatures bind a *reproducible* `input_hash`; Class B can bind only *content* plus a register-state digest — state, not derivation (§1.7, §3.1, G16). | If equivalence is required, the corporate register needs an immutable snapshot-run concept of its own before those packs may be signed — a material build, not a configuration change. |

## 8. Confirmation register — Bank of Ghana facts (must be verified, never assumed)

| ID | Question | Where it is answered in the design |
|---|---|---|
| **C1** | **Does BoG's submission channel require a formally signed PDF artifact, or does it accept authenticated submission plus an internal certification record?** Must be confirmed against the BoG/ORASS submission documentation the bank and AequorOS hold. | `return_signing_policies.require_signed_pdf` — a per-bank, per-return flag. Default `false` until confirmed; the detached attestation (§3.1 artefact B) exists precisely so the internal certification record stands alone if that is what BoG accepts. |
| **C2** | **Exactly which officers must sign which return — across all thirteen, not only BSD-2 and BSD-3.** The signer set may differ per return, per family, and per bank. | `return_signing_policies.required_signatures`, resolved most-specific-first per `(bank, return_code, basis)`, with `officer_titles` enforced at signing. Nothing hardcoded. |
| **C3** | **Does BoG require a signature on the DAILY return (`DBK-DAILY`, due 10:00 T+1)?** If it does, what ceremony is acceptable — per-return two-person signing, a single signer, or a standing periodic certification with daily returns filed under that authority? | `require_signature` defaults to **false** for the daily family. We will not build a standing-authority mechanism on an assumption; if C3 requires signing, the ceremony model is chosen from the confirmed answer (G17). |
| **C4** | **For `ICAAP-STRESS`, which board/senior-management signatures and accompanying documents are required?** The template already asserts board resolutions and senior-management reports must accompany it. | `required_signatures` (additional board slots) + `required_attachments`, both empty until confirmed (G18). |

Until C1–C4 are confirmed, this capability should run against the sandbox
channel only, and only for the return families whose policy has been explicitly
configured. Configuring them is a settings change, not a release.

---

## 9. As-built deltas from this design

Built 2026-07-25. Where implementation taught us something the design got
wrong, the code won and this section records why. Read it before trusting an
earlier section's detail.

| # | Design said | As built | Why |
|---|---|---|---|
| D1 | Append-only on every evidence table, UPDATE **and** DELETE blocked | **Tiered.** `audit_events`: UPDATE + DELETE + TRUNCATE blocked. `attestation_signatures`, `signer_identities`, `regulatory_artifact_versions`: UPDATE + TRUNCATE blocked; DELETE reachable only by deleting the owning package (CASCADE) | A blanket DELETE ban broke the demo-seed fixture, which legitimately purges package children. Alteration and forgery remain impossible — the properties that matter — and a removed row is *detectable* via the per-tenant hash chain. `regulatory_package_approvals` / `regulatory_submission_events` are deliberately not trigger-guarded for the same reason |
| D2 | `regulatory_artifact_versions.signed` flag marks signed bytes | **No flag.** Signedness is derived: a version row is signed iff an `AttestationSignature` references it | The table is append-only, so the flag could never be written on Postgres. A control that cannot fire is worse than no control |
| D3 | Submission blocked until fully certified, by default | **Default is signature-OPTIONAL.** The gate activates only when an administrator configures a `return_signing_policy` | Every BoG requirement (C1–C4) is unconfirmed. Blocking a *statutory* filing on a guessed requirement risks a real Act 930 s.93(3) penalty; requiring an explicit, audited opt-in does not. The pre-existing `generated_by ≠ approver` control still applies to every return |
| D4 | Signer-identity backfill as an alembic migration | **A script** (`scripts/provision_signer_identities.py`) | Derivation needs `SIGNER_ID_PEPPER`, which is deployment config. A migration requiring it would fail `alembic upgrade head` in CI and anywhere signing is not yet configured |
| D5 | A void sets `attestation_state = 'void'` | A void increments `attestation_cycle`, resets state to `unsigned`, and returns the package to `generated`; `voided_at`/`void_reason` persist as the historical marker | Keeps signatures strictly append-only (nothing is mutated) while allowing re-certification. Signatures from a voided cycle stay readable forever, satisfying L15 |
| D6 | `certification_digest` binds `snapshot_sha256` | Binds a **new `content_digest`** (volatile metadata excluded); `snapshot_sha256` is recorded alongside but is not the binding | `snapshot_sha256` embeds `metadata.generated_at`, so it seals a *version*, not content (G13). A signature bound to it could never be shown to cover the same figures across a re-render |
| D7 | §3.2 "an explicit box on the attestation page", enforced by a guard that the page's content stream contains the `(Attestation)` heading | **Placement is data.** `return_signature_placements` (a reusable template per return code, optionally per bank) plus `package_signature_placements` (a per-package override), resolved override → bank template → org template → the retained hardcoded boxes. The page guard is gone; a placement must instead name a real page, fit inside that page's MediaBox, and be large enough to print ITS OWN KIND of content at ≥6 pt (see D10) | The guard was the only defence available while the boxes were invisible module constants. With a workspace where an operator positions each field on a rendered page and the position is stored, audited and shown back, *where* a signature goes is the institution's call — including onto a figures page, if that is where their return format puts the block. What is not their call is a field that cannot be drawn, so the checks that replaced it are the ones an operator cannot make for themselves. Boxes are refused, never clamped: a trimmed box silently moves a signature somewhere nobody placed it |
| D8 | §2.5 "the appearance is generated server-side from the signature record — never from client input"; §4.6 lists no adopt step | **Officers adopt a mark** (`signature_appearances`): drawn, or typed in one of four PDF standard-14 faces. Drawn bytes are re-rastered to 600×200, stripped of every ancillary chunk and re-encoded as PNG before storage; the raw upload is never persisted. The mark occupies a band above the four evidential lines in a box roomy enough for both, and stands alone in one that is not (D10); the lines are still built from the signature record alone | §2.5's claim was true only while the appearance *was* the four lines. A drawn signature is client input by definition, so the guarantee is **narrowed rather than dropped**: normalised raster only — no user-supplied PDF, vector, font or text template ever reaches the page. The evidential lines are not tradeable for the mark, which is why a box too small for both is refused. No true script typeface is offered: embedding one is a font-licensing decision, and a signature block set in a face we have no right to distribute would be a defect in the filed document |
| D10 | §3.2 gives each signing role ONE box, whose minimum size (185×61 pt) is derived from fitting the four evidential lines at the legibility floor | **Typed fields.** A role has MANY placements, each carrying a `field_type`: exactly one `signature` — the real PDF signature field, whose appearance is the adopted mark alone — plus any number of `name`, `title`, `initials` and `date_signed` boxes, created as AcroForm TEXT fields and filled server-side from the signature record. Each kind has its own derived floor (`pdf_signing.MIN_BOX_SIZES`); 185×61 survives as the threshold at which the four lines are drawn as a caption beneath the mark. `Sig_Preparer` gains a FieldMDP `/Exclude` lock naming the approver's fields | A BoG attestation block asks each officer for four things — BSD3 reads "Prepared by (name / designation / signature / date)" — so one box per role could not complete the form, and a floor sized for four lines of text was far larger than the ruled line the form prints: the signature field could not be put where the regulator asked for it. Text fields rather than drawn content because the approver's values are unknown until the approver signs, and by then the certification permits only form FILLING; each role's values are filled in the same incremental update as that role's signature, which both keeps them inside the covered revision and avoids pyHanko classifying an older appearance stream as an in-place update. The preparer's lock closes what would otherwise be a real hole: filling a form field is precisely what the DocMDP level allows, so without it an appended revision could rewrite the name printed under their signature and still report `docmdp_ok` |
| D9 | Policy names ROLES; nothing names people | **`package_signature_recipients`** — cycle-scoped named recipients, plus a `certify-and-send` action that certifies and nominates in ONE transaction (`signing.certify(commit=False)`). Nomination is validated by calling the same `ensure_maker_checker`, so a preparer cannot nominate themselves and an officer-title rule bites at nomination rather than only at signing. Once a role is routed, only a named recipient may fill it — re-assignable by an approver with a recorded reason | Routing *satisfies* the policy, it does not replace it: every guard in §4 still runs when the nominee signs. Two consequences are deliberate. Refusing a nominee must take the certification down with it, or a certified return would sit in nobody's queue — the exact failure the feature exists to remove. And a nomination the audit trail cannot rely on would be decoration, hence the named-signer gate; the re-assignment path exists so an unavailable approver is not a dead end escapable only by voiding a good signature |
| D11 | §2.5 fixes four evidential lines; §3.2 gives each role one box whose 185×61 floor is derived from fitting them | **DocuSign's stamp anatomy at every accepted size.** The role label straddles the frame's top rule, the adopted mark takes the middle band, and the permanent signer ID is printed beneath it — always. The floor is re-derived from fitting all three (`pdf_signing._signature_minimum`, 78×32 pt) and a smaller box is refused with those elements named. A box past `DETAIL_MIN_WIDTH`/`DETAIL_MIN_HEIGHT` adds the name/designation and timestamp rows underneath, so a layout saved when the boxes were 240×80 keeps the fuller block | D10 demoted 185×61 from a refusal to a threshold and let a smaller box print the mark alone, on the reasoning that the identity travelled in the placed text fields and in the signature dictionary's `/Name`. Both are true; neither is enough. The ordinary case — a field dropped on the form's ruled line — filed a bare squiggle: nothing printed on the page said it was a digital signature or whose it was, and an examiner holding the paper has neither the form fields' provenance nor a PDF parser. The identifier is the one thing the stamp exists to carry, so it is now what the floor is derived from |
| D12 | D8: "No true script typeface is offered: embedding one is a font-licensing decision" | **Four script faces are bundled and embedded** (`app/services/attestation/fonts/`, SIL Open Font License 1.1, licence text committed beside each `.ttf`): Caveat, Dancing Script, Great Vibes, Allura. They reach the page as pyHanko-subsetted embedded fonts, and the dashboard previews them from the same files via `next/font/local` — one copy, both consumers. The base-14 four are kept, offered as "typeset", so a deployment stripped of `fonts/` can still stamp; a face whose file is missing is refused at adoption rather than substituted | The licensing decision was made, not avoided: the OFL permits redistribution and embedding, which is exactly what a filed PDF does. The old answer produced marks that were slanted body text — nobody reads "Times Italic" as a signature, which is the mark's whole job. Every bundled face must be on a 1000-unit em grid: pyHanko writes raw font-unit advances into the CIDFont `/W` array, which PDF reads as thousandths of an em, so a 2048-unit face (Sacramento, Parisienne) stamps with its letters flung apart. Asserted in `tests/services/test_attestation_typed_fonts.py` |
| D13 | §3.2 has no view on the return template's own attestation block | **The block rules the four cells it asks for.** `exports/pdf.py._signing_block` draws a labelled Name / Designation / Signature / Date row per officer, and `DEFAULT_PLACEMENTS` is pinned to those cells — eight boxes, not two — with the pairing asserted against the rendered page. The derived text fields declare an empty `/MK /BG` and `/BC`, a zero-width `/BS`, and `ReadOnly` | The template printed one undivided 150 mm rule under wording asking for four things, so the default placement had nothing to land on and sat in the empty band below the block; the founder had to drag every field onto a return we designed. The widget flags are the other half: readers tint form fields, and a filed return showing grey boxes behind an officer's name reads as a half-finished web form. Changing the template affects FUTURE exports only — signing never re-renders (G12) — which `test_an_artifact_signed_with_the_pre_stamp_layout_still_passes_every_check` holds to |
| D14 | §3.5 checks 1–2 validate with pyHanko's default revision-diff policy | **One allowance is switched off** (`verify.attestation_diff_policy`): `allow_in_place_appearance_stream_changes`. `pdf_signing` never rewrites an appearance stream in place — every filled value gets a fresh stream object — so the allowance can only produce a false positive | And it did. pyHanko skips a field only when its appearance stream's last change *equals* the revision being diffed from, rather than is *not later than* it, so every field the preparer filled reads as an in-place update once any revision exists after the approver's signature. PAdES B-LTA always appends one (the DSS, then the document timestamp), so a fully signed return with placed name/designation/date fields reported `docmdp_ok = False` against a document nobody had touched. Switching the allowance off is strictly stricter: a real in-place rewrite is now refused rather than whitelisted |

### Honest limitations of the built system

These are not design choices; they are things that are **not yet true** and
must not be represented as working:

1. **SSO step-up is wired, but has never run against a real IdP.** Both proofs
   now work end to end in code: password re-entry, and an SSO redirect. The SSO
   leg is three Next.js server routes
   (`app/api/attestation/step-up/{start,callback}`, `app/api/attestation/certify`)
   that mint a `prompt=login&max_age=0` authorize URL with PKCE, state and nonce;
   exchange the code server-side; check the nonce; forward the `id_token` to the
   risk service, which verifies it against the issuer's JWKS; and put the
   resulting authorisation in an **HttpOnly** cookie that only a server route can
   spend. Neither the id_token nor the authorisation ever reaches the browser —
   which is precisely the decision the earlier note said was outstanding.

   What remains unproven is the outbound leg. The hermetic stack has no SSO
   connection, so the Playwright journey exercises the **return** leg
   (a signer comes back to the same ceremony with an honest explanation, nothing
   signed, markers scrubbed from the URL) and the route guards (open-redirect
   refusal, missing-package refusal, certify refused with no held authorisation,
   forged callback refused). A real Google/Entra round trip — including whether
   the IdP honours `max_age=0` and emits `auth_time` — has **not** been observed.
   Bank IT must also register a second redirect URI
   (`/api/attestation/step-up/callback`); until they do, sign-in works and
   signing fails at re-authentication. Both URIs are now shown in
   Settings → Authentication and in `docs/sso-onboarding.md`.

   One residual weakness, unchanged: `verify_step_up` checks `auth_time`
   freshness **only when the claim is present**. An IdP that omits it yields no
   freshness proof from the token itself. Through the UI the code-exchange
   happens seconds earlier so the token cannot be stale, but an API caller
   presenting an id_token directly has no such guarantee. Requiring `auth_time`
   would be stricter and would break IdPs that omit it — a deliberate open
   question, not an oversight.
2. **PAdES B-LTA long-term validity is not proven.** B-LTA is produced and both
   timestamps validate, but in tests the TSA key is minted in-process and no
   revocation material is fetched. Third-party trusted time and real LTV remain
   unproven until a live RFC 3161 authority is configured. This matters
   legally: without a trusted timestamp, certificate revocation would
   retroactively poison past signatures (§3.4, L12).
3. **The PKCS#11 signing path is not exercised.** `python-pkcs11` is an opt-in
   extra and no SoftHSM is present, so only the missing-extra error path is
   tested. The software backend refuses to initialise when `APP_ENV` is
   production, so production signing requires HSM wiring that has not run.
4. **Verification trust anchoring is optional.** With `ATTESTATION_TRUST_ROOTS`
   unset the verifier anchors on the chain the signature carries and reports
   `trust_anchor: "embedded_chain"` — internal consistency, **not** proof of
   issuance by an authority the institution recognises.
5. **Certificate path validation in the detached check is issuance-only** —
   chain building, issuer signatures, validity windows. No name constraints, no
   policy tree, no CRL/OCSP. Revocation-aware validation happens only in the
   PDF check, from LTV material embedded in the file.
6. **The signing workspace has a backend and no UI yet.** Placement, adopted
   marks and named routing are complete server-side, with contract tests over
   every endpoint, but nothing drives them from a browser: there is no PDF
   viewer, no drag-to-place, no signature pad, no recipient picker. Until those
   exist, the only way to place a field or adopt a mark is an API call, so the
   founder's flow — open the return, place two fields, draw a signature, pick the
   approver, send — is **not yet observable end to end**.

   Two residual weaknesses in what *is* built. First, `SHRINK_TO_FIT` bounds the
   evidential block by the box, and the minimum box is derived from the two
   FIXED-format lines (signer ID, timestamp); an exceptionally long
   name-plus-designation line can still shrink the whole block below 6 pt in a
   box at the floor. Second, the typed faces are base-14, so a typed mark is a
   slanted serif rather than handwriting — honest, but not what a signer picking
   "script" expects.
7. **The browser journey covers the ceremony surfaces, not the whole
   lifecycle.** The hermetic stack now enrols disposable self-signed *software*
   signing keys (`scripts/e2e_bootstrap.py`), so seven Playwright journeys run
   for real: the signee ID visible in Settings, unsigned state visible on a
   generated return, the certify dialog showing the figures digest plus the
   full Act 930 s.93(3) declaration before signing is possible, the SSO
   step-up return leg, and four route guards. A full
   preparer-then-approver-then-verify pass through the UI is **not** yet
   driven; the submission gate and the two-signature flow are asserted in the
   backend suite instead. Noted because an earlier version of that journey
   asserted `Submit` was disabled and passed only because no Submit button is
   rendered at `generated` status — a conditional assertion that proved
   nothing. It was removed rather than left green.

---

## 10. What this design does not do

- It does not implement any cryptographic primitive. pyHanko and `cryptography`
  do the signing, timestamping, encoding and path validation.
- It does not build ahead of confirmed BoG requirements; every unconfirmed
  requirement is a configuration row.
- It does not alter the immutable-run or maker-checker architecture — it binds
  to the first and extends the second.
- It does not claim legal compliance. See §7.
