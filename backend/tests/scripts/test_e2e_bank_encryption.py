"""The journey controller only touches its own disposable synthetic tenant.

This module's other-suite cases live in ``test_e2e_bank_encryption_integration.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.key_management.settings import get_key_settings
from scripts.e2e_bank_encryption import Options, run


def test_controller_requires_explicit_emulator(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit local KMS endpoint"):
        run(Options(action="create", story="revocation", directory=tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("environment", ["local", "staging", "production"])
def test_controller_refuses_non_test_environments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="APP_ENV=test"):
        run(Options(action="create", story="revocation", directory=tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_controller_refuses_symlinked_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BANK_KMS_ENDPOINT_URL", "http://127.0.0.1:18080")
    get_key_settings.cache_clear()
    target = tmp_path / "other.db"
    target.touch()
    (tmp_path / "e2e.db").symlink_to(target)
    with pytest.raises(ValueError, match="disposable e2e.db"):
        run(Options(action="remove", story="revocation", directory=tmp_path))
    assert target.stat().st_size == 0
