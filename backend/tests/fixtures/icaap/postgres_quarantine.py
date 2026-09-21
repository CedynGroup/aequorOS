"""ICAAP defects that only Postgres surfaces, held as strict expected failures.

Each marker is conditioned on ``TEST_DATABASE_URL`` (the hermetic SQLite run
passes these tests) and strict, so the fix is reported as an unexpected pass
and forces the marker out. Same treatment as ``_FK_INSERT_REFUSED`` in
``tests/db/test_icaap_workspace_migration.py`` (#235).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.exc import DataError

_ON_POSTGRES = os.getenv("TEST_DATABASE_URL") is not None

#: Issue #248: Postgres ``text`` cannot hold a NUL byte, so the legacy narrative
#: the test plants (bypassing ``reject_control_characters``) is refused at the
#: write rather than exercised at the export.
NUL_IN_STORED_NARRATIVE = pytest.mark.xfail(
    _ON_POSTGRES,
    raises=DataError,
    reason="#248: PostgreSQL text fields cannot contain NUL (0x00) bytes",
    strict=True,
)

__all__ = ["NUL_IN_STORED_NARRATIVE"]
