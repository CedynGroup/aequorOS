"""The ``bi_mart_refresh`` handler: (re)build one bank's marts for one as-of.

Payload (``.ai/bi_contracts.md`` §Interfaces)::

    {"organization_id", "bank_id", "as_of_date", "builder_version", "reason"}

coalesced on ``bi:{bank}:{as_of}`` by every enqueue site, so a burst of
ingestions for one day debounces into one build. The handler itself is thin —
see ``bi_common`` — and the build is
``mart_builder.refresh_bank_as_of(db, *, organization_id, bank_id, as_of, reason)``,
which owns the fingerprint skip, the atomic slice replace, the aggregates,
dimensions, ``bi_dim_date`` and reconciliation, and returns a ``BuildOutcome``.

Run gate: ``BI_MART_ENQUEUE_ENABLED`` is re-checked HERE as well as at enqueue
(the ``desk_capture`` idiom). A job queued before the switch was pulled must
not build, and flipping the flag off is therefore also how a backlog drains.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.jobs import bi_common
from app.models import Job

#: Written to ``payload["reason"]`` by enqueue sites; the handler passes it
#: through to the builder, which records it on ``bi_mart_builds``.
DEFAULT_REASON = "unspecified"


def run_bi_mart_refresh(session: Session, job: Job) -> None:
    """Worker handler: build the marts for ``(bank, as_of)``; idempotent."""
    if not get_settings().bi.mart_enqueue_enabled:
        bi_common.mark_skipped(job, bi_common.SKIP_REASON_DISABLED)
        return

    builder = bi_common.load_builder()
    if bi_common.skip_if_payload_is_newer(job, builder.BUILDER_VERSION):
        return

    bank = bi_common.resolve_bank(session, job)
    as_of = bi_common.payload_date(job, "as_of_date")
    reason = str((job.payload or {}).get("reason") or DEFAULT_REASON)

    try:
        outcome = builder.refresh_bank_as_of(
            session,
            organization_id=job.organization_id,
            bank_id=bank.id,
            as_of=as_of,
            reason=reason,
        )
    except bi_common.BiJobError:
        raise
    except Exception as exc:
        raise bi_common.TransientBiBuildError("refresh", exc) from exc

    session.commit()
    job.progress = {
        "status": outcome.status,
        "as_of_date": as_of.isoformat(),
        "reason": reason,
        "builder_version": builder.BUILDER_VERSION,
        "fingerprint": outcome.fingerprint,
        "row_counts": dict(outcome.row_counts),
        "trust": dict(outcome.trust),
    }


__all__ = ["DEFAULT_REASON", "run_bi_mart_refresh"]
