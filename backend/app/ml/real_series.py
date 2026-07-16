"""Load the simulator's real 10-year daily cash-flow series as ``DailyFlow``s.

The cash-flow LSTM trains on a ``list[DailyFlow]`` (see ``app.ml.synthetic``).
By default that list comes from the in-code synthetic generator; when
``CASHFLOW_USE_REAL_SERIES`` is set, training and serving read the real daily
series produced by ``data/simulator`` instead (parquet ``daily_cashflows``
panel). ``net`` is already in GHS millions there, matching ``DailyFlow.net``.

This is the additive half of the LSTM retrain seam: no change to the model,
scaler, metrics, or HTTP contract — only the source of the ``DailyFlow`` list.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from app.ml.synthetic import DailyFlow

# Repo root: backend/app/ml/real_series.py -> parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PANEL = _REPO_ROOT / "data" / "history" / "panels" / "daily_cashflows"
_TRUTHY = ("1", "true", "yes", "on")


def real_series_enabled() -> bool:
    return os.environ.get("CASHFLOW_USE_REAL_SERIES", "").strip().lower() in _TRUTHY


def real_series_path() -> Path:
    override = os.environ.get("CASHFLOW_REAL_SERIES_PATH")
    return Path(override) if override else _DEFAULT_PANEL


def load_real_daily_series(path: Path | str | None = None) -> list[DailyFlow]:
    """Read the real daily cash-flow series (ascending by date) as ``DailyFlow``s.

    Raises ``FileNotFoundError`` if the panel is absent so callers can fall back
    to the synthetic series (serving) or fail loudly (training).
    """
    root = Path(path) if path is not None else real_series_path()
    files = sorted(root.rglob("*.parquet")) if root.is_dir() else ([root] if root.exists() else [])
    if not files:
        raise FileNotFoundError(
            f"No daily_cashflows parquet found under {root}. Generate it with "
            "`python -m data.simulator.run` or unset CASHFLOW_USE_REAL_SERIES."
        )
    frame = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    frame = frame.sort_values("date").reset_index(drop=True)
    dates = pd.to_datetime(frame["date"]).dt.date
    inflow = (frame["inflow_ghs"] / 1e6).to_numpy()
    outflow = (frame["outflow_ghs"] / 1e6).to_numpy()
    net = frame["net_ghs"].to_numpy()  # already GHS millions
    return [
        DailyFlow(date=d, inflow=float(i), outflow=float(o), net=float(n))
        for d, i, o, n in zip(dates, inflow, outflow, net, strict=True)
    ]
