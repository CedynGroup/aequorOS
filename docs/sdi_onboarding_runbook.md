# AequorOS SDI — client onboarding runbook

How to stand up a Specialised Deposit-taking Institution as a real, scoped tenant.

> **Licence reform, 27 January 2026.** Notice No. BG/GOV/SEC/2026/03 (*Guideline on
> the Revised Microfinance Sector Framework*, effective on issuance) abolished the
> old specialised deposit-taking licence classes: savings & loans companies,
> finance houses, deposit-taking microfinance companies and micro-credit companies
> are folded into a single **Microfinance Bank** class (§3.1.3–3.1.5), and rural
> banks became **Community Banks** (§3.2.2, confirmed by BoG's press release of
> 17 June 2026). Compliance date **31 December 2026**. The `savings_and_loans` and
> `finance_house` codes remain in the registry because institutions mid-transition
> are still on them — they are **legacy classes with a cutover**, not current
> forward-looking licences. Do not describe them to a client as current.
> Evidence: `backend/docs/bog_parameter_sources.md` §2.2.

No fixture, no seeded book — the institution exists once its first ingestion batch
lands (the platform's standing no-seed rule).

Clients of every institution type sign in from the **same** universal login
(`bank.aequoros.com` / `:3002` in dev). The scoping is post-auth, driven by the
`institution_type` chosen at provisioning — there is no separate SDI portal.

---

## 0. Prerequisites (one-time, platform side)

> **Read before quoting any number below to a client.** The seeded SDI parameter
> set has been audited against Bank of Ghana primary sources and several rows are
> **not** safely citable today. `backend/docs/bog_parameter_sources.md` and the
> master register record, with evidence:
>
> - **CAR floor 10% cited to "Act 930 s.29"** — s.29 is an *enabling* provision
>   empowering BoG to prescribe a ratio; it sets no number. The 10% figure is also
>   exactly the DDEP-era *bank* number. Treat as unconfirmed.
> - **Primary/secondary liquidity reserves 10%/15%** and the **4-grade
>   provisioning grid (20/50/100)** are cited to the *NBFI Business (BoG) Rules,
>   2000*, made under PNDCL 328 — **repealed** by Act 774 / Act 930 and no longer
>   published by BoG. Historical context only; do not present as current SDI rules.
>   No current BoG instrument gives SDIs a distinct five-band schedule.
> - **The 8 LMTD Table-1 floors** come from the *Liquidity Monitoring Tools
>   Directive* **exposure draft** (Feb 2026, stated effective 1 Jan 2027). Not in
>   force. The Board's own adopted thresholds are what bind today.
> - **Paid-up floors** — the 2026 framework sets Microfinance Bank at GH¢50m
>   (transitioning) / GH¢100m (new entrant) and Community Bank at GH¢5m / GH¢10m
>   (urban). The S&L/finance-house GH¢15m figure is legacy and is itself only an
>   undated licensing sheet read by elimination.
> - **No cash reserve ratio has ever been published for SDIs.** Act 930 s.36(1)–(2)
>   permits a different one; BoG has not issued it. Do not default an SDI to the
>   bank's 20%.
>
> Every one of these is editable in **Console → Regulatory Parameters** under
> four-eyes approval. Where a value cannot be sourced, leave it `pending` — a
> plausible wrong number is a filing risk, an empty field is not.

- The regulatory-parameter control plane is seeded (migration `202608200025`). The
  SDI class carries: CAR floor 10% (Act 930 s.29), large-exposure limit 15%,
  single-obligor 25%, the 8 LMTD Table-1 floors (90/100/50/60/30/40/60/70),
  primary/secondary liquidity reserves 10%/15%, NBFI 4-grade provisioning
  (20/50/100), and the licence paid-up floors (S&L 15m, finance house 15m, MFC 2m,
  RCB 1m). Values marked **pending BoG confirmation** (risk-weight buckets,
  related-party & aggregate-exposure caps, the loan-classification grid choice)
  are editable in **Console → Regulatory Parameters** with four-eyes approval —
  update them the moment BoG confirms; no deploy needed.

## 1. Operator provisions the tenant (console developer portal)

1. Console → **Onboard** → fill Institution details. Set **Institution type** from
   the selector (validated against the `institution_types` registry server-side).
   For a new licence this is **Microfinance Bank** or **Community Bank**; pick
   `Savings & Loans` or `Finance House` only for an institution that still holds
   that legacy licence and is transitioning by 31 December 2026. Set currency `GHS`, jurisdiction `GH`, the admin email.
2. Submit → the `provision_institution` saga creates the Organization (`OR-…`),
   Bank (`BK-…`), storage bucket, KMS key, SSO stub, and the first account
   administrator (one-time password shown once). In the same transaction it
   records that necessarily sole active human administrator as Org Owner through
   an organization-wide binding. Selecting the SDI type here is what scopes the
   modules, requirements and returns.

## 2. Account administrator signs in — the scope is applied

Sign in at `bank.aequoros.com` / `:3002`. The SDI now sees the scoped module set
(Command Center, Risk, Alerts, Liquidity, simplified Capital, Regulatory
Reporting, Data Engine, Institution Profile, Reports, Settings). Bank-only
modules (IRRBB, FTP, Forecasting, FX, Markets, Positions, Behavioral) are hidden
and 404 by URL (`ModuleGuard`). The return calendar is class-filtered.

## 3. Enter institution master data (`/institution`, REST — not ingested)

Institution Profile (`institution_type`, licence class, `orass_institution_code`,
ownership), Related Parties + roles, Shareholding, Outlets (branch network),
Licence, Product register. Analyst role; every mutation reasoned + audited.

## 4. Ingest the book through the Data Engine (never seeded)

Push the SDI canonical dataset (docs/sdi.md §5) via the three-call push
(`push-batches → records → commit`) or Excel/CSV upload:

- **Canonical positions + snapshots** — `CASH` (vault = no counterparty; BoG
  balances = `CENTRAL_BANK`), `DEPOSIT` (the load-bearing funding book),
  `SECURITY_HOLDING` (GoG/BoG bills), `LOAN` (the asset book), interbank if any.
  Mandatory attributes: `balance` + `attributes.balance_ghs`; **`deposit_account_type`**;
  `contractual_maturity`; `ifrs9_stage` and/or `attributes.days_past_due` on loans;
  `encumbered`/`pledged_as_collateral`; `regulatory_category` on products.
- **Counterparties** — `counterparty_type`, `resident`, `group_reference`
  (connected-group limits + concentration).
- **`capital_structure` reference dataset (MANDATORY)** — `{capital_component,
  tier, amount_ghs}`. **The component names must map to the recognised set** so
  the paid-up check resolves: use `paid_up_capital` (or stated_capital /
  share_capital / ordinary_shares / common_equity) and `statutory_reserves`. The
  S&L chart-of-accounts → component mapping is part of onboarding.
- **`historical_cashflows`** (for the 90-day view) if available.

## 5. Confirm module readiness (data-quality diagnostics)

`assess_sdi_readiness` (`app/services/sdi_readiness.py`) reports per module
**READY / PARTIAL / BLOCKED** with the exact missing-data reason, e.g.:

```
liquidity_table1   READY
maturity_ladder    PARTIAL   17 positions without contractual_maturity → residual bucket
capital            BLOCKED   capital_structure reference dataset not ingested
provisioning       PARTIAL   4 loans without days_past_due or ifrs9_stage → unclassified
```

Feed what each BLOCKED/PARTIAL line names, re-ingest, re-check.

## 6. What computes for the SDI (all against SDI parameters)

- **Liquidity** — LMTD Table-1 prudential ratios bind against the SDI floors;
  primary/secondary liquidity-reserve check (cash+BoG / eligible securities vs
  10%/15% of deposits); maturity ladder; funding concentration. LCR/NSFR are
  excluded.
- **Capital (simplified s.29)** — CAR against the 10% floor over the credit-risk
  base (market/operational RWA excluded); minimum paid-up-capital check;
  statutory-reserve-fund adequacy. No CET1/AT1/Tier2/leverage.
- **Exposures** — single-obligor (25%) + large-exposure (15%) limits at the
  connected-group level, findings on breach; aggregate cap dormant until confirmed.
- **Loan classification / provisioning** — NBFI 4-grade, rates 20/50/100, NPL@90d.

## 7. Regulatory returns

The bank BSD return calendar does **not** apply — an SDI's calendar filters to its
own set. The SDI/ORASS return pack itself is **blocked pending BoG** (it is not
public and is never fabricated); the return framework is ready to receive it.

## 8. Audit

Every regulatory value carries its source citation + confirmation status; every
control-plane change is four-eyes + `operator_audit_log`; every tenant mutation is
reasoned + audited. A calculation can answer: what value, which version, whose
change, what source, was it confirmed, what data was missing.
