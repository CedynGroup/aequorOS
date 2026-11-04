"""Postgres proof for the ICAAP Pillar 2 parameter seed (202609190056).

Twenty governed codes reach the Pillar 2 engine from the control plane (D-024),
eleven of them as structural tables. The migration must seed exactly the
catalogue's rows and bodies, must not touch an operator-approved generation of
the same code, must be idempotent, and its downgrade must remove only the rows
the seed itself wrote.

The structural half is the part worth proving against a real database rather
than in the hermetic suite: ``value_json`` bodies round-trip through a JSON
column, and a seed that inserted them as text, or dropped a nested null, would
look fine in Python and wrong in Postgres.

This module's other-suite cases live in
``test_icaap_pillar2_parameters_migration_integration.py``.
"""

from __future__ import annotations

__all__ = ["migrated_postgres_schema"]
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from app.icaap.service.parameters import ICAAP_PARAM_CODES
from app.policy.service.regulatory_parameters import (
    P2_PARAMETER_CODES,
    SEED_EFFECTIVE_FROM,
)
from tests.db.test_postgres_migrations_integration import (
    migrated_postgres_schema,
)

pytestmark = pytest.mark.committing_db
PREVIOUS_REVISION = "202609190055"
REVISION = "202609190056"


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "202609190056_icaap_pillar2_parameters.py"
    )
    spec = importlib.util.spec_from_file_location("mig_202609190056", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_chains_after_the_workspace_with_disjoint_codes() -> None:
    module = _migration()
    assert (module.revision, module.down_revision) == (REVISION, PREVIOUS_REVISION)
    assert module.EFFECTIVE_FROM == SEED_EFFECTIVE_FROM
    assert set(module.PARAM_CODES) == set(P2_PARAMETER_CODES)
    assert set(module.PARAM_CODES) & set(ICAAP_PARAM_CODES) == set()
