"""BoG CRD (June 2018) ¶98: both fact planes admit the net capital basis."""

from __future__ import annotations

from collections.abc import Callable
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import cast

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.requirement("BoG CRD (June 2018) ¶98")


def test_migration_admits_credit_basis_without_changing_accounting_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BoG CRD (June 2018) ¶98: upgrade admits capital evidence on both planes.

    Execute the real Alembic batch rewrite, insert the new group, and prove
    accounting rows survive. Downgrade requires removal of new derived rows
    before narrowing the constraint; it must not erase financial evidence.
    """
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/202610080085_credit_exposure_basis.py"
    )
    spec = spec_from_file_location("credit_exposure_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    upgrade = cast(Callable[[], None], migration.upgrade)
    downgrade = cast(Callable[[], None], migration.downgrade)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        # Minimal tables exercise the owned CHECK contract through its real consumer.
        for table in ("bank_financial_facts", "current_financial_facts"):
            connection.execute(
                text(
                    f"CREATE TABLE {table} (fact_group TEXT NOT NULL, amount NUMERIC NOT NULL, "
                    f"CONSTRAINT ck_{table}_fact_group CHECK "
                    "(fact_group IN ('balance_sheet', 'loan_exposure')))"
                )
            )
            connection.execute(text(f"INSERT INTO {table} VALUES ('loan_exposure', 1000)"))
            with pytest.raises(IntegrityError):
                connection.execute(text(f"INSERT INTO {table} VALUES ('credit_exposure', 750)"))
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        upgrade()
        for table in ("bank_financial_facts", "current_financial_facts"):
            connection.execute(text(f"INSERT INTO {table} VALUES ('credit_exposure', 750)"))
            assert (
                connection.execute(
                    text(f"SELECT amount FROM {table} WHERE fact_group='loan_exposure'")
                ).scalar_one()
                == 1000
            )
            assert (
                connection.execute(
                    text(f"SELECT amount FROM {table} WHERE fact_group='credit_exposure'")
                ).scalar_one()
                == 750
            )
            connection.execute(text(f"DELETE FROM {table} WHERE fact_group='credit_exposure'"))
        downgrade()
        for table in ("bank_financial_facts", "current_financial_facts"):
            with pytest.raises(IntegrityError):
                connection.execute(text(f"INSERT INTO {table} VALUES ('credit_exposure', 750)"))
            assert connection.execute(text(f"SELECT amount FROM {table}")).scalar_one() == 1000
    engine.dispose()


def test_credit_source_provenance_migration_preserves_periods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BoG CRD (June 2018) ¶98: unknown historic source basis stays unknown."""
    path = (
        Path(__file__).resolve().parents[2] / "alembic/versions/202610080086_credit_source_basis.py"
    )
    spec = spec_from_file_location("credit_source_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    upgrade = cast(Callable[[], None], migration.upgrade)
    downgrade = cast(Callable[[], None], migration.downgrade)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE bank_reporting_periods (id TEXT PRIMARY KEY)"))
        connection.execute(text("INSERT INTO bank_reporting_periods VALUES ('existing')"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        upgrade()
        assert (
            connection.execute(
                text("SELECT credit_source_basis FROM bank_reporting_periods WHERE id='existing'")
            ).scalar_one()
            is None
        )
        connection.execute(text("UPDATE bank_reporting_periods SET credit_source_basis='[]'"))
        assert (
            connection.execute(
                text("SELECT credit_source_basis FROM bank_reporting_periods")
            ).scalar_one()
            == "[]"
        )
        downgrade()
        assert (
            connection.execute(text("SELECT id FROM bank_reporting_periods")).scalar_one()
            == "existing"
        )
    engine.dispose()
