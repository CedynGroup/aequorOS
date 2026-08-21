"""SDI Phase B — the reporting calendar is class-filtered (docs/sdi.md §6.2).

A savings-&-loans tenant resolves to the ``sdi`` regime and sees NONE of the
bank/BoG returns in its reporting calendar; a universal bank sees the full set.
Every registered return is a bank return today (the SDI/ORASS return pack is
Phase F, blocked on BoG), so the SDI calendar is honestly empty — the filter is
the scoping mechanism, not a claim that an SDI has no obligations of its own.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services.regulatory_reporting import calendar, generation
from app.services.regulatory_reporting.registry import REGISTRY
from tests.api.helpers import ORG_1, USER_1

_AS_OF = date(2026, 6, 30)


def _make_bank(db: Session, *, institution_type: str) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name=f"{institution_type} tenant",
        short_name="Scope",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def test_every_registered_return_defaults_to_the_bank_class() -> None:
    """The applicability field is the SDI filter's mechanism. Every return
    registered so far is a bank/BoG return — the SDI/ORASS pack (Phase F) sets
    ``('sdi',)``/``('bank', 'sdi')`` when its layouts land."""
    assert all("bank" in d.institution_classes for d in REGISTRY.values())
    assert not any("sdi" in d.institution_classes for d in REGISTRY.values())


def test_universal_bank_sees_the_full_reporting_calendar(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="universal_bank")
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    result = calendar.list_obligations(db_session, ctx, bank.id, as_of=_AS_OF)
    # Bank/BoG returns expand into calendar obligations for a 'bank' tenant.
    assert result.obligations


def test_savings_and_loans_calendar_is_class_filtered_to_empty(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    result = calendar.list_obligations(db_session, ctx, bank.id, as_of=_AS_OF)
    # No bank return leaks to an SDI tenant — the calendar is honestly empty
    # until the SDI/ORASS return pack lands (docs/sdi.md §Phase F).
    assert result.obligations == []


def test_finance_house_is_also_scoped_out_of_bank_returns(db_session: Session) -> None:
    # Every deposit-taking non-bank licence resolves to 'sdi' and is scoped out
    # of the bank return set the same way.
    bank = _make_bank(db_session, institution_type="finance_house")
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    result = calendar.list_obligations(db_session, ctx, bank.id, as_of=_AS_OF)
    assert result.obligations == []


def test_sdi_cannot_generate_a_bank_only_return(db_session: Session) -> None:
    """Server-side return-set scoping (docs/sdi.md §4.4/§14): the single package-
    mint site rejects a bank-only return code for an SDI tenant — the calendar
    filter alone is not security, since an SDI could POST the code directly."""
    bank_only_code = next(
        code for code, d in REGISTRY.items() if "sdi" not in d.institution_classes
    )
    sdi = _make_bank(db_session, institution_type="savings_and_loans")
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    payload = RegulatoryPackageCreate(return_code=bank_only_code, reporting_date=_AS_OF)
    with pytest.raises(HTTPException) as exc:
        generation.generate_package(db_session, ctx, sdi.id, payload)
    assert exc.value.status_code == 403
    assert "class" in str(exc.value.detail).lower()
