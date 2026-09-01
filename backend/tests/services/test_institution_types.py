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
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, InstitutionType
from app.services import banks as banks_service
from app.services import institution_types
from tests.api.helpers import ORG_1, USER_1

# The authoritative institution_type -> institution_class derivation map
# (docs/sdi.md §1.1). Deposit-taking non-bank licences resolve to the 'sdi'
# regime; the universal bank and a banking-group financial holding company to
# 'bank'.
EXPECTED_CLASS = {
    "universal_bank": "bank",
    "savings_and_loans": "sdi",
    "finance_house": "sdi",
    "rural_community_bank": "sdi",
    "microfinance_bank": "sdi",
    "financial_holding_company": "bank",
    "other_rfi": "sdi",
}


def _make_bank(db: Session, *, institution_type: str, org_id: str = ORG_1) -> Bank:
    bank = Bank(
        organization_id=org_id,
        name="Resolver Test Bank",
        short_name="Resolver",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def test_registry_is_fully_seeded_with_the_derivation_map(db_session: Session) -> None:
    rows = {row.type_code: row for row in db_session.scalars(select(InstitutionType))}
    assert set(rows) == set(EXPECTED_CLASS)

    for code, expected_class in EXPECTED_CLASS.items():
        row = rows[code]
        assert row.institution_class == expected_class, code
        # Single-obligor limit is statutory 25% across classes (Act 930 s.62(1)).
        assert row.single_obligor_limit_pct == Decimal("25"), code
        assert row.default_modules, code  # non-empty scoped module set (data for Phase B)
        if expected_class == "sdi":
            assert row.return_family == "sdi", code
            assert row.capital_regime == "s29", code
            assert row.large_exposure_limit_pct == Decimal("15"), code
            assert row.liquidity_binding is True, code
        else:
            assert row.return_family == "bsd", code
            assert row.capital_regime == "crd", code
            assert row.large_exposure_limit_pct == Decimal("20"), code
            assert row.liquidity_binding is False, code


def test_bank_modules_are_a_superset_of_sdi_modules(db_session: Session) -> None:
    """The SDI default config drops only the treasury/trading-book modules
    (docs/sdi.md §3.2, migration 202608210026): FX, FTP and trading Positions.
    An SDI keeps the ALM engines (behavioural, forecasting), IRRBB and Market Data,
    so the SDI set is a strict subset of the universal-bank set."""
    universal = db_session.get(InstitutionType, "universal_bank")
    savings = db_session.get(InstitutionType, "savings_and_loans")
    assert universal is not None and savings is not None
    bank_modules = set(universal.default_modules)
    sdi_modules = set(savings.default_modules)
    assert sdi_modules < bank_modules
    # The bank-only modules are exactly the ones excluded by default for an SDI.
    assert bank_modules - sdi_modules == {"fx", "ftp", "positions"}
    # The ALM + market-data modules an SDI keeps.
    for kept in ("behavioral", "forecasting", "irrbb", "markets"):
        assert kept in sdi_modules


def test_resolver_returns_the_banks_own_type(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    row = institution_types.get_type(db_session, bank)
    assert row.type_code == "savings_and_loans"
    assert institution_types.institution_class(db_session, bank) == "sdi"
    assert institution_types.return_family(db_session, bank) == "sdi"
    assert institution_types.capital_regime(db_session, bank) == "s29"
    assert institution_types.large_exposure_limit_pct(db_session, bank) == Decimal("15")
    assert institution_types.single_obligor_limit_pct(db_session, bank) == Decimal("25")
    assert institution_types.liquidity_binding(db_session, bank) is True


def test_resolver_fails_closed_for_an_unknown_code(db_session: Session) -> None:
    """P0-12: an unknown licence class must NOT resolve to ``universal_bank``.

    This assertion was inverted on 2026-08-21. Until then the resolver fell back
    to the named universal-bank row, which silently selected the entire bank
    regime for a typo — and, because ``universal_bank.default_modules`` is the
    full module set, made ``require_module_access`` grant rather than deny.
    """
    bank = Bank(
        organization_id=ORG_1,
        name="Ghost",
        short_name="Ghost",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="not_a_real_licence_class",
    )
    with pytest.raises(institution_types.InstitutionTypeUnresolved) as excinfo:
        institution_types.get_type(db_session, bank)
    assert "not in the institution_types registry" in str(excinfo.value)
    assert institution_types.try_get_type(db_session, bank) is None


def test_resolver_fails_closed_for_a_blank_code(db_session: Session) -> None:
    bank = Bank(
        organization_id=ORG_1,
        name="Blank",
        short_name="Blank",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="",
    )
    with pytest.raises(institution_types.InstitutionTypeUnresolved) as excinfo:
        institution_types.institution_class(db_session, bank)
    assert "has no institution_type" in str(excinfo.value)


def test_resolver_fails_loud_when_the_registry_is_unseeded(db_session: Session) -> None:
    # With the registry empty, resolving raises rather than defaulting a regime.
    db_session.execute(delete(InstitutionType))
    db_session.flush()
    bank = Bank(
        organization_id=ORG_1,
        name="Orphan",
        short_name="Orphan",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    with pytest.raises(institution_types.InstitutionTypeUnresolved) as excinfo:
        institution_types.get_type(db_session, bank)
    assert "institution_types registry is empty" in str(excinfo.value)


def test_bank_read_rides_the_resolved_discriminator(db_session: Session) -> None:
    _make_bank(db_session, institution_type="finance_house")
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)

    listed = banks_service.list_banks(db_session, ctx).banks
    read = next(b for b in listed if b.name == "Resolver Test Bank")
    assert read.institution_type == "finance_house"
    assert read.institution_type_detail is not None
    assert read.institution_type_detail.type_code == "finance_house"
    assert read.institution_type_detail.institution_class == "sdi"
    assert read.institution_type_detail.return_family == "sdi"
    assert read.institution_type_detail.large_exposure_limit_pct == Decimal("15")
    assert read.institution_type_detail.default_modules

    detail = banks_service.get_bank(db_session, ctx, read.id)
    assert detail.institution_type == "finance_house"
    assert detail.institution_type_detail == read.institution_type_detail


def _load_migration(filename: str) -> ModuleType:
    """Import an alembic revision file by path (``alembic/versions`` is not a package)."""
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / filename
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
