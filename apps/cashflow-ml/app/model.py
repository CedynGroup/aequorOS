"""Two-layer LSTM cash-flow forecaster: training, persistence, and recursive forecasting.

Input per sample is a sliding window of the last ``window`` daily net flows
(z-score normalized with train-window statistics); each timestep additionally
carries the 11 calendar features of the TARGET day, so the network conditions
the whole window on the day being predicted. Architecture per the MVP spec:
LSTM(12 -> 64) -> LSTM(64 -> 32) -> Linear(32 -> 1), trained with Adam + MSE
and early stopping on validation loss.

Holdout metrics compare both methods in serving mode: multi-step forecasts
over the full validation window issued from the end of the train window
(recursive one-step-ahead for the LSTM; the static baseline is multi-step by
construction). RMSE is reported on daily net flows. MAPE is reported on the
cumulative net cash position (the running sum of forecast daily net flows --
the projected liquidity trajectory a treasurer acts on): daily net flows
hover near zero, so a daily-denominator MAPE degenerates into noise even with
flooring, while the cumulative position keeps denominators meaningful.
Denominators are still floored at ``MAPE_DENOMINATOR_FLOOR`` (GHS 0.5M) to
avoid division blowups in the first few days of the window; the floor and the
metric are applied identically to both methods so the comparison stays fair.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC
from pathlib import Path

import numpy as np
import torch
from torch import Tensor, nn

from app.baseline import StaticBaseline
from app.config import MODEL_VERSION, Settings, TrainingConfig
from app.features import CALENDAR_FEATURE_COUNT, calendar_features
from app.synthetic import DailyFlow, generate_daily_series

INPUT_SIZE = 1 + CALENDAR_FEATURE_COUNT
HIDDEN_1 = 64
HIDDEN_2 = 32
MAPE_DENOMINATOR_FLOOR = 0.5  # GHS millions
MODEL_FILE = "model.pt"
SCALER_FILE = "scaler.json"
METRICS_FILE = "metrics.json"
_MIN_IMPROVEMENT = 1e-6


class CashFlowLSTM(nn.Module):
    """LSTM(input -> 64) -> LSTM(64 -> 32) -> Linear(32 -> 1)."""

    def __init__(self, input_size: int = INPUT_SIZE) -> None:
        super().__init__()
        self.lstm1 = nn.LSTM(input_size, HIDDEN_1, batch_first=True)
        self.lstm2 = nn.LSTM(HIDDEN_1, HIDDEN_2, batch_first=True)
        self.head = nn.Linear(HIDDEN_2, 1)

    def forward(self, x: Tensor) -> Tensor:
        out, _ = self.lstm1(x)
        out, _ = self.lstm2(out)
        return self.head(out[:, -1, :]).squeeze(-1)


def _window_tensor(
    window_nets_norm: Sequence[float], target_day: datetime.date
) -> list[list[float]]:
    features = calendar_features(target_day)
    return [[net, *features] for net in window_nets_norm]


def _build_samples(
    nets_norm: Sequence[float],
    dates: Sequence[datetime.date],
    window: int,
    targets: range,
) -> tuple[Tensor, Tensor]:
    xs = [_window_tensor(nets_norm[i - window : i], dates[i]) for i in targets]
    ys = [nets_norm[i] for i in targets]
    return (
        torch.tensor(xs, dtype=torch.float32),
        torch.tensor(ys, dtype=torch.float32),
    )


def _cumulative_mape(daily_predictions: np.ndarray, daily_actuals: np.ndarray) -> float:
    """MAPE on the cumulative net position, denominators floored at 0.5M."""
    cumulative_predictions = np.cumsum(daily_predictions)
    cumulative_actuals = np.cumsum(daily_actuals)
    denominators = np.maximum(np.abs(cumulative_actuals), MAPE_DENOMINATOR_FLOOR)
    errors = np.abs(cumulative_predictions - cumulative_actuals)
    return float(np.mean(errors / denominators) * 100.0)


def _rmse(predictions: np.ndarray, actuals: np.ndarray) -> float:
    return float(np.sqrt(np.mean((predictions - actuals) ** 2)))


def _recursive_predict(
    model: CashFlowLSTM,
    rolling_norm: Sequence[float],
    target_days: Sequence[datetime.date],
    mean: float,
    std: float,
) -> list[float]:
    """Multi-step forecast: feed predictions back while calendar features stay known."""
    rolling = list(rolling_norm)
    predictions: list[float] = []
    model.eval()
    with torch.no_grad():
        for target_day in target_days:
            x = torch.tensor([_window_tensor(rolling, target_day)], dtype=torch.float32)
            predicted_norm = float(model(x).item())
            predictions.append(predicted_norm * std + mean)
            rolling = [*rolling[1:], predicted_norm]
    return predictions


def _fit(
    model: CashFlowLSTM,
    train_data: tuple[Tensor, Tensor],
    val_data: tuple[Tensor, Tensor],
    cfg: TrainingConfig,
) -> None:
    """Adam + MSE with early stopping on validation loss; restores the best weights."""
    train_x, train_y = train_data
    val_x, val_y = val_data
    optimizer = torch.optim.Adam(model.parameters(), lr=cfg.learning_rate)
    loss_fn = nn.MSELoss()
    shuffler = torch.Generator().manual_seed(cfg.seed)

    best_val_loss = float("inf")
    best_state = deepcopy(model.state_dict())
    epochs_without_improvement = 0
    for _epoch in range(cfg.max_epochs):
        model.train()
        permutation = torch.randperm(len(train_x), generator=shuffler)
        for start in range(0, len(train_x), cfg.batch_size):
            batch = permutation[start : start + cfg.batch_size]
            optimizer.zero_grad()
            loss = loss_fn(model(train_x[batch]), train_y[batch])
            loss.backward()
            optimizer.step()

        model.eval()
        with torch.no_grad():
            val_loss = float(loss_fn(model(val_x), val_y).item())
        if val_loss < best_val_loss - _MIN_IMPROVEMENT:
            best_val_loss = val_loss
            best_state = deepcopy(model.state_dict())
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= cfg.patience:
                break

    model.load_state_dict(best_state)
    model.eval()


def train_and_save(
    config: TrainingConfig | None = None,
    artifacts_dir: Path | str | None = None,
) -> dict[str, float | str]:
    """Train the LSTM on the synthetic series, save artifacts, return holdout metrics.

    Deterministic for a given config (fixed data seed + ``torch.manual_seed``),
    so retraining is idempotent.
    """
    settings = Settings()
    cfg = config or TrainingConfig.from_settings(settings)
    out_dir = Path(artifacts_dir) if artifacts_dir is not None else settings.artifacts_dir

    series = generate_daily_series(days=cfg.total_days)
    dates = [flow.date for flow in series]
    nets = np.array([flow.net for flow in series], dtype=np.float64)

    train_nets = nets[: cfg.train_days]
    mean = float(train_nets.mean())
    std = float(train_nets.std())
    if std <= 0.0:
        raise ValueError("train window has zero variance; cannot normalize")
    nets_norm: list[float] = ((nets - mean) / std).tolist()

    torch.manual_seed(cfg.seed)
    model = CashFlowLSTM()
    train_data = _build_samples(nets_norm, dates, cfg.window, range(cfg.window, cfg.train_days))
    val_x, val_y = _build_samples(
        nets_norm, dates, cfg.window, range(cfg.train_days, cfg.total_days)
    )
    _fit(model, train_data, (val_x, val_y), cfg)

    # Serving-mode holdout evaluation: both methods forecast the whole validation
    # window from the end of the train window (recursive for the LSTM). MAPE on
    # the cumulative net position, RMSE on daily net flows (see module docstring).
    rolling_start = nets_norm[cfg.train_days - cfg.window : cfg.train_days]
    lstm_predictions = np.array(
        _recursive_predict(model, rolling_start, dates[cfg.train_days :], mean, std),
        dtype=np.float64,
    )
    actuals = nets[cfg.train_days :]
    baseline = StaticBaseline.fit(series[: cfg.train_days])
    static_predictions = np.array(baseline.predict(dates[cfg.train_days :]), dtype=np.float64)

    lstm_mape = _cumulative_mape(lstm_predictions, actuals)
    static_mape = _cumulative_mape(static_predictions, actuals)
    metrics: dict[str, float | str] = {
        "lstm_mape": round(lstm_mape, 4),
        "lstm_rmse": round(_rmse(lstm_predictions, actuals), 4),
        "static_mape": round(static_mape, 4),
        "static_rmse": round(_rmse(static_predictions, actuals), 4),
        "improvement_pct": round((static_mape - lstm_mape) / static_mape * 100.0, 4),
        "residual_std": round(float(np.std(actuals - lstm_predictions)), 4),
        "trained_at": datetime.datetime.now(UTC).isoformat(timespec="seconds"),
        "model_version": MODEL_VERSION,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), out_dir / MODEL_FILE)
    scaler = {
        "mean": mean,
        "std": std,
        "window": cfg.window,
        "input_size": INPUT_SIZE,
        "model_version": MODEL_VERSION,
    }
    (out_dir / SCALER_FILE).write_text(json.dumps(scaler, indent=2))
    (out_dir / METRICS_FILE).write_text(json.dumps(metrics, indent=2))
    return metrics


def load_artifacts(
    artifacts_dir: Path | str,
) -> tuple[CashFlowLSTM, dict[str, float | int | str], dict[str, float | str]] | None:
    """Load saved model, scaler, and metrics; ``None`` if any artifact is missing."""
    out_dir = Path(artifacts_dir)
    model_path = out_dir / MODEL_FILE
    scaler_path = out_dir / SCALER_FILE
    metrics_path = out_dir / METRICS_FILE
    if not (model_path.exists() and scaler_path.exists() and metrics_path.exists()):
        return None
    scaler = json.loads(scaler_path.read_text())
    metrics = json.loads(metrics_path.read_text())
    model = CashFlowLSTM(input_size=int(scaler["input_size"]))
    model.load_state_dict(torch.load(model_path, map_location="cpu", weights_only=True))
    model.eval()
    return model, scaler, metrics


def forecast_net_flows(
    model: CashFlowLSTM,
    scaler: dict[str, float | int | str],
    history: Sequence[DailyFlow],
    horizon: int,
) -> list[float]:
    """Recursive one-step-ahead forecast for the ``horizon`` days after ``history``.

    Each step feeds predicted net flows back into the rolling window while the
    calendar features of the next target day are known in advance.
    """
    mean = float(scaler["mean"])
    std = float(scaler["std"])
    window = int(scaler["window"])
    if len(history) < window:
        raise ValueError(f"history must contain at least {window} days")

    rolling = [(flow.net - mean) / std for flow in history[-window:]]
    as_of = history[-1].date
    target_days = [as_of + datetime.timedelta(days=step) for step in range(1, horizon + 1)]
    return _recursive_predict(model, rolling, target_days, mean, std)
