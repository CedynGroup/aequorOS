# LMTD 2026 — Directive vs As-Built Gap Analysis

**Source:** Bank of Ghana, *Liquidity Monitoring Tools Directive* (Exposure Draft,
February 2026) — the full 32-page document, supplied by Bernard.
**Compared against:** the running `LMT` return
(`app/services/regulatory_reporting/templates.py`, `registry.py`), not our documentation.
**Date:** 3 August 2026.

**Method note.** Every "we have / we don't have" claim below was checked against the
template and generator code. Where I am inferring rather than verifying, it says so
explicitly. Two items are flagged as *needs a decision from a practitioner*, not findings.

---

## 1. Two facts that change the schedule

**The comment window has closed.** The draft invited comments to `bsdletters@bog.gov.gh`
**by 30 June 2026**. That was five weeks ago. We cannot file comments on this draft through
the official channel, and **a final version may already exist that differs from this
document.** Everything below is measured against an exposure draft.

→ *First question for Bernard: has the final LMTD been published, and can he obtain it?
Building precisely against a superseded draft is the expensive version of this mistake.*

**The real deadline is 31 December 2026.** The Directive takes effect **1 January 2027**
(¶8), and RFIs must have governance, tools and processes aligned **by 31 December 2026**
(¶9). That is roughly five months out, and it is an alignment deadline for the *bank*, not
just a reporting start date.

Reporting cadence is **monthly, no later than 9 days after month end** (Part II ¶7) —
which matches what we already have.

---

## 2. Headline: our own internal assessment was accurate

Our code carries this note:

> "LMTD Appendix Tables 1, 3-4, 6-8 and 10 remain unfilled."

Having read the directive in full: **that is exactly right.** Seven tables unfilled, four
partial, none complete. I had expected to find our self-assessment optimistic. It is not.

| # | BoG table | Our state |
|---|---|---|
| 1 | BOG Prudential Ratios | **NOT BUILT** |
| 2 | Contractual Maturity Mismatch | **PARTIAL** — buckets do not match (see §3) |
| 3 | Items with No Contractual Maturity | **NOT BUILT** |
| 4 | Customer Collateral Received (re-hypothecation) | **NOT BUILT** |
| 5 | Funding from Significant Counterparties | **PARTIAL** — wrong selection rule (see §4) |
| 6 | Assets & Liabilities by Significant Currency | **NOT BUILT** |
| 7 | Time Buckets of Maturity of Exposures | **NOT BUILT** |
| 8 | Concentration of Deposit Funding | **NOT BUILT** |
| 9 | Available Unencumbered Assets | **PARTIAL** — 3 of 7 columns |
| 10 | Collateral Received | **NOT BUILT** |
| 11 | LCR by Significant Currency | **PARTIAL** — aggregate currency only |

---

## 3. Finding A — the maturity ladder buckets are lossy, not just coarse

This is the most consequential technical finding, because it is **not a display change**.

| BoG Table 2 (14 buckets + Total) | Our ladder (7 + non-contractual) |
|---|---|
| Next Day | overnight |
| 2–7 days | 2–7d |
| **8–14 days** | *merged* → 8–30d |
| **15 days–1 month** | *merged* → 8–30d |
| **1–2 months** | *merged* → 1–3m |
| **2–3 months** | *merged* → 1–3m |
| 3–6 months | 3–6m |
| **6–9 months** | *merged* → 6–12m |
| **9 months–1 year** | *merged* → 6–12m |
| **1–2 years** | *merged* → >1y |
| **2–3 years** | *merged* → >1y |
| **3–5 years** | *merged* → >1y |
| **> 5 years** | *merged* → >1y |
| Non-contractual | non-contractual ✓ |

Our buckets are a strict aggregation of BoG's. **The finer buckets cannot be recovered from
the coarser ones** — this must be re-bucketed at derivation from contractual maturity dates,
which we already hold. The fix is mechanical, but it is a generator change, not a
formatting one.

**Also missing from Table 2: the entire off-balance-sheet block.** BoG numbers rows 1–17;
we produce the equivalent of rows 1–11 only. Rows 12–17 are absent:

- 12 Off-balance sheet exposure to liquidity risk
- 13 Liquidity facilities provided to off-balance-sheet vehicles
- 14 Undrawn commitments (sum of 15–17)
- 15 Unutilised portion of irrevocable lending facilities
- 16 Unutilised portion of irrevocable letters of credit
- 17 Indemnities and guarantees

We *do* ingest off-balance-sheet positions (`LC_GUARANTEE`, `COMMITMENT_UNDRAWN`, and the
FLEXCUBE `LCTB_OBS_MASTER` table), so rows 15–17 look reachable from data we already have.
Row 13 needs an off-balance-sheet-vehicle concept we do not model.

**Row structure also differs.** BoG splits liabilities into **stable deposits** (row 6) and
**volatile deposits** (row 7). We do not carry that split in the ladder. Volatile is
precisely defined by the directive — see §5.

---

## 4. Finding B — funding concentration uses the wrong selection rule

We produce **Top-10 depositors**. The directive asks for three different things, and Top-10
is none of them:

- **Table 5** — every counterparty accounting for **more than 1% of total assets**, named,
  with amount, percentage of total liabilities, and an **intragroup/related-party Yes/No
  flag**. Plus separate totals for **Top 20** and **Top 100** depositors, and each as a
  percentage of total deposits.
- **Table 7** — Top 20 depositors and each significant counterparty broken across **five
  maturity buckets** (<1, 1–3, 3–6, 6–12, >12 months).
- **Table 8** — deposit funding concentration across **nine buckets**, split into: funding
  from associates, twenty largest depositors, twenty largest *financial institution*
  balances, twenty largest *government and parastatal* balances, and negotiable paper
  funding instruments (with "of which ≤12 months" and "of which >5 years" sub-rows).

"Top N by size" and ">1% of total assets" are different populations — a bank may have three
counterparties over 1%, or thirty. Both are required.

**A netting rule we do not implement.** ¶23: deposits from top-20 or top-100 customers *that
have been used to secure a loan* must be **deducted from both the numerator and the total
deposit denominator**. That needs a deposit→collateral/lien link we do not currently carry.

**A definition we can implement today.** "Significant counterparty" = >1% of total balance
sheet, using the **Large Exposures Directive's** definition of connected counterparties
(footnote 4) — which is the same grouping our `LE-MONTHLY` return already needs. That is
shared work, not duplicated work.

---

## 5. Finding C — the prudential ratios are closer than our note implies

Our code says the canonical data "carries no prudential-ratio series." Having read Table 1,
that framing is slightly off in a useful way: **BoG does not want a series.** Table 1 wants
six inputs for the reporting month and the previous month, from which eight ratios are
computed:

Narrow Liquid Assets · Broad Liquid Assets · Volatile Liabilities · Total Deposits ·
Short-term Liabilities · Total Assets

The directive defines each precisely (¶5 definitions), and the ratio thresholds for **banks**
are:

| Ratio | Narrow | Broad |
|---|---|---|
| to Volatile Liabilities | 80% | 100% |
| to Short-Term Liabilities | 50% | 70% |
| to Total Assets | 30% | 50% |
| to Total Deposits | 60% | 80% |

(SDI thresholds differ and are also specified — and note ¶9: **for SDIs these are binding
compliance ratios, not monitoring tools.** If we ever serve Savings & Loans or Finance
Houses, Table 1 becomes a hard limit, not a report.)

**What we would still need.** Narrow Liquid Assets requires classifying: notes and coins;
unencumbered correspondent balances at **non-resident** FIs held for **operational purposes**
and readily withdrawable; placements with non-resident FIs **rated AAA**; balances at BoG;
unencumbered GoG T-bills and BoG bills ≤1 year; unencumbered sovereign/central-bank/MDB
securities ≤1 year **redeemable within two working days**; claims on other domestic banks.

Against our canonical model we have counterparty type, rating and contractual maturity. We
**do not** have: an **encumbrance flag**, a **resident/non-resident** marker, an
**operational-purpose** flag, or a **two-working-day redeemability** marker.

So Table 1 is the *most* reachable of the seven missing tables, but it is not free. The
encumbrance flag in particular is load-bearing across Tables 1, 9 and 10.

**Volatile Liabilities is trivially defined** — "all demand deposits (Current and Call
accounts)" — a product/account-type classification we can encode immediately.

**Short-term liabilities carries a rule worth noting**: current, call and savings accounts
are deemed to mature in under a year **by their nature**, regardless of stated maturity. If
our behavioural/NMD modelling assigns them longer lives, the prudential ratio must ignore
that and use the contractual-by-nature rule. Those two must not be allowed to drift.

---

## 6. Finding D — one missing concept blocks four tables

**Encumbrance.** We carry no flag distinguishing encumbered from unencumbered assets. It is
required by:

- Table 1 (Narrow and Broad liquid assets are defined as *unencumbered*)
- Table 9 (the whole table is available **unencumbered** assets)
- Table 10 (collateral received, and what is available for encumbrance)
- Table 4 (re-hypothecation — collateral we are *permitted* to re-pledge vs actually
  re-pledged)

This is a single canonical-model addition that unlocks a third of the return. It is the
highest-leverage data change identified in this review.

**Table 9's missing columns.** We produce description, HQLA level and value. BoG asks for
seven: S/No, Description, **Asset Type & Nature**, **Location**, Value in Cedi ('000),
**Estimated Haircut (%)**, **Monetized Value of Collateral** — and three separate sections
(marketable in secondary market / eligible for BOG standing facilities / by significant
currency). Haircut and monetised value are explicitly required by ¶37–38.

---

## 7. Finding E — significant currency is absent everywhere

The concept appears in Tables 6, 7, 9C and 11, and we implement it in **none** of them —
Table 11 is generated at aggregate-currency level only.

Definition (¶30, ¶41): a currency is significant if **liabilities denominated in it are ≥5%
of total liabilities**. Note ¶36 uses a *different* denominator for unencumbered assets: ≥5%
of the total available unencumbered collateral. Two thresholds, both 5%, different bases —
easy to conflate in implementation.

Table 11 names the expected columns explicitly: **Cedi, USD, Pound, Euro, Others.**

We hold currency on every position, so the split is derivable. This is a generator change
rather than a data-model change — the cheapest of the structural gaps.

---

## 8. Two apparent errors in the directive itself

Recording these because they affect implementation, and because they are worth raising with
BoG through Bernard even though the formal window has closed.

**Table 11's net cash outflow formula is inverted.** The template labels
`Total Cash outflow (1)`, `Total Cash inflow (2)`, then `B) Net Cash Outflow (2-1)`. As
written that is inflow minus outflow, which yields a negative net outflow and inverts the
LCR. It should be `(1-2)`. We should implement `(1-2)` and note the deviation.

**The concentration time buckets are internally inconsistent.** ¶31 states the concentration
metrics "shall be reported separately for the time horizons of less than one month, 1-3
months, 3-6 months, 6-12 months, and for longer than 12 months" — five buckets, which
Table 7 uses. But **Table 8 uses nine** (Total, Next day, 2–7 days, 8 days–1 month, 1–2, 2–3,
3–6, 6–12, >12 months). We should build to the templates, since those are what gets filed,
but the discrepancy is worth a question.

---

## 9. Tool (f) — Market-Related Monitoring Tools has no template

Worth stating plainly because it changes scope: the sixth tool has **no appendix template**.
¶44–46 describe BOG monitoring market-wide and institution-specific data, and say BOG
"shall request" four items from RFIs: detailed costs of secured and unsecured funding by
tenor and instrument; trends in collateral flows including stress projections; current
short-term funding spreads; and cash balances held at BOG.

That is an ad-hoc supervisory request, not a monthly grid. **We are not behind on this** —
there is nothing to build to yet. It does, however, connect directly to Bernard's "local
market intelligence" observation: funding spreads and rollover conditions are exactly the
data he says Ghanaian banks lack visibility into, and BoG is going to start asking for them.

---

## 9A. Part II (¶10–21) — governance duties that are product surface

*Added 2026-08-07, after the org-flow discussion.* The first read of this directive
focused on Part III's tools and templates; Part II turns out to carry build
implications of its own. By **31 Dec 2026** (¶9) an RFI must align "governance
arrangements" too — and several of those arrangements are things a platform can
either evidence or not:

| ¶ | Duty | Product surface |
|---|---|---|
| 11(b)–(e) | **Board sets** internal thresholds for the six tools, cumulative + per-currency mismatch limits, concentration limits — at least annually | **Threshold register** with Board-approval evidence + version history (now product.md Phase 2 item 1(h)). The tables need thresholds anyway to emit RAG findings |
| 11(g)–(h) | Board "regularly review reports… review and **challenge** results… even during periods when liquidity is abundant" | The committee record (minutes, decisions, action items linked to runs) is the challenge *evidence* — product.md Phase 3 item 6 |
| 12–16 | Senior Management implements; policies must cover funding diversity, significant counterparties, collateral pledging, per-currency management | Matches the Phase 2 tool builds; ¶16(h) collateral pledging reinforces the encumbrance flag |
| 17–19 | Framework integrated into enterprise-wide risk management; HQLA cushion; significant-counterparty/currency identification framework | The identification rules are the same ones Tables 5–8 and 11 need |
| 20–21 | **Internal Audit** reviews the framework annually, reports to Board | The Auditor persona + audit-log UI + examiner mode are regulatory requirements here, not conveniences |

Also from Part I worth pinning: **¶4 requires this directive to be read in
conjunction with** the Risk Management Directive 2021, the **Liquidity Risk
Management Directive 2026**, the **Liquidity Coverage Ratio Directive 2026**, and
the Corporate Governance Directive 2018. *Status update 2026-08-07:* the
**LRMD 2026 has been obtained and analysed** (`lrmd_gap_analysis.md` — same
exposure-draft status, same dates; it adds the quarterly LAS, the 31-March
disclosure, the CFP BoG-notification event, and mandates LTP). The **LCR
Directive 2026 is confirmed to exist** (LRMD preamble: three-directive package,
banks only) but is **not on BoG's public directives page** — pending publication
or circulated directly; obtaining it is Bernard question 7 below and an
open-decision register item.

## 10. What I would do, in order

1. **Confirm the draft is current** (Bernard). Everything else is wasted if the final
   differs.
2. **Add an encumbrance flag** to the canonical position model. Unlocks parts of Tables 1,
   4, 9 and 10 — the single highest-leverage change.
3. **Re-bucket the maturity ladder to BoG's 14 buckets.** Mechanical, uses data we hold, and
   the current aggregation is lossy so it cannot be deferred to a display layer.
4. **Build Table 1 (prudential ratios).** Highest regulatory salience — it is tool (a), it
   is binding for SDIs, and six of its inputs are near-reachable.
5. **Add significant-currency splitting** to Tables 6, 9C and 11. Generator-only change.
6. **Rebuild funding concentration** on the >1%-of-total-assets rule plus Top 20/100, reusing
   the Large Exposures connected-counterparty grouping.
7. **Tables 7, 8, 10, 3, 4** — sequenced after the above, as they depend on the same
   encumbrance, currency and counterparty foundations.

Items 2–3 are prerequisites for most of the rest; doing them first avoids building twice.

---

## 11. Questions for Bernard

1. Has the **final** LMTD been published? This draft's comment window closed 30 June 2026.
2. Is the **Table 11 `(2-1)` formula** an error, or a convention I am misreading?
3. **Table 8 vs ¶31** — nine buckets or five? Which do banks actually file?
4. Does a core banking system carry an **encumbrance flag** natively, or is it maintained
   separately by treasury? This determines whether item 2 above is an extraction change or a
   new operational process for the bank.
5. **Estimated haircut and monetised value** (Table 9) — does a bank hold these, or are they
   computed at reporting time against a BoG haircut schedule?
6. For **Table 5's intragroup/related-party flag** — where does a bank source that?

*Added 2026-08-07 — the organizational flow (for the follow-up call):*

7. **The LCR Directive 2026** — confirmed to exist (LRMD preamble: part of the
   three-directive package, **banks only**) but not on BoG's public directives page as
   of 2026-08-07. Can he obtain it, or say when it publishes? It carries BoG's own
   run-off rates and haircuts, replacing the Basel defaults our LCR currently runs on.
   *(Questions 12–16 on the LRMD itself are in `lrmd_gap_analysis.md` §9.)*
8. **Who compiles the ALCO pack at ADB** — treasury middle office or Finance? And who is
   the pack's *secretary* (chases sign-offs, records decisions)?
9. **What sections does a mid-tier Ghanaian bank's ALCO pack actually contain** — do credit
   and op-risk sections go to ALCO, or to separate committees (EXCO / Op Risk Committee)?
10. **Which officers sign BoG returns in practice** — MD + CFO? Does the pairing vary by
    return family? (Our signing supports configured officer titles; we have deliberately
    left them unset until told the real convention.)
11. **Adoption reality check** — would units realistically *contribute their sections
    in-app* (each arm signs off its piece, the pack assembles itself), or is
    export-to-their-own-format the honest year-one path?
