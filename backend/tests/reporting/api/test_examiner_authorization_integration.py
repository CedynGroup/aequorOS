"""Examiner evidence needs an AUDIT grant AND each source module's own grant.

Issue 180. The bundle aggregates every module, so it takes two independent
sentences: one complete ``audit``/``restricted`` ``view`` binding for the
institution (the entry gate), and the ``view`` behind every embedded source (the
source gate). This module proves both against the HTTP surface:

* refusal before any read, with no scalar-role fallback and no partial-binding
  composition;
* per-module filtering in the run list and in the documentation package;
* an unauthorized run module answering the same opaque 404 as a missing run;
* the register catalogue and the CFP block withheld explicitly rather than
  rendered as absent facts;
* operator inspection (act-as-examiner staff) still reading the bundle, gaining
  nothing from a tenant AUDIT grant and needing none;
* a generated binding set checked against an independently stated oracle for
  every examiner operation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from datetime import UTC
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from hypothesis import example, given
from sqlalchemy import Connection, delete, func, select
from sqlalchemy.orm import Session

from app.core import security
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.core.tenancy import TenantContext
from app.db.base import utc_now
from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.user import User
from app.identity.service import authorization
from app.models import Bank, BankReportingPeriod, RegulatoryPackage, RegulatoryRun
from app.models.audit_event import AuditEvent
from app.reporting.schemas.examiner import (
    ExaminerDocumentationRead,
    ExaminerRunsRead,
    RunReproductionRead,
)
from app.services.audit import record_event
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.reporting.testing.authorization_matrix import OPERATIONS, expected_status
from tests.reporting.testing.generators import (
    SIBLING_BANK_ID,
    EvidenceBinding,
    evidence_binding_sets,
)
from tests.support.authorization_fixtures import tenant_user
from tests.support.helpers import ORG_1, ORG_2, USER_1, error_envelope, headers

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
RUNS = f"{BASE}/examiner/runs"
DOCUMENTATION = f"{BASE}/examiner/documentation-package"
REPORTING_DATE = dt.date(2026, 3, 31)
OPERATOR = "ops@aequoros.example"


@pytest.fixture(autouse=True)
def book_without_authority(db_client: TestClient, db_session: Session) -> None:
    """Build the canonical book and strip the bootstrap binding.

    The shared hermetic fixture grants ``USER_1`` a viewer/all/all row, which
    legitimately covers Audit too. These tests start from zero authority so every
    decision is the one the test states.
    """
    _ = db_client
    materialize_canonical_test_book(db_session)
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    db_session.add(
        Bank(
            id=SIBLING_BANK_ID,
            organization_id=ORG_1,
            name="Sibling Bank Ltd",
            short_name="Sibling",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type="universal_bank",
        )
    )
    db_session.commit()


def grant(  # noqa: PLR0913 - every binding dimension is an explicit input
    db: Session,
    bundle: RoleBundle = RoleBundle.AUDITOR,
    *,
    module: ModuleScope = ModuleScope.AUDIT,
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
    bank_id: str | None = None,
    user_id: UUID = USER_1,
) -> AuthorizationBinding:
    """One complete authority sentence; ``None`` institution is organization-wide."""

    row = authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION if bank_id is None else InstitutionScope.INSTITUTION,
            bank_id,
            module,
            sensitivity,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "examiner-authorization-test"),
        reason="Exercise explicit examiner evidence authority",
        commit=False,
    )
    user = tenant_user(db, organization_id=ORG_1, user_id=user_id)
    user.authorization_version = 1
    db.commit()
    return row


def set_lifecycle(db: Session, row: AuthorizationBinding, lifecycle: str) -> None:
    """Put a present row outside the active window, or suspend/revoke it."""

    if lifecycle == "future":
        row.valid_from = utc_now() + dt.timedelta(days=1)
    elif lifecycle == "expired":
        row.valid_from = utc_now() - dt.timedelta(days=2)
        row.valid_until = utc_now() - dt.timedelta(days=1)
    elif lifecycle in ("suspended", "revoked"):
        row.status = lifecycle
        if lifecycle == "revoked":
            row.revoked_at = utc_now()
            row.revoked_by_type = "system"
            row.revoked_by_id = "examiner-authorization-test"
            row.revoked_reason = "Exercise revocation"
    else:  # pragma: no cover - the caller only asks for a refusal
        raise AssertionError(f"unexpected lifecycle {lifecycle}")
    db.commit()


def period(db: Session) -> BankReportingPeriod:
    row = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == ORG_1,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert row is not None
    return row


def period_path(suffix: str, db: Session) -> str:
    return f"{suffix}?reporting_period_id={period(db).id}"


def run(  # noqa: PLR0913 - one stored run row, spelled out
    db: Session,
    *,
    module: str = "liquidity",
    scenario_code: str = "baseline",
    status: str = "succeeded",
) -> RegulatoryRun:
    inputs: dict[str, object] = {"facts": [], "module": module}
    row = RegulatoryRun(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        reporting_period_id=period(db).id,
        module=module,
        scenario_code=scenario_code,
        status=status,
        engine_version="examiner-test-v1",
        input_schema_version="examiner-test-input-v1",
        output_schema_version="examiner-test-output-v1",
        input_hash=hashlib.sha256(
            json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest(),
        inputs=inputs,
        metrics={},
        created_by=USER_1,
    )
    db.add(row)
    db.commit()
    return row


def package(db: Session, *, family: str, return_code: str) -> RegulatoryPackage:
    row = RegulatoryPackage(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        return_family=family,
        return_code=return_code,
        reporting_date=REPORTING_DATE,
        frequency="monthly",
        basis="solo",
        status="generated",
        version=1,
        snapshot={"sections": []},
        source_runs=[],
        generated_by=USER_1,
        generated_at=dt.datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    return row


def documentation(
    client: TestClient, db: Session, *, user_id: UUID | None = None
) -> ExaminerDocumentationRead:
    response = client.get(period_path(DOCUMENTATION, db), headers=headers(user_id=user_id))
    assert response.status_code == 200, response.text
    return ExaminerDocumentationRead.model_validate(response.json())


# --- the entry gate ---------------------------------------------------------
@pytest.mark.parametrize("role", ["admin", "approver", "analyst", "viewer", "examiner"])
def test_scalar_roles_never_open_examiner_evidence(
    db_client: TestClient, db_session: Session, role: str
) -> None:
    for path in (period_path(RUNS, db_session), period_path(DOCUMENTATION, db_session)):
        denied = db_client.get(path, headers=headers(roles=(role,)))
        assert denied.status_code == 403, denied.text
    run(db_session)
    assert (
        db_client.get(period_path(RUNS, db_session), headers=headers(roles=(role,))).status_code
        == 403
    )
    # ...and another tenant cannot even resolve the institution: 404, not 403.
    assert db_client.get(period_path(RUNS, db_session), headers=headers(ORG_2)).status_code == 404


@pytest.mark.parametrize(
    "dimension",
    [
        "absent",
        "module",
        "sensitivity",
        "sibling_bank",
        "future",
        "expired",
        "suspended",
        "revoked",
    ],
)
def test_incomplete_or_inactive_audit_bindings_are_refused(
    db_client: TestClient, db_session: Session, dimension: str
) -> None:
    if dimension != "absent":
        row = grant(
            db_session,
            module=ModuleScope.CAPITAL if dimension == "module" else ModuleScope.AUDIT,
            sensitivity=(
                SensitivityScope.CONFIDENTIAL
                if dimension == "sensitivity"
                else SensitivityScope.RESTRICTED
            ),
            bank_id=SIBLING_BANK_ID if dimension == "sibling_bank" else None,
        )
        if dimension in ("future", "expired", "suspended", "revoked"):
            set_lifecycle(db_session, row, dimension)
    denied = db_client.get(period_path(DOCUMENTATION, db_session), headers=headers())
    assert denied.status_code == 403, denied.text


def test_a_bundle_without_view_is_refused_however_complete_its_scope_is(
    db_client: TestClient, db_session: Session
) -> None:
    """Account administration carries ``administer``, never ``view``."""

    grant(db_session, RoleBundle.ACCOUNT_ADMIN)
    denied = db_client.get(period_path(DOCUMENTATION, db_session), headers=headers())
    assert denied.status_code == 403, denied.text


def test_partial_bindings_never_compose(db_client: TestClient, db_session: Session) -> None:
    """Two half-sentences are two rows, never one Audit/restricted sentence."""

    grant(db_session, module=ModuleScope.AUDIT, sensitivity=SensitivityScope.CONFIDENTIAL)
    grant(db_session, module=ModuleScope.LIQUIDITY, sensitivity=SensitivityScope.RESTRICTED)
    for path in (period_path(RUNS, db_session), period_path(DOCUMENTATION, db_session)):
        assert db_client.get(path, headers=headers()).status_code == 403


# --- the source gate -------------------------------------------------------
def test_run_list_filters_modules_before_composing(
    db_client: TestClient, db_session: Session
) -> None:
    visible = run(db_session, module="liquidity")
    capital = run(db_session, module="capital")
    credit = run(db_session, module="credit", scenario_code="vintage")
    grant(db_session)
    assert (
        ExaminerRunsRead.model_validate(
            db_client.get(period_path(RUNS, db_session), headers=headers()).json()
        ).runs
        == []
    )
    # One source grant opens exactly one module of the stored set.
    grant(
        db_session,
        RoleBundle.VIEWER,
        module=ModuleScope.LIQUIDITY,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    listed = ExaminerRunsRead.model_validate(
        db_client.get(period_path(RUNS, db_session), headers=headers()).json()
    )
    assert {item.run_id for item in listed.runs} == {visible.id}
    # Asking for a module the caller cannot open filters the same frozen set
    # instead of disclosing that a run exists.
    narrowed = ExaminerRunsRead.model_validate(
        db_client.get(f"{period_path(RUNS, db_session)}&module=capital", headers=headers()).json()
    )
    assert narrowed.runs == []
    assert {capital.id, credit.id}.isdisjoint({item.run_id for item in listed.runs})


def test_unreadable_run_module_answers_the_missing_run_404(
    db_client: TestClient, db_session: Session
) -> None:
    capital = run(db_session, module="capital")
    grant(db_session)
    hidden = db_client.get(f"{RUNS}/{capital.id}/reproduction", headers=headers())
    missing = db_client.get(f"{RUNS}/{uuid4()}/reproduction", headers=headers())
    assert hidden.status_code == missing.status_code == 404
    assert error_envelope(hidden) == error_envelope(missing)

    grant(
        db_session,
        RoleBundle.VIEWER,
        module=ModuleScope.CAPITAL,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    allowed = db_client.get(f"{RUNS}/{capital.id}/reproduction", headers=headers())
    assert allowed.status_code == 200, allowed.text
    body = RunReproductionRead.model_validate(allowed.json())
    assert body.recomputed_input_hash == capital.input_hash
    assert body.reproducible is True


def test_a_sibling_institutions_grant_never_opens_this_book(
    db_client: TestClient, db_session: Session
) -> None:
    """Two banks of one tenant share an organization, never each other's book."""

    grant(
        db_session,
        module=ModuleScope.AUDIT,
        sensitivity=SensitivityScope.RESTRICTED,
        bank_id=SIBLING_BANK_ID,
    )
    assert (
        db_client.get(period_path(DOCUMENTATION, db_session), headers=headers()).status_code == 403
    )
    # The same sentence at organization breadth is what actually covers this
    # institution — coverage is a property of the row, not of the bundle.
    grant(db_session, module=ModuleScope.AUDIT, sensitivity=SensitivityScope.RESTRICTED)
    assert (
        db_client.get(period_path(DOCUMENTATION, db_session), headers=headers()).status_code == 200
    )


def test_documentation_package_filters_every_embedded_source(
    db_client: TestClient, db_session: Session
) -> None:
    run(db_session, module="liquidity")
    run(db_session, module="capital")
    package(db_session, family="liquidity", return_code="LCR-NSFR")
    package(db_session, family="icaap", return_code="ICAAP-REPORT")

    # Entry alone: the bundle is an empty shell that SAYS what it withheld.
    grant(db_session)
    bare = documentation(db_client, db_session)
    assert bare.latest_runs == []
    assert bare.packages == []
    assert bare.register_endpoints == []
    assert {"cfp", "run:liquidity", "run:capital"} <= set(bare.withheld_sources)
    assert "register:liquidity-thresholds" in bare.withheld_sources

    # One source grant opens exactly that source, including the CFP posture it
    # authorizes (Liquidity/confidential) and the register it cites.
    grant(
        db_session,
        RoleBundle.VIEWER,
        module=ModuleScope.LIQUIDITY,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    body = documentation(db_client, db_session)
    assert {item.module for item in body.latest_runs} == {"liquidity"}
    # Return families ride their own gate: an ordinary return needs Regulatory
    # Reporting/restricted, ICAAP needs Capital/confidential.
    assert body.packages == []
    assert {"package:liquidity", "package:icaap"} <= set(body.withheld_sources)
    assert "run:capital" in body.withheld_sources
    assert "cfp" not in body.withheld_sources
    assert body.cfp_approved_version is None
    assert all("capital-plan" not in path for path in body.register_endpoints)
    assert all("ecl-assumptions" not in path for path in body.register_endpoints)
    assert any(path.endswith("/liquidity-thresholds") for path in body.register_endpoints)
    assert {"register:capital-plan", "register:ecl-assumptions"} <= set(body.withheld_sources)

    grant(
        db_session,
        RoleBundle.VIEWER,
        module=ModuleScope.REGULATORY,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    grant(
        db_session,
        RoleBundle.VIEWER,
        module=ModuleScope.CAPITAL,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    granted = documentation(db_client, db_session)
    assert {item.module for item in granted.latest_runs} == {"liquidity", "capital"}
    assert {item.return_code for item in granted.packages} == {"LCR-NSFR", "ICAAP-REPORT"}
    assert "cfp" not in granted.withheld_sources
    assert "run:capital" not in granted.withheld_sources
    assert any("capital-plan" in path for path in granted.register_endpoints)
    # Every remaining disclosure names a source this reader still does not hold,
    # and names it as withheld rather than leaving a quiet gap.
    assert {"register:ecl-assumptions", "register:crm-haircuts"} <= set(granted.withheld_sources)
    assert {"package:liquidity", "package:icaap"}.isdisjoint(granted.withheld_sources)
    assert all(
        entry == "cfp" or entry.startswith(("run:", "package:", "register:"))
        for entry in granted.withheld_sources
    )


def test_credit_registers_require_restricted_not_confidential(
    db_client: TestClient, db_session: Session
) -> None:
    """The examiner's Credit register source uses the register route's sentence.

    The four credit registers became CREDIT / ``restricted`` / ``view`` in the
    #182 remainder. The bundle cites two of them, so its source map must use the
    same sensitivity: a CREDIT/``confidential`` row is a different sentence and
    leaves them withheld rather than showing a register the reader cannot open.
    """

    grant(db_session)  # the AUDIT/restricted entry sentence
    grant(
        db_session,
        module=ModuleScope.CREDIT,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    confidential = documentation(db_client, db_session)
    assert all("ecl-assumptions" not in path for path in confidential.register_endpoints)
    assert all("crm-haircuts" not in path for path in confidential.register_endpoints)
    assert {"register:ecl-assumptions", "register:crm-haircuts"} <= set(
        confidential.withheld_sources
    )

    grant(
        db_session,
        module=ModuleScope.CREDIT,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    restricted = documentation(db_client, db_session)
    assert any(path.endswith("/ecl-assumptions") for path in restricted.register_endpoints)
    assert any(path.endswith("/crm-haircuts") for path in restricted.register_endpoints)
    assert {"register:ecl-assumptions", "register:crm-haircuts"}.isdisjoint(
        restricted.withheld_sources
    )


def test_auditor_and_viewer_bundles_are_equivalent_at_the_permission_level(
    db_client: TestClient, db_session: Session
) -> None:
    """Two bundles that both carry only ``view`` must not decide differently."""

    run(db_session, module="liquidity")
    package(db_session, family="liquidity", return_code="LCR-NSFR")
    viewer = User(
        id=uuid4(),
        organization_id=ORG_1,
        email="examiner.viewer@example.test",
        display_name="Examiner Viewer",
    )
    db_session.add(viewer)
    db_session.flush()
    for user_id, bundle in ((USER_1, RoleBundle.AUDITOR), (viewer.id, RoleBundle.VIEWER)):
        grant(db_session, bundle, user_id=user_id)
        grant(
            db_session,
            bundle,
            module=ModuleScope.LIQUIDITY,
            sensitivity=SensitivityScope.CONFIDENTIAL,
            user_id=user_id,
        )
    auditor = documentation(db_client, db_session)
    viewer_body = documentation(db_client, db_session, user_id=viewer.id)
    assert viewer_body == auditor
    assert (
        db_client.get(period_path(RUNS, db_session), headers=headers(user_id=viewer.id)).json()
        == db_client.get(period_path(RUNS, db_session), headers=headers()).json()
    )


def test_audit_event_count_is_attributable_to_this_institution(
    db_client: TestClient, db_session: Session
) -> None:
    """Two banks of one tenant share a tenant, not their activity volume."""

    grant(db_session)
    baseline = documentation(db_client, db_session).audit_event_count
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)
    ours = run(db_session)
    record_event(
        db_session,
        ctx,
        event_type="examiner_probe",
        entity_type="regulatory_run",
        entity_id=str(ours.id),
    )
    record_event(
        db_session,
        ctx,
        event_type="examiner_probe_sibling",
        entity_type="bank",
        entity_id=SIBLING_BANK_ID,
        bank_id=SIBLING_BANK_ID,
    )
    db_session.commit()
    sibling_events = db_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.event_type == "examiner_probe_sibling")
    )
    assert sibling_events == 1
    assert documentation(db_client, db_session).audit_event_count == baseline + 1


# --- operator inspection ----------------------------------------------------
def impersonation_headers(organization_id: str = ORG_1) -> dict[str, str]:
    secret = get_settings().auth.impersonation_jwt_secret
    assert secret
    now = utc_now()
    token = security.mint_impersonation_token(
        organization_id=organization_id,
        act_operator=OPERATOR,
        session_id=str(uuid4()),
        secret=secret,
        issued_at=now,
        expires_at=now + dt.timedelta(minutes=15),
    )
    return {"Authorization": f"Bearer {token}"}


def test_operator_inspection_reads_without_a_tenant_audit_grant(
    db_client: TestClient, db_session: Session
) -> None:
    """Staff inspection is its own lifecycle and never needs a product grant."""

    capital = run(db_session, module="capital")
    run(db_session, module="liquidity")
    package(db_session, family="icaap", return_code="ICAAP-REPORT")
    bearer = impersonation_headers()
    response = db_client.get(period_path(DOCUMENTATION, db_session), headers=bearer)
    assert response.status_code == 200, response.text
    body = ExaminerDocumentationRead.model_validate(response.json())
    assert {item.module for item in body.latest_runs} == {"capital", "liquidity"}
    assert {item.return_code for item in body.packages} == {"ICAAP-REPORT"}
    assert body.withheld_sources == []
    assert db_client.get(f"{RUNS}/{capital.id}/reproduction", headers=bearer).status_code == 200
    # A tenant AUDIT grant changes nothing for a staff session: it is not the
    # entry, and the seat stays read-only either way.
    grant(db_session)
    assert db_client.get(period_path(DOCUMENTATION, db_session), headers=bearer).status_code == 200
    assert (
        db_client.post(
            f"{BASE}/regulatory-runs",
            headers=bearer,
            json={"module": "liquidity", "reporting_period_id": str(period(db_session).id)},
        ).status_code
        == 403
    )


def test_operator_inspection_is_still_tenant_isolated(
    db_client: TestClient, db_session: Session
) -> None:
    foreign = db_client.get(
        period_path(DOCUMENTATION, db_session), headers=impersonation_headers(ORG_2)
    )
    assert foreign.status_code == 404


# --- generated authority matrix --------------------------------------------
def test_generated_binding_sets_match_the_examiner_http_matrix(
    db_client: TestClient, db_session: Session
) -> None:
    # Ratchet the executable catalogue: a new examiner route must receive its
    # own independent matrix row rather than silently going untested.
    app = cast(FastAPI, db_client.app)
    catalogued = {
        (method, route.path.removeprefix("/api/v1"))
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.endpoint.__module__.startswith("app.reporting.api.examiner_")
        and route.include_in_schema
        for method in route.methods
    }
    assert catalogued == {(operation.method, operation.path) for operation in OPERATIONS}

    liquidity = run(db_session, module="liquidity")
    period_id = period(db_session).id
    runs_before = db_session.scalar(select(func.count()).select_from(RegulatoryRun))
    connection = db_session.get_bind()
    assert isinstance(connection, Connection)

    @given(bindings=evidence_binding_sets())
    @example(bindings=[])
    @example(
        bindings=[
            EvidenceBinding(RoleBundle.AUDITOR, ModuleScope.AUDIT, SensitivityScope.RESTRICTED)
        ]
    )
    @example(
        bindings=[
            EvidenceBinding(RoleBundle.AUDITOR, ModuleScope.AUDIT, SensitivityScope.RESTRICTED),
            EvidenceBinding(
                RoleBundle.ANALYST, ModuleScope.LIQUIDITY, SensitivityScope.CONFIDENTIAL
            ),
        ]
    )
    @example(
        bindings=[
            EvidenceBinding(RoleBundle.VIEWER, ModuleScope.AUDIT, SensitivityScope.CONFIDENTIAL),
            EvidenceBinding(RoleBundle.VIEWER, ModuleScope.CAPITAL, SensitivityScope.RESTRICTED),
        ]
    )
    @example(bindings=[EvidenceBinding(RoleBundle.VIEWER, ModuleScope.ALL, SensitivityScope.ALL)])
    @example(
        bindings=[
            EvidenceBinding(
                RoleBundle.AUDITOR,
                ModuleScope.AUDIT,
                SensitivityScope.RESTRICTED,
                institution_id=SIBLING_BANK_ID,
            )
        ]
    )
    def check(bindings: list[EvidenceBinding]) -> None:
        example_transaction = connection.begin_nested()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
                db.info["organization_id"] = ORG_1
                db.execute(
                    delete(AuthorizationBinding).where(
                        AuthorizationBinding.principal_user_id == USER_1
                    )
                )
                db.commit()
                for binding in bindings:
                    row = grant(
                        db,
                        binding.bundle,
                        module=binding.module,
                        sensitivity=binding.sensitivity,
                        bank_id=binding.institution_id,
                    )
                    if binding.lifecycle != "active":
                        set_lifecycle(db, row, binding.lifecycle)
                for operation in OPERATIONS:
                    path = operation.path.replace("{bank_id}", SAMPLE_BANK_ID)
                    if operation.run_module is None:
                        path = f"{path}?reporting_period_id={period_id}"
                    else:
                        path = path.replace("{run_id}", str(liquidity.id))
                    response = db_client.get(f"/api/v1{path}", headers=headers())
                    assert response.status_code == expected_status(
                        bindings, operation, institution_id=SAMPLE_BANK_ID
                    ), (operation.path, bindings, response.status_code, response.text)
                # The examiner surfaces are read-only: no row, no run, no event.
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(AuthorizationBinding)
                        .where(AuthorizationBinding.principal_user_id == USER_1)
                    )
                    or 0
                ) == len(bindings)
                assert db.scalar(select(func.count()).select_from(RegulatoryRun)) == runs_before
        finally:
            example_transaction.rollback()
            db_session.expire_all()

    check()
