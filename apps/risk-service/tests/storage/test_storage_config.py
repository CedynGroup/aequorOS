from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.storage.config import StorageEngineSettings, StorageRetiredError, enforce_retirement


@pytest.fixture(autouse=True)
def isolate_from_local_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Run from an empty directory so the developer's .env (which carries a
    # real STORAGE_RETIRE_AFTER) cannot shadow the values under test.
    monkeypatch.chdir(tmp_path)


def settings(**overrides: object) -> StorageEngineSettings:
    values: dict[str, object] = {
        "STORAGE_BACKEND": "minio",
        "STORAGE_ENV": "mvp",
        "S3_ENDPOINT": "https://minio.test",
        "S3_ACCESS_KEY": "key",
        "S3_SECRET_KEY": "secret",
    }
    values.update(overrides)
    return StorageEngineSettings.model_validate(values)


class TestRetirementEnforcement:
    def test_mvp_requires_a_retirement_date(self) -> None:
        with pytest.raises(StorageRetiredError, match="STORAGE_RETIRE_AFTER must be set"):
            enforce_retirement(settings(), today=date(2026, 7, 14))

    def test_mvp_past_retirement_refuses_to_initialize(self) -> None:
        stale = settings(STORAGE_RETIRE_AFTER="2026-01-01")
        with pytest.raises(StorageRetiredError, match="passed its retirement date"):
            enforce_retirement(stale, today=date(2026, 7, 14))

    def test_mvp_before_retirement_is_allowed(self) -> None:
        enforce_retirement(settings(STORAGE_RETIRE_AFTER="2027-01-14"), today=date(2026, 7, 14))

    def test_non_mvp_environments_have_no_retirement_gate(self) -> None:
        enforce_retirement(settings(STORAGE_ENV="prod"), today=date(2026, 7, 14))
