"""The BoG return-code recode must never be able to silently do nothing again.

Audit finding P0-18: migration 202608150013 rewrites FORCE-RLS reporting rows.
Run as the tenant-scoped app role it matched zero rows, reported success, and
alembic stamped it applied — the recode simply did not happen and nothing said
so. Its own docstring recorded the hazard and left the remediation to a human
remembering to re-run it under a BYPASSRLS role.

These tests pin the replacement: ``force_rls_suspended`` makes the rewrite
actually see every tenant, and where it cannot it raises instead of returning.

This module's other-suite cases live in

``test_bog_return_recode_migration_integration.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy.exc import ProgrammingError

from app.db.session import RlsBlindError, force_rls_suspended

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


class _Connection:
    """Enough of a SQLAlchemy connection to drive the suspension helper."""

    def __init__(
        self,
        *,
        dialect: str = "postgresql",
        bypasses_rls: bool = False,
        forced: tuple[str, ...] = (),
        owns_tables: bool = True,
    ) -> None:
        self.dialect = SimpleNamespace(name=dialect)
        self._bypasses_rls = bypasses_rls
        self._forced = forced
        self._owns_tables = owns_tables
        self.statements: list[str] = []

    def scalar(self, _statement: object) -> object:
        return self._bypasses_rls

    def execute(self, _statement: object, _params: object = None):
        return SimpleNamespace(scalars=lambda: self._forced)

    def exec_driver_sql(self, statement: str) -> None:
        if not self._owns_tables and "NO FORCE" in statement:
            raise ProgrammingError("ALTER TABLE", {}, Exception("must be owner of table"))
        self.statements.append(statement)


def test_suspension_is_a_noop_when_the_role_already_sees_every_tenant() -> None:
    connection = _Connection(bypasses_rls=True, forced=("regulatory_packages",))

    with force_rls_suspended(cast("Connection", connection), "regulatory_packages") as suspended:
        assert suspended == ()

    assert connection.statements == []


def test_suspension_is_a_noop_on_sqlite() -> None:
    connection = _Connection(dialect="sqlite")

    with force_rls_suspended(cast("Connection", connection), "regulatory_packages") as suspended:
        assert suspended == ()

    assert connection.statements == []


def test_suspension_lifts_and_restores_force_rls_for_a_tenant_scoped_role() -> None:
    """The rewrite is what has to work, not the operator's memory."""
    connection = _Connection(forced=("regulatory_packages", "return_signing_policies"))

    with force_rls_suspended(
        cast("Connection", connection), "regulatory_packages", "return_signing_policies"
    ) as suspended:
        assert suspended == ("regulatory_packages", "return_signing_policies")
        assert all("NO FORCE ROW LEVEL SECURITY" in stmt for stmt in connection.statements)

    restored = [stmt for stmt in connection.statements if "NO FORCE" not in stmt]
    assert len(restored) == 2
    assert all("FORCE ROW LEVEL SECURITY" in stmt for stmt in restored)


def test_suspension_raises_loudly_when_it_cannot_see_the_rows() -> None:
    """The one outcome that must not exist is "wrote nothing, said nothing"."""
    connection = _Connection(forced=("regulatory_packages",), owns_tables=False)

    with (
        pytest.raises(RlsBlindError) as excinfo,
        force_rls_suspended(cast("Connection", connection), "regulatory_packages"),
    ):
        raise AssertionError("body must not run")

    assert "MIGRATION_DATABASE_URL" in str(excinfo.value)
    assert "regulatory_packages" in str(excinfo.value)
