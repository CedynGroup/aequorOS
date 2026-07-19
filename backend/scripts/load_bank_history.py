"""Bulk-load the 10-year simulator parquet panels into the canonical DB and
derive the module fact set for every month.

    DATABASE_URL=postgresql+psycopg://... \
      python backend/scripts/load_bank_history.py --panels-dir data/history/panels

The loader is NOT content-hash idempotent: it refuses to run if canonical
positions already exist for the bank. Pass --reset to clear the bank first
(runs reset_uploaded_data with --wipe-all-periods), or clear it yourself.

Flags:
  --panels-dir PATH   panels root (default data/history/panels)
  --months N          cap to the first N months (smoke tests)
  --reset             wipe the bank's uploaded data + all periods first
  --skip-derive       load canonical rows only, do not derive facts
  --only-derive       derive facts only (assumes canonical rows already loaded)
  --database-url URL  override DATABASE_URL
  --yes               required to proceed (guards against accidental runs)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ on path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # backend/scripts on path

from app.core.config import get_settings  # noqa: E402
from app.services.history_loader import (  # noqa: E402
    _months_from_panels,
    derive_all_periods,
    load_history,
)

DEMO_ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
SAMPLE_BANK_ID = UUID("77000000-0000-4000-8000-000000000001")


def main() -> None:  # noqa: PLR0915
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--panels-dir", default="data/history/panels")
    ap.add_argument("--months", type=int, default=None)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument(
        "--additive", action="store_true",
        help="load only months not already present; never reset/delete existing data",
    )
    ap.add_argument("--skip-derive", action="store_true")
    ap.add_argument("--only-derive", action="store_true")
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--org-id", default=str(DEMO_ORG_ID))
    ap.add_argument("--bank-id", default=str(SAMPLE_BANK_ID))
    ap.add_argument("--yes", action="store_true", help="confirm the run")
    args = ap.parse_args()

    # Fall back to the app's configured URL (env or backend/.env) — same source
    # the running service uses. No hard-coded local default.
    if not args.database_url:
        args.database_url = get_settings().database.database_url
    if not args.database_url:
        ap.error("DATABASE_URL (env, backend/.env, or --database-url) is required")
    if not args.yes:
        ap.error("Pass --yes to confirm. This writes millions of rows to the target DB.")

    org_id, bank_id = UUID(args.org_id), UUID(args.bank_id)
    panels_dir = Path(args.panels_dir)
    engine = create_engine(args.database_url)
    session = Session(engine)
    session.info["organization_id"] = org_id

    months = _months_from_panels(panels_dir)
    if args.months:
        months = months[: args.months]

    if args.additive:
        # Load only months not already present; leave existing data + periods untouched.
        present = {
            r[0] for r in session.execute(
                text("select distinct as_of_date from canonical_position_snapshots "
                     "where organization_id=:o and bank_id=:b"),
                {"o": org_id, "b": bank_id}).all()
        }
        skipped = [m for m in months if m in present]
        months = [m for m in months if m not in present]
        print(f"Additive mode: {len(present)} months already present, "
              f"skipping {len(skipped)}; loading {len(months)} new months.")
        if not months:
            sys.exit("Nothing to load — every panel month already exists for this bank.")

    print(f"Target DB: {args.database_url.split('@')[-1]}  bank={bank_id}")
    print(f"Panels: {panels_dir}  months: {months[0]}..{months[-1]} ({len(months)})")

    if args.reset:
        from reset_uploaded_data import reset  # type: ignore  # noqa: PLC0415
        print("Resetting bank (uploaded data + all periods)...")
        counts = reset(session, org_id, bank_id, wipe_all_periods=True)
        session.commit()
        print(f"  reset removed: {counts}")

    existing = session.execute(
        text("select count(*) from canonical_positions where organization_id=:o and bank_id=:b"),
        {"o": org_id, "b": bank_id}).scalar()
    if existing and not args.only_derive and not args.additive:
        sys.exit(f"Refusing to load: {existing} canonical positions already exist for this bank. "
                 f"Re-run with --reset, --additive, or clear the bank first.")

    t0 = time.time()
    if not args.only_derive:
        print("Loading canonical rows...")
        summary = load_history(session, org_id, bank_id, panels_dir, months=months)
        session.commit()
        print(f"Loaded in {time.time() - t0:.1f}s: counterparties={summary.counterparties} "
              f"products={summary.products} gl={summary.gl_accounts} positions={summary.positions} "
              f"snapshots={summary.snapshots:,} reference_rows={summary.reference_rows}")

    if not args.skip_derive:
        print("Deriving facts for every month...")
        t1 = time.time()
        result = derive_all_periods(session, org_id, bank_id, months)
        print(f"Derived in {time.time() - t1:.1f}s: {result}")

    session.close()
    print(f"Done in {time.time() - t0:.1f}s.")


if __name__ == "__main__":
    main()
