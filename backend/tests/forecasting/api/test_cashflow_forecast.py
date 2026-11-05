"""Cash-flow forecast endpoints against the ACTUAL primary database.

Invariants (the real bank's daily history moves, so no frozen goldens): the
forecast horizon controls the point count and dates advance one day at a time
from the series' own as-of date; LSTM bands bracket the point forecast and widen
with the horizon while static bands are degenerate; the model scope and artifact
location follow the bank's own history length (bank-specific vs generic, no
spillover); history returns the trailing window ending on the as-of date;
422/404/503 paths never trigger training.

The ML module runs in fast-test config with a module-scoped isolated artifacts
directory so the LSTM trains at most once for the whole module. Order matters:
the validation, tenant, and torch-failure tests run first and assert no training
was triggered (empty artifacts dir); the LSTM forecast test then lazy-trains,
and later tests reuse the warm model. The routing test at the end is hermetic
(no DB) and therefore deliberately NOT gated on the real database.

This module's other-suite cases live in ``test_cashflow_forecast_integration.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.forecasting.ml.config import TrainingConfig
from app.forecasting.ml.synthetic import generate_daily_series
from app.forecasting.service import cashflow_forecast
from tests.real_data import (
    REAL_ORG_ID,
)


def test_build_service_routes_bank_specific_vs_generic_with_isolated_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bank with enough of its own history trains a bank-specific model in a
    per-tenant artifact dir; a bank without falls back to the shared generic model.
    Proves the no-spillover routing without paying for LSTM training (hermetic)."""
    ctx = TenantContext(organization_id=REAL_ORG_ID, actor_user_id=None)
    total = TrainingConfig.from_settings(get_settings().cashflow).total_days

    bank_specific = str(uuid4())
    long_series = generate_daily_series(days=total + 30)  # enough own history
    monkeypatch.setattr(
        cashflow_forecast, "load_bank_daily_series", lambda _db, _ctx, _bid: long_series
    )
    service = cashflow_forecast._build_service(None, ctx, bank_specific)  # type: ignore[arg-type]
    assert service._scope == "bank_specific"
    assert service._series is not long_series  # trains on the recent window slice
    assert len(service._series) == total
    artifacts = str(service._artifacts_dir)
    assert REAL_ORG_ID in artifacts and bank_specific in artifacts  # per-tenant path

    generic_bank = str(uuid4())
    monkeypatch.setattr(
        cashflow_forecast, "load_bank_daily_series", lambda _db, _ctx, _bid: long_series[:5]
    )
    generic = cashflow_forecast._build_service(None, ctx, generic_bank)  # type: ignore[arg-type]
    assert generic._scope == "generic"
    assert generic._artifacts_dir.name == "generic"
    # A generic bank's model dir carries neither tenant id — no spillover either way.
    assert generic_bank not in str(generic._artifacts_dir)
