# Going Live with Bank of Ghana Submissions — Bank Onboarding Guide

How a bank moves AequorOS regulatory reporting from the demo sandbox to live BoG submission.
Companion to `docs/regulatory_reporting.md` (architecture) and `docs/research/` (source dossier).

## What works out of the box (no credentials)

- The full package lifecycle on your own computed data: generate → validate → maker-checker
  approval → export (Excel/CSV/PDF) → sandbox submission with acknowledgement simulation.
- The obligations calendar with BoG deadline rules (LMT/BSD3 monthly ≤9 days, IRRBB quarterly
  ≤9 days, ICAAP/stress annual end-March) and Act 930 s.93(3) indicative penalty exposure.
- Every artifact is BoG-convention formatted (GHS'000, parentheses negatives, solo basis,
  attestation blocks) and carries a provenance appendix (source runs + input hashes).

## Fidelity disclosure (read before filing anything)

Template fidelity is graded per return in Governance → Regulatory Reporting → Templates:
- **CONFIRMED/PARTIAL** — structure sourced from published BoG directive appendices
  (LMTD 2026 exposure-draft tables; IRRBB Appendix parameters).
- **REPRESENTATIVE** — professional reconstruction where BoG has not published the form
  (ORASS CAR form, DBK layouts, LCR runoff rates). Before first live filing, reconcile these
  against the forms in your bank's ORASS account and file template deltas with AequorOS —
  templates are versioned config, not code.

## ORASS go-live checklist

1. **Confirm your ORASS access.** Your institution was onboarded to
   orassportal.bog.gov.gh by BoG (all RFIs onboarded as of 2023). Identify your ORASS
   administrator and institution code.
2. **API access.** ORASS (Vizor/Regnology platform) exposes a submission API
   ("Vizor API Service – Submit"), but BoG does not publish public documentation. Request
   API onboarding through your BoG supervision contact, or engage an accredited local
   integrator. Ask for: authentication credentials, accepted file formats for each return,
   and the acknowledgement/status interface.
3. **Configure AequorOS.** Governance → Regulatory Reporting → Settings: institution code,
   reporting basis (solo/consolidated), contacts, and the ORASS credentials (stored
   AES-256-GCM encrypted, write-only — the UI shows only a fingerprint; requires
   `CREDENTIAL_VAULT_MASTER_KEY` on the backend).
4. **Swap the channel.** The sandbox channel is a drop-in seam
   (`app/services/regulatory_reporting/channels/`). A live `OrassChannel` implementing the
   same `submit`/`poll` protocol replaces it per return family — no workflow, UI, or
   pipeline changes. This is deliberately the only code change between demo and production.
5. **Parallel-run.** BoG expects continuity: run AequorOS packages alongside your current
   process for at least one full cycle per return; reconcile numbers and template layout
   before switching the channel default.

## Email fallback (downtime only)

Per Notice BG/FMD/2026/07, email is accepted **only during ORASS downtime**, and a submission
is deemed complete **only after re-upload once ORASS is restored**. AequorOS encodes this:
an email submission keeps the obligation open (amber "pending ORASS re-upload") until the
ORASS re-submission acknowledges. The fallback bundle includes the artifact list with
checksums and a pre-formatted subject line; confirm the correct downtime recipient with your
supervision contact (BoG has not published one — bsdletters@bog.gov.gh is confirmed for
consultation correspondence only).

## Deadlines & penalties (why the calendar is strict)

Act 930 s.93(3): late/incomplete/inaccurate returns expose the institution AND responsible
officers to up to 500 penalty units plus 50 units/day continuing (penalty unit GH¢12).
The calendar grades every obligation (overdue / due-soon / on-track) in Africa/Accra time and
shows indicative exposure for overdue items.

## Support boundary

AequorOS generates, validates, packages, and transmits. The bank remains the submitting
institution of record: approvals are your officers' attestations, and template fidelity for
REPRESENTATIVE returns must be confirmed against your ORASS forms before first live filing.
