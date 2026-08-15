# AequorOS Implied Credit Rating & Probability of Default — Implementation Specification

*Build specification for the implied bank credit rating and PD capability. Audience: engineering (Dela), quant review (Eric), and the tokenized-repo partner who will consume the outputs. This document carries the full methodology, mathematics, models, governance, validation, and the bank-facing explainability layer. It is written to be defensible to a bank's model-risk function and a regulator, per SR 11-7.*

**Status:** design specification. All parameter *values* (weights, thresholds, transform anchors, confidence levels, MoC multipliers) are to be calibrated by AequorOS against real data before production; this document fixes the *structure, mathematics, and governance*, not the production numbers.

---

## 0. Design principles (non-negotiable)

1. **Glass box, not black box.** Every rating must be explainable to the bank it describes: which ratios drove it, how much each contributed, why the band is as wide as it is. This is a product requirement, not a nicety — a synthetic rating a bank cannot interrogate is a synthetic rating a bank will not trust.
2. **Ordinal first, cardinal second.** The rank-ordering (this bank is stronger than that one) is defensible from financials today. The probability (this bank's one-year PD is x%) is not, because of the low-default problem. The two are produced by separate stages so their very different reliability is never conflated.
3. **Uncertainty is a first-class output.** A PD is published as a conservative band with a stated confidence level and an explicit margin of conservatism, never as a false-precision point.
4. **The sovereign is a hard constraint, not a covariate.** A Ghanaian bank cannot be assessed as materially safer than Ghana, and the 2022 default plus the DDEP proved sovereign risk is the dominant systematic driver. It enters as a ceiling and as the systematic factor in the correlation model.
5. **Governed methodology, versioned parameters.** Same discipline as the market-data desk: the methodology and its parameters are versioned; every rating is reproducible from (parameter version × input vintage); a methodology change is a controlled, documented event, not a silent edit.
6. **Conservative into money.** Where an output feeds a repo haircut or a counterparty limit, use the conservative end of the band, not the midpoint, so model uncertainty is priced in rather than assumed away.

---

## 1. Architecture: the three-stage pipeline

```
Financial statements (governed, lineage-tracked)
        │
        ▼
[STAGE 1]  Component scorecard  ─────────►  standalone financial-strength score
   CAMELS + agency ratios, monotonic transforms, operating-environment matrix
        │
        ▼
[STAGE 2]  Standalone → implied rating
   aggregate score → master-scale grade → sovereign ceiling → support notching
        │
        ▼
[STAGE 3]  Implied rating → PD band
   grade→PD master scale → Pluto–Tasche upper bound → Margin of Conservatism → Basel floor
        │                         │
        ▼                         ▼
   Ordinal rating (headline)   PD band [lower, point, upper] + confidence level + PIT/TTC
        │
        ▼
[EXPLAINABILITY LAYER]  per-rating explanation + bank-facing tooltips
        │
        ▼
[CONSUMERS]  EWI dashboard · counterparty analytics · repo haircuts & limits
```

Each stage writes an immutable, versioned record. Given the methodology version and the input snapshot, the entire chain is exactly reproducible — the same invariant the risk engines already satisfy for IRRBB/Basel as-of runs.

---

## 2. Data inputs and conventions

### 2.1 Inputs (all already computed or ingested by AequorOS)
| Component | Ratios | Source in platform |
|---|---|---|
| Capital | CET1 ratio; Tier 1 leverage ratio; total CAR; tangible common equity / RWA | Basel Capital module |
| Asset quality | problem loans / gross loans; provision coverage; IFRS 9 stage-2 & stage-3 %; loan concentration; loan growth | Data engine, IFRS 9 staging |
| Earnings | ROA (net income / tangible assets); ROE; net interest margin; cost-to-income | Financials |
| Liquidity | LCR; NSFR; loan-to-deposit; market-funds / tangible banking assets; liquid assets / tangible banking assets | Liquidity module |
| Sensitivity | IRRBB EVE sensitivity / Tier 1; FX net open position / capital | IRRBB, FX modules |
| Environment | Ghana operating-environment / macro score; sovereign rating & PD | Market-data desk (sovereign), macro overlay |

### 2.2 Input conventions (anti-manipulation, from Moody's practice)
- **Problem-loan and profitability ratios:** use the **weaker of the three-year average and the latest annual figure**. Prevents flattering the score on a single good year.
- **Capital ratios:** use the **latest** figure (capital position is point-in-time relevant).
- **Period-end window dressing guard:** flag ratios that move sharply only at reporting dates.
- **Accounting basis:** normalize IFRS vs local GAAP; carry DDEP restatement flags so restructuring losses are not double-counted or missed.
- **Default/credit-event definition (fixed, see §12):** 90 days past due **OR** unlikeliness-to-pay **OR** distressed restructuring/recapitalization.

---

## 3. Stage 1 — the component scorecard

### 3.1 Structure
Six components (CAMELS). Within each, 2–4 ratios. Each ratio is mapped to a common sub-score scale by a **monotonic transform**; sub-scores combine to a component score; component scores combine by weight to the standalone score. This mirrors the agency architecture (Moody's BCA solvency/liquidity factors; S&P SACP factors; Fitch VR key rating drivers).

### 3.2 Component weights (starting template — Fitch VR, the published and citable scheme)
| Component | Weight | Fitch core metric analogue |
|---|---|---|
| Capitalisation & Leverage | **25%** | core capital ratio (CET1/RWA) |
| Asset Quality | **20%** | impaired loans / gross loans |
| Funding & Liquidity | **20%** | loans / customer deposits |
| Business Profile | **20%** | total operating income (scale/stability) |
| Earnings & Profitability | **15%** | operating profit / RWA |
| Risk Profile | **10%** | qualitative attributes |

*Rationale for adopting Fitch's weights as the base:* they are published (unlike Moody's grid weights, which are not disclosed), so the methodology can cite them; capital is the single heaviest factor, consistent with all three agencies and with the DDEP lesson that capital buffers are what absorb sovereign shocks. Weights are a **versioned parameter** — calibratable, but every change is a Track-2 methodology event (§9).

### 3.3 Ratio → sub-score transform
Each ratio *r* maps to a sub-score *s ∈ [0, 1]* (1 = strongest) via a monotonic, bounded transform. Two admissible forms (choose per ratio, version the choice):

**(a) Piecewise-linear against anchor thresholds** (interpretable, agency-style two-factor matrix):
```
s(r) = clamp( (r − r_floor) / (r_cap − r_floor), 0, 1 )      for "higher is better" ratios
s(r) = clamp( (r_cap − r) / (r_cap − r_floor), 0, 1 )        for "lower is better" ratios (e.g. NPL)
```
where `r_floor`, `r_cap` are versioned anchors (e.g. CET1 floor 6.5%, cap 20%).

**(b) Logistic transform** (smooth, RiskCalc-style capture of non-linearity):
```
s(r) = 1 / (1 + exp(−a·(r − b)))        (sign of a set by ratio direction)
```
`a` (steepness) and `b` (midpoint) versioned per ratio. Use (b) where the marginal information of a ratio is non-linear (e.g. NPL: the move from 3%→8% matters far more than 25%→30%).

### 3.4 Operating-environment matrix (the macro overlay)
Following Fitch's two-factor matrices and Moody's Macro Profile: the **same ratio sub-score is adjusted by the operating-environment score**, because a given CET1 ratio is worth less in a fragile system. Implement as a 2-D lookup / bilinear interpolation:
```
adjusted_subscore = M( ratio_subscore , operating_environment_score )
```
where `operating_environment_score` for Ghana is set by the desk (banking-system risk + credit conditions) and `M` is a versioned matrix. This is where Ghana's macro fragility, and the sovereign backdrop, enter Stage 1.

### 3.5 Standalone score
```
standalone_score = Σ_c  w_c · component_score_c
component_score_c = Σ_r  v_{c,r} · adjusted_subscore_{c,r}
```
`w_c` = component weights (§3.2); `v_{c,r}` = within-component ratio weights (versioned). Output is a continuous standalone financial-strength score ∈ [0, 1].

---

## 4. Stage 2 — standalone score → implied rating

### 4.1 Master scale (ordinal grades)
Map the standalone score to a lowercase alphanumeric grade scale aligned to agency notches (the "intrinsic strength" scale, before support):
```
aaa · aa+ · aa · aa− · a+ · a · a− · bbb+ · bbb · bbb− · bb+ · bb · bb− · b+ · b · b− · ccc+ · ccc · ccc− · cc · c
```
The score→grade cutpoints are versioned parameters, calibrated so the mapping matches shadow ratings where agency ratings exist for comparable banks (§7.3).

### 4.2 Sovereign ceiling (hard constraint)
Compute the standalone grade, then apply:
```
implied_grade = min( standalone_grade , sovereign_ceiling_grade )
```
`sovereign_ceiling_grade` is derived from Ghana's sovereign rating/PD (from the market-data desk). Rare, documented exceptions (e.g. a bank with predominantly external, ring-fenced assets) may be allowed as a versioned rule, but the default is: **no bank rated above the sovereign.** Ghana's post-2022-default sub-investment-grade sovereign is a binding cap for domestic banks.

### 4.3 Support notching (parent / government)
Agencies use intrinsic-plus-support (Moody's Adjusted BCA + government support; Fitch "higher of" VR and support-driven rating). Implement:
```
issuer_grade = notch( implied_grade , support_uplift )
support_uplift = f( parent_strength , systemic_importance , sovereign_capacity_to_support )
```
Critically, **sovereign capacity to support is itself constrained by sovereign strength** — a distressed sovereign cannot credibly backstop its banks, so support uplift is capped near/below the sovereign. This is the Fitch "higher of VR vs support" logic, bounded by the ceiling in §4.2.

---

## 5. Stage 3 — implied rating → PD band (the low-default core)

This is the stage where rigor is non-negotiable, because a single emerging market yields essentially **zero bank defaults**, making naive PD = defaults/obligors meaningless.

### 5.1 Master-scale grade → PD anchor
Attach to each grade an anchor PD from external reference data (agency idealized/observed default rates by grade, sovereign-adjusted). This is the **central tendency** the model calibrates to. Grade PDs must be monotonic (worse grade ⇒ higher PD) and floored (§5.5).

### 5.2 Pluto–Tasche most-prudent estimation (the primary LDP method)
For grades ordered `p_A ≤ p_B ≤ … ≤ p_K` (best to worst), the **most-prudent** upper-confidence-bound estimate assumes, for grade *g*, that its PD equals that of all worse grades pooled. With confidence level γ and **zero observed defaults**:

For the best grade A (pooling all obligors n_A + n_B + … + n_K = N):
```
1 − γ ≤ (1 − p_A)^N        ⇒        p̂_A = 1 − (1 − γ)^(1/N)
```
For a general grade *g*, pool obligors in *g* and all worse grades (n_g^+ = Σ_{j≥g} n_j):
```
p̂_g = 1 − (1 − γ)^(1 / n_g^+)
```

**Worked example (Pluto–Tasche Table 1), zero defaults, n_A=100, n_B=400, n_C=300 (N=800):** the estimated upper-bound PD for the best grade A across confidence levels is:

| γ | 50% | 75% | 90% | 95% | 99% | 99.9% |
|---|---|---|---|---|---|---|
| p̂_A | 0.09% | 0.17% | 0.29% | 0.37% | 0.57% | 0.86% |

(Higher confidence ⇒ higher, more conservative PD; smaller samples ⇒ higher PD.) The Pluto–Tasche paper advises γ **below 95%** as more appropriate; **recommend γ = 90%** as the production default, versioned.

**Few defaults (not zero):** replace the equality with the binomial tail — p̂_g solves
```
1 − γ = Σ_{k=0}^{d_g^+}  C(n_g^+, k) · p^k · (1 − p)^(n_g^+ − k)
```
where d_g^+ = defaults in grade g and worse. **Correlated defaults:** extend via the one-factor Vasicek model (§6) — integrate the conditional binomial over the systematic factor, which widens the bound (correlation reduces effective sample size).

### 5.3 Bayesian alternative (parallel estimate, reconciled)
Place a prior on each grade PD (expert-informed from agency data, or uninformative), update on the sparse data, and take a posterior upper quantile. Advantages: yields a full posterior (natural bands), no arbitrary γ. Use as a **challenger** to Pluto–Tasche; where they diverge materially, the wider (more conservative) governs, and the divergence is documented.

### 5.4 Margin of Conservatism (MoC) — required add-on
Per Basel II ¶451 ("a margin of conservatism … related to the likely range of errors … larger where methods and data are less satisfactory") and the EBA MoC framework, add an explicit MoC. Practical **k-sigma** form:
```
PD_final = PD_estimate + k · σ(PD_estimate)
```
`σ(PD_estimate)` from the estimator's distribution (binomial/Vasicek variance or posterior SD); `k` versioned (start k = 1). Categorize the MoC by deficiency source (data, methodology, general estimation error) and document each — this is an EBA expectation.

### 5.5 Basel PD floor
```
PD_final = max( PD_final , 0.03% )
```
Applies to all grades except sovereign/where regulation dictates otherwise. Hard-coded.

### 5.6 PIT vs TTC (dual output)
- **PIT (point-in-time):** reflects current conditions; aligns to current observed default rates; used for **live repo pricing/haircuts** and IFRS-9-style views.
- **TTC (through-the-cycle):** long-run average, cycle-smoothed; used for **counterparty limit-setting** (less procyclical).
Produce both; label every published PD as PIT or TTC. Given Ghana's macro volatility, the PIT–TTC gap will be material and must be shown, not hidden.

### 5.7 The published PD band
```
PD_lower   = grade anchor (central tendency), floored
PD_point   = calibrated estimate (PIT or TTC), floored
PD_upper   = Pluto–Tasche/Bayesian upper bound + MoC, floored
```
Always publish all three plus the confidence level γ. **Repo haircuts and limits size off `PD_upper`.**

---

## 6. The sovereign & systematic-risk model

### 6.1 One-factor Vasicek (sovereign as the systematic factor)
Model each bank's latent asset return as
```
A_i = √ρ · Y  +  √(1 − ρ) · ε_i
```
where **Y is the systematic factor identified with Ghana sovereign risk**, ε_i idiosyncratic, ρ the asset correlation. Conditional PD given a sovereign stress realization Y:
```
p_i(Y) = N[ ( N^{-1}(PD_i) − √ρ · Y ) / √(1 − ρ) ]
```
Set ρ **elevated** relative to developed-market defaults, because Ghanaian bank fortunes are tightly coupled to the sovereign (shared government-security holdings, shared macro). ρ is versioned and is one of the most consequential parameters — treat like the cointegration β in the curve work: re-estimated only as a governed event.

### 6.2 DDEP-style sovereign stress (mandatory scenario)
Every bank is stressed for a sovereign-restructuring shock calibrated to the observed DDEP: apply an NPV haircut (~30% average, per IMF Country Report 23/168) to sovereign-security holdings, flow the loss through capital, and re-rate. A bank whose capital cannot absorb the modeled haircut is flagged and (for repo) rendered ineligible or punitively haircut. This operationalizes the 2022–23 lesson that "risk-free" domestic government paper was the primary loss driver, impairing 22 banks by an estimated GH¢37.7bn and technically rendering some insolvent.

### 6.3 Wrong-way risk (for repo)
Because a Ghana sovereign event hits **collateral value and counterparty PD simultaneously**, the repo layer must carry concentration limits on counterparties with heavy sovereign exposure and must not net sovereign-correlated collateral against sovereign-correlated counterparty risk. Flagged explicitly to the repo partner.

---

## 7. Statistical models (Stage-2 recalibration engines)

These are the engines that *recalibrate* the scorecard once outcome data accrue (Stage 2 of the roadmap). Until then, Stage 1's expert-anchored transforms stand in.

### 7.1 Logistic-regression scorecard (workhorse)
```
PD = 1 / (1 + exp( −(β_0 + Σ_j β_j · x_j) ))
```
`x_j` = transformed ratios (WoE or the §3.3 sub-scores). Interpretable coefficients, regulator-familiar, outputs probability directly. Primary recalibration model.

### 7.2 Shumway (2001) discrete-time hazard model (correct panel form)
Single-period models are biased when a firm is observed over multiple years. The hazard model is estimable as a **pooled logit over all bank-year observations of surviving banks**, with time-varying covariates:
```
P(default in year t | survived to t) = 1 / (1 + exp( −(α + β' x_{i,t}) ))
```
This is the statistically correct form for AequorOS's accumulating panel of bank-years and is the target Stage-2 engine. Shumway found roughly half the classic accounting ratios lose significance once dynamics/market variables are included — so re-select variables on local data, do not assume the developed-market set.

### 7.3 Altman Z''-score (emerging-market) — cross-check only
```
Z'' = 3.25 + 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4
X1 = working capital / total assets
X2 = retained earnings / total assets
X3 = EBIT / total assets
X4 = book value of equity / total liabilities
```
Zones: Z'' > 2.6 safe · 1.1–2.6 grey · < 1.1 distress. The +3.25 constant standardizes a zero score to a D-rated bond; designed by Altman for non-manufacturers and emerging markets lacking local default data. **Caveat:** built on non-financial firms; working-capital/leverage ratios map poorly to banks. Use as a **benchmark cross-check**, never the bank engine.

### 7.4 Merton / KMV distance-to-default — indirect overlay only
```
DD = [ ln(V/D) + (μ − ½σ_V²)·T ] / (σ_V · √T)
PD_structural = N(−DD)
```
Requires asset value V and asset volatility σ_V, inferred from **traded equity** — which mid-tier Ghanaian banks lack. KMV replaces N(−DD) with an empirically calibrated EDF (e.g. a DD ≈ 3.3 historically defaulting near ~0.4%, vs the theoretical ~0.04%). **Use only indirectly**: a listed-peer or sovereign distance-to-default can serve as a systematic-factor input to §6, not as the per-bank engine.

---

## 8. Governance & model risk management (SR 11-7)

### 8.1 The three validation elements (SR 11-7)
1. **Conceptual soundness** — documented design, theory, variable selection, and developmental evidence for every choice (why these ratios, these weights, this transform, this γ, this ρ). Expert-judgment components are models too and are documented as such.
2. **Ongoing monitoring** — correct implementation; continued performance; process verification; benchmarking against the Altman/agency cross-checks.
3. **Outcomes analysis** — back-testing predicted ratings/PDs against realized distress events as they occur.

### 8.2 Three lines of defense
- **1st line:** the desk/quant building and running the model.
- **2nd line:** independent model validation ("effective challenge") — separate from development.
- **3rd line:** audit of the framework.
For a two-founder company, the "independent validation" can start as a documented internal challenge by the non-builder plus an external review before the outputs feed live repo decisions; it must not be skipped where money rides on the number.

### 8.3 Versioned methodology register (reuse the market-data pattern)
For the rating/PD model, store: current methodology version; the full parameter set (component weights `w_c`, ratio weights `v_{c,r}`, transform anchors, operating-environment matrix `M`, master-scale cutpoints, grade→PD anchors, γ, MoC k, ρ, sovereign-ceiling rule, support rules); effective date; approval record; change history. The scoring run **reads** from the register; a change **writes** a new version under controlled approval. Every published rating carries the methodology version that produced it. This is what makes the on-chain repo parameters auditable.

### 8.4 Change control (Track 1 vs Track 2)
- **Track 1 (routine):** run the fixed methodology on new financials; analyst confirms correct application; supervisor approves/publishes. High-frequency, two-person.
- **Track 2 (methodology change):** any change to weights, transforms, γ, MoC, ρ, or the master scale is a documented, higher-bar, effective-dated event; history is never silently altered (corrections create new asserted versions; bitemporal store).

---

## 9. Validation metrics (with formulas)

### 9.1 Discrimination (rank-ordering quality)
- **AUC / ROC:** probability a randomly chosen defaulter is ranked riskier than a randomly chosen non-defaulter. Target AUC > 0.7 (acceptable), > 0.8 (strong).
- **Accuracy Ratio / Gini:**
```
AR (Gini) = 2 · AUC − 1
```
These behave best under scarce defaults (they need ranking signal, not many defaults) and are the primary metrics while defaults are near-zero.

### 9.2 Calibration (are the PD levels right)
- **Binomial test (per grade):** under H₀ that true PD = p, with k defaults in n obligors,
```
z = (k − n·p) / √( n·p·(1 − p) )        (normal approx; exact binomial for small n)
```
- **Hosmer–Lemeshow (across grades):**
```
χ² = Σ_g  ( O_g − n_g·p_g )² / ( n_g·p_g·(1 − p_g) )
```
`O_g` observed defaults, `p_g` predicted PD in grade g; compare to χ² with (grades − 2) df.
- **Traffic-light approach:** green/amber/red by the deviation of observed from predicted default rate per grade.
**Honesty requirement:** these tests **assume default independence and have low power in low-default portfolios** — report them, but state their limits and supplement with benchmark/expert review. Do not present a "green" calibration result from three data points as strong evidence.

### 9.3 Stability
- **Population Stability Index (PSI):**
```
PSI = Σ_b ( a_b − e_b ) · ln( a_b / e_b )
```
`a_b`, `e_b` = actual vs expected proportion in score-bucket b. Rule of thumb: < 0.10 stable · 0.10–0.25 shift · > 0.25 significant shift. Run on score distributions and on each input ratio.

---

## 10. Bank-facing explainability layer (trust architecture)

A synthetic rating is only trusted if the bank can see inside it. Every published rating ships with a structured explanation and interactive tooltips. This is a **product requirement**.

### 10.1 Per-rating explanation object (data the UI renders)
```
{
  rating_grade:        "bb−",
  pd_band:             { lower: 1.1%, point: 2.3%, upper: 4.7%, confidence: 90%, basis: "PIT" },
  standalone_grade:    "bb",
  sovereign_ceiling:   "b+",              // note: ceiling did NOT bind here (bb− < b+? show logic)
  ceiling_applied:     false,
  support_uplift:      0,
  component_contributions: [               // signed contribution to the standalone score
    { component: "Capitalisation & Leverage", weight: 25%, score: 0.72, contribution: +0.18, top_ratio: "CET1 14.2%" },
    { component: "Asset Quality",             weight: 20%, score: 0.41, contribution: −0.06, top_ratio: "NPL 11.3%" },
    ... ],
  key_drivers_up:   ["Strong CET1 headroom", "Stable deposit funding"],
  key_drivers_down: ["Elevated NPLs vs peers", "Thin earnings buffer"],
  methodology_version: "v1.4.0",
  as_of:               "2026-06-30",
  input_vintage:       "FY2025 + H1-2026"
}
```

### 10.2 Tooltip copy (production text — plain, honest, non-technical where the reader is non-technical)

**On the rating grade (primary tooltip):**
> *"This is AequorOS's implied credit assessment of [Bank], derived only from your reported financial statements using a documented, transparent methodology modeled on the frameworks published by Moody's, S&P, and Fitch. It is an indicative internal assessment to help you benchmark and monitor your standing — it is not a credit rating issued by a licensed rating agency, and it is not investment advice. See 'How this is calculated' for the full methodology."*

**On the PD band (why a range, not a number):**
> *"We show a range, not a single number, on purpose. Bank defaults are rare in Ghana, so there is very little historical default data to calibrate an exact probability against. Rather than imply a precision we don't have, we publish a conservative range: the upper figure ([4.7%]) is a deliberately cautious estimate at a [90%] confidence level, with an added margin for data limitations. Decisions that put capital at risk (such as collateral terms) use the upper, most conservative figure. [Learn how the range is built.]"*

**On the sovereign ceiling:**
> *"A bank's assessment is anchored to the strength of the country it operates in. Because Ghana's own credit standing constrains what any Ghanaian bank can be assessed at, no bank is rated above the sovereign except in narrowly defined cases. This reflects a real risk demonstrated in 2022–23, when the domestic debt restructuring (DDEP) imposed large losses on banks holding government securities. [See how the sovereign affects your assessment.]"*

**On a component (e.g. Asset Quality dragging the score):**
> *"Asset Quality contributed −0.06 to your standalone score this period. The main driver is your NPL ratio of 11.3%, which is elevated relative to the scoring band. This component is weighted 20%. Capital (weighted 25%) remains your strongest component. Improving problem-loan coverage would move this the most."*

**On the confidence band width:**
> *"The width of the range reflects how much data supports the estimate. A wider band means more uncertainty — usually from limited default history in this market. As more data accumulates over time, we expect these ranges to narrow. We would rather show an honest wide range than a precise number we cannot defend."*

**On 'indicative vs agency rating' (persistent disclaimer):**
> *"Indicative assessment. Produced by AequorOS from your financial statements using a versioned, documented methodology (v[1.4.0]). Not a rating-agency rating. Reproducible: the same inputs and methodology version always produce the same result."*

### 10.3 "How this is calculated" full-methodology view (linked from every tooltip)
A plain-language walkthrough of: the six components and their weights; that each ratio is scored and adjusted for Ghana's operating environment; that the standalone score becomes a grade; that the sovereign caps it; that the PD is a conservative range because defaults are rare (Pluto–Tasche, in plain words: "the fewest defaults consistent with the data, at a chosen confidence level, plus a safety margin"); and the governance (versioned methodology, two-person approval, independent validation). Include a downloadable methodology document for the bank's own model-risk team.

### 10.4 Design guardrails on the bank-facing side
- Never surface a bare point PD without its band and the "indicative" label.
- Always show which methodology version produced the rating and let the bank retrieve the exact inputs used (lineage).
- For a bank viewing **its own** unflattering assessment, frame as a private early-warning/benchmarking tool it controls (tie to the EWI dashboard), not a public scarlet letter. Counterparty views (a bank assessing *others*) are commercially cleaner.

---

## 11. Default / credit-event definition (fixed)

Because legal bankruptcy is rare and support/forbearance is common, define the credit event explicitly and version it:
```
default = ( 90+ days past due on a material obligation )
        OR ( unlikeliness to pay — regulatory intervention, forbearance )
        OR ( distressed restructuring / DDEP-style exchange with economic loss )
        OR ( recapitalization triggered by insolvency )
```
This definition is what §5's PD estimates and §9's back-tests are measured against. Ambiguity here silently corrupts every downstream number, so it is documented and change-controlled.

---

## 12. Staged implementation roadmap

**Stage 1 — Defensible expert-anchored scorecard (now).**
Ship the CAMELS/agency scorecard (§3), master-scale mapping and sovereign ceiling (§4), and an indicative PD band via Pluto–Tasche + MoC + Basel floor (§5), calibrated using shadow ratings (replicate any agency-rated Ghanaian/regional banks), agency default-rate anchors, and regional benchmarks. Ship the full explainability layer (§10). Fully defensible today despite zero internal defaults. *Advance when:* methodology documented and independently reviewed for conceptual soundness.

**Stage 2 — Statistical recalibration (12–36 months).**
As the panel of bank-years and distress/near-miss events accrues (regulatory breaches, DDEP-type impairments, recapitalizations as default proxies), fit the Shumway hazard/logit (§7), re-estimate transforms/weights on local data, tighten the bands, refine the existing PIT/TTC calibration, and stand up an ML challenger for benchmarking only. *Advance when:* enough events for statistically meaningful discrimination testing.

**Stage 3 — Full IRB-grade PD (mature).**
Calibrate to observed central tendencies, run full discrimination/calibration/stability back-testing, narrow the MoC. *Trigger:* sufficient default/distress observations for meaningful central-tendency calibration.

---

## 13. Repo integration (how the outputs are consumed)

- **Haircuts** — on both the collateral and the counterparty's own credit; size off `PD_upper` (§5.7), not the point, so uncertainty is priced conservatively. Higher implied PD ⇒ higher haircut.
- **Counterparty limits** — exposure tiers by rating grade (TTC PD for stability).
- **Eligibility gates** — below a grade / above a PD ⇒ ineligible or punitive terms; a bank failing the DDEP stress (§6.2) is gated.
- **Wrong-way-risk controls** — sovereign-concentration limits; no netting of sovereign-correlated collateral against sovereign-correlated counterparty risk (§6.3).
- **Auditability** — the versioned methodology and reproducible lineage make on-chain haircut/limit parameters defensible to the counterparty and any regulator; expose the methodology version alongside the parameter feed.

---

## 14. Failure modes & pitfalls (document and monitor)

- **False precision** — publishing a point PD a zero-default portfolio cannot support. (Mitigated by mandatory bands + MoC.)
- **Overfitting** on tiny samples. (Mitigated by expert-anchored Stage 1; ML only as challenger.)
- **Ignoring sovereign correlation** — the DDEP failure mode; treating government paper as risk-free. (Mitigated by §6 ceiling + stress + elevated ρ.)
- **Accounting non-comparability** — IFRS vs local GAAP, DDEP restatements, window dressing. (Mitigated by §2.2 conventions + lineage.)
- **Look-ahead / survivorship bias** in the bank panel. (Guard in data assembly.)
- **Calibration drift** in high-inflation regimes. (Monitored by PSI + PIT/TTC split.)
- **Low-power calibration tests read as strong evidence.** (Mitigated by honest reporting of test limits, §9.2.)
- **Buyer trust** — a bank resenting an unflattering self-assessment. (Mitigated by framing as private EWI tool, §10.4.)

---

## 15. Caveats & open items (verify before production)

- **Moody's sub-factor weights are not published**; the "25/25/12.5" style figures are unofficial reconstructions. Fitch's 25/20/20/20/15/10 weights **are** published (Criteria Essentials) and are the citable base. Agency methodologies also change (Moody's updated its bank-liquidity ratios in Nov 2025) — re-verify before pinning parameters.
- **Structural (Merton/KMV) models are unavailable** for un-listed mid-tier banks; usable only as an indirect systematic overlay.
- **Calibration tests have low power** in LDPs and assume independence; interpret cautiously.
- **"Default" is ambiguous** in a support regime; the §11 definition must be fixed and documented before back-testing.
- **Some Ghana figures are secondary-sourced** (GH¢37.7bn DDEP bank losses and coupon/maturity terms from the Atuahene/Frimpong/Agyei autopsy and MoF announcements; ~30% NPV reduction from IMF Country Report 23/168). Confirm against primary Bank of Ghana sources before these drive live parameters.
- **All parameter values here are placeholders** — component/ratio weights, transform anchors, the operating-environment matrix, master-scale cutpoints, grade→PD anchors, γ, MoC k, and ρ must be calibrated on real data and independently reviewed before any output feeds a live repo decision.
- **Independent validation before money** — the model may inform internal benchmarking immediately, but must clear independent validation (§8.2) before its outputs set real collateral haircuts or counterparty limits.
