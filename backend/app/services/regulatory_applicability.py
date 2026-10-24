from datetime import date

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.identity.public import Bank


def rule_in_force(
    db: Session, ctx: TenantContext, bank: Bank, code: str, *, as_of: date | None = None
) -> bool:
    from app.reporting.public import REGISTRY, resolve_eligibility  # noqa: PLC0415

    when = as_of or date.today()
    return (
        resolve_eligibility(db, ctx, bank, as_of=when).instrument_status(REGISTRY[code], when)
        == "in_force"
    )
