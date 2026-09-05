"""Bootstrap the hermetic e2e database (plan W7.5).

Creates the schema, the GLOBAL reference registries a deployment gets from
its migrations (jurisdictions, institution types, the regulatory-parameter
control plane — ``tests/fixtures/reference_data.py``, shared with the
hermetic pytest suite), and the tenant scaffolding the API's zero-trust layer
requires before any request can succeed: the demo organization and one user
per role. Everything else (bank, periods, facts) flows through the API in the
Playwright global setup — the same paths the product uses.

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
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.security import hash_password
from app.db.base import Base
from app.models import Organization, User
from app.services import authorization
from app.services.attestation.identity import ensure_signer_identity
from app.services.attestation.keys import SignerKeyService
from app.services.organization_ownership import assign_initial_owner
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.fixtures.live_plane import materialize_live_plane
from tests.fixtures.reference_data import seed_global_reference_data

# The platform tenant ID used by the hermetic E2E fixture.
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
    "grant_member": UUID("eeeeeeee-5555-4eee-8eee-eeeeeeeeeee5"),
}


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    if "sqlite" not in database_url:
        msg = "e2e bootstrap only ever runs against a disposable sqlite file"
        raise SystemExit(msg)
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        # Every GLOBAL registry a migration would have seeded. create_all builds
        # the schema only, so without this the stack boots and then fails on the
        # first request that resolves a regulatory regime — the institution-type
        # resolver is fail-closed by design (P0-12) and a 409 naming the seed
        # migration is the correct answer to an empty registry, not a bug to
        # relax. Shared with the hermetic pytest suite so the two cannot drift.
        seed_global_reference_data(session)
        if session.get(Organization, DEMO_ORG_ID) is None:
            session.add(Organization(id=DEMO_ORG_ID, name="E2E Tenant"))
        # One hash for every user rather than one per user: Argon2id is
        # deliberately slow, and four of them is four seconds of every e2e run.
        password_hash = hash_password(E2E_PASSWORD)
        users: dict[str, User] = {}
        for role, user_id in E2E_USERS.items():
            user = session.get(User, user_id)
            if user is None:
                display_role = "Grant Member" if role == "grant_member" else role.capitalize()
                user = User(
                    id=user_id,
                    organization_id=DEMO_ORG_ID,
                    email=f"e2e.{role}@aequoros.example",
                    display_name=f"E2E {display_role}",
                    role="viewer" if role == "grant_member" else role,
                    auth_provider="password",
                    password_hash=password_hash,
                )
                session.add(user)
            users[role] = user
        session.flush()
        # create_all does not run the initial-owner migration. Mirror the landed
        # #127 bootstrap so the Members journey exercises real owner authority.
        assign_initial_owner(
            session,
            organization_id=DEMO_ORG_ID,
            candidate=users["admin"],
            granted_by_id="e2e-bootstrap",
            commit=False,
        )
        session.commit()
        _enrol_signing_keys(session)
        materialize_canonical_test_book(session)
        authorization.create_role_binding(
            session,
            organization_id=DEMO_ORG_ID,
            principal_user_id=users["admin"].id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.LIQUIDITY,
                SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(
                GrantorType.SYSTEM,
                "e2e-bootstrap",
            ),
            reason="exercise the binding-enforced Liquidity Monitoring journey",
        )
        session.commit()
        _materialize_live_plane(session)
    print("e2e database bootstrapped")


def _materialize_live_plane(session: Session) -> None:
    """Stand in for the worker's pipeline refresh.

    Every Treasury/ALM cockpit reads the live fact plane, which only the
    background worker writes — and the e2e stack runs no worker. Without this
    the whole live half of the dashboard opens on "no computed data yet".
    """
    facts, modules_ok, modules_failed = materialize_live_plane(
        session, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID
    )
    session.commit()
    print(f"live plane: {facts} current facts, modules ok: {', '.join(modules_ok) or 'none'}")
    for module, error in sorted(modules_failed.items()):
        print(f"live plane: module {module} failed: {error}")


def _enrol_signing_keys(session: Session) -> None:
    """Give every e2e human a signer identity and a self-signed software key."""
    ctx = TenantContext(organization_id=DEMO_ORG_ID)
    service = SignerKeyService(session, ctx)
    for role, user_id in E2E_USERS.items():
        identity = ensure_signer_identity(session, ctx, user_id)
        display_role = "Grant Member" if role == "grant_member" else role.capitalize()
        service.issue(
            signer_id=identity.signer_id,
            display_name=f"E2E {display_role}",
            organization_name="AequorOS E2E",
        )
        session.commit()
        print(f"enrolled signing key for {role}: {identity.signer_id}")


if __name__ == "__main__":
    main()
