"""Bank audit reads: explicit authority, evidence, filtering and isolation."""

import os
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, JsonValue, TypeAdapter
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker
from sqlalchemy.sql import Select

from app.api import deps
from app.api.deps import IcaapAccess
from app.attestation.service import stepup
from app.core import security
from app.core.audit_attribution import subject_bank_sql
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    Permission,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import AuthSettings, get_settings
from app.core.tenancy import TenantContext
from app.domain.capital.engine import CapitalParams, CapitalRatiosResult, RwaResult
from app.icaap.schemas.workspace import (
    IcaapCycleCreate,
    IcaapSectionCommit,
    IcaapSectionWorkingSave,
)
from app.icaap.service import cycles, sections
from app.identity.schemas.audit_log import AuditLogRead
from app.identity.schemas.auth import MeResponse
from app.identity.schemas.banks import BankListRead
from app.identity.service import audit_log, authentication, authorization, grant_administration
from app.models import (
    AuditEvent,
    Bank,
    BankReportingPeriod,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CapitalPlan,
    ContingencyFundingPlan,
    EnterpriseStressSignoff,
    IlaapSnapshot,
    IngestionBatch,
    Job,
    LineageRecord,
    MacroScenario,
    ManagementActionPlan,
    MappingConfigRecord,
    OperatorAuditLog,
    OperatorInspectorSession,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    RegulatoryRun,
    SavedScenarioAnalysis,
    SsoConnection,
    User,
)
from app.operator.deps import OperatorContext, record_operator_action
from app.operator.features.activity import get_tenant_activity
from app.operator.features.audit_log import read_audit_log
from app.operator.features.inspector_reads import (
    get_tenant_config,
    get_tenant_ingestion,
    get_tenant_ingestion_batch,
    get_tenant_metrics,
)
from app.operator.features.jobs import list_jobs
from app.operator.services import inspector, operator_views
from app.reporting.domain import workflow as filing_domain
from app.reporting.schemas.filing_workflow import (
    FilingWorkflowTemplateCreate,
    FilingWorkflowTemplateDecision,
    FilingWorkflowTemplateSubmit,
    FilingWorkflowTemplateUpdate,
)
from app.reporting.service import workflow
from app.reporting.service.filing_workflow import templates
from app.schemas.credit_params import CrmHaircutUpdate, EclAssumptionUpdate
from app.schemas.database_connection import DatabaseConnectionCreate
from app.schemas.ingestion import PositionSnapshotOverrideCreate
from app.schemas.liquidity_thresholds import LiquidityHaircutUpdate, LiquidityThresholdUpdate
from app.schemas.push import PushBatchOpen, PushRecordsPage
from app.schemas.regulatory_credit import ConcentrationLimitUpdate, CreditThresholdUpdate
from app.services import (
    credit_params,
    database_connections,
    ingestion,
    liquidity_thresholds,
    push_ingestion,
    regulatory_capital,
)
from app.services.audit import record_event
from tests.conftest import (
    ApiTestApp,
    ApiTestDatabase,
    rollback_sessionmaker_lifecycle,
    seed_demo_tenants,
)
from tests.db import test_postgres_migrations as migrations
from tests.support.database import database_connections as managed_database_connections
from tests.support.factories.authorization import grant_institution_authority
from tests.support.factories.outbound import stub_public_dns
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers
from tests.support.inmemory_storage import InMemoryStorageClient

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="Migrated PostgreSQL required"
)

BANK = "BK-AUDIT001"
SIBLING = "BK-AUDIT002"
FOREIGN = "BK-AUDIT003"
PATH = f"/api/v1/banks/{BANK}/audit-log"
NOW = datetime(2026, 10, 12, 10, tzinfo=UTC)
_SESSIONS: ContextVar[sessionmaker[Session]] = ContextVar("audit_fixture_sessions")


def _session() -> Session:
    return _SESSIONS.get()()


@pytest.fixture(scope="module")
def audit_database() -> Iterator[ApiTestDatabase]:
    """Use the production migration chain, including append-only audit custody."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("APP_ENV", "test")
        patch.setenv("TLS_ALLOW_PLAINTEXT", "1")
        schemas = migrations.migrate_isolated_postgres_schema(patch, downgrade=False)
        for schema in schemas:
            url = migrations.postgres_schema_url(
                migrations.postgres_administrator_url(), schema.schema_name
            )
            with managed_database_connections(url) as owner:
                engine: Engine = owner.engine()
                seed_demo_tenants(engine)
                yield ApiTestDatabase(database_url=url, engine=engine, seed=seed_demo_tenants)


@pytest.fixture(scope="module")
def audit_app() -> ApiTestApp:
    """Construct once, lazily under the first test's isolated settings."""
    return ApiTestApp()


@pytest.fixture
def db_client(
    audit_database: ApiTestDatabase, audit_app: ApiTestApp, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Seed as administrator; execute every HTTP database dependency as aqs_api."""
    with rollback_sessionmaker_lifecycle(audit_database, monkeypatch) as maker:
        app = audit_app.get()

        def runtime_db(request: Request, ctx: deps.Tenant) -> Iterator[Session]:
            for db in deps.get_tenant_db_session(request, ctx):
                db.execute(text("SET LOCAL ROLE aqs_api"))
                try:
                    yield db
                finally:
                    # Keep request commits inside the outer fixture rollback,
                    # and restore setup authority even after a failed request.
                    db.rollback()
                    db.execute(text("RESET ROLE"))
                    db.commit()

        previous_override = app.dependency_overrides.get(deps.get_tenant_db_session)
        app.dependency_overrides[deps.get_tenant_db_session] = runtime_db
        token = _SESSIONS.set(maker)
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client
        finally:
            _SESSIONS.reset(token)
            if previous_override is None:
                app.dependency_overrides.pop(deps.get_tenant_db_session, None)
            else:
                app.dependency_overrides[deps.get_tenant_db_session] = previous_override


@pytest.fixture(autouse=True)
def banks(db_client: TestClient) -> None:
    with _session() as db:
        for bank_id, organization in ((BANK, ORG_1), (SIBLING, ORG_1), (FOREIGN, ORG_2)):
            db.add(
                Bank(
                    id=bank_id,
                    organization_id=organization,
                    name="Synthetic audit bank",
                    short_name="Audit fixture",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type="universal_bank",
                )
            )
        db.commit()


def grant(
    bundle: RoleBundle,
    *,
    bank: str = BANK,
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
) -> None:
    with _session() as db:
        grant_institution_authority(
            db,
            organization_id=ORG_1,
            bank_id=bank,
            user_id=USER_1,
            bundle=bundle,
            module=ModuleScope.AUDIT,
            sensitivity=sensitivity,
        )
        db.commit()


def event(
    bank: str | None,
    *,
    action: str = "data.uploaded",
    when: datetime = NOW,
    outcome: str = "success",
    organization: str = ORG_1,
) -> str:
    with _session() as db:
        row = AuditEvent(
            id=uuid4(),
            organization_id=organization,
            actor_user_id=USER_1,
            event_type=action,
            entity_type="upload",
            entity_id=str(uuid4()),
            created_at=when,
            details={
                "bank_id": bank,
                "outcome": outcome,
                "source": "api",
                "before": {"state": "draft"},
                "after": {"state": "filed"},
            },
        )
        db.add(row)
        db.commit()
        return str(row.id)


@pytest.mark.parametrize(
    "bundle", [RoleBundle.SECURITY_OFFICER, RoleBundle.COMPLIANCE_OFFICER, RoleBundle.AUDITOR]
)
def test_officers_and_auditors_read_only_attributable_bank_events(
    db_client: TestClient, bundle: RoleBundle
) -> None:
    grant(bundle)
    own = event(BANK)
    event(SIBLING)
    event(FOREIGN, organization=ORG_2)
    event(None)
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert [str(row.id) for row in rows] == [own]
    assert rows[0].before == {"state": "draft"}
    assert rows[0].after == {"state": "filed"}
    assert rows[0].integrity == "verified"
    assert rows[0].sequence is not None
    with _session() as db:
        views = list(
            db.scalars(select(AuditEvent).where(AuditEvent.event_type == "audit_log.viewed"))
        )
        assert len(views) == 1
        assert views[0].details["bank_id"] == BANK
        assert views[0].actor_user_id == USER_1


@pytest.mark.parametrize(
    "bundle", [RoleBundle.VIEWER, RoleBundle.ANALYST, RoleBundle.APPROVER, RoleBundle.VALIDATOR]
)
def test_other_bundles_cannot_read_even_with_audit_view(
    db_client: TestClient, bundle: RoleBundle
) -> None:
    grant(bundle)
    assert db_client.get(PATH, headers=headers(ORG_1)).status_code == 403
    with _session() as db:
        assert (
            db.scalar(select(AuditEvent).where(AuditEvent.event_type == "audit_log.viewed")) is None
        )


def test_sibling_and_wrong_sensitivity_grants_do_not_authorize(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER, bank=SIBLING)
    grant(RoleBundle.COMPLIANCE_OFFICER, sensitivity=SensitivityScope.AGGREGATED)
    assert db_client.get(PATH, headers=headers(ORG_1)).status_code == 403
    assert (
        db_client.get(f"/api/v1/banks/{FOREIGN}/audit-log", headers=headers(ORG_1)).status_code
        == 404
    )
    assert (
        db_client.get("/api/v1/banks/BK-MISSING1/audit-log", headers=headers(ORG_1)).status_code
        == 404
    )


def test_filters_are_combined_and_end_is_exclusive(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    own = event(BANK)
    event(BANK, when=NOW + timedelta(hours=1))
    event(BANK, outcome="failure")
    event(BANK, action="access.changed")
    response = db_client.get(
        PATH,
        headers=headers(ORG_1),
        params={
            "start": NOW.isoformat(),
            "end": (NOW + timedelta(hours=1)).isoformat(),
            "actor": str(USER_1),
            "event_type": "data.uploaded",
            "outcome": "success",
        },
    )
    assert response.status_code == 200, response.text
    assert [str(row.id) for row in AuditLogRead.model_validate_json(response.text).events] == [own]


def test_cursor_pages_equal_timestamps_without_duplicates_and_is_bank_bound(
    db_client: TestClient,
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    grant(RoleBundle.SECURITY_OFFICER, bank=SIBLING)
    expected = {event(BANK) for _ in range(5)}
    found: list[str] = []
    cursor = None
    while True:
        params = {"limit": "2", "event_type": "data.uploaded"}
        if cursor:
            params["cursor"] = cursor
        response = db_client.get(PATH, headers=headers(ORG_1), params=params)
        assert response.status_code == 200, response.text
        page = AuditLogRead.model_validate_json(response.text)
        found.extend(str(row.id) for row in page.events)
        cursor = page.next_cursor
        if cursor is None:
            break
        assert (
            db_client.get(
                f"/api/v1/banks/{SIBLING}/audit-log",
                headers=headers(ORG_1),
                params={"cursor": cursor},
            ).status_code
            == 422
        )
    assert set(found) == expected
    assert len(found) == len(expected)


@pytest.mark.parametrize(
    "action", ["inspector.fix.recompute", "inspector.fix.official_run", "bi.backfill"]
)
@pytest.mark.parametrize("reason_field", ["reason", "note"])
def test_staff_is_labelled_and_private_staff_detail_is_not_exposed(
    db_client: TestClient, action: str, reason_field: str
) -> None:
    grant(RoleBundle.COMPLIANCE_OFFICER)
    with _session() as db:
        db.add_all(
            [
                OperatorAuditLog(
                    operator_email="staff@example.test",
                    auth_mode="dev",
                    action=action,
                    target_org=ORG_1,
                    created_at=NOW,
                    detail={
                        "bank_id": bank,
                        reason_field: "Investigate discrepancy",
                        "support_reference": "SUP-123",
                        "internal_note": "private operator context",
                    },
                )
                for bank in (BANK, SIBLING)
            ]
        )
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1), params={"actor": "staff@example.test"})
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert len(rows) == 1
    assert rows[0].actor_kind == "aequoros_staff"
    assert rows[0].reason == "Investigate discrepancy"
    assert rows[0].details["reason"] == "Investigate discrepancy"
    assert (rows[0].target_type, rows[0].target_id) == ("bank", BANK)
    assert rows[0].support_reference == "SUP-123"
    assert "internal_note" not in rows[0].details


@pytest.mark.parametrize(
    "params",
    [
        {"cursor": "!bad"},
        {"start": "2026-10-12T10:00:00"},
        {"start": NOW.isoformat(), "end": NOW.isoformat()},
        {"limit": "201"},
    ],
)
def test_invalid_ranges_and_cursors_are_rejected(
    db_client: TestClient, params: dict[str, str]
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    assert db_client.get(PATH, headers=headers(ORG_1), params=params).status_code == 422


def test_auditor_cannot_export_but_officer_can(db_client: TestClient) -> None:
    grant(RoleBundle.AUDITOR)
    ctx = TenantContext(ORG_1, actor_user_id=USER_1, authorization_version=1)
    with _session() as db:
        with pytest.raises(HTTPException) as denied:
            audit_log.require_access(db, ctx, BANK, Permission.EXPORT)
        assert denied.value.status_code == 403
    grant(RoleBundle.SECURITY_OFFICER)
    with _session() as db:
        assert audit_log.require_access(db, ctx, BANK, Permission.EXPORT).id == BANK


def test_conflicting_bank_attribution_is_not_visible(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    with _session() as db:
        db.add(
            AuditEvent(
                organization_id=ORG_1,
                event_type="conflicting.probe",
                entity_type="bank",
                entity_id=SIBLING,
                details={"bank_id": BANK},
                created_at=NOW,
            )
        )
        db.commit()
    response = db_client.get(
        PATH, headers=headers(ORG_1), params={"event_type": "conflicting.probe"}
    )
    assert response.status_code == 200, response.text
    assert AuditLogRead.model_validate_json(response.text).events == []


def test_unknown_outcome_can_be_filtered_without_implying_success(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    with _session() as db:
        db.add(
            AuditEvent(
                organization_id=ORG_1,
                event_type="legacy.probe",
                entity_type="bank",
                entity_id=BANK,
                details={},
                created_at=NOW,
            )
        )
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1), params={"outcome": "unspecified"})
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert len(rows) == 1 and rows[0].outcome == "unspecified"


@pytest.mark.parametrize("bundle", [RoleBundle.SECURITY_OFFICER, RoleBundle.COMPLIANCE_OFFICER])
@pytest.mark.parametrize("organization_wide", [False, True])
def test_officer_only_authority_discovers_covered_banks(
    db_client: TestClient, bundle: RoleBundle, organization_wide: bool
) -> None:
    user_id = uuid4()
    with _session() as db:
        user = User(id=user_id, organization_id=ORG_1, email="officer@example.test")
        db.add(user)
        db.flush()
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=user_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION
                if organization_wide
                else InstitutionScope.INSTITUTION,
                None if organization_wide else BANK,
                ModuleScope.AUDIT,
                SensitivityScope.RESTRICTED,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "audit-fixture"),
            reason="Explicit audit officer appointment",
            commit=False,
        )
        user.authorization_version = 1
        db.commit()
    auth = headers(ORG_1, user_id=user_id, roles=())
    expected = {BANK, SIBLING} if organization_wide else {BANK}
    profile = db_client.get("/api/v1/auth/me", headers=auth)
    assert profile.status_code == 200, profile.text
    projected = MeResponse.model_validate_json(
        profile.text
    ).effective_authority.institution_capabilities
    assert {item.institution_id for item in projected} == expected
    assert all(cap.module.value == "audit" for item in projected for cap in item.capabilities)
    listed = db_client.get("/api/v1/banks", headers=auth)
    assert listed.status_code == 200, listed.text
    assert {bank.id for bank in BankListRead.model_validate_json(listed.text).banks} == expected
    for bank in expected:
        assert db_client.get(f"/api/v1/banks/{bank}", headers=auth).status_code == 200
        assert db_client.get(f"/api/v1/banks/{bank}/audit-log", headers=auth).status_code == 200
    assert db_client.get(f"/api/v1/banks/{FOREIGN}", headers=auth).status_code == 404
    if not organization_wide:
        assert db_client.get(f"/api/v1/banks/{SIBLING}", headers=auth).status_code == 404


def _owned_subjects(
    db: Session, bank_id: str, organization: str = ORG_1
) -> tuple[IngestionBatch, RegulatoryPackage, RegulatoryRun]:
    period = BankReportingPeriod(
        organization_id=organization,
        bank_id=bank_id,
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
        label="September",
        status="open",
    )
    db.add(period)
    db.flush()
    batch = IngestionBatch(
        organization_id=organization,
        bank_id=bank_id,
        source_system="API_PUSH",
        adapter_version="1",
        extraction_mode="full",
        status="accepted",
        as_of_date=period.period_end,
    )
    package = RegulatoryPackage(
        organization_id=organization,
        bank_id=bank_id,
        return_family="capital",
        return_code="CAR-RWA",
        reporting_date=period.period_end,
        frequency="monthly",
        status="generated",
        version=1,
        generated_by=USER_1,
    )
    run = RegulatoryRun(
        organization_id=organization,
        bank_id=bank_id,
        reporting_period_id=period.id,
        module="capital",
        scenario_code="base",
        status="queued",
        engine_version="audit-fixture",
        input_schema_version="1",
        output_schema_version="1",
        input_hash="a" * 64,
        inputs={},
        created_by=USER_1,
    )
    db.add_all([batch, package, run])
    db.commit()
    return batch, package, run


def test_real_data_and_filing_writers_retain_bank_attribution(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    ctx = TenantContext(ORG_1, actor_user_id=USER_1, authorization_version=1)
    with _session() as db:
        batch, package, run = _owned_subjects(db, BANK)
        ingestion._record_batch_event(db, ctx, batch, "Accepted source data")  # pyright: ignore[reportPrivateUsage]
        batch.status = "failed"
        ingestion._record_batch_event(db, ctx, batch, "Source data failed")  # pyright: ignore[reportPrivateUsage]
        workflow.transition(db, ctx, package, "validated")
        workflow._add_approval(  # pyright: ignore[reportPrivateUsage]
            db, ctx, package, action="approved", actor_user_id=USER_1, reason="Reviewed return"
        )
        workflow.add_submission_event(db, ctx, package, channel="manual", event="submitted")
        db.commit()
        regulatory_capital._persist_failure(  # pyright: ignore[reportPrivateUsage]
            db, ctx, run.id, regulatory_capital.CapitalRunError("missing_parameter", "Unavailable")
        )
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert {row.action for row in rows} == {
        "ingestion_batch.accepted",
        "ingestion_batch.failed",
        "regulatory_package.status_changed",
        "regulatory_package.approval_approved",
        "regulatory_package.submission_submitted",
        "regulatory_run.failed",
    }
    assert all(row.details["bank_id"] == BANK and row.integrity == "verified" for row in rows)
    transition = next(row for row in rows if row.action == "regulatory_package.status_changed")
    assert transition.before == {"status": "generated"}
    assert transition.after == {"status": "validated"}
    assert transition.outcome == "success"
    response = db_client.get(PATH, headers=headers(ORG_1), params={"outcome": "failure"})
    failures = AuditLogRead.model_validate_json(response.text).events
    assert {row.action for row in failures} == {"regulatory_run.failed", "ingestion_batch.failed"}
    assert all(row.outcome == "failure" for row in failures)


def test_capital_success_writer_retains_bank_attribution(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    one = Decimal("1")
    rwa = RwaResult(one, one, one, Decimal("3"), one, one, one, one, 1, one, ())
    ratios = CapitalRatiosResult(
        cet1_capital=one,
        at1_capital=one,
        tier1_capital=one,
        tier2_capital=one,
        total_capital=one,
        general_provisions_amount=one,
        general_provisions_cap=one,
        gp_cap_applied=False,
        leverage_exposure=one,
        cet1_ratio_pct=one,
        tier1_ratio_pct=one,
        car_pct=one,
        leverage_ratio_pct=one,
        cet1_status="green",
        tier1_status="green",
        car_status="green",
        leverage_status="green",
        line_items=(),
    )
    params = CapitalParams({}, one, one, one, one, one, one, one, one, one, one)
    with _session() as db:
        _, _, run = _owned_subjects(db, BANK)
        regulatory_capital._persist_success(  # pyright: ignore[reportPrivateUsage]
            db, TenantContext(ORG_1, actor_user_id=USER_1), run, rwa, ratios, params, None, "GHS"
        )
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert [row.action for row in rows] == ["regulatory_run.succeeded"]
    assert rows[0].details["bank_id"] == BANK and rows[0].integrity == "verified"
    assert rows[0].outcome == "success"
    response = db_client.get(
        PATH,
        headers=headers(ORG_1),
        params={"outcome": "success", "event_type": "regulatory_run.succeeded"},
    )
    assert [row.id for row in AuditLogRead.model_validate_json(response.text).events] == [
        rows[0].id
    ]


def test_historical_subject_ownership_is_scoped_and_verified(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    expected: set[str] = set()
    with _session() as db:
        for bank, organization in ((BANK, ORG_1), (SIBLING, ORG_1), (FOREIGN, ORG_2)):
            batch, package, run = _owned_subjects(db, bank, organization)
            for kind, subject in (
                ("ingestion_batch", batch),
                ("regulatory_package", package),
                ("regulatory_run", run),
            ):
                row = AuditEvent(
                    organization_id=organization,
                    event_type=f"{kind}.historical",
                    entity_type=kind,
                    entity_id=str(subject.id),
                    details={},
                )
                db.add(row)
                db.flush()
                if bank == BANK:
                    expected.add(str(row.id))
            db.add(
                AuditEvent(
                    organization_id=organization,
                    event_type="conflicting.subject",
                    entity_type="ingestion_batch",
                    entity_id=str(batch.id),
                    details={"bank_id": SIBLING if bank == BANK else BANK},
                )
            )
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert {str(row.id) for row in rows} == expected
    assert all(row.integrity == "verified" and row.sequence for row in rows)
    with _session() as db:
        for row in rows:
            stored = db.get(AuditEvent, row.id)
            assert stored is not None and stored.details == {}


@pytest.mark.parametrize("kind", ["regulatory_package_artifact", "regulatory_artifact_version"])
def test_artifact_writers_and_historical_reads_use_package_ownership(
    db_client: TestClient, kind: str
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    with _session() as db:
        _, package, _ = _owned_subjects(db, BANK)
        model = (
            RegulatoryPackageArtifact
            if kind == "regulatory_package_artifact"
            else RegulatoryArtifactVersion
        )
        artifact = model(
            organization_id=ORG_1,
            package_id=package.id,
            kind="pdf",
            object_path="synthetic/return.pdf",
            checksum_sha256="b" * 64,
            size_bytes=1,
        )
        db.add(artifact)
        db.flush()
        recorded = record_event(
            db,
            TenantContext(ORG_1, actor_user_id=USER_1),
            event_type=f"{kind}.downloaded",
            entity_type=kind,
            entity_id=artifact.id,
        )
        assert recorded.details["bank_id"] == BANK
        db.add(
            AuditEvent(
                organization_id=ORG_1,
                event_type=f"{kind}.historical",
                entity_type=kind,
                entity_id=str(artifact.id),
                details={},
            )
        )
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert {row.action for row in rows} == {f"{kind}.downloaded", f"{kind}.historical"}
    assert all(row.integrity == "verified" for row in rows)


@pytest.mark.parametrize(
    "action",
    [
        "inspector.read.ingestion_batch",
        "inspector.fix.rerun_ingestion",
        "inspector.fix.redrive_dedup",
    ],
)
@pytest.mark.parametrize("historical", [False, True])
def test_staff_batch_actions_are_owned_projected_and_verified(
    db_client: TestClient, action: str, historical: bool
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    expected: set[str] = set()
    with _session() as db:
        for bank, organization in ((BANK, ORG_1), (SIBLING, ORG_1), (FOREIGN, ORG_2)):
            batch, _, _ = _owned_subjects(db, bank, organization)
            detail = {
                "batch_id": str(batch.id),
                "note": "Support review",
                "private_error": "secret",
            }
            if historical:
                entry = OperatorAuditLog(
                    operator_email="staff@example.test",
                    auth_mode="dev",
                    action=action,
                    target_org=organization,
                    detail=detail,
                )
                db.add(entry)
            else:
                entry = record_operator_action(
                    db,
                    OperatorContext("staff@example.test", "dev", "super_admin"),
                    action=action,
                    target_org=organization,
                    detail=detail,
                )
                assert entry.detail["bank_id"] == bank
            db.flush()
            if bank == BANK:
                expected.add(str(entry.id))
            db.add(
                OperatorAuditLog(
                    operator_email="staff@example.test",
                    auth_mode="dev",
                    action=action,
                    target_org=organization,
                    detail={
                        "batch_id": str(batch.id),
                        "bank_id": SIBLING if bank == BANK else BANK,
                    },
                )
            )
        db.add(
            OperatorAuditLog(
                operator_email="staff@example.test",
                auth_mode="dev",
                action=action,
                target_org=ORG_1,
                detail={"batch_id": "invalid-uuid"},
            )
        )
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert {str(row.id) for row in rows} == expected
    assert all(row.actor_kind == "aequoros_staff" and row.integrity == "verified" for row in rows)
    assert all(
        row.target_type == "ingestion_batch" and row.target_id == row.details["batch_id"]
        for row in rows
    )
    assert all(
        row.reason == "Support review" and "private_error" not in row.details for row in rows
    )


class _QueryPlan(BaseModel):
    index_condition: str = Field(default="", alias="Index Cond")
    plans: list["_QueryPlan"] = Field(default_factory=list, alias="Plans")


class _PlanEnvelope(BaseModel):
    plan: _QueryPlan = Field(alias="Plan")


def test_real_position_override_exposes_change_and_indexed_ownership(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    ctx = TenantContext(ORG_1, actor_user_id=USER_1)
    with _session() as db:
        batch, _, _ = _owned_subjects(db, BANK)
        lineage = LineageRecord(
            organization_id=ORG_1,
            bank_id=BANK,
            ingestion_batch_id=batch.id,
            operation_type="VALIDATION",
            operation_ref="audit-fixture",
        )
        db.add(lineage)
        db.flush()
        metadata = {
            "organization_id": ORG_1,
            "bank_id": BANK,
            "as_of_date": batch.as_of_date,
            "source_system": "API_PUSH",
            "source_reference": "audit-position",
            "ingestion_batch_id": batch.id,
            "lineage_id": lineage.id,
            "validation_status": "accepted",
        }
        position = CanonicalPosition(**metadata, position_type="LOAN", currency="GHS")
        db.add(position)
        db.flush()
        snapshot = CanonicalPositionSnapshot(
            **metadata, position_id=position.id, balance=Decimal("10")
        )
        db.add(snapshot)
        db.commit()
        updated = ingestion.override_position_snapshot(
            db,
            ctx,
            BANK,
            snapshot.id,
            PositionSnapshotOverrideCreate(
                field="balance", value="12", reason="Reviewed source correction"
            ),
        )
        query = "SELECT " + subject_bank_sql(
            ":org",
            "'canonical_position_snapshot'",
            ":id",
            postgresql=True,
            subject="canonical_position_snapshot",
        )
        parameters = {"org": ORG_1, "id": str(updated.id)}
        assert cast("str | None", db.scalar(text(query), parameters)) == BANK
        db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = TypeAdapter(list[_PlanEnvelope]).validate_python(
            cast("object", db.scalar(text("EXPLAIN (FORMAT JSON) " + query), parameters))
        )
        nodes = [plan[0].plan]
        indexed_id = False
        while nodes:
            node = nodes.pop()
            indexed_id |= "id =" in node.index_condition
            nodes.extend(node.plans)
        assert indexed_id, plan
        assert (
            cast("str | None", db.scalar(text(query), {"org": ORG_1, "id": "invalid-uuid"})) is None
        )
        assert (
            cast("str | None", db.scalar(text(query), {"org": ORG_2, "id": str(updated.id)}))
            is None
        )
    response = db_client.get(
        PATH,
        headers=headers(ORG_1),
        params={"event_type": "position_snapshot.overridden", "outcome": "success"},
    )
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert len(rows) == 1 and rows[0].integrity == "verified"
    assert isinstance(rows[0].before, dict) and isinstance(rows[0].after, dict)
    assert Decimal(str(rows[0].before["balance"])) == Decimal("10")
    assert Decimal(str(rows[0].after["balance"])) == Decimal("12")


def _authentication_subject(
    db: Session, claims: dict[str, object], password: str
) -> tuple[User, SsoConnection]:
    user = User(
        organization_id=ORG_1,
        email=str(claims["email"]),
        password_hash=security.hash_password(password),
        auth_provider="oidc",
        sso_subject=str(claims["sub"]),
    )
    db.add(user)
    db.flush()
    grant_institution_authority(
        db,
        organization_id=ORG_1,
        bank_id=BANK,
        user_id=user.id,
        bundle=RoleBundle.ANALYST,
    )
    connection = SsoConnection(
        organization_id=ORG_1,
        issuer=str(claims["iss"]),
        client_id=str(claims["aud"]),
        enabled=True,
    )
    db.add(connection)
    db.commit()
    return user, connection


@pytest.mark.parametrize("method", ["password", "sso", "password_step_up", "oidc_step_up"])
def test_real_authentication_success_and_refusal_are_bank_visible(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    password = "disposable-audit-password"
    settings = AuthSettings.model_validate(
        {"AUTH_JWT_SECRET": "audit-test-signing-secret-0000000000"}
    )
    claims: dict[str, object] = {
        "iss": "https://audit-idp.example.test",
        "aud": "audit-client",
        "sub": "audit-subject",
        "email": "audit-user@example.test",
        "email_verified": True,
        "auth_time": int(datetime.now(UTC).timestamp()),
        "amr": ["pwd", "mfa"],
    }

    def hints(_token: str) -> dict[str, object]:
        return claims

    def verified(_token: str, *, issuer: str, audience: str) -> dict[str, object]:
        assert issuer == claims["iss"] and audience == claims["aud"]
        return claims

    monkeypatch.setattr(security, "unverified_claims", hints)
    monkeypatch.setattr(security, "verify_oidc_id_token", verified)
    with _session() as db:
        user, connection = _authentication_subject(db, claims, password)
        user_id = user.id
        ctx = TenantContext(ORG_1, actor_user_id=user_id, authorization_version=1)
        if method == "password":
            assert authentication.login_with_password(
                db, email=user.email, password=password, settings=settings
            ).access_token
        elif method == "sso":
            assert authentication.login_with_sso(
                db, id_token="boundary-token", settings=settings
            ).access_token
        else:
            evidence = stepup.verify_step_up(
                db,
                ctx,
                user_id,
                password=password if method == "password_step_up" else None,
                id_token="boundary-token" if method == "oidc_step_up" else None,
                request_metadata={"amr": ["mfa"]} if method == "password_step_up" else None,
            )
            assert evidence["method"] in {"password_reauth", "oidc_reauth"}
        with pytest.raises(HTTPException):
            if method == "password":
                authentication.login_with_password(
                    db, email=user.email, password="incorrect", settings=settings
                )
            elif method == "sso":
                connection.allowed_email_domains = ["forbidden.example"]
                db.commit()
                authentication.login_with_sso(db, id_token="boundary-token", settings=settings)
            else:
                if method == "oidc_step_up":
                    claims["auth_time"] = 1
                stepup.verify_step_up(
                    db,
                    ctx,
                    user_id,
                    password="incorrect" if method == "password_step_up" else None,
                    id_token="boundary-token" if method == "oidc_step_up" else None,
                )
        db.rollback()
    response = db_client.get(PATH, headers=headers(ORG_1), params={"actor": str(user_id)})
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    actions = {f"authentication.{method}.succeeded", f"authentication.{method}.failed"}
    if method in {"sso", "oidc_step_up"}:
        actions.add("authentication.mfa.succeeded")
    assert {row.action for row in rows} == actions
    assert all(row.integrity == "verified" and row.details["bank_id"] == BANK for row in rows)
    assert all(row.source == "authentication" and row.target_id == str(user_id) for row in rows)
    assert {row.outcome for row in rows} == {"success", "failure"}
    with _session() as db:
        retained = list(db.scalars(select(AuditEvent).where(AuditEvent.actor_user_id == user_id)))
        assert all(row.details["bank_id"] == BANK for row in retained)
        assert all(
            "password" not in row.details and "id_token" not in row.details for row in retained
        )
    response = db_client.get(
        PATH, headers=headers(ORG_1), params={"actor": str(user_id), "outcome": "failure"}
    )
    assert [row.action for row in AuditLogRead.model_validate_json(response.text).events] == [
        f"authentication.{method}.failed"
    ]


def test_unknown_authentication_refusals_remain_platform_only(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    with _session() as db:
        with pytest.raises(HTTPException) as refusal:
            authentication.login_with_password(
                db, email="unknown@example.test", password="incorrect"
            )
        assert refusal.value.status_code == 401
        db.rollback()
        row = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "authentication.password.failed")
        )
        assert row is not None and row.organization_id is None and row.actor_user_id is None
        assert "bank_id" not in row.details
    response = db_client.get(
        PATH, headers=headers(ORG_1), params={"event_type": "authentication.password.failed"}
    )
    assert AuditLogRead.model_validate_json(response.text).events == []


def test_historical_production_shapes_normalize_without_changing_seals(
    db_client: TestClient,
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    evidence: dict[str, dict[str, JsonValue]] = {
        "regulatory_run.failed": {},
        "ingestion_batch.failed": {},
        "regulatory_package.status_changed": {"from_status": "generated", "to_status": "validated"},
        "position_snapshot.overridden": {
            "field": "ifrs9_stage",
            "original_value": "1",
            "new_value": "2",
        },
        "data.unknown": {},
        "regulatory_run.succeeded": {"outcome": "refused"},
    }
    with _session() as db:
        for action, detail in evidence.items():
            db.add(
                AuditEvent(
                    organization_id=ORG_1,
                    event_type=action,
                    entity_type="bank",
                    entity_id=BANK,
                    details={"bank_id": BANK, **detail},
                )
            )
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = {row.action: row for row in AuditLogRead.model_validate_json(response.text).events}
    assert rows["regulatory_package.status_changed"].before == {"status": "generated"}
    assert rows["regulatory_package.status_changed"].after == {"status": "validated"}
    assert rows["position_snapshot.overridden"].before == {"ifrs9_stage": "1"}
    assert rows["position_snapshot.overridden"].after == {"ifrs9_stage": "2"}
    assert rows["data.unknown"].outcome == "unspecified"
    assert rows["regulatory_run.succeeded"].outcome == "refused"
    assert all(row.integrity == "verified" for row in rows.values())
    response = db_client.get(PATH, headers=headers(ORG_1), params={"outcome": "failure"})
    assert {row.action for row in AuditLogRead.model_validate_json(response.text).events} == {
        "regulatory_run.failed",
        "ingestion_batch.failed",
    }
    with _session() as db:
        for action, row in rows.items():
            stored = db.get(AuditEvent, row.id)
            assert stored is not None and stored.details == {"bank_id": BANK, **evidence[action]}


def test_real_connection_and_template_writers_are_visible(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", "audit-fixture-vault-key")
    get_settings.cache_clear()
    stub_public_dns(monkeypatch, "core-db.example.test")
    ctx = TenantContext(ORG_1, actor_user_id=USER_1, authorization_version=1)
    with _session() as db:
        connection = database_connections.create_connection(
            db,
            ctx,
            BANK,
            DatabaseConnectionCreate.model_validate(
                {
                    "backend": "sqlserver",
                    "display_name": "Audit core connection",
                    "host": "core-db.example.test",
                    "port": 1433,
                    "database": "audit",
                    "schemas": ["DBO"],
                    "credentials": {"username": "fixture", "password": "disposable-secret"},
                    "extraction_spec": {
                        "tables": [{"table": "DBO.GL_ACCOUNTS", "record_kind": "gl_account"}],
                        "default_mode": "full",
                    },
                }
            ),
        )
        database_connections.revoke_connection(db, ctx, BANK, connection.id)
        bank = db.get(Bank, BANK)
        assert bank is not None
        stages = templates.stage_inputs(filing_domain.default_stages())
        proposed = templates.propose_template(
            db,
            ctx,
            bank,
            FilingWorkflowTemplateCreate(stages=stages, reason="Propose audit review chain"),
        )
        templates.update_template(
            db,
            ctx,
            bank,
            proposed.id,
            FilingWorkflowTemplateUpdate(stages=stages, reason="Review audit filing chain"),
        )
        templates.submit_template(
            db,
            ctx,
            bank,
            proposed.id,
            FilingWorkflowTemplateSubmit(reason="Submit reviewed audit chain"),
        )
        checker = User(organization_id=ORG_1, email="audit-checker@example.test")
        db.add(checker)
        db.flush()
        templates.decide_template(
            db,
            TenantContext(ORG_1, actor_user_id=checker.id),
            bank,
            proposed.id,
            FilingWorkflowTemplateDecision(
                decision="approved", reason="Approve reviewed audit chain"
            ),
        )
        stored = list(db.scalars(select(AuditEvent)))
        assert all(row.details["bank_id"] == BANK for row in stored)
        for row in stored:
            db.add(
                AuditEvent(
                    organization_id=ORG_1,
                    event_type=row.event_type + ".historical",
                    entity_type=row.entity_type,
                    entity_id=row.entity_id,
                    details={},
                )
            )
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert len(rows) == 12
    assert all(row.integrity == "verified" for row in rows)
    assert {row.action for row in rows} >= {
        "database_direct_connection.created",
        "database_direct_connection.revoked",
        "filing_workflow_template.updated.historical",
    }
    get_settings.cache_clear()


def test_real_icaap_commit_and_retained_cycle_ownership(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    with _session() as db:
        bank = db.get(Bank, BANK)
        assert bank is not None
        access = IcaapAccess(
            ctx=TenantContext(ORG_1, actor_user_id=USER_1, authorization_version=1), bank=bank
        )
        cycle = cycles.create_cycle(
            db,
            access,
            IcaapCycleCreate.model_validate(
                {
                    "fiscal_year": 2025,
                    "cycle_kind": "rehearsal",
                    "basis": "solo",
                    "framework_code": "bog_icaap",
                    "framework_version": "2026.02-ed.1",
                    "reason": "Audit evidence rehearsal",
                }
            ),
        )
        sections.save_working(
            db,
            access,
            cycle.id,
            "executive_summary",
            IcaapSectionWorkingSave(
                base_rev=0,
                doc={
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Audit evidence"}],
                        }
                    ],
                },
            ),
        )
        committed = sections.commit_version(
            db,
            access,
            cycle.id,
            "executive_summary",
            IcaapSectionCommit(base_rev=1, note="Reviewed audit section"),
        )
        db.add(
            AuditEvent(
                organization_id=ORG_1,
                event_type="icaap.section.historical",
                entity_type="icaap_section_version",
                entity_id=str(committed.id),
                details={},
            )
        )
        db.add(
            AuditEvent(
                organization_id=ORG_1,
                event_type="icaap.reconciliation.resources_line_deleted",
                entity_type="icaap_resources_reconciliation_line",
                entity_id=str(uuid4()),
                details={"cycle_id": str(cycle.id)},
            )
        )
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert {row.action for row in rows} >= {
        "icaap.cycle.created",
        "icaap.section.version_committed",
        "icaap.section.historical",
        "icaap.reconciliation.resources_line_deleted",
    }
    assert all(row.integrity == "verified" for row in rows)


def test_real_push_staging_snapshots_resolved_bank(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    storage = InMemoryStorageClient()
    ctx = TenantContext(ORG_1, actor_user_id=USER_1)
    with _session() as db:
        opened = push_ingestion.open_push_batch(
            db,
            ctx,
            BANK,
            PushBatchOpen(
                as_of_date=date(2026, 9, 30),
                idempotency_key="audit-push",
                reason="Audit source transfer",
            ),
            storage,
        )
        push_ingestion.stage_push_records(
            db,
            ctx,
            BANK,
            opened.push_batch_id,
            PushRecordsPage(
                entities={
                    "gl_account": [
                        {
                            "source_reference": "1000",
                            "account_code": "1000",
                            "name": "Cash",
                            "account_class": "ASSET",
                            "currency": "GHS",
                        }
                    ]
                }
            ),
            storage,
        )
        push_ingestion.commit_push_batch(db, ctx, BANK, opened.push_batch_id, storage)
        batch = db.scalar(select(IngestionBatch).where(IngestionBatch.bank_id == BANK))
        assert batch is not None
        legacy = str(uuid4())
        for action, detail in (
            ("push_batch.opened", {}),
            ("push_batch.records_staged", {}),
            ("push_batch.committed", {"ingestion_batch_id": str(batch.id)}),
        ):
            db.add(
                AuditEvent(
                    organization_id=ORG_1,
                    event_type=action,
                    entity_type="push_batch",
                    entity_id=legacy,
                    details=detail,
                )
            )
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert len([row for row in rows if row.target_type == "push_batch"]) == 6
    assert all(row.integrity == "verified" for row in rows)


@pytest.mark.parametrize("historical", [False, True])
def test_real_staff_batch_read_uses_session_evidence(
    db_client: TestClient, historical: bool
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    with _session() as db:
        batch, _, _ = _owned_subjects(db, BANK)
        operator = OperatorContext("audit-staff@example.test", "dev", "super_admin")
        session = inspector.start_session(
            db,
            organization_id=ORG_1,
            started_by=operator.email,
            reason="Diagnose bank upload",
            support_reference="SUP-494",
            mode="consent",
            ttl_minutes=30,
        )
        db.commit()
        if historical:
            db.add(
                OperatorAuditLog(
                    operator_email=operator.email,
                    auth_mode="dev",
                    action="inspector.read.ingestion_batch",
                    target_org=ORG_1,
                    detail={"session_id": str(session.id), "batch_id": str(batch.id)},
                )
            )
            db.commit()
        else:
            get_tenant_ingestion_batch(ORG_1, batch.id, db, operator)
        retained = db.scalar(
            select(OperatorAuditLog).where(
                OperatorAuditLog.action == "inspector.read.ingestion_batch"
            )
        )
        assert retained is not None
        event_id = retained.id
        if not historical:
            assert (
                retained.detail["reason"] == session.reason
                and retained.detail["support_reference"] == "SUP-494"
            )
        assert db.get(OperatorInspectorSession, session.id) is not None
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert len(rows) == 1
    assert rows[0].id == event_id and rows[0].integrity == "verified"
    assert rows[0].reason == "Diagnose bank upload" and rows[0].support_reference == "SUP-494"


def test_governed_subjects_share_write_read_and_verification_ownership(
    db_client: TestClient,
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    expected: set[str] = set()
    with _session() as db:
        for bank_id, org in ((BANK, ORG_1), (SIBLING, ORG_1), (FOREIGN, ORG_2)):
            _, _, run = _owned_subjects(db, bank_id, org)
            subjects = [
                (
                    "capital_plan",
                    CapitalPlan(organization_id=org, bank_id=bank_id, version=1, content={}),
                ),
                (
                    "ilaap_snapshot",
                    IlaapSnapshot(
                        organization_id=org,
                        bank_id=bank_id,
                        reporting_period_id=run.reporting_period_id,
                        as_of_date=date(2026, 9, 30),
                        content={},
                        adequate=False,
                    ),
                ),
                (
                    "contingency_funding_plan",
                    ContingencyFundingPlan(
                        organization_id=org, bank_id=bank_id, version=1, content={}
                    ),
                ),
                (
                    "macro_scenario",
                    MacroScenario(
                        organization_id=org,
                        bank_id=bank_id,
                        code="audit",
                        name="Audit scenario",
                        scenario_type="base",
                    ),
                ),
                (
                    "management_action_plan",
                    ManagementActionPlan(
                        organization_id=org, bank_id=bank_id, code="audit", name="Audit action plan"
                    ),
                ),
                (
                    "scenario_analysis",
                    SavedScenarioAnalysis(
                        organization_id=org,
                        bank_id=bank_id,
                        reporting_period_id=run.reporting_period_id,
                        module="capital",
                        name="Audit analysis",
                        engine_version="fixture",
                        scenarios=[],
                        results=[],
                    ),
                ),
                (
                    "enterprise_stress_signoff",
                    EnterpriseStressSignoff(
                        organization_id=org,
                        bank_id=bank_id,
                        run_id=run.id,
                        reporting_period_id=run.reporting_period_id,
                        scenario_code="base",
                        scenario_narrative="Audit narrative",
                        assumptions_rationale="Audit assumptions",
                    ),
                ),
            ]
            for kind, subject in subjects:
                db.add(subject)
                db.flush()
                written = record_event(
                    db,
                    TenantContext(org),
                    event_type=kind + ".changed",
                    entity_type=kind,
                    entity_id=subject.id,
                )
                assert written.details["bank_id"] == bank_id
                db.flush()
                historical = AuditEvent(
                    organization_id=org,
                    event_type=kind + ".historical",
                    entity_type=kind,
                    entity_id=str(subject.id),
                    details={},
                )
                db.add(historical)
                db.flush()
                if bank_id == BANK:
                    expected.update((str(written.id), str(historical.id)))
                elif bank_id == SIBLING:
                    db.add(
                        AuditEvent(
                            organization_id=ORG_1,
                            event_type=kind + ".conflicting",
                            entity_type=kind,
                            entity_id=str(subject.id),
                            details={"bank_id": BANK},
                        )
                    )
            parameter = record_event(
                db,
                TenantContext(org),
                event_type="parameter.changed",
                entity_type="param_ecl_assumption",
                entity_id=bank_id,
            )
            assert parameter.details["bank_id"] == bank_id
            db.flush()
            if org == ORG_1:
                expected.add(str(parameter.id))
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert {str(row.id) for row in rows} == expected
    assert all(row.integrity == "verified" for row in rows)


def test_historical_access_request_institution_marker_is_consistent(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    with _session() as db:
        ctx = TenantContext(ORG_1, actor_user_id=USER_1)
        written = record_event(
            db,
            ctx,
            event_type="authorization.access_requested",
            entity_type="authorization_access_request",
            entity_id=uuid4(),
            details={"institution_id": BANK},
        )
        assert written.details["bank_id"] == BANK
        for institution, scope in ((BANK, BANK), (BANK, SIBLING), (SIBLING, BANK)):
            db.add(
                AuditEvent(
                    organization_id=ORG_1,
                    event_type="authorization.access_request_rejected",
                    entity_type="authorization_access_request",
                    entity_id=str(uuid4()),
                    details={"institution_id": institution, "scope": {"institution_id": scope}},
                )
            )
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert len(rows) == 2
    assert all(row.integrity == "verified" for row in rows)


@pytest.mark.parametrize("mismatch", ["organization", "operator"])
def test_historical_staff_session_evidence_does_not_cross_ownership(
    db_client: TestClient, mismatch: str
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    with _session() as db:
        session = inspector.start_session(
            db,
            organization_id=ORG_2 if mismatch == "organization" else ORG_1,
            started_by="other-staff@example.test"
            if mismatch == "operator"
            else "audit-staff@example.test",
            reason="Private incident explanation",
            support_reference="SUP-PRIVATE",
            mode="consent",
            ttl_minutes=30,
        )
        db.add(
            OperatorAuditLog(
                operator_email="audit-staff@example.test",
                auth_mode="dev",
                action="inspector.read.ingestion_batch",
                target_org=ORG_1,
                detail={"bank_id": BANK, "session_id": str(session.id)},
            )
        )
        db.commit()
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert len(rows) == 1 and rows[0].integrity == "verified"
    assert rows[0].reason is None and rows[0].support_reference is None


@pytest.mark.parametrize("operation", ["metrics", "ingestion", "config"])
def test_actual_inspector_resources_are_separated_per_bank(
    db_client: TestClient, operation: str
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    operator = OperatorContext("catalogue@example.test", "dev", "super_admin")
    with _session() as db:
        for bank_id in (BANK, SIBLING):
            _owned_subjects(db, bank_id)
        inspector.start_session(
            db,
            organization_id=ORG_1,
            started_by=operator.email,
            reason="Review returned resources",
            support_reference="SUP-CATALOGUE",
            mode="consent",
            ttl_minutes=30,
        )
        db.commit()
        if operation == "metrics":
            get_tenant_metrics(ORG_1, db, operator)
        elif operation == "ingestion":
            get_tenant_ingestion(ORG_1, db, operator, limit=1)
        else:
            get_tenant_config(ORG_1, db, operator)
        recorded = list(
            db.scalars(
                select(OperatorAuditLog).where(
                    OperatorAuditLog.action == f"inspector.read.{operation}"
                )
            )
        )
        assert len(recorded) == (1 if operation == "ingestion" else 2)
        expected = {row.id for row in recorded if row.detail["bank_id"] == BANK}
        assert all(row.detail["support_reference"] == "SUP-CATALOGUE" for row in recorded)
    response = db_client.get(
        PATH, headers=headers(ORG_1), params={"event_type": f"inspector.read.{operation}"}
    )
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert {row.id for row in rows} == expected
    assert all(
        row.integrity == "verified" and row.reason == "Review returned resources" for row in rows
    )


def test_fleet_job_page_auto_records_only_returned_banks(db_client: TestClient) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    operator = OperatorContext("fleet@example.test", "dev", "super_admin")
    with _session() as db:
        for bank_id, org in ((BANK, ORG_1), (SIBLING, ORG_1), (FOREIGN, ORG_2)):
            db.add(
                Job(
                    organization_id=org,
                    bank_id=bank_id,
                    job_type="pipeline_refresh",
                    status="queued",
                    payload={},
                )
            )
        db.commit()
        result = list_jobs(db, operator, limit=1, status=None)
        assert len(result.jobs) == 1
        recorded = list(
            db.scalars(select(OperatorAuditLog).where(OperatorAuditLog.action == "jobs.list"))
        )
        assert len(recorded) == 1
        assert recorded[0].detail["bank_id"] == result.jobs[0].bank_id
        assert recorded[0].target_org == result.jobs[0].organization_id
        assert recorded[0].detail["reason"] == "jobs list"
        assert recorded[0].detail["support_reference"] is None
        expected: set[UUID] = {recorded[0].id} if result.jobs[0].bank_id == BANK else set()
    response = db_client.get(PATH, headers=headers(ORG_1), params={"event_type": "jobs.list"})
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert {row.id for row in rows} == expected
    assert all(row.integrity == "verified" for row in rows)


@pytest.mark.parametrize("historical", [False, True])
def test_mapping_change_ownership_and_change_evidence(
    db_client: TestClient, historical: bool
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    with _session() as db:
        mapping = MappingConfigRecord(
            organization_id=ORG_1,
            bank_id=BANK,
            source_system="API_PUSH",
            source_ref="catalogue",
            version=1,
            name="Catalogue",
            status="active",
            config={},
        )
        db.add(mapping)
        db.flush()
        detail: dict[str, JsonValue] = {
            "kind": "mapping_active",
            "target_id": str(mapping.id),
            "before": {"status": "inactive"},
            "after": {"status": "active", "bank_id": BANK},
            "note": "Correct mapping",
        }
        if historical:
            event = OperatorAuditLog(
                operator_email="mapping@example.test",
                auth_mode="dev",
                action="inspector.fix.config",
                target_org=ORG_1,
                detail=detail,
            )
            db.add(event)
        else:
            event = record_operator_action(
                db,
                OperatorContext("mapping@example.test", "dev", "super_admin"),
                action="inspector.fix.config",
                target_org=ORG_1,
                detail=detail,
            )
        db.commit()
        event_id = event.id
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert len(rows) == 1 and rows[0].id == event_id
    assert rows[0].target_type == "mapping_config" and rows[0].target_id == str(mapping.id)
    assert rows[0].before == {"status": "inactive"} and rows[0].after == {
        "status": "active",
        "bank_id": BANK,
    }
    assert rows[0].integrity == "verified"


@pytest.mark.parametrize("surface", ["activity", "audit"])
def test_staff_feed_reads_resolve_retained_bank_subjects(
    db_client: TestClient, surface: str
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    operator = OperatorContext("feed@example.test", "dev", "super_admin")
    with _session() as db:
        batch, _, _ = _owned_subjects(db, BANK)
        inspector.start_session(
            db,
            organization_id=ORG_1,
            started_by=operator.email,
            reason="Review retained bank evidence",
            support_reference="SUP-FEEDS",
            mode="consent",
            ttl_minutes=30,
        )
        db.add(
            AuditEvent(
                organization_id=ORG_1,
                actor_user_id=USER_1,
                event_type="ingestion_batch.accepted",
                entity_type="ingestion_batch",
                entity_id=str(batch.id),
                details={},
            )
        )
        db.add(
            OperatorAuditLog(
                operator_email=operator.email,
                auth_mode="dev",
                action="inspector.read.ingestion_batch",
                target_org=ORG_1,
                detail={"batch_id": str(batch.id)},
            )
        )
        db.commit()
        if surface == "activity":
            get_tenant_activity(ORG_1, db, operator, limit=100)
            action = "inspector.read.activity"
        else:
            read_audit_log(
                db,
                operator,
                operator_email=operator.email,
                target_org=None,
                action=None,
                from_=None,
                to=None,
                limit=100,
            )
            action = "audit.read"
        recorded = list(
            db.scalars(select(OperatorAuditLog).where(OperatorAuditLog.action == action))
        )
        assert len(recorded) == 1
        assert recorded[0].detail["bank_id"] == BANK and recorded[0].target_org == ORG_1
        expected_id = recorded[0].id
    response = db_client.get(PATH, headers=headers(ORG_1), params={"event_type": action})
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert len(rows) == 1 and rows[0].id == expected_id and rows[0].integrity == "verified"
    if surface == "activity":
        assert (
            rows[0].support_reference == "SUP-FEEDS"
            and rows[0].reason == "Review retained bank evidence"
        )
    else:
        assert rows[0].support_reference is None and rows[0].reason == "audit read"


def test_organization_binding_grants_and_removals_cover_each_tenant_bank(
    db_client: TestClient,
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    grant(RoleBundle.SECURITY_OFFICER, bank=SIBLING)
    operator = OperatorContext("binding-review@example.test", "dev", "super_admin")
    expected: dict[str, set[UUID]] = {}
    with _session() as db:
        for org in (ORG_1, ORG_2):
            principal = User(
                id=uuid4(), organization_id=org, email=f"org-officer-{org}@example.test"
            )
            db.add(principal)
            db.flush()
            grantor = authorization.GrantorRef(GrantorType.SYSTEM, "audit-fixture")
            binding = authorization.create_role_binding(
                db,
                organization_id=org,
                principal_user_id=principal.id,
                principal_type=PrincipalType.HUMAN,
                role_bundle=RoleBundle.SECURITY_OFFICER,
                scope=authorization.BindingScope(
                    InstitutionScope.ORGANIZATION,
                    None,
                    ModuleScope.AUDIT,
                    SensitivityScope.RESTRICTED,
                ),
                grantor=grantor,
                reason="Organization-wide security coverage",
                commit=False,
            )
            authorization.record_binding_grant_audit(
                db, binding=binding, grantor=grantor, authority_sentence="Organization-wide audit"
            )
            grant_administration.revoke_scoped_grant(
                db,
                organization_id=org,
                binding_id=binding.id,
                actor_user_id=USER_1 if org == ORG_1 else principal.id,
                reason="End organization-wide appointment",
                commit=False,
            )
            db.flush()
            expected[org] = set(
                db.scalars(
                    select(AuditEvent.id).where(
                        AuditEvent.organization_id == org,
                        AuditEvent.entity_id == str(binding.id),
                    )
                )
            )
            if org == ORG_1:
                inspection = inspector.start_session(
                    db,
                    organization_id=org,
                    started_by=operator.email,
                    reason="Review organization-wide access",
                    support_reference="SUP-ORG-GRANT",
                    mode="consent",
                    ttl_minutes=30,
                )
                db.add(
                    AuditEvent(
                        organization_id=org,
                        entity_type="authorization_binding",
                        entity_id=str(binding.id),
                        event_type="authorization.binding_probe",
                        details={
                            "act_operator": operator.email,
                            "impersonation_session_id": str(inspection.id),
                        },
                    )
                )
        db.commit()
        activity = operator_views.get_tenant_activity(db, ORG_1, 100)
        for action in ("authorization.binding_granted", "authorization.binding_revoked"):
            assert {item.bank_id for item in activity.items if item.summary.startswith(action)} == {
                BANK,
                SIBLING,
            }
    for bank in (BANK, SIBLING):
        found: set[UUID] = set()
        for action in ("authorization.binding_granted", "authorization.binding_revoked"):
            response = db_client.get(
                f"/api/v1/banks/{bank}/audit-log",
                headers=headers(ORG_1),
                params={"event_type": action},
            )
            assert response.status_code == 200, response.text
            rows = AuditLogRead.model_validate_json(response.text).events
            found.update(row.id for row in rows)
            assert all(row.integrity == "verified" and row.sequence for row in rows)
        assert found == expected[ORG_1] and found.isdisjoint(expected[ORG_2])
        response = db_client.get(
            f"/api/v1/banks/{bank}/audit-log",
            headers=headers(ORG_1),
            params={"event_type": "authorization.binding_probe"},
        )
        assert response.status_code == 200, response.text
        rows = AuditLogRead.model_validate_json(response.text).events
        assert len(rows) == 1 and rows[0].integrity == "verified"
        assert rows[0].reason == "Review organization-wide access"
        assert rows[0].support_reference == "SUP-ORG-GRANT"


@pytest.mark.parametrize(
    "action",
    ["desk.determination.publish", "desk.curve.publish", "desk.operating_environment.publish"],
)
@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        ("complete", "success"),
        ("failed", "failure"),
        ("skipped", "skipped"),
        ("unknown", "unspecified"),
        (None, "unspecified"),
    ],
)
def test_publication_audit_outcome_matches_each_bank_delivery(
    db_client: TestClient, action: str, status: str | None, outcome: str
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    grant(RoleBundle.SECURITY_OFFICER, bank=SIBLING)
    with _session() as db:
        resources: list[dict[str, JsonValue]] = [
            {"bank_id": BANK, "status": status},
            {"bank_id": SIBLING, "status": "complete"},
            {"bank_id": FOREIGN, "status": "failed"},
        ]
        record_operator_action(
            db,
            OperatorContext("publisher@example.test", "dev", "super_admin"),
            action=action,
            resources=resources,
            detail={"status": "partial"},
        )
        db.commit()
        recorded = list(
            db.scalars(select(OperatorAuditLog).where(OperatorAuditLog.action == action))
        )
        assert len(recorded) == 3
        own = next(row for row in recorded if row.detail["bank_id"] == BANK)
        assert own.detail["status"] == status and own.detail["outcome"] == outcome
    for bank, expected_outcome in ((BANK, outcome), (SIBLING, "success")):
        response = db_client.get(
            f"/api/v1/banks/{bank}/audit-log",
            headers=headers(ORG_1),
            params={"event_type": action, "outcome": expected_outcome},
        )
        assert response.status_code == 200, response.text
        rows = AuditLogRead.model_validate_json(response.text).events
        assert len(rows) == 1 and rows[0].integrity == "verified"
        assert rows[0].outcome == expected_outcome and rows[0].target_id == bank
    if outcome != "success":
        response = db_client.get(
            PATH, headers=headers(ORG_1), params={"event_type": action, "outcome": "success"}
        )
        assert response.status_code == 200, response.text
        assert AuditLogRead.model_validate_json(response.text).events == []


@pytest.mark.parametrize("evidence_kind", ["automatic", "caller", "session"])
def test_only_explicit_session_id_attaches_inspection_evidence(
    db_client: TestClient, evidence_kind: str
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    operator = OperatorContext("purpose@example.test", "dev", "super_admin")
    with _session() as db:
        inspection = inspector.start_session(
            db,
            organization_id=ORG_1,
            started_by=operator.email,
            reason="Investigate unrelated upload",
            support_reference="SUP-UPLOAD",
            mode="consent",
            ttl_minutes=30,
        )
        db.commit()
        detail: dict[str, JsonValue] = {}
        reason = "regulatory parameter approve"
        reference = None
        if evidence_kind == "caller":
            detail = {"reason": "Approve reviewed policy", "support_reference": "POLICY-42"}
            reason, reference = "Approve reviewed policy", "POLICY-42"
        elif evidence_kind == "session":
            detail = {"session_id": str(inspection.id)}
            reason, reference = inspection.reason, inspection.support_reference
        record_operator_action(
            db,
            operator,
            action="regulatory_parameter.approve",
            target_org=ORG_1,
            resources=[{"bank_id": BANK}, {"bank_id": SIBLING}],
            detail=detail,
        )
        db.commit()
        recorded = list(
            db.scalars(
                select(OperatorAuditLog).where(
                    OperatorAuditLog.action == "regulatory_parameter.approve"
                )
            )
        )
        assert len(recorded) == 2
        assert all(row.detail["reason"] == reason for row in recorded)
        assert all(row.detail["support_reference"] == reference for row in recorded)
        assert all(("session_id" in row.detail) == (evidence_kind == "session") for row in recorded)
    response = db_client.get(
        PATH, headers=headers(ORG_1), params={"event_type": "regulatory_parameter.approve"}
    )
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert len(rows) == 1 and rows[0].integrity == "verified"
    assert rows[0].reason == reason and rows[0].support_reference == reference


def test_repeated_resource_banks_are_verified_in_one_batch(db_client: TestClient) -> None:
    with _session() as db:
        bank_queries = 0

        def count_bank_queries(state: ORMExecuteState) -> None:
            nonlocal bank_queries
            statement = state.statement
            if isinstance(statement, Select) and any(
                column.get("entity") is Bank
                for column in cast(list[dict[str, object]], statement.column_descriptions)
            ):
                bank_queries += 1

        resources: list[dict[str, JsonValue]] = [
            {"bank_id": bank} for _ in range(100) for bank in (BANK, SIBLING, FOREIGN)
        ]
        resources.extend(
            [
                {"organization_id": ORG_2, "bank_id": BANK},
                {"organization_id": ORG_1, "bank_id": FOREIGN},
                {"bank_id": "BK-MISSING1"},
            ]
        )
        sqlalchemy_event.listen(db, "do_orm_execute", count_bank_queries)
        try:
            record_operator_action(
                db,
                OperatorContext("history@example.test", "dev", "super_admin"),
                action="desk.publications.read",
                resources=resources,
            )
        finally:
            sqlalchemy_event.remove(db, "do_orm_execute", count_bank_queries)
        assert bank_queries == 1
        db.commit()
        recorded = list(
            db.scalars(
                select(OperatorAuditLog).where(OperatorAuditLog.action == "desk.publications.read")
            )
        )
        assert {(row.target_org, row.detail["bank_id"]) for row in recorded} == {
            (ORG_1, BANK),
            (ORG_1, SIBLING),
            (ORG_2, FOREIGN),
        }
        assert len(recorded) == 3 and all(row.detail["outcome"] == "success" for row in recorded)


def _update_shared_register(
    db: Session, ctx: TenantContext, subject: str, payload: dict[str, JsonValue]
) -> None:
    if subject == "param_ecl_assumption":
        credit_params.update_ecl_register(
            db, ctx, BANK, EclAssumptionUpdate.model_validate(payload)
        )
    elif subject == "param_crm_haircut":
        credit_params.update_crm_register(db, ctx, BANK, CrmHaircutUpdate.model_validate(payload))
    elif subject == "param_concentration_limit":
        credit_params.update_concentration_limit_register(
            db, ctx, BANK, ConcentrationLimitUpdate.model_validate(payload)
        )
    elif subject == "param_credit_threshold":
        credit_params.update_credit_threshold_register(
            db, ctx, BANK, CreditThresholdUpdate.model_validate(payload)
        )
    elif subject == "param_liquidity_threshold":
        liquidity_thresholds.update_register(
            db, ctx, BANK, LiquidityThresholdUpdate.model_validate(payload)
        )
    else:
        liquidity_thresholds.update_haircut_schedule(
            db, ctx, BANK, LiquidityHaircutUpdate.model_validate(payload)
        )


def _prepare_shared_register_banks() -> tuple[str, str]:
    other_jurisdiction = "BK-AUDIT004"
    other_class = "BK-AUDIT005"
    with _session() as db:
        for bank_id, jurisdiction, currency, kind in (
            (other_jurisdiction, "NG", "NGN", "universal_bank"),
            (other_class, "GH", "GHS", "savings_and_loans"),
        ):
            db.add(
                Bank(
                    id=bank_id,
                    organization_id=ORG_1,
                    name="Unaffected audit fixture",
                    short_name="Audit fixture",
                    currency=currency,
                    jurisdiction_code=jurisdiction,
                    license_type=kind,
                    institution_type=kind,
                )
            )
        db.commit()
    for bank_id in (BANK, SIBLING, other_jurisdiction, other_class):
        grant(RoleBundle.SECURITY_OFFICER, bank=bank_id)
    return other_jurisdiction, other_class


def _assert_shared_register_change_values(
    original_details: dict[str, JsonValue],
    subject: str,
    payload: dict[str, JsonValue],
    previous_payload: dict[str, JsonValue],
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    after = TypeAdapter(dict[str, JsonValue]).validate_python(original_details["after"])
    model: type[BaseModel] = {
        "param_ecl_assumption": EclAssumptionUpdate,
        "param_crm_haircut": CrmHaircutUpdate,
        "param_concentration_limit": ConcentrationLimitUpdate,
        "param_credit_threshold": CreditThresholdUpdate,
        "param_liquidity_threshold": LiquidityThresholdUpdate,
        "param_liquidity_haircut": LiquidityHaircutUpdate,
    }[subject]
    assert model.model_validate({**payload, **after}) == model.model_validate(payload)
    before = TypeAdapter(dict[str, JsonValue]).validate_python(original_details["before"])
    assert model.model_validate({**payload, **before}) == model.model_validate(
        {**previous_payload, "effective_from": payload["effective_from"]}
    )
    return before, after


def _assert_shared_register_inspection_visibility(
    db: Session, original_id: UUID, expected_banks: set[str], staff: bool
) -> None:
    for bank_id in expected_banks:
        db.execute(
            text(
                "SELECT set_config('app.organization_id', :org, true), "
                "set_config('app.audit_bank_id', :bank, true)"
            ),
            {"org": ORG_1, "bank": bank_id},
        )
        context = cast(
            object,
            db.scalar(
                text("SELECT bank_audit_inspection_context('audit_events', :event)"),
                {"event": original_id},
            ),
        )
        if staff and bank_id == BANK:
            assert context is not None
        else:
            assert context is None


@pytest.mark.parametrize("staff", [False, True])
@pytest.mark.parametrize(
    ("subject", "action", "change"),
    [
        (
            "param_ecl_assumption",
            "ecl_assumptions.updated",
            {"assumptions": [{"segment": "ALL", "stage": 1, "pd_pct": "2", "lgd_pct": "40"}]},
        ),
        ("param_crm_haircut", "crm_haircuts.updated", {"haircuts": {"CASH": "5"}}),
        (
            "param_concentration_limit",
            "concentration_limits.updated",
            {
                "limits": [
                    {
                        "dimension": "sector",
                        "limit_kind": "share_of_book_pct",
                        "bucket_key": None,
                        "value": "25",
                    }
                ]
            },
        ),
        (
            "param_credit_threshold",
            "credit_thresholds.updated",
            {"thresholds": {"npl_board_trigger_pct": "7"}},
        ),
        (
            "param_liquidity_threshold",
            "liquidity_thresholds.updated",
            {"thresholds": {"narrow_to_volatile": "110"}},
        ),
        ("param_liquidity_haircut", "liquidity_haircuts.updated", {"haircuts": {"BONDS": "10"}}),
    ],
)
def test_shared_register_visibility_redaction_and_integrity(
    db_client: TestClient, subject: str, action: str, change: dict[str, JsonValue], staff: bool
) -> None:
    other_jurisdiction, other_class = _prepare_shared_register_banks()
    payload: dict[str, JsonValue] = {
        **change,
        "effective_from": "2026-10-01",
        "approved_by": "Private approving officer",
        "reason": "Private board deliberation",
    }
    with _session() as db:
        session = inspector.start_session(
            db,
            organization_id=ORG_1,
            started_by="private-staff@example.test",
            reason="Private investigation",
            support_reference="PRIVATE-TICKET",
            mode="consent",
            ttl_minutes=30,
        )
        ctx = TenantContext(
            ORG_1,
            actor_user_id=None if staff else USER_1,
            impersonation_context=str(session.id) if staff else None,
            actor_operator="private-staff@example.test" if staff else None,
        )
        previous_payload: dict[str, JsonValue] = {**payload, "effective_from": "2026-09-01"}
        if subject == "param_ecl_assumption":
            previous_payload["assumptions"] = [
                {"segment": "ALL", "stage": 1, "pd_pct": "1", "lgd_pct": "40"}
            ]
        for current_payload in (previous_payload, payload):
            _update_shared_register(db, ctx, subject, current_payload)
        original = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == action)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(1)
        )
        assert original is not None
        original_details = TypeAdapter(dict[str, JsonValue]).validate_python(original.details)
        assert original_details["bank_id"] == BANK
        before, after = _assert_shared_register_change_values(
            original_details, subject, payload, previous_payload
        )
        historical = AuditEvent(
            organization_id=ORG_1,
            actor_user_id=original.actor_user_id,
            event_type=action,
            entity_type=subject,
            entity_id=BANK,
            details={key: value for key, value in original_details.items() if key != "bank_id"},
        )
        db.add(historical)
        record_event(
            db, TenantContext(ORG_2), event_type=action, entity_type=subject, entity_id=FOREIGN
        )
        db.add(
            AuditEvent(
                organization_id=ORG_1,
                event_type=action,
                entity_type=subject,
                entity_id=BANK,
                details={"bank_id": other_jurisdiction},
            )
        )
        db.commit()
        expected_ids = {original.id, historical.id}
        activity = operator_views.get_tenant_activity(db, ORG_1, 100)
        expected_banks = {BANK, SIBLING}
        if subject != "param_liquidity_threshold":
            expected_banks.add(other_class)
        assert {
            item.bank_id for item in activity.items if item.summary.startswith(action)
        } == expected_banks
        _assert_shared_register_inspection_visibility(db, original.id, expected_banks, staff)
    sequences: dict[UUID, int | None] = {}
    for bank_id in (BANK, SIBLING, other_jurisdiction, other_class):
        url = f"/api/v1/banks/{bank_id}/audit-log"
        response = db_client.get(
            url,
            headers=headers(ORG_1),
            params={"event_type": action, "start": original.created_at.isoformat()},
        )
        assert response.status_code == 200, response.text
        rows = AuditLogRead.model_validate_json(response.text).events
        if bank_id not in expected_banks:
            assert rows == []
            continue
        assert {row.id for row in rows} == expected_ids
        for row in rows:
            assert row.integrity == "verified" and row.sequence
            assert row.before == before and row.after == after and row.target_id == BANK
            if bank_id == BANK:
                sequences[row.id] = row.sequence
                assert row.actor == ("private-staff@example.test" if staff else str(USER_1))
                assert row.reason == "Private board deliberation"
                assert row.details["approved_by"] == "Private approving officer"
            else:
                assert row.sequence == sequences[row.id]
                assert row.actor is None and row.reason is None and row.support_reference is None
                assert row.details == {
                    **({"bank_id": BANK} if row.id == original.id else {}),
                    **(
                        {"institution_class": "bank"}
                        if subject == "param_liquidity_threshold"
                        else {}
                    ),
                    "effective_from": "2026-10-01",
                    "before": before,
                    "after": after,
                    **{
                        key: original_details[key]
                        for key in (
                            "entries",
                            "collateral_classes",
                            "rows",
                            "codes",
                            "asset_classes",
                        )
                        if key in original_details
                    },
                }
        filtered = db_client.get(
            url,
            headers=headers(ORG_1),
            params={
                "event_type": action,
                "start": original.created_at.isoformat(),
                "actor": "private-staff@example.test" if staff else str(USER_1),
            },
        )
        assert filtered.status_code == 200, filtered.text
        assert {row.id for row in AuditLogRead.model_validate_json(filtered.text).events} == (
            expected_ids if bank_id == BANK else set()
        )


@pytest.mark.parametrize(
    ("subject", "action", "change"),
    [
        (
            "param_ecl_assumption",
            "ecl_assumptions.updated",
            {
                "assumptions": [
                    {
                        "segment": "ALL",
                        "stage": 1,
                        "pd_pct": "1.23456789",
                        "lgd_pct": "40.12345678",
                    },
                    {
                        "segment": "ALL",
                        "stage": 2,
                        "pd_pct": "2.34567891",
                        "lgd_pct": "50.23456789",
                    },
                ]
            },
        ),
        ("param_crm_haircut", "crm_haircuts.updated", {"haircuts": {"CASH": "5.12345678"}}),
        (
            "param_concentration_limit",
            "concentration_limits.updated",
            {
                "limits": [
                    {
                        "dimension": "sector",
                        "limit_kind": "share_of_book_pct",
                        "bucket_key": None,
                        "value": "25.12345678",
                    },
                    {
                        "dimension": "product",
                        "limit_kind": "share_of_book_pct",
                        "bucket_key": "loans",
                        "value": "35.12345678",
                    },
                ]
            },
        ),
        (
            "param_credit_threshold",
            "credit_thresholds.updated",
            {"thresholds": {"npl_board_trigger_pct": "7.12345678"}},
        ),
        (
            "param_liquidity_threshold",
            "liquidity_thresholds.updated",
            {"thresholds": {"narrow_to_volatile": "110.12345678"}},
        ),
        (
            "param_liquidity_haircut",
            "liquidity_haircuts.updated",
            {"haircuts": {"BONDS": "10.12345678"}},
        ),
    ],
)
def test_register_audit_after_matches_next_before_at_storage_precision(
    db_client: TestClient, subject: str, action: str, change: dict[str, JsonValue]
) -> None:
    grant(RoleBundle.SECURITY_OFFICER)
    grant(RoleBundle.SECURITY_OFFICER, bank=SIBLING)
    with _session() as db:
        for index, effective_from in enumerate(("2026-10-01", "2026-11-01")):
            request_change: dict[str, JsonValue] = change
            if index and subject in {"param_ecl_assumption", "param_concentration_limit"}:
                key = "assumptions" if subject == "param_ecl_assumption" else "limits"
                request_entries = TypeAdapter(list[dict[str, JsonValue]]).validate_python(
                    change[key]
                )
                request_change = {**change, key: list(reversed(request_entries))}
            _update_shared_register(
                db,
                TenantContext(ORG_1, actor_user_id=USER_1),
                subject,
                {
                    **request_change,
                    "effective_from": effective_from,
                    "approved_by": "Private approver",
                    "reason": "Private reason",
                },
            )
    for bank in (BANK, SIBLING):
        response = db_client.get(
            f"/api/v1/banks/{bank}/audit-log",
            headers=headers(ORG_1),
            params={"event_type": action},
        )
        assert response.status_code == 200, response.text
        entries = AuditLogRead.model_validate_json(response.text).events
        assert len(entries) == 2
        newest, previous = entries
        assert previous.after == newest.before == newest.after
        assert previous.after != change
        assert all(entry.integrity == "verified" for entry in entries)
        if subject == "param_ecl_assumption":
            evidence = TypeAdapter(dict[str, list[dict[str, str | int]]]).validate_python(
                previous.after
            )
            assert Decimal(str(evidence["assumptions"][0]["pd_pct"])) == Decimal("1.234568")
            assert Decimal(str(evidence["assumptions"][0]["lgd_pct"])) == Decimal("40.123457")
        if bank == SIBLING:
            assert all(entry.actor is None and entry.reason is None for entry in entries)
        else:
            assert all(
                entry.actor == str(USER_1) and entry.reason == "Private reason" for entry in entries
            )
