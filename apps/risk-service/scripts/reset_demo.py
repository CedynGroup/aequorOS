from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import Table, create_engine, delete, insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import instance_state

from app.db.base import Base
from app.models import (
    CalculationForecastPeriod,
    CalculationRun,
    CapitalIndicator,
    CapitalProjection,
    CapitalProjectionFinding,
    Document,
    DocumentChunk,
    DocumentExtraction,
    FinancialAccount,
    FinancialBalance,
    FinancialCashFlow,
    FinancialCovenant,
    FinancialInstitution,
    FinancialObligation,
    FinancialRecordSourceLink,
    FinancialReportingPeriod,
    FinancialSourceRow,
    LiquidityAnalysisResult,
    Organization,
    RiskAssessment,
    RiskAssessmentRun,
    RiskCase,
    RiskCaseDecision,
    RiskFinding,
    RiskFindingEvidence,
    RiskScenario,
    RiskScore,
    ScenarioAssumption,
    StoredObject,
    User,
)
from app.services.calculations import calculate_forecast
from app.services.liquidity import RULE_VERSION as LIQUIDITY_VERSION
from app.services.liquidity import calculate_metrics

DEMO_ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
DEMO_USER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DEMO_ORG_NAME = "AequorOS Pan-African Demo Bank"
ALLOWED_EXISTING_ORG_NAMES = {DEMO_ORG_NAME, "Demo Tenant 1", "AequorOS Demo Organization"}
AS_OF_DATE = date(2026, 6, 30)
SEEDED_AT = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)

CASE_IDS = {
    "clean": UUID("90000000-0000-4000-8000-000000000001"),
    "breach": UUID("90000000-0000-4000-8000-000000000002"),
    "liquidity": UUID("90000000-0000-4000-8000-000000000003"),
    "completed": UUID("90000000-0000-4000-8000-000000000004"),
}


@dataclass(frozen=True)
class SeededRun:
    case_key: str
    case_number: int
    run_number: int
    case_id: UUID
    scenario_id: UUID
    run_id: UUID
    input_hash: str
    run_time: datetime
    periods: list[CalculationForecastPeriod]


# Tenant-scoped deletion is intentionally explicit. Several database foreign keys
# were added by migrations after their ORM tables were introduced, so metadata
# sorting alone cannot guarantee a safe reset order.
DEMO_DELETE_TABLES = (
    "jobs",
    "risk_finding_evidence",
    "capital_projection_findings",
    "capital_indicators",
    "capital_projections",
    "financial_record_source_links",
    "financial_covenants",
    "financial_balances",
    "financial_cash_flows",
    "financial_obligations",
    "financial_accounts",
    "financial_source_rows",
    "liquidity_analysis_results",
    "calculation_forecast_periods",
    "calculation_runs",
    "scenario_assumption_history",
    "scenario_assumptions",
    "risk_scores",
    "risk_findings",
    "risk_assessment_runs",
    "risk_assessments",
    "risk_case_decisions",
    "financial_manual_edit_history",
    "financial_validation_issues",
    "document_chunks",
    "document_extractions",
    "documents",
    "financial_reporting_periods",
    "financial_institutions",
    "risk_scenarios",
    "audit_events",
    "risk_cases",
    "users",
    "stored_objects",
)


def uid(prefix: int, case_number: int, item: int) -> UUID:
    return UUID(f"{prefix:08d}-0000-4000-8000-{case_number:04d}{item:08d}")


def database_url() -> str:
    value = os.environ.get("RISK_DEMO_DATABASE_URL", "").strip()
    if not value:
        raise SystemExit(
            "RISK_DEMO_DATABASE_URL is required. Use the local admin Postgres URL from "
            "docs/demo-playbook.md."
        )
    url = make_url(value)
    if url.get_backend_name() != "postgresql":
        raise SystemExit("Demo reset requires a PostgreSQL database URL.")
    return value


def reset_demo(session: Session) -> None:
    existing_name = session.scalar(select(Organization.name).where(Organization.id == DEMO_ORG_ID))
    if existing_name is not None and existing_name not in ALLOWED_EXISTING_ORG_NAMES:
        raise SystemExit(
            f"Refusing to reset organization {DEMO_ORG_ID}: unexpected name {existing_name!r}."
        )

    tenant_tables = {
        table.name
        for table in Base.metadata.tables.values()
        if table.c.get("organization_id") is not None
    }
    missing_tables = tenant_tables.difference(DEMO_DELETE_TABLES)
    if missing_tables:
        missing = ", ".join(sorted(missing_tables))
        raise RuntimeError(f"Demo reset deletion order is missing tenant tables: {missing}")
    for table_name in DEMO_DELETE_TABLES:
        table = Base.metadata.tables[table_name]
        session.execute(delete(table).where(table.c.organization_id == DEMO_ORG_ID))
    session.execute(delete(Organization).where(Organization.id == DEMO_ORG_ID))
    session.flush()

    core_insert(
        session,
        Organization(
            id=DEMO_ORG_ID,
            name=DEMO_ORG_NAME,
            created_at=SEEDED_AT,
            updated_at=SEEDED_AT,
        ),
    )
    core_insert(
        session,
        User(
            id=DEMO_USER_ID,
            organization_id=DEMO_ORG_ID,
            email="credit.officer@demo.aequoros.test",
            display_name="Ama Mensah",
            is_active=True,
            created_at=SEEDED_AT,
            updated_at=SEEDED_AT,
        ),
    )
    session.flush()

    seed_cases(session)
    seed_score_provenance(session)
    seed_financial_portfolio(session)
    seed_breach_evidence(session)
    seed_scenarios(session)
    session.commit()

    pre_run_analyses(session)


def core_insert(session: Session, *rows: object) -> None:
    """Insert fully specified seed rows immediately, without ORM dependency inference."""
    for row in rows:
        state = instance_state(row)
        values = {
            attribute.columns[0]: getattr(row, attribute.key)
            for attribute in state.mapper.column_attrs
            if attribute.key in row.__dict__
        }
        session.execute(insert(cast(Table, state.mapper.local_table)).values(values))


def seed_cases(session: Session) -> None:
    cases = (
        (
            "clean",
            "Annual review — Volta Aluminium Industries Plc",
            "Volta Aluminium Industries Plc",
            "Metals and manufacturing",
            "in_review",
            18,
            "low",
            None,
            "Large Ghanaian manufacturer with conservative leverage, strong cash conversion, "
            "and ample committed liquidity headroom.",
        ),
        (
            "breach",
            "Covenant exception — Adom Textiles & Garments Ltd",
            "Adom Textiles & Garments Ltd",
            "Textiles and apparel",
            "in_review",
            78,
            "high",
            "needs_more_info",
            "Kumasi-based SME whose current-ratio breach traces to a transposed extraction row "
            "in the June management accounts.",
        ),
        (
            "liquidity",
            "Liquidity stress review — Kivu Fresh Produce Logistics Ltd",
            "Kivu Fresh Produce Logistics Ltd",
            "Cold-chain logistics",
            "active",
            66,
            "high",
            None,
            "Regional cold-chain operator whose baseline remains liquid while delayed customer "
            "collections create a downside cash shortfall.",
        ),
        (
            "completed",
            "Completed review — Baobab Health Distribution SA",
            "Baobab Health Distribution SA",
            "Healthcare distribution",
            "completed",
            31,
            "medium",
            "approved",
            "Completed Senegal healthcare-distributor review with a documented approval, "
            "monitoring conditions, and generated committee report.",
        ),
    )
    for (
        key,
        title,
        subject,
        sector,
        status,
        score,
        risk_level,
        decision,
        description,
    ) in cases:
        core_insert(
            session,
            RiskCase(
                id=CASE_IDS[key],
                organization_id=DEMO_ORG_ID,
                title=title,
                case_type="financial_statement_review",
                subject_type="borrower",
                subject_name=subject,
                description=description,
                status=status,
                assigned_to_user_id=DEMO_USER_ID,
                assigned_at=SEEDED_AT,
                risk_score=score,
                risk_level=risk_level,
                scored_at=SEEDED_AT,
                scoring_version="demo-portfolio-v1",
                decision=decision,
                decided_at=SEEDED_AT if decision else None,
                metadata_={"sector": sector, "portfolio": "pan-african commercial lending"},
                created_by=DEMO_USER_ID,
                created_at=SEEDED_AT,
                updated_at=SEEDED_AT,
            ),
        )
    session.flush()


def seed_score_provenance(session: Session) -> None:
    score_profiles = {
        "clean": ("Volta annual credit assessment", 18, "low"),
        "breach": ("Adom covenant credit assessment", 78, "high"),
        "liquidity": ("Kivu liquidity credit assessment", 66, "high"),
        "completed": ("Baobab committee credit assessment", 31, "medium"),
    }
    for case_number, (case_key, case_id) in enumerate(CASE_IDS.items(), start=1):
        name, score, risk_level = score_profiles[case_key]
        assessment_id = uid(91400000, case_number, 1)
        run_id = uid(91500000, case_number, 1)
        input_snapshot = {
            "portfolio": "pan-african commercial lending",
            "case": case_key,
            "as_of_date": AS_OF_DATE.isoformat(),
        }
        input_hash = hashlib.sha256(
            json.dumps(input_snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        rule_results = [
            {
                "rule_id": "demo.portfolio_credit_score",
                "score_impact": score,
                "details": {"case": case_key, "as_of_date": AS_OF_DATE.isoformat()},
            }
        ]
        core_insert(
            session,
            RiskAssessment(
                id=assessment_id,
                organization_id=DEMO_ORG_ID,
                case_id=case_id,
                name=name,
                assessment_type="borrower_risk",
                status="completed",
                input_snapshot=input_snapshot,
                config_snapshot={"scoring_version": "demo-portfolio-v1"},
                created_by=DEMO_USER_ID,
                created_at=SEEDED_AT,
                updated_at=SEEDED_AT,
            ),
            RiskAssessmentRun(
                id=run_id,
                organization_id=DEMO_ORG_ID,
                assessment_id=assessment_id,
                status="completed",
                engine_version="demo-portfolio-v1",
                input_hash=input_hash,
                started_at=SEEDED_AT,
                completed_at=SEEDED_AT,
                summary={"risk_score": score, "risk_level": risk_level},
                created_at=SEEDED_AT,
            ),
            RiskScore(
                id=uid(91600000, case_number, 1),
                organization_id=DEMO_ORG_ID,
                case_id=case_id,
                assessment_id=assessment_id,
                run_id=run_id,
                score=score,
                risk_level=risk_level,
                scoring_version="demo-portfolio-v1",
                input_hash=input_hash,
                input_snapshot=input_snapshot,
                rule_results=rule_results,
                created_at=SEEDED_AT,
            ),
        )
    session.flush()


FINANCIAL_PROFILES = {
    "clean": {
        "currency": "GHS",
        "institution": "AequorOS Commercial Bank Ghana",
        "facility": "GHS 120m revolving and term facilities",
        "cash": "42000000",
        "receivables": "118000000",
        "inventory": "76000000",
        "payables": "54000000",
        "inflows": "96000000",
        "outflows": "61000000",
        "principal": "120000000",
        "outstanding": "48000000",
    },
    "breach": {
        "currency": "GHS",
        "institution": "AequorOS Commercial Bank Ghana",
        "facility": "GHS 12m SME working-capital line",
        "cash": "1800000",
        "receivables": "9200000",
        "inventory": "7800000",
        "payables": "14800000",
        "inflows": "11400000",
        "outflows": "8900000",
        "principal": "12000000",
        "outstanding": "7600000",
    },
    "liquidity": {
        "currency": "KES",
        "institution": "AequorOS Commercial Bank East Africa",
        "facility": "KES 15m seasonal trade facility",
        "cash": "2000000",
        "receivables": "31000000",
        "inventory": "14000000",
        "payables": "17000000",
        "inflows": "24000000",
        "outflows": "17000000",
        "principal": "15000000",
        "outstanding": "8000000",
    },
    "completed": {
        "currency": "XOF",
        "institution": "AequorOS Banque Afrique de l'Ouest",
        "facility": "XOF 2.4bn distribution finance facility",
        "cash": "780000000",
        "receivables": "3100000000",
        "inventory": "1850000000",
        "payables": "2100000000",
        "inflows": "2650000000",
        "outflows": "1900000000",
        "principal": "2400000000",
        "outstanding": "1100000000",
    },
}


def seed_financial_portfolio(session: Session) -> None:
    for case_number, key in enumerate(CASE_IDS, start=1):
        case_id = CASE_IDS[key]
        profile = FINANCIAL_PROFILES[key]
        institution_id = uid(93100000, case_number, 1)
        account_id = uid(93200000, case_number, 1)
        period_id = uid(93300000, case_number, 1)
        obligation_id = uid(93600000, case_number, 1)
        core_insert(
            session,
            FinancialInstitution(
                id=institution_id,
                organization_id=DEMO_ORG_ID,
                case_id=case_id,
                dedupe_key=f"demo:{key}:institution",
                name=profile["institution"],
                institution_type="commercial_bank",
                reference_code=f"PACB-{case_number:03d}",
                metadata_={"seeded": True},
                created_at=SEEDED_AT,
                updated_at=SEEDED_AT,
            ),
        )
        session.flush()
        core_insert(
            session,
            FinancialAccount(
                id=account_id,
                organization_id=DEMO_ORG_ID,
                case_id=case_id,
                dedupe_key=f"demo:{key}:account",
                institution_id=institution_id,
                account_number=f"DEMO-{case_number:02d}-OPERATING",
                account_name="Primary operating account",
                account_type="operating",
                currency=profile["currency"],
                status="active",
                metadata_={"seeded": True},
                created_at=SEEDED_AT,
                updated_at=SEEDED_AT,
            ),
        )
        session.flush()
        core_insert(
            session,
            FinancialReportingPeriod(
                id=period_id,
                organization_id=DEMO_ORG_ID,
                case_id=case_id,
                dedupe_key=f"demo:{key}:fy2026-q2",
                period_type="quarter",
                start_date=date(2026, 4, 1),
                end_date=AS_OF_DATE,
                as_of_date=AS_OF_DATE,
                label="Q2 2026 management accounts",
                metadata_={"basis": "management_accounts", "seeded": True},
                created_at=SEEDED_AT,
                updated_at=SEEDED_AT,
            ),
        )
        session.flush()
        balances = (
            (1, "cash", profile["cash"]),
            (2, "accounts_receivable", profile["receivables"]),
            (3, "inventory", profile["inventory"]),
            (4, "accounts_payable", profile["payables"]),
        )
        for item, balance_type, amount in balances:
            core_insert(
                session,
                FinancialBalance(
                    id=uid(93400000, case_number, item),
                    organization_id=DEMO_ORG_ID,
                    case_id=case_id,
                    dedupe_key=f"demo:{key}:balance:{balance_type}",
                    account_id=account_id,
                    reporting_period_id=period_id,
                    balance_type=balance_type,
                    amount=Decimal(amount),
                    currency=profile["currency"],
                    as_of_date=AS_OF_DATE,
                    metadata_={"source": "Q2 2026 management accounts", "seeded": True},
                    created_at=SEEDED_AT,
                    updated_at=SEEDED_AT,
                ),
            )
        for item, direction, category, amount in (
            (1, "inflow", "customer receipts", profile["inflows"]),
            (2, "outflow", "operating and supplier payments", profile["outflows"]),
        ):
            core_insert(
                session,
                FinancialCashFlow(
                    id=uid(93500000, case_number, item),
                    organization_id=DEMO_ORG_ID,
                    case_id=case_id,
                    dedupe_key=f"demo:{key}:cash-flow:{direction}",
                    account_id=account_id,
                    reporting_period_id=period_id,
                    cash_flow_date=date(2026, 6, 15 + item),
                    amount=Decimal(amount),
                    currency=profile["currency"],
                    direction=direction,
                    category=category,
                    metadata_={"source": "13-week treasury forecast", "seeded": True},
                    created_at=SEEDED_AT,
                    updated_at=SEEDED_AT,
                ),
            )
        core_insert(
            session,
            FinancialObligation(
                id=obligation_id,
                organization_id=DEMO_ORG_ID,
                case_id=case_id,
                dedupe_key=f"demo:{key}:facility",
                institution_id=institution_id,
                account_id=account_id,
                reporting_period_id=period_id,
                obligation_type="credit_facility",
                facility_type="revolving_credit",
                principal_amount=Decimal(profile["principal"]),
                outstanding_amount=Decimal(profile["outstanding"]),
                currency=profile["currency"],
                start_date=date(2025, 7, 1),
                maturity_date=date(2028, 6, 30),
                interest_rate=Decimal("0.185"),
                status="active",
                details={"facility_name": profile["facility"], "seeded": True},
                created_at=SEEDED_AT,
                updated_at=SEEDED_AT,
            ),
        )
        session.flush()
        covenant_value = Decimal("0.91") if key == "breach" else Decimal("1.42")
        core_insert(
            session,
            FinancialCovenant(
                id=uid(93700000, case_number, 1),
                organization_id=DEMO_ORG_ID,
                case_id=case_id,
                dedupe_key=f"demo:{key}:current-ratio-covenant",
                obligation_id=obligation_id,
                reporting_period_id=period_id,
                name="Minimum current ratio",
                metric="current_ratio",
                operator="gte",
                threshold=Decimal("1.15"),
                actual_value=covenant_value,
                compliance_status=("non_compliant" if key == "breach" else "compliant"),
                source_record={"document": "Q2 2026 covenant certificate", "page": 4},
                reporting_context={"period": "Q2 2026", "basis": "management accounts"},
                metadata_={"seeded": True},
                created_at=SEEDED_AT,
                updated_at=SEEDED_AT,
            ),
        )
    session.flush()


def seed_breach_evidence(session: Session) -> None:
    case_id = CASE_IDS["breach"]
    document_id = uid(94100000, 2, 1)
    object_id = uid(94000000, 2, 1)
    extraction_id = uid(94200000, 2, 1)
    linked_row_id = uid(94300000, 2, 1)
    unmapped_row_id = uid(94300000, 2, 2)
    covenant_id = uid(93700000, 2, 1)
    core_insert(
        session,
        StoredObject(
            id=object_id,
            organization_id=DEMO_ORG_ID,
            provider="s3",
            bucket="risk-local",
            object_key="demo/adom-textiles/q2-2026-management-accounts.pdf",
            content_type="application/pdf",
            byte_size=284112,
            sha256="4f" * 32,
            status="ready",
            created_by=DEMO_USER_ID,
            created_at=SEEDED_AT,
        ),
    )
    core_insert(
        session,
        Document(
            id=document_id,
            organization_id=DEMO_ORG_ID,
            case_id=case_id,
            stored_object_id=object_id,
            filename="Adom Textiles - Q2 2026 Management Accounts.pdf",
            document_type="management_accounts",
            source="upload",
            status="ready",
            parse_status="completed",
            uploaded_by=DEMO_USER_ID,
            uploaded_at=SEEDED_AT,
            created_at=SEEDED_AT,
            updated_at=SEEDED_AT,
        ),
    )
    chunk_id = uid(94110000, 2, 1)
    core_insert(
        session,
        DocumentChunk(
            id=chunk_id,
            organization_id=DEMO_ORG_ID,
            document_id=document_id,
            chunk_index=3,
            page_start=4,
            page_end=4,
            text=(
                "Current assets GHS 18.4m; current liabilities GHS 14.8m; "
                "minimum current ratio covenant 1.15x."
            ),
            token_count=22,
            metadata_={"section": "covenant certificate"},
            created_at=SEEDED_AT,
        ),
    )
    core_insert(
        session,
        DocumentExtraction(
            id=extraction_id,
            organization_id=DEMO_ORG_ID,
            document_id=document_id,
            extraction_type="financial_statement_rows",
            schema_version="financial-extraction-v1",
            status="completed",
            extracted_json={"row_count": 2, "warning": "column alignment confidence below 0.8"},
            confidence=Decimal("0.74"),
            created_at=SEEDED_AT,
        ),
    )
    core_insert(
        session,
        FinancialSourceRow(
            id=linked_row_id,
            organization_id=DEMO_ORG_ID,
            case_id=case_id,
            document_id=document_id,
            document_extraction_id=extraction_id,
            row_index=18,
            locator={"page": 4, "table": "Covenant certificate", "row": 18},
            raw_payload={
                "label": "Current ratio",
                "reported_value": "1.24x",
                "extracted_value": "0.91x",
                "warning": "OCR column shift",
            },
            created_at=SEEDED_AT,
        ),
        FinancialSourceRow(
            id=unmapped_row_id,
            organization_id=DEMO_ORG_ID,
            case_id=case_id,
            document_id=document_id,
            document_extraction_id=extraction_id,
            row_index=19,
            locator={"page": 4, "table": "Covenant certificate", "row": 19},
            raw_payload={
                "label": "Current assets",
                "reported_value": "GHS 18.4m",
                "note": "Unmapped after OCR column shift",
            },
            created_at=SEEDED_AT,
        ),
    )
    core_insert(
        session,
        FinancialRecordSourceLink(
            id=uid(94400000, 2, 1),
            organization_id=DEMO_ORG_ID,
            case_id=case_id,
            record_table="financial_covenants",
            record_id=covenant_id,
            source_row_id=linked_row_id,
            field_name="actual_value",
            source_field="extracted_value",
            confidence=Decimal("0.74"),
            metadata_={"requires_review": True, "reason": "OCR column shift"},
            created_at=SEEDED_AT,
        ),
    )
    session.flush()


def seed_breach_finding(session: Session) -> None:
    case_id = CASE_IDS["breach"]
    covenant_id = uid(93700000, 2, 1)
    document_id = uid(94100000, 2, 1)
    chunk_id = uid(94110000, 2, 1)
    finding_id = uid(92000000, 2, 1)
    core_insert(
        session,
        RiskFinding(
            id=finding_id,
            organization_id=DEMO_ORG_ID,
            case_id=case_id,
            risk_type="covenant_breach",
            title="Minimum current ratio reported below covenant",
            summary="The extracted current ratio is 0.91x against a minimum covenant of 1.15x.",
            rationale=(
                "The source certificate reports 1.24x, but OCR shifted the adjacent column and "
                "mapped 0.91x. Correcting the canonical covenant value removes the apparent breach."
            ),
            severity="high",
            likelihood="high",
            impact="medium",
            confidence=Decimal("0.74"),
            status="needs_review",
            source="deterministic_rule",
            rule_id="covenant.minimum_current_ratio",
            rule_version="demo-v1",
            score_impact=28,
            details={
                "threshold": "1.15",
                "extracted_value": "0.91",
                "source_value": "1.24",
                "root_cause": "OCR column shift",
                "source_url": (f"/cases/{case_id}?tab=financial#financial-covenants-{covenant_id}"),
            },
            created_at=SEEDED_AT,
            updated_at=SEEDED_AT,
        ),
    )
    core_insert(
        session,
        RiskFindingEvidence(
            id=uid(92100000, 2, 1),
            organization_id=DEMO_ORG_ID,
            finding_id=finding_id,
            document_id=document_id,
            document_chunk_id=chunk_id,
            page_number=4,
            quote="Current assets GHS 18.4m; current liabilities GHS 14.8m.",
            locator={
                "source_url": f"/cases/{case_id}?tab=financial#financial-covenants-{covenant_id}",
                "section": "Covenant certificate",
                "row": 18,
            },
            relevance=Decimal("0.98"),
            created_at=SEEDED_AT,
        ),
    )
    session.flush()


def seed_scenarios(session: Session) -> None:
    values = {
        "baseline": ("0.04", "0.03", 5, "0.35", "1.0"),
        "downside": ("-0.10", "0.10", 30, "0.80", "0.50"),
    }
    definitions = (
        ("growth", "revenue_growth_rate", "Revenue growth", "ratio"),
        ("expenses", "expense_growth_rate", "Expense growth", "ratio"),
        ("cash_flow_timing", "cash_flow_delay_days", "Cash-flow delay", "days"),
        ("credit_usage", "credit_usage_rate", "Credit usage", "ratio"),
        ("repayment_behavior", "repayment_rate", "Repayment rate", "ratio"),
    )
    for case_number, (case_key, case_id) in enumerate(CASE_IDS.items(), start=1):
        for scenario_number, scenario_type in enumerate(("baseline", "downside"), start=1):
            scenario_id = uid(97000000 + scenario_number, case_number, 1)
            core_insert(
                session,
                RiskScenario(
                    id=scenario_id,
                    organization_id=DEMO_ORG_ID,
                    case_id=case_id,
                    name=(
                        "Baseline"
                        if scenario_type == "baseline"
                        else "Downside — collections stress"
                    ),
                    description=(
                        "Management plan with modest growth and normal collection timing."
                        if scenario_type == "baseline"
                        else "Thirty-day collections delay, lower revenue, higher costs, and "
                        "partial debt repayment under stress."
                    ),
                    scenario_type=scenario_type,
                    created_by=DEMO_USER_ID,
                    created_at=SEEDED_AT,
                    updated_at=SEEDED_AT,
                ),
            )
            for assumption_number, ((category, key, label, unit), value) in enumerate(
                zip(definitions, values[scenario_type], strict=True), start=1
            ):
                core_insert(
                    session,
                    ScenarioAssumption(
                        id=uid(97200000 + scenario_number, case_number, assumption_number),
                        organization_id=DEMO_ORG_ID,
                        case_id=case_id,
                        scenario_id=scenario_id,
                        category=category,
                        key=key,
                        label=label,
                        value=value,
                        unit=unit,
                        provenance={
                            "source": "credit_team_demo_pack",
                            "case": case_key,
                            "review_note": "Approved for June 2026 portfolio review",
                        },
                        review_status="reviewed",
                        reviewed_by=DEMO_USER_ID,
                        reviewed_at=SEEDED_AT,
                        created_at=SEEDED_AT,
                        updated_at=SEEDED_AT,
                    ),
                )
    session.flush()


def seed_manual_findings(session: Session) -> None:
    completed_id = CASE_IDS["completed"]
    core_insert(
        session,
        RiskFinding(
            id=uid(92000000, 4, 1),
            organization_id=DEMO_ORG_ID,
            case_id=completed_id,
            risk_type="working_capital_monitoring",
            title="Distributor concentration requires quarterly monitoring",
            summary="The two largest hospital groups represent 38% of receivables.",
            rationale="Approval conditions include quarterly ageing and concentration reporting.",
            severity="medium",
            status="accepted",
            disposition_reason="Accepted with quarterly monitoring covenant.",
            source="manual",
            rule_id="portfolio.receivables_concentration",
            rule_version="demo-v1",
            score_impact=10,
            details={"top_two_concentration": "0.38", "monitoring_frequency": "quarterly"},
            created_at=SEEDED_AT,
            updated_at=SEEDED_AT,
        ),
    )
    session.flush()


def seed_decisions(session: Session) -> None:
    completed_id = CASE_IDS["completed"]
    core_insert(
        session,
        RiskCaseDecision(
            id=uid(91000000, 2, 1),
            organization_id=DEMO_ORG_ID,
            case_id=CASE_IDS["breach"],
            decision="needs_more_info",
            reason="Confirm the covenant certificate extraction against the signed source.",
            decided_by=DEMO_USER_ID,
            created_at=SEEDED_AT,
        ),
        RiskCaseDecision(
            id=uid(91000000, 4, 1),
            organization_id=DEMO_ORG_ID,
            case_id=completed_id,
            decision="approved",
            reason=(
                "Approve the facility with quarterly receivables ageing and concentration "
                "monitoring."
            ),
            decided_by=DEMO_USER_ID,
            created_at=SEEDED_AT,
        ),
    )
    session.flush()


def pre_run_analyses(session: Session) -> None:
    runs = [
        seed_successful_run(session, case_key, case_number, "baseline", 1)
        for case_number, case_key in enumerate(CASE_IDS, start=1)
    ]
    downside = seed_successful_run(session, "liquidity", 3, "downside", 2)
    runs.append(downside)
    if not any(period.cash < 0 for period in downside.periods):
        raise RuntimeError("Liquidity downside must contain a projected cash shortfall.")
    seed_failed_run(session)
    session.commit()

    concerns_by_run = [(run, seed_liquidity_analysis(session, run)) for run in runs]
    capital_by_run = [(run, seed_capital_projection(session, run)) for run in runs]
    session.commit()

    seed_breach_finding(session)
    seed_manual_findings(session)
    for run, concerns in concerns_by_run:
        seed_liquidity_findings(
            session,
            run.case_number,
            run.run_number,
            run.case_id,
            run.scenario_id,
            run.run_id,
            run.input_hash,
            concerns,
        )
    for run, indicators in capital_by_run:
        seed_capital_findings(session, run, indicators)
    session.commit()

    seed_decisions(session)
    session.commit()


def seed_successful_run(
    session: Session,
    case_key: str,
    case_number: int,
    scenario_type: str,
    run_number: int,
) -> SeededRun:
    case_id = CASE_IDS[case_key]
    scenario_number = 1 if scenario_type == "baseline" else 2
    scenario_id = uid(97000000 + scenario_number, case_number, 1)
    run_id = uid(98000000 + run_number, case_number, 1)
    snapshot = calculation_snapshot(case_key, case_number, scenario_type)
    input_hash = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    run_time = SEEDED_AT.replace(hour=10 + run_number)
    core_insert(
        session,
        CalculationRun(
            id=run_id,
            organization_id=DEMO_ORG_ID,
            case_id=case_id,
            scenario_id=scenario_id,
            status="succeeded",
            engine_version="balance-sheet-v1.0.0",
            input_schema_version="calculation-input-v1",
            output_schema_version="balance-sheet-output-v1",
            input_hash=input_hash,
            inputs=snapshot,
            forecast_periods=3,
            as_of_date=AS_OF_DATE,
            started_at=run_time,
            completed_at=run_time,
            created_by=DEMO_USER_ID,
            created_at=run_time,
            updated_at=run_time,
        ),
    )
    periods: list[CalculationForecastPeriod] = []
    for result in calculate_forecast(snapshot):
        period = CalculationForecastPeriod(
            id=uid(98100000 + run_number, case_number, result.period_number),
            organization_id=DEMO_ORG_ID,
            case_id=case_id,
            run_id=run_id,
            **result.__dict__,
        )
        core_insert(session, period)
        periods.append(period)
    session.flush()
    return SeededRun(
        case_key=case_key,
        case_number=case_number,
        run_number=run_number,
        case_id=case_id,
        scenario_id=scenario_id,
        run_id=run_id,
        input_hash=input_hash,
        run_time=run_time,
        periods=periods,
    )


def seed_liquidity_analysis(session: Session, run: SeededRun) -> list[dict[str, object]]:
    liquidity = calculate_metrics(run.periods)
    core_insert(
        session,
        LiquidityAnalysisResult(
            id=uid(98200000 + run.run_number, run.case_number, 1),
            organization_id=DEMO_ORG_ID,
            case_id=run.case_id,
            run_id=run.run_id,
            analysis_version=LIQUIDITY_VERSION,
            result={
                "currency": FINANCIAL_PROFILES[run.case_key]["currency"],
                "metrics": [metric.model_dump(mode="json") for metric in liquidity.metrics],
            },
            generated_at=run.run_time,
        ),
    )
    session.flush()
    return liquidity.concerns


def seed_capital_projection(session: Session, run: SeededRun) -> list[CapitalIndicator]:
    projection_id = uid(98500000 + run.run_number, run.case_number, 1)
    currency = str(FINANCIAL_PROFILES[run.case_key]["currency"])
    core_insert(
        session,
        CapitalProjection(
            id=projection_id,
            organization_id=DEMO_ORG_ID,
            case_id=run.case_id,
            scenario_id=run.scenario_id,
            calculation_run_id=run.run_id,
            status="succeeded",
            engine_version="capital-projection-v1.0.0",
            input_hash=run.input_hash,
            reporting_currency=currency,
            started_at=run.run_time,
            completed_at=run.run_time,
            created_by=DEMO_USER_ID,
            created_at=run.run_time,
            updated_at=run.run_time,
        ),
    )
    first_components = run.periods[0].components
    opening_equity = Decimal(str(first_components["opening_assets"])) - Decimal(
        str(first_components["opening_liabilities"])
    )
    indicators: list[CapitalIndicator] = []
    for period in run.periods:
        equity = period.total_equity.quantize(Decimal("0.0001"))
        equity_ratio = (period.total_equity / period.total_assets).quantize(Decimal("0.00000001"))
        liabilities_ratio = (period.total_liabilities / period.total_assets).quantize(
            Decimal("0.00000001")
        )
        equity_change = (period.total_equity - opening_equity).quantize(Decimal("0.0001"))
        pressure = (
            "critical"
            if equity < 0
            else "high"
            if equity_ratio < Decimal("0.10")
            else "medium"
            if equity_ratio < Decimal("0.20") or equity_change < 0
            else "low"
        )
        indicator = CapitalIndicator(
            id=uid(
                98600000 + run.run_number,
                run.case_number,
                period.period_number,
            ),
            organization_id=DEMO_ORG_ID,
            case_id=run.case_id,
            projection_id=projection_id,
            forecast_period_id=period.id,
            period_number=period.period_number,
            equity=equity,
            equity_to_assets_ratio=equity_ratio,
            liabilities_to_assets_ratio=liabilities_ratio,
            equity_change=equity_change,
            pressure_level=pressure,
            evidence={
                "calculation_run_id": str(run.run_id),
                "forecast_period_id": str(period.id),
                "total_assets": str(period.total_assets),
                "total_liabilities": str(period.total_liabilities),
                "total_equity": str(period.total_equity),
                "opening_equity": str(opening_equity),
            },
        )
        core_insert(session, indicator)
        indicators.append(indicator)
    session.flush()
    return indicators


def seed_capital_findings(
    session: Session, run: SeededRun, indicators: list[CapitalIndicator]
) -> None:
    projection_id = uid(98500000 + run.run_number, run.case_number, 1)
    worst = min(indicators, key=lambda item: item.equity_to_assets_ratio)
    final = indicators[-1]
    candidates: list[tuple[str, str, str, str, CapitalIndicator]] = []
    if any(item.equity < 0 for item in indicators):
        item = min(indicators, key=lambda row: row.equity)
        candidates.append(
            (
                "capital_negative_equity",
                "Projected negative equity",
                "critical",
                f"Equity falls to {item.equity} in period {item.period_number}.",
                item,
            )
        )
    elif worst.equity_to_assets_ratio < Decimal("0.10"):
        candidates.append(
            (
                "capital_thin_buffer",
                "Projected capital buffer is thin",
                "high",
                "The minimum equity-to-assets ratio is "
                f"{worst.equity_to_assets_ratio:.2%} in period {worst.period_number}.",
                worst,
            )
        )
    if final.equity_change < 0:
        candidates.append(
            (
                "capital_erosion",
                "Projected capital erosion",
                "medium",
                f"Equity declines by {abs(final.equity_change)} by period {final.period_number}.",
                final,
            )
        )
    for finding_number, (rule_id, title, severity, summary, indicator) in enumerate(
        candidates, start=1
    ):
        finding_id = uid(98700000 + run.run_number, run.case_number, finding_number)
        details = {
            "capital_projection_id": str(projection_id),
            "calculation_run_id": str(run.run_id),
            "scenario_id": str(run.scenario_id),
            "input_hash": run.input_hash,
            "indicator_id": str(indicator.id),
            "evidence": indicator.evidence,
        }
        core_insert(
            session,
            RiskFinding(
                id=finding_id,
                organization_id=DEMO_ORG_ID,
                case_id=run.case_id,
                risk_type="leverage_risk",
                title=title,
                summary=summary,
                rationale=(
                    "Deterministic capital projection rule based on immutable forecast outputs."
                ),
                severity=severity,
                status="needs_review",
                source="deterministic_rule",
                rule_id=rule_id,
                rule_version="capital-projection-v1.0.0",
                details=details,
                created_at=run.run_time,
                updated_at=run.run_time,
            ),
            CapitalProjectionFinding(
                id=uid(98800000 + run.run_number, run.case_number, finding_number),
                organization_id=DEMO_ORG_ID,
                case_id=run.case_id,
                projection_id=projection_id,
                finding_id=finding_id,
            ),
            RiskFindingEvidence(
                id=uid(98900000 + run.run_number, run.case_number, finding_number),
                organization_id=DEMO_ORG_ID,
                finding_id=finding_id,
                quote=summary,
                locator={
                    "source_type": "calculation_forecast_period",
                    "label": f"Forecast period {indicator.period_number}",
                    "source_url": (
                        f"/cases/{run.case_id}?tab=calculations#calculation-run-{run.run_id}"
                        f"-forecast-period-{indicator.period_number}"
                    ),
                    **details,
                },
                relevance=Decimal("1"),
                created_at=run.run_time,
            ),
        )
    session.flush()


def calculation_snapshot(case_key: str, case_number: int, scenario_type: str) -> dict[str, object]:
    profile = FINANCIAL_PROFILES[case_key]
    scenario_number = 1 if scenario_type == "baseline" else 2
    scenario_id = uid(97000000 + scenario_number, case_number, 1)
    assumption_values = (
        ("0.04", "0.03", 5, "0.35", "1.0")
        if scenario_type == "baseline"
        else ("-0.10", "0.10", 30, "0.80", "0.50")
    )
    keys = (
        "revenue_growth_rate",
        "expense_growth_rate",
        "cash_flow_delay_days",
        "credit_usage_rate",
        "repayment_rate",
    )
    assumptions = [
        {
            "id": str(uid(97200000 + scenario_number, case_number, index)),
            "category": category,
            "key": key,
            "label": label,
            "value": value,
            "unit": unit,
            "review_status": "reviewed",
        }
        for index, ((category, key, label, unit), value) in enumerate(
            zip(
                (
                    ("growth", keys[0], "Revenue growth", "ratio"),
                    ("expenses", keys[1], "Expense growth", "ratio"),
                    ("cash_flow_timing", keys[2], "Cash-flow delay", "days"),
                    ("credit_usage", keys[3], "Credit usage", "ratio"),
                    ("repayment_behavior", keys[4], "Repayment rate", "ratio"),
                ),
                assumption_values,
                strict=True,
            ),
            start=1,
        )
    ]
    balance_values = (
        ("cash", profile["cash"]),
        ("accounts_receivable", profile["receivables"]),
        ("inventory", profile["inventory"]),
        ("accounts_payable", profile["payables"]),
    )
    return {
        "organization_id": str(DEMO_ORG_ID),
        "case_id": str(CASE_IDS[case_key]),
        "scenario": {
            "id": str(scenario_id),
            "name": "Baseline" if scenario_type == "baseline" else "Downside — collections stress",
            "scenario_type": scenario_type,
            "assumptions": assumptions,
            "numeric_assumptions": dict(zip(keys, assumption_values, strict=True)),
        },
        "as_of_date": AS_OF_DATE.isoformat(),
        "forecast_periods": 3,
        "currency": profile["currency"],
        "reporting_period": {
            "id": str(uid(93300000, case_number, 1)),
            "period_type": "quarter",
            "start_date": "2026-04-01",
            "end_date": AS_OF_DATE.isoformat(),
            "as_of_date": AS_OF_DATE.isoformat(),
            "label": "Q2 2026 management accounts",
        },
        "balances": [
            {
                "id": str(uid(93400000, case_number, index)),
                "balance_type": balance_type,
                "amount": amount,
                "currency": profile["currency"],
            }
            for index, (balance_type, amount) in enumerate(balance_values, start=1)
        ],
        "cash_flows": [
            {
                "id": str(uid(93500000, case_number, 1)),
                "direction": "inflow",
                "amount": profile["inflows"],
                "currency": profile["currency"],
                "category": "customer receipts",
            },
            {
                "id": str(uid(93500000, case_number, 2)),
                "direction": "outflow",
                "amount": profile["outflows"],
                "currency": profile["currency"],
                "category": "operating and supplier payments",
            },
        ],
        "obligations": [
            {
                "id": str(uid(93600000, case_number, 1)),
                "principal_amount": profile["principal"],
                "outstanding_amount": profile["outstanding"],
                "currency": profile["currency"],
                "status": "active",
            }
        ],
    }


def seed_liquidity_findings(  # noqa: PLR0913
    session: Session,
    case_number: int,
    run_number: int,
    case_id: UUID,
    scenario_id: UUID,
    run_id: UUID,
    input_hash: str,
    concerns: list[dict[str, object]],
) -> None:
    for finding_number, concern in enumerate(concerns, start=1):
        finding_id = uid(98300000 + run_number, case_number, finding_number)
        rule_id = str(concern["rule_id"])
        core_insert(
            session,
            RiskFinding(
                id=finding_id,
                organization_id=DEMO_ORG_ID,
                case_id=case_id,
                risk_type="liquidity_risk",
                title=str(concern["title"]),
                summary=str(concern["summary"]),
                rationale=str(concern["rationale"]),
                severity=str(concern["severity"]),
                status="open",
                source="deterministic_rule",
                rule_id=rule_id,
                rule_version=LIQUIDITY_VERSION,
                details={
                    "liquidity": {
                        "workflow_id": "liquidity_analysis",
                        "rule_version": LIQUIDITY_VERSION,
                        "calculation_run_id": str(run_id),
                        "scenario_id": str(scenario_id),
                        "input_hash": input_hash,
                        "metrics": [],
                    }
                },
                created_at=SEEDED_AT,
                updated_at=SEEDED_AT,
            ),
        )
        period = concern["period"]
        if not isinstance(period, CalculationForecastPeriod):
            raise RuntimeError(f"Liquidity concern {rule_id} has no forecast period.")
        core_insert(
            session,
            RiskFindingEvidence(
                id=uid(98400000 + run_number, case_number, finding_number),
                organization_id=DEMO_ORG_ID,
                finding_id=finding_id,
                quote=str(concern["rationale"]),
                locator={
                    "source_type": "forecast_output",
                    "label": f"Forecast period {period.period_number}",
                    "source_url": (
                        f"/cases/{case_id}?tab=calculations#calculation-run-{run_id}"
                        f"-forecast-period-{period.period_number}"
                    ),
                    "calculation_run_id": str(run_id),
                    "forecast_period_id": str(period.id),
                    "period_number": period.period_number,
                    "period_end": period.period_end.isoformat(),
                    "input_hash": input_hash,
                },
                relevance=Decimal("1"),
                created_at=SEEDED_AT,
            ),
        )


def seed_failed_run(session: Session) -> None:
    case_number = 2
    case_id = CASE_IDS["breach"]
    scenario_id = uid(97000002, case_number, 1)
    run_time = SEEDED_AT.replace(hour=13)
    core_insert(
        session,
        CalculationRun(
            id=uid(98000003, case_number, 1),
            organization_id=DEMO_ORG_ID,
            case_id=case_id,
            scenario_id=scenario_id,
            status="failed",
            engine_version="balance-sheet-v1.0.0",
            input_schema_version="calculation-input-v1",
            output_schema_version="balance-sheet-output-v1",
            input_hash="f" * 64,
            inputs={
                "case_id": str(case_id),
                "scenario_id": str(scenario_id),
                "forecast_periods": 3,
                "as_of_date": AS_OF_DATE.isoformat(),
            },
            forecast_periods=3,
            as_of_date=AS_OF_DATE,
            started_at=run_time,
            completed_at=run_time,
            error_code="scenario_not_ready",
            error_message="The downside scenario contained an assumption awaiting review.",
            error_details={
                "missing_values": [
                    {
                        "category": "cash_flow_timing",
                        "label": "Cash-flow delay",
                        "corrective_action": (
                            "Review the cash-flow timing assumption before rerunning the forecast."
                        ),
                    }
                ],
                "corrective_action": (
                    "Open Scenarios, review the named assumption, then create a new forecast run."
                ),
            },
            created_by=DEMO_USER_ID,
            created_at=run_time,
            updated_at=run_time,
        ),
    )


def main() -> None:
    engine = create_engine(database_url(), pool_pre_ping=True)
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        session.info["organization_id"] = DEMO_ORG_ID
        reset_demo(session)
    print(
        "Demo portfolio reset complete: 4 cases, reviewed scenarios, forecasts, liquidity, "
        "and capital."
    )


if __name__ == "__main__":
    main()
