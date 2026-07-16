"""Per-tenant behavioral ML models: NMD-duration, prepayment, deposit-stability.

Each model is trained on ONE bank's own ingested canonical history (not our
synthetic data) and produces per-product behavioral estimates that are applied
as ``behavioral_assumptions`` the ALM engines consume. Models are gradient-
boosted (scikit-learn ``HistGradientBoosting``) with a robustness contract:
a minimum-data gate that falls back to a statistical/policy baseline, time-
series cross-validation, and a per-product confidence score.

This package keeps scikit-learn/torch OUT of the import path — heavy libraries
are imported lazily inside the training modules, mirroring ``app/ml/__init__``,
so importing the package (e.g. for config/history helpers) stays cheap and a
missing/broken sklearn install degrades to the baseline rather than breaking
service startup.
"""
