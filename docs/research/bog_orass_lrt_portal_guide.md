# Bank of Ghana ORASS LRT Portal — Primary-Source Notes (User Guide v1.0)

**Source:** *ORASS Licensing and Regulatory Transactions (LRT) Portal User Guide,
Version 1.0, September 2020 (watermarked DRAFT)* — Bank of Ghana, prepared by the
IT & Cyber Security Office (BSD) and Policy & Regulation Office (OFISD).
**Reviewed:** 2026-07-24 (78 pages, read in full).
**Status of this doc:** primary-source companion to
`bog_orass_submission_channels.md` and `bog_returns_and_templates.md`. It records
what the guide **documents** and, per fact, whether it **upgrades** a prior
research confidence level. The guide PDF itself is not committed (a bank-supplied
document); this note is the citable summary.

> The guide covers the **licensing / master-data module** (institution profile,
> related parties, outlets, products, capital, lifecycle transactions). It is
> NOT the prudential-returns module — no BSD-2/BSD-3/DBK content appears — but it
> documents the **same Vizor return-lifecycle engine** the supervisory returns
> ride on, so it is our best calibration source for the submission pipeline.

## 1. Confidence upgrades (research UNKNOWN/INFERRED → DOCUMENTED)

| Fact | Prior state | Now | Guide evidence |
|---|---|---|---|
| ORASS platform = Vizor | CONFIRMED (vendor) | **Reconfirmed from BoG's own doc** | p.4 names "Vizor Licensing and Regulatory Transactions (VLRT)" |
| Submission is immutable post-submit | INFERRED | **DOCUMENTED** | §5.1: "Once submitted, a return can no longer be edited, but can still be viewed in submission history." |
| Correction requires a formal request | INFERRED | **DOCUMENTED** | §5.3 Request Resubmission: reason-required → BoG grants/denies → return reappears in drafts, **revision increments 1.0 → 1.1** |
| Reject vs Decline are distinct outcomes | UNKNOWN (names were ours) | **DOCUMENTED** | §5.2/§3: **Submitted for Approval → Approved / Declined**; separately, a **Rejected** return is *returned for correction* with a **View Comments** panel (§4, Actions) |
| Reviewer comments exist | UNKNOWN | **DOCUMENTED** | §4: "View Comments" shows supervisor comments on a returned return |
| Per-revision audit log | UNKNOWN | **DOCUMENTED** | §4 View Audit Log: revision × status × action × actioned-on/by × submitted-on/by |
| Reference-ID shape | UNKNOWN | **DOCUMENTED** | Form-set-prefixed sequences: `PS01390`, `AFL01296`, `VWU01217` |
| Validate-before-submit | INFERRED | **DOCUMENTED** | §4: forms validate against "structural rules programmed in the system"; a return submits only when every form is **Valid** |
| Two-date deadline model | INFERRED | **DOCUMENTED** | Draft Returns carry both **Return End Date** (effective/period) and **Due Date** |
| Async submission | UNKNOWN | **DOCUMENTED** | §5.1: "final submission can take up to 2 minutes depending on the complexity" |
| Portal roles | UNKNOWN | **DOCUMENTED** | §8, §13: **Licensing Portal Principal User** (full access, **sole submitter**, BoG-managed) vs **Secondary User** (create/complete only, cannot submit or manage users) |
| Password policy | UNKNOWN | **DOCUMENTED** | §2.1: ≥8 chars, all four classes, ≤30, no spaces; 3 failed attempts → lockout |

**Still UNKNOWN after the guide** (unchanged): the prudential-returns transport
(BSD-2/BSD-3/DBK lodgement mechanics), any public API endpoint/field/credential
spec (none appears anywhere in the guide — LRT returns are hand-filled web forms
+ document uploads). Our XLSX-export + portal-entry + email-fallback posture and
the labeled ORASS simulator remain correct; real integration still goes through
the BoG/Regnology onboarding pack.

## 2. Return-lifecycle vocabulary (as implemented, plan W1)

Documented flow: `No Data → In Draft → Valid → Submitted for Approval →
Approved / Rejected / Declined`, where **Rejected** = returned for correction
(reappears in Draft Returns with comments) and **Declined** = final refusal.
Resubmission of a submitted/acknowledged return: **Request Resubmission
(reason) → granted/denied → revision +0.1**. AequorOS mirrors this exactly —
see `docs/submission_pipeline_plan.md` §W1 and the fidelity suite
`backend/tests/services/test_regulatory_reporting_fidelity.py`.

## 3. Roles (as implemented, plan W2)

ORASS's Principal-only-submit split is a portal-enforced maker–checker. AequorOS
maps it: analysts prepare (generate/validate), **approver-role** logins release
(approve/submit/poll/resubmission-decision). The bank's ORASS Principal/Secondary
user identities are captured in the channel config. See §W2.

## 4. Master-data surface (as implemented, plans W4/W5)

The LRT catalog (guide §7, §12) is the institution master data BoG holds:
Reporting Institution Profile (type, legal structure, authorisation date,
approved capital, TIN/registration, business activities, GSE listing/ISIN,
local/foreign ownership, licences, name history) and the Related-Party register
(shareholders with share classes/rights/%, directors with allowances, KMP by
officer role, external auditor with ICAG registration, ultimate beneficial
owners [individuals only, linkable to corporate shareholders], outsourced
providers, liquidators — each with CDD/EDD/Personality-Notes forms), plus outlet
open/relocate/close, product/service approval, capital injection, tier
conversion, M&A, MTN issuance, voluntary winding-up, and financial-markets ad-hoc
uploads (externalization, equity confirmation, FX forward-rate auction).

AequorOS built this as the `institution_profiles` + related-party register (W4)
and the five event-driven **corporate (LRT)** return packs (W5) generated from
that register at CONFIRMED-per-guide fidelity. The guide's own **draft** status
is carried in every LRT template's notes; the OO projected-investment / 3-year
operating-results tables are noted as completed-in-ORASS-at-submission (not
derivable from master data).

## 5. Caveats

- **Draft, 2020.** Some described features were not active at writing ("delete
  return has not been activated yet"). Treat lifecycle **semantics** as
  authoritative; treat exact **screens/field layouts** as potentially stale.
- The guide does not resolve prudential-return transport — that remains the
  Regnology-onboarding dependency the settings page already anticipates.
