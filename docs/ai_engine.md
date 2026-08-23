# AequorOS AI Engine — Architecture and Integration Specification

**Author:** Eric Inkoom Danso
**Status:** Draft v1.0 — for engineering implementation. **As-built status annotated 2026-07-18 (see §1A);** everything from §2 onward is target design, not a completion claim.
**Applies to:** Six calculation modules (IRRBB, Liquidity, FX, Basel Capital, FTP, Balance Sheet Forecasting) plus the Data Engine intelligence layer
**Companion documents:** `data_engine.md`, `storage.md`, `market_data_adapter.md`, Product Documentation

---

## 1. Purpose and Scope

The AI Engine is the shared machine learning infrastructure that trains, validates, serves, monitors, and governs every AI/ML model in AequorOS. It is not a module; it is a horizontal layer used by all six calculation modules and by the Data Engine's intelligence layer.

This document specifies:

- The model inventory across all AequorOS components with architecture, training data, and governance tier for each
- The engine's architecture: training pipeline, serving pipeline, feature store, model registry, monitoring
- The per-bank agnostic pattern that lets one codebase train and serve models across every customer
- The MRM (Model Risk Management) framework mapping to SR 11-7 and OCC 2011-12 with tiered validation
- The integration surface between the AI Engine and each calculation module
- The phased build plan aligned with MVP and post-MVP module rollout

The AI Engine is architected to be the second-most-strategic engineering artifact in AequorOS, after the Data Engine. The data engine determines whether AequorOS can ingest any bank's data. The AI Engine determines whether AequorOS can genuinely deliver on the "AI-powered" claim in the product's positioning.

## 1A. Implementation Status Snapshot (as-built — 2026-07-18)

> Added after a codebase audit (branch `eric`) against the target architecture below. **Everything from §2 onward is the target design; this section records what is actually implemented today.** Legend: **✅ Built** · **🟡 Partial** · **⬜ Not built** (target/planned).

**Headline.** The product's ML surface is real and partly in production, but the "AI Engine" as a *horizontal platform* (feature store, model registry, unified serving API, monitoring, MRM workflow) is **not** built — its concerns exist as per-model, filesystem-based, ad-hoc implementations. Every ML prediction has a working deterministic fallback, so principle 2.4 holds in practice. There is **no MLOps stack**: the only ML dependencies are `scikit-learn` and `torch` (plus `rapidfuzz`/`jellyfish`/`metaphone` for string matching). No MLflow, Feast, SHAP, XGBoost, RL framework, Cox/lifelines, or statsmodels appears anywhere in the tree. Where a model is built it typically uses a **classical, small-data-robust** algorithm rather than the deep-learning / ensemble / RL design named in §3.

### Product-module models (§3.1)

| ID | Target architecture | As-built reality | Status |
|---|---|---|---|
| M-LIQ-01 (cash-flow) | 2-layer LSTM(64,32)+Dense | Real torch `LSTM(12→64→32)→Linear`, trained + persisted (`artifacts/cashflow`), early stopping. **Not per-tenant** (process-wide singleton); output is a standalone forecast endpoint, **not** fed into LCR. | ✅ Built (arch matches) |
| M-FTP-01 (NMD duration) | Cox PH + Random Forest | Per-tenant sklearn **HistGradientBoostingRegressor**; **applied as assumptions** into FTP/IRR/LCR. | ✅ Built (different algo) |
| M-BSF-01 (strategic opt.) | Deep RL PPO/A3C + Monte Carlo | Deterministic **108-candidate grid-search** optimizer under LCR/NSFR/CAR constraints — no RL. | 🟡 Feature built, not ML |
| M-FTP-02 (FTP curve) | Regression + gradient boosting | Deterministic Decimal curve (base + premiums, linear interp). | ⬜ No ML |
| M-FX-01 (FX prediction) | XGBoost + LSTM ensemble | Deterministic FX engine (hist-sim VaR/NOP/hedge) — no forecast. | ⬜ No ML |
| M-IRRBB-01 (hedge opt.) | Deep RL + LSTM | Deterministic IRRBB engine; hedge ratio is an input, not optimized. | ⬜ No ML |

**Plus three per-tenant behavioral GBMs** (`app/ml/behavioral/`) not separately listed in §3 but central to the product: **NMD duration** (= M-FTP-01), **prepayment** (feeds Liquidity/LCR inflows), **deposit stability** (feeds the LCR stable/less-stable split + NMD core share). All are `HistGradientBoostingRegressor` with a data-sufficiency gate (≥24 samples / 18 months) that degrades to empirical mean → generic prior; outputs are written back as an accepted `behavioral_assumptions` canonical batch consumed by `fact_derivation` (LCR, FTP, IRR). Retrain = `POST /banks/{id}/behavioral/{model}/train`. This applied-as-assumptions seam is the most complete AI→module integration in the codebase.

### Data-Engine intelligence models (§3.2 / §3.3)

| ID | Target architecture | As-built reality | Status |
|---|---|---|---|
| D-DEDUP-01 | Fuzzy + phonetic + supervised classifier | Fuzzy (rapidfuzz) + phonetic (soundex/metaphone/double-metaphone) + char-n-gram TF-IDF cosine run **live on every batch**. The RandomForest classifier is coded + trainable but **not trained, not persisted, not loaded** (`artifacts/etl_models/` does not exist) → the live path runs a deterministic weighted blend, stamping `model_id=None`. | 🟡 Partial (classifier dormant) |
| D-ANOM-01 | Isolation forest + SPC | IsolationForest (**fit per-batch**, unsupervised, ≥8 rows) + modified-MAD SPC z-score, both live. No pre-trained/persisted model. | 🟡 Partial (unsupervised, not trained) |
| D-MAP-01 | Embedding similarity + classifier | Deterministic reverse-alias index (`app/etl/resolve.py`, `financial_mapping/row_mapper.py`). No embeddings/classifier. | ⬜ No ML (deterministic aliasing) |
| D-RECON-01 | Rule-based + classifier | Deterministic lineage rules (position dedup) + GL/subledger reconciliation validation. No supervised classifier. | ⬜ No ML (rules only) |
| S-BEHAV-01 / S-CREDIT-01 | Stress models (Phase 3) | Not built. | ⬜ Not built (Phase 3) |

> The ETL **preprocessing** layer (ISO-4217/3166/8601 normalization, type coercion, reference resolution, and the flag-not-modify guard on regulatory-critical fields per §15.3) is fully built and live — but it is deterministic rule-based data cleaning, not one of the ML models above. Both ETL models carry a real MRM scaffold (`app/etl/models/_mrm.py`: `ModelCard` feature contract + `HumanOverride`/`OverrideRegistry` + joblib persistence), but that scaffold governs only these two models.

### AI-Engine layers (§4–§10)

| Layer | Status | Reality |
|---|---|---|
| 1 Feature Store | ⬜ Not built | No `@feature`/store/registry; feature engineering is ad-hoc and embedded per model. (`app/features/` is the FastAPI **route** layer, not a feature store.) |
| 2 Training | 🟡 Partial | Real, reproducible, seeded per-model training (LSTM holdout; behavioral expanding-window CV; ETL train-and-validate on injected corruptions). No unified DAG, no scheduled/drift-triggered retrain — retraining is manual. |
| 3 Registry | 🟡 Partial | Filesystem artifacts (joblib / torch+scaler+json / behavioral `estimates.json`) + `ModelCard` + **static** version strings. No MLflow, no `{model_id}:{institution_id}:{version}:{stage}` key, no stages/promotion, no point-in-time reproduction, **no DB table**. Only behavioral is institution-scoped on disk. |
| 4 Serving | 🟡 Partial | Bespoke per-bank REST endpoints (`/behavioral/…`, `/cashflow-forecast`) with real deterministic fallbacks; ETL models are called in-process, not served. No unified `POST /ai/v1/predict/{model_id}` contract. |
| 5 Monitoring | ⬜ Not built | No drift (KS/PSI/JS), performance, or bias monitoring. Metrics exist only at training time. |
| 6 Governance / MRM | 🟡 Partial | `_mrm.py` model cards + auditable human-override registry for the **two ETL models only**. No tiers, approval routing/gates, examiner mode, SHAP/explainability, or model-lifecycle audit table. The cash-flow LSTM and behavioral GBMs sit outside `_mrm`. |

**Supporting infrastructure that IS built:** the 10-year synthetic **history simulator** (`data/simulator/` → parquet panels under `data/history/panels/`) and its LSTM retrain seam (`app/ml/real_series.py`, gated by `CASHFLOW_USE_REAL_SERIES`, 8y-train / 2y-val) — matching §12.2's bootstrap-training intent.

---

## 2. Design Principles

Six principles that govern every AI Engine decision.

### 2.1 Bank-agnostic pipelines, per-bank models

Every training pipeline reads from the canonical model's feature interface, not from source-specific structures. Every trained model is scoped to a single institution and retrained on that institution's data. Cross-bank patterns are learned in Phase 3+ via federated or transfer learning, never by co-mingling raw bank data. A model trained for GCB never touches Merchant Bank's data or vice versa.

### 2.2 Canonical features, not source features

Models train on features derived from the canonical model per `data_engine.md` section 4. A canonical loan record has the same shape regardless of whether it came from Oracle FlexCube, Snowflake warehouse, or Excel drops. This means the LSTM cash flow model doesn't care what core banking system the bank runs. Source-agnostic data plus source-agnostic features equals model portability across the entire customer base.

### 2.3 Governance is the moat, not the models

The specific weights of any specific model are a per-bank artifact with limited transferability. The moat is the training pipeline, the validation pipeline, the MRM discipline, the audit trail, the explainability infrastructure, and the ability to defend model behavior to regulators. Investing in governance infrastructure compounds; investing in one-off model weights does not.

### 2.4 Deterministic fallbacks for every AI prediction

Every ML output has a deterministic fallback that produces a defensible answer when the model is unavailable, when confidence is below threshold, or when a human override is requested. LSTM cash flow forecast falls back to linear extrapolation. Deep RL hedging falls back to static duration matching. XGBoost+LSTM FX prediction falls back to random walk. Falling back is not failure; it is regulated-industry engineering discipline. Banks and regulators require it.

### 2.5 Explainability is non-negotiable

Every prediction that reaches a regulatory calculation or ALCO decision must be explainable. SHAP values for tree-based and neural models, attention weights for LSTMs where possible, feature importance rankings, counterfactual explanations. If the model cannot explain itself in terms the CRO can defend to Bank of Ghana, the model does not ship.

### 2.6 Synthetic training is architecture proof, not accuracy claim

Sample Bank Limited's 10-year synthetic data proves the training pipeline works, the serving pipeline works, the governance pipeline works, and the architecture generalizes. Performance metrics on synthetic data are architecture-validation numbers, not marketing numbers. All customer-facing accuracy claims come from per-bank retraining on real bank data with per-bank validation reports.

## 3. Model Inventory

Every AI/ML model AequorOS ships or will ship, with tier, module, architecture, training data, and governance requirements. This is the master inventory; every model in the codebase must appear here.

### 3.1 Product Module Models

*Status column reflects the as-built audit (see §1A). Architecture column remains the **target** design.*

| ID | Module | Purpose | Architecture | Tier | Training Data | Retraining | Status |
|---|---|---|---|---|---|---|---|
| M-IRRBB-01 | IRRBB | Optimize hedge ratios for IR swaps | Deep RL, 3-layer LSTM (128,64,32), policy gradient | 2 | 5 years yield curves + bank position history | Semi-annual | ⬜ No ML (deterministic engine; hedge is an input) |
| M-LIQ-01 | Liquidity | 30-90 day cash flow forecast | 2-layer LSTM (64,32) + Dense | 2 | 2 years daily transactions, macro | Monthly | ✅ Built (torch LSTM; not per-tenant; not fed to LCR) |
| M-FX-01 | FX Risk | GHS/USD prediction 1/7/30 day | XGBoost + LSTM ensemble | 2 | 10 years daily FX + macro | Weekly (short) / Monthly (long) | ⬜ No ML (deterministic FX engine) |
| M-FTP-01 | FTP | NMD effective duration | Cox PH + Random Forest ensemble | 2 | Full deposit history + demographics | Quarterly | ✅ Built as per-tenant GBM (not Cox+RF); applied as assumptions |
| M-FTP-02 | FTP | Dynamic FTP curve construction | Regression + gradient boosting | 2 | Market rates + funding conditions | Daily curve refresh | ⬜ No ML (deterministic curve) |
| M-BSF-01 | Balance Sheet Forecasting | Strategic decision optimization | Deep RL PPO/A3C + Monte Carlo | 1 | 3-5 years balance sheet + macro scenarios | Annual or scenario change | 🟡 Deterministic grid-search optimizer (not RL) |

### 3.2 Data Engine Intelligence Models

*Status column reflects the as-built audit (see §1A). Architecture column remains the **target** design.*

| ID | Component | Purpose | Architecture | Tier | Training Data | Retraining | Status |
|---|---|---|---|---|---|---|---|
| D-DEDUP-01 | Ingestion | Cross-source counterparty dedup | Fuzzy match + phonetic + supervised classifier | 3 | Cumulative onboarding data | Quarterly | 🟡 Fuzzy+phonetic+TF-IDF live; RandomForest coded but untrained/dormant |
| D-ANOM-01 | Ingestion | Anomaly detection in feeds | Isolation forest + statistical process control | 3 | Historical bank data (per bank) | Monthly | 🟡 IsolationForest (fit per-batch) + MAD/SPC live; no trained model |
| D-MAP-01 | Onboarding | Schema mapping assistance | Embedding-based similarity + supervised classifier | 3 | Cumulative onboarding mappings | Continuous | ⬜ No ML (deterministic reverse-alias index) |
| D-RECON-01 | Reconciliation | Break attribution assistance | Rule-based + supervised classifier | 3 | Historical breaks with resolutions | Quarterly | ⬜ Rules only (no supervised classifier) |

### 3.3 Stress Testing Support Models (Phase 3)

*Status column reflects the as-built audit (see §1A). Architecture column remains the **target** design.*

| ID | Component | Purpose | Architecture | Tier | Training Data | Retraining | Status |
|---|---|---|---|---|---|---|---|
| S-BEHAV-01 | Stress Testing | Behavioral assumptions under stress | Regression + regime classifier | 2 | Historical stress episodes + bank data | Annual | ⬜ Not built (Phase 3) |
| S-CREDIT-01 | Stress Testing | Credit migration under scenarios | Markov chain + supervised | 2 | Historical PD/LGD/EAD | Annual | ⬜ Not built (Phase 3) |

**Governance tier definitions per `Product Documentation` MRM section:**

- **Tier 1:** Drives regulatory capital, stress testing results, or major ALCO decisions. Full validation, annual review, Model Risk Committee approval, CRO sign-off.
- **Tier 2:** Supports business decisions with manual oversight. Validation at deployment, bi-annual review, MRC review, senior risk sign-off.
- **Tier 3:** Supplementary analytics. Documentation required, informal review.

## 4. Architecture Overview

The AI Engine is six layers stacked. Each layer is independently deployable and testable.

```
┌────────────────────────────────────────────────────────────────┐
│  Layer 6: Governance & Audit                                    │
│  MRM workflow, approval routing, audit trail, examiner mode     │
├────────────────────────────────────────────────────────────────┤
│  Layer 5: Monitoring                                            │
│  Drift detection, performance monitoring, bias monitoring       │
├────────────────────────────────────────────────────────────────┤
│  Layer 4: Serving                                               │
│  Inference API, per-bank model routing, fallback logic          │
├────────────────────────────────────────────────────────────────┤
│  Layer 3: Registry                                              │
│  Versioned model artifacts, per-bank scoping, promotion         │
├────────────────────────────────────────────────────────────────┤
│  Layer 2: Training                                              │
│  Feature engineering, model training, validation, backtesting   │
├────────────────────────────────────────────────────────────────┤
│  Layer 1: Feature Store                                         │
│  Canonical feature definitions shared training and inference    │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    Data Engine canonical model
                    (per `data_engine.md` section 4)
```

Each calculation module talks to Layer 4 (Serving) via a standardized inference contract. The module does not talk to any lower layer. This gives the AI Engine full control over how models are trained, versioned, deployed, and monitored without leaking that complexity into module code.

## 5. Layer 1: Feature Store

> **As-built status (§1A): ⬜ Not built.** No shared feature store, `@feature` decorator, or domain-organized feature definitions exist. Feature engineering is ad-hoc and embedded per model (`app/ml/features.py`, `app/ml/behavioral/features.py`, `app/etl/…/fingerprint.py`). The nearest thing to a feature contract is the per-artifact `feature_names` tuple on `ModelCard`. The section below is the target design.

Purpose: define, compute, and serve canonical features that both training and inference consume, ensuring train-serve consistency.

### 5.1 Feature Contract

A feature is defined once with a schema, a computation function, and a validity window. Both the training pipeline and the serving pipeline read the same feature definition. Train-serve skew (a common failure mode where the training and inference feature computations diverge) is prevented by construction.

Example feature definition for M-LIQ-01 cash flow forecast:

```python
@feature(
    name="daily_deposit_outflow_ghs",
    entity="institution",
    freshness=timedelta(hours=1),
    ttl=timedelta(days=1),
    tier=1,  # regulatory-critical
)
def daily_deposit_outflow_ghs(institution_id: str, as_of_date: date) -> float:
    """Sum of deposit outflows in GHS on the given date."""
    return canonical.transactions.query(
        institution_id=institution_id,
        transaction_date=as_of_date,
        transaction_type="deposit_withdrawal",
        currency="GHS",
    ).sum("amount")
```

The feature contract lives in `features/definitions/`. Training reads a range of dates for a range of features; serving reads a single date's features. Same function, same output shape.

### 5.2 Feature Categories

Features are organized by domain, not by module. This is important because features are reused across models.

- `features/balance_sheet/` — positions, balances by GL, product mix
- `features/cash_flow/` — inflows, outflows, net flow by day/week/month
- `features/deposit_behavior/` — churn, seasonality, segment behavior
- `features/loan_behavior/` — prepayment, default, migration between stages
- `features/market/` — yield curves, FX rates, commodity prices, macro
- `features/regulatory/` — RWA components, HQLA classifications, capital ratios
- `features/temporal/` — day of week, month end, payroll dates, holidays, seasonal

The LSTM cash flow model M-LIQ-01 might use features from `cash_flow/`, `deposit_behavior/`, `temporal/`, and `market/`. The Deep RL hedging model M-IRRBB-01 uses `balance_sheet/`, `market/`, and `regulatory/`. Cross-cutting concerns like temporal features are defined once and consumed everywhere.

### 5.3 Per-Bank Scoping

Every feature query includes an `institution_id` parameter. Feature values are always scoped to a single bank. There is no cross-bank feature aggregation at the Feature Store layer. Cross-bank pattern learning (Phase 3+) happens at higher layers with explicit multi-tenant privacy controls.

### 5.4 Feature Store Implementation

Phase 1 (MVP): implement Feature Store as a Python library reading from the canonical model in the data engine's operational database. No separate Feature Store service. Adequate for MVP scale.

Phase 2: introduce a caching layer for high-frequency features (Redis or equivalent) to reduce inference latency.

Phase 3: adopt a managed Feature Store (Feast per Product Documentation, or Vertex Feature Store on GCP) when multi-tenant scale demands it. The Python library interface stays the same; the backing store changes.

## 6. Layer 2: Training

> **As-built status (§1A): 🟡 Partial.** Real, reproducible, seeded per-model training exists — cash-flow LSTM with holdout eval (`app/ml/model.py`), behavioral GBMs with expanding-window time-series CV (`app/ml/behavioral/estimator.py`), and the two ETL models with train-and-validate on injected corruptions (`app/etl/models/*/training.py`). But there is no unified DAG, no scheduled or drift-triggered retrain (triggers are manual: `POST …/behavioral/{model}/train` or `python -m …training`), and no cross-model backtesting harness. The section below is the target design.

Purpose: reproducibly train models from feature data, validate them, and produce artifacts ready for the Registry.

### 6.1 Training Pipeline

Every model has a training pipeline defined as a DAG. Inputs: bank institution_id, training window (start date, end date), model configuration, feature list. Outputs: trained model artifact, validation report, feature importance report, backtesting report.

The pipeline is deterministic given the same inputs. Reruns produce byte-identical outputs. This is required for MRM audit compliance: a regulator asking "how was this model trained" gets an exact reproduction.

### 6.2 Training DAG Stages

Each pipeline goes through named stages that are individually inspectable and cacheable.

1. **Data extraction:** pull features from the Feature Store for the training window
2. **Data validation:** check for missingness, distributions, drift from prior training data
3. **Feature engineering:** compute derived features (lags, rolling windows, interactions)
4. **Train/test/holdout split:** time-series aware split respecting temporal ordering
5. **Model training:** run the algorithm with configured hyperparameters
6. **Model validation:** apply the validation framework (Section 6.4)
7. **Explainability generation:** SHAP values or equivalents for the trained model
8. **Artifact packaging:** serialize model + metadata for the Registry
9. **Validation report generation:** structured document that becomes part of the MRM record

Failure at any stage halts the pipeline and produces a diagnostic record.

### 6.3 Per-Bank Retraining Cadence

Per the Product Documentation MRM section:

- M-LIQ-01 (Cash Flow): monthly retrain on new bank data
- M-FX-01 (FX Rate): weekly retrain for short-horizon, monthly for long-horizon
- M-FTP-01 (NMD): quarterly retrain
- M-IRRBB-01 (Deep RL Hedging): semi-annual retrain
- M-BSF-01 (Strategic RL): annual retrain or when macro scenarios materially shift

Retraining is triggered by:
- Scheduled cadence per model config
- Drift alerts from Layer 5 (Monitoring) exceeding thresholds
- Data quality events flagged by the Data Engine
- Model performance degradation observed in production
- Manual trigger by CRO or MRC

Every retrain produces a new versioned model in the Registry. Old versions remain accessible for point-in-time reproduction.

### 6.4 Validation Framework

Every model must pass the following before it can be promoted to production. Directly from Product Documentation MRM section.

**Backtesting:** Walk-forward validation. Train on 80% of historical data, predict on out-of-sample 20%. Metrics vary by model type:
- Time-series forecasting (M-LIQ-01, M-FX-01): MAPE, RMSE, directional accuracy
- Classification (D-DEDUP-01): precision, recall, F1
- RL policy (M-IRRBB-01, M-BSF-01): Sharpe ratio of policy vs. baseline, max drawdown

**Cross-validation:** 5-fold time-series cross-validation respecting temporal ordering. Ensures the model is robust across economic regimes, not just calibrated to the most recent one.

**Champion-Challenger:** New model versions must achieve ≥10% improvement on the primary metric with no regression on secondary metrics to replace the champion in production. Otherwise the new version enters a shadow deployment (Section 8.2) or is rejected.

**Stress testing:** Validate model behavior under extreme scenarios. For Ghana models, the 2022-2023 cedi crisis period is a required stress episode. Models must not produce non-sensical outputs when inputs fall outside training distribution.

**A/B testing in production:** For models above Tier 3, new versions deploy to 10% of banks initially. Compare actual to predicted performance over 3 months before full rollout. This is a Phase 3+ capability requiring multiple banks in production.

### 6.5 Model Performance Benchmarks

Per Product Documentation, minimum performance thresholds before a model can be promoted from validation to production:

- M-LIQ-01 (Cash Flow): MAPE ≤ 15% for 30-day forecasts, vs. 25-30% baseline
- M-FX-01 (FX Prediction): Directional accuracy ≥ 55% for 7-day forecasts, vs. 50% random walk
- M-FTP-01 (NMD Behavior): R² ≥ 0.75 on hold-out sample
- M-IRRBB-01 (Deep RL Hedging): Sharpe ratio improvement ≥ 0.3 vs. static duration matching
- M-BSF-01 (Strategic RL): ROE improvement ≥ 150bps vs. naive baseline in backtests

Models that fail to meet these benchmarks stay in the Registry as challengers but are not promoted to production.

### 6.6 Training Infrastructure

Phase 1 (MVP): Cloud-hosted managed training (Vertex AI Custom Training if on GCP, SageMaker if on AWS). GPU instances for the deep learning models (LSTM cash flow, Deep RL). CPU for classical models (Cox PH, gradient boosting).

Phase 2: Add distributed training for the RL models that require simulation environments (M-IRRBB-01, M-BSF-01). Add automated hyperparameter tuning.

Phase 3: Add pipelines for federated learning across banks that consent to cross-bank pattern learning under formal data sharing agreements. Multi-tenant model training with strict privacy guarantees.

## 7. Layer 3: Registry

> **As-built status (§1A): 🟡 Partial.** Model artifacts persist to an untracked filesystem tree (`artifacts/cashflow`, `artifacts/behavioral/{model}/{org}/{bank}/estimates.json`; `artifacts/etl_models/` for the joblib ETL models, which is currently absent) alongside `ModelCard` metadata and **static** version strings. There is no MLflow, no registry service, no `{model_id}:{institution_id}:{version}:{stage}` identifier, no stages/promotion workflow, no point-in-time reproduction, and **no database table** for model artifacts. Only the behavioral models are institution-scoped on disk. The section below is the target design.

Purpose: store versioned model artifacts with full lineage, enable per-bank retrieval, and support promotion workflows.

### 7.1 Model Artifact

A model artifact is a self-contained bundle containing:

- Serialized model weights (framework-specific: PyTorch, XGBoost, etc.)
- Feature list and versions used at training time
- Training configuration (hyperparameters, random seeds, data window)
- Validation report (from Layer 2 Section 6.4)
- Explainability artifacts (SHAP explainers, feature importance)
- Fallback function reference (deterministic backup)
- MRM metadata (tier, approver, approval date, effective date)
- Lineage graph pointing back to the training dataset

Artifacts are immutable once registered. Updates produce new versions.

### 7.2 Naming and Scoping

Every artifact has a fully qualified identifier:

```
{model_id}:{institution_id}:{version}:{stage}

Examples:
M-LIQ-01:SBL-GH-001:v3:production
M-LIQ-01:SBL-GH-001:v4:challenger
M-LIQ-01:MERCHANT-GH-002:v1:production
```

Per-bank scoping is enforced at the Registry level. Cross-bank artifact access requires an explicit multi-tenant permission grant and is logged in the audit trail.

### 7.3 Promotion Workflow

Artifacts move through stages: `training` → `validation` → `challenger` → `production` → `retired`.

Transitions are gated by MRM approval per model tier:
- Tier 1: Full MRC review, CRO approval, monthly meeting
- Tier 2: MRC review, senior risk sign-off, ad-hoc
- Tier 3: Documentation review, MRM ops sign-off

Every promotion transition is recorded in the audit trail with actor, timestamp, and rationale. Downgrade transitions (e.g., production → retired due to drift) are equally logged.

### 7.4 Point-in-Time Reproduction

Because artifacts are immutable and lineage is complete, the Registry supports the question "which model was in production for Merchant Bank on 2026-03-15 for cash flow forecasting?" and returns the exact artifact used. This is required for BoG examinations that ask about specific reported metrics.

### 7.5 Registry Implementation

Phase 1 (MVP): MLflow Model Registry deployed on the AequorOS operational database. Adequate for MVP scale.

Phase 2: Move to Vertex AI Model Registry (or SageMaker Model Registry) with the same MLflow-compatible interface.

Phase 3: Custom Registry layer if multi-tenant governance requirements exceed what managed services provide.

## 8. Layer 4: Serving

> **As-built status (§1A): 🟡 Partial.** There is no unified `POST /ai/v1/predict/{model_id}` contract. Serving is bespoke per model: behavioral via `GET/POST /v1/banks/{bank_id}/behavioral/{model}`, cash-flow via `GET /v1/banks/{bank_id}/cashflow-forecast`; the ETL models are invoked in-process by the ingestion pipeline, not served over HTTP. Per-bank routing (via `bank_id` path + tenant scoping) and real deterministic fallbacks **do** exist. The section below is the target design.

Purpose: expose trained models via a standardized inference API that calculation modules call.

### 8.1 Inference Contract

Every model exposes an inference endpoint with a fixed contract:

```
POST /ai/v1/predict/{model_id}
Authorization: Bearer <service token>
X-Institution-ID: SBL-GH-001

{
  "as_of_date": "2026-04-30",
  "features": {
    ...  # optional, else pulled from Feature Store
  },
  "options": {
    "explainability": true,
    "confidence_threshold": 0.7,
    "fallback_mode": "linear"
  }
}
```

Response:

```
{
  "prediction": [...],
  "confidence": 0.83,
  "explainability": {
    "shap_values": [...],
    "top_features": [...]
  },
  "model_version": "M-LIQ-01:SBL-GH-001:v3",
  "fallback_used": false,
  "audit_id": "..."
}
```

The calculation module does not know which model version served the request or which algorithm was used. It knows the model_id and the institution_id. The Serving layer resolves the current production version, invokes it, applies fallback logic if confidence is below threshold, and returns the prediction with full audit metadata.

### 8.2 Deployment Patterns

**Blue-green deployment:** New model versions deploy to a green environment while blue continues serving. Traffic shifts gradually. Rollback is instantaneous if issues surface.

**Shadow deployment:** Challenger models receive live inference traffic in parallel to the champion but their outputs are logged, not returned. This gathers real-world performance data before promotion.

**Canary deployment:** New model versions receive 5% of traffic for a period, then 25%, then 50%, then 100%. Enables early detection of production issues without full exposure.

**A/B testing:** Requires multiple banks. New model versions deploy to a randomly selected subset of banks and comparative performance is measured.

Phase 1 (MVP): Blue-green only. Adequate for one to three banks.

Phase 2: Add shadow and canary deployments as customer base grows.

Phase 3: Full A/B testing across multi-tenant customer base.

### 8.3 Fallback Logic

Every model must define a deterministic fallback that produces a valid prediction when:
- The model returns confidence below the configured threshold
- The model service is unavailable (timeout, crash, deployment failure)
- The Feature Store returns missing features that the model requires
- The MRC has explicitly disabled the model for regulatory reasons

Fallbacks per model:

- M-LIQ-01: Linear extrapolation of prior 30-day trend
- M-FX-01: Random walk (next day = today's value)
- M-FTP-01: Static behavioral duration per bank policy
- M-IRRBB-01: Static duration matching
- M-BSF-01: Baseline scenario projection with no optimization

Fallback usage is logged with a flag on the response so the calculation module knows whether the answer came from the AI or from the deterministic backup. Fallback triggers an alert to the operations team and, if sustained, to the CRO.

### 8.4 Serving Infrastructure

Phase 1 (MVP): Model serving via Vertex AI Prediction (or SageMaker Endpoints on AWS). One endpoint per model_id, per-bank routing at the endpoint via institution_id header.

Phase 2: Add caching layer for high-frequency low-variance predictions (e.g., NMD behavioral duration, which changes slowly).

Phase 3: Add real-time streaming predictions for models that require sub-second latency (FX prediction for treasury desk, intraday liquidity).

## 9. Layer 5: Monitoring

> **As-built status (§1A): ⬜ Not built.** No production drift detection (KS/PSI/JS), performance monitoring against realized outcomes, or bias monitoring exists. The only metrics are computed at training time (`ModelCard.validation_metrics`, cash-flow `metrics.json`). The section below is the target design.

Purpose: continuously observe model behavior in production and alert on drift, degradation, or bias.

### 9.1 Input Drift Monitoring

Statistical process control on feature distributions. When the distribution of input features shifts materially from the training distribution, the model may be operating outside its validated envelope.

Detection: Kolmogorov-Smirnov tests, Population Stability Index (PSI), Jensen-Shannon divergence. Thresholds calibrated per feature.

Response: Alert to MRM ops. Trigger retraining if drift exceeds sustained threshold. Escalate to CRO if drift is systemic.

### 9.2 Prediction Drift Monitoring

Same statistical tests applied to prediction distributions. A model whose predictions are shifting even though inputs look stable may have a bug or a stale calibration.

### 9.3 Performance Monitoring

Backtesting against realized outcomes as ground truth arrives. For M-LIQ-01, cash flow predictions made 30 days ago are compared to actual cash flows today. Cumulative MAPE is tracked and alerted on threshold breach.

### 9.4 Bias Monitoring

Per Product Documentation: monitor for unfair treatment across customer segments, geographies, product types. Statistical tests for prediction bias. Regular review by compliance team.

Bias metrics per model context:
- Deposit behavior models: bias by customer income segment, geography
- Loan default models: bias by product type, tenor, sector
- FX models: not applicable at customer level (market-level model)

### 9.5 Alerting

Monitoring signals feed a hierarchical alerting system:
- **Green:** Normal operation, no alert
- **Amber:** Metric outside normal range, MRM ops review required within 5 business days
- **Red:** Metric outside acceptable range, immediate escalation to CRO, model may be disabled

Fallback engagement itself is an amber signal. Sustained fallback usage (more than 10% of predictions over a week) is a red signal.

### 9.6 Monitoring Infrastructure

Phase 1 (MVP): Log predictions and features to the data engine's canonical store. Batch monitoring jobs run daily to compute drift metrics.

Phase 2: Real-time drift monitoring via streaming (Vertex AI Model Monitoring or equivalent).

Phase 3: Automated retraining triggered by monitoring alerts, subject to MRM approval workflow.

## 10. Layer 6: Governance and Audit

> **As-built status (§1A): 🟡 Partial.** `app/etl/models/_mrm.py` implements a real but lightweight MRM scaffold — `ModelCard` (feature-contract + validation metrics + training-data ref) and an auditable `HumanOverride`/`OverrideRegistry` — but it governs **only the two ETL models**; the cash-flow LSTM and behavioral GBMs sit outside it. There are no model tiers, approval routing / validation gates, examiner mode, SHAP/explainability (zero SHAP in the tree), or model-lifecycle audit table. The section below is the target design.

Purpose: enforce MRM discipline, produce audit-ready records, and enable regulator examination.

### 10.1 MRM Workflow

Every model change (initial registration, retraining, promotion, retirement) flows through an approval workflow scoped to the model's tier.

**Tier 1 workflow:**
1. Developer submits Model Development Document (MDD)
2. Independent validator conducts validation (4-6 weeks)
3. Validation report submitted to Model Risk Committee
4. MRC reviews at monthly meeting
5. CRO signs off for production use
6. IT deploys with monitoring enabled
7. Ongoing validation schedule established

**Tier 2 workflow:** Same as Tier 1 but validation is bi-annual, MRC review can be async, sign-off by senior risk officer.

**Tier 3 workflow:** MDD and monitoring config required; formal approval by MRM ops.

Every step in these workflows is recorded in the audit trail with actor, timestamp, artifacts, and rationale.

### 10.2 Model Documentation Standards

Per Product Documentation, every model has:
- Model Development Document (MDD): purpose, methodology, data, assumptions, limitations
- Model Validation Report (MVR): independent validator's findings and challenges
- User Guide: how the model is consumed, input requirements, interpretation of outputs
- Ongoing Monitoring Reports: quarterly performance summaries
- Model Change Log: all modifications with rationale and approvals

Documentation lives with the model artifact in the Registry, not in a separate wiki. When a regulator examines a model, they get the artifact plus its complete documentation trail in one export.

### 10.3 Explainability Infrastructure

Every prediction returned to a calculation module carries an explainability payload. For:
- Tree-based models (XGBoost): SHAP values per feature
- Neural models (LSTM): SHAP values via DeepExplainer, plus attention weights where applicable
- RL policies: state-value decomposition, action justification
- Ensemble models: SHAP values from the aggregation plus component-level explainability

Explanations are displayed in the module UI wherever the prediction is displayed. A user clicking on a cash flow forecast can drill into "why did the model predict this" and get a feature contribution breakdown.

### 10.4 Bias Detection

Statistical tests run monthly per model:
- Demographic parity: prediction distributions across protected segments
- Equalized odds: false positive and false negative rates across segments
- Calibration: predicted probabilities matching realized outcomes across segments

Bias reports go to the compliance officer role in the bank's user directory.

### 10.5 Concept Drift Response

Concept drift (the world changing so historical patterns no longer apply) is detected via monitoring but requires model-level response beyond retraining. The MRC evaluates whether:
- Retraining on recent data is sufficient (most common)
- Feature engineering must change (add new signals)
- Model architecture must change (a new model version generation)
- The model must be retired and replaced (fundamental shift)

Every response option is a formal MRM decision with audit record.

### 10.6 Examiner Mode

Per Product Documentation, the platform provides Examiner Mode for BoG on-site inspections. For AI Engine specifically:

- Read-only access to all model artifacts, versions, and lineage
- Query interface: "which model produced this prediction on this date"
- Documentation package: auto-generated MRM records for any model
- Reproducibility: rerun any historical prediction from the exact artifact

This is a hard requirement for regulatory audits, not optional.

## 11. Integration with the Six Modules

How each module consumes the AI Engine.

### 11.1 Module 1: IRRBB

Consumes M-IRRBB-01 for hedge ratio recommendations. Inference call at:
- ALCO decision points (weekly or ad-hoc)
- New swap origination (deal pricing calculator)
- Stress test scenario runs

Fallback: static duration matching. Module remains fully functional without the AI, calculating gap analysis, duration, EVE, and EaR deterministically.

### 11.2 Module 2: Liquidity Risk

Consumes M-LIQ-01 for 30-90 day cash flow forecasts. Inference call at:
- Daily treasury dashboard refresh
- Weekly ALCO liquidity review
- Contingency Funding Plan scenario runs
- Intraday liquidity management (Phase 2, real-time)

Fallback: linear extrapolation of prior 30-day trend. Module calculates LCR and NSFR deterministically without the AI; only the forward-looking projections rely on M-LIQ-01.

### 11.3 Module 3: FX Risk

Consumes M-FX-01 for GHS/USD (and later NGN/USD, KES/USD, ZAR/USD) predictions. Inference call at:
- Daily FX position review
- Hedge sizing calculations
- VaR calculations (used for stressed VaR scenarios)

Fallback: random walk. VaR remains fully calculable from historical simulation; only optimal hedge sizing under forward expectations relies on M-FX-01.

### 11.4 Module 4: Regulatory Capital

Does not directly consume AI models in MVP. RWA calculations are deterministic per Basel Standardized Approach. AI enhancement in Phase 3+ may include:
- Credit rating prediction for unrated corporates (Tier 2)
- Off-balance-sheet credit conversion factor estimation (Tier 3)

Fallback: static regulatory categories per bank policy.

### 11.5 Module 5: Funds Transfer Pricing

Consumes M-FTP-01 for NMD effective duration estimation and M-FTP-02 for dynamic FTP curves. Inference call at:
- Deposit product pricing decisions
- FTP curve refresh (daily, intraday for deal pricing)
- Product profitability analysis

Fallback: bank policy behavioral assumptions for NMDs, static FTP curve from BoG rates + fixed spreads.

### 11.6 Module 6: Balance Sheet Forecasting

Consumes M-BSF-01 for strategic decision recommendations under scenarios. Inference call at:
- ICAAP/ILAAP scenario runs
- Strategic planning workshops
- Board-level capital planning
- Multi-year P&L projections

Fallback: baseline scenario projection with no optimization; users manually adjust strategic parameters. Module remains functional; only the automated optimization relies on M-BSF-01.

## 12. Bank Onboarding and Model Bootstrap

How a new bank customer moves from data ingestion to production AI predictions.

### 12.1 The Cold Start Problem

A newly onboarded bank has no trained models scoped to their institution_id. They cannot get real predictions on day one because per-bank retraining requires their historical data to have flowed through the data engine.

Three-phase bootstrap:

**Phase A (Week 1): Deterministic-only.** Data engine ingests bank data. All AI Engine calls return fallback predictions with a clear flag indicating no bank-specific model is available yet.

**Phase B (Weeks 2-8): Global model.** A general-purpose model trained on Sample Bank Limited's 10-year synthetic data (and on aggregated learnings from prior bank onboardings once available under consent) serves predictions with a "generic model" flag. Performance is limited but represents architectural readiness.

**Phase C (Week 8+): Per-bank model.** Sufficient historical data has flowed through the data engine to train a bank-specific model. Validation runs, MRM approval flows, model promotes to production. Serving switches to bank-specific model.

Each phase is disclosed to the bank so they know which regime is in effect. Regulators are told the same. There is no pretending a generic model is a bank-specific one.

### 12.2 Sample Bank Limited as Bootstrap Training Data

Per your explicit direction: Sample Bank Limited's 10-year synthetic dataset is the bootstrap training data for the global model layer. This is architecturally correct because:

- The data has enough temporal depth (10 years) for LSTM models to learn seasonality
- The portfolio composition matches the target bank profile (mid-tier Ghanaian universal)
- The behavioral patterns (deposit churn, prepayment, IFRS 9 migration) are engineered to reflect reality
- The macro backdrop includes actual Ghana historical stress episodes (2022-2023 cedi crisis)

Every model in Section 3 can be trained on Sample Bank Limited to prove the architecture works. Performance numbers from this training are internal architecture-validation numbers, not customer-facing accuracy claims. When a real bank onboards, the same code retrains on their data and per-bank performance numbers become bank-specific.

### 12.3 Progressive Enhancement

As banks onboard and consent to cross-bank pattern learning:

Phase 3: Federated learning across consenting banks. Models learn aggregated patterns without any bank seeing another bank's data. Requires formal data sharing agreements with each participating bank.

Phase 4: Meta-learning where the training pipeline itself improves from cumulative onboarding experience. Schema mapping accuracy improves. Anomaly detection improves. Model architectures adapt based on which patterns transfer across banks.

Neither Phase 3 nor Phase 4 is a Phase 1 concern. The architecture accommodates them; the implementation waits until the customer base and legal framework support them.

## 13. Phased Build Plan

Aligned with the module rollout in Product Documentation and Business Plan.

### 13.1 Phase 1 (Months 1-9): MVP AI Engine

> **As-built status (§1A):** The *models* are largely present (M-LIQ-01 LSTM ✅; M-BSF-01 as a deterministic optimizer 🟡; behavioral GBMs feeding assumptions ✅; D-DEDUP-01/D-ANOM-01 live in heuristic/unsupervised form 🟡), but the six *platform layers* below are mostly ad-hoc or absent: the Feature Store and Monitoring are not built, and the Registry, Serving, and MRM deliverables listed here exist only as per-model filesystem/endpoint conveniences (no MLflow, no Vertex/SageMaker, no unified inference API). The deliverables list below is the target Phase-1 scope, not a completion claim.

**Ships with MVP modules Liquidity, Basel Capital, Balance Sheet Forecasting.**

*Note: MVP module scope per your recent decision. The Product Documentation and Business Plan reference IRRBB, Liquidity, Basel Capital as MVP. Update those documents to reflect current call.*

Deliverables:
- Feature Store Python library (Layer 1)
- Training pipeline framework (Layer 2)
- Model Registry via MLflow (Layer 3)
- Serving infrastructure via Vertex AI Prediction (Layer 4)
- Basic monitoring (Layer 5): drift detection, prediction logging
- MRM workflow scaffolding (Layer 6): documentation templates, approval routing

Models ready for production in Phase 1:
- M-LIQ-01 (LSTM cash flow forecast) — first-class MVP feature
- M-BSF-01 (Simplified strategic forecasting, not full Deep RL) — MVP-scoped simplification

Models with fallbacks only in Phase 1:
- M-IRRBB-01, M-FX-01, M-FTP-01, M-FTP-02 (modules not in MVP)

Data Engine ML utilities (D-DEDUP-01, D-ANOM-01) ship with the data engine per the enterprise-grade Fable 5 build prompt.

### 13.2 Phase 2 (Months 9-18): Full Model Suite

**Ships with IRRBB, FX Risk, FTP modules.**

Deliverables:
- Shadow and canary deployments (Layer 4 enhancements)
- Real-time drift monitoring (Layer 5 enhancements)
- Automated retraining pipelines
- Enhanced explainability infrastructure

Models ready for production:
- M-IRRBB-01 (Deep RL hedging)
- M-FX-01 (XGBoost + LSTM FX prediction)
- M-FTP-01 (Cox PH + RF NMD behavioral)
- M-FTP-02 (Dynamic FTP curve construction)
- M-BSF-01 (Full Deep RL strategic optimization, upgrade from Phase 1 simplified version)

### 13.3 Phase 3 (Months 18-30): Intelligence Layer Maturity

**Bank-adjacent expansion begins. Data Engine intelligence layer matures.**

Deliverables:
- Data Engine intelligence models trained on cumulative onboarding data
- Schema mapping assistance in production
- Anomaly detection in production
- Reconciliation assistance in production
- Federated learning infrastructure (Layer 2 addition)
- A/B testing infrastructure (Layer 4 addition)

Models ready for production:
- D-MAP-01 (Schema mapping assistance)
- D-RECON-01 (Reconciliation assistance)
- S-BEHAV-01 (Stress test behavioral models)
- S-CREDIT-01 (Credit migration models)

### 13.4 Phase 4 (Year 3+): Advanced Capabilities

- Cross-bank meta-learning where consented
- Transfer learning for new market entry (e.g., a Kenyan bank benefits from Ghanaian model priors)
- Advanced RL for strategic optimization
- Climate risk and ESG scenario modeling

## 14. Infrastructure Choices

Depends on the cloud provider decision (which per recent conversations is trending GCP but per Product Documentation is AWS; this needs to be resolved).

### 14.1 If GCP

- **Training:** Vertex AI Custom Training with GPU (a2-highgpu instances for deep learning)
- **Registry:** Vertex AI Model Registry
- **Serving:** Vertex AI Prediction (online endpoints)
- **Feature Store:** Vertex Feature Store
- **Monitoring:** Vertex AI Model Monitoring
- **Experiment Tracking:** Vertex Experiments (MLflow-compatible)
- **Pipeline Orchestration:** Vertex AI Pipelines (Kubeflow-based)

### 14.2 If AWS (per current Product Documentation)

- **Training:** SageMaker Training with p3.2xlarge or ml.g5 GPU instances
- **Registry:** SageMaker Model Registry
- **Serving:** SageMaker Endpoints
- **Feature Store:** Feast on SageMaker (per Product Documentation)
- **Monitoring:** SageMaker Model Monitor
- **Experiment Tracking:** MLflow on SageMaker
- **Pipeline Orchestration:** SageMaker Pipelines or Apache Airflow

Both options provide equivalent capability. The choice affects cost, integration with the rest of the AequorOS stack, and the technical skills required for hiring. The current Product Documentation says AWS; recent architecture conversations trend GCP. Whichever direction is chosen must be reflected consistently across all documents.

## 15. Guardrails and What This Document Does Not Cover

### 15.1 What is NOT in scope for the AI Engine

- **Deterministic regulatory calculations.** RWA per Basel Standardized, LCR, NSFR, EVE, gap analysis — these are calculation-module logic, not AI Engine models. The AI enhances these calculations with forward-looking predictions; it does not replace them.
- **Report generation.** BSD 1-4 template generation, ALCO reports, examiner exports — these are module-level responsibilities.
- **Data ingestion.** Adapters, canonical model, storage — these are Data Engine responsibilities.
- **Business rules and policy configuration.** Bank-defined risk weights, limit thresholds, product mappings — configuration, not ML.

### 15.2 What models CANNOT do in this architecture

- **Make regulatory decisions autonomously.** Every ML output is a recommendation. Human review is required before any regulatory-critical action is taken. This is enforced at the module UI, not just as a policy.
- **Cross-contaminate bank data.** Under no circumstances does one bank's canonical data influence another bank's model predictions without formal consent and audit trail. Enforced at Feature Store and Registry layers.
- **Modify regulatory values silently.** Any AI-driven overlay of a regulatory calculation must produce a distinct output field, not modify the deterministic calculation. Both are surfaced to the user.

### 15.3 What is deferred to later specifications

- Detailed feature specifications per model (`features_M-LIQ-01.md`, etc.)
- Detailed hyperparameter configurations per model
- Detailed MRM workflow implementation
- Data sharing agreement templates for federated learning
- Detailed infrastructure sizing per phase

Each will be produced as a companion document when the corresponding build phase reaches implementation.

## 16. Open Questions

Named honestly for future resolution:

**Q1:** Cloud provider decision — GCP or AWS? Currently inconsistent between recent conversations and Product Documentation.

**Q2:** MVP module scope — Product Documentation says IRRBB + Liquidity + Basel; recent decision seems to be Liquidity + Basel + Balance Sheet Forecasting. Which is authoritative for the Phase 1 model builds?

**Q3:** Deep RL feasibility for MVP — M-BSF-01 as specified requires simulation infrastructure that is Phase 2-level engineering. Should Phase 1 ship a simpler forecasting model (ensemble regression, gradient boosting) and defer full RL to Phase 2?

**Q4:** Sample Bank Limited synthetic data as bootstrap — is 10 years sufficient temporal depth for the deep RL models specifically? Cash flow LSTM is fine on 10 years. RL agents may want longer.

**Q5:** Federated learning consent framework — needs legal review before any cross-bank pattern learning ships in Phase 3.

**Q6:** Per-bank explainability standards — do we need per-bank customization of what explainability payloads look like (e.g., a Nigerian bank may want CBN-format explanations)?

Resolution of these questions blocks corresponding implementation. Do not build ahead of the resolution.

---

## Appendix A: Model Governance Tier Assignments

Complete tier assignments with rationale.

| Model | Tier | Rationale |
|---|---|---|
| M-IRRBB-01 | 2 | Supports hedging decisions, subject to manual review by treasurer before execution |
| M-LIQ-01 | 2 | Forecasts feed dashboards and ALCO; do not directly drive regulatory ratios |
| M-FX-01 | 2 | Supports hedge sizing decisions, subject to manual review |
| M-FTP-01 | 2 | Feeds FTP curve construction; deterministic fallback available |
| M-FTP-02 | 2 | Supports pricing decisions, human review at deal level |
| M-BSF-01 | 1 | Directly drives strategic capital allocation; ALCO and board decision input |
| D-DEDUP-01 | 3 | Utility ingestion function, human review at data quality review |
| D-ANOM-01 | 3 | Alerting only, does not modify regulatory calculations |
| D-MAP-01 | 3 | Onboarding utility, human confirmation required for every mapping |
| D-RECON-01 | 3 | Reconciliation assistance, human review always required |
| S-BEHAV-01 | 2 | Feeds stress test scenarios which support regulatory decisions |
| S-CREDIT-01 | 2 | Feeds credit migration under stress, regulatory-adjacent |

## Appendix B: Fallback Function Registry

Every model must register a fallback. This appendix will grow as models are built.

| Model | Fallback Function | Determinism |
|---|---|---|
| M-IRRBB-01 | `static_duration_matching(bank_id, positions)` | Deterministic given inputs |
| M-LIQ-01 | `linear_trend_extrapolation(bank_id, history_window=30)` | Deterministic given inputs |
| M-FX-01 | `random_walk(current_rate)` | Deterministic given inputs |
| M-FTP-01 | `policy_behavioral_duration(bank_id, product_code)` | Deterministic per bank policy |
| M-FTP-02 | `static_ftp_curve(base_curve, funding_spread, liquidity_premium)` | Deterministic given inputs |
| M-BSF-01 | `baseline_projection_no_optimization(bank_id, scenario)` | Deterministic given inputs |

## Appendix C: References

- Product Documentation, AI/ML Implementation Framework and Model Risk Management sections
- `data_engine.md` sections 4 (canonical model), 12 (intelligence layer), 15 (phasing)
- Federal Reserve SR 11-7: Guidance on Model Risk Management
- OCC 2011-12: Sound Practices for Model Risk Management
- ETH Zurich Deep ALM research (Frontiers in AI, 2023)
- Basel Committee on Banking Supervision guidance on model risk

---

*End of Document*
