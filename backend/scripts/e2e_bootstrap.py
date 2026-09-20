"""Bootstrap the hermetic e2e database (plan W7.5).

Creates the schema, the GLOBAL reference registries a deployment gets from
its migrations (jurisdictions, institution types, the regulatory-parameter
control plane — ``tests/fixtures/reference_data.py``, shared with the
hermetic pytest suite), the tenant scaffolding the API's zero-trust layer
requires before any request can succeed (the demo organization and one user
per role), and the canonical Sample Bank book (``tests/fixtures/
canonical_bank_fixture.py``), carried forward to the reporting anchor
currently due. Everything downstream — the liquidity baseline run, the
institution profile, every package — flows through the API in the Playwright
global setup and the journeys themselves: the same paths the product uses.

The book is carried forward because the Returns workspace opens on the
regulator's anchor (the last month end on or before today for a monthly return —
``services/regulatory_reporting/anchors.py``), and a return can only be
generated from an EXACT snapshot as of that date. The canonical book ends at
a fixed month; without the carry-forward every anchor after it reads "no
position has been computed", correctly, and the generate journeys have
nothing to drive. ``extend_canonical_test_book`` appends one snapshot per
month end through the last month end on or before today, each repeating the canonical
latest fact set unchanged.

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

import calendar
import hashlib
import os
from datetime import date, timedelta
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
from app.models import IntegrationKey, Organization, RegulatoryParameter, User
from app.services import authorization, membership
from app.services.attestation.identity import ensure_signer_identity
from app.services.attestation.keys import SignerKeyService
from app.services.organization_ownership import assign_initial_owner
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    extend_canonical_test_book,
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
    "account_admin": UUID("eeeeeeee-6666-4eee-8eee-eeeeeeeeeee6"),
    "legacy_account_admin": UUID("eeeeeeee-7777-4eee-8eee-eeeeeeeeeee7"),
    "integration_admin": UUID("eeeeeeee-8888-4eee-8eee-eeeeeeeeeee8"),
    "liquidity_viewer": UUID("eeeeeeee-9999-4eee-8eee-eeeeeeeeeee9"),
    "liquidity_aggregated_viewer": UUID("eeeeeeee-aaaa-4eee-8eee-eeeeeeeeeeea"),
    "macro_viewer": UUID("eeeeeeee-cccc-4eee-8eee-eeeeeeeeeeec"),
    "invite_fresh": UUID("eeeeeeee-bbbb-4eee-8eee-eeeeeeeeeeeb"),
    # A board member: Capital/confidential APPROVER on the sample bank and
    # nothing else. Deliberately holds NO Regulatory Reporting access, because
    # that is the real shape of the person — the ICAAP filing surface has to
    # give them a signature they could not otherwise give.
    "board": UUID("eeeeeeee-ffff-4eee-8eee-eeeeeeeeeeef"),
    # The officer who transmits a return to the regulator, and only that.
    # Filing stopped sharing the approver's permission on 2026-09-20
    # (docs/filing_workflow_redesign.md §6 step 1): a Validator holds an exact
    # Regulatory Reporting / restricted `submit` binding, plus a read sentence
    # so they can open the return they are being asked to file. Deliberately
    # NOT the `approver` fixture — one identity that both approves and files is
    # the defect the split closed, and the tenant grant surface blocks it.
    "validator": UUID("eeeeeeee-dddd-4eee-8eee-eeeeeeeeeeed"),
}

#: The governed date from which an ICAAP report may be filed.
#:
#: The seeded value is the Ghana exposure draft's inferred first year end
#: (2026-12-31, ``confirmation_status="pending"``), which is AFTER the canonical
#: test book's span — so a freeze would be refused ``return_not_yet_effective``
#: and the filing journey could never run. Staff move this row in the console
#: when a regulator confirms its regime's commencement (D-024/D-032); the e2e
#: stack has no console, so the row is moved here, the same way the pytest
#: suite's ``govern_first_as_of`` helper moves it. It is DATA, not code: no
#: commencement date is written into an engine.
E2E_ICAAP_FIRST_AS_OF = date(2025, 12, 31)


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
        _govern_icaap_commencement(session)
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
                    # Account-plane fixtures become scalar account admins
                    # after initial ownership is assigned, so they do not
                    # create ambiguous owner candidates during bootstrap.
                    role=(
                        "viewer"
                        if role
                        in {
                            "grant_member",
                            "account_admin",
                            "legacy_account_admin",
                            "integration_admin",
                            "liquidity_viewer",
                            "liquidity_aggregated_viewer",
                            "invite_fresh",
                            "macro_viewer",
                            # A board member holds no scalar role at all: their
                            # authority is one exact Capital/confidential
                            # binding, which is the whole point of the fixture.
                            "board",
                            # Likewise the Validator: a scalar role must never
                            # supply filing authority, so this fixture must be
                            # unable to file on anything but its binding.
                            "validator",
                        }
                        else role
                    ),
                    auth_provider="password",
                    password_hash=password_hash,
                )
                session.add(user)
            users[role] = user
        session.flush()
        for user in users.values():
            membership.ensure_baseline_membership(
                session,
                user=user,
                granted_by_id="e2e-bootstrap",
                commit=False,
            )
        # create_all does not run the initial-owner migration. Mirror the landed
        # #127 bootstrap so the Members journey exercises real owner authority.
        assign_initial_owner(
            session,
            organization_id=DEMO_ORG_ID,
            candidate=users["admin"],
            granted_by_id="e2e-bootstrap",
            commit=False,
        )
        # A review stage can name the officer titles that may take it, and the
        # check compares the SIGNED-IN user's recorded job title — never a
        # title sent with the decision, which would let a caller state who
        # approved the ICAAP. One fixture officer therefore needs one.
        #
        # It is THIS user and not `approver`, deliberately: the signing
        # workspace's recipient picker labels an option
        # `"{display name}{ — job title} ({role})"`, and
        # `e2e/support/ceremony.ts` selects the approver by that exact label.
        # Giving `approver` a title renames the option and hangs both
        # full-lifecycle journeys on a select with "no matching option".
        users["board"].job_title = "Chief Risk Officer"
        users["account_admin"].role = "account_admin"
        users["legacy_account_admin"].role = "account_admin"
        users["integration_admin"].role = "account_admin"
        session.commit()
        _enrol_signing_keys(session)
        _materialize_book(session)
        legacy_service_user = User(
            organization_id=DEMO_ORG_ID,
            email="e2e.legacy.integration@service.aequoros.invalid",
            display_name="E2E legacy unscoped integration",
            role="viewer",
            auth_provider="service",
            is_active=True,
        )
        session.add(legacy_service_user)
        session.flush()
        session.add(
            IntegrationKey(
                organization_id=DEMO_ORG_ID,
                bank_id=None,
                service_user_id=legacy_service_user.id,
                label="Legacy core banking feed",
                key_prefix="aeq_live_LEGY…",
                key_hash=hashlib.sha256(b"e2e legacy unscoped integration key").hexdigest(),
                created_by=users["admin"].id,
            )
        )
        for role, bundle in (
            ("admin", RoleBundle.ANALYST),
            ("approver", RoleBundle.APPROVER),
            ("analyst", RoleBundle.ANALYST),
        ):
            authorization.create_role_binding(
                session,
                organization_id=DEMO_ORG_ID,
                principal_user_id=users[role].id,
                principal_type=PrincipalType.HUMAN,
                role_bundle=bundle,
                scope=authorization.BindingScope(
                    InstitutionScope.ORGANIZATION,
                    None,
                    ModuleScope.ALL,
                    SensitivityScope.ALL,
                ),
                grantor=authorization.GrantorRef(
                    GrantorType.SYSTEM,
                    "e2e-bootstrap",
                ),
                reason="exercise the dashboard through explicit effective authority",
            )
        authorization.create_role_binding(
            session,
            organization_id=DEMO_ORG_ID,
            principal_user_id=users["liquidity_viewer"].id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.LIQUIDITY,
                SensitivityScope.ALL,
            ),
            grantor=authorization.GrantorRef(
                GrantorType.SYSTEM,
                "e2e-bootstrap",
            ),
            reason="exercise exact Liquidity read authority without granting another module",
        )
        authorization.create_role_binding(
            session,
            organization_id=DEMO_ORG_ID,
            principal_user_id=users["liquidity_aggregated_viewer"].id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.LIQUIDITY,
                SensitivityScope.AGGREGATED,
            ),
            grantor=authorization.GrantorRef(
                GrantorType.SYSTEM,
                "e2e-bootstrap",
            ),
            reason="exercise exact Liquidity read authority without granting another module",
        )
        # The board member. Capital/confidential APPROVER on the sample bank and
        # nothing else — no Regulatory Reporting access at all, which is why the
        # ICAAP Filing tab embeds the signing workspace rather than linking to
        # `/submissions`.
        authorization.create_role_binding(
            session,
            organization_id=DEMO_ORG_ID,
            principal_user_id=users["board"].id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.APPROVER,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.CAPITAL,
                SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(
                GrantorType.SYSTEM,
                "e2e-bootstrap",
            ),
            reason="a board member signs the ICAAP without Regulatory Reporting access",
        )
        # The Validator: two sentences, because they answer two questions. The
        # read sentence opens the Returns workspace; the filing sentence is the
        # only authority in the product that reaches a regulator channel.
        authorization.create_role_binding(
            session,
            organization_id=DEMO_ORG_ID,
            principal_user_id=users["validator"].id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.ALL,
                SensitivityScope.ALL,
            ),
            grantor=authorization.GrantorRef(
                GrantorType.SYSTEM,
                "e2e-bootstrap",
            ),
            reason="a validator must be able to read the return they are asked to file",
        )
        authorization.create_role_binding(
            session,
            organization_id=DEMO_ORG_ID,
            principal_user_id=users["validator"].id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VALIDATOR,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.REGULATORY,
                SensitivityScope.RESTRICTED,
            ),
            grantor=authorization.GrantorRef(
                GrantorType.SYSTEM,
                "e2e-bootstrap",
            ),
            reason="the officer who transmits this tenant's returns to the regulator",
        )
        authorization.create_role_binding(
            session,
            organization_id=DEMO_ORG_ID,
            principal_user_id=users["account_admin"].id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ACCOUNT_ADMIN,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.ACCOUNT,
                SensitivityScope.RESTRICTED,
            ),
            grantor=authorization.GrantorRef(
                GrantorType.SYSTEM,
                "e2e-bootstrap",
            ),
            reason="exercise scoped Account administration in the dashboard",
        )
        authorization.create_role_binding(
            session,
            organization_id=DEMO_ORG_ID,
            principal_user_id=users["integration_admin"].id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ACCOUNT_ADMIN,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.ACCOUNT,
                SensitivityScope.RESTRICTED,
            ),
            grantor=authorization.GrantorRef(
                GrantorType.SYSTEM,
                "e2e-bootstrap",
            ),
            reason="exercise integration-key administration in the dashboard",
        )
        authorization.create_role_binding(
            session,
            organization_id=DEMO_ORG_ID,
            principal_user_id=users["integration_admin"].id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.DATA,
                SensitivityScope.RESTRICTED,
            ),
            grantor=authorization.GrantorRef(
                GrantorType.SYSTEM,
                "e2e-bootstrap",
            ),
            reason="make the authorized API Push page visible for browser evidence",
        )
        session.commit()
        _materialize_live_plane(session)
    print("e2e database bootstrapped")


def _materialize_book(session: Session) -> None:
    """Seed the canonical book and carry it forward to the anchor currently due."""
    summary = materialize_canonical_test_book(session)
    appended = extend_canonical_test_book(session, through=latest_month_end_on_or_before())
    print(
        f"canonical book: {summary.periods} periods, carried forward through "
        f"{appended[-1].isoformat() if appended else 'the canonical span'} "
        f"({len(appended)} appended)"
    )


def latest_month_end_on_or_before(today: date | None = None) -> date:
    """The last month end on or before ``today``.

    Mirrors the Returns workspace's first anchor <= today in descending order,
    including today itself when it is a month end.
    """
    today = today or date.today()
    if today.day == calendar.monthrange(today.year, today.month)[1]:
        return today
    return today.replace(day=1) - timedelta(days=1)


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


def _govern_icaap_commencement(session: Session) -> None:
    """Move the ICAAP commencement date onto the canonical book's year end.

    The seeded row is the Ghana exposure draft's INFERRED first year end and is
    marked ``pending``; the canonical test book stops before it, so an annual
    FY2025 assessment would be refused ``return_not_yet_effective`` and the
    filing journey could never run.

    This writes the control-plane ROW, which is exactly what staff do in the
    console when a regulator confirms a commencement date — so it is also a
    proof that the change takes effect without a release (D-024 / D-032). No
    date is written into an engine, a template or a service.
    """
    from app.services.icaap.freeze import FIRST_AS_OF_PARAM  # noqa: PLC0415 - avoid a cycle

    rows = (
        session.query(RegulatoryParameter)
        .filter(RegulatoryParameter.param_code == FIRST_AS_OF_PARAM)
        .all()
    )
    for row in rows:
        row.value_json = {
            "schema": "icaap-effective-date-v1",
            "date": E2E_ICAAP_FIRST_AS_OF.isoformat(),
        }
    session.flush()
    print(
        f"governed {FIRST_AS_OF_PARAM} to {E2E_ICAAP_FIRST_AS_OF.isoformat()} "
        f"({len(rows)} row(s))"
    )


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
