"""ICAAP defects that only Postgres surfaces, held as strict expected failures.

Each marker is conditioned on ``TEST_DATABASE_URL`` (the hermetic SQLite run
passes these tests) and strict, so the fix is reported as an unexpected pass
and forces the marker out. Same treatment as ``_FK_INSERT_REFUSED`` in
``tests/db/test_icaap_workspace_migration.py`` (#235).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.exc import DataError, ProgrammingError

_ON_POSTGRES = os.getenv("TEST_DATABASE_URL") is not None

#: Issue #247: the route modules' seeding fixtures compare the ``date`` column
#: ``bank_reporting_periods.period_end`` to a string, which SQLite coerces and
#: Postgres refuses (``operator does not exist: date = character varying``).
DATE_COMPARED_TO_TEXT = pytest.mark.xfail(
    _ON_POSTGRES,
    raises=ProgrammingError,
    reason="#247: seeding fixture compares period_end (date) to a string parameter",
    strict=True,
)

#: Issue #248: Postgres ``text`` cannot hold a NUL byte, so the narrative the
#: test stores is refused at the write rather than exercised at the export.
NUL_IN_STORED_NARRATIVE = pytest.mark.xfail(
    _ON_POSTGRES,
    raises=DataError,
    reason="#248: PostgreSQL text fields cannot contain NUL (0x00) bytes",
    strict=True,
)

__all__ = ["DATE_COMPARED_TO_TEXT", "NUL_IN_STORED_NARRATIVE"]
