"""``python -m app.seed``: build the design's synthetic world, or a clean start.

Runs only when ``GATEWAY_ENV`` is ``local``, ``test`` or ``sandbox``; refuses
``production``. The database must be migrated and empty (``--reset`` recreates the
schema first, ``local`` and ``test`` only).

``--definitions-only`` publishes the AequorOS-aligned return definitions and nothing
else: no institutions, filings or queries, so the portal shows only institutions that
are really enrolled and the filings they really send.
"""

from __future__ import annotations

import argparse
import heapq
import sys
import time
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.audit.chain import verify
from app.core.clock import SettableClock, parse_iso
from app.core.context import app_context
from app.core.sequences import ensure_at_least
from app.core.settings import Settings, get_settings
from app.db.migrate import reset as reset_schema
from app.db.models import AuditEntry, Institution, Query, Submission
from app.main import create_app
from app.returns.catalogue_loader import publish_catalogue_baseline
from app.seed.plan import plan
from app.seed.world import TARGET_LAST_RECEIPT, TARGET_RIVERBEND_QUERY, TODAY, World


class Args(argparse.Namespace):
    reset: bool = False
    live_now: str | None = None
    definitions_only: bool = False


def main(argv: list[str], settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seed", description=__doc__)
    parser.add_argument(
        "--reset", action="store_true", help="drop and recreate the schema first (local/test)"
    )
    parser.add_argument(
        "--definitions-only",
        action="store_true",
        help="publish the return definitions only: no demo institutions, filings or queries",
    )
    parser.add_argument(
        "--live-now", default=None, help="anchor for time-boxed items (default: wall clock)"
    )
    args = parser.parse_args(argv, namespace=Args())
    settings = settings or get_settings()
    if not settings.allows_seed:
        print(
            f"refusing to seed: GATEWAY_ENV={settings.env} (seeding runs only in local, test or sandbox)",
            file=sys.stderr,
        )
        return 2
    if args.reset:
        if not settings.is_dev:
            print("refusing --reset outside local and test", file=sys.stderr)
            return 2
        reset_schema(settings.database_url)
    clock = SettableClock(datetime(2025, 10, 1, 8, tzinfo=UTC))
    app = create_app(settings, clock=clock, run_scheduler=False)
    ctx = app_context(app)
    try:
        with ctx.db.session() as session:
            session.execute(text("SELECT version_num FROM alembic_version"))
            existing = session.scalar(select(func.count()).select_from(Institution)) or 0
    except SQLAlchemyError as exc:
        print(
            f"the database is not migrated (run `alembic upgrade head`): {exc.__class__.__name__}",
            file=sys.stderr,
        )
        return 2
    if existing:
        print(
            f"the database already holds {existing} institutions; use --reset (local/test) to reseed",
            file=sys.stderr,
        )
        return 2
    if args.definitions_only:
        clock.set(datetime.now(UTC))
        with ctx.db.session() as session:
            # The AequorOS catalogue publishes the baseline itself: a real gateway names
            # no person as having approved definitions nobody at the Bank approved.
            published = publish_catalogue_baseline(session, ctx, at=clock.now())
        print(
            f"published {len(published)} return definitions as the AequorOS catalogue "
            "baseline; no institutions, filings or queries (enrol institutions through the "
            "operator API)"
        )
        return 0
    live_now = parse_iso(args.live_now) if args.live_now else datetime.now(UTC)
    world = World(ctx, clock, live_now=live_now)
    plan(world)
    with ctx.db.session() as session:
        ensure_at_least(session, "receipt:2025", 97_000)
        ensure_at_least(session, "receipt:2026", TARGET_LAST_RECEIPT - world.filings_2026)
        ensure_at_least(session, "query:2025", 240)
        ensure_at_least(
            session, "query:2026", TARGET_RIVERBEND_QUERY - 1 - world.queries_2026_before_riverbend
        )
        ensure_at_least(session, "operator:2026", 38)
    started = time.monotonic()
    total = len(world.events)
    session = ctx.db.sessions()
    done = 0
    event = None
    try:
        while world.events:
            event = heapq.heappop(world.events)
            clock.set(event.when)
            if event.label == "verify":
                session.commit()
            event.run(session)
            done += 1
            if done % 300 == 0:
                session.commit()
                session.close()
                session = ctx.db.sessions()
                print(f"  {done} events · {event.when:%Y-%m-%d}", file=sys.stderr)
        session.commit()
    except Exception:
        session.rollback()
        where = f"{event.label} at {event.when.isoformat()}" if event is not None else "start"
        print(f"seed failed at event {done} ({where})", file=sys.stderr)
        raise
    finally:
        session.close()
    with ctx.db.session() as session:
        institutions = session.scalar(select(func.count()).select_from(Institution))
        filings = session.scalar(select(func.count()).select_from(Submission))
        queries = session.scalar(select(func.count()).select_from(Query))
        entries = session.scalar(select(func.count()).select_from(AuditEntry))
        meridian = session.scalars(
            select(Submission).order_by(Submission.received_at.desc()).limit(1)
        ).one()
        chain = verify(session, ctx.keys)
    print(
        f"seeded in {time.monotonic() - started:.1f}s: {total} events, {institutions} institutions, "
        f"{filings} filings, {queries} queries, {entries} audit entries"
    )
    print(
        f"latest filing {meridian.receipt_id} · chain intact={chain.intact} head={chain.head_hash[:16]}…"
    )
    print(f"design today: {TODAY.isoformat()}")
    return 0 if chain.intact else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
