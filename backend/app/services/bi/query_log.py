"""The append-only record of every BI read, and the read budget measured over it.

Two things live here because they are the same table read two ways.

**The record (S26, D-026).** One row per BI read decision, written by the route
BEFORE it answers: who asked, on which institution, through which surface, a
one-way hash of the query, the catalogue member ids the query touched, the
allow/deny decision and the ids it denied, how many rows were served and how
long it took, plus the catalogue version and the mart build fingerprint the
answer came from. A DENIAL is recorded too — a probe for an obligor a principal
may not see is precisely what a reviewer needs to find — and so is a read that
served no rows (a 304, a refusal after authorization, a cancelled statement),
which is what ``row_count IS NULL`` means. ``decision`` is the AUTHORIZATION
decision, never a description of the HTTP status.

**What is never written.** No filter VALUE, ever: not the obligor name that was
filtered on, not a branch code, not a date. Only member IDS and a SHA-256 of the
request body, which is also the ETag's input, so a reviewer can tell two
identical questions apart from two different ones without the log becoming a
second copy of the data. ``bi_query_log`` is append-only three ways on Postgres
(trigger, revoked privileges, RESTRICTIVE policies — migration ``202609220066``),
so nothing here ever updates a row: everything a row says is known at the moment
it is written, which is why it is written after the statement has run.

**The budget (D-010).** ``app/services/auth_throttle.py`` states the rule this
follows: the API runs several uvicorn workers behind a load balancer, so an
in-process token bucket hands out ``budget × workers × replicas`` and forgets
everything on deploy. Its own store is the durable one that already exists for
the question it answers (two ``users`` columns). BI's is this table: every served
and every denied query is already a row here, the index
``ix_bi_query_log_org_principal_queried_at`` is exactly the one a
principal-and-window count needs, and no new state has to be invented. The
counter lags by whatever is in flight — a row is written after its statement
runs — so a simultaneous burst can overshoot by the number of concurrent
requests and the next request sees them. That is a rate limit, not a semaphore,
and the shared-database reason (a scripted extraction, a runaway dashboard) is
bounded either way.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.authorization import PrincipalType
from app.db.base import utc_now
from app.models.bi import QUERY_LOG_DECISIONS, QUERY_LOG_SURFACES, BiQueryLog
from app.schemas.bi import BiQuery
from app.services.bi import partitions

#: The window the budget is measured over, and how many BI reads one principal
#: may put inside it. A dashboard pack opens about a dozen queries at once and a
#: user may walk several packs in a minute, so the budget is generous; what it
#: bounds is a script, not a person.
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_QUERIES = 120

DECISION_ALLOWED = "allowed"
DECISION_DENIED = "denied"


def query_digest(query: BiQuery) -> str:
    """A one-way digest of the request body, stable across equal queries.

    Pydantic's serialisation is field-ordered by the model, so two equal
    ``BiQuery`` values produce one digest and the ETag and the log row agree on
    what "the same question" means. It is a hash, never a value: nothing here
    can be read back into a filter.
    """

    return hashlib.sha256(query.model_dump_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class QueryRecord:
    """One row of :class:`~app.models.bi.BiQueryLog`, as the route knows it.

    A dataclass rather than a dozen keyword arguments so that adding a column
    is one edit and no call site can pass the arguments in the wrong order.
    """

    organization_id: str
    bank_id: str
    principal_user_id: UUID
    surface: str
    query_hash: str
    decision: str
    catalogue_version: str
    member_ids: tuple[str, ...] = ()
    denied_members: tuple[str, ...] = ()
    #: ``None`` means no rows were served: denied, refused after authorization,
    #: cancelled, or answered from the caller's cache.
    row_count: int | None = None
    duration_ms: int | None = None
    build_fingerprint: str | None = None
    #: Only a tenant human reaches a BI read route (D-026); the column exists so
    #: the Phase 4 machine feed writes the same log rather than a second one.
    principal_type: str = field(default=PrincipalType.HUMAN.value)


def record(db: Session, entry: QueryRecord) -> BiQueryLog:
    """Append one decision. The caller commits.

    Raises ``ValueError`` for a surface or decision outside the table's own
    vocabulary — the database CHECK would refuse it anyway, and failing in
    Python names the offending value instead of a constraint.
    """

    if entry.surface not in QUERY_LOG_SURFACES:
        raise ValueError(f"unknown BI query-log surface: {entry.surface}")
    if entry.decision not in QUERY_LOG_DECISIONS:
        raise ValueError(f"unknown BI query-log decision: {entry.decision}")
    queried_at = utc_now()
    # The month child must exist before the row for that month is written: once
    # rows land in the DEFAULT partition, that month can never get a child
    # (``partitions``, migration ``202609220066``). Idempotent, and a no-op
    # wherever the parent is a plain table (SQLite, ``create_all``).
    partitions.ensure_month_partition(db, BiQueryLog.__tablename__, queried_at.date())
    row = BiQueryLog(
        id=uuid4(),
        queried_at=queried_at,
        organization_id=entry.organization_id,
        bank_id=entry.bank_id,
        principal_user_id=entry.principal_user_id,
        principal_type=entry.principal_type,
        surface=entry.surface,
        query_hash=entry.query_hash,
        member_ids=list(entry.member_ids),
        decision=entry.decision,
        denied_members=list(entry.denied_members),
        row_count=entry.row_count,
        duration_ms=entry.duration_ms,
        catalogue_version=entry.catalogue_version,
        build_fingerprint=entry.build_fingerprint,
    )
    db.add(row)
    db.flush()
    return row


def _as_aware(value: dt.datetime) -> dt.datetime:
    """SQLite hands back naive datetimes; arithmetic must not raise on them."""

    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


@dataclass(frozen=True, slots=True)
class RateBudget:
    """How much of one principal's BI read budget the window still holds."""

    used: int
    limit: int
    window_seconds: int
    #: Whole seconds until the oldest counted read leaves the window, at least 1.
    retry_after_seconds: int

    @property
    def exceeded(self) -> bool:
        return self.used >= self.limit


def budget_for(
    db: Session,
    *,
    organization_id: str,
    principal_user_id: UUID,
    now: dt.datetime | None = None,
) -> RateBudget:
    """The principal's recorded BI reads inside the window, across every worker."""

    moment = now or utc_now()
    window_start = moment - dt.timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
    used, oldest = db.execute(
        select(func.count(), func.min(BiQueryLog.queried_at)).where(
            BiQueryLog.organization_id == organization_id,
            BiQueryLog.principal_user_id == principal_user_id,
            BiQueryLog.queried_at >= window_start,
        )
    ).one()
    if oldest is None:
        retry_after = RATE_LIMIT_WINDOW_SECONDS
    else:
        leaves_at = _as_aware(oldest) + dt.timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
        retry_after = max(1, int(-(-(leaves_at - moment).total_seconds() // 1)))
    return RateBudget(
        used=int(used or 0),
        limit=RATE_LIMIT_MAX_QUERIES,
        window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        retry_after_seconds=min(retry_after, RATE_LIMIT_WINDOW_SECONDS),
    )


__all__ = [
    "DECISION_ALLOWED",
    "DECISION_DENIED",
    "RATE_LIMIT_MAX_QUERIES",
    "RATE_LIMIT_WINDOW_SECONDS",
    "QueryRecord",
    "RateBudget",
    "budget_for",
    "query_digest",
    "record",
]
