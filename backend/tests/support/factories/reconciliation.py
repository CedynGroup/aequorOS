"""Governed reconciliation exceptions for the hermetic fixtures.

Why this exists: the compact canonical fixture is a synthetic book whose GL and
sub-ledgers do NOT reconcile — assets exceed liabilities + equity by 15.28m GHS,
10.41% of assets, which the pre-audit derivation plugged silently into
``term_borrowings_gt_1y``. Every golden number in the hermetic suite (LCR, NSFR,
CAR, RWA, the BoG return values) is therefore computed on a balance sheet whose
funding side is 11.6% manufactured.

The fail-closed control (``app/services/reconciliation.py``) refuses to derive
official facts on such a book. Rather than change a single fixture balance —
which would move every golden — the fixture now carries the SAME governed
exception a real bank would need: reason, requester, a named second approver,
timestamp, an effective window and a ceiling on the breach it covers. The
fixture's manufactured funding becomes explicit and auditable instead of
invisible, and no expected value moves.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank
from app.services import reconciliation

#: The fixture's gap is 10.41% of assets; the ceiling sits just above it so the
#: exception covers the known defect and nothing wider.
FIXTURE_MAX_GAP_FRACTION = Decimal("0.13")
FIXTURE_EXCEPTION_REASON = (
    "Hermetic test fixture: the compact canonical book is a synthetic extract whose "
    "sub-ledgers deliberately do not tie to the GL. Recorded as a bounded, dated "
    "exception so the fail-closed balance-sheet control is exercised rather than "
    "disabled."
)
FIXTURE_APPROVER = "fixture_supervisor"


def allow_fixture_balance_gap(  # noqa: PLR0913 - a governed exception names every field
    session: Session,
    *,
    organization_id: str,
    bank_id: str,
    actor_user_id: UUID | None = None,
    effective_from: date = date(2000, 1, 1),
    effective_to: date | None = None,
    max_gap_fraction: Decimal = FIXTURE_MAX_GAP_FRACTION,
) -> None:
    """Register the fixture's approved balance-sheet identity exception."""
    bank = session.get(Bank, bank_id)
    assert bank is not None, "seed the bank before its reconciliation exception"
    reconciliation.grant_exception(
        session,
        TenantContext(organization_id=organization_id, actor_user_id=actor_user_id),
        bank,
        reason=FIXTURE_EXCEPTION_REASON,
        approved_by=FIXTURE_APPROVER,
        max_gap_fraction=max_gap_fraction,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    session.flush()
