"""Bank audit reads: explicit authority, evidence, filtering and isolation."""

import os
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    Permission,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.tenancy import TenantContext
from app.domain.capital.engine import CapitalParams, CapitalRatiosResult, RwaResult
from app.identity.schemas.audit_log import AuditLogRead
from app.identity.schemas.auth import MeResponse
from app.identity.schemas.banks import BankListRead
from app.identity.service import audit_log, authorization
from app.main import create_app
from app.models import (
    AuditEvent,
    Bank,
    BankReportingPeriod,
    IngestionBatch,
    OperatorAuditLog,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    RegulatoryRun,
    User,
)
from app.reporting.service import workflow
from app.services import ingestion, regulatory_capital
from app.services.audit import record_event
from tests.conftest import (
    ApiTestDatabase,
    rollback_sessionmaker_lifecycle,
    seed_demo_tenants,
)
from tests.db import test_postgres_migrations as migrations
from tests.support.factories.authorization import grant_institution_authority
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

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
            engine: Engine = create_engine(url)
            try:
                seed_demo_tenants(engine)
                yield ApiTestDatabase(database_url=url, engine=engine, seed=seed_demo_tenants)
            finally:
                engine.dispose()


@pytest.fixture
def db_client(
    audit_database: ApiTestDatabase, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Seed as administrator; execute every HTTP database dependency as aqs_api."""
    with rollback_sessionmaker_lifecycle(audit_database, monkeypatch) as maker:
        app = create_app()

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

        app.dependency_overrides[deps.get_tenant_db_session] = runtime_db
        token = _SESSIONS.set(maker)
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client
        finally:
            _SESSIONS.reset(token)


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
    projected = (
        MeResponse.model_validate_json(profile.text).effective_authority.institution_capabilities
    )
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
        ingestion._record_batch_event(db, ctx, batch, "Accepted source data")
        workflow._add_approval(
            db, ctx, package, action="approved", actor_user_id=USER_1, reason="Reviewed return"
        )
        workflow.add_submission_event(db, ctx, package, channel="manual", event="submitted")
        db.commit()
        regulatory_capital._persist_failure(
            db, ctx, run.id, regulatory_capital.CapitalRunError("missing_parameter", "Unavailable")
        )
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert {row.action for row in rows} == {
        "ingestion_batch.accepted",
        "regulatory_package.approval_approved",
        "regulatory_package.submission_submitted",
        "regulatory_run.failed",
    }
    assert all(row.details["bank_id"] == BANK and row.integrity == "verified" for row in rows)


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
        regulatory_capital._persist_success(
            db, TenantContext(ORG_1, actor_user_id=USER_1), run, rwa, ratios, params, None, "GHS"
        )
    response = db_client.get(PATH, headers=headers(ORG_1))
    assert response.status_code == 200, response.text
    rows = AuditLogRead.model_validate_json(response.text).events
    assert [row.action for row in rows] == ["regulatory_run.succeeded"]
    assert rows[0].details["bank_id"] == BANK and rows[0].integrity == "verified"


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
