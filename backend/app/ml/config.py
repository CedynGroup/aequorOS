"""Training configuration for the cash-flow LSTM.

Runtime settings (artifacts dir, fast-test flag) live in
``app.core.config.CashflowSettings``; this module owns the model version and
the training hyperparameters derived from those settings.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import CashflowSettings
from app.ml.real_series import real_series_enabled

MODEL_VERSION = "lstm-v1.0.0"


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    """Hyperparameters for the LSTM trainer and its train/validation split."""

    window: int = 28
    train_days: int = 600
    val_days: int = 130
    max_epochs: int = 300
    patience: int = 20
    batch_size: int = 32
    # Tuned down from the spec's initial 1e-3: 5e-4 converges to a better
    # validation optimum on both daily RMSE and cumulative-position MAPE.
    learning_rate: float = 5e-4
    seed: int = 42
    # When true, the trainer/service read the real 10-year daily series from the
    # simulator parquet panel instead of the in-code synthetic generator.
    use_real_series: bool = False

    @property
    def total_days(self) -> int:
        return self.train_days + self.val_days

    @classmethod
    def for_real_series(cls) -> TrainingConfig:
        """Config for the real 10-year daily series (~3,650 days): 8y train / 2y val."""
        return cls(window=28, train_days=2920, val_days=730, max_epochs=300, use_real_series=True)

    @classmethod
    def from_settings(cls, settings: CashflowSettings) -> TrainingConfig:
        """Full config by default; reduced under ``CASHFLOW_FAST_TEST=1``; the real
        10-year series under ``CASHFLOW_USE_REAL_SERIES``."""
        if real_series_enabled():
            return cls.for_real_series()
        if settings.fast_test:
            return cls(window=14, train_days=300, val_days=80, max_epochs=60)
        return cls()
