"""Tests for test_ingestion_position_locks."""

from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.data_engine.service.ingestion import (
    _lock_position_identities,
)
from app.models import Bank
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.support.helpers import ORG_1


def test_postgresql_lock_statement_has_bounded_parameters_for_large_batches() -> None:
    """The emitted PostgreSQL statement binds 70,000 references as one protocol parameter."""
    db = MagicMock(spec=Session)
    db.get_bind.return_value.dialect = postgresql.dialect()
    references = {f"LN-{index:06d}" for index in range(70_000)}
    _lock_position_identities(
        db,
        TenantContext(organization_id=ORG_1),
        Bank(id=SAMPLE_BANK_ID),
        "EXCEL_CSV",
        references,
    )
    db.scalars.assert_called_once()
    statement = db.scalars.call_args.args[0]
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}
    )
    assert str(compiled).endswith("ORDER BY canonical_positions.id FOR NO KEY UPDATE")
    assert len(compiled.params) == 4
    assert set(compiled.params["position_references"]) == references
    assert len(compiled.params["position_references"]) == 70_000
    db.scalars.return_value.all.assert_called_once_with()


def test_sqlite_skips_the_position_lock_query() -> None:
    db = MagicMock(spec=Session)
    db.get_bind.return_value.dialect = sqlite.dialect()
    _lock_position_identities(
        db,
        TenantContext(organization_id=ORG_1),
        Bank(id=SAMPLE_BANK_ID),
        "EXCEL_CSV",
        {"LN-0001"},
    )
    db.scalars.assert_not_called()
