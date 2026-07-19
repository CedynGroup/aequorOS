"""Seed Sample Bank Ltd regulatory demo data.

Usage:
    uv run python scripts/seed_sample_bank.py

Connects with the app's configured ``DATABASE_URL`` (resolved from the
environment or ``backend/.env`` exactly like the running service — never a
hard-coded local database) and idempotently re-seeds the Sample Bank Ltd bank,
its twelve monthly reporting periods with tie-out validated financial facts,
and the Bank of Ghana CRD baseline parameter tables.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.sample_bank_seed import (  # noqa: E402
    DEMO_ORG_ID,
    SeedSummary,
    seed_sample_bank,
)


def run(database_url: str) -> SeedSummary:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            session.info["organization_id"] = DEMO_ORG_ID
            summary = seed_sample_bank(session)
            session.commit()
    finally:
        engine.dispose()
    return summary


def main() -> int:
    # Single source of truth: the same DATABASE_URL the service uses (env or
    # backend/.env). No local fallback — fail loudly rather than seed the wrong DB.
    database_url = get_settings().database.database_url
    if not database_url:
        print(
            "DATABASE_URL is not configured (set it in the environment or backend/.env).",
            file=sys.stderr,
        )
        return 1
    summary = run(database_url)
    print("Sample Bank Ltd seed complete.")
    print(f"  bank_id:     {summary.bank_id}")
    print(f"  periods:     {summary.periods}")
    print(f"  facts:       {summary.fact_count}")
    print(f"  parameters:  {summary.param_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
