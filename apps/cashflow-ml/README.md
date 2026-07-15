# cashflow-ml

Standalone LSTM daily cash-flow forecasting service for the AequorOS MVP
(Sample Bank Ltd, GHS millions, as-of date 2026-03-31). It trains a real
PyTorch model on a deterministic synthetic series and serves forecasts with
confidence bands, benchmarked against a static behavioral baseline.

## Quickstart

```bash
uv sync                       # or: mise run cashflow-ml:sync
mise run cashflow-ml:dev      # FastAPI dev server on http://127.0.0.1:8010
```

Train once (also happens lazily on the first LSTM forecast call):

```bash
curl -X POST http://127.0.0.1:8010/train
# or offline: mise run cashflow-ml:train
```

Fetch forecasts and history:

```bash
curl "http://127.0.0.1:8010/forecast?horizon=30&mode=lstm"
curl "http://127.0.0.1:8010/forecast?horizon=90&mode=static"
curl "http://127.0.0.1:8010/history?days=90"
curl http://127.0.0.1:8010/health
```

## Endpoints

| Endpoint | Description |
| --- | --- |
| `GET /health` | `{status, model_trained, model_version}` |
| `POST /train` | Trains (idempotently) and returns holdout metrics |
| `GET /forecast?horizon=30\|60\|90&mode=lstm\|static` | camelCase forecast points with confidence bands (422 on other values) |
| `GET /history?days=90` | Trailing actual daily net flows for charting |

`/forecast` and `/history` serialize camelCase (`asOfDate`, `netFlow`,
`lstmMape`, ...); `/health` and `/train` return snake_case metric payloads.

## Model

- Two-layer LSTM (64 and 32 units) + dense output head, MSE loss, Adam (lr 5e-4,
  tuned down from 1e-3), batch 32, up to 300 epochs with early stopping
  (patience 20) on validation loss, `torch.manual_seed(42)`, CPU-only.
- Input: sliding window of the last 28 daily net flows (z-score normalized with
  train-window stats); every timestep also carries the 11 calendar features of the
  target day (day-of-week one-hot, day-of-month/31, month-end-window, payday, and
  mid-month flags).
- Split: first 600 days train, last 130 days validation. Multi-step forecasts are
  recursive one-step-ahead, feeding predictions back while using known future
  calendar features.
- Confidence band: +/-1.96 x validation residual std, widening by `sqrt(day/7)`
  clamped to [1.0, 2.5]; static mode has degenerate bands (`lower == upper`).
- Artifacts (`artifacts/model.pt`, `scaler.json`, `metrics.json`) are gitignored
  and regenerated deterministically by `POST /train`.

## Baseline and metrics

The static behavioral baseline is the mean net flow per
(day-of-week, month-end-window, payday) bucket computed from the train window
only. Holdout metrics compare both methods in serving mode: each forecasts the
entire 130-day validation window from the end of the train window (recursively
for the LSTM; the static method is multi-step by construction).

- RMSE is reported on daily net flows.
- MAPE is reported on the cumulative net cash position (running sum of daily
  net flows -- the projected liquidity trajectory). Daily net flows hover near
  zero, so a daily-denominator MAPE degenerates into noise; the cumulative
  position keeps denominators meaningful. Denominators are still floored at
  GHS 0.5M to avoid division blowups in the first days of the window.

Both the metric and the floor apply identically to both methods. The LSTM
wins because the static bucket means accumulate bias over the horizon
(no trend, seasonality, or mid-month coupon awareness), while the recursive
LSTM tracks them through its input window and calendar features.

## Synthetic series

Deterministic per seed (numpy `PCG64(42)`), regenerated on demand, never
persisted: business-day base flows (inflow ~ N(9.5, 0.8), outflow ~ N(9.0, 0.9)),
weekends at 12% volume, a GHS 11M salary run on each of the last two business
days of the month, a GHS 5M corporate inflow bump on the 25th, GHS 3M of
mid-month (14th-16th) coupon inflows, +/-6% annual seasonality peaking
mid-December, and +8%/year linear growth.

## Development

```bash
mise run cashflow-ml:check    # lint + typecheck + tests (fast model config)
CASHFLOW_FAST_TEST=1 uv run pytest -q
```

`CASHFLOW_FAST_TEST=1` switches training to a reduced config (window 14,
300/80 train/val days, max 60 epochs) so the suite stays fast; tests redirect
artifacts via `CASHFLOW_ARTIFACTS_DIR`. Configuration uses the `CASHFLOW_`
env prefix (e.g. `CASHFLOW_CORS_ORIGINS_RAW=http://localhost:3000`).
