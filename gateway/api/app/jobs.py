"""Background jobs: scheduled audit-chain verification, operator-session expiry,
API call statistics and (optionally) a sweep for filings stuck in validation.

Run in-process by the scheduler thread, or once from the command line:
``python -m app.jobs verify-audit|expire-access|validate-pending``.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.audit.chain import SYSTEM, VerifyResult, append, verify
from app.core.context import AppContext, app_context
from app.db.models import ApiDailyStat, AuditVerification


@dataclass
class _Counts:
    total: int = 0
    errors: int = 0


_stats_lock = threading.Lock()
_pending_stats: dict[date, _Counts] = {}


def record_api_call(ctx: AppContext, *, server_error: bool) -> None:
    day = ctx.clock.now().date()
    with _stats_lock:
        counts = _pending_stats.setdefault(day, _Counts())
        counts.total += 1
        if server_error:
            counts.errors += 1


def flush_api_stats(ctx: AppContext) -> None:
    with _stats_lock:
        pending = dict(_pending_stats)
        _pending_stats.clear()
    if not pending:
        return
    with ctx.db.session() as session:
        for day, counts in pending.items():
            if ctx.db.postgres:
                statement = pg_insert(ApiDailyStat).values(
                    day=day, total=counts.total, server_errors=counts.errors
                )
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["day"],
                        set_={
                            "total": ApiDailyStat.total + counts.total,
                            "server_errors": ApiDailyStat.server_errors + counts.errors,
                        },
                    )
                )
            else:
                row = session.get(ApiDailyStat, day)
                if row is None:
                    session.add(
                        ApiDailyStat(day=day, total=counts.total, server_errors=counts.errors)
                    )
                else:
                    row.total += counts.total
                    row.server_errors += counts.errors


def verify_audit(
    ctx: AppContext, *, trigger: str, actor_label: str | None = None
) -> tuple[VerifyResult, AuditVerification]:
    """Recompute the chain, store the result and record the verification in the chain."""
    with ctx.db.session() as session:
        result = verify(session, ctx.keys)
    now = ctx.clock.now()
    with ctx.db.session() as session:
        record = AuditVerification(
            verified_at=now,
            trigger=trigger,
            intact=result.intact,
            entries=result.entries,
            head_hash=result.head_hash,
            first_bad_seq=result.first_bad_seq,
        )
        session.add(record)
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=SYSTEM,
            category="system",
            action="Verified audit chain" if result.intact else "Audit chain verification failed",
            object_type="audit_chain",
            object_ref=f"{result.entries} entries",
            detail={
                "trigger": trigger,
                "intact": result.intact,
                "entries": result.entries,
                "head_hash": result.head_hash,
                "first_bad_seq": result.first_bad_seq,
                "requested_by": actor_label,
            },
        )
    return result, record


def latest_verification(ctx: AppContext) -> AuditVerification | None:
    with ctx.db.session() as session:
        return session.scalars(
            select(AuditVerification).order_by(AuditVerification.verified_at.desc()).limit(1)
        ).first()


def run_scheduler(ctx: AppContext, stop: threading.Event) -> None:
    """Scheduler loop: flush statistics each minute, verify the chain every 30 minutes."""
    from app.access.service import expire_requests
    from app.filing.validation import validate_pending

    interval = ctx.settings.audit_verify_interval_seconds
    sweep = ctx.settings.validation_sweep_seconds
    next_verify = time.monotonic() + 5.0
    next_sweep = time.monotonic() + max(sweep, 1)
    while not stop.wait(1.0):
        now = time.monotonic()
        try:
            flush_api_stats(ctx)
            expire_requests(ctx)
            if interval > 0 and now >= next_verify:
                verify_audit(ctx, trigger="scheduled")
                next_verify = now + interval
            if sweep > 0 and now >= next_sweep:
                validate_pending(ctx, older_than_seconds=sweep)
                next_sweep = now + sweep
        except Exception as exc:
            print(f"srg-scheduler: {exc!r}", file=sys.stderr)
    flush_api_stats(ctx)


def main(argv: list[str]) -> int:
    from app.access.service import expire_requests
    from app.filing.validation import validate_pending
    from app.main import create_app

    if len(argv) != 1 or argv[0] not in ("verify-audit", "expire-access", "validate-pending"):
        print(
            "usage: python -m app.jobs verify-audit|expire-access|validate-pending", file=sys.stderr
        )
        return 2
    app = create_app(run_scheduler=False)
    ctx = app_context(app)
    if argv[0] == "verify-audit":
        result, _ = verify_audit(ctx, trigger="manual", actor_label="command line")
        print(f"intact={result.intact} entries={result.entries} head={result.head_hash}")
        return 0 if result.intact else 1
    if argv[0] == "expire-access":
        print(f"expired or ended: {expire_requests(ctx)}")
        return 0
    print(f"validated: {validate_pending(ctx, older_than_seconds=0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
