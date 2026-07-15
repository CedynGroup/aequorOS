"""Cash-flow ML module: synthetic series, static baseline, and LSTM forecaster.

Formerly the standalone ``cashflow-ml`` sidecar service; now an internal
package of the risk service. ``app.ml.model`` imports torch and is therefore
imported lazily by ``app.services.cashflow_forecast`` — keep this package's
``__init__`` free of model imports so app startup never pays for the ML
runtime.
"""
