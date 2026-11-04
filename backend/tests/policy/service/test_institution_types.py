"""Institution-type discriminator: registry seed, resolver, and BankRead ride.

Phase A (docs/sdi.md §1) lays the typed ``institution_type`` discriminator every
future SDI scoping keys off. These hermetic tests pin:

- the seeded ``institution_types`` registry and its institution_type -> class
  derivation map (savings_and_loans -> sdi, universal_bank -> bank, ...);
- the resolver's FAIL-CLOSED discipline (P0-12, enterprise audit 2026-08-20):
  an unknown or blank licence class raises instead of resolving to the bank
  regime, because that substitution used to pick the CAR floor, provisioning
  grid, DPD boundaries and LMTD floors AND make the API module gate fail open;
- the resolved discriminator riding on ``BankRead`` exactly as jurisdiction does.

No capital/liquidity/return behaviour is asserted here — Phase A changes none.

This module's other-suite cases live in ``test_institution_types_integration.py``.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from app.policy.service import institution_types


def _load_migration(filename: str) -> ModuleType:
    """Import an alembic revision file by path (``alembic/versions`` is not a package)."""
    path = Path(__file__).resolve().parents[3] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"_migration_{filename[:12]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_catalogue_matches_the_migration_chain_end_state() -> None:
    """``institution_types.SEED_TYPES`` is the single catalogue the hermetic
    conftest seed and the e2e bootstrap build the registry from; a deployed
    database gets the same rows from the migration chain. Nothing imports across
    that boundary — a migration must keep the historical snapshot of the change
    it made — so this test is what keeps the two in step. If it fails, either the
    catalogue changed without a migration, or a migration landed without the
    catalogue.
    """
    created = _load_migration("202608190018_institution_types_registry.py")
    widened = _load_migration("202608210026_sdi_module_set_expand.py")
    credit = _load_migration("202609010046_credit_default_module.py")

    # Replay the chain: 202608190018 inserts, 202608210026 rewrites the SDI
    # module set on every 'sdi'-class row, 202609010046 inserts 'credit' after
    # 'capital' on every row.
    def _with_credit(modules: list[str]) -> list[str]:
        if credit.MODULE in modules:
            return modules
        anchor = modules.index("capital") + 1 if "capital" in modules else len(modules)
        return [*modules[:anchor], credit.MODULE, *modules[anchor:]]

    replayed = {}
    for (
        code,
        display_name,
        institution_class_,
        return_family_,
        capital_regime_,
        large_exposure,
        single_obligor,
        liquidity_binding_,
        modules,
    ) in created.SEED_ROWS:
        replayed[code] = {
            "type_code": code,
            "display_name": display_name,
            "institution_class": institution_class_,
            "return_family": return_family_,
            "capital_regime": capital_regime_,
            "large_exposure_limit_pct": Decimal(large_exposure),
            "single_obligor_limit_pct": Decimal(single_obligor),
            "liquidity_binding": liquidity_binding_,
            "default_modules": _with_credit(
                list(widened._SDI_MODULES) if institution_class_ == "sdi" else list(modules)
            ),
        }

    catalogue = {row["type_code"]: row for row in institution_types.seed_rows()}
    assert catalogue == replayed
