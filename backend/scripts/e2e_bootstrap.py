"""Bootstrap the hermetic e2e database (plan W7.5).

Creates the schema plus the tenant scaffolding the API's zero-trust layer
requires before any request can succeed: the demo organization, the GH
jurisdiction row, and one user per role. Everything else (bank, periods,
facts) flows through the API in the Playwright global setup — the same
paths the product uses.

Usage: DATABASE_URL=sqlite+pysqlite:///<path> uv run python scripts/e2e_bootstrap.py
"""

from __future__ import annotations

import os
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Jurisdiction, Organization, User

# The platform tenant ID pinned by the hermetic fixture (sample_bank_seed).
DEMO_ORG_ID = "OR-DEM00001"
E2E_USERS = {
    # id suffix encodes the role for readable storage-state files.
    "admin": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    "approver": UUID("eeeeeeee-2222-4eee-8eee-eeeeeeeeeee2"),
    "analyst": UUID("eeeeeeee-3333-4eee-8eee-eeeeeeeeeee3"),
    "viewer": UUID("eeeeeeee-4444-4eee-8eee-eeeeeeeeeee4"),
}


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    if "sqlite" not in database_url:
        msg = "e2e bootstrap only ever runs against a disposable sqlite file"
        raise SystemExit(msg)
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        if session.get(Jurisdiction, "GH") is None:
            session.add(
                Jurisdiction(
                    code="GH",
                    country_name="Ghana",
                    currency_code="GHS",
                    currency_name="Ghana Cedi",
                    locale="en-GH",
                    central_bank_name="Bank of Ghana",
                    regulator_short="BoG",
                    submission_portal="ORASS",
                    timezone="Africa/Accra",
                )
            )
        if session.get(Organization, DEMO_ORG_ID) is None:
            session.add(Organization(id=DEMO_ORG_ID, name="E2E Tenant"))
        for role, user_id in E2E_USERS.items():
            if session.get(User, user_id) is None:
                session.add(
                    User(
                        id=user_id,
                        organization_id=DEMO_ORG_ID,
                        email=f"e2e.{role}@aequoros.example",
                        display_name=f"E2E {role.capitalize()}",
                        role=role,
                    )
                )
        session.commit()
    print("e2e database bootstrapped")


if __name__ == "__main__":
    main()
