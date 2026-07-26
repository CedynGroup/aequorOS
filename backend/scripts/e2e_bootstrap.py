"""Bootstrap the hermetic e2e database (plan W7.5).

Creates the schema plus the tenant scaffolding the API's zero-trust layer
requires before any request can succeed: the demo organization, the GH
jurisdiction row, and one user per role. Everything else (bank, periods,
facts) flows through the API in the Playwright global setup — the same
paths the product uses.

It also enrols a **software signing key** per human role so the attestation
ceremony can be driven end to end in a browser. Self-signed and disposable:
the software backend refuses to initialise when APP_ENV is production, so this
path cannot exist in a real deployment. Without it the ceremony journey could
only be skipped, and a skipped journey proves nothing.

For the same reason every fixture user gets a **password hash**: signing requires
step-up re-authentication, and the sessions Playwright mints are tokens with no
password behind them, so ``verify_step_up``'s password path could never succeed
and the lifecycle journeys could only reach a channel by relaxing the signing
policy — which would delete coverage of the gate they exist to prove. These are
disposable accounts on a throwaway sqlite file that is deleted before every run;
the value is a literal in the repo precisely because it must never be a real
credential.

Usage: DATABASE_URL=sqlite+pysqlite:///<path> uv run python scripts/e2e_bootstrap.py
"""

from __future__ import annotations

import os
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.security import hash_password
from app.db.base import Base
from app.models import Jurisdiction, Organization, User
from app.services.attestation.identity import ensure_signer_identity
from app.services.attestation.keys import SignerKeyService

# The platform tenant ID pinned by the hermetic fixture (sample_bank_seed).
DEMO_ORG_ID = "OR-DEM00001"
#: Step-up re-authentication for every e2e signer. Mirrored in
#: dashboard/e2e/support/mint.ts (E2E_PASSWORD) — the two must agree, and both say
#: what they are.
E2E_PASSWORD = "e2e-step-up-password-not-production-000"  # noqa: S105 - disposable fixture
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
        # One hash for every user rather than one per user: Argon2id is
        # deliberately slow, and four of them is four seconds of every e2e run.
        password_hash = hash_password(E2E_PASSWORD)
        for role, user_id in E2E_USERS.items():
            if session.get(User, user_id) is None:
                session.add(
                    User(
                        id=user_id,
                        organization_id=DEMO_ORG_ID,
                        email=f"e2e.{role}@aequoros.example",
                        display_name=f"E2E {role.capitalize()}",
                        role=role,
                        auth_provider="password",
                        password_hash=password_hash,
                    )
                )
        session.commit()
        _enrol_signing_keys(session)
    print("e2e database bootstrapped")


def _enrol_signing_keys(session: Session) -> None:
    """Give every e2e human a signer identity and a self-signed software key."""
    ctx = TenantContext(organization_id=DEMO_ORG_ID)
    service = SignerKeyService(session, ctx)
    for role, user_id in E2E_USERS.items():
        identity = ensure_signer_identity(session, ctx, user_id)
        service.issue(
            signer_id=identity.signer_id,
            display_name=f"E2E {role.capitalize()}",
            organization_name="AequorOS E2E",
        )
        session.commit()
        print(f"enrolled signing key for {role}: {identity.signer_id}")


if __name__ == "__main__":
    main()
