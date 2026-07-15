"""Training configuration for the cash-flow LSTM.

Runtime settings (artifacts dir, fast-test flag) live in
``app.core.config.CashflowSettings``; this module owns the model version and
the training hyperparameters derived from those settings.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import CashflowSettings

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

    @property
    def total_days(self) -> int:
        return self.train_days + self.val_days

    @classmethod
    def from_settings(cls, settings: CashflowSettings) -> TrainingConfig:
        """Full config by default; a reduced config when ``CASHFLOW_FAST_TEST=1``."""
        if settings.fast_test:
            return cls(window=14, train_days=300, val_days=80, max_epochs=60)
        return cls()
