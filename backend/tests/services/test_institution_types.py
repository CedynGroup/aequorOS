"""Institution-type discriminator: registry seed, resolver, and BankRead ride.

Phase A (docs/sdi.md §1) lays the typed ``institution_type`` discriminator every
future SDI scoping keys off. These hermetic tests pin:

- the seeded ``institution_types`` registry and its institution_type -> class
  derivation map (savings_and_loans -> sdi, universal_bank -> bank, ...);
- the resolver's fail-loud discipline (mirror of ``jurisdictions.base_currency``)
  and its named ``universal_bank`` fallback;
- the resolved discriminator riding on ``BankRead`` exactly as jurisdiction does.

No capital/liquidity/return behaviour is asserted here — Phase A changes none.
"""

from __future__ import annotations

from decimal import Decimal

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


def test_resolver_falls_back_to_universal_bank_for_an_unknown_code(db_session: Session) -> None:
    # A detached bank carrying a code with no registry row: the resolver falls
    # back to the named universal_bank row rather than inventing a regime.
    bank = Bank(
        organization_id=ORG_1,
        name="Ghost",
        short_name="Ghost",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="not_a_real_licence_class",
    )
    row = institution_types.get_type(db_session, bank)
    assert row.type_code == institution_types.FALLBACK_TYPE_CODE == "universal_bank"
    assert institution_types.institution_class(db_session, bank) == "bank"


def test_resolver_fails_loud_when_the_registry_is_unseeded(db_session: Session) -> None:
    # Mirror of base_currency: with neither the bank's type nor the fallback
    # present, resolving raises rather than silently defaulting a regime.
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
    with pytest.raises(ValueError, match="institution_types registry"):
        institution_types.get_type(db_session, bank)


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
