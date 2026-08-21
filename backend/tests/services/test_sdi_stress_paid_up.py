"""Enterprise-stress paid-up-capital resolution (QA audit 2026-08-20 P0-2).

``_resolve_paid_up_min`` must not silently drop the SDI s.29 paid-up floor. An
SDI run with no explicit override and no board register row resolves the GH¢15m
minimum from the regulatory-parameter control plane (millions → absolute GHS);
a bank keeps the historical payload → register → 0 path byte-identical.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank
from app.schemas.enterprise_stress import EnterpriseStressRunCreate
from app.services import enterprise_stress as svc
from tests.api.helpers import ORG_1, USER_1

AS_OF = date(2026, 6, 30)
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _payload(paid_up_min: Decimal | None = None) -> EnterpriseStressRunCreate:
    return EnterpriseStressRunCreate(
        scenario_id=uuid4(),
        reporting_period_id=uuid4(),
        reason="test",
        paid_up_min=paid_up_min,
    )


def _make_bank(db: Session, *, institution_type: str) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Paid-Up Stress Bank",
        short_name="PUSB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def test_sdi_paid_up_min_falls_back_to_the_control_plane(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    # No payload override, no board register row → the GH¢15m s.29 floor from the
    # control plane (15 millions → 15_000_000 absolute GHS), NOT a silent 0.
    resolved = svc._resolve_paid_up_min(db_session, CTX, bank, AS_OF, _payload())
    assert resolved == Decimal("15000000")


def test_bank_paid_up_min_stays_zero_without_override_or_register(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="universal_bank")
    # Byte-identical bank behaviour: no override/register → 0 (the enterprise-stress
    # goldens are written against this; a bank's paid-up floor is a BSD-return matter).
    resolved = svc._resolve_paid_up_min(db_session, CTX, bank, AS_OF, _payload())
    assert resolved == Decimal("0")


def test_explicit_override_wins_for_an_sdi(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    resolved = svc._resolve_paid_up_min(
        db_session, CTX, bank, AS_OF, _payload(paid_up_min=Decimal("20000000"))
    )
    assert resolved == Decimal("20000000")
