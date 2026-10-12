"""Bounded tenant-local cleanup of spent sign-in credentials.

Inspector sessions are retained audit evidence and are deliberately excluded.
Each hourly job commits one batch; retries and later ticks drain the backlog.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.identity.models.refresh_token import RefreshToken
from app.models.job import Job


def purge_refresh_tokens(session: Session, organization_id: str, *, now: datetime) -> int:
    """Delete at most one batch strictly older than the investigation window."""
    settings = get_settings().auth
    # Even an unusually long concurrent-refresh grace must survive cleanup.
    window = max(
        timedelta(days=settings.refresh_token_retention_days),
        timedelta(seconds=settings.refresh_rotation_grace_seconds),
    )
    cutoff = now - window
    eligible = or_(RefreshToken.expires_at < cutoff, RefreshToken.revoked_at < cutoff)
    identifiers = list(
        session.scalars(
            select(RefreshToken.id)
            .where(RefreshToken.organization_id == organization_id, eligible)
            .order_by(RefreshToken.expires_at, RefreshToken.id)
            .limit(settings.session_purge_batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    if not identifiers:
        return 0
    deleted = list(
        session.scalars(
            delete(RefreshToken)
            .where(
                RefreshToken.organization_id == organization_id,
                RefreshToken.id.in_(identifiers),
                eligible,
            )
            .returning(RefreshToken.id)
        )
    )
    return len(deleted)


def run_session_purge(session: Session, job: Job) -> None:
    """Worker handler; recheck the switch for jobs already queued."""
    if not get_settings().auth.session_purge_enabled:
        job.progress = {"status": "skipped", "reason": "session_purge_disabled"}
        return
    deleted = purge_refresh_tokens(session, job.organization_id, now=utc_now())
    session.commit()
    job.progress = {"status": "succeeded", "refresh_tokens_deleted": deleted}
