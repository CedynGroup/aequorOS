# LSEG trial scope — what to ask for, and what to fix first

A 30-day evaluation window to get the Refinitiv/LSEG adapter right. This document
scopes it from the code rather than from the spec, because the two disagree in a
way that would waste the window.

Vendor naming: the Refinitiv brand is retired (Eikon withdrawn 2025-06-30 →
LSEG Workspace; the platform APIs are the LSEG Data Platform, formerly RDP). The
internal vendor id stays `refinitiv` for wire/DB stability.

---

## 1. The catalog claimed a verification that does not exist (now corrected)

`backend/app/adapters/market_data/refinitiv/ric_catalog.yaml` marked **three**
scopes `supported: true` under the header *"Supported: verified against spec
§7.2"*.

`docs/market_data_adapter.md:591` introduces that section as:

> The Refinitiv RIC catalog … maps `DataScope` values to Refinitiv Instrument
> Codes (RICs) and data field references. **Illustrative excerpt:**

The catalog cites the spec as its authority; the spec calls its content an
example. The verification is circular, and these are the identifiers the code
currently treats as live:

| Scope | Identifiers treated as live |
|---|---|
| `YIELD_CURVE_GHS` | `GH1M=` `GH3M=` `GH6M=` `GH1Y=` `GH2Y=` `GH5Y=` `GH10Y=` |
| `FX_SPOT_USD_GHS` | `USDGHS=R` |
| `CREDIT_RATING_GHANA_SOVEREIGN` | `GH=` |

Field references in the same position: `TR.MidYield`, `TR.MidPrice`, the
`TR.*IssuerRating` codes. (`NG=` appears too, but correctly — inside
`CREDIT_RATING_NIGERIA_SOVEREIGN`'s `verification:` block as a candidate.)

The same defect exists on the Bloomberg side — `docs/market_data_adapter.md:496`
labels its field catalog excerpt "Illustrative" too, and `GHGGB1M Index` follows.

The other **20** scopes are correctly `supported: false, verification_required:
true`, with a `verification:` block naming tenors, fields, quota and the vendor
doc to consult. The repo's rule (§16.4 — never invent a RIC) was honoured
everywhere except the four entries that actually run.

**What was done (2026-07-31).** Both catalog headers and both spec sections now
say plainly that the structure is settled but the identifiers have never been
confirmed against a live service. Nothing was disabled: `supported: true` still
means "the adapter dispatches this scope", the fixture-backed tests still pass,
and `MARKET_DATA_PULL_ENABLED` remains off, so no data has ever been pulled with
these identifiers either way.

That is the honest minimum. The stronger option — flipping the three to
`supported: false, verification_required: true` and moving their identifiers into
the `verification:` block — is a one-line-per-entry change, but it disables the
only live-offered scopes across **both** vendors and rewrites six contract tests
(`tests/adapters/market_data/{bloomberg,refinitiv}/test_catalog.py` assert these
"match spec exactly", including quota units). Worth doing if LSEG's answer
contradicts them; not worth doing pre-emptively, since the risk being managed is
a *conversation* risk, not a runtime one.

Either way the operating rule stands: "we already pull these" is a claim the
trial should **establish**, not assume. Confirming these three is the shortest
possible round-trip that proves the whole pipe works, which is why it is week 1.

---

## 2. What the platform actually consumes today

This is narrower than the taxonomy implies, and it is the single most important
input to trial scoping.

No calculation module reads market data. Every read funnels through
`app/services/market_data.py`, and the only calculation-side consumer is
`app/services/fact_derivation.py:603-610` — **three call sites, total**:

| Call | Argument | Feeds |
|---|---|---|
| `get_yield_curve(base_ccy)` | bank's own base currency, one curve | FTP |
| `get_fx_spot(ccy, base_ccy)` | per foreign currency held | overlays `fx_rates_current` |
| `get_fx_spot_history(ccy, base_ccy)` | ≥ 30 observations (`_MARKET_FX_HISTORY_MIN_OBSERVATIONS`) | FX VaR return series |

`get_rating()` and `get_index()` exist and are wired to the `/markets` screen via
`app/features/read_market_data_views.py` — **display only**. No calculation
depends on a rating or an index today.

So the live vendor dependency is: **one curve, FX spot, FX spot history.**
Everything absent falls back to legacy `canonical_reference_rows`.

Two consequences:

- A trial that only validates those three finishes in a week and proves almost
  nothing about whether LSEG can carry the product.
- A trial scoped to all 24 taxonomy scopes cannot finish in 30 days, and would be
  scoped against unverified identifiers.

Scope the trial to the **third thing**: the data each module will need at the
point it is finished, prioritised by which modules are closest to needing it.

---

## 3. Per-module requirements, vendor-neutral

Stated as business requirements, not vendor identifiers — that is what LSEG's
solution consultant needs in order to answer, and it keeps the trial honest about
what is as-built versus designed.

**Wired today** = a calculation reads it now. **Designed** = the module exists and
will need it; the read path is not yet built.

### FTP — wired today
- Base-currency yield curve, full tenor spine. The spec's GHS curve intentionally
  omits the 36- and 84-month standard tenors (§5.2) — a curve product that cannot
  deliver arbitrary tenor points forces interpolation the bank did not choose.
- Curve vintage/as-of addressability: FTP rates must be reproducible for a past
  date, not just "latest". **This is the hardest requirement to satisfy and the
  one most likely to be a paid add-on. Ask early.**

### FX — wired today
- Spot, per pair, with a defined fixing convention.
- ≥ 30 consecutive observations of history per pair for VaR. Deeper is better;
  250 business days is the conventional 1-year VaR window.
- Designed: forwards/NDF points for the taxonomy's `FX_FORWARD_*` tenors
  (1/3/6/12M). For African crosses these are frequently NDF or simply absent.

### IRR / IRRBB — designed
- Curve as above, plus the ability to shock it (±450bp appears in the return
  set); shocks are computed internally, so the vendor need only supply the base
  curve.
- Basis curves where the bank funds in a different index than it lends.

### Liquidity (LCR/NSFR) — designed
- HQLA valuation: clean prices for government and central-bank paper the bank
  holds, plus enough security master to classify the instrument (issuer, maturity,
  coupon, currency). Security master is flagged in the catalog as the one scope
  that needs a canonical entity rather than a data update.
- Haircuts are regulatory, not vendor-supplied.

### Basel / capital — designed
- Issuer and counterparty ratings for standardised-approach risk weights. This is
  where `get_rating()` stops being display-only.
- Coverage question below is decisive: African corporate and bank counterparties
  are frequently unrated by the majors, and a rating feed that only covers
  sovereigns does not change a single risk weight.

### Forecasting — designed
- Macro series per country (policy rate, CPI, GDP, and the local-market series a
  central bank publishes).
- Forward curve points where projections extend past the observable spine.

### Cross-cutting, all modules
- **Point-in-time / as-of retrieval on everything.** Regulatory `input_hash` is
  value-based and official runs must be reproducible years later. A feed that only
  answers "what is it now" cannot support a reproducible filing.
- **Source attribution and a refresh timestamp per datum** — the platform stamps
  staleness and source on every view, and cross-source disagreement is resolved at
  read time by most-recent-refreshed.

---

## 4. The taxonomy has to be parameterised before the trial, not after

`app/adapters/market_data/scope_taxonomy.py` enumerates Ghana into the type
system: `YIELD_CURVE_GHS`, `FX_SPOT_USD_GHS`, `FX_FORWARD_USD_GHS_{1,3,6,12}M`,
`SECURITY_MASTER_GOG_*`, `CREDIT_RATING_GHANA_SOVEREIGN`, `MACRO_GHANA_*`,
alongside `YIELD_CURVE_{USD,EUR,GBP,NGN,KES,ZAR}`.

Adding Nigeria today means adding roughly a dozen enum members and a catalog entry
each. That directly contradicts the jurisdiction-is-data rule the rest of the
platform already follows (`banks.jurisdiction_code`, the `jurisdictions` registry).

Replace the enum with a parameterised scope — category × parameters:

```
CURVE(currency)                  FX_SPOT(base, quote)
FX_FORWARD(base, quote, tenor)   RATING(issuer)
SECURITY_MASTER(issuer_class, country)
MACRO(country, series)           INDEX(identifier)
```

The catalog then maps *(category, parameters) → vendor identifier*, which is the
shape it already effectively has. Enabling a market stays a data change, as the
catalog header promises.

Do this **before** the trial: the trial's whole output is verified identifiers,
and they should land in the structure that will hold them.

---

## 5. Trial plan, 30 days

**Week 1 — prove the pipe, un-claim the unverified.**
Flip the four scopes to `verification_required`. Establish credentials and the
access mechanism. Then re-verify those same four identifiers against the live
service. Success criterion: a `pull_runner.execute_pull` cycle that writes
canonical state from a real response, and a Ghana curve whose values a treasurer
recognises.

**Week 2 — breadth across markets, not depth in one.**
Ghana, Nigeria, Kenya, South Africa: curve + FX spot + FX history for each. This
is the coverage question the whole Africa-first positioning rests on, and it is
better to learn in week 2 that Kenyan curve coverage is thin than in month six.
Land the parameterised taxonomy alongside.

**Week 3 — the hard requirements.**
Point-in-time retrieval. FX forwards/NDFs. Rating coverage for actual bank and
corporate counterparties, not just sovereigns. Security master for government
paper. Expect the first real "no" here — that is the point of doing it inside the
window.

**Week 4 — commercial and operational reality.**
Quota and rate limits against a realistic multi-tenant refresh pattern. Failure
and staleness behaviour. Pricing for what weeks 2–3 proved necessary. Write up
what LSEG covers, what it does not, and what the fallback is per gap.

Throughout: no identifier enters the catalog as `supported: true` without a live
response behind it. Raw vendor errors and fields must never reach bank-facing
surfaces — the contract suite's leak canary enforces this, and it should stay
green every day of the trial.

---

## 6. Questions for LSEG, ordered by what would most change the design

1. **Point-in-time.** Can every series be retrieved as-of a past date, with the
   value as it stood then — not the restated value? Is that the standard
   entitlement or a separate product? *(If no: official-run reproducibility needs
   another answer, and that is an architectural problem, not an integration one.)*
2. **Redistribution.** We are a multi-tenant SaaS. Banks see vendor-derived values
   in our UI and in regulatory filings, and we persist the data. Which of those
   is redistribution under your licence, and what does per-tenant entitlement
   look like? *(See §7 — likely the binding constraint.)*
3. **African coverage, concretely.** For Ghana, Nigeria, Kenya, South Africa:
   which curves exist, at which tenors, from what source, with what history depth?
   Where is it a real market curve versus a modelled one?
4. **Ratings depth.** Beyond sovereigns — coverage for African banks and
   corporates. What fraction of a typical Ghanaian bank's counterparties would be
   rated? *(If thin, Basel standardised-approach automation is not a vendor
   problem to solve.)*
5. **FX forwards / NDFs** for USD/GHS, USD/NGN, USD/KES at 1/3/6/12M — real
   quotes or interpolated?
6. **Government security master** for African issuers: coverage, identifiers,
   corporate-action handling.
7. **Access mechanism and quota.** Which API for server-side scheduled pulls (no
   desktop in the loop), and what are the request limits against a pattern of N
   tenants × daily refresh?
8. **Failure semantics.** What is returned for a stale, halted or absent
   instrument, and is that distinguishable from an error? *(Our staleness
   surfacing depends on telling those apart.)*
9. **Identifier authority.** Where is the authoritative RIC and field
   documentation for these markets, so future scopes are enabled from a document
   rather than from a support conversation?

Question 9 is the one that keeps paying after the trial ends.

---

## 7. Licensing will bind harder than the integration

Worth saying plainly because it is the risk most likely to be discovered late.

Market-data licences are typically priced and permissioned per user or per
application, with redistribution — showing vendor-derived values to a third party
— as a separate and more expensive right. AequorOS does three things that push
against that at once: it persists vendor data, it serves derived values to
multiple tenant institutions, and those values end up inside filings sent to a
regulator.

A trial that proves the technology and ignores this produces an adapter that
cannot be switched on commercially. Raise question 2 in the first call, and get
the answer in writing before week 3.

There is also an architectural hedge worth keeping open: for several African
markets the central bank publishes the authoritative curve and FX fixings itself,
free and without redistribution restrictions. Where that is true, a vendor feed is
convenience rather than necessity — and knowing which markets those are changes
the negotiating position. The adapter layer already makes this a per-scope choice
rather than a per-vendor one.

---

## 8. What this document does not claim

I did not verify any RIC or field name against LSEG documentation or a live
service — none is available here, and inventing plausible identifiers is exactly
the failure mode §16.4 exists to prevent. Every identifier named above is quoted
from the repository, including the ones §1 argues are unverified.

The per-module requirements in §3 marked *designed* are read from module scope and
the taxonomy, not from executing code — by definition, since the read paths are
not built. The three *wired today* entries are from `fact_derivation.py:603-610`
and are exact.
