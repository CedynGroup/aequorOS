"""Training configuration, model registry, and shared result types."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import BehavioralSettings

# Model slugs (also the API path segment) -> persisted model version.
MODEL_VERSIONS: dict[str, str] = {
    "nmd-duration": "nmd-gbm-v1.0.0",
    "prepayment": "prepay-gbm-v1.0.0",
    "deposit-stability": "stability-gbm-v1.0.0",
}
MODEL_SLUGS: tuple[str, ...] = tuple(MODEL_VERSIONS)

# assumption_type each model writes into behavioral_assumptions.
ASSUMPTION_TYPE: dict[str, str] = {
    "nmd-duration": "NMD_DURATION",
    "prepayment": "PREPAYMENT_RATE",
    "deposit-stability": "DEPOSIT_STABILITY",
}
UNIT: dict[str, str] = {
    "nmd-duration": "months",
    "prepayment": "annual_rate",
    "deposit-stability": "ratio",
}


@dataclass(frozen=True, slots=True)
class BehavioralTrainingConfig:
    """Hyperparameters + robustness gate for the behavioral GBMs."""

    window_months: int = 36          # trailing history used for training
    min_months: int = 18             # gate: distinct as-of months required for ML
    min_samples: int = 24            # gate: product-month label rows required for ML
    cv_folds: int = 4                # expanding-window time-series CV folds
    forward_window: int = 6          # deposit-stability retained-fraction horizon
    # HistGradientBoostingRegressor hyperparameters (regularized for small data).
    max_leaf_nodes: int = 15
    min_samples_leaf: int = 20
    l2_regularization: float = 1.0
    max_iter: int = 300
    learning_rate: float = 0.05
    random_state: int = 42

    @classmethod
    def from_settings(cls, settings: BehavioralSettings) -> BehavioralTrainingConfig:
        """Full config by default; a lowered gate under ``BEHAVIORAL_FAST_TEST=1``."""
        if settings.fast_test:
            return cls(window_months=36, min_months=3, min_samples=6, cv_folds=2)
        return cls()


@dataclass(frozen=True, slots=True)
class ProductEstimate:
    product_code: str
    assumption_type: str
    value: float
    unit: str
    confidence: float
    method: str  # "ml" | "baseline"
    extra: dict = field(default_factory=dict)  # e.g. {"corePct": .8} or {"incentiveCurve": [...]}


@dataclass(frozen=True, slots=True)
class Accuracy:
    cv_rmse: float | None
    cv_mae: float | None
    sample_count: int
    month_coverage: int
    method: str


@dataclass(frozen=True, slots=True)
class ModelResult:
    model_id: str
    model_version: str
    method: str
    as_of_date: str | None
    accuracy: Accuracy
    products: list[ProductEstimate]
