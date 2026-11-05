"""Postgres proof for the CRD capital-floor control-plane seed (202609190054).

Regulatory audit B2 / M21 (ICAAP P0): ``cet1_min``, ``tier1_min``,
``leverage_min`` (6, fixing the 3% register default), the CCB1 / CCyB / D-SIB
buffers and the AT1 / Tier 2 recognition caps become governed bank-class rows.
The migration must seed exactly the catalogue values, be idempotent against a
row already present for the same scope (an operator-approved generation is
never duplicated or overwritten), and its downgrade must remove only the rows
the seed itself wrote.

This module's other-suite cases live in
``test_crd_capital_floor_parameters_migration_integration.py``.
"""

from __future__ import annotations

__all__ = ["migrated_postgres_schema"]
import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from app.policy.service.regulatory_parameters import (
    CRD_CAPITAL_FLOOR_SEEDS,
    SDI_RECOGNITION_CAP_SEEDS,
    SEED_EFFECTIVE_FROM,
    SEED_PARAMETERS,
)
from tests.db.test_postgres_migrations_integration import (
    migrated_postgres_schema,
)

pytestmark = pytest.mark.committing_db
CODES = (
    "cet1_min",
    "tier1_min",
    "leverage_min",
    "ccb1_pct",
    "ccyb_pct",
    "dsib_buffer_pct",
    "at1_cap_pct_rwa",
    "tier2_cap_pct_rwa",
)
#: D-024: CET1 / Tier 1 await stakeholder confirmation of the conservation-buffer
#: treatment (audit item M20); every other bank seed is confirmed.
PENDING = frozenset({"cet1_min", "tier1_min"})
#: D-042: the SDI class carries the two recognition caps at the pre-2026-09-19
#: platform values, pending.
SDI_EXPECTED = {"at1_cap_pct_rwa": Decimal("1.5"), "tier2_cap_pct_rwa": Decimal("2")}
EXPECTED = {
    "cet1_min": Decimal("6.5"),
    "tier1_min": Decimal("8"),
    "leverage_min": Decimal("6"),
    "ccb1_pct": Decimal("3"),
    "ccyb_pct": Decimal("0"),
    "dsib_buffer_pct": Decimal("0"),
    "at1_cap_pct_rwa": Decimal("1.5"),
    "tier2_cap_pct_rwa": Decimal("2"),
}


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "202609190054_crd_capital_floor_parameters.py"
    )
    spec = importlib.util.spec_from_file_location("mig_202609190054", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_catalogue_and_the_migration_agree() -> None:
    """The migration pins its values (it never imports the live catalogue), and
    today the pinned rows equal the catalogue's exactly — value, citation, unit,
    scope and generation date — so a fresh database seeded by the initial
    control-plane migration and an existing one seeded by this revision agree."""
    specs = {
        spec.param_code: spec
        for spec in SEED_PARAMETERS
        if spec.param_code in CODES and spec.scope_key == "bank"
    }
    assert set(specs) == set(CODES)
    sdi_specs = {
        spec.param_code: spec
        for spec in SEED_PARAMETERS
        if spec.param_code in CODES and spec.scope_key == "sdi"
    }
    # Every capital floor is a scalar percent, so a missing ``value`` (the
    # field is optional since ``value_json`` landed) is itself the defect.
    assert all(spec.value is not None for spec in sdi_specs.values())
    assert {
        code: Decimal(spec.value) for code, spec in sdi_specs.items() if spec.value is not None
    } == SDI_EXPECTED
    assert {spec.confirmation_status for spec in sdi_specs.values()} == {"pending"}
    for code, spec in specs.items():
        assert spec.value is not None, code
        assert Decimal(spec.value) == EXPECTED[code]
        assert (spec.scope_type, spec.scope_key, spec.unit) == (
            "institution_class",
            "bank",
            "percent",
        )
        assert spec.confirmation_status == ("pending" if code in PENDING else "confirmed")
    migration = _migration()
    assert tuple(migration.SEEDS) == CRD_CAPITAL_FLOOR_SEEDS
    assert tuple(migration.SDI_SEEDS) == SDI_RECOGNITION_CAP_SEEDS
    assert migration.EFFECTIVE_FROM == SEED_EFFECTIVE_FROM
    assert tuple(migration.PARAM_CODES) == CODES
