"""Object catalogue for the object-reference (IDOR) authorization tests.

Every identifier family a tenant route accepts — a ``{package_id}`` path
segment, a ``reporting_period_id`` body field, a ``scenario_id`` query value —
is one :class:`ObjectKind`: where the identifier appears, and a factory that
persists one referenced object for a tenant.  Both the deterministic coverage
test (`tests/api/test_authorization_object_reference_coverage.py`) and the
generative property (`tests/db/test_authorization_object_reference_properties.py`)
seed the catalogue from here and share the route census in
`tests/fixtures/object_reference_routes.py`.

Factories take any session and only ``add``/``flush`` rows, so the same
catalogue seeds a disposable SQLite database (coverage) and a FORCE-RLS
Postgres session (property) unchanged; the caller owns the organization GUC and
the commit.  One object per kind per tenant is created, in dependency order;
uniqueness-bound columns derive from the tenant's bank slug so three tenants
coexist.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.authorization import InstitutionScope, ModuleScope, RoleBundle, SensitivityScope
from app.models import (
    AuthorizationBinding,
    Bank,
    BankFinancialFact,
    BankLicense,
    BankNameHistory,
    BankProduct,
    BankReportingPeriod,
    CalculationRun,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalWithdrawal,
    CapitalProjection,
    DatabaseDirectConnection,
    Document,
    EnterpriseStressSignoff,
    FilingWorkflowTemplate,
    FinancialAccount,
    FinancialBalance,
    FinancialCashFlow,
    FinancialCovenant,
    FinancialInstitution,
    FinancialObligation,
    FinancialReportingPeriod,
    ImpliedRatingRun,
    IngestionBatch,
    IntegrationKey,
    Job,
    LineageRecord,
    MacroScenario,
    ManagementActionPlan,
    MappingConfigRecord,
    MarketDataConnection,
    MarketDataOverlay,
    Notification,
    Outlet,
    ReconciliationException,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    RegulatoryPackageAttachment,
    RegulatoryResubmissionRequest,
    RegulatoryRun,
    RelatedParty,
    RiskAssessment,
    RiskAssessmentRun,
    RiskCase,
    RiskFinding,
    RiskScenario,
    SavedScenarioAnalysis,
    ScenarioAssumption,
    Shareholding,
    StoredObject,
    StressScenario,
    SystemOfRecordDeclaration,
    TemenosConnection,
    User,
)

AS_OF: Final = date(2026, 9, 18)
PERIOD_START: Final = date(2026, 9, 1)
_NOW: Final = datetime(2026, 9, 18, 12, tzinfo=UTC)


@dataclass(frozen=True)
class TenantSeed:
    """One organization/bank pair with the human principals that act in it."""

    organization_id: str
    bank_id: str
    user_ids: tuple[UUID, ...]
    marker: str

    @property
    def actor_id(self) -> UUID:
        return self.user_ids[0]

    @property
    def maker_id(self) -> UUID:
        """A second human so maker-checker fixtures do not collapse onto the actor."""
        return self.user_ids[1]

    @property
    def slug(self) -> str:
        """A per-tenant suffix for columns unique within a shared organization."""
        return self.bank_id.lower()


@dataclass
class ObjectSet:
    """The persisted objects for one tenant, keyed by object kind."""

    tenant: TenantSeed
    ids: dict[str, str] = field(default_factory=dict)

    def __getitem__(self, kind: str) -> str:
        return self.ids[kind]

    def identifiers(self) -> set[str]:
        return {*self.ids.values(), self.tenant.marker}


Factory = Callable[[Session, TenantSeed, "ObjectSet"], Any]


@dataclass(frozen=True)
class ObjectKind:
    """One identifier family and the factory that persists an instance of it."""

    name: str
    factory: Factory
    #: Route path prefixes (up to and including the identifier segment) that
    #: introduce this identifier in a path.  Empty for body/query-only kinds.
    path_prefixes: tuple[str, ...] = ()
    #: Whether the object row belongs to a bank (sibling-bank layouts apply).
    bank_scoped: bool = True


def _uuid(session: Session, row: Any) -> str:
    session.add(row)
    session.flush()
    return str(row.id)


def _period(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    period = BankReportingPeriod(
        organization_id=tenant.organization_id,
        bank_id=tenant.bank_id,
        period_start=PERIOD_START,
        period_end=AS_OF,
        label=tenant.marker,
        status="open",
    )
    period_id = _uuid(session, period)
    session.add(
        BankFinancialFact(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            reporting_period_id=period.id,
            fact_group="balance_sheet",
            category=tenant.marker,
            amount=Decimal("12345"),
            currency="GHS",
        )
    )
    session.flush()
    return period_id


def _regulatory_run(module: str) -> Factory:
    def factory(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
        return _uuid(
            session,
            RegulatoryRun(
                organization_id=tenant.organization_id,
                bank_id=tenant.bank_id,
                reporting_period_id=UUID(objects["period"]),
                module=module,
                scenario_code="baseline",
                status="succeeded",
                engine_version="object-reference-property",
                input_schema_version="bank-facts-v3",
                output_schema_version="v1",
                input_hash="0" * 64,
                inputs={"marker": tenant.marker},
                metrics={},
                completed_at=_NOW,
                created_by=tenant.actor_id,
            ),
        )

    return factory


def _signoff(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        EnterpriseStressSignoff(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            run_id=UUID(objects["enterprise_stress_run"]),
            reporting_period_id=UUID(objects["period"]),
            scenario_code="baseline",
            scenario_narrative=tenant.marker,
            assumptions_rationale=tenant.marker,
            created_by=tenant.maker_id,
        ),
    )


def _implied_rating_run(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        ImpliedRatingRun(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            reporting_period_id=UUID(objects["period"]),
            methodology_code="object-reference-property",
            methodology_version=1,
            status="succeeded",
            engine_version="object-reference-property",
            input_hash="0" * 64,
            input_snapshot={"marker": tenant.marker},
            completed_at=_NOW,
            created_by=tenant.actor_id,
        ),
    )


def _ingestion_batch(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        IngestionBatch(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            source_system="MANUAL_UPLOAD",
            adapter_version="1",
            extraction_mode="full",
            status="accepted",
            as_of_date=AS_OF,
            created_by=tenant.actor_id,
        ),
    )


def _mapping_config(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        MappingConfigRecord(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            source_system="MANUAL_UPLOAD",
            version=1,
            status="draft",
            name=tenant.marker,
            config={},
            created_by=tenant.actor_id,
        ),
    )


def _lineage(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        LineageRecord(
            organization_id=tenant.organization_id,
            ingestion_batch_id=UUID(objects["ingestion_batch"]),
            operation_type="ADAPTER_EXTRACT",
            operation_ref=tenant.marker,
        ),
    )


def _position_snapshot(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    provenance = {
        "organization_id": tenant.organization_id,
        "bank_id": tenant.bank_id,
        "as_of_date": AS_OF,
        "source_system": "MANUAL_UPLOAD",
        "ingestion_batch_id": UUID(objects["ingestion_batch"]),
        "lineage_id": UUID(objects["lineage"]),
    }
    position = CanonicalPosition(
        position_type="LOAN",
        currency="GHS",
        source_reference=f"{tenant.marker}-position-{tenant.slug}",
        **provenance,
    )
    session.add(position)
    session.flush()
    return _uuid(
        session,
        CanonicalPositionSnapshot(
            position_id=position.id,
            balance=Decimal("1000"),
            source_reference=f"{tenant.marker}-snapshot-{tenant.slug}",
            **provenance,
        ),
    )


def _license(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        BankLicense(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            license_name=tenant.marker,
        ),
    )


def _name_history(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        BankNameHistory(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            previous_name=tenant.marker,
        ),
    )


def _outlet(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        Outlet(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            outlet_type="branch",
            name=tenant.marker,
        ),
    )


def _product(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        BankProduct(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            name=tenant.marker,
            product_type="deposit",
        ),
    )


def _related_party(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        RelatedParty(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            party_type="individual",
            full_name=tenant.marker,
        ),
    )


def _shareholding(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        Shareholding(
            organization_id=tenant.organization_id,
            party_id=UUID(objects["related_party"]),
            share_type="ordinary",
            shareholder_rights="voting",
            number_of_shares=Decimal("100"),
            pct_shareholding=Decimal("1.5"),
        ),
    )


def _database_connection(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        DatabaseDirectConnection(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            backend="jdbc",
            display_name=f"{tenant.marker} {tenant.slug}",
            host="db.example.test",
            database="core",
            vault_path=f"object-reference/{tenant.bank_id}/database",
            created_by=tenant.actor_id,
        ),
    )


def _market_data_connection(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        MarketDataConnection(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            vendor="bloomberg",
            display_name=f"{tenant.marker} {tenant.slug}",
            vault_path=f"object-reference/{tenant.bank_id}/market-data",
            created_by=tenant.actor_id,
        ),
    )


def _temenos_connection(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        TemenosConnection(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            connection_mode="OPEN_API",
            display_name=f"{tenant.marker} {tenant.slug}",
            endpoint="https://core.example.test",
            vault_path=f"object-reference/{tenant.bank_id}/temenos",
            default_currency="GHS",
            created_by=tenant.actor_id,
        ),
    )


def _overlay(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        MarketDataOverlay(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            base_ref_kind="curve",
            base_curve_name="AEQ.GHS.OIS",
            adjustment_type="additive_bps",
            value=Decimal("5"),
            component_tag="liquidity_premium",
            effective_from=AS_OF,
            note=tenant.marker,
            created_by=tenant.actor_id,
        ),
    )


def _reconciliation_exception(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        ReconciliationException(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            control="balance_sheet_identity",
            max_gap_fraction=Decimal("0.05"),
            effective_from=AS_OF,
            reason=tenant.marker,
            requested_by=tenant.maker_id,
            requested_at=_NOW,
            approved_by=tenant.marker,
            approved_by_user_id=tenant.actor_id,
            approval_timestamp=_NOW,
        ),
    )


def _package(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        RegulatoryPackage(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            return_family="bsd",
            return_code="BSD1",
            reporting_date=AS_OF,
            frequency="monthly",
            status="draft",
            version=1,
            snapshot={"marker": tenant.marker},
            source_runs=[
                {
                    "module": "liquidity",
                    "run_id": objects["regulatory_run"],
                    "input_hash": "0" * 64,
                    "engine_version": "object-reference-property",
                }
            ],
            generated_by=tenant.maker_id,
            notes=tenant.marker,
        ),
    )


def _package_artifact(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        RegulatoryPackageArtifact(
            organization_id=tenant.organization_id,
            package_id=UUID(objects["package"]),
            kind="pdf",
            object_path=f"object-reference/{tenant.marker}.pdf",
            checksum_sha256="0" * 64,
            size_bytes=1,
        ),
    )


def _artifact_version(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        RegulatoryArtifactVersion(
            organization_id=tenant.organization_id,
            package_id=UUID(objects["package"]),
            kind="pdf",
            object_path=f"object-reference/{tenant.marker}-v1.pdf",
            checksum_sha256="0" * 64,
            size_bytes=1,
        ),
    )


def _package_attachment(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        RegulatoryPackageAttachment(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            package_id=UUID(objects["package"]),
            package_version=1,
            kind="board_resolution",
            title=tenant.marker,
            original_filename=f"{tenant.marker}.pdf",
            media_type="application/pdf",
            byte_size=1,
            sha256="0" * 64,
            storage_tier="outputs",
            object_path=f"object-reference/{tenant.marker}-attachment.pdf",
            source="package_upload",
            gate="optional",
            attached_by=tenant.maker_id,
        ),
    )


def _resubmission_request(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        RegulatoryResubmissionRequest(
            organization_id=tenant.organization_id,
            package_id=UUID(objects["package"]),
            reason=tenant.marker,
            requested_by=tenant.maker_id,
        ),
    )


def _filing_workflow_template(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        FilingWorkflowTemplate(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            version=1,
            status="draft",
            stages=[],
            reason=tenant.marker,
            proposed_by=tenant.maker_id,
        ),
    )


def _analysis(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        SavedScenarioAnalysis(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            module="liquidity",
            reporting_period_id=UUID(objects["period"]),
            name=tenant.marker,
            engine_version="object-reference-property",
            scenarios=[],
            results=[],
            created_by=tenant.actor_id,
        ),
    )


def _stress_scenario(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        StressScenario(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            module="liquidity",
            code=f"custom-{tenant.slug}",
            name=tenant.marker,
            shocks={},
            created_by=tenant.actor_id,
        ),
    )


def _withdrawal(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        CanonicalWithdrawal(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            source_system="MANUAL_UPLOAD",
            as_of_date=AS_OF,
            entity="position",
            reason=tenant.marker,
            requested_by=tenant.marker,
            requested_by_user_id=tenant.maker_id,
            requested_at=_NOW,
        ),
    )


def _declaration(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        SystemOfRecordDeclaration(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            position_type="LOAN",
            source_system="MANUAL_UPLOAD",
            effective_from=AS_OF,
            source_citation=tenant.marker,
            rationale=tenant.marker,
            proposed_by=tenant.marker,
            proposed_by_user_id=tenant.maker_id,
            proposed_at=_NOW,
        ),
    )


def _macro_scenario(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        MacroScenario(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            code=f"macro-{tenant.slug}",
            name=tenant.marker,
            scenario_type="hypothetical",
            created_by=tenant.actor_id,
        ),
    )


def _management_action_plan(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        ManagementActionPlan(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            code=f"plan-{tenant.slug}",
            name=tenant.marker,
            created_by=tenant.actor_id,
        ),
    )


def _case(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        RiskCase(
            organization_id=tenant.organization_id,
            title=tenant.marker,
            case_type="credit_review",
            status="active",
            created_by=tenant.actor_id,
        ),
    )


def _risk_scenario(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        RiskScenario(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            name=tenant.marker,
            scenario_type="baseline",
            created_by=tenant.actor_id,
        ),
    )


def _assumption(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        ScenarioAssumption(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            scenario_id=UUID(objects["risk_scenario"]),
            category="growth",
            key="asset_growth_rate",
            label=tenant.marker,
            value=0.05,
        ),
    )


def _calculation_run(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        CalculationRun(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            scenario_id=UUID(objects["risk_scenario"]),
            status="succeeded",
            engine_version="object-reference-property",
            input_schema_version="v1",
            output_schema_version="v1",
            input_hash="0" * 64,
            inputs={"marker": tenant.marker},
            forecast_periods=1,
            as_of_date=AS_OF,
            completed_at=_NOW,
            created_by=tenant.actor_id,
        ),
    )


def _projection(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        CapitalProjection(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            scenario_id=UUID(objects["risk_scenario"]),
            calculation_run_id=UUID(objects["calculation_run"]),
            status="succeeded",
            engine_version="object-reference-property",
            input_hash="0" * 64,
            reporting_currency="GHS",
            completed_at=_NOW,
            created_by=tenant.actor_id,
        ),
    )


def _financial_institution(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        FinancialInstitution(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            dedupe_key=f"institution:{tenant.marker}",
            name=tenant.marker,
        ),
    )


def _account(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        FinancialAccount(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            dedupe_key=f"account:{tenant.marker}",
            institution_id=UUID(objects["financial_institution"]),
            account_name=tenant.marker,
        ),
    )


def _financial_period(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        FinancialReportingPeriod(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            dedupe_key=f"period:{tenant.marker}",
            period_type="month",
            start_date=PERIOD_START,
            end_date=AS_OF,
            label=tenant.marker,
        ),
    )


def _balance(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        FinancialBalance(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            dedupe_key=f"balance:{tenant.marker}",
            account_id=UUID(objects["account"]),
            reporting_period_id=UUID(objects["financial_period"]),
            balance_type="closing",
            amount=Decimal("100"),
        ),
    )


def _cash_flow(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        FinancialCashFlow(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            dedupe_key=f"cash-flow:{tenant.marker}",
            account_id=UUID(objects["account"]),
            reporting_period_id=UUID(objects["financial_period"]),
            amount=Decimal("100"),
            direction="inflow",
            category="operating",
        ),
    )


def _obligation(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        FinancialObligation(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            dedupe_key=f"obligation:{tenant.marker}",
            institution_id=UUID(objects["financial_institution"]),
            account_id=UUID(objects["account"]),
            reporting_period_id=UUID(objects["financial_period"]),
            obligation_type="term_loan",
            principal_amount=Decimal("100"),
            outstanding_amount=Decimal("50"),
            status="active",
        ),
    )


def _covenant(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        FinancialCovenant(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            dedupe_key=f"covenant:{tenant.marker}",
            obligation_id=UUID(objects["obligation"]),
            reporting_period_id=UUID(objects["financial_period"]),
            name=tenant.marker,
            metric="leverage",
            operator="lte",
            threshold=Decimal("3"),
            compliance_status="unknown",
        ),
    )


def _finding(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        RiskFinding(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            risk_type="liquidity",
            title=tenant.marker,
            summary=tenant.marker,
            severity="medium",
        ),
    )


def _document(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    stored = StoredObject(
        organization_id=tenant.organization_id,
        provider="s3",
        bucket="object-reference-property",
        object_key=f"{tenant.marker}/{tenant.slug}/document.pdf",
        status="uploaded",
        created_by=tenant.actor_id,
    )
    session.add(stored)
    session.flush()
    return _uuid(
        session,
        Document(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            stored_object_id=stored.id,
            filename=f"{tenant.marker}.pdf",
            status="uploaded",
            uploaded_by=tenant.actor_id,
        ),
    )


def _assessment(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        RiskAssessment(
            organization_id=tenant.organization_id,
            case_id=UUID(objects["case"]),
            name=tenant.marker,
            assessment_type="credit",
            status="draft",
            created_by=tenant.actor_id,
        ),
    )


def _assessment_run(session: Session, tenant: TenantSeed, objects: ObjectSet) -> str:
    return _uuid(
        session,
        RiskAssessmentRun(
            organization_id=tenant.organization_id,
            assessment_id=UUID(objects["assessment"]),
            status="succeeded",
        ),
    )


def _notification(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        Notification(
            organization_id=tenant.organization_id,
            recipient_user_id=tenant.actor_id,
            type="object_reference_property",
            severity="info",
            title=tenant.marker,
            body=tenant.marker,
        ),
    )


def _job(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        Job(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_id,
            job_type="object_reference_property",
            status="completed",
            payload={"marker": tenant.marker},
        ),
    )


def _integration_key(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    """Persist an integration key's rows directly (service user, key, binding).

    The lifecycle routes authenticate the human account administrator, so the
    row content — not a live ``aeq_live_…`` credential — is what a foreign
    reference targets.  Rows are added to the caller's session so the key seeds
    identically under SQLite rollback and FORCE-RLS Postgres.
    """
    service_user = User(
        id=uuid4(),
        organization_id=tenant.organization_id,
        email=f"integration-{tenant.slug}@service.aequoros.invalid",
        display_name=f"Integration — {tenant.marker}",
        role="viewer",
        auth_provider="service",
        is_active=True,
    )
    session.add(service_user)
    session.flush()
    raw = f"aeq_live_object_reference_{tenant.slug}"
    key = IntegrationKey(
        organization_id=tenant.organization_id,
        bank_id=tenant.bank_id,
        service_user_id=service_user.id,
        label=tenant.marker,
        key_prefix="aeq_live_…",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        created_by=tenant.actor_id,
    )
    key_id = _uuid(session, key)
    session.add(
        AuthorizationBinding(
            organization_id=tenant.organization_id,
            principal_user_id=service_user.id,
            principal_type="machine",
            role_bundle=RoleBundle.INTEGRATION_WRITER.value,
            institution_scope=InstitutionScope.INSTITUTION.value,
            institution_id=tenant.bank_id,
            module_scope=ModuleScope.DATA.value,
            sensitivity_scope=SensitivityScope.RESTRICTED.value,
            granted_by_type="system",
            granted_by_id="object-reference-property",
            grant_reason=f"integration key for {tenant.marker}",
            status="active",
        )
    )
    session.flush()
    return key_id


def _binding(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        AuthorizationBinding(
            organization_id=tenant.organization_id,
            principal_user_id=tenant.maker_id,
            principal_type="human",
            role_bundle=RoleBundle.VIEWER.value,
            institution_scope=InstitutionScope.INSTITUTION.value,
            institution_id=tenant.bank_id,
            module_scope=ModuleScope.LIQUIDITY.value,
            sensitivity_scope=SensitivityScope.AGGREGATED.value,
            granted_by_type="system",
            granted_by_id="object-reference-property",
            grant_reason=tenant.marker,
            status="active",
        ),
    )


def _access_request(session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return _uuid(
        session,
        User(
            id=uuid4(),
            organization_id=tenant.organization_id,
            email=f"access-request-{tenant.slug}@example.test",
            display_name=tenant.marker,
            is_active=False,
            role="viewer",
            auth_provider="oidc",
            sso_subject=f"object-reference-{tenant.slug}",
        ),
    )


def _bank(_session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return tenant.bank_id


def _user(_session: Session, tenant: TenantSeed, _objects: ObjectSet) -> str:
    return str(tenant.maker_id)


_BANK_PREFIX: Final = "/api/v1/banks/{bank_id}"
_CASE_PREFIX: Final = "/api/v1/cases/{case_id}"

#: Creation order respects parent references (a signoff needs its run, a
#: covenant its obligation).  ``path_prefixes`` bind path identifiers; body and
#: query identifiers resolve through :data:`REFERENCE_FIELDS`.
OBJECT_KINDS: Final[tuple[ObjectKind, ...]] = (
    ObjectKind("bank", _bank, bank_scoped=False),
    ObjectKind("user", _user, bank_scoped=False),
    ObjectKind("period", _period, (f"{_BANK_PREFIX}/reporting-periods/{{period_id}}",)),
    ObjectKind(
        "regulatory_run",
        _regulatory_run("liquidity"),
        (
            f"{_BANK_PREFIX}/regulatory-runs/{{run_id}}",
            f"{_BANK_PREFIX}/examiner/runs/{{run_id}}",
        ),
    ),
    ObjectKind(
        "enterprise_stress_run",
        _regulatory_run("enterprise_stress"),
        (f"{_BANK_PREFIX}/enterprise-stress/runs/{{run_id}}",),
    ),
    ObjectKind(
        "forecast_run",
        _regulatory_run("forecast"),
        (f"{_BANK_PREFIX}/forecast/runs/{{run_id}}",),
    ),
    ObjectKind("signoff", _signoff, (f"{_BANK_PREFIX}/enterprise-stress/signoffs/{{signoff_id}}",)),
    ObjectKind(
        "implied_rating_run",
        _implied_rating_run,
        (f"{_BANK_PREFIX}/implied-rating/runs/{{run_id}}",),
    ),
    ObjectKind(
        "ingestion_batch", _ingestion_batch, (f"{_BANK_PREFIX}/ingestion-batches/{{batch_id}}",)
    ),
    ObjectKind("mapping_config", _mapping_config),
    ObjectKind("lineage", _lineage, ("/api/v1/lineage/{lineage_id}",), bank_scoped=False),
    ObjectKind(
        "position_snapshot",
        _position_snapshot,
        (f"{_BANK_PREFIX}/position-snapshots/{{snapshot_id}}",),
    ),
    ObjectKind("license", _license, (f"{_BANK_PREFIX}/licenses/{{license_id}}",)),
    ObjectKind("name_history", _name_history, (f"{_BANK_PREFIX}/name-history/{{entry_id}}",)),
    ObjectKind("outlet", _outlet, (f"{_BANK_PREFIX}/outlets/{{outlet_id}}",)),
    ObjectKind("product", _product, (f"{_BANK_PREFIX}/products/{{product_id}}",)),
    ObjectKind("related_party", _related_party, (f"{_BANK_PREFIX}/related-parties/{{party_id}}",)),
    ObjectKind(
        "shareholding",
        _shareholding,
        (f"{_BANK_PREFIX}/related-parties/{{party_id}}/shareholdings/{{shareholding_id}}",),
    ),
    ObjectKind(
        "database_connection",
        _database_connection,
        (f"{_BANK_PREFIX}/database-direct/connections/{{connection_id}}",),
    ),
    ObjectKind(
        "market_data_connection",
        _market_data_connection,
        (f"{_BANK_PREFIX}/market-data/connections/{{connection_id}}",),
    ),
    ObjectKind(
        "temenos_connection",
        _temenos_connection,
        (f"{_BANK_PREFIX}/temenos/connections/{{connection_id}}",),
    ),
    ObjectKind("overlay", _overlay, (f"{_BANK_PREFIX}/market-data/overlays/{{overlay_id}}",)),
    ObjectKind(
        "reconciliation_exception",
        _reconciliation_exception,
        (f"{_BANK_PREFIX}/reconciliation/exceptions/{{exception_id}}",),
    ),
    ObjectKind("package", _package, (f"{_BANK_PREFIX}/regulatory-packages/{{package_id}}",)),
    ObjectKind(
        "package_artifact",
        _package_artifact,
        (f"{_BANK_PREFIX}/regulatory-artifacts/{{artifact_id}}",),
    ),
    ObjectKind(
        "artifact_version",
        _artifact_version,
        (f"{_BANK_PREFIX}/regulatory-artifact-versions/{{version_id}}",),
    ),
    ObjectKind(
        "package_attachment",
        _package_attachment,
        (f"{_BANK_PREFIX}/regulatory-packages/{{package_id}}/attachments/{{attachment_id}}",),
    ),
    ObjectKind(
        "resubmission_request",
        _resubmission_request,
        (
            f"{_BANK_PREFIX}/regulatory-packages/{{package_id}}/resubmission-requests/{{request_id}}",
        ),
    ),
    ObjectKind(
        "filing_workflow_template",
        _filing_workflow_template,
        (f"{_BANK_PREFIX}/filing-workflow-templates/{{template_id}}",),
    ),
    ObjectKind(
        "analysis",
        _analysis,
        (f"{_BANK_PREFIX}/scenario-workbench/{{module}}/analyses/{{analysis_id}}",),
    ),
    ObjectKind(
        "stress_scenario",
        _stress_scenario,
        (f"{_BANK_PREFIX}/scenario-workbench/{{module}}/scenarios/{{scenario_id}}",),
    ),
    ObjectKind(
        "withdrawal", _withdrawal, (f"{_BANK_PREFIX}/canonical-withdrawals/{{withdrawal_id}}",)
    ),
    ObjectKind(
        "declaration", _declaration, (f"{_BANK_PREFIX}/system-of-record/{{declaration_id}}",)
    ),
    ObjectKind(
        "macro_scenario",
        _macro_scenario,
        ("/api/v1/macro-scenarios/{scenario_id}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "management_action_plan",
        _management_action_plan,
        ("/api/v1/management-action-plans/{plan_id}",),
        bank_scoped=False,
    ),
    ObjectKind("case", _case, (_CASE_PREFIX,), bank_scoped=False),
    ObjectKind(
        "risk_scenario",
        _risk_scenario,
        (f"{_CASE_PREFIX}/scenarios/{{scenario_id}}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "assumption",
        _assumption,
        (f"{_CASE_PREFIX}/scenarios/{{scenario_id}}/assumptions/{{assumption_id}}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "calculation_run",
        _calculation_run,
        (f"{_CASE_PREFIX}/calculation-runs/{{run_id}}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "projection",
        _projection,
        (f"{_CASE_PREFIX}/capital-projections/{{projection_id}}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "financial_institution",
        _financial_institution,
        (f"{_CASE_PREFIX}/financial-workspace/institutions/{{institution_id}}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "account",
        _account,
        (f"{_CASE_PREFIX}/financial-workspace/accounts/{{account_id}}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "financial_period",
        _financial_period,
        (f"{_CASE_PREFIX}/financial-workspace/reporting-periods/{{reporting_period_id}}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "balance",
        _balance,
        (f"{_CASE_PREFIX}/financial-workspace/balances/{{balance_id}}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "cash_flow",
        _cash_flow,
        (f"{_CASE_PREFIX}/financial-workspace/cash-flows/{{cash_flow_id}}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "obligation",
        _obligation,
        (f"{_CASE_PREFIX}/financial-workspace/obligations/{{obligation_id}}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "covenant",
        _covenant,
        (f"{_CASE_PREFIX}/financial-workspace/covenants/{{covenant_id}}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "finding",
        _finding,
        (
            "/api/v1/findings/{finding_id}",
            f"{_CASE_PREFIX}/liquidity/findings/{{finding_id}}",
        ),
        bank_scoped=False,
    ),
    ObjectKind("document", _document, ("/api/v1/documents/{document_id}",), bank_scoped=False),
    ObjectKind(
        "assessment",
        _assessment,
        ("/api/v1/assessments/{assessment_id}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "assessment_run",
        _assessment_run,
        ("/api/v1/assessment-runs/{run_id}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "notification",
        _notification,
        ("/api/v1/notifications/{notification_id}",),
        bank_scoped=False,
    ),
    ObjectKind("job", _job, ("/api/v1/jobs/{job_id}",), bank_scoped=False),
    ObjectKind(
        "integration_key",
        _integration_key,
        ("/api/v1/integration-keys/{key_id}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "binding",
        _binding,
        ("/api/v1/authorization/bindings/{binding_id}",),
        bank_scoped=False,
    ),
    ObjectKind(
        "access_request",
        _access_request,
        ("/api/v1/auth/sso/access-requests/{user_id}",),
        bank_scoped=False,
    ),
)

KINDS_BY_NAME: Final[Mapping[str, ObjectKind]] = {kind.name: kind for kind in OBJECT_KINDS}

#: The ORM model each id-bearing kind resolves to, for the data-layer existence
#: control.  ``bank`` and ``user`` are identity references, not seeded objects.
MODEL_BY_KIND: Final[Mapping[str, type]] = {
    "period": BankReportingPeriod,
    "regulatory_run": RegulatoryRun,
    "enterprise_stress_run": RegulatoryRun,
    "forecast_run": RegulatoryRun,
    "signoff": EnterpriseStressSignoff,
    "implied_rating_run": ImpliedRatingRun,
    "ingestion_batch": IngestionBatch,
    "mapping_config": MappingConfigRecord,
    "lineage": LineageRecord,
    "position_snapshot": CanonicalPositionSnapshot,
    "license": BankLicense,
    "name_history": BankNameHistory,
    "outlet": Outlet,
    "product": BankProduct,
    "related_party": RelatedParty,
    "shareholding": Shareholding,
    "database_connection": DatabaseDirectConnection,
    "market_data_connection": MarketDataConnection,
    "temenos_connection": TemenosConnection,
    "overlay": MarketDataOverlay,
    "reconciliation_exception": ReconciliationException,
    "package": RegulatoryPackage,
    "package_artifact": RegulatoryPackageArtifact,
    "artifact_version": RegulatoryArtifactVersion,
    "package_attachment": RegulatoryPackageAttachment,
    "resubmission_request": RegulatoryResubmissionRequest,
    "filing_workflow_template": FilingWorkflowTemplate,
    "analysis": SavedScenarioAnalysis,
    "stress_scenario": StressScenario,
    "withdrawal": CanonicalWithdrawal,
    "declaration": SystemOfRecordDeclaration,
    "macro_scenario": MacroScenario,
    "management_action_plan": ManagementActionPlan,
    "case": RiskCase,
    "risk_scenario": RiskScenario,
    "assumption": ScenarioAssumption,
    "calculation_run": CalculationRun,
    "projection": CapitalProjection,
    "financial_institution": FinancialInstitution,
    "account": FinancialAccount,
    "financial_period": FinancialReportingPeriod,
    "balance": FinancialBalance,
    "cash_flow": FinancialCashFlow,
    "obligation": FinancialObligation,
    "covenant": FinancialCovenant,
    "finding": RiskFinding,
    "document": Document,
    "assessment": RiskAssessment,
    "assessment_run": RiskAssessmentRun,
    "notification": Notification,
    "job": Job,
    "integration_key": IntegrationKey,
    "binding": AuthorizationBinding,
    "access_request": User,
}

#: Body and query identifier fields, resolved by the most specific route path
#: substring first.  A field absent here is not an object reference.
REFERENCE_FIELDS: Final[tuple[tuple[str, str, str], ...]] = (
    ("/financial-workspace/", "reporting_period_id", "financial_period"),
    ("/financial-workspace/", "institution_id", "financial_institution"),
    ("/financial-workspace/", "account_id", "account"),
    ("/financial-workspace/", "obligation_id", "obligation"),
    ("/financial-workspace/", "document_id", "document"),
    ("/cases", "scenario_id", "risk_scenario"),
    ("/cases", "run_id", "calculation_run"),
    ("/cases", "calculation_run_id", "calculation_run"),
    ("/enterprise-stress/runs", "scenario_id", "macro_scenario"),
    ("/enterprise-stress/latest", "scenario_id", "macro_scenario"),
    ("/enterprise-stress/signoffs", "run_id", "enterprise_stress_run"),
    ("/scenario-workbench/", "scenario_id", "stress_scenario"),
    ("/authorization/bindings", "institution_id", "bank"),
    ("/auth/sso/access-requests", "institution_id", "bank"),
    ("", "reporting_period_id", "period"),
    ("", "management_action_plan_id", "management_action_plan"),
    ("", "mapping_config_id", "mapping_config"),
    ("", "declaration_id", "declaration"),
    ("", "case_id", "case"),
    ("", "assessment_id", "assessment"),
    ("", "ubo_party_id", "related_party"),
    ("", "user_id", "user"),
    ("", "principal_user_id", "user"),
    ("", "bank_id", "bank"),
)
#: ``*_id`` fields that name the actor of a create under the caller's own parent
#: rather than an object whose data is read.  Referencing a foreign value here is
#: not object-reference exposure, so the census treats them as non-references.
NON_REFERENCE_FIELDS: Final[frozenset[str]] = frozenset(
    {"assigned_to_user_id", "approved_by_user_id"}
)


def reference_field_kind(path: str, field_name: str) -> str | None:
    """Resolve a body/query identifier field on ``path`` to its object kind."""
    for path_marker, name, kind in REFERENCE_FIELDS:
        if name == field_name and path_marker in path:
            return kind
    return None


def path_parameter_kind(path: str, parameter: str) -> str | None:
    """Resolve a path identifier on ``path`` to its object kind."""
    token = f"{{{parameter}}}"
    prefix = path[: path.index(token) + len(token)]
    for kind in OBJECT_KINDS:
        if prefix in kind.path_prefixes:
            return kind.name
    return None


def seed_objects(session: Session, tenant: TenantSeed) -> ObjectSet:
    """Persist one object of every kind for ``tenant`` in creation order."""
    objects = ObjectSet(tenant=tenant)
    for kind in OBJECT_KINDS:
        objects.ids[kind.name] = str(kind.factory(session, tenant, objects))
    return objects


def bank_row(tenant: TenantSeed) -> Bank:
    return Bank(
        id=tenant.bank_id,
        organization_id=tenant.organization_id,
        name=tenant.marker,
        short_name=tenant.bank_id,
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
