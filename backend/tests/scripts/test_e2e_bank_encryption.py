"""The journey controller only touches its own disposable synthetic tenant."""

from __future__ import annotations

import sqlite3
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


def test_cleanup_retains_other_tenants_and_only_accepts_fixed_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BANK_KMS_ENDPOINT_URL", "http://127.0.0.1:18080")
    get_key_settings.cache_clear()
    options = Options(action="remove", story="revocation", directory=tmp_path)
    with pytest.raises(ValueError, match="disposable e2e.db"):
        run(options)
    database = tmp_path / "e2e.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE organizations (id TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE records (organization_id TEXT, value TEXT)")
        for org in ("OR-KEY00001", "OR-OTHER001"):
            db.execute("INSERT INTO organizations VALUES (?)", (org,))
            db.execute("INSERT INTO records VALUES (?, 'synthetic')", (org,))
    assert run(options) == {"removed": True}
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT id FROM organizations").fetchall() == [("OR-OTHER001",)]
        assert db.execute("SELECT organization_id FROM records").fetchall() == [("OR-OTHER001",)]


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
