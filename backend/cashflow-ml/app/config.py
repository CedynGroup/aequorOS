"""Service settings and training configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARTIFACTS_DIR = APP_ROOT / "artifacts"
MODEL_VERSION = "lstm-v1.0.0"


class Settings(BaseSettings):
    """Environment-driven settings (prefix ``CASHFLOW_``)."""

    model_config = SettingsConfigDict(
        env_prefix="CASHFLOW_",
        env_file=".env",
        extra="ignore",
        protected_namespaces=(),
    )

    app_name: str = "cashflow-ml"
    fast_test: bool = False
    artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR
    cors_origins_raw: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]


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
    def from_settings(cls, settings: Settings) -> TrainingConfig:
        """Full config by default; a reduced config when ``CASHFLOW_FAST_TEST=1``."""
        if settings.fast_test:
            return cls(window=14, train_days=300, val_days=80, max_epochs=60)
        return cls()
