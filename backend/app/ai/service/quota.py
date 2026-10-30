"""Daily caps and the in-flight debounce.

One budget per tenant across every AI feature, counted since UTC midnight from
pluggable usage sources — ICAAP registers its suggestions table here, and BI
commentary will register its own, so a tenant's spend is one number rather than
one per surface.

Cancelled requests are excluded: a request the platform refused to send is not
something the tenant spent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings

QuotaCode = Literal["allowed", "org_requests", "user_requests", "org_tokens"]


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    code: QuotaCode
    retry_after_seconds: int


class UsageSource(Protocol):
    def requests_since(
        self, db: Session, organization_id: str, since: datetime, *, user_id: UUID | None
    ) -> int: ...

    def output_tokens_since(self, db: Session, organization_id: str, since: datetime) -> int: ...


#: Registered by each AI feature at import time. A list rather than a constant
#: so a feature can add itself without this module importing it (and creating a
#: cycle through the feature's models).
USAGE_SOURCES: list[UsageSource] = []


def register_source(source: UsageSource) -> None:
    if source not in USAGE_SOURCES:
        USAGE_SOURCES.append(source)


def _utc_midnight(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)


def _seconds_to_next_midnight(now: datetime) -> int:
    moment = now.astimezone(UTC)
    tomorrow = _utc_midnight(moment) + timedelta(days=1)
    return max(int((tomorrow - moment).total_seconds()), 1)


def check(  # noqa: PLR0913 - the quota key plus its injectable dependencies
    db: Session,
    organization_id: str,
    user_id: UUID,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    sources: list[UsageSource] | None = None,
) -> QuotaDecision:
    settings = settings or get_settings()
    moment = now or datetime.now(UTC)
    since = _utc_midnight(moment)
    retry_after = _seconds_to_next_midnight(moment)
    active = USAGE_SOURCES if sources is None else sources

    def total(fn: Callable[[UsageSource], int]) -> int:
        return sum(fn(source) for source in active)

    org_requests = total(lambda s: s.requests_since(db, organization_id, since, user_id=None))
    if org_requests >= settings.ai.daily_requests_per_org:
        return QuotaDecision(allowed=False, code="org_requests", retry_after_seconds=retry_after)

    user_requests = total(lambda s: s.requests_since(db, organization_id, since, user_id=user_id))
    if user_requests >= settings.ai.daily_requests_per_user:
        return QuotaDecision(allowed=False, code="user_requests", retry_after_seconds=retry_after)

    tokens = total(lambda s: s.output_tokens_since(db, organization_id, since))
    if tokens >= settings.ai.daily_output_tokens_per_org:
        return QuotaDecision(allowed=False, code="org_tokens", retry_after_seconds=retry_after)

    return QuotaDecision(allowed=True, code="allowed", retry_after_seconds=0)


__all__ = [
    "USAGE_SOURCES",
    "QuotaCode",
    "QuotaDecision",
    "UsageSource",
    "check",
    "register_source",
]
