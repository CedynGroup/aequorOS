"""The journey controller only touches its own disposable synthetic tenant.

This module's other-suite cases live in ``test_e2e_bank_encryption.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.key_management.settings import get_key_settings
from scripts.e2e_bank_encryption import Options, run
from tests.support.database import isolated_database


def test_cleanup_retains_other_tenants_and_only_accepts_fixed_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BANK_KMS_ENDPOINT_URL", "http://127.0.0.1:18080")
    get_key_settings.cache_clear()
    options = Options(action="remove", story="revocation", directory=tmp_path)
    with pytest.raises(ValueError, match="disposable e2e.db"):
        run(options)
    database = tmp_path / "e2e.db"
    with isolated_database(f"sqlite:///{database}") as owner:
        with owner.sqlite_connection() as db:
            db.execute("CREATE TABLE organizations (id TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE records (organization_id TEXT, value TEXT)")
            for org in ("OR-KEY00001", "OR-OTHER001"):
                db.execute("INSERT INTO organizations VALUES (?)", (org,))
                db.execute("INSERT INTO records VALUES (?, 'synthetic')", (org,))
            db.commit()
        assert run(options) == {"removed": True}
        with owner.sqlite_connection() as db:
            assert db.execute("SELECT id FROM organizations").fetchall() == [("OR-OTHER001",)]
            assert db.execute("SELECT organization_id FROM records").fetchall() == [
                ("OR-OTHER001",)
            ]
