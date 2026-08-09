# AequorOS Internal Staff Operations Console: Architecture & Recommendation

## TL;DR
- **Build the staff console as a SEPARATE application (its own frontend, its own login, its own workforce identity provider) that talks to shared backend services — this is the "control plane" pattern AWS, Stripe, and virtually every serious multi-tenant SaaS follows.** Do NOT put AequorOS employees into the same identity flow or the same "operator tenant" as your bank customers; the blast-radius and audit-separation risks are unacceptable for a regulated treasury/ALM platform.
- **The market-data desk is genuinely different and is your core IP.** Model curated market data and curves as GLOBAL/SHARED reference data (published by AequorOS via a maker-checker, effective-dated, versioned workflow) that is read-only to tenants, sitting alongside tenant-private spread overlays. This mirrors exactly how Bloomberg BVAL, S&P Global Totem, ICE/LSEG benchmark administration, and GoldenSource/NeoXam golden-copy MDM operate: analysts input/validate/publish centrally, customers consume.
- **For two founders: buy the generic admin (Retool/Django Admin/Forest Admin for onboarding, support, billing CRUD), build the market-data desk and curve construction tooling custom.** The minimum viable console is a thin operator app with impersonation + a publish workflow; it evolves toward a hardened internal control plane as tenant count and compliance load grow.

## Key Findings

1. **The industry-standard answer is a separate "control plane," not a super-admin role in the customer app.** AWS's SaaS Architecture Fundamentals explicitly decomposes every multi-tenant system into a *control plane* (onboarding, authentication, management, operations, analytics, billing) and an *application plane* (the tenant-facing product), and states the control plane "is not actually multi-tenant" and includes "a separate administration application" used by the SaaS provider to manage the environment. This is the canonical framing for your decision.

2. **Real vendors run internal staff tooling as separate applications.** Stripe operates "Stripe Admin," a distinct internal platform relied on by Stripe employees globally, with its own dedicated "Admin Platform" engineering org, deployed separately from the customer Dashboard. This is the reference implementation of the pattern.

3. **Employee identity and customer identity should be separate systems.** The industry now cleanly distinguishes Workforce identity (employees/contractors — optimized for control, provisioning/deprovisioning, MFA, audit) from Customer identity/CIAM (external users — optimized for scale, SSO federation). Since Okta acquired Auth0 in May 2021 for roughly $6.5 billion, it has run "two complementary product lines — Customer Identity Cloud and Workforce Identity Cloud" — precisely because the requirements and threat models differ. AequorOS staff belong in workforce identity; banks authenticate via their own per-bank OIDC SSO into the customer plane.

4. **Vendor-operated-data-that-flows-to-customers is a well-established architecture.** Bloomberg BVAL (independent evaluated pricing for over 2.7 million securities across all asset classes, delivered daily), S&P Global Totem (market makers contribute; clients consume consensus), ICE Benchmark Administration (panel banks submit; IBA calculates and publishes on its own schedule), and LSEG/Refinitiv (calculate → publish → distribute) all run an internal input/validate/publish back office that is architecturally distinct from the customer distribution layer. This is your market-data desk pattern, and it is normal.

5. **The global-shared + tenant-private data split is a solved database pattern.** Shared read-only reference data lives in shared tables/containers; tenant-writable data lives in per-tenant isolated storage; "mixed/split" tables carry both. Publishing uses golden-copy master-data workflows with validation, lineage, effective-dating (bitemporal: valid-time + transaction-time), and maker-checker approval.

6. **Support impersonation into a tenant is essential but dangerous, and there is a known-safe recipe:** consent or break-glass, time-boxed sessions (15–60 min), a distinct impersonation session with both identities preserved (delegation token with `act` claim), a persistent visual banner, immutable and separate audit logs, tiered approval for sensitive accounts.

## Details

### 1. The core decision: separate app vs shared app with roles

**Recommendation: a separate internal application (option a/c hybrid) — separate frontend and separate auth, sharing backend services and the database through a well-defined internal API.** Reject option (b), the "super-admin role inside the customer app," for a regulated financial platform.

The authoritative framing comes from AWS's SaaS Architecture Fundamentals: every multi-tenant SaaS decomposes into a **control plane** and an **application plane**. The control plane handles "onboard, authenticate, manage, operate, and analyze," is "foundational to any multi-tenant SaaS model," is explicitly "not actually multi-tenant," and includes "a separate administration application" for the provider. Your staff console IS the control plane. AWS guidance elsewhere reinforces the isolation rationale: "the control plane is deliberately separate from the request path so that onboarding logic, which runs with powerful provisioning permissions, is never reachable from tenant-facing code."

Why not option (b), elevated roles inside the same app:
- **Blast radius.** If internal "operator" super-admin capability lives in the same codebase, same auth flow, and same deployment as the customer product, then any vulnerability in the customer-facing surface (XSS, an authz bug, a leaked session) becomes a potential path to cross-tenant god-mode. One of the most-cited multi-tenant failure modes is exactly this: "Missing tenant scoping in admin tools: internal dashboards accidentally bypass tenant checks." You want the powerful capability physically separated from the internet-facing attack surface.
- **Auth separation / identity provider.** Customer auth (per-bank OIDC SSO, CIAM concerns: federation, many external identities) and employee auth (workforce identity: SCIM provisioning/deprovisioning on termination, enforced MFA, hardware keys, IP allowlisting, tight audit) have genuinely different threat models and lifecycles. Putting AequorOS staff into the customer IdP — or worse, modeling them as an "operator tenant" inside the same identity system — couples your employee security to your customer login system and makes "has any AequorOS employee accessed our data?" much harder to answer cleanly. The Okta WIC-vs-CIC split exists for this reason.
- **The "operator tenant" anti-pattern specifically.** Making internal staff just another tenant with elevated cross-tenant entitlements is seductive because it reuses your RLS machinery, but it fundamentally breaks the invariant that "one tenant should never see another tenant's data." The whole security model of your platform rests on that invariant; the moment one tenant is *designed* to read across all others, RLS is no longer a hard boundary and every cross-tenant read is now an application-logic decision that can be bugged. Cross-tenant access should be an explicit, separate, heavily-audited service — not a role bit on a tenant.
- **Independent deploy / availability.** A separate operator app can be deployed, rate-limited, network-restricted (VPN/zero-trust, IP allowlist, mTLS) and even taken down independently of the customer product.

Why not fully separate stacks (pure option a) either: you do NOT want a duplicated backend or a second copy of business logic. The correct shape is **separate frontend + separate authentication + a Backend-for-Frontend (BFF) for the operator app, calling the SAME underlying domain/services and database** that the customer plane uses. Shared backend = one source of truth for market data, curves, entitlements; separate frontend/auth/BFF = isolation and least privilege. This is the "hybrid (c)" in your question and it is the right answer.

**Concrete shape for AequorOS:**
- `console.aequoros.internal` (or behind VPN/zero-trust) — the staff app. Workforce IdP (Okta/Entra/Google Workspace), enforced MFA, hardware keys for publish/break-glass actions.
- An **operator BFF** service with its own service identity and scoped, audited permissions to the core services. All privileged operations (publish curve, provision tenant, impersonate) go through this BFF, which is the single choke point for authorization and audit.
- The customer app (`bank.aequoros.com` — renamed from `app.` 2026-08-03; subdomains are product segments per CLAUDE.md) keeps per-bank OIDC SSO and never contains operator UI or operator endpoints. A `console.` (or `ops.`) subdomain fits the established segment scheme; note the Coolify deploy rules that bind any new app (no `${}` interpolation in compose files, no repo bind-mounts — config lives inside the compose).

### 2. How financial / market-data vendors do it

The reassuring finding: **AequorOS's market-data desk is a textbook instance of how the entire market-data industry is built.** In every case there is an internal input/validate/publish back office operated by vendor staff, distinct from the customer distribution channel.

- **Bloomberg BVAL** supplies "independent and transparent evaluated pricing daily for over 2.7 million securities for all asset classes." A 2024 SEC order (as summarized by law firm Norris McLaughlin) described BVAL as providing "daily price valuations for more than 2.5 million securities across all asset classes to more than 1,300 financial institution customers," with each price carrying a "BVAL Score, a proprietary measure showing the relative amount and consistency of market data used to generate each evaluated price." Bloomberg runs an evaluator team clients can challenge: "Clients that disagree with an evaluated price can reach out to BVAL's evaluator team, which will analyze the securities in question and rapidly respond." Human analysts + models produce a golden price that is distributed to all customers via Terminal and enterprise feed — and note the pattern of publishing *confidence metadata* (the BVAL Score) alongside the value.
- **S&P Global / IHS Markit Totem**: a two-sided consensus service — "contributed by the world's leading market makers" who submit prices into structured sheets; "price submissions that are deemed problematic do not enter the consensus price calculation." The contributor/submission workflow (with a Price Challenge system and cutoff calendar) is a distinct application from how clients consume the consensus. This is your maker-checker + validation-gating pattern in a production market-data product.
- **ICE Benchmark Administration (LIBOR/benchmarks)**: crisp separation of *submit → calculate → publish*. Panel banks submit inputs; "USD LIBOR is calculated using Contributor Bank submissions … through the use of a standardised, transaction data-driven Waterfall Methodology," and "LIBOR is calculated as of 11:00 … and normally published by IBA at 11:55." The administrator's internal calculation engine is separate from the publication/distribution.
- **LSEG / Refinitiv**: explicitly "We calculate the benchmark rate … We publish the data on Eikon/Elektron … We distribute this data via third-party vendors," plus surveillance/monitoring for manipulation. LSEG's Real-Time Distribution System productizes exactly the internal-contribution-vs-external-distribution split: "Capture internal data contributions and publish them consistently across all consuming systems," with "granular, DACS-based permissions" and "a comprehensive audit trail."
- **Reference-data-management vendors (GoldenSource, NeoXam)**: the "data steward publishes centrally, everyone consumes" flow is literally the product. GoldenSource: ingest from multiple sources → "Validation & Data Governance" with embedded rules, workflows, and data lineage → "Mastering" (each item mastered once) → "the golden copy is published or distributed … via APIs and tailored data feeds." NeoXam DataHub builds a "single point of truth" / Golden Copy via configurable rules and automated workflows. This is precisely the internal tool your market-data desk needs.
- **QRM / Murex MX.3 (ALM/treasury peers)**: Murex offers MXSaaS as vendor-managed SaaS where "Murex handles infrastructure management … MX.3 technical settings and configurations" and monitors the platform 24/7 — i.e., vendor staff operate the platform on the customer's behalf through vendor-side operational tooling. MX.3's curve framework (multi-tenor curves, OIS/collateral discounting) is the analytic peer to what AequorOS builds; the operational lesson is the vendor-operated management layer.
- **Benchmark governance (IOSCO)**: benchmark administrators embed sign-off/validation before release ("sign off processes … prior to releasing Benchmark Rate determinations," a "three lines of defence" model, and escalation of expert judgment to a governance forum). The classic banking control here is maker-checker / four-eyes: "for each transaction, there must be at least two individuals … one individual may create a transaction, the other … confirmation/authorization." Bake this into your publish workflow.

**Implication for AequorOS:** your market-data desk should be a first-class internal application with contribution/entry, automated validation rules, lineage, a confidence/quality indicator, and a formal maker-checker publish gate — not a spreadsheet or a raw DB console. This tooling is core IP and worth building custom (see §6).

Note the specific Ghanaian context you're serving. The **Ghana Reference Rate (GRR)** is itself a published benchmark: the Bank of Ghana set its "maiden Ghana Reference Rate at 16.82%" in April 2018, developed "in consultation with the Ghana Association of Bankers re-constituted Working Group which reviewed the existing Base Rate model," and it is published monthly as a single industry-wide benchmark. Secondary-market government bond data comes from the **Ghana Fixed Income Market (GFIM)**, which "commenced operations on Monday, August 17, 2015" under the Ghana Stock Exchange's securities market licence, serving what was then a US$7.7 billion (GHS 28.9 billion) fixed income market; per BoG Governor Asiama's November 2025 10th-anniversary speech, "cumulative trading has now surpassed GHS 1.2 trillion" (up from GHS 5.2 billion at inception), trading via Bloomberg E-Bond and Capizar and settling at CSD Ghana. Your desk is effectively curating and re-publishing a golden copy of these public/official benchmarks plus your own constructed curves — reinforcing the need for auditable lineage back to the official source.

### 3. Market-data publishing architecture (global-shared + tenant-private)

This is the heart of the platform. Design it as **shared reference data + tenant-isolated overlays**, with a formal publish pipeline.

**Data placement.** The established multi-tenant pattern (documented in vendor patent literature and SAP/enterprise practice) is:
- **Shared, read-only tables/containers** hold common data used by all tenants (your golden-copy market data and published curves).
- **Tenant-isolated tables/containers** hold tenant-private data (each bank's spread overlays, private curves, configuration) — protected by your existing RLS + bucket-per-institution.
- **"Mixed/split" tables** carry both a shared read-only portion and a tenant-writable portion — which is exactly the "published curve + bank's overlay on top" case. Store the published base once in shared storage; store each bank's overlay in its tenant partition; compose at read time.

Do NOT copy the golden data into every tenant's schema (a common but poor approach that duplicates and de-syncs). Keep one authoritative shared copy; grant tenants read access; compose overlays on read.

**Publish pipeline (maker-checker + effective-dating + versioning):**
1. **Draft/entry** — an analyst enters or ingests Bank of Ghana rates, GFIM bond data, FX, GRR; the desk constructs the sovereign zero/forward curves and the synthetic OIS discounting curve.
2. **Validation** — automated rules (bounds, staleness, monotonicity/no-arbitrage sanity on curves, variance vs prior) gate the data; problematic inputs are flagged/excluded (the Totem model).
3. **Maker-checker / four-eyes approval** — a second qualified staffer must approve before publish. This is mandatory for a regulated data product and is standard banking control.
4. **Publish** — the approved dataset/curve becomes the new effective version, visible to all tenants' Markets tab and risk engines.
5. **Distribution** — publish/subscribe or event-driven propagation (an internal event bus) notifies tenant contexts / invalidates caches / triggers risk-engine refresh. Since the data is stored once in shared tables, "distribution" is largely cache/event invalidation rather than N copies.

**Versioning & effective-dating (bitemporal).** Financial data must be **bitemporal**: track *valid time* (the date the rate/curve applies to, e.g., the COB date) AND *transaction time* (when AequorOS recorded/corrected it). This lets you (a) reproduce exactly what a bank's risk engine saw on any past run ("as-of" reporting for IRRBB/Basel/FTP), (b) issue corrections/restatements without destroying history, and (c) satisfy audit/regulator queries. Every published curve version is immutable and append-only; corrections create new asserted versions, never overwrite. This is standard in financial systems and directly supports your risk-engine reproducibility and regulatory defensibility.

**Entitlements on published data.** Not every tenant necessarily gets every dataset (tiering, licensing, or data-vendor redistribution restrictions — note Bloomberg/LSEG contributed data often carries redistribution constraints you must respect). Model per-tenant entitlements on shared datasets (a permissioning layer keyed by tenant × dataset × effective range), analogous to LSEG's DACS permissioning on distributed data.

### 4. Onboarding / provisioning tooling

Provisioning a new bank tenant is a **control-plane workflow** and should be automated via infrastructure-as-code, orchestrated from the staff console but executed by a privileged provisioning service (never from tenant-facing code).

- **Tenant lifecycle**: onboarding → provisioning → active → offboarding. The provisioning step creates the RLS tenant, the per-institution storage bucket, IAM/policies, the per-bank OIDC SSO connection, seed configuration, and the first admin account.
- **IaC-driven**: Terraform / AWS CDK / CloudFormation templates parameterized per tenant give repeatable, auditable provisioning; a "saga" pattern with rollback handles partial failures. AWS publishes reference patterns for exactly this (silo and pool onboarding with Step Functions/Lambda control plane).
- **Integration configuration**: connecting a bank's core banking system (Temenos T24, Oracle FLEXCUBE) is bespoke per bank — the console should provide a structured config/secrets workflow (connection params in a vault, test-connection tooling, mapping configuration) rather than ad-hoc scripts.
- **Offboarding matters for compliance**: design deletion up front — delete/reclaim tenant data and crypto-shred per-tenant KMS keys so residual ciphertext (including backups) becomes undecryptable. Verify deletion is achievable *before* onboarding your first bank.

### 5. Support access / impersonation

You will need staff to enter a tenant to troubleshoot. Do it with the known-safe recipe; anything less creates SOC 2 / data-protection exposure, anything sloppier risks a cross-tenant incident.

- **Consent or break-glass.** Prefer explicit customer consent (à la Salesforce "Grant Account Login," where the customer enables time-limited vendor login without exchanging passwords). For emergencies, a break-glass path (Oracle's `SAAS_ADMIN` model: a normally-locked privileged path enabled only via a defined approval process, time-limited, heavily logged) — but "if the fallback path is easier than the normal path, it will be abused."
- **Distinct impersonation session, both identities preserved.** Use a delegation token that records both the agent and the target (JWT `act` claim pattern, inspired by AWS STS): the backend always knows *who* is really acting. The impersonation token has independent, short expiry (15–60 min) and does not disturb the customer's real session.
- **Persistent, un-dismissable visual indicator** during impersonation to prevent the classic "I thought I was in a test account" mistake.
- **Least privilege by default.** Read-only impersonation should be the default; write actions and access to sensitive banks require manager/second approval. Treat any session that can change security settings or export data as privileged access, not ordinary support.
- **Immutable, separate audit log** of every impersonation session (who, which tenant/user, reason, start/end, and ideally every write with an `impersonation_context` session ID), restricted to security/compliance roles, exportable to answer "has any AequorOS employee accessed our account in the last 90 days?" For banks this auditability is a procurement requirement, not a nicety.

### 6. Pragmatic staged build approach for a pre-seed/seed startup

The governing principle: **build the core IP, buy/framework the generic CRUD.**

**Custom (core IP — build it yourselves):**
- The **market-data desk**: data entry/ingestion, validation rules, lineage, confidence indicators, bitemporal versioning, and the **maker-checker publish workflow**. This is your moat and encodes Ghanaian-market domain knowledge — do not outsource it to a generic admin builder.
- The **curve construction tooling**: sovereign zero/forward curves, synthetic OIS discounting. Core quant IP.
- The **impersonation/support access** mechanism (because it touches your auth and tenant-isolation invariants — security-critical, must be correct).

**Framework/buy (generic, undifferentiated):**
- **Onboarding CRUD, user/entitlement management, billing views, tenant health dashboards, support ticket context** — build on an internal-tools framework. Options:
  - **Retool** — fastest to ship, polished, mature permissions and audit; note the pricing gotcha: the Business plan is "$65 per standard user" month-to-month ($50/user/mo billed annually) plus $18/end user, and **SSO is gated to the Enterprise tier** — which matters because you'll want SSO/SCIM against your workforce IdP from early on. Team plan is ~$10–12/standard user but thinner on governance. Still the best default for two founders who value speed, provided you budget for Enterprise when SSO becomes non-negotiable.
  - **Appsmith** — open-source, self-hostable, no per-seat cost, more control (matters if you want internal tooling on your own infra for data-residency reasons in Ghana/SSA); more maintenance.
  - **Django Admin** — N/A as written: the AequorOS backend is FastAPI, not Django. The FastAPI-native equivalents are SQLAdmin / Starlette-Admin (free, same lock-down caveats), or skip straight to Appsmith/Retool over the operator BFF.
  - **Forest Admin / React-Admin** — API-based admin over your existing services; Forest Admin gives SSO/2FA/permissions out of the box, React-Admin needs you to code authz (risk of inconsistency).

**Minimum viable internal console (MVP, first weeks):**
1. Staff login via workforce IdP (Okta/Google Workspace) with enforced MFA — separate from customer auth.
2. A market-data desk screen: enter/import today's BoG rates, GRR, GFIM/FX; construct curves; **validate → second-person approve → publish** (even a simple two-click maker-checker is enough to start).
3. Read-only tenant health + support impersonation (time-boxed, audited).
4. Tenant provisioning: even a semi-manual IaC-triggered runbook to start.

Wrap all of it behind a **single operator BFF** so authorization and audit are centralized from day one — that choke point is the one piece of architecture you should not defer, because retrofitting audit/isolation later is painful.

**Evolution path:** MVP (Retool/Django Admin front, custom desk logic behind BFF) → harden (SoD roles, full bitemporal store, automated validation, break-glass, immutable audit export) → scale (self-service tenant provisioning, entitlement tiers, formal governance forum for data publishing as you approach IOSCO-style benchmark credibility and bank due-diligence). Migrate generic screens from Retool to custom UI only when the framework's limits (or per-seat cost) actually bite — not before.

## Recommendations

1. **Commit now to the separate-control-plane architecture.** Stand up `console.aequoros.internal` as a distinct app behind VPN/zero-trust, with a workforce IdP (Okta/Entra/Google Workspace) + enforced MFA, and an operator BFF as the single audited choke point to core services. Do not add a super-admin role to the customer app. *Threshold to revisit:* none — this is foundational; the cost of retrofitting isolation later is far higher.
2. **Model market data/curves as shared read-only reference data + tenant-private overlays** using shared vs tenant containers and mixed/split composition-on-read. Never duplicate golden data per tenant.
3. **Make the publish pipeline maker-checker + bitemporal from day one.** Even a lightweight two-person approval and valid-time/transaction-time stamping now saves a painful migration and is a bank-procurement and audit necessity. *Benchmark:* if any bank asks "can you reproduce the curve my IRRBB run used on date X?" you must be able to say yes.
4. **Ship the known-safe impersonation recipe** (consent/break-glass, time-boxed, dual-identity token, visible banner, immutable separate audit). Default read-only; approval-gated writes. This is a checkbox on every bank's security questionnaire.
5. **Automate tenant provisioning with IaC**, orchestrated from the console but run by a privileged provisioning service; design and test offboarding/crypto-shredding before the first customer.
6. **Build the market-data desk and curve tooling custom; buy/framework the generic admin** (start with Retool or Django Admin). Revisit the buy decision when per-seat cost, data-residency, or customization limits become real constraints — typically as headcount and tenant count grow.

## Caveats

- **Failure modes to respect:** (a) internal admin tools that silently bypass tenant scoping — your single biggest cross-tenant risk; enforce tenant checks at the API and DB layers even for staff paths. (b) An "operator tenant" with cross-tenant read rights quietly turns RLS from a hard boundary into a bug-prone application decision. (c) Break-glass paths that are easier than normal paths get abused. (d) Duplicating golden data per tenant causes drift; keep one authoritative copy.
- **Complexity is real.** The BFF + separate-auth + bitemporal-publish design is more upfront work than a single app with a role flag. It is justified here specifically because you are regulated, multi-tenant with strict isolation, and vending data that feeds banks' regulatory risk engines. A non-regulated B2C tool might reasonably choose the simpler role-based approach; you should not.
- **Source quality notes:** Several vendor descriptions (Bloomberg, Murex, LSEG, S&P) come from vendor marketing/product pages and describe capabilities, not internal implementation detail; the internal-vs-customer *split* is well evidenced, but exact internal architectures are proprietary. The Totem "distinct dashboard" detail is partly from a designer's portfolio (self-reported) corroborated by S&P's product page. The literal phrase "four-eyes/maker-checker" is an industry-generic banking control; benchmark administrators document the concept under "sign-off," "validation checks," and "three lines of defence" rather than that exact phrase.
- **Ghana Reference Rate methodology conflict:** current (2025–26) reporting describes a three-input formula (monetary policy rate + interbank rate + 91-day T-bill); older secondary sources describe a weighted average of 91/182/364-day T-bills. No Bank of Ghana primary methodology PDF was located in this research (the 16.82% maiden rate and Working-Group governance are well sourced); confirm the current formula directly with BoG before encoding it in your curve/validation logic.
- **Not covered / to validate with counsel:** data-redistribution licensing (Bloomberg/LSEG/exchange data you may ingest often restricts onward distribution to your tenants), Ghanaian/SSA data-residency and banking-supervision requirements, and SOC 2 / ISO 27001 scoping for the control plane — all of which will shape the final design.
## As-built alignment & gap register (added 2026-08-09, reviewed against the codebase)

The research above is sound and the control-plane recommendation stands. This
section reconciles it with what AequorOS has actually built, because several
of the document's "build this" items already exist as first-class machinery —
and one major integration decision is missing entirely.

### A. The one unmade decision: how desk-published data reaches tenants

The document proposes global shared tables for golden market data, but the
codebase has a hard, deliberately-guarded invariant: **market data flows only
through `app/adapters/market_data/` and `pull_runner.execute_pull` — the
single writer of market-data canonical state** (CLAUDE.md; enforced by the
contract suite's leak canary). Three adapters exist today, registered by
vendor name: `bloomberg`, `refinitiv`, `manual_upload`.

Two compliant shapes, pick one before any console work starts:

1. **Desk-as-vendor (recommended).** The AequorOS desk becomes a fourth
   registered source (`aequoros_desk`). The console's publish step lands the
   approved golden dataset through the SAME single-writer seam per entitled
   tenant — distribution is a pull/push through existing plumbing, read-time
   resolution and DataScope gating apply unchanged, and the customer plane
   needs zero new read paths. Cost: N tenant copies (the document warns
   against per-tenant duplication, but here the copies are mechanical
   projections of one immutable published version with lineage back to it —
   drift is impossible if tenants cannot edit the vendor's rows, which the
   adapter model already guarantees).
2. **Shared tables + read-path integration.** One shared copy, composed at
   read time inside `app/services/market_data.py` (the single read seam).
   Truer to the document's storage guidance, but it introduces the first
   shared-mutable-adjacent market state outside the single-writer rule and
   touches every calculation module's read path. Precedent exists — the
   `jurisdictions` registry is already global/non-tenant-scoped — but that
   registry is tiny, near-static config, not daily-published pricing.

Shape 1 preserves the platform's strongest invariant and reuses the existing
supersession/effective-dating semantics ("within a source series") for the
bitemporal story. Shape 2 is only worth its cost at a tenant count where N
projections are materially expensive.

### B. Already built — reuse, don't reinvent

- **Maker-checker + audited registers**: the approver role ladder, Board
  Register editors (evidence fields: effective-from, approved-by, reason),
  append-only `audit_events` (UPDATE/DELETE blocked by DB trigger), and the
  attestation workflow are live. The desk's validate→approve→publish gate
  should reuse these exact patterns and roles, not parallel ones.
- **Effective dating + supersession** on market-data series, with read-time
  cross-source resolution — the bitemporal foundation exists; publish adds
  immutable version rows, it does not invent temporality.
- **Entitlements**: DataScope already gates ingestion and vendor pulls per
  tenant. Per-tenant entitlements on published datasets are a DataScope
  extension (tenant × dataset × effective range), not a new layer.
- **Cross-tenant service precedent**: the background worker is the ONLY
  cross-tenant actor, via a dedicated BYPASSRLS role (`WORKER_DATABASE_URL`).
  The operator BFF should follow that precedent: its own DB role, its own
  service identity, never the tenant app role.
- **Workforce-auth machinery**: the platform is its own OIDC RP
  (`verify_oidc_id_token`, JWKS, domain allow-lists). Pointing the console at
  a Google Workspace/Okta issuer reuses this code wholesale — a separate IdP
  does not mean a separate auth implementation.
- **Read-only staff-adjacent role**: the `examiner` role (reads everything in
  a tenant, no mutation gate admits it) is the in-tenant permission shape a
  default read-only impersonation should map onto.
- **Vendor-credential custody**: EncryptedDbVault (AES-256-GCM) for per-
  connection credentials; T24/FLEXCUBE connection config + test-connection
  tooling already exist in the Data Engine — §4's "structured config/secrets
  workflow" is partially built, tenant-side.
- **Network posture**: the VPS already runs source-IP allowlisting at the
  Docker level (CEDYN-RESTRICT chain in DOCKER-USER) — the console's
  VPN/allowlist story rides the same mechanism.

### C. Genuine gaps (build items, roughly sequenced)

- **CP-1 — Tenant provisioning service (the biggest, and a prerequisite for
  any non-Ghana pilot).** There is NO bank-creation path outside the test
  seed today; ingestion 404s if the bank doesn't exist. Provisioning must
  create org + bank (platform IDs are generator-assigned), REQUIRED
  `currency` + `jurisdiction_code` (no defaults, by design), the storage
  bucket, the SSO connection stub, and the first admin. This is the control
  plane's first real service regardless of every other choice in this doc.
- **CP-2 — Operator BFF + workforce login** (separate app, worker-precedent
  DB role, impersonation issuing dual-identity tokens: org claim + `act`
  claim; the backend's TenantContext already carries actor identity, and
  audit_events is already append-only).
- **CP-3 — The market-data desk** (entry/import, validation rules, lineage to
  official sources, maker-checker publish through the Shape-1 seam).
  **Curve CONSTRUCTION does not exist anywhere yet** — fact derivation
  selects ingested curves; bootstrapping sovereign zero/forward curves and a
  synthetic OIS discount curve is new quant engine work, not console work.
  Budget it separately from the console UI.
- **CP-4 — Offboarding/crypto-shred: currently NOT achievable.** The MinIO
  deployment has no KES (documented quirk; retirement planned 2027-01-14).
  The document's own rule — "verify deletion is achievable before onboarding
  your first bank" — currently fails. Storage re-platform or KES enablement
  is a prerequisite to the first paying tenant, independent of the console.
- **GRR methodology**: the document's caveat stands; additionally, nothing in
  the current codebase encodes a GRR formula, so there is nothing to correct
  yet — resolve with BoG before CP-3 encodes validation rules.

### D. Corrections applied to this document
- `app.aequoros.com` → `bank.aequoros.com` (subdomain rename, 2026-08-03).
- Django Admin option marked N/A (backend is FastAPI; SQLAdmin/
  Starlette-Admin are the native equivalents).
