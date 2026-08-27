# AequorOS — RBAC, User Management & Role-Aware Dashboards

**Status:** implementation spec · **Audience:** dashboard + platform engineers · **Owner:** Eric

> **As-built foundation (updated 2026-08-27):** The additive authorization slices
> are recorded in the backend
> [`authorization_foundation.md`](../backend/docs/authorization_foundation.md).
> It corrects the proposed independent `user_roles`/`user_scopes` shape to
> indivisible scoped bindings, adds exact resource evaluation and authorization
> version invalidation, and remains shadow-only except for stale-session denial.
> The institution-target slice (2026-08-27) makes resource target scope explicit
> (organization or one exact institution; `NULL` never broadens) and records
> shadow decisions on Liquidity Monitoring without changing route enforcement.
> This proposal's later UI, role-administration, lifecycle, and broad endpoint
> rollout sections are not claims that those features are built.

This document specifies how AequorOS grants access to bank users, what each user
type can see and do, how the three settings surfaces (personal / org-admin /
vendor-platform) are structured, and how banks invite and onboard their people.
It is written to be **built incrementally on the auth layer that already exists**
(JWT sessions, `organization_id` RLS, the legacy
`admin > approver > analyst > examiner > viewer` hierarchy, and the
regulatory-reporting maker-checker trail).

Everything here is grounded in how real treasury/ALM systems (Kyriba, ION, FIS,
Murex, Adenza/ControllerView, OneSumX, the Regnology-powered **Bank of Ghana
ORASS**) and mature B2B SaaS (Okta, Snowflake, Stripe, Datadog, Google
Workspace, GitHub) do it. Sources are listed in [§16](#16-sources).

---

## 1. How to use this document

Read [§2](#2-current-state--target) first (what exists vs what to build), then jump to what you're
building:

- Building the **role-aware dashboards** → [§5 personas](#5-personas--roles--what-they-need), [§9 per-persona dashboards](#9-per-persona-dashboards-what-to-build), [§8 enforcement](#8-enforcement-architecture).
- Building **settings** → [§10 three-tier settings](#10-settings-architecture-three-tiers), [§12 UI specs](#12-user-menu--ui-specs).
- Building **invite / onboarding** → [§11 lifecycle & onboarding](#11-user-lifecycle--onboarding).
- Need the **data model / API** → [§13](#13-data-model), [§14](#14-target-api-surface).
- Sequencing the work → [§15 roadmap](#15-phased-roadmap).

Terminology: **security tenant = organization (`OR-*`)**; an organization owns
one or more **institutions/banks (`BK-*`)**. **Maker** = the person who
creates/edits/runs. **Checker** = the independent person who reviews/approves.
Module shorthand: **LIQ** (Liquidity), **CAP** (Basel Capital), **IRRBB**, **FX**,
**FTP**, **FCST** (Forecasting), **BEH** (Behavioral), **DATA** (Data Engine),
**REG** (Regulatory Reporting), **RISK**, **MARKETS**, **ACCOUNT**, and **AUDIT**.

---

## 2. Current state → target

### What already exists (build on this — do not rebuild)

| Area                                 | Current state                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Where                                                                                                                                                                                                   |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Auth                                 | Zero-trust JWT (HS256), Argon2id passwords, **own-OIDC SSO** (no third-party broker; built 2026-07-20), refresh rotation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | `app/core/security.py`, `dashboard/auth.ts`                                                                                                                                                             |
| SSO self-service (single connection) | **BUILT.** Per-org `sso_connections` row: issuer + client id + AES-256-GCM-sealed secret (write-only), allowed email domains, enable toggle — managed by an `admin` in **Settings → Authentication**. Backend verifies id_tokens against the connection issuer's JWKS (discovery-based, RS256/ES256, `email_verified` + domain enforcement); the uniquely selected verified connection is the sole organization authority for subject/email lookup, JIT records, and issued tokens. Ambiguous issuer/audience routing fails closed. Dashboard NextAuth loads the client config through an `SSO_INTERNAL_KEY`-gated internal endpoint. Pre-provisioned users by default; **request-access JIT is BUILT as a per-connection opt-in** (`jit_enabled`): first sign-in from an allowed domain records a **deactivated** stub and returns 403 "awaiting administrator approval" — zero access until an admin approves it with an explicit role via `/auth/sso/access-requests` (list/approve/reject; Settings → Authentication card). Refused at config AND login time without a non-empty domain list. One connection per org / one enabled per deployment — Phase 2 lifts this. | `app/models/sso_connection.py`, `app/services/sso_config.py`, `app/services/authentication.py`, `app/api/v1/auth.py`, `dashboard/components/settings/AuthenticationPanel.tsx`, `docs/sso-onboarding.md` |
| Connection health (bank-IT surface)  | **BUILT** (read-only): Data Engine → Overview aggregates every configured source connection (DB-direct, T24, market-data) with live status, last sync, credential expiry, and plain-language remediation hints                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | `dashboard/components/data-engine/ConnectionHealthPanel.tsx`                                                                                                                                            |
| Legacy roles                         | `admin > approver > analyst > examiner > viewer` (linear rank), one scalar role per user; still the endpoint authority during shadow rollout                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | `app/core/security.py:ROLES`, `app/models/user.py:USER_ROLES`                                                                                                                                           |
| Endpoint enforcement                 | Coarse legacy dependencies enforce viewer/analyst mutation separation and selected approver/admin gates. The new binding evaluator is not an endpoint gate yet.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | `app/api/deps.py`                                                                                                                                                                                       |
| Tenancy                              | Postgres RLS forced on `app.organization_id`; cross-tenant work runs on the BYPASSRLS `WORKER_DATABASE_URL` role                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | `app/db/session.py`, CLAUDE.md                                                                                                                                                                          |
| Maker-checker                        | **Regulatory reporting already has it**: `draft→generated→validated→pending_approval→approved→submitted→acknowledged→…` with an append-only approval trail where **checker ≠ maker is enforced in the service**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | `app/models/regulatory_reporting.py` (`PACKAGE_STATUSES`, `APPROVAL_ACTIONS`, `RegulatoryPackageApproval`)                                                                                              |
| Authorization foundation             | **BUILT, SHADOW-ONLY.** Deny-by-default evaluation over indivisible `authorization_bindings`; explicit organization-or-exact-institution resource targets; exact module, sensitivity, lifecycle, principal-type, and runtime-condition matching. Liquidity Monitoring emits legacy-versus-binding shadow decisions, but no binding CRUD API or binding endpoint gate exists.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | `app/core/authorization.py`, `app/services/authorization.py`, `app/features/read_liquidity_monitoring.py`, `backend/docs/authorization_foundation.md`                                                    |
| Token claims                         | App access and refresh tokens carry `sub`, `org`, legacy `roles[]`, and authoritative `authv`, plus `email`/`name` when present; refresh tokens also require `jti`. Pre-`202608250044` and stale-version sessions fail closed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | `app/core/security.py:create_token`, `app/api/deps.py:validate_tenant_context`                                                                                                                          |
| Identity in UI                       | Header + settings read the real session (name/role); route gate redirects unauthenticated → `/login`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | `dashboard/components/shell/Header.tsx`, `dashboard/middleware.ts`                                                                                                                                      |

### The gap this spec closes

1. Selected `approver` and `admin` gates exist, but remain coarse legacy-rank
   checks rather than exact module/resource binding decisions.
2. **No tenant grant administration**: no invitations, general user CRUD, role
   assignment UI, or org-admin grant console. The separate staff operator
   console is built but is not a tenant authorization surface.
3. **`Organization` is bare** (`{id, name}`) — no domain, plan, SSO config, or settings.
4. Endpoint enforcement still uses **one flat legacy role per user**. Exact
   institution/module/sensitivity bindings now exist and are evaluable, but are
   shadow-only and have no administration surface; desk/currency scope is not
   represented yet.
5. **No SoD engine** beyond the one hand-rolled REG check; no generalized maker-checker on calculation/official runs.
6. Audit events and read-only operator impersonation exist, but there is no
   generalized authorization-decision audit envelope, SCIM, or seat/plan
   concept. (SSO self-service exists in single-connection form — see §2 above;
   Phase 2 grows it to multi-connection + home-realm discovery, it is NOT
   rebuilt.)

### Target model in one sentence

> **RBAC** (a small set of predefined, job-shaped roles) as the backbone, **plus a
> thin attribute/scope layer** (which modules, which legal entities, which desks,
> live-vs-demo) for context — with **maker-checker segregation of duties enforced
> at action time**, a **per-tenant admin console**, a **separate vendor platform
> console**, and **invite + SSO + SCIM** onboarding.

This RBAC-with-attributes blend is the consensus of both NIST SP 800-162 ("RBAC
covers ~90% of enterprise needs") and how Snowflake/AWS/Okta/Entra ship. It
avoids the **role-explosion** failure mode (encoding every context combination as
a new role → thousands of roles) by pushing context into scopes, not role names.

---

## 3. Core model

### 3.1 Two governing frames (the "why" behind the roles)

Treasury access design is not arbitrary — it falls out of two long-standing
control frameworks a bank auditor will expect to see reflected:

- **Front / Middle / Back office separation** (Association of Corporate
  Treasurers). Front office _executes_ deals; middle office _monitors limits and
  exposures_; back office _confirms, settles, reconciles_. "The front office does
  the deal but doesn't settle the money; the back office settles the money but
  doesn't do the deal."
- **Three Lines model** (IIA; codified for banking by BCBS). **1st line** =
  business owns/runs risk (can _initiate/configure_); **2nd line** = Risk &
  Compliance set limits and _review/challenge/approve_; **3rd line** = Internal
  Audit gets _independent read + audit trail_, changes nothing.

These collapse into **the one rule everything else serves**:

> The identity that **creates or runs** something must not be the identity that
> **approves or submits** it; and the 2nd/3rd lines must be able to **see
> everything without being able to change operational data**.

### 3.2 The three layers of an authorization decision

Every access check answers: **who (role) → may do what (permission) → on what
(scope) → under which conditions (attributes)**.

1. **Role** — a job-shaped bundle of permissions. Predefined; small set; additive.
2. **Permission** — `resource:action` (e.g. `liq:run`, `reg:submit`, `users:manage`). See [§7](#7-permission-model).
3. **Scope** — the boundary a permission applies within: tenant → legal entity → module → desk/portfolio/currency → data-sensitivity.
4. **Condition (attribute)** — runtime context: environment (**live vs demo**),
   **as-of date**, **maker≠checker** on this object, **approval limit** not
   exceeded, step-up-MFA present. These are _conditions_, not roles — encoding
   them as roles is exactly the explosion trap.

> Two AequorOS rules from `CLAUDE.md` are **conditions, not roles**: "financial
> mutations require a non-empty reason" → an audit + policy condition; "mutations
> disabled while demo mode is active" → an environment condition. Do **not** make
> demo-specific roles.

---

## 4. Tenancy & the two planes

```
                       ┌────────────────────────────────────────────┐
                       │   PLATFORM PLANE  (AequorOS / vendor)       │
                       │   console.aequoros.com — CROSS-TENANT       │
                       │   runs OUTSIDE RLS (BYPASSRLS, like the     │
                       │   background worker's WORKER_DATABASE_URL)  │
                       └───────────────┬────────────────────────────┘
                                       │ provisions / supports / audits
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                               ▼                              ▼
 ┌────────────────┐            ┌────────────────┐            ┌────────────────┐
 │ TENANT: Bank A │            │ TENANT: Bank B │            │ TENANT: Bank C │
 │ org_id = A     │            │ org_id = B     │    …       │ org_id = C     │
 │ RLS-scoped     │            │ RLS-scoped     │            │ RLS-scoped     │
 │ Org Admin      │            │ Org Admin      │            │ Org Admin      │
 │ console + app  │            │ console + app  │            │ console + app  │
 └────────────────┘            └────────────────┘            └────────────────┘
```

- **Tenant plane** — everything a bank's own users touch, hard-scoped to their
  `organization_id` by RLS. No tenant role can ever reach cross-tenant data.
- **Platform plane** — the _only_ cross-tenant surface, for AequorOS staff. It
  must run outside RLS — **the same architectural seam as the existing
  `WORKER_DATABASE_URL` BYPASSRLS worker** — or it reads empty. It is the most
  heavily audited surface in the system.

---

## 5. Personas → roles → what they need

This is the answer to "who gets access, at what level, and for what." Posture:
**I** = initiates/creates/runs (maker), **A** = reviews/approves/signs-off
(checker), **V** = view-only.

| #   | Persona (bank job)                  | Line           | What they do & need access for                                                                                                                                                                                   | Modules                                        | Posture                         | AequorOS role preset (§6)                                                        |
| --- | ----------------------------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ------------------------------- | -------------------------------------------------------------------------------- |
| 1   | **Group / Head Treasurer**          | 1              | Owns funding & liquidity strategy; sets desk mandates; approves large exceptions; final treasury sign-off                                                                                                        | LIQ, FCST, FTP, IRRBB, FX (oversight), CAP (V) | A + V                           | Approver (LIQ/FCST/FTP/IRRBB/FX), high approval tier                             |
| 2   | **ALM / Balance-sheet Manager**     | 1              | Runs forecasts & scenarios; structural IRR & liquidity gap; curates behavioral assumptions; ALCO packs                                                                                                           | FCST, IRRBB, LIQ, BEH, FTP                     | I                               | Analyst (FCST/IRRBB/LIQ/BEH)                                                     |
| 3   | **Liquidity Manager**               | 1              | Daily cash/liquidity position; LCR/NSFR monitoring & drivers; HQLA; survival horizon                                                                                                                             | LIQ, FCST (V), DATA (V)                        | I                               | Analyst (LIQ)                                                                    |
| 4   | **Money-Market / FX Dealer**        | 1              | Executes MM/FX within mandate; deal entry; manages open positions                                                                                                                                                | FX, LIQ (funding)                              | I (deal only)                   | Analyst (FX), desk-scoped; **never** settlement approve                          |
| 5   | **Market & FX Risk Officer**        | 2              | Independent limit/VaR monitoring; challenges positions; maintains limits; breach escalation                                                                                                                      | FX, IRRBB (challenge), CAP (mkt RWA)           | A + V + configure(limits)       | Approver (FX) + Risk config                                                      |
| 6   | **IRRBB Analyst**                   | 1/2            | EVE/NII, repricing gap; NMD/prepayment assumptions; IRRBB return prep                                                                                                                                            | IRRBB, BEH, FCST, REG (IRRBB templates)        | I                               | Analyst (IRRBB/BEH)                                                              |
| 7   | **FTP / Funding Owner**             | 1              | Designs & maintains FTP curves/methodology; publishes transfer rates                                                                                                                                             | FTP, LIQ, FCST                                 | I + configure                   | Analyst (FTP) + FTP config                                                       |
| 8   | **Back-office / Settlements / Ops** | 1 (segregated) | Confirms & settles deals; payment approval/release; reconciliation; standing data                                                                                                                                | DATA, FX/LIQ post-trade                        | I + A (approve **xor** release) | Analyst (DATA) + Approver (settlements), split per user                          |
| 9   | **Financial Control / Finance**     | 1/2            | GL reconciliations; source-to-report mapping; reconcile engine↔accounting; data-quality sign-off                                                                                                                 | DATA (map), REG (prep), CAP, FCST              | I + review + export             | Analyst (DATA/REG) + export                                                      |
| 10  | **CFO**                             | Exec           | Owns finance/reg numbers; **attests/signs off returns before submission**; approves capital & funding plans                                                                                                      | REG, CAP, FCST, LIQ                            | A / sign-off                    | Approver + `reg:sign_off`, top approval tier                                     |
| 11  | **CRO / Head of Risk**              | 2              | Owns risk appetite & limits; independent review of all risk; **approves models & assumptions**                                                                                                                   | all risk modules, BEH, REG (risk returns)      | A / challenge + configure       | Approver (all) + `beh:approve` + Risk config                                     |
| 12  | **Regulatory Reporting Officer**    | 2              | Assembles BoG returns; runs validation rules; reconciles template↔source; **submits to ORASS**; owns supervisor relationship                                                                                     | REG, DATA (V), all outputs (consume)           | I + submit                      | Analyst (REG) + `reg:submit`                                                     |
| 13  | **Internal Audit**                  | 3              | Independent assurance over controls, models, SoD, lineage; reads all, changes nothing                                                                                                                            | ALL (read + **audit log**)                     | V-only + audit                  | Auditor                                                                          |
| 14  | **Compliance**                      | 2              | Obligation mapping; policy adherence; verifies SoD/four-eyes are configured & operating                                                                                                                          | REG, DATA (policy), config-audit               | V + policy config               | Auditor + policy config                                                          |
| 15  | **Board / Exec (read-only)**        | Gov            | Consume ALCO/board dashboards; risk appetite vs actuals; no operational access                                                                                                                                   | aggregated dashboards                          | V-only (published)              | Viewer (published views)                                                         |
| 16  | **Managing Director / CEO**         | Exec           | **Final attester on BoG returns** — "the MD sends the report to BoG" is a signature act, not a compilation step; chairs ALCO in most banks; consumes board views                                                 | REG (attest only), aggregated dashboards       | A / sign-off                    | Approver limited to `reg:sign_off` (signing step-up), top tier; Viewer elsewhere |
| 17  | **ALCO Secretary / pack compiler**  | 1/2            | Assembles the committee pack from unit-owned sections; chases section sign-offs; records decisions & action items; circulates pre-meeting. Usually treasury middle office or Finance (confirm with practitioner) | all module outputs (V), PACK                   | I (compile) + V                 | Analyst (PACK) + `pack:compile`, `pack:publish`, `committee:record`              |
| 18  | **Credit contributor**              | 1              | Contributes the credit section (loan book, NPLs, concentrations narrative) to the monthly committee pack; **not a treasury user** — touches nothing else                                                         | PACK (own section), LE outputs (V)             | I (own section only)            | Analyst scoped to owned pack section                                             |
| 19  | **Operations contributor**          | 1              | Contributes the op-risk incidents section monthly; **not a treasury user** — the narrowest access pattern in the product                                                                                         | PACK (own section)                             | I (own section only)            | Analyst scoped to owned pack section                                             |
| —   | **Org Admin**                       | —              | Manages the bank's users, roles, SSO/SCIM, org settings, audit — **no operational approve/run**                                                                                                                  | Settings only                                  | admin                           | Org Admin                                                                        |
| —   | **Org Owner**                       | —              | The bank's account owner: Org Admin + billing + ownership transfer                                                                                                                                               | Settings + billing                             | admin+                          | Org Owner                                                                        |

**Reading it for dashboards:** persona → role preset → [§9](#9-per-persona-dashboards-what-to-build) tells you the landing page, visible nav, and allowed actions to render.

### 5.1 The organizational overlay — units, committees, and the two reporting flows

Added 2026-08-07 from a practitioner review (16y Ghanaian bank regulatory
reporting/treasury) plus LMTD 2026 Part II ¶10–21. A bank runs **two distinct
flows** that our personas must serve, and they exercise different parts of the
product:

**Flow A — internal governance (monthly).** Each _arm_ of the bank produces its
piece → the ALCO Secretary compiles the committee pack from unit-owned sections
→ ALCO meets, challenges (LMTD ¶11(h) makes "review and challenge" a Board duty),
decides, minutes → escalates to Board. The pack always contains sections the
platform does not compute (macro commentary, credit narrative, op-risk
incidents) — hence the contributor personas (#18–19) and manual sections in the
pack model.

**Flow B — regulatory filing.** Finance/Reg Reporting _prepares_ returns (#9,
#12); sign-off is an attestation chain (#10 CFO, #16 MD); submission goes out
under `reg:submit` (#12). The MD compiles nothing — the MD signs. This flow is
already built (attestation + PAdES signing); persona #16 exists so the MD's
narrow footprint (sign + view, never edit) is a preset, not a custom role.

The two flows reconcile by construction here: both read the same canonical
store and immutable runs. That single-source property is the product's answer
to the spreadsheet-per-department status quo — never fork per-department apps.

**Bank org unit → preset bundle** (module grants express departments; no new
base roles needed — §6.2 multi-role covers hybrids):

| Bank unit                   | Line | Preset bundle (invite-time)                                          |
| --------------------------- | ---- | -------------------------------------------------------------------- |
| Treasury front office       | 1    | #1 Treasurer, #3 Liquidity Mgr, #4 Dealer (desk-scoped)              |
| ALM / middle office         | 1/2  | #2 ALM Mgr, #7 FTP Owner, #17 ALCO Secretary                         |
| Risk (CRO org)              | 2    | #5 Market Risk, #11 CRO, #6 IRRBB Analyst                            |
| Finance / Financial Control | 1/2  | #9 Finance, #10 CFO, #12 Reg Reporting Officer                       |
| Credit                      | 1    | #18 Credit contributor (LE outputs view + own pack section)          |
| Operations                  | 1    | #8 Back-office, #19 Ops contributor                                  |
| Executive                   | —    | #16 MD/CEO, #15 Board viewers                                        |
| Internal Audit              | 3    | #13 Auditor (LMTD ¶20–21: annual framework review, reports to Board) |

**LMTD Part II hooks this spec must serve:** ¶11(b)–(e) — the **Board** sets
internal thresholds for the six monitoring tools, cumulative and per-currency
mismatch limits, and concentration limits: threshold configuration is therefore
`risk:configure` **with Board-approval evidence attached** (the threshold
register, product.md Phase 2); ¶11(g)–(h) — review-and-challenge evidence is the
committee record (`committee:record`); ¶20–21 — the Auditor persona is a
regulatory requirement, not a nice-to-have.

**LRMD 2026 hooks (directive obtained + analysed 2026-08-07 —
`lrmd_gap_analysis.md` §7):** ¶19 — the **CRO is ultimately responsible** for
liquidity-risk oversight (persona #11's grounding, per CGD 2018 + RMD 2021);
¶20 — **ALCO "and any other management committee"** oversees liquidity risk,
which endorses the generalized-committee design over a hardcoded ALCO; ¶17 —
Senior Management must ensure **separation of responsibilities** in liquidity
processes (SoD §7.4 is a liquidity-directive requirement, not just best
practice); ¶12 — the quarterly **LAS is the Board's statement** to BoG, so Flow B
gains a Board-level attestation above the CFO/MD pair (signer convention: open
question, `lrmd_gap_analysis.md` §9 Q12); ¶87(a) — the annual public disclosure
must describe committee and unit responsibilities, i.e. this §5.1 structure
becomes disclosable content.

---

## 6. Role catalog (predefined tenant roles)

Ship a **small** predefined set. Personas are **presets** = `(base role) + (module
grants) + (approval tier)` applied at invite time — not new roles. This is the
Stripe/Datadog/Okta pattern (predefined roles + optional custom roles later),
deliberately capped to avoid role explosion.

### 6.1 Target base roles

| Base role                         | Replaces / maps from      | Purpose                                      | Key permissions                                                       | Must NOT                                        |
| --------------------------------- | ------------------------- | -------------------------------------------- | --------------------------------------------------------------------- | ----------------------------------------------- |
| **Viewer**                        | `viewer`                  | Read-only within scope                       | `*:view` (scoped)                                                     | any mutation                                    |
| **Auditor**                       | _(new; a Viewer variant)_ | Read-only **+ audit-log read**, whole tenant | `*:view`, `audit:read`                                                | any mutation, any approve                       |
| **Analyst** (Preparer / maker)    | `analyst`                 | Core treasury/ALM work                       | `{module}:view                                                        | create                                          | edit                               | run`, `export` (scoped) | approve/sign-off/submit **their own** object |
| **Approver** (Reviewer / checker) | `approver`                | Four-eyes approval                           | `{module}:review                                                      | approve`, `reg:sign_off`, `reg:submit` (scoped) | edit the object they are approving |
| **Org Admin**                     | _(split out of `admin`)_  | Account administration only                  | `users:*`, `roles:*`, `sso:*`, `scim:*`, `org:settings`, `audit:read` | operational `run/approve/submit` (SoD C9)       |
| **Org Owner**                     | _(top of `admin`)_        | Bank's account owner                         | Org Admin **+** `billing:*`, `org:transfer`, `org:delete`             | cross-tenant anything                           |
| **Billing Manager**               | _(new, optional)_         | Subscription & seats                         | `billing:*`                                                           | domain data                                     |

**Migration note:** today's single `admin` conflates account-admin with
operational-super — a segregation-of-duties smell (Snowflake's rule: never mix
account-management privileges with entity privileges in one role). The
foundation adds Account Admin as a static binding bundle, but migration
`202608250044` deliberately creates no bindings and converts no existing
`admin`. Org Owner is not a foundation bundle. Keep the legacy hierarchy for
backward compatibility during shadow rollout; a later governed migration must
make any Admin/Owner mapping explicit.

### 6.2 A user can hold more than one scoped bundle

Do not derive this authority from token `roles[]`: that claim is legacy rollout
state and the new evaluator ignores it. Persist one complete
`authorization_bindings` row per bundle/scope combination. A person can be
**Analyst on LIQ and Approver on REG** simultaneously because independently
matching rows union; their dimensions never form a Cartesian product. Thus the
same rows do not grant Analyst on REG or Approver on LIQ. **Maker≠checker** is a
non-bypassable per-object condition (§7.4), not a reason to forbid holding both
bundles.

### 6.3 Custom roles — later, gated

Do **not** ship a custom-role builder in v1. When a bank demonstrably can't be
expressed by presets, add a **clone-a-role → toggle permissions** editor
(Datadog pattern) with **sensitive permissions visibly flagged**, and cap the
count (Okta caps at 100/org) to prevent proliferation.

---

## 7. Permission model

> **Foundation boundary:** `app/core/authorization.py` stores a small action
> enum separately from the resource's concrete module. Its v1 bundles grant:
> Viewer/Auditor = `view`; Analyst = `view|create|edit|run|validate|export`;
> Approver = `view|review|approve`; Account Admin = `administer`; and the
> machine-only Integration Writer = `ingest`. `configure`, `sign_off`, and
> `submit` are reserved but are not in any v1 bundle. The richer namespaces and
> matrices below remain target design, not as-built authority.

### 7.1 Permission namespace (`resource:action`)

**Domain (per module)** — `{module} ∈ liq | cap | irrbb | fx | ftp | fcst | beh | data | reg | risk | markets`:

```
{module}:view          see data / dashboards / results
{module}:create        create a draft object (scenario, mapping, return)
{module}:edit          modify an unlocked draft
{module}:run           execute an engine / scenario / calculation run (→ immutable snapshot)
{module}:review        maker-checker "checker" step (challenge / request-changes)
{module}:approve       bless a run/object/limit for downstream use (subject to approval tier)
{module}:configure     change assumptions / FTP curves / mappings / thresholds / model params
{module}:export        extract data / reports (sensitivity-gated)
```

**REG-specific extras** (the filing chain — Flow B, §5.1):

```
reg:validate           run BoG validation rules + reconcile template↔source
reg:sign_off           formal attestation gate (CFO / CRO / Head of Reg / MD)
reg:submit             transmit a signed return to BoG / ORASS
```

**Committee-pack extras** (the internal governance chain — Flow A, §5.1;
namespace reserved here, ships with the pack contribution workflow,
product.md global Phase 3):

```
pack:view              see the assembling committee pack
pack:contribute        create/edit the section(s) the user's unit owns —
                       engine-backed OR manual/uploaded content
pack:signoff_section   unit-head sign-off on an owned section (maker≠checker
                       per section, same mechanism as §7.4)
pack:compile           assemble signed-off sections into the circulated pack
pack:publish           freeze + circulate to committee members (immutable)
committee:record       minutes, decisions log, action items with owners —
                       the ¶11(h) "review and challenge" evidence
```

**Account plane (org admin):**

```
org:settings   users:read   users:manage   roles:read   roles:manage
sso:manage     scim:manage   audit:read    billing:manage   org:transfer   org:delete
```

**Platform plane (vendor):**

```
platform:tenants   platform:provision   platform:impersonate
platform:flags     platform:billing     platform:audit   platform:staff
```

### 7.2 Base-role → permission matrix (verbs collapsed across granted modules)

`●` = for every module in the user's grant scope. `—` = never.

| Permission                                      | Viewer | Auditor | Analyst | Approver | Org Admin | Org Owner |
| ----------------------------------------------- | :----: | :-----: | :-----: | :------: | :-------: | :-------: |
| `{m}:view`                                      |   ●    |    ●    |    ●    |    ●     |    ●¹     |    ●¹     |
| `{m}:create` / `edit`                           |   —    |    —    |    ●    |    —     |     —     |     —     |
| `{m}:run`                                       |   —    |    —    |    ●    |    —     |     —     |     —     |
| `{m}:review` / `approve`                        |   —    |    —    |    —    |    ●     |     —     |     —     |
| `{m}:configure`                                 |   —    |    —    |   ●²    |    ●²    |     —     |     —     |
| `{m}:export`                                    |   —³   |    ●    |    ●    |    ●     |     —     |     —     |
| `reg:validate`                                  |   —    |    —    |    ●    |    ●     |     —     |     —     |
| `reg:sign_off`                                  |   —    |    —    |    —    |    ●⁴    |     —     |     —     |
| `reg:submit`                                    |   —    |    —    |   ●⁴    |    ●⁴    |     —     |     —     |
| `pack:contribute`                               |   —    |    —    |   ●⁵    |    —     |     —     |     —     |
| `pack:signoff_section`                          |   —    |    —    |    —    |    ●⁵    |     —     |     —     |
| `pack:compile` / `publish` / `committee:record` |   —    |    —    |   ●⁵    |    —     |     —     |     —     |
| `audit:read`                                    |   —    |    ●    |    —    |    —     |     ●     |     ●     |
| `users:* / roles:* / sso:* / scim:*`            |   —    |    —    |    —    |    —     |     ●     |     ●     |
| `org:settings`                                  |   —    |    —    |    —    |    —     |     ●     |     ●     |
| `billing:* / org:transfer / org:delete`         |   —    |    —    |    —    |    —     |     —     |     ●     |

¹ Org Admin/Owner see dashboards for administration context but hold no operational write.
² `configure` is granted per-preset (FTP owner, ALM assumptions, Risk limits) — not to every Analyst/Approver.
³ Board/Exec "Viewer" gets published-dashboard view only; raw `export` is off by default.
⁴ `sign_off` / `submit` are **preset add-ons** (CFO, MD, Head of Reg), not blanket to every Approver — and are **SoD-gated** (§7.4).
⁵ `pack:*` / `committee:*` are **preset add-ons** (§5 #17–19 + unit heads), section-scoped (§7.3): a Credit contributor holds `pack:contribute` on the credit section only; `compile`/`publish`/`committee:record` belong to the ALCO Secretary preset (#17).

### 7.3 Scoping dimensions

Every grant is evaluated within a scope. Default-deny outside it.

| Scope                           | Meaning                                                                                        | Enforcement                                                                                                   |
| ------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Organization**                | the security tenant/account (`OR-*`)                                                           | binding `organization_id` + forced RLS                                                                        |
| **Institution**                 | one bank/legal entity (`BK-*`) beneath the organization                                        | exact `institution_id`, or explicit `institution_scope=organization`                                          |
| **Module**                      | LIQ/CAP/…                                                                                      | exact `module_scope`, or explicit `all`                                                                       |
| **Desk / portfolio / currency** | a dealer acts only on their book (Bloomberg TOMS precedent: user/desk/asset-class/region/firm) | future scoped extension; absent from rollout v1                                                               |
| **Data sensitivity**            | published, aggregated, confidential, or restricted                                             | exact `sensitivity_scope`, or explicit `all`                                                                  |
| **Environment**                 | live vs **demo**                                                                               | global condition veto, not a role or binding dimension                                                        |
| **Approval tier**               | numeric ceiling on `approve` (deal size / exception magnitude); above → escalate               | attach to the `approve` grant per preset                                                                      |
| **Pack section**                | a contributor acts only on the committee-pack section(s) their unit owns (§5.1 Flow A)         | `pack_sections.owner` (role preset or named users); `pack:contribute`/`signoff_section` evaluated per section |

### 7.4 Segregation of Duties — enforced at action time

Generalize the existing REG maker-checker to a **reusable approval primitive**.
For any approvable object (calculation run, official run, scenario/assumption
set, regulatory package, limit exception):

```
draft ─▶ submitted_for_review ─▶ reviewed ─▶ approved/attested ─▶ [submitted]
  maker         maker              checker₁      checker₂             submitter
```

**Hard rule (deny at action time):** the `actor_user_id` of an `approve` /
`sign_off` / `submit` **must differ from** every prior maker on that object.
This is already implemented for regulatory packages — lift it into a shared
service (`app/services/approvals.py`) keyed on `(object_type, object_id, org_id)`.

**Toxic-combination denies (checked at role-assignment time too):**

| #   | Deny both to one identity within the same scope                             | Why                                    |
| --- | --------------------------------------------------------------------------- | -------------------------------------- |
| C1  | deal entry (FX) **&** deal confirm/settle                                   | front ≠ back office                    |
| C2  | payment/settlement **approve** & **release**                                | two-stage even inside back office      |
| C3  | reconciliation & payment approval                                           | conceal-your-own-error risk            |
| C4  | DATA ingest/map/activate **&** sign-off/submit of the return built on it    | producer ≠ approver of numbers         |
| C5  | configure scenario/BEH assumptions **&** approve the run that consumes them | assumption-setter can't self-bless     |
| C6  | run an engine calc **&** reg sign-off/submit of that result                 | run ≠ approve ≠ submit                 |
| C7  | BEH model owner **&** model validator **&** audit                           | Three-Lines independence               |
| C9  | user/role administration **&** operational approve rights on same object    | admin can't grant themselves approvals |
| C10 | reg-return preparer **&** internal sign-off **&** submitter                 | prepare / attest / submit split        |

Ship SoD **monitoring/reporting**, not just assignment-time blocks — the Kyriba
lesson is that role assignment alone is insufficient; auditors want a report of
who _could_ violate SoD and who _did_.

---

## 8. Enforcement architecture

### 8.1 Backend (extend `app/api/deps.py`)

The persistence-neutral vocabulary, explicit-target `ResourceLocator`, static
bundle map, exact evaluator, database service, and transactional invalidation
seam are built.
`evaluate_permission()` starts denied, loads only the principal's persisted
bindings, requires every dimension within one row to match, unions complete
rows, verifies the active principal and institution ownership, then applies all
workflow-supplied conditions as global vetoes. It returns an audit-ready trace.

Liquidity Monitoring is the first measured path: it constructs the canonical
institution target and emits `authz.shadow_decision`, but the binding result is
not yet an endpoint gate. For each later migrated vertical, construct the same
explicit locator and call the service evaluator; do not resolve authority from
`roles[]`, route names, HTTP verbs, or UI state. Keep
`get_mutation_tenant_context` as the legacy coarse gate during measured shadow
rollout. Demo-mode, maker-checker, step-up, and approval limits remain owned by
their workflows and enter the evaluator as typed conditions, so another allow
binding cannot bypass them.

`users.authorization_version` is already live. Every app token carries `authv`;
tenant validation and refresh reject a stale version. Any future role, scope,
status, or security mutation must call
`invalidate_user_authorization()` in the same transaction, which increments the
version and revokes every refresh family with `authorization_changed`.

### 8.2 Frontend (dashboard)

- **Nav filtering** (`Sidebar.tsx`): render a nav item only if the session grants
  any `{module}:view`. Drives which modules a persona even sees.
- **Route guard:** `middleware.ts` already gates auth. Add a per-route permission
  check (or a server component guard) so deep links to a module the user can't
  see redirect to their landing page, not a 403 wall.
- **Action gating:** buttons for `run / approve / sign_off / submit / configure`
  render disabled-with-tooltip or hidden based on permissions in the session.
  Never rely on hiding alone — the backend is the boundary; the UI just avoids
  dead ends.
- **Session state:** do not make token `roles[]`, `perms`, or `scopes` an
  authority source. Expose an effective, display-only capability summary from a
  server-evaluated `/auth/me` contract when endpoint rollout begins. A version
  change invalidates the app session through `authv` (§8.1).

### 8.3 Default landing per role

Send each user to where their job starts, not always Command Center:

| Role preset                         | Default landing                       |
| ----------------------------------- | ------------------------------------- |
| Treasurer / ALM / CFO / CRO / Board | `/` Command Center (their role-lens)  |
| Liquidity Manager                   | `/liquidity`                          |
| IRRBB Analyst                       | `/irr`                                |
| FX Dealer / FX Risk                 | `/fx`                                 |
| FTP Owner                           | `/ftp`                                |
| Finance / Reg Reporting Officer     | `/submissions` (Regulatory Reporting) |
| Data/Ops                            | `/data-engine`                        |
| Auditor / Compliance                | `/reports` + Audit log                |
| Org Admin / Owner                   | `/settings` (Org console)             |

---

## 9. Per-persona dashboards (what to build)

For each role preset the dev renders: **default landing**, **visible nav**,
**allowed actions**, **hidden/disabled**. Use the same underlying pages —
role-gate the surface, don't fork the app.

| Role preset                  | Visible nav (modules)                                                       | Allowed actions                                                           | Hidden / disabled                                      |
| ---------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------ |
| **Treasurer**                | Command Center, LIQ, FCST, FTP, IRRBB, FX, CAP(view), Risk, Alerts, Reports | approve runs/exceptions, view all, mint official runs (with tier), export | Data Engine writes, Settings admin, reg submit         |
| **ALM Manager**              | Command Center, FCST, IRRBB, LIQ, BEH, FTP, Markets, Positions              | create/edit scenarios, run calcs, configure assumptions                   | approve own runs, reg sign-off, Settings admin         |
| **Liquidity Manager**        | Command Center, LIQ, FCST(view), Alerts, DATA(view)                         | run LIQ, monitor, view                                                    | other-module writes, approve, Settings                 |
| **FX Dealer**                | FX (own desk), Markets, Positions, LIQ(funding)                             | deal entry on own desk/ccy                                                | settlement approve/release, other desks, other modules |
| **FX / Market Risk Officer** | FX, IRRBB(challenge), CAP(mkt), Risk, Alerts                                | review/approve FX limits, configure limits, view                          | deal entry (SoD), reg submit                           |
| **IRRBB Analyst**            | IRRBB, BEH, FCST, REG(IRRBB templates)                                      | run IRRBB, set assumptions, prep IRRBB return                             | approve own, submit, Settings                          |
| **FTP Owner**                | FTP, LIQ, FCST                                                              | configure FTP curves, run, approve own methodology                        | other-module writes, reg submit                        |
| **Back-office / Ops**        | DATA, FX/LIQ post-trade, Alerts                                             | confirm/settle, approve **xor** release (per user)                        | deal entry, both approve+release                       |
| **Finance / Control**        | DATA(map), REG, CAP, FCST, Reports                                          | map data, prep returns, reconcile, export                                 | reg sign-off (unless CFO), approve own                 |
| **CFO**                      | Command Center(CFO lens), REG, CAP, FCST, LIQ, Reports                      | **sign off / attest** returns & plans, approve, view                      | data entry, deal entry                                 |
| **CRO / Head of Risk**       | all risk modules, BEH, REG(risk), Risk & Limits, Alerts                     | approve/challenge, approve models, configure risk appetite/limits         | run own calcs then self-approve                        |
| **Reg Reporting Officer**    | REG, DATA(view), all outputs(view), Reports                                 | prep + validate + **submit to ORASS** (post sign-off)                     | edit source financials, self-sign-off                  |
| **Auditor**                  | ALL (read) + **Audit log**                                                  | view everything, export evidence                                          | every mutation, every approve                          |
| **Compliance**               | REG, DATA(policy), Reports, Audit(read)                                     | configure policy/SoD rules, view                                          | operational writes                                     |
| **Board / Exec (Viewer)**    | Command Center (published), module dashboards (aggregated)                  | view published views only                                                 | raw data, export, drill-to-source                      |
| **MD / CEO**                 | Command Center (published), REG (attestation queue), Reports                | **sign/attest returns** (step-up), view published views                   | edit anything, deal entry, data entry, submit          |
| **ALCO Secretary**           | Committee pack workspace, all module outputs (view), Reports                | assemble pack, chase section sign-offs, record decisions/actions, publish | edit unit sections they don't own, reg sign-off/submit |
| **Credit / Ops contributor** | **Their pack section only** (+ LE outputs view for Credit)                  | contribute + edit own section until sign-off                              | everything else — the narrowest surface in the app     |
| **Org Admin / Owner**        | **Settings / Org console** (+ read dashboards)                              | manage users/roles/SSO/SCIM/audit (+ billing = Owner)                     | run/approve/submit anything                            |

The existing **role-lens tabs** on Command Center (Treasurer / ALM / Risk / CFO)
are the right pattern — extend them to _default and lock_ to the user's role
rather than being a free toggle for everyone.

---

## 10. Settings architecture (three tiers)

Three distinct surfaces. **Do not conflate them.**

### 10.1 Personal — the avatar dropdown (every user)

Already partly built (`Header.tsx`). Target:

```
[Avatar ▾]
  ── Signed in as jane@bigbank.com · Approver ──
  Profile & preferences        (name, avatar, job title, locale, timezone, theme, notifications)
  My security                  (my password, my MFA devices, my active sessions / "sign out everywhere", personal API tokens)
  ─────────────
  Organization settings        ← only if users:read / org:settings
  Audit log                     ← only if audit:read
  ─────────────
  Switch organization ▸        ← if the user belongs to >1 org (rare for banks; keep for staff)
  Documentation / Support
  Sign out                      ← already wired
```

### 10.2 Organization admin console (`/settings`, admin-only)

Self-service administration scoped to one `organization_id`. Left-nav IA:

```
Settings
├─ Organization
│   ├─ General            name, logo, locale, jurisdiction, org defaults
│   ├─ Members            users list + invites            ← default landing (§12.2)
│   ├─ Roles & permissions  view presets; who-has-what matrix; (later) custom roles
│   └─ Billing & seats    plan, seat usage/limit, invoices   (Owner)
├─ Authentication
│   ├─ Single sign-on     self-service OIDC per org — SEED BUILT (Settings →
│   │                     Authentication card: issuer/client id/write-only
│   │                     secret/domains/enable); grow in place, add SAML later
│   ├─ Verified domains   DNS-TXT domain verification + capture
│   └─ Provisioning       SCIM token, attribute/group→role mapping, sync status
├─ Security
│   ├─ MFA & step-up policy
│   ├─ Session policy     idle + absolute lifetime, sign-in frequency
│   └─ IP allowlist
└─ Audit log              tenant-scoped, read-only, filterable
```

Today's Settings page (Institution profile, Appearance, Users & roles, Data &
compute, About) is the seed of **Organization → General** + the read-only
**Data & compute**. Grow it into the above; the current "Users & roles" panel
(now showing the real signed-in user) becomes the full **Members** page.

### 10.3 Platform / vendor super-admin console (`console.aequoros.com`, staff-only)

Separate app/subdomain, **never** mixed into the tenant nav. Runs outside RLS.

```
Platform admin
├─ Tenants / Organizations   list all banks; create/provision; suspend/offboard; health
│   └─ Tenant detail          users, plan, feature flags, connections, usage, entitlements
├─ Provisioning              onboard a bank: seed org + first Org-Owner invite + data-scope + env
├─ Support / Impersonation   "view as" a tenant user (read-only, audited — §11.5)
├─ Billing & subscriptions   cross-tenant plans, invoices, seat enforcement
├─ Feature flags             per-tenant + global rollout (market-data adapters, ML-ETL, etc.)
├─ Global audit log          every staff action across all tenants; impersonation first-class
└─ Platform staff & roles    vendor employees: Super-Admin / Support Engineer / Billing Ops / Read-only
```

Apply least-privilege here too: most support staff are **read-only or
impersonation-gated**, not full super-admin. Minimize standing super-admins.

---

## 11. User lifecycle & onboarding

### 11.1 State machine

```
   (none) ── invite ──▶ INVITED ── accept (set credential / SSO) ──▶ ACTIVE
                          │  TTL          ┌──────────────────────────┘ │
                          │  expires      │                            │ admin suspends
                          ▼               │ reactivate                 ▼
                       EXPIRED            └──────────────────────── SUSPENDED
                          ▲                                            │ offboard
              admin revokes│                                           ▼
                       REVOKED                                    DEACTIVATED ──(retention)──▶ DELETED/ANONYMIZED
```

- **INVITED** — record exists, token outstanding, no login yet.
- **ACTIVE** — first successful credential set / SSO login.
- **SUSPENDED** — access blocked, record + history preserved; reversible.
- **DEACTIVATED** — offboarded: login gone, sessions/tokens revoked, data retained under a custodian.
- **DELETED / ANONYMIZED** — after the retention window (records-retention / GDPR).

> **Separate access-revocation from data-retention.** A departing user loses
> login immediately; their cases/scenarios/reports transfer to a custodian.
> Deactivate ≠ delete.

### 11.2 Invite-by-email flow

1. **Admin opens Members → Invite**: enters email(s), picks **role preset(s)** and **scope** (entities/desks), only offering grants the admin themselves may give. System **checks the seat limit** before allowing send.
2. **Generate token**: ≥32 bytes CSPRNG, URL-safe; **store only a hash** (like a password) so a DB breach doesn't leak pending invites. Token binds `{email, org_id, role_preset(s), scope, invited_by, expires_at}`.
3. **Send email** with a single-use link. **TTL 48 h – 7 days** (7-day + resend is common). State → **INVITED**.
4. **Invitee clicks** → validate (unexpired, unused, email/org match). If the org **enforces SSO**, route to the IdP (don't ask for a password). Else streamlined signup, **email pre-filled**.
5. **Set credential / first login** → **consume token (single-use, delete after enrollment)** → state → **ACTIVE**.
6. **Resend** reissues + invalidates the old token; **Revoke** invalidates → **REVOKED**.

### 11.3 SSO (OIDC, later SAML) — per organization

Each bank brings its own IdP (Google Workspace, Entra, Okta, Ping). **AequorOS
is its own OIDC relying party — there is no third-party auth broker (Auth0 was
removed 2026-07-20), and none should be reintroduced.** The single-connection
version is BUILT (see §2): `sso_connections` + Settings → Authentication +
zero-trust backend verification + the bank-IT runbook `docs/sso-onboarding.md`.
Phase 2 **continues from that code**: many connections per org, email-first
home-realm discovery on /login (type work email → route to that bank's IdP —
NextAuth v5's per-request lazy config is the mechanism, already in use in
`dashboard/auth.ts`), verified domains, and `sso_enforced` per org. SSO decides
_who may sign in_; provisioning decides _who exists_.

### 11.4 JIT + SCIM + verified domains

- **JIT** — **BUILT in request-access form** (opt-in `jit_enabled` per connection): first OIDC login from an allowed email domain records a deactivated stub; an admin approves it with an explicit role before any access exists. Phase 2 adds group→role mapping (which can then safely auto-activate). **JIT does not deprovision** → SCIM below is the governance answer.
- **SCIM 2.0** — the IdP syncs create/update/**deactivate** to AequorOS. **Mandatory for bank tenants** — SCIM-driven deprovisioning is the single most-probed enterprise security-questionnaire item. Key on a **stable IdP id (`externalId`/`sub`), never email**, or JIT+SCIM produce duplicate records.
- **Verified domains** — a tenant verifies a domain via DNS TXT; then auto-suggest membership and/or **enforce SSO** for all users on that domain (domain capture stops shadow personal accounts).
- **Rule:** JIT creates, SCIM governs, SSO authenticates, verified domains bound the population — all keyed on one stable identifier.

### 11.5 Offboarding (deprovision order)

SSO cutoff alone does **not** kill live sessions or app-native entitlements. In order:

1. Disable account + **terminate all active sessions** (active-user validation
   rejects access tokens; revoke every refresh family).
2. **Revoke API keys, refresh/OAuth tokens, connected-app grants.**
3. Remove app-native roles/scopes.
4. **Transfer ownership** of cases/scenarios/reports/connections to a named custodian _before_ deactivating.
5. Rotate any shared secrets held.
6. **Post-offboarding audit 24–72 h later** to confirm all paths closed.

### 11.6 Impersonation (platform support) — done right

For the platform console's "view as" (Pigment's reference model):

- **New JWT per session** with the impersonated user's identity **plus a separate
  `impersonator` claim** for attribution, a **read-only flag**, and a **≤30-min
  expiry**. Keep the admin's own token separately for instant exit.
- **Read-only by default, enforced in middleware** (mutations → 403 before
  business logic). Impersonation is for _observing_, not acting.
- **Access = intersection** of both users' permissions, only within orgs the
  impersonator already covers.
- **Persistent unmistakable UI**: full-screen border + sticky banner with the
  impersonated email and one-click **Exit**.
- **JIT approval + reason/ticket binding**; auto-expire.
- **Audit everything**: initiator, assumed identity, reason/ticket, start, org
  scope, resources touched. For a bank product, **notify the tenant Org Admin**
  when a vendor impersonates one of their users; consider tenant opt-in consent.

---

## 12. User menu & UI specs

### 12.1 Role-aware account menu

See [§10.1](#101-personal--the-avatar-dropdown-every-user). Show the signed-in identity + role; reveal
"Organization settings" / "Audit log" **only** with the matching permission.

### 12.2 Members (users list) table

**Columns:** Name · Email · **Status** badge (Invited / Active / Suspended /
Deactivated) · **Role(s)** · Scope (entities/desks) · Last active · Auth method
(SSO / password) · Invited-by · Actions (⋯).

**Filters:** status, role, auth method, domain, last-active range; column
show/hide; sort; global search.

**Bulk actions** (row checkboxes → bottom-center action bar): change role, assign
scope, activate/suspend/deactivate, resend/revoke invite. Destructive actions get
a confirm modal **stating the affected count**; show a per-row success/fail
summary; keep selection after non-destructive actions.

### 12.3 Invite modal (not a page)

Fields: **Email(s)** (multi / CSV) · **Role preset(s)** (only grantable ones) ·
**Scope** (entities / desks) · optional message · **seat-usage indicator**
("12 of 25 seats used"). Primary "Send invite(s)" → new rows appear as **Invited**.

### 12.4 Role editor

List presets with descriptions + a **permission matrix** (resource rows × verb
columns, checkmarks) and "N users have this role." Custom-role flow (later):
clone → rename → toggle, with **sensitive permissions flagged**.

### 12.5 Audit log view

Read-only table: **Timestamp · Actor (with impersonator chain) · Action ·
Target · Reason · IP/session · Result**. Filters: actor, action type, resource,
date range, org (platform only). Export to CSV/SIEM. Impersonation & privileged
actions surfaced distinctly. **Immutable — no edit/delete in the UI.**

---

## 13. Data model

Additive migrations. New/changed tables:

**`organizations`** (extend the bare `{id,name}`):
`legal_name, logo_url, jurisdiction_code, locale, timezone, plan, seat_limit,
status (active|suspended), sso_enforced (bool), created_by`.

**`users`** (extend `app/models/user.py`):
add `status (invited|active|suspended|deactivated)`, `job_title`,
`external_id` (stable IdP id for SCIM), `invited_by`, `invited_at`,
`activated_at`, `deactivated_at`, `mfa_enrolled (bool)`. Keep `role` for legacy
endpoint compatibility. `authorization_version` is already built; use it rather
than adding a second session-generation field.

**`authorization_bindings`** _(BUILT, shadow-only)_: one indivisible row holds
`organization_id, principal_user_id, principal_type, role_bundle,
institution_scope, institution_id, module_scope, sensitivity_scope,
granted_by_type, granted_by_id, grant_reason, granted_at, status, valid_from,
valid_until, revoked_at, revoked_reason`. Composite principal/institution tenant
foreign keys, checks, and FORCE RLS enforce the shape. This table supersedes the
independent `user_roles`/`user_scopes` proposal, whose arrays could accidentally
create cross-product authority.

**`invitations`** _(new)_:
`id, org_id, email, role_presets[], scope, token_hash, invited_by, expires_at,
status (pending|accepted|expired|revoked), accepted_at`. Store **only the hash**.

**`sso_connections`** _(BUILT in single-connection form — extend, don't recreate)_:
as-built columns: `organization_id (unique — Phase 2 drops the unique for
multi-connection), issuer, client_id, client_secret_ciphertext +
client_secret_fingerprint (AES-256-GCM via CREDENTIAL_VAULT_MASTER_KEY),
allowed_email_domains (json), enabled, updated_by` + RLS (enabled/forced +
tenant policy). Phase 2 adds: `protocol (saml|oidc), idp_metadata (SAML),
domains[] (verified), enforced (bool), jit_enabled, scim_token_hash, status`.

**`approvals`** _(new — generalize `RegulatoryPackageApproval`)_:
`id, org_id, object_type, object_id, action (requested|reviewed|approved|rejected|signed_off|submitted),
actor_user_id, reason, occurred_at`. Append-only; **checker ≠ prior maker enforced in the service**.

**`audit_log`** _(new — append-only, tamper-evident)_:
`id, org_id (nullable for platform), actor_user_id, impersonator_user_id (nullable),
action, resource_type, resource_id, reason, ip, session_id, result, occurred_at`.
Not editable/deletable via the app; consider hash-chaining + WORM storage
(S3 Object Lock). Covers logins, role/permission changes, every
create/edit/delete/**export**, approvals, submissions, impersonation, SSO/SCIM
changes, and every financial mutation (with its reason).

**`platform_staff`** + **`platform_staff_roles`** _(new)_: vendor employees
(super-admin / support / billing / read-only), outside RLS.

**`impersonation_sessions`** _(new)_: `id, impersonator_user_id, target_user_id,
org_id, reason, ticket_ref, read_only, started_at, expires_at, ended_at`.

### RLS reminder

Everything tenant-scoped keeps RLS on `organization_id`. `platform_staff`,
`impersonation_sessions`, and cross-tenant reads live on the **BYPASSRLS** path
(same seam as `WORKER_DATABASE_URL`). The platform console is the _only_
cross-tenant surface.

---

## 14. Target API surface

Except for the routes explicitly marked **BUILT** below, this is the target
tenant contract rather than the current OpenAPI surface. The existing
`GET /auth/me` returns identity, preferences, and the legacy scalar role; its
effective-permission and scope fields land with endpoint authorization rollout.

Tenant plane (under `/api/v1`, RLS-scoped, permission-gated where noted):

```
# current user
GET   /auth/me                                    → identity + roles + effective perms + scopes

# members (org admin)
GET   /orgs/{org}/users                           users:read
POST  /orgs/{org}/users/{id}:suspend|activate     users:manage
DELETE/orgs/{org}/users/{id}                       users:manage   (→ deactivate; hard-delete platform-only)
PATCH /orgs/{org}/users/{id}/roles                 roles:manage    (SoD-checked)

# invitations
POST  /orgs/{org}/invitations                      users:manage    (seat-checked)
GET   /orgs/{org}/invitations                       users:read
POST  /orgs/{org}/invitations/{id}:resend|revoke   users:manage
POST  /invitations/accept                          (public, token in body)   → set credential / SSO

# roles & permissions
GET   /orgs/{org}/roles                             roles:read
POST  /orgs/{org}/roles                             roles:manage    (custom roles — later)

# authentication config
# BUILT (Phase-1 form, admin-role-gated; migrate to sso:manage when the
# permission layer lands — same handlers, new dependency):
#   POST /auth/sso                public exchange; body is {id_token} only;
#                                 verified connection selects the organization
#   GET  /auth/sso/status         public login-page probe {enabled}
#   GET  /auth/sso/connection     admin — secret returned only as client_secret_set
#   PUT  /auth/sso/connection     admin — upsert; client_secret write-only
#   GET  /auth/sso/client-config  internal (SSO_INTERNAL_KEY header; not in OpenAPI)
GET/PUT /orgs/{org}/sso                             sso:manage   (Phase 2: multi-connection)
POST    /orgs/{org}/domains:verify                  sso:manage
GET/PUT /orgs/{org}/scim                            scim:manage
/scim/v2/Users, /scim/v2/Groups                     (SCIM 2.0, token-auth)

# org settings, security, audit
GET/PUT /orgs/{org}/settings                        org:settings
GET/PUT /orgs/{org}/security-policy                 org:settings
GET     /orgs/{org}/audit                           audit:read

# approvals (generalized maker-checker) — mounted per approvable object
POST  /.../{object}:request-review | review | approve | reject | sign-off | submit
```

Platform plane (`console.aequoros.com`, outside RLS, `platform:*`):

```
GET/POST /platform/tenants                          platform:tenants / platform:provision
GET      /platform/tenants/{org}                    platform:tenants
POST     /platform/impersonations                   platform:impersonate   (reason+ticket, read-only, ≤30m)
GET/PUT  /platform/flags                             platform:flags
GET      /platform/audit                             platform:audit
```

The generated TS client (`packages/risk-service-api`) must be regenerated
(`mise run risk-service:openapi-client`) after adding these; assign explicit
`operation_id`s to avoid the schema-name collisions noted in CLAUDE.md.

---

## 15. Phased roadmap

Ship value early; don't block the dashboards on SSO/SCIM.

**Phase 0 — authorization foundation and first endpoint slice.**
The static `ROLE_PERMISSIONS` map, scoped binding table, exact evaluator, and
`authv` invalidation seam are **BUILT**. The first measured endpoint observation
is also built on Liquidity Monitoring; it is shadow telemetry, not enforcement.
Remaining Phase-0 work is governed pilot binding creation, an evidence-backed
endpoint enforcement decision, `/auth/me` display capabilities, nav/action
gating, and default landings
([§8](#8-enforcement-architecture), [§9](#9-per-persona-dashboards-what-to-build)).
Do not add independent `user_roles`/`user_scopes` tables or implicitly split
legacy `admin` into Org Admin/Owner.

**Phase 1 — org admin console + invites.**
`organizations`/`users` fields, `invitations`, Members table, invite modal, role
assignment, seat limits, suspend/deactivate, `audit_log` (write path + view).
Banks can now onboard their own people by email. **The invite modal offers the
unit presets from §5.1** (Treasury / ALM / Risk / Finance / Credit / Operations
/ Executive / Internal Audit → the §5 persona bundles) so an org admin invites
"the Head of Credit" without composing grants by hand — presets are bundles,
not new roles (§6.1).

_(The `pack:*` / `committee:*` namespace in §7.1 is reserved here but ships
with the committee-pack contribution workflow itself — product.md global
Phase 3 items 5–6. Do not build pack permissions before the pack exists;
personas #17–19 become invitable at that point.)_

**Phase 2 — enterprise auth + bank-safe administration (CONTINUE from the
own-OIDC already built — do not rebuild it, and do not reintroduce a
third-party auth broker).**

Decided 2026-07-20 after the developer-portal evaluation: there is **no separate
bank developer portal** — banks are operators, not API consumers, so the
"portal" is the in-app Administration area (the Kyriba pattern), and a true
developer portal is deferred until a partner actually wants to build against
our API (commercial trigger, not pilot infrastructure).

Foundation that already exists (Phase 1, shipped): `sso_connections` (encrypted
secret, RLS), zero-trust OIDC verification with discovery/`email_verified`/
domain allow-list, Settings → Authentication self-service card, the
`SSO_INTERNAL_KEY` internal config fetch, NextAuth per-request lazy config, the
Data Engine connection-health panel, and `docs/sso-onboarding.md` (Google
Workspace + Entra runbooks — extend per IdP as banks onboard).

Build in this phase:

1. **Multi-connection SSO + home-realm discovery** — drop the one-per-org
   unique, key linked identities on `(connection, subject)`, email-first /login
   (work email → that bank's IdP), `sso_enforced` per org (passwords off).
2. **Verified domains** (DNS-TXT) bounding which connections may claim which
   email domains — closes the cross-org domain-squatting hole the Phase-1
   allow-list only mitigates.
3. **JIT + SCIM 2.0** provisioning/deprovisioning keyed on `external_id`
   (mandatory for bank tenants; JIT never deprovisions).
4. **Session/MFA/step-up policy + token revocation** on role change through the
   built `authorization_version` / `authv` invalidation seam.
5. **Administration area consolidation** — grow Settings + the Data Engine
   screens into the §10.2 org console behind Org Admin/`sso:manage`/`users:*`,
   so bank IT self-serves connections and SSO without AequorOS staff. The
   read-only connection-health panel becomes actionable here (test/rotate/
   disable stay in the integration tabs, permission-gated).
6. **SAML** as a second `sso_connections.protocol` for IdPs where the bank
   refuses OIDC (some on-prem ADFS estates) — additive, same table.

**Phase 3 — platform console + advanced governance.**
Vendor super-admin app (tenants, provisioning, billing, feature flags, global
audit), safe impersonation, generalized approvals across all approvable objects,
SoD monitoring report, custom roles (gated), JIT privilege elevation for the top
roles, break-glass accounts.

---

## 16. Sources

**Bank governance flow (added 2026-08-07 — grounds §5.1)**

- BoG Liquidity Monitoring Tools Directive 2026, Part II ¶10–21 (Board/Senior Management/IAF duties): local copy `docs/Liquidity-Monitoring-Tools-Directive-Cleaned-9.2.26.pdf`
- BoG Liquidity Risk Management Directive 2026 (**obtained 2026-08-07** — local copy `docs/Liquidity-Risk-Management-Directive-Cleaned-11.12.25.pdf`; analysis in `docs/lrmd_gap_analysis.md`; ¶19 CRO, ¶20 ALCO, ¶17 SoD, ¶12 LAS)
- BoG Corporate Governance Directive 2018: https://www.bog.gov.gh/wp-content/uploads/2019/09/CGD-Corporate-Governance-Directive-2018-Final-For-PublicationV1.1.pdf
- Choudhry, _The Principles of Banking_ ch. 9 — the ALCO pack is compiled by the middle office: https://www.oreilly.com/library/view/the-principles-of/9780470827024/chapter09.html

**Treasury roles, SoD, incumbents**

- ACT — Segregation of duties: https://www.treasurers.org/hub/treasurer-magazine/treasury-essentials-segregation-duties
- ACT — Ideal treasury team structure: https://www.treasurers.org/hub/treasurer-magazine/is-there-an-ideal-structure-for-treasury-teams
- ACT Wiki — Segregation of duties: https://wiki.treasurers.org/wiki/Segregation_of_duties
- Three Lines of Defence — Risk.net: https://www.risk.net/definition/three-lines-of-defence-3lod
- Baker Tilly — Three Lines model for banks: https://www.bakertilly.com/insights/three-lines-model-risk-management-for-banks
- SafePaaS — Access governance for Kyriba: https://www.safepaas.com/access-governance-for-kyriba/
- Murex — Security (fine-grained entitlements, four-eyes): https://www.murex.com/en/solutions/technology/security
- Nasdaq/AxiomSL ControllerView: https://www.axiomsl.com/platform/controllerview/
- Wolters Kluwer OneSumX (Finance/Risk/Reg Reporting): https://www.wolterskluwer.com/en/solutions/onesumx-for-finance-risk-and-regulatory-reporting
- Bloomberg TOMS (user/desk/asset-class/region/firm scoping): https://professional.bloomberg.com/products/trading/order-management-system/toms/
- Regnology — Bank of Ghana ORASS case study: https://www.regnology.net/en/resources/insights/integrated-financial-supervision-system-supports-bank-of-ghana-reforms/
- Bank of Ghana — ORASS Portal: https://orassportal.bog.gov.gh/
- RegReportingDesk — COREP sign-off accountability: https://regreportingdesk.com/corep-reporting-explained/

**RBAC/ABAC, product role models, lifecycle, security, UI**

- IBM — RBAC implementation: https://www.ibm.com/think/topics/role-based-access-control-implementation
- DEV — RBAC vs ABAC vs ReBAC (role explosion): https://dev.to/kanywst/rbac-vs-abac-vs-rebac-how-to-choose-and-implement-access-control-models-3i2d
- Cerbos — 3 authorization designs for SaaS: https://www.cerbos.dev/blog/3-most-common-authorization-designs-for-saas-products
- Snowflake — Access control overview & considerations: https://docs.snowflake.com/en/user-guide/security-access-control-overview · https://docs.snowflake.com/en/user-guide/security-access-control-considerations
- AWS IAM — managed vs inline policies: https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies-choosing-managed-or-inline.html
- Okta — standard & custom admin roles: https://help.okta.com/en-us/content/topics/security/administrators-admin-comparison.htm
- Microsoft Entra — role best practices & PIM: https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/best-practices · https://learn.microsoft.com/en-us/entra/id-governance/privileged-identity-management/pim-configure
- Google Workspace — prebuilt admin roles: https://knowledge.workspace.google.com/admin/users/prebuilt-administrator-roles
- Stripe — user roles: https://docs.stripe.com/get-started/account/teams/roles
- Datadog — RBAC / permissions: https://docs.datadoghq.com/account_management/rbac/permissions/
- GitHub — org roles: https://docs.github.com/en/organizations/managing-peoples-access-to-your-organization-with-roles/roles-in-an-organization
- GitLab — roles/permissions (hierarchy): https://docs.gitlab.com/user/permissions/
- Auth0 — multiple-organization architecture: https://auth0.com/docs/get-started/architecture-scenarios/multiple-organization-architecture
- OWASP — Multi-Tenant Security cheat sheet: https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html
- Pigment — safe user impersonation: https://engineering.pigment.com/2026/04/08/safe-user-impersonation/
- Authgear — SCIM provisioning: https://www.authgear.com/post/what-is-scim-provisioning/
- Clerk — SCIM vs JIT; verified domains: https://clerk.com/articles/scim-vs-jit-provisioning-when-to-use-each · https://clerk.com/docs/guides/organizations/add-members/verified-domains
- WorkOS — model your B2B SaaS with organizations: https://workos.com/blog/model-your-b2b-saas-with-organizations
- Security Boulevard — step-up auth in OIDC: https://securityboulevard.com/2026/05/step-up-authentication-when-to-require-it-and-how-to-implement-it-in-oidc/
- Britive — break-glass account management: https://www.britive.com/resource/blog/break-glass-account-management-best-practices
- hoop.dev — immutable audit logs: https://hoop.dev/blog/immutable-audit-logs-the-foundation-of-saas-governance
- Eleken — bulk-action UX: https://www.eleken.co/blog-posts/bulk-actions-ux
- NIST SP 800-162 (ABAC) & NIST RBAC — https://csrc.nist.gov/pubs/sp/800/162/final

---

_This spec is intentionally incremental: Phase 0 makes the dashboards
role-aware on the existing auth layer; later phases add the org console, SSO/SCIM,
and the platform plane. Build in that order._
