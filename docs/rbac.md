# AequorOS — RBAC, User Management & Role-Aware Dashboards

**Status:** implementation spec · **Audience:** dashboard + platform engineers · **Owner:** Eric

This document specifies how AequorOS grants access to bank users, what each user
type can see and do, how the three settings surfaces (personal / org-admin /
vendor-platform) are structured, and how banks invite and onboard their people.
It is written to be **built incrementally on the auth layer that already exists**
(JWT sessions, `organization_id` RLS, the `admin > approver > analyst > viewer`
roles, and the regulatory-reporting maker-checker trail).

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
- Need the **data model / API** → [§13](#13-data-model), [§14](#14-api-surface).
- Sequencing the work → [§15 roadmap](#15-phased-roadmap).

Terminology: **tenant = organization = one bank**. **Maker** = the person who
creates/edits/runs. **Checker** = the independent person who reviews/approves.
Module shorthand: **LIQ** (Liquidity), **CAP** (Basel Capital), **IRRBB**, **FX**,
**FTP**, **FCST** (Forecasting), **BEH** (Behavioral), **DATA** (Data Engine),
**REG** (Regulatory Reporting).

---

## 2. Current state → target

### What already exists (build on this — do not rebuild)

| Area | Current state | Where |
|---|---|---|
| Auth | Zero-trust JWT (HS256), Argon2id passwords, Auth0 SSO, refresh rotation | `app/core/security.py`, `dashboard/auth.ts` |
| Roles | `admin > approver > analyst > viewer` (linear rank), single role per user | `app/core/security.py:ROLES`, `app/models/user.py:USER_ROLES` |
| Enforcement | Only the **viewer↔analyst write boundary** is wired (`get_mutation_tenant_context` requires `analyst`+). `require_role(minimum)` factory exists but gates nothing else. | `app/api/deps.py` |
| Tenancy | Postgres RLS forced on `app.organization_id`; cross-tenant work runs on the BYPASSRLS `WORKER_DATABASE_URL` role | `app/db/session.py`, CLAUDE.md |
| Maker-checker | **Regulatory reporting already has it**: `draft→generated→validated→pending_approval→approved→submitted→acknowledged→…` with an append-only approval trail where **checker ≠ maker is enforced in the service** | `app/models/regulatory_reporting.py` (`PACKAGE_STATUSES`, `APPROVAL_ACTIONS`, `RegulatoryPackageApproval`) |
| Token claims | `sub`, `org`, `roles[]`, `email`, `name` | `app/core/security.py:create_token` |
| Identity in UI | Header + settings read the real session (name/role); route gate redirects unauthenticated → `/login` | `dashboard/components/shell/Header.tsx`, `dashboard/middleware.ts` |

### The gap this spec closes

1. `approver` and `admin` roles exist but **enforce nothing distinct** — no approval gate, no admin gate.
2. **No user management**: no invitations, no user CRUD, no role assignment UI, no org-admin console, no vendor platform console.
3. **`Organization` is bare** (`{id, name}`) — no domain, plan, SSO config, or settings.
4. **One flat role per user**, no module/entity/desk scoping — a Liquidity Manager and a CFO get the same surface.
5. **No SoD engine** beyond the one hand-rolled REG check; no generalized maker-checker on calculation/official runs.
6. **No audit log**, no SSO/SCIM self-service, no impersonation, no seat/plan concept.

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
  Treasurers). Front office *executes* deals; middle office *monitors limits and
  exposures*; back office *confirms, settles, reconciles*. "The front office does
  the deal but doesn't settle the money; the back office settles the money but
  doesn't do the deal."
- **Three Lines model** (IIA; codified for banking by BCBS). **1st line** =
  business owns/runs risk (can *initiate/configure*); **2nd line** = Risk &
  Compliance set limits and *review/challenge/approve*; **3rd line** = Internal
  Audit gets *independent read + audit trail*, changes nothing.

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
   exceeded, step-up-MFA present. These are *conditions*, not roles — encoding
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
                       │   admin.aequoros.com — CROSS-TENANT         │
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
- **Platform plane** — the *only* cross-tenant surface, for AequorOS staff. It
  must run outside RLS — **the same architectural seam as the existing
  `WORKER_DATABASE_URL` BYPASSRLS worker** — or it reads empty. It is the most
  heavily audited surface in the system.

---

## 5. Personas → roles → what they need

This is the answer to "who gets access, at what level, and for what." Posture:
**I** = initiates/creates/runs (maker), **A** = reviews/approves/signs-off
(checker), **V** = view-only.

| # | Persona (bank job) | Line | What they do & need access for | Modules | Posture | AequorOS role preset (§6) |
|---|---|---|---|---|---|---|
| 1 | **Group / Head Treasurer** | 1 | Owns funding & liquidity strategy; sets desk mandates; approves large exceptions; final treasury sign-off | LIQ, FCST, FTP, IRRBB, FX (oversight), CAP (V) | A + V | Approver (LIQ/FCST/FTP/IRRBB/FX), high approval tier |
| 2 | **ALM / Balance-sheet Manager** | 1 | Runs forecasts & scenarios; structural IRR & liquidity gap; curates behavioral assumptions; ALCO packs | FCST, IRRBB, LIQ, BEH, FTP | I | Analyst (FCST/IRRBB/LIQ/BEH) |
| 3 | **Liquidity Manager** | 1 | Daily cash/liquidity position; LCR/NSFR monitoring & drivers; HQLA; survival horizon | LIQ, FCST (V), DATA (V) | I | Analyst (LIQ) |
| 4 | **Money-Market / FX Dealer** | 1 | Executes MM/FX within mandate; deal entry; manages open positions | FX, LIQ (funding) | I (deal only) | Analyst (FX), desk-scoped; **never** settlement approve |
| 5 | **Market & FX Risk Officer** | 2 | Independent limit/VaR monitoring; challenges positions; maintains limits; breach escalation | FX, IRRBB (challenge), CAP (mkt RWA) | A + V + configure(limits) | Approver (FX) + Risk config |
| 6 | **IRRBB Analyst** | 1/2 | EVE/NII, repricing gap; NMD/prepayment assumptions; IRRBB return prep | IRRBB, BEH, FCST, REG (IRRBB templates) | I | Analyst (IRRBB/BEH) |
| 7 | **FTP / Funding Owner** | 1 | Designs & maintains FTP curves/methodology; publishes transfer rates | FTP, LIQ, FCST | I + configure | Analyst (FTP) + FTP config |
| 8 | **Back-office / Settlements / Ops** | 1 (segregated) | Confirms & settles deals; payment approval/release; reconciliation; standing data | DATA, FX/LIQ post-trade | I + A (approve **xor** release) | Analyst (DATA) + Approver (settlements), split per user |
| 9 | **Financial Control / Finance** | 1/2 | GL reconciliations; source-to-report mapping; reconcile engine↔accounting; data-quality sign-off | DATA (map), REG (prep), CAP, FCST | I + review + export | Analyst (DATA/REG) + export |
| 10 | **CFO** | Exec | Owns finance/reg numbers; **attests/signs off returns before submission**; approves capital & funding plans | REG, CAP, FCST, LIQ | A / sign-off | Approver + `reg:sign_off`, top approval tier |
| 11 | **CRO / Head of Risk** | 2 | Owns risk appetite & limits; independent review of all risk; **approves models & assumptions** | all risk modules, BEH, REG (risk returns) | A / challenge + configure | Approver (all) + `beh:approve` + Risk config |
| 12 | **Regulatory Reporting Officer** | 2 | Assembles BoG returns; runs validation rules; reconciles template↔source; **submits to ORASS**; owns supervisor relationship | REG, DATA (V), all outputs (consume) | I + submit | Analyst (REG) + `reg:submit` |
| 13 | **Internal Audit** | 3 | Independent assurance over controls, models, SoD, lineage; reads all, changes nothing | ALL (read + **audit log**) | V-only + audit | Auditor |
| 14 | **Compliance** | 2 | Obligation mapping; policy adherence; verifies SoD/four-eyes are configured & operating | REG, DATA (policy), config-audit | V + policy config | Auditor + policy config |
| 15 | **Board / Exec (read-only)** | Gov | Consume ALCO/board dashboards; risk appetite vs actuals; no operational access | aggregated dashboards | V-only (published) | Viewer (published views) |
| — | **Org Admin** | — | Manages the bank's users, roles, SSO/SCIM, org settings, audit — **no operational approve/run** | Settings only | admin | Org Admin |
| — | **Org Owner** | — | The bank's account owner: Org Admin + billing + ownership transfer | Settings + billing | admin+ | Org Owner |

**Reading it for dashboards:** persona → role preset → [§9](#9-per-persona-dashboards-what-to-build) tells you the landing page, visible nav, and allowed actions to render.

---

## 6. Role catalog (predefined tenant roles)

Ship a **small** predefined set. Personas are **presets** = `(base role) + (module
grants) + (approval tier)` applied at invite time — not new roles. This is the
Stripe/Datadog/Okta pattern (predefined roles + optional custom roles later),
deliberately capped to avoid role explosion.

### 6.1 Base roles (extend the current four)

| Base role | Replaces / maps from | Purpose | Key permissions | Must NOT |
|---|---|---|---|---|
| **Viewer** | `viewer` | Read-only within scope | `*:view` (scoped) | any mutation |
| **Auditor** | *(new; a Viewer variant)* | Read-only **+ audit-log read**, whole tenant | `*:view`, `audit:read` | any mutation, any approve |
| **Analyst** (Preparer / maker) | `analyst` | Core treasury/ALM work | `{module}:view|create|edit|run`, `export` (scoped) | approve/sign-off/submit **their own** object |
| **Approver** (Reviewer / checker) | `approver` | Four-eyes approval | `{module}:review|approve`, `reg:sign_off`, `reg:submit` (scoped) | edit the object they are approving |
| **Org Admin** | *(split out of `admin`)* | Account administration only | `users:*`, `roles:*`, `sso:*`, `scim:*`, `org:settings`, `audit:read` | operational `run/approve/submit` (SoD C9) |
| **Org Owner** | *(top of `admin`)* | Bank's account owner | Org Admin **+** `billing:*`, `org:transfer`, `org:delete` | cross-tenant anything |
| **Billing Manager** | *(new, optional)* | Subscription & seats | `billing:*` | domain data |

**Migration note:** today's single `admin` conflates account-admin with
operational-super — a segregation-of-duties smell (Snowflake's rule: never mix
account-management privileges with entity privileges in one role). Split it:
existing `admin` users become **Org Admin**; designate one **Org Owner** per
tenant. Keep the `admin>approver>analyst>viewer` rank in `security.py` for
backward compatibility during migration, but move real decisions to the
permission check in [§7](#7-permission-model).

### 6.2 A user can hold more than one role

The token already carries `roles[]` (a list). Use it: a person can be **Analyst
on LIQ and Approver on REG** simultaneously — effective permissions are the
union, and **maker≠checker is enforced per object at action time** (§7.4), not by
forbidding the combination. This is exactly how the existing
`RegulatoryPackageApproval` works ("checker ≠ maker enforced in service") —
generalize that mechanism.

### 6.3 Custom roles — later, gated

Do **not** ship a custom-role builder in v1. When a bank demonstrably can't be
expressed by presets, add a **clone-a-role → toggle permissions** editor
(Datadog pattern) with **sensitive permissions visibly flagged**, and cap the
count (Okta caps at 100/org) to prevent proliferation.

---

## 7. Permission model

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

**REG-specific extras** (the filing chain):

```
reg:validate           run BoG validation rules + reconcile template↔source
reg:sign_off           formal attestation gate (CFO / CRO / Head of Reg)
reg:submit             transmit a signed return to BoG / ORASS
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

| Permission | Viewer | Auditor | Analyst | Approver | Org Admin | Org Owner |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| `{m}:view` | ● | ● | ● | ● | ●¹ | ●¹ |
| `{m}:create` / `edit` | — | — | ● | — | — | — |
| `{m}:run` | — | — | ● | — | — | — |
| `{m}:review` / `approve` | — | — | — | ● | — | — |
| `{m}:configure` | — | — | ●² | ●² | — | — |
| `{m}:export` | —³ | ● | ● | ● | — | — |
| `reg:validate` | — | — | ● | ● | — | — |
| `reg:sign_off` | — | — | — | ●⁴ | — | — |
| `reg:submit` | — | — | ●⁴ | ●⁴ | — | — |
| `audit:read` | — | ● | — | — | ● | ● |
| `users:* / roles:* / sso:* / scim:*` | — | — | — | — | ● | ● |
| `org:settings` | — | — | — | — | ● | ● |
| `billing:* / org:transfer / org:delete` | — | — | — | — | — | ● |

¹ Org Admin/Owner see dashboards for administration context but hold no operational write.
² `configure` is granted per-preset (FTP owner, ALM assumptions, Risk limits) — not to every Analyst/Approver.
³ Board/Exec "Viewer" gets published-dashboard view only; raw `export` is off by default.
⁴ `sign_off` / `submit` are **preset add-ons** (CFO, Head of Reg), not blanket to every Approver — and are **SoD-gated** (§7.4).

### 7.3 Scoping dimensions

Every grant is evaluated within a scope. Default-deny outside it.

| Scope | Meaning | Enforcement |
|---|---|---|
| **Tenant** | the bank | already: RLS on `organization_id` |
| **Legal entity** | subsidiary within a banking group | `user_scopes.entity_id[]`; filter queries |
| **Module** | LIQ/CAP/… | encoded in which `{module}:*` perms the role holds |
| **Desk / portfolio / currency** | a dealer acts only on their book (Bloomberg TOMS precedent: user/desk/asset-class/region/firm) | `user_scopes.desk[]` |
| **Data sensitivity** | customer-level BEH inputs vs aggregated outputs | gate raw `view`/`export` separately from dashboard `view` |
| **Environment** | live vs **demo** | condition, not role — block writes in demo |
| **Approval tier** | numeric ceiling on `approve` (deal size / exception magnitude); above → escalate | attach to the `approve` grant per preset |

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

| # | Deny both to one identity within the same scope | Why |
|---|---|---|
| C1 | deal entry (FX) **&** deal confirm/settle | front ≠ back office |
| C2 | payment/settlement **approve** & **release** | two-stage even inside back office |
| C3 | reconciliation & payment approval | conceal-your-own-error risk |
| C4 | DATA ingest/map/activate **&** sign-off/submit of the return built on it | producer ≠ approver of numbers |
| C5 | configure scenario/BEH assumptions **&** approve the run that consumes them | assumption-setter can't self-bless |
| C6 | run an engine calc **&** reg sign-off/submit of that result | run ≠ approve ≠ submit |
| C7 | BEH model owner **&** model validator **&** audit | Three-Lines independence |
| C9 | user/role administration **&** operational approve rights on same object | admin can't grant themselves approvals |
| C10 | reg-return preparer **&** internal sign-off **&** submitter | prepare / attest / submit split |

Ship SoD **monitoring/reporting**, not just assignment-time blocks — the Kyriba
lesson is that role assignment alone is insufficient; auditors want a report of
who *could* violate SoD and who *did*.

---

## 8. Enforcement architecture

### 8.1 Backend (extend `app/api/deps.py`)

The plumbing is 80% there. Add:

```python
# app/api/deps.py  (sketch)

def require_permission(perm: str, *, scope: ScopeSpec | None = None):
    """FastAPI dependency: 403 unless the principal holds `perm` within `scope`."""
    def _dep(ctx: Annotated[TenantContext, Depends(get_current_principal)]) -> TenantContext:
        if not authz.has_permission(ctx, perm, scope):
            raise HTTPException(403, f"Requires '{perm}'.")
        return ctx
    return _dep

def require_maker_checker(object_type: str):
    """For approve/sign_off/submit endpoints: 409 if actor is a prior maker."""
    ...
```

- Resolve a principal's **effective permissions** from `roles[] + user_scopes`
  via a static `ROLE_PERMISSIONS` map (mostly constant) — cache per (org,user)
  with a short TTL (reuse the tenant-validation cache seam) so it's not a
  per-request DB hit.
- Keep `get_mutation_tenant_context` as the coarse "is a writer" gate; layer
  `require_permission("liq:run", …)` on the specific endpoints.
- **Environment condition:** the existing demo-mode write block becomes a
  condition inside `has_permission` (writes denied when `env == demo`).
- **Reason + audit:** every `run/approve/sign_off/submit/configure/admin` action
  writes an immutable audit row (§13) with the already-required non-empty reason.
- **Token/session revocation on role change:** on role/scope/deactivation change,
  bump a per-user `session_epoch`; reject tokens with a stale epoch (don't wait
  for the 15-min expiry).

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
- **Session claims:** extend the JWT/session to carry `roles[]` + a compact
  `perms`/`scopes` summary (or fetch `/auth/me` once and cache) so the UI can
  gate without a call per button. Refresh on role change (§8.1 epoch).

### 8.3 Default landing per role

Send each user to where their job starts, not always Command Center:

| Role preset | Default landing |
|---|---|
| Treasurer / ALM / CFO / CRO / Board | `/` Command Center (their role-lens) |
| Liquidity Manager | `/liquidity` |
| IRRBB Analyst | `/irr` |
| FX Dealer / FX Risk | `/fx` |
| FTP Owner | `/ftp` |
| Finance / Reg Reporting Officer | `/submissions` (Regulatory Reporting) |
| Data/Ops | `/data-engine` |
| Auditor / Compliance | `/reports` + Audit log |
| Org Admin / Owner | `/settings` (Org console) |

---

## 9. Per-persona dashboards (what to build)

For each role preset the dev renders: **default landing**, **visible nav**,
**allowed actions**, **hidden/disabled**. Use the same underlying pages —
role-gate the surface, don't fork the app.

| Role preset | Visible nav (modules) | Allowed actions | Hidden / disabled |
|---|---|---|---|
| **Treasurer** | Command Center, LIQ, FCST, FTP, IRRBB, FX, CAP(view), Risk, Alerts, Reports | approve runs/exceptions, view all, mint official runs (with tier), export | Data Engine writes, Settings admin, reg submit |
| **ALM Manager** | Command Center, FCST, IRRBB, LIQ, BEH, FTP, Markets, Positions | create/edit scenarios, run calcs, configure assumptions | approve own runs, reg sign-off, Settings admin |
| **Liquidity Manager** | Command Center, LIQ, FCST(view), Alerts, DATA(view) | run LIQ, monitor, view | other-module writes, approve, Settings |
| **FX Dealer** | FX (own desk), Markets, Positions, LIQ(funding) | deal entry on own desk/ccy | settlement approve/release, other desks, other modules |
| **FX / Market Risk Officer** | FX, IRRBB(challenge), CAP(mkt), Risk, Alerts | review/approve FX limits, configure limits, view | deal entry (SoD), reg submit |
| **IRRBB Analyst** | IRRBB, BEH, FCST, REG(IRRBB templates) | run IRRBB, set assumptions, prep IRRBB return | approve own, submit, Settings |
| **FTP Owner** | FTP, LIQ, FCST | configure FTP curves, run, approve own methodology | other-module writes, reg submit |
| **Back-office / Ops** | DATA, FX/LIQ post-trade, Alerts | confirm/settle, approve **xor** release (per user) | deal entry, both approve+release |
| **Finance / Control** | DATA(map), REG, CAP, FCST, Reports | map data, prep returns, reconcile, export | reg sign-off (unless CFO), approve own |
| **CFO** | Command Center(CFO lens), REG, CAP, FCST, LIQ, Reports | **sign off / attest** returns & plans, approve, view | data entry, deal entry |
| **CRO / Head of Risk** | all risk modules, BEH, REG(risk), Risk & Limits, Alerts | approve/challenge, approve models, configure risk appetite/limits | run own calcs then self-approve |
| **Reg Reporting Officer** | REG, DATA(view), all outputs(view), Reports | prep + validate + **submit to ORASS** (post sign-off) | edit source financials, self-sign-off |
| **Auditor** | ALL (read) + **Audit log** | view everything, export evidence | every mutation, every approve |
| **Compliance** | REG, DATA(policy), Reports, Audit(read) | configure policy/SoD rules, view | operational writes |
| **Board / Exec (Viewer)** | Command Center (published), module dashboards (aggregated) | view published views only | raw data, export, drill-to-source |
| **Org Admin / Owner** | **Settings / Org console** (+ read dashboards) | manage users/roles/SSO/SCIM/audit (+ billing = Owner) | run/approve/submit anything |

The existing **role-lens tabs** on Command Center (Treasurer / ALM / Risk / CFO)
are the right pattern — extend them to *default and lock* to the user's role
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
│   ├─ Single sign-on     self-service SAML/OIDC per org
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

### 10.3 Platform / vendor super-admin console (`admin.aequoros.com`, staff-only)

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

### 11.3 SSO (SAML / OIDC) — per organization

Each bank brings its own IdP (Okta, Entra, ADFS, Ping). SSO is configured **per
org, self-service** by the Org Admin — Auth0 is already wired at the app level;
extend to per-org connections. SSO decides *who may sign in*.

### 11.4 JIT + SCIM + verified domains

- **JIT** — create the account on first SAML/OIDC login from assertion attributes (name, email, group→role). Convenience only; **JIT does not deprovision** → orphaned accounts if used alone.
- **SCIM 2.0** — the IdP syncs create/update/**deactivate** to AequorOS. **Mandatory for bank tenants** — SCIM-driven deprovisioning is the single most-probed enterprise security-questionnaire item. Key on a **stable IdP id (`externalId`/`sub`), never email**, or JIT+SCIM produce duplicate records.
- **Verified domains** — a tenant verifies a domain via DNS TXT; then auto-suggest membership and/or **enforce SSO** for all users on that domain (domain capture stops shadow personal accounts).
- **Rule:** JIT creates, SCIM governs, SSO authenticates, verified domains bound the population — all keyed on one stable identifier.

### 11.5 Offboarding (deprovision order)

SSO cutoff alone does **not** kill live sessions or app-native entitlements. In order:
1. Disable account + **terminate all active sessions** (bump `session_epoch`).
2. **Revoke API keys, refresh/OAuth tokens, connected-app grants.**
3. Remove app-native roles/scopes.
4. **Transfer ownership** of cases/scenarios/reports/connections to a named custodian *before* deactivating.
5. Rotate any shared secrets held.
6. **Post-offboarding audit 24–72 h later** to confirm all paths closed.

### 11.6 Impersonation (platform support) — done right

For the platform console's "view as" (Pigment's reference model):

- **New JWT per session** with the impersonated user's identity **plus a separate
  `impersonator` claim** for attribution, a **read-only flag**, and a **≤30-min
  expiry**. Keep the admin's own token separately for instant exit.
- **Read-only by default, enforced in middleware** (mutations → 403 before
  business logic). Impersonation is for *observing*, not acting.
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
`activated_at`, `deactivated_at`, `session_epoch (int)`, `mfa_enrolled (bool)`.
Keep `role` for back-compat but treat `user_roles` as the source of truth.

**`user_roles`** *(new — many-to-many, replaces single `role`)*:
`user_id, org_id, role (base role), preset (nullable), granted_by, granted_at`.

**`user_scopes`** *(new)*:
`user_id, org_id, entity_id[] , desk[] , module[] , approval_tier (int), environment`.

**`invitations`** *(new)*:
`id, org_id, email, role_presets[], scope, token_hash, invited_by, expires_at,
status (pending|accepted|expired|revoked), accepted_at`. Store **only the hash**.

**`sso_connections`** *(new)*: `org_id, protocol (saml|oidc), idp_metadata,
domains[], enforced (bool), jit_enabled, scim_token_hash, status`.

**`approvals`** *(new — generalize `RegulatoryPackageApproval`)*:
`id, org_id, object_type, object_id, action (requested|reviewed|approved|rejected|signed_off|submitted),
actor_user_id, reason, occurred_at`. Append-only; **checker ≠ prior maker enforced in the service**.

**`audit_log`** *(new — append-only, tamper-evident)*:
`id, org_id (nullable for platform), actor_user_id, impersonator_user_id (nullable),
action, resource_type, resource_id, reason, ip, session_id, result, occurred_at`.
Not editable/deletable via the app; consider hash-chaining + WORM storage
(S3 Object Lock). Covers logins, role/permission changes, every
create/edit/delete/**export**, approvals, submissions, impersonation, SSO/SCIM
changes, and every financial mutation (with its reason).

**`platform_staff`** + **`platform_staff_roles`** *(new)*: vendor employees
(super-admin / support / billing / read-only), outside RLS.

**`impersonation_sessions`** *(new)*: `id, impersonator_user_id, target_user_id,
org_id, reason, ticket_ref, read_only, started_at, expires_at, ended_at`.

### RLS reminder
Everything tenant-scoped keeps RLS on `organization_id`. `platform_staff`,
`impersonation_sessions`, and cross-tenant reads live on the **BYPASSRLS** path
(same seam as `WORKER_DATABASE_URL`). The platform console is the *only*
cross-tenant surface.

---

## 14. API surface

Tenant plane (under `/api/v1`, RLS-scoped, admin-gated where noted):

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
GET/PUT /orgs/{org}/sso                             sso:manage
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

Platform plane (`admin.aequoros.com`, outside RLS, `platform:*`):

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

**Phase 0 — role plumbing (unblocks role-aware dashboards).**
`ROLE_PERMISSIONS` map + `require_permission` + `/auth/me` returning effective
perms; split `admin`→ Org Admin/Owner; nav filtering + action gating + default
landings ([§8](#8-enforcement-architecture), [§9](#9-per-persona-dashboards-what-to-build)). No new tables except `user_roles`/`user_scopes`.

**Phase 1 — org admin console + invites.**
`organizations`/`users` fields, `invitations`, Members table, invite modal, role
assignment, seat limits, suspend/deactivate, `audit_log` (write path + view).
Banks can now onboard their own people by email.

**Phase 2 — enterprise auth.**
Per-org SSO (SAML/OIDC) self-service, verified domains, JIT, SCIM
provisioning/deprovisioning, session/MFA/step-up policy, token revocation on
role change.

**Phase 3 — platform console + advanced governance.**
Vendor super-admin app (tenants, provisioning, billing, feature flags, global
audit), safe impersonation, generalized approvals across all approvable objects,
SoD monitoring report, custom roles (gated), JIT privilege elevation for the top
roles, break-glass accounts.

---

## 16. Sources

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

*This spec is intentionally incremental: Phase 0 makes the dashboards
role-aware on the existing auth layer; later phases add the org console, SSO/SCIM,
and the platform plane. Build in that order.*
