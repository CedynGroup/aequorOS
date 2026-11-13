"""The credit engines refuse an unknown bank through the one shared lookup.

The credit package kept its own copy of the tenant-scoped bank lookup whose
refusal detail lacked the full stop every other engine shares. The copies are
gone; these pins hold the shared refusal text and the resolved-register
behaviour that must not move.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.credit.service import credit_params, regulatory_credit
from app.models import Bank
from tests.support.helpers import ORG_1, USER_1

UNKNOWN_BANK = "BK-UNKNOWN0"
AS_OF = date(2026, 6, 30)
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)


@pytest.mark.parametrize(
    ("lookup", "args"),
    [
        pytest.param(credit_params.get_ecl_register, (AS_OF,), id="ecl-register"),
        pytest.param(credit_params.get_crm_register, (AS_OF,), id="crm-register"),
        pytest.param(regulatory_credit.get_credit_dashboard, (), id="credit-dashboard"),
    ],
)
def test_unknown_bank_refusal_uses_the_shared_detail(
    db_session: Session, lookup: object, args: tuple[object, ...]
) -> None:
    db_session.info["organization_id"] = ORG_1
    with pytest.raises(HTTPException) as refused:
        lookup(db_session, CTX, UNKNOWN_BANK, *args)  # pyright: ignore[reportCallIssue]
    assert refused.value.status_code == 404
    assert refused.value.detail == "Bank not found."


def test_existing_bank_still_resolves_the_register(db_session: Session) -> None:
    db_session.info["organization_id"] = ORG_1
    db_session.add(
        Bank(
            id="BK-LOOKUP01",
            organization_id=ORG_1,
            name="Bank lookup detail fixture",
            short_name="Lookup",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type="universal_bank",
        )
    )
    db_session.commit()
    register = credit_params.get_ecl_register(db_session, CTX, "BK-LOOKUP01", AS_OF)
    assert register.bank_id == "BK-LOOKUP01"
    assert register.assumptions == []
    assert register.history == []
