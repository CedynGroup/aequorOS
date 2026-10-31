"""The relocated loader keeps the existing repository-data default."""

from pathlib import Path

import pytest

from app.forecasting.ml.real_series import real_series_path


def test_default_panel_path_is_independent_of_the_working_directory(
    monkeypatch: pytest.MonkeyPatch, pytestconfig: pytest.Config, tmp_path: Path
) -> None:
    monkeypatch.delenv("CASHFLOW_REAL_SERIES_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    assert real_series_path() == (
        pytestconfig.rootpath.parent / "data" / "history" / "panels" / "daily_cashflows"
    )
