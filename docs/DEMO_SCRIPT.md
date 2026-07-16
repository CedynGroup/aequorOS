# AequorOS — 15–20 Minute Demo Script (Treasurer / ALM Head / CRO)

Audience: bank Treasurer, ALM Manager, or CRO. Environment: Sample Bank Ltd (synthetic BoG
licensee) with 10 years of monthly history (2016-07 → 2026-06), a 418k-position canonical
book, live calculation engines, and real market-data connections. Dark theme. Backend on
:8003, dashboard on :3001.

**Pre-demo checklist (5 min before):** backend healthy (`/api/v1/banks` 200); latest period
has live metrics (Command Center shows "period 2026-06"); alerts bell shows the open breaches;
optionally reset the tour (`localStorage.removeItem('aeq-tour-done')`).

---

## Act 1 — "What should I care about right now?" (4 min)

1. **Open `/` (Command Center).** Let the breach banner land: *"5 open limit breaches —
   CAR 7.77% below the 10% minimum."* Point out these are computed numbers from the bank's
   own uploaded book, not staged data.
2. **Module pulse wall.** Six engines, one glance: LCR 183.8% compliant, FX NOP in breach,
   worst ΔEVE −0.89% vs 15% limit. Every card: live status, delta vs prior month, sparkline,
   computed-minutes-ago.
3. **Role lenses.** Click **Risk** — breached modules sort first; click **CFO** — capital and
   earnings lead. Same truth, different desk.
4. **Freshness strip.** "Data changed since last official run" — the live view is continuous;
   the filing numbers are immutable. That's the two-tier engine.

## Act 2 — Drill-down: from KPI to deal (5 min)

5. **Click the Capital card → `/basel`.** CAR gauge vs the BoG floors (LimitBar shows exact
   headroom), capital waterfall CET1→deductions→T2, RWA donut. Open **RWA** tab: bucket
   breakdown with exposures and weights.
6. **`/risk` (Risk & Limit Monitor).** The wall: 9 tracked limits, 6 breaching, all thresholds
   read from the engines — "we never hardcode a limit in the UI."
7. **`/positions`.** The blotter sizes the book: 418,796 positions — and *refuses* an
   unbounded read (scale honesty). Filter to a type, open a row: full detail + lineage chain
   back to the exact ingestion batch. "Every number is auditable to its source file."

## Act 3 — Market data & the Data Engine (4 min)

8. **`/markets`.** GHS sovereign curve with per-tenor table; every value carries its source
   chip (BLOOMBERG / REFINITIV / MANUAL_UPLOAD) and freshness. Stale values wear an amber
   age chip — stale is never silent.
9. **`/data-engine/market-data`.** The connect flow: Bloomberg/Refinitiv cards (credential
   forms, scope selection with quota estimates, test pull) and Manual Upload with one-click
   templates. "No Bloomberg? BoG website + our template = same canonical curve."
10. **Data Engine Overview.** Batches flow in → facts derive → engines recompute →
    dashboards update. No buttons. Show the pipeline timeline on the Command Center ops feed.

## Act 4 — Decisions: scenarios and forecasting (4 min)

11. **`/irr` → Scenarios.** 22-scenario official suite; pick two shocks side-by-side; the
    register shows immutable runs with input hashes — "reproduce any filed number, years later."
12. **`/forecasting` → What-if Lab.** Run a shock live; base-vs-shocked paths + breach pills
    appear in seconds. Then **Scenarios**: A/B compare two saved runs with assumption diffs.
13. **`/liquidity` → CFP.** Early-warning indicators driven by today's real values; the
    escalation playbook is clearly badged Illustrative — "we never blur computed vs indicative."

## Act 5 — Governance close (2 min)

14. **`/reports` → Board Pack.** Print preview: cover, executive KPIs, six module briefs with
    provenance — a board-ready PDF via the browser, every figure traceable.
15. **Close on `/` + ⌘K.** "Six regulatory engines, live on your own data, every number
    auditable to source. This is the workbench your treasury runs on."

---

### Q&A quick answers
- **"Is this our data?"** Yes — canonical model from your uploads/API/vendors; lineage on every row.
- **"What if Bloomberg goes down?"** Fallback hierarchy: other vendor → fresh cache →
  stale-with-attribution → manual upload. Never silently stale.
- **"Can numbers change after filing?"** No — official runs are immutable snapshots with
  value-based input hashes; the live view updates continuously alongside them.
- **"Multi-currency?"** GHS reporting with USD/EUR/GBP/NGN books, FX translation via
  arbitrated market rates.

### Known demo caveats
- Alert acknowledge and resolved-history are Phase 2 (read-only stream today).
- Role lenses are view permutations, not access control (Phase 2).
- ORASS submission is a clearly-labeled sandbox simulator (BoG publishes no public API docs);
  live onboarding is a channel swap — see docs/BOG_ONBOARDING.md.

---

## Act 6 — Official BoG submission (added with the Regulatory Reporting hub; +5 min)

16. **Governance → Regulatory Reporting.** The calendar lands first: every BoG obligation
    (LMT/BSD3 monthly ≤9 days, IRRBB quarterly, ICAAP end-March) with RAG status and — for
    overdue items — indicative Act 930 penalty exposure. "This is the compliance officer's
    morning screen."
17. **Generate a return.** Returns → BSD3 for the latest period → Generate. The package is
    an immutable snapshot carrying the input hash of every source calculation run — point at
    the fidelity banner (directive citation; CONFIRMED vs REPRESENTATIVE honesty).
18. **Validate + dual control.** Validation findings appear graded; request approval; switch
    to Approvals and approve as a second officer (demo affordance, clearly labeled). Try
    approving as the maker first — the 409 maker-checker refusal is the compliance story.
19. **Export + submit.** Export Excel/PDF (open the PDF: cover, attestation block, provenance
    appendix). Submit via ORASS — sandbox-labeled — poll to acknowledgement. The obligation
    flips to on-track on the calendar.
20. **The downtime drill (if time).** Settings → toggle channel downtime → submit → the 409
    routes to the email fallback with the pre-formatted bundle; note the amber "pending ORASS
    re-upload" state that refuses to call the obligation complete until the re-upload
    acknowledges — exactly Notice BG/FMD/2026/07 semantics.
