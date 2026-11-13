"""The configure cutover inventory stays consistent with the mapped schema."""

from __future__ import annotations

import app.models  # noqa: F401 - importing the registry registers every mapped table
from app.db.base import Base
from scripts.authorization_configure_routes import CONFIGURE_ROUTES


def test_every_storage_table_names_a_mapped_table() -> None:
    """A typo in ``storage_table`` would publish impact metadata naming a table
    no model owns, so every value must be a real ``__tablename__``."""
    named = {route.storage_table for route in CONFIGURE_ROUTES if route.storage_table}
    missing = sorted(named - set(Base.metadata.tables))
    assert missing == [], f"storage_table values with no mapped table: {missing}"
