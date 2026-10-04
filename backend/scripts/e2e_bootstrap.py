"""Bootstrap the hermetic e2e database (plan W7.5).

Creates the schema, the GLOBAL reference registries a deployment gets from
its migrations (jurisdictions, institution types, the regulatory-parameter
control plane — ``tests/fixtures/reference_data.py``, shared with the
hermetic pytest suite), and the tenant scaffolding the API's zero-trust layer
requires before any request can succeed: the demo organization and one user
per role.

It then lays down the fixture BOOK, in the order a deployment would arrive at
it: the canonical test book (the bank, its reporting-period spine and its
governed parameter registers) carried forward to the reporting anchor currently
due, the canonical position and GL sub-ledger the Data Engine would have
ingested, the live fact plane the worker's ``pipeline_refresh`` would have
derived, and the ``bi_*`` marts the ``bi`` worker lane would have built. The
e2e stack runs no worker and no migration, so each of those has a fixture
standing in for it; each one is a mirror of what the product writes, never a
second source of numbers. Everything downstream — the liquidity baseline run,
the institution profile, every package — flows through the API in the
Playwright global setup and the journeys themselves: the same paths the product
uses.

The book is carried forward because the Returns workspace opens on the
regulator's anchor (the last month end on or before today for a monthly return —
``services/regulatory_reporting/anchors.py``), and a return can only be
generated from an EXACT snapshot as of that date. The canonical book ends at
a fixed month; without the carry-forward every anchor after it reads "no
position has been computed", correctly, and the generate journeys have
nothing to drive. ``extend_canonical_test_book`` appends one snapshot per
month end through the last month end on or before today, each repeating the
canonical latest fact set unchanged.

It also enrols a **software signing key** per human role so the attestation
ceremony can be driven end to end in a browser. Self-signed and disposable:
the software backend refuses to initialise when APP_ENV is production, so this
path cannot exist in a real deployment. Without it the ceremony journey could
only be skipped, and a skipped journey proves nothing.

It also registers the local OIDC issuer (``scripts/e2e_idp.py``, started by
``playwright.config.ts``) as the tenant's **SSO connection** when
``E2E_IDP_ISSUER`` names it, through the same service the Settings page uses,
so the browser SSO journeys sign in and step up against a real relying-party
configuration rather than a mocked one. The connection restricts sign-in to
the fixture domain; ``e2e.sso_analyst`` is the pre-provisioned officer the
issuer's linked account maps onto.

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

from sqlalchemy import create_engine, func, select
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
from app.models import (
    CanonicalPositionSnapshot,
    CanonicalProduct,
    IntegrationKey,
    Organization,
    RegulatoryParameter,
    User,
)
from app.services import authorization, membership, sso_config
from app.services.attestation.identity import ensure_signer_identity
from app.services.attestation.keys import SignerKeyService
from app.services.organization_ownership import assign_initial_owner
from tests.factories.canonical import seed_canonical_fixture
from tests.fixtures.bi_plane import materialize_bi_plane
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
    "access_request_member": UUID("eeeeeeee-1010-4eee-8eee-eeeeeeee1010"),
    "access_extra_member": UUID("eeeeeeee-1011-4eee-8eee-eeeeeeee1011"),
    "grant_member": UUID("eeeeeeee-5555-4eee-8eee-eeeeeeeeeee5"),
    "account_admin": UUID("eeeeeeee-6666-4eee-8eee-eeeeeeeeeee6"),
    "legacy_account_admin": UUID("eeeeeeee-7777-4eee-8eee-eeeeeeeeeee7"),
    "integration_admin": UUID("eeeeeeee-8888-4eee-8eee-eeeeeeeeeee8"),
    "liquidity_viewer": UUID("eeeeeeee-9999-4eee-8eee-eeeeeeeeeee9"),
    "liquidity_aggregated_viewer": UUID("eeeeeeee-aaaa-4eee-8eee-eeeeeeeeeeea"),
    "macro_viewer": UUID("eeeeeeee-cccc-4eee-8eee-eeeeeeeeeeec"),
    "fx_member": UUID("eeeeeeee-dddd-4eee-8eee-eeeeeeeeeeed"),
    # Forecasting's live-grant journey revokes what it grants, but revoked rows stay
    # in a member's history, so it needs an identity no other journey counts.
    "forecast_member": UUID("eeeeeeee-0000-4eee-8eee-eeeeeeeeeee0"),
    # The same journey's aggregated-only reader. `macro_viewer` cannot play it:
    # the macro journey leaves that member an organization-wide read grant.
    "forecast_summary_member": UUID("eeeeeeee-0001-4eee-8eee-eeeeeeeee001"),
    "invite_fresh": UUID("eeeeeeee-bbbb-4eee-8eee-eeeeeeeeeeeb"),
    # A board member: Capital/confidential APPROVER on the sample bank and
    # nothing else. Deliberately holds NO Regulatory Reporting access, because
    # that is the real shape of the person — the ICAAP filing surface has to
    # give them a signature they could not otherwise give.
    "board": UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
    # The officer who transmits a return to the regulator, and only that.
    # Filing stopped sharing the approver's permission on 2026-09-20
    # (docs/filing_workflow_redesign.md §6 step 1): a Validator holds an exact
    # Regulatory Reporting / restricted `submit` binding, plus a read sentence
    # so they can open the return they are being asked to file. Deliberately
    # NOT the `approver` fixture — one identity that both approves and files is
    # the defect the split closed, and the tenant grant surface blocks it.
    "validator": UUID("eeeeeeee-ffff-4eee-8eee-eeeeeeeeeeef"),
    "sso_analyst": UUID("eeeeeeee-1111-4eee-8eee-eeeeeeeeeee1"),
}
#: The e2e tenant's SSO connection. Client id/secret mirror scripts/e2e_idp.py.
E2E_SSO_CLIENT_ID = "aequoros-dashboard-e2e"
E2E_SSO_CLIENT_SECRET = "e2e-idp-client-secret-not-production-000"  # noqa: S105 - disposable fixture
E2E_SSO_ALLOWED_DOMAINS = ["aequoros.example"]


def _assert_one_identity_per_role() -> None:
    """Every fixture role must be its OWN identity, checked before anything runs.

    From ``a4223447`` (PR #204) until 2026-09-22 ``macro_viewer`` carried the
    ``board`` UUID, and the consequences were nowhere near the cause. Two of
    them:

    * ``_enrol_signing_keys`` walks these entries and issues one software key
      per signer identity, so the second visit to the shared id raised
      ``SignerKeyError`` and the WHOLE bootstrap aborted — no canonical book, no
      live plane, no marts, every Playwright journey unrunnable.
    * Worse if it had not crashed: the loop below would have created one user
      and then hung BOTH authority fixtures on it, so a test asserting that a
      Macro-only viewer cannot reach Capital would have been silently asserting
      it about a Capital approver.

    Checked at import, so the failure names the collision instead of surfacing
    three stages later as a key-enrolment error. ``dashboard/e2e/support/mint.ts``
    mints cookies from its own copy of this table: the two must agree, exactly
    as they must for ``E2E_PASSWORD``.
    """
    seen: dict[UUID, str] = {}
    for role, user_id in E2E_USERS.items():
        if (owner := seen.get(user_id)) is not None:
            msg = (
                f"E2E_USERS['{role}'] reuses the '{owner}' UUID {user_id}. Each fixture "
                "role is a distinct authority fixture and needs its own identity: a "
                "shared id makes the two roles one user, which merges their grants and "
                "breaks signing-key enrolment."
            )
            raise SystemExit(msg)
        seen[user_id] = role


_assert_one_identity_per_role()

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
                user = User(
                    id=user_id,
                    organization_id=DEMO_ORG_ID,
                    email=f"e2e.{role}@aequoros.example",
                    display_name=f"E2E {_display_role(role)}",
                    # Account-plane fixtures become scalar account admins
                    # after initial ownership is assigned, so they do not
                    # create ambiguous owner candidates during bootstrap.
                    role=(
                        "viewer"
                        if role
                        in {
                            "grant_member",
                            "fx_member",
                            "forecast_member",
                            "forecast_summary_member",
                            "access_request_member",
                            "access_extra_member",
                            "forecast_summary_member",
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
                        else "analyst"
                        if role == "sso_analyst"
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
        session.flush()
        _seed_canonical_positions(session)
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
            ("sso_analyst", RoleBundle.ANALYST),
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
        _register_sso_connection(session)
        _materialize_live_plane(session)
        _materialize_bi_plane(session)
    print("e2e database bootstrapped")


def _seed_canonical_positions(session: Session) -> None:
    """Layer the canonical POSITION book on top of the period fact spine.

    ``materialize_canonical_test_book`` writes the 12-period
    ``bank_financial_facts`` spine and the governed parameter registers, but no
    ``canonical_position_snapshots`` at all — so before this the position and
    loan-level half of the product had nothing to read, and the BI marts built
    from an empty book (H-013: ``materialize_bi_plane`` returned "no position
    snapshots to build from" and every BI page would have opened on its empty
    state).

    ``tests/factories/canonical.py`` is the same fixture every hermetic suite
    layers on the test book — the GL chart, one product per regulatory category,
    retail and corporate counterparties, and one position per type with
    hand-checkable aggregates — so the browser sees the book the unit tests are
    written against rather than a second, unverified one. Fixture data on a
    throwaway sqlite file, exactly as ``live_plane.py`` and ``bi_plane.py`` are:
    it stands in for what the Data Engine and the worker would have written, and
    it exists nowhere near a product code path.

    **The position book is seeded at the date the FACT SPINE reaches, not at the
    fixture's own default (H-015).** ``_materialize_book`` carries the spine
    forward to ``latest_month_end_on_or_before()``, and the live plane therefore
    computes at that date, while ``seed_canonical_fixture``'s default
    ``FIXTURE_AS_OF`` is fixed. Left alone the two halves of one fixture sit at
    different dates, and the consequences are silent rather than loud:
    ``materialize_bi_plane`` builds at the latest POSITION date, looks for live
    metrics there, finds them two months later, and copies **zero**
    ``bi_fact_engine_metric`` rows — so every engine measure in BI is empty. A
    browser journey asserting an engine figure would then be vacuous, or would
    assert the broken state as if it were the product's. (Until 2026-09-29 this
    also greyed the R1–R4 reconciliation checks; those are gone, but the empty
    engine mart they were the loud symptom of is not.)
    A real bank's book and its fact spine advance together;
    passing the date explicitly is what makes the fixture behave that way. The
    hermetic suite keeps the default, so nothing there moves.
    """
    as_of = latest_month_end_on_or_before()
    seed_canonical_fixture(
        session, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID, as_of=as_of
    )
    session.flush()
    _assign_position_risk_weights(session)
    snapshots = session.scalar(
        select(func.count())
        .select_from(CanonicalPositionSnapshot)
        .where(
            CanonicalPositionSnapshot.organization_id == DEMO_ORG_ID,
            CanonicalPositionSnapshot.bank_id == SAMPLE_BANK_ID,
        )
    )
    print(f"canonical positions: {snapshots} snapshots at {as_of.isoformat()}")


#: The governed default risk-weight code each credit product carries.
_PRODUCT_RISK_WEIGHT_CODES = {
    "LN.CORP.5Y": "RW100",
    "LN.RET.PERS": "RW75",
    "LN.RET.MORT": "RW35",
    "LN.SME.TERM": "RW75",
}
_POSITION_RISK_WEIGHT_OVERRIDES = {"IBP/1": "RW20", "LOAN/6": "RW150"}


def _assign_position_risk_weights(session: Session) -> None:
    """Give every credit exposure in the position book a governed risk weight.

    ``seed_canonical_fixture`` books positions without risk-weight codes, which
    the hermetic suites' hand-checked aggregates are written against. A bank's
    real book arrives with them, and enterprise stress refuses a book where any
    credit exposure has none (``risk_weight_unresolved``) rather than assuming a
    weight. Without this the browser would only ever see that refusal.
    """
    products = session.scalars(
        select(CanonicalProduct).where(
            CanonicalProduct.organization_id == DEMO_ORG_ID,
            CanonicalProduct.bank_id == SAMPLE_BANK_ID,
            CanonicalProduct.product_code.in_(_PRODUCT_RISK_WEIGHT_CODES),
        )
    )
    for product in products:
        product.risk_weight_code = _PRODUCT_RISK_WEIGHT_CODES[product.product_code]
    snapshots = session.scalars(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.organization_id == DEMO_ORG_ID,
            CanonicalPositionSnapshot.bank_id == SAMPLE_BANK_ID,
            CanonicalPositionSnapshot.source_reference.in_(_POSITION_RISK_WEIGHT_OVERRIDES),
        )
    )
    for snapshot in snapshots:
        snapshot.attributes = {
            **snapshot.attributes,
            "risk_weight_code": _POSITION_RISK_WEIGHT_OVERRIDES[snapshot.source_reference],
        }
    session.flush()


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


def _display_role(role: str) -> str:
    """``sso_analyst`` → ``SSO Analyst``; the name the shell and the approver picker show."""
    return " ".join(
        part.upper() if part == "sso" else part.capitalize() for part in role.split("_")
    )


def _register_sso_connection(session: Session) -> None:
    """Point the tenant at the local issuer, through the product's own path.

    ``upsert_connection`` seals the client secret with the credential vault and
    applies the same issuer screening the Settings page does — the plain-http
    loopback issuer passes only because APP_ENV is undeployed, which is exactly
    the rule that keeps this issuer out of a deployment.
    """
    issuer = os.environ.get("E2E_IDP_ISSUER")
    if not issuer:
        print("sso connection: not registered (E2E_IDP_ISSUER unset)")
        return
    sso_config.upsert_connection(
        session,
        organization_id=DEMO_ORG_ID,
        issuer=issuer,
        client_id=E2E_SSO_CLIENT_ID,
        client_secret=E2E_SSO_CLIENT_SECRET,
        allowed_email_domains=E2E_SSO_ALLOWED_DOMAINS,
        enabled=True,
        actor_user_id=None,
    )
    print(f"sso connection: {issuer} registered for {DEMO_ORG_ID}")


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


def _materialize_bi_plane(session: Session) -> None:
    """Stand in for the ``bi`` worker lane's ``bi_mart_refresh`` job.

    The BI surfaces read the ``bi_*`` marts, which only that lane writes — and
    the e2e stack runs no worker. Runs the product's own builder for the
    bank's latest snapshot date, AFTER the live plane so the engine tier and
    the freshness signal has a build to report.
    """
    outcome = materialize_bi_plane(session, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID)
    session.commit()
    if outcome is None:
        print("bi plane: no position snapshots to build from")
        return
    print(
        f"bi plane: {outcome.status}, {outcome.row_counts.get('bi_fact_position_daily', 0)} "
        "position rows"
    )


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
        f"governed {FIRST_AS_OF_PARAM} to {E2E_ICAAP_FIRST_AS_OF.isoformat()} ({len(rows)} row(s))"
    )


def _enrol_signing_keys(session: Session) -> None:
    """Give every e2e human a signer identity and a self-signed software key."""
    ctx = TenantContext(organization_id=DEMO_ORG_ID)
    service = SignerKeyService(session, ctx)
    for role, user_id in E2E_USERS.items():
        identity = ensure_signer_identity(session, ctx, user_id)
        service.issue(
            signer_id=identity.signer_id,
            display_name=f"E2E {_display_role(role)}",
            organization_name="AequorOS E2E",
        )
        session.commit()
        print(f"enrolled signing key for {role}: {identity.signer_id}")


if __name__ == "__main__":
    main()
