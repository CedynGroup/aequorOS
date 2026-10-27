"""Bank security and compliance audit evidence."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Tenant
from app.identity.schemas.audit_log import AuditLogRead
from app.identity.service import audit_log

router = APIRouter(prefix="/banks/{bank_id}/audit-log", tags=["audit-log"])


@router.get("", response_model=AuditLogRead, operation_id="readBankAuditLog")
def read_bank_audit_log(  # noqa: PLR0913 - one argument per filter
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    start: datetime | None = None,
    end: datetime | None = None,
    actor: Annotated[str | None, Query(max_length=320)] = None,
    event_type: Annotated[str | None, Query(max_length=120)] = None,
    outcome: Annotated[str | None, Query(max_length=40)] = None,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> AuditLogRead:
    """Newest first, with an inclusive start and exclusive end in UTC.

    Verification covers the event payload and its stored predecessor link;
    a bank feed cannot certify the full platform stream or its global head.
    """
    return audit_log.read_log(
        db,
        ctx,
        bank_id,
        audit_log.Filters(
            start=start, end=end, actor=actor, event_type=event_type, outcome=outcome
        ),
        cursor=cursor,
        limit=limit,
    )
