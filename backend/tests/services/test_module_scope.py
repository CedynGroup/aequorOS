"""Regime scoping: which calculation modules an institution actually runs.

Founder review 2026-08-23. The live tier had this right and the official filing
tier did not — it ran a hardcoded tuple of all seven bank modules for every
licence class. For an SDI that meant attempting the Basel LCR/NSFR engine, the
bank-only FX and FTP modules, and the Basel-ratio five-year projection; all four
failed on refusals they were always going to give, the run produced zero
succeeded modules, and nothing could be published or filed.

These tests pin the corrected boundary and, just as importantly, that it is a
no-op for a universal bank.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank
from app.services import data_activation, module_scope
from app.services.institution_types import InstitutionTypeUnresolved
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
EVERY_MODULE = (
    "liquidity",
    "capital",
    "credit",
    "irr",
    "fx",
    "ftp",
    "forecast",
    "implied_rating",
)


def _bank(db: Session) -> Bank:
    bank = db.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    return bank


def _as_class(db: Session, institution_type: str) -> Bank:
    bank = _bank(db)
    bank.institution_type = institution_type
    db.flush()
    return bank


def test_a_universal_bank_runs_every_module(db_session: Session) -> None:
    """The scoping must be byte-identical for banks — it is a no-op for them."""
    materialize_canonical_test_book(db_session)
    bank = _as_class(db_session, "universal_bank")
    for module in EVERY_MODULE:
        assert module_scope.runs_module(db_session, bank, module), module


def test_an_sdi_does_not_run_the_basel_liquidity_engine(db_session: Session) -> None:
    """BoG supervises an SDI's liquidity through the LMTD monitoring tools.

    Seeding ``lcr_min``/``nsfr_min`` to make this module pass would assert a
    Basel floor no instrument imposes — the fail-open this codebase refuses.
    """
    materialize_canonical_test_book(db_session)
    bank = _as_class(db_session, "savings_and_loans")
    assert module_scope.runs_module(db_session, bank, "liquidity") is False
    note = module_scope.out_of_regime_note(db_session, bank, "liquidity")
    assert "liquidity monitoring tools" in note
    assert "not the Basel LCR and NSFR" in note


def test_an_sdi_does_not_run_bank_only_modules(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    bank = _as_class(db_session, "savings_and_loans")
    for module in ("fx", "ftp"):
        assert module_scope.runs_module(db_session, bank, module) is False, module
        assert "licence class's module set" in module_scope.out_of_regime_note(
            db_session, bank, module
        )


def test_an_sdi_does_not_run_the_basel_ratio_projection(db_session: Session) -> None:
    """No projection method is registered for the s.29 regime."""
    materialize_canonical_test_book(db_session)
    bank = _as_class(db_session, "savings_and_loans")
    assert module_scope.runs_module(db_session, bank, "forecast") is False


def test_an_sdi_still_runs_capital_and_irrbb(db_session: Session) -> None:
    """Scoping must not over-reach — an SDI IS supervised on capital, and keeps
    IRRBB (``institution_types.SDI_MODULES``)."""
    materialize_canonical_test_book(db_session)
    bank = _as_class(db_session, "savings_and_loans")
    for module in ("capital", "irr", "implied_rating"):
        assert module_scope.runs_module(db_session, bank, module) is True, module


def test_scoping_is_fail_closed_on_an_unresolved_licence_class(db_session: Session) -> None:
    """An unknown institution type must never resolve to the bank module set.

    Built in memory rather than persisted: ``banks.institution_type`` carries a
    foreign key to the registry, so the DB already refuses an unknown code. This
    covers the service-layer guard for the paths that reach it first (an empty
    registry, a blank type on an in-flight object).
    """
    materialize_canonical_test_book(db_session)
    unresolved = Bank(
        id="BK-NOTAREAL",
        organization_id=DEMO_ORG_ID,
        name="Unresolved",
        short_name="Unresolved",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="not_a_licence_class",
    )
    with pytest.raises(InstitutionTypeUnresolved):
        module_scope.runs_module(db_session, unresolved, "capital")

    unresolved.institution_type = ""
    with pytest.raises(InstitutionTypeUnresolved):
        module_scope.runs_module(db_session, unresolved, "capital")


def test_official_run_scope_matches_the_module_authority(db_session: Session) -> None:
    """The official filing tier reports the same in/out split as the authority."""
    materialize_canonical_test_book(db_session)
    _as_class(db_session, "savings_and_loans")
    in_scope, out_of_scope = data_activation.official_module_scope(
        db_session, MAKER, SAMPLE_BANK_ID
    )
    assert in_scope == ["capital", "credit", "irr", "implied_rating"]
    assert set(out_of_scope) == {"liquidity", "fx", "ftp", "forecast"}

    _as_class(db_session, "universal_bank")
    bank_in, bank_out = data_activation.official_module_scope(db_session, MAKER, SAMPLE_BANK_ID)
    assert bank_out == [], "a universal bank runs the full official set"
    assert set(bank_in) == set(EVERY_MODULE)


def test_both_tiers_read_one_authority(db_session: Session) -> None:
    """The live tier and the official tier cannot disagree.

    ``pipeline._scoped_modules`` and ``data_activation`` both filter through
    ``module_scope``; this pins that a module the official tier runs is one the
    live tier would also compute (for the modules both tiers implement).
    """
    from app.services import pipeline  # noqa: PLC0415 - avoids an import cycle at collection

    materialize_canonical_test_book(db_session)
    bank = _as_class(db_session, "savings_and_loans")
    live = {module for module, _ in pipeline._scoped_modules(db_session, bank)}
    official, _ = data_activation.official_module_scope(db_session, MAKER, SAMPLE_BANK_ID)
    shared = {"liquidity", "capital", "credit", "irr", "fx", "ftp", "forecast"}
    assert live & shared == set(official) & shared
