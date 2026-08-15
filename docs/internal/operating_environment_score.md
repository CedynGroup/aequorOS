# Operating-Environment Score — governed, data-derived jurisdiction assessment

**Status:** building (2026-08-12). **Owner:** Market Research Desk. **Audience:** engineering + quant/model-risk.

## Why this exists

`GHANA_OPERATING_ENVIRONMENT_SCORE ∈ [0,1]` (higher = stronger banking system) is a
**jurisdiction-level systematic input** — one value for the whole Ghanaian banking
system, the same for every Ghanaian bank. It feeds the implied-rating model twice:

1. **Scorecard overlay** (§3.4 of the rating spec) — adjusts each ratio sub-score (a
   given CET1 is worth less in a fragile system).
2. **PIT systematic factor** (§6.1) — `Z = (score − neutral) / scale`; a fragile system
   (low score) makes `Z < 0`, lifting PIT above TTC via Vasicek conditioning.

Because it moves **every** Ghanaian bank's PD, it must not be a hand-dialed number.
This document specifies a **derived, versioned, maker-checker-governed** determination
so "who set it" has a defensible answer: *this formula, over these named public inputs,
approved as methodology version X* — not a matter of taste (SR 11-7 conceptual soundness).

## Model: BICRA-style, two pillars

Mirrors S&P's Banking Industry Country Risk Assessment (economic risk + industry risk,
each 1–10; here we score sub-factors 1–6, 1 = lowest risk). Every threshold, weight and
mapping is a **versioned methodology parameter**.

### Pillar 1 — Economic risk (of the economy)
| Sub-factor | Observable input | Direction | Source |
|---|---|---|---|
| Economic resilience | real GDP growth %; GDP per capita band | higher growth/income → lower risk | macro (desk-entered / ingested) |
| Economic imbalances | CPI inflation %; private-credit-to-GDP growth | higher → higher risk | macro; policy rate (MPR) already published |
| Credit risk in economy | system NPL %; private debt / GDP | higher → higher risk | banking-system aggregate (BoG) |

### Pillar 2 — Industry risk (of the banking system)
| Sub-factor | Observable input | Direction | Source |
|---|---|---|---|
| Institutional framework | regulatory-quality score (1–6, documented judgment) + sovereign rating | weaker/lower sovereign → higher risk | analyst judgment + published sovereign rating |
| Competitive dynamics | system ROA/ROE level; risk-appetite proxy | weaker earnings → higher risk | banking-system aggregate |
| System-wide funding | system loan-to-deposit %; system CAR %; external-funding reliance % | higher LtD / lower CAR / more external → higher risk | banking-system aggregate |

### Calculation
```
sub_score_i      = threshold_map_i(input_i)                 # 1..6, versioned table per input
pillar_econ      = Σ w_econ,i · sub_score_i                 # weighted mean, versioned weights
pillar_industry  = Σ w_ind,j  · sub_score_j
composite_risk   = w_E · pillar_econ + w_I · pillar_industry   # 1..6
strength_raw     = (RISK_MAX − composite_risk) / (RISK_MAX − RISK_MIN)   # → [0,1]
score            = min(strength_raw, sovereign_governor(sovereign_rating))  # cap near sovereign
```
`sovereign_governor` maps the published sovereign grade to a maximum plausible
operating-environment strength (a CCC sovereign caps the system's strength) — the
"a bank system can't be much stronger than its sovereign" rule, mirrored from the
rating model's own sovereign ceiling.

Every input, `threshold_map`, weight, `RISK_MIN/MAX`, and the governor table is a
**versioned parameter**. Auto-pulled where already published (sovereign rating, MPR);
entered by the analyst (macro + banking-system aggregates) with the one explicit
judgment sub-score (institutional framework), each captured with its value + rationale.

## Governance (maker-checker, desk-as-vendor)

- The assessment is a **governed determination**: analyst computes/reviews → supervisor
  (≠ analyst) approves → publish. Versioned; history never rewritten (bitemporal).
- On approval it **publishes** `GHANA_OPERATING_ENVIRONMENT_SCORE` as a
  `CanonicalMarketIndex` via the desk-as-vendor `pull_runner.execute_pull` path
  (`source_system=AEQUOR_DESK`), so it fans to every tenant and the rating picks it up
  — no rating-engine change needed (the rating already reads the index, falls back to
  the methodology default only when none is published).
- Full lineage: inputs + thresholds + methodology version + both signers + rationale,
  reproducible from the stored snapshot (same discipline as the curve determinations).

## Backend

- **Domain (pure):** `app/domain/rating/operating_environment.py` — the BICRA calc:
  immutable inputs → sub-scores → pillars → composite → `[0,1]` score + a structured
  breakdown, with a canonical-JSON `input_digest`. No I/O.
- **Service:** `app/services/market_desk/operating_environment.py` — resolve the
  auto-pulled inputs (sovereign, MPR), accept the entered inputs, compute, persist a
  governed assessment (maker-checker), and on approval publish the index through the
  desk path.
- **Model + migration:** `desk_operating_environment_assessments` (jurisdiction, cob
  date, inputs snapshot, computed breakdown, status draft→pending_review→approved→
  published, proposed_by/approved_by, methodology version). GLOBAL desk table.
- **Operator endpoints:** `app/operator/features/operating_environment.py` — compute
  preview (writes nothing), stage draft, submit, approve, publish; list/get.

## Console UI (Market Desk → Operating Environment)

A workbench: input/confirm the observable inputs (sovereign & MPR shown auto-pulled;
macro + banking-system aggregates as fields; the one judgment sub-score with a rationale
box), a live breakdown (each input → sub-score → pillar → composite → the `[0,1]` result,
with the sovereign governor shown), an assumptions panel (the versioned thresholds/weights,
Track-2 to change), and the lifecycle rail draft → compute → submit → approve → publish.

## Non-goals / open items
- The threshold tables, weights and governor mapping ship as **documented calibration
  placeholders** pending independent validation (§8.2) before any output sizes a live
  repo haircut — same discipline as the PD master scale.
- Automatic ingestion of the macro / banking-system aggregates (from BoG data feeds) is
  a later enhancement; v1 accepts desk entry of those inputs with provenance.
