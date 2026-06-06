from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Table, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.financial import (
    FinancialAccount,
    FinancialBalance,
    FinancialInstitution,
    FinancialManualEditHistory,
    FinancialObligation,
    FinancialRecordSourceLink,
    FinancialReportingPeriod,
    FinancialSourceRow,
    FinancialValidationIssue,
)
from tests.api.helpers import ORG_1, ORG_2, USER_2


def db_uuid(session: Session, value: UUID) -> str:
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        return value.hex
    return str(value)


def normalize_json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    assert isinstance(value, dict)
    return value


def index_columns(model: type, index_name: str) -> tuple[str, ...]:
    table = cast(Table, model.__table__)
    for index in table.indexes:
        if index.name == index_name:
            return tuple(column.name for column in index.columns)
    raise AssertionError(f"Index {index_name} not found on {table.name}")


def index_names(model: type) -> set[str]:
    table = cast(Table, model.__table__)
    return {index.name or "" for index in table.indexes}


def test_financial_mapper_indexes_are_minimal_and_selective() -> None:
    assert index_names(FinancialInstitution) == {
        "ix_financial_institutions_case_id",
        "uq_financial_institutions_dedupe_key",
    }
    assert index_names(FinancialAccount) == {
        "ix_financial_accounts_case_id",
        "uq_financial_accounts_dedupe_key",
    }
    assert index_names(FinancialReportingPeriod) == {
        "ix_financial_reporting_periods_case_id",
        "uq_financial_reporting_periods_dedupe_key",
    }
    assert index_names(FinancialBalance) == {
        "ix_financial_balances_case_id",
        "uq_financial_balances_dedupe_key",
    }
    assert index_names(FinancialObligation) == {
        "ix_financial_obligations_case_id",
        "uq_financial_obligations_dedupe_key",
    }
    assert index_names(FinancialSourceRow) == {
        "ix_financial_source_rows_case_id",
        "uq_financial_source_rows_extraction_row",
    }
    assert index_names(FinancialRecordSourceLink) == {
        "ix_financial_record_source_links_case_id",
        "uq_financial_record_source_links_field",
    }
    assert index_names(FinancialManualEditHistory) == {
        "ix_financial_manual_edit_history_case_id",
    }
    assert index_names(FinancialValidationIssue) == {
        "ix_financial_validation_issues_case_id",
    }

    assert index_columns(FinancialInstitution, "ix_financial_institutions_case_id") == ("case_id",)
    assert index_columns(FinancialAccount, "ix_financial_accounts_case_id") == ("case_id",)
    assert index_columns(FinancialReportingPeriod, "ix_financial_reporting_periods_case_id") == (
        "case_id",
    )
    assert index_columns(FinancialBalance, "ix_financial_balances_case_id") == ("case_id",)
    assert index_columns(FinancialObligation, "ix_financial_obligations_case_id") == ("case_id",)
    assert index_columns(FinancialSourceRow, "ix_financial_source_rows_case_id") == ("case_id",)
    assert index_columns(FinancialSourceRow, "uq_financial_source_rows_extraction_row") == (
        "document_extraction_id",
        "row_index",
    )
    assert index_columns(FinancialRecordSourceLink, "ix_financial_record_source_links_case_id") == (
        "case_id",
    )
    assert index_columns(FinancialRecordSourceLink, "uq_financial_record_source_links_field") == (
        "source_row_id",
        "record_id",
        "record_table",
        "field_name",
        "source_field",
    )
    assert index_columns(FinancialInstitution, "uq_financial_institutions_dedupe_key") == (
        "dedupe_key",
        "organization_id",
        "case_id",
    )
    assert index_columns(FinancialAccount, "uq_financial_accounts_dedupe_key") == (
        "dedupe_key",
        "organization_id",
        "case_id",
    )
    assert index_columns(FinancialReportingPeriod, "uq_financial_reporting_periods_dedupe_key") == (
        "dedupe_key",
        "organization_id",
        "case_id",
    )
    assert index_columns(FinancialBalance, "uq_financial_balances_dedupe_key") == (
        "dedupe_key",
        "organization_id",
        "case_id",
    )
    assert index_columns(FinancialObligation, "uq_financial_obligations_dedupe_key") == (
        "dedupe_key",
        "organization_id",
        "case_id",
    )
    assert index_columns(
        FinancialManualEditHistory, "ix_financial_manual_edit_history_case_id"
    ) == ("case_id",)
    assert index_columns(FinancialValidationIssue, "ix_financial_validation_issues_case_id") == (
        "case_id",
    )


def test_financial_canonical_dedupe_keys_are_case_scoped(db_session: Session) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    first_case_id = db_uuid(db_session, uuid4())
    second_case_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:first_case_id, :org_id, 'First financial case', 'vendor', 'active', :now, :now),
              (:second_case_id, :org_id, 'Second financial case', 'vendor', 'active', :now, :now)
            """
        ),
        {
            "first_case_id": first_case_id,
            "second_case_id": second_case_id,
            "org_id": org_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_institutions
              (id, organization_id, case_id, dedupe_key, name, created_at, updated_at)
            VALUES
              (
                :first_id,
                :org_id,
                :first_case_id,
                'institution:shared-key',
                'First Bank',
                :now,
                :now
              ),
              (
                :second_id,
                :org_id,
                :second_case_id,
                'institution:shared-key',
                'Second Bank',
                :now,
                :now
              )
            """
        ),
        {
            "first_id": db_uuid(db_session, uuid4()),
            "second_id": db_uuid(db_session, uuid4()),
            "org_id": org_id,
            "first_case_id": first_case_id,
            "second_case_id": second_case_id,
            "now": now,
        },
    )
    db_session.commit()

    expect_integrity_error(
        db_session,
        """
        INSERT INTO financial_institutions
          (id, organization_id, case_id, dedupe_key, name, created_at, updated_at)
        VALUES
          (
            :id,
            :org_id,
            :first_case_id,
            'institution:shared-key',
            'Duplicate Bank',
            :now,
            :now
          )
        """,
        {
            "id": db_uuid(db_session, uuid4()),
            "org_id": org_id,
            "first_case_id": first_case_id,
            "now": now,
        },
    )


def test_phase_1_database_defaults_are_defined(db_session: Session) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    case_id = db_uuid(db_session, uuid4())
    stored_object_id = db_uuid(db_session, uuid4())
    document_id = db_uuid(db_session, uuid4())
    chunk_id = db_uuid(db_session, uuid4())
    extraction_id = db_uuid(db_session, uuid4())
    assessment_id = db_uuid(db_session, uuid4())
    run_id = db_uuid(db_session, uuid4())
    score_id = db_uuid(db_session, uuid4())
    finding_id = db_uuid(db_session, uuid4())
    decision_id = db_uuid(db_session, uuid4())
    evidence_id = db_uuid(db_session, uuid4())
    job_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:case_id, :org_id, 'Vendor case', 'vendor', 'active', :now, :now)
            """
        ),
        {"case_id": case_id, "org_id": org_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO stored_objects
              (id, organization_id, provider, bucket, object_key, status, created_at)
            VALUES
              (:stored_object_id, :org_id, 's3', 'risk-local', 'object-key', 'available', :now)
            """
        ),
        {"stored_object_id": stored_object_id, "org_id": org_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO documents
              (
                id,
                organization_id,
                case_id,
                stored_object_id,
                filename,
                status,
                created_at,
                updated_at
              )
            VALUES
              (
                :document_id,
                :org_id,
                :case_id,
                :stored_object_id,
                'financials.pdf',
                'uploaded',
                :now,
                :now
              )
            """
        ),
        {
            "document_id": document_id,
            "org_id": org_id,
            "case_id": case_id,
            "stored_object_id": stored_object_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO document_chunks
              (id, organization_id, document_id, chunk_index, text, created_at)
            VALUES
              (:chunk_id, :org_id, :document_id, 0, 'placeholder text', :now)
            """
        ),
        {"chunk_id": chunk_id, "org_id": org_id, "document_id": document_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO document_extractions
              (
                id,
                organization_id,
                document_id,
                extraction_type,
                schema_version,
                status,
                created_at
              )
            VALUES
              (
                :extraction_id,
                :org_id,
                :document_id,
                'phase_1',
                '1',
                'completed',
                :now
              )
            """
        ),
        {
            "extraction_id": extraction_id,
            "org_id": org_id,
            "document_id": document_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO risk_assessments
              (id, organization_id, case_id, name, assessment_type, status, created_at, updated_at)
            VALUES
              (
                :assessment_id,
                :org_id,
                :case_id,
                'Initial vendor risk assessment',
                'vendor_risk',
                'draft',
                :now,
                :now
              )
            """
        ),
        {"assessment_id": assessment_id, "org_id": org_id, "case_id": case_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO risk_assessment_runs
              (id, organization_id, assessment_id, status, created_at)
            VALUES
              (:run_id, :org_id, :assessment_id, 'queued', :now)
            """
        ),
        {"run_id": run_id, "org_id": org_id, "assessment_id": assessment_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO risk_findings
              (
                id,
                organization_id,
                case_id,
                assessment_id,
                run_id,
                risk_type,
                title,
                summary,
                severity,
                created_at,
                updated_at
              )
            VALUES
              (
                :finding_id,
                :org_id,
                :case_id,
                :assessment_id,
                :run_id,
                'documentation_gap',
                'Missing covenant support',
                'The file is missing covenant details.',
                'medium',
                :now,
                :now
              )
            """
        ),
        {
            "finding_id": finding_id,
            "org_id": org_id,
            "case_id": case_id,
            "assessment_id": assessment_id,
            "run_id": run_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO risk_scores
              (
                id,
                organization_id,
                case_id,
                assessment_id,
                run_id,
                score,
                risk_level,
                scoring_version,
                input_hash,
                created_at
              )
            VALUES
              (
                :score_id,
                :org_id,
                :case_id,
                :assessment_id,
                :run_id,
                25,
                'medium',
                'deterministic_v1',
                'hash',
                :now
              )
            """
        ),
        {
            "score_id": score_id,
            "org_id": org_id,
            "case_id": case_id,
            "assessment_id": assessment_id,
            "run_id": run_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO risk_finding_evidence
              (id, organization_id, finding_id, document_id, document_chunk_id, created_at)
            VALUES
              (:evidence_id, :org_id, :finding_id, :document_id, :chunk_id, :now)
            """
        ),
        {
            "evidence_id": evidence_id,
            "org_id": org_id,
            "finding_id": finding_id,
            "document_id": document_id,
            "chunk_id": chunk_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO risk_case_decisions
              (id, organization_id, case_id, decision, created_at)
            VALUES
              (:decision_id, :org_id, :case_id, 'approved', :now)
            """
        ),
        {
            "decision_id": decision_id,
            "org_id": org_id,
            "case_id": case_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO jobs
              (id, organization_id, job_type, status, queued_at)
            VALUES
              (:job_id, :org_id, 'document_parse', 'queued', :now)
            """
        ),
        {"job_id": job_id, "org_id": org_id, "now": now},
    )
    db_session.commit()

    row = db_session.execute(
        text(
            """
            SELECT
              risk_cases.metadata AS case_metadata,
              documents.source,
              documents.parse_status,
              document_chunks.metadata AS chunk_metadata,
              document_extractions.extracted_json,
              risk_assessments.input_snapshot,
              risk_assessments.config_snapshot,
              risk_assessment_runs.summary AS run_summary,
              risk_scores.input_snapshot AS score_input_snapshot,
              risk_scores.rule_results AS score_rule_results,
              risk_findings.status AS finding_status,
              risk_findings.source AS finding_source,
              risk_findings.details AS finding_details,
              risk_finding_evidence.locator,
              risk_case_decisions.previous_decision,
              jobs.attempts,
              jobs.max_attempts,
              jobs.progress
            FROM risk_cases
            JOIN documents ON documents.case_id = risk_cases.id
            JOIN document_chunks ON document_chunks.document_id = documents.id
            JOIN document_extractions ON document_extractions.document_id = documents.id
            JOIN risk_assessments ON risk_assessments.case_id = risk_cases.id
            JOIN risk_assessment_runs
              ON risk_assessment_runs.assessment_id = risk_assessments.id
            JOIN risk_scores ON risk_scores.run_id = risk_assessment_runs.id
            JOIN risk_findings ON risk_findings.run_id = risk_assessment_runs.id
            JOIN risk_finding_evidence ON risk_finding_evidence.finding_id = risk_findings.id
            JOIN risk_case_decisions ON risk_case_decisions.case_id = risk_cases.id
            JOIN jobs ON jobs.organization_id = risk_cases.organization_id
            WHERE risk_cases.id = :case_id
            """
        ),
        {"case_id": case_id},
    ).one()

    assert normalize_json(row.case_metadata) == {}
    assert row.source == "upload"
    assert row.parse_status == "not_started"
    assert normalize_json(row.chunk_metadata) == {}
    assert normalize_json(row.extracted_json) == {}
    assert normalize_json(row.input_snapshot) == {}
    assert normalize_json(row.config_snapshot) == {}
    assert normalize_json(row.run_summary) == {}
    assert normalize_json(row.score_input_snapshot) == {}
    score_rule_results = row.score_rule_results
    if isinstance(score_rule_results, str):
        score_rule_results = json.loads(score_rule_results)
    assert score_rule_results == []
    assert row.finding_status == "open"
    assert row.finding_source == "manual"
    assert normalize_json(row.finding_details) == {}
    assert normalize_json(row.locator) == {}
    assert row.previous_decision is None
    assert row.attempts == 0
    assert row.max_attempts == 3
    assert normalize_json(row.progress) == {}


def test_financial_workspace_database_defaults_are_defined(db_session: Session) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    case_id = db_uuid(db_session, uuid4())
    institution_id = db_uuid(db_session, uuid4())
    account_id = db_uuid(db_session, uuid4())
    reporting_period_id = db_uuid(db_session, uuid4())
    balance_id = db_uuid(db_session, uuid4())
    obligation_id = db_uuid(db_session, uuid4())
    source_row_id = db_uuid(db_session, uuid4())
    source_link_id = db_uuid(db_session, uuid4())
    manual_edit_id = db_uuid(db_session, uuid4())
    validation_issue_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (
                :case_id,
                :org_id,
                'Financial case',
                'financial_statement_review',
                'active',
                :now,
                :now
              )
            """
        ),
        {"case_id": case_id, "org_id": org_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_institutions
              (id, organization_id, case_id, dedupe_key, name, created_at, updated_at)
            VALUES
              (
                :institution_id,
                :org_id,
                :case_id,
                'test:institution:workspace',
                'Aequor Bank',
                :now,
                :now
              )
            """
        ),
        {"institution_id": institution_id, "org_id": org_id, "case_id": case_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_accounts
              (
                id,
                organization_id,
                case_id,
                dedupe_key,
                institution_id,
                account_name,
                created_at,
                updated_at
              )
            VALUES
              (
                :account_id,
                :org_id,
                :case_id,
                'test:account:workspace',
                :institution_id,
                'Operating Account',
                :now,
                :now
              )
            """
        ),
        {
            "account_id": account_id,
            "org_id": org_id,
            "case_id": case_id,
            "institution_id": institution_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_reporting_periods
              (id, organization_id, case_id, dedupe_key, period_type, created_at, updated_at)
            VALUES
              (
                :reporting_period_id,
                :org_id,
                :case_id,
                'test:period:workspace',
                'quarter',
                :now,
                :now
              )
            """
        ),
        {
            "reporting_period_id": reporting_period_id,
            "org_id": org_id,
            "case_id": case_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_balances
              (
                id,
                organization_id,
                case_id,
                dedupe_key,
                account_id,
                reporting_period_id,
                balance_type,
                amount,
                created_at,
                updated_at
              )
            VALUES
              (
                :balance_id,
                :org_id,
                :case_id,
                'test:balance:workspace',
                :account_id,
                :reporting_period_id,
                'cash',
                250000,
                :now,
                :now
              )
            """
        ),
        {
            "balance_id": balance_id,
            "org_id": org_id,
            "case_id": case_id,
            "account_id": account_id,
            "reporting_period_id": reporting_period_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_obligations
              (
                id,
                organization_id,
                case_id,
                dedupe_key,
                institution_id,
                account_id,
                reporting_period_id,
                obligation_type,
                created_at,
                updated_at
              )
            VALUES
              (
                :obligation_id,
                :org_id,
                :case_id,
                'test:obligation:workspace',
                :institution_id,
                :account_id,
                :reporting_period_id,
                'credit_facility',
                :now,
                :now
              )
            """
        ),
        {
            "obligation_id": obligation_id,
            "org_id": org_id,
            "case_id": case_id,
            "institution_id": institution_id,
            "account_id": account_id,
            "reporting_period_id": reporting_period_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_source_rows
              (id, organization_id, case_id, row_index, created_at)
            VALUES
              (:source_row_id, :org_id, :case_id, 0, :now)
            """
        ),
        {
            "source_row_id": source_row_id,
            "org_id": org_id,
            "case_id": case_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_record_source_links
              (id, organization_id, case_id, record_table, record_id, source_row_id, created_at)
            VALUES
              (
                :source_link_id,
                :org_id,
                :case_id,
                'financial_balances',
                :balance_id,
                :source_row_id,
                :now
              )
            """
        ),
        {
            "source_link_id": source_link_id,
            "org_id": org_id,
            "case_id": case_id,
            "balance_id": balance_id,
            "source_row_id": source_row_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_manual_edit_history
              (id, organization_id, case_id, record_table, record_id, field_name, created_at)
            VALUES
              (
                :manual_edit_id,
                :org_id,
                :case_id,
                'financial_accounts',
                :account_id,
                'account_name',
                :now
              )
            """
        ),
        {
            "manual_edit_id": manual_edit_id,
            "org_id": org_id,
            "case_id": case_id,
            "account_id": account_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_validation_issues
              (id, organization_id, case_id, severity, status, message, created_at)
            VALUES
              (:validation_issue_id, :org_id, :case_id, 'low', 'open', 'Check source.', :now)
            """
        ),
        {
            "validation_issue_id": validation_issue_id,
            "org_id": org_id,
            "case_id": case_id,
            "now": now,
        },
    )
    db_session.commit()

    row = db_session.execute(
        text(
            """
            SELECT
              financial_institutions.metadata AS institution_metadata,
              financial_accounts.metadata AS account_metadata,
              financial_reporting_periods.metadata AS period_metadata,
              financial_balances.metadata AS balance_metadata,
              financial_obligations.details AS obligation_details,
              financial_source_rows.locator,
              financial_source_rows.raw_payload,
              financial_record_source_links.metadata AS source_link_metadata,
              financial_manual_edit_history.previous_value,
              financial_manual_edit_history.new_value,
              financial_validation_issues.details AS validation_details
            FROM financial_institutions
            JOIN financial_accounts
              ON financial_accounts.institution_id = financial_institutions.id
            JOIN financial_reporting_periods
              ON financial_reporting_periods.case_id = financial_institutions.case_id
            JOIN financial_balances
              ON financial_balances.account_id = financial_accounts.id
            JOIN financial_obligations
              ON financial_obligations.account_id = financial_accounts.id
            JOIN financial_source_rows
              ON financial_source_rows.case_id = financial_institutions.case_id
            JOIN financial_record_source_links
              ON financial_record_source_links.source_row_id = financial_source_rows.id
            JOIN financial_manual_edit_history
              ON financial_manual_edit_history.record_id = financial_accounts.id
            JOIN financial_validation_issues
              ON financial_validation_issues.case_id = financial_institutions.case_id
            WHERE financial_institutions.id = :institution_id
            """
        ),
        {"institution_id": institution_id},
    ).one()

    assert normalize_json(row.institution_metadata) == {}
    assert normalize_json(row.account_metadata) == {}
    assert normalize_json(row.period_metadata) == {}
    assert normalize_json(row.balance_metadata) == {}
    assert normalize_json(row.obligation_details) == {}
    assert normalize_json(row.locator) == {}
    assert normalize_json(row.raw_payload) == {}
    assert normalize_json(row.source_link_metadata) == {}
    assert row.previous_value is None
    assert row.new_value is None
    assert normalize_json(row.validation_details) == {}


def test_financial_workspace_allows_null_optional_relationships(db_session: Session) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    case_id = db_uuid(db_session, uuid4())
    balance_id = db_uuid(db_session, uuid4())
    obligation_id = db_uuid(db_session, uuid4())
    source_row_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:case_id, :org_id, 'Financial case', 'vendor', 'active', :now, :now)
            """
        ),
        {"case_id": case_id, "org_id": org_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_balances
              (
                id,
                organization_id,
                case_id,
                dedupe_key,
                account_id,
                reporting_period_id,
                balance_type,
                amount,
                created_at,
                updated_at
              )
            VALUES
              (
                :balance_id,
                :org_id,
                :case_id,
                'test:balance:nullable-links',
                NULL,
                NULL,
                'cash',
                100,
                :now,
                :now
              )
            """
        ),
        {"balance_id": balance_id, "org_id": org_id, "case_id": case_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_obligations
              (
                id,
                organization_id,
                case_id,
                dedupe_key,
                institution_id,
                account_id,
                reporting_period_id,
                obligation_type,
                created_at,
                updated_at
              )
            VALUES
              (
                :obligation_id,
                :org_id,
                :case_id,
                'test:obligation:nullable-links',
                NULL,
                NULL,
                NULL,
                'lease',
                :now,
                :now
              )
            """
        ),
        {"obligation_id": obligation_id, "org_id": org_id, "case_id": case_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_source_rows
              (id, organization_id, case_id, document_id, row_index, created_at)
            VALUES
              (:source_row_id, :org_id, :case_id, NULL, NULL, :now)
            """
        ),
        {"source_row_id": source_row_id, "org_id": org_id, "case_id": case_id, "now": now},
    )
    db_session.commit()

    count = db_session.execute(
        text(
            """
            SELECT
              (
                SELECT count(*)
                FROM financial_balances
                WHERE id = :balance_id
                  AND account_id IS NULL
                  AND reporting_period_id IS NULL
              ) +
              (
                SELECT count(*)
                FROM financial_obligations
                WHERE id = :obligation_id
                  AND institution_id IS NULL
                  AND account_id IS NULL
                  AND reporting_period_id IS NULL
              ) +
              (
                SELECT count(*)
                FROM financial_source_rows
                WHERE id = :source_row_id
                  AND document_id IS NULL
              ) AS inserted_count
            """
        ),
        {
            "balance_id": balance_id,
            "obligation_id": obligation_id,
            "source_row_id": source_row_id,
        },
    ).scalar_one()

    assert count == 3


def test_financial_workspace_rejects_cross_tenant_case_links(db_session: Session) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    other_org_id = db_uuid(db_session, ORG_2)
    other_case_id = db_uuid(db_session, uuid4())
    institution_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:other_case_id, :other_org_id, 'Other tenant case', 'vendor', 'active', :now, :now)
            """
        ),
        {"other_case_id": other_case_id, "other_org_id": other_org_id, "now": now},
    )

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO financial_institutions
                  (id, organization_id, case_id, dedupe_key, name, created_at, updated_at)
                VALUES
                  (
                    :institution_id,
                    :org_id,
                    :other_case_id,
                    'test:institution:wrong-tenant',
                    'Wrong Tenant Bank',
                    :now,
                    :now
                  )
                """
            ),
            {
                "institution_id": institution_id,
                "org_id": org_id,
                "other_case_id": other_case_id,
                "now": now,
            },
        )
        db_session.flush()

    db_session.rollback()


def test_financial_workspace_rejects_cross_case_parent_links(db_session: Session) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    source_case_id = db_uuid(db_session, uuid4())
    target_case_id = db_uuid(db_session, uuid4())
    institution_id = db_uuid(db_session, uuid4())
    account_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:source_case_id, :org_id, 'Source case', 'vendor', 'active', :now, :now),
              (:target_case_id, :org_id, 'Target case', 'vendor', 'active', :now, :now)
            """
        ),
        {
            "source_case_id": source_case_id,
            "target_case_id": target_case_id,
            "org_id": org_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_institutions
              (id, organization_id, case_id, dedupe_key, name, created_at, updated_at)
            VALUES
              (
                :institution_id,
                :org_id,
                :source_case_id,
                'test:institution:source-case',
                'Source Bank',
                :now,
                :now
              )
            """
        ),
        {
            "institution_id": institution_id,
            "org_id": org_id,
            "source_case_id": source_case_id,
            "now": now,
        },
    )

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO financial_accounts
                  (
                    id,
                    organization_id,
                    case_id,
                    dedupe_key,
                    institution_id,
                    account_name,
                    created_at,
                    updated_at
                  )
                VALUES
                  (
                    :account_id,
                    :org_id,
                    :target_case_id,
                    'test:account:cross-case-parent',
                    :institution_id,
                    'Cross-case account',
                    :now,
                    :now
                  )
                """
            ),
            {
                "account_id": account_id,
                "org_id": org_id,
                "target_case_id": target_case_id,
                "institution_id": institution_id,
                "now": now,
            },
        )
        db_session.flush()

    db_session.rollback()


def test_financial_source_rows_reject_cross_case_documents(db_session: Session) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    source_case_id = db_uuid(db_session, uuid4())
    target_case_id = db_uuid(db_session, uuid4())
    stored_object_id = db_uuid(db_session, uuid4())
    document_id = db_uuid(db_session, uuid4())
    source_row_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:source_case_id, :org_id, 'Source case', 'vendor', 'active', :now, :now),
              (:target_case_id, :org_id, 'Target case', 'vendor', 'active', :now, :now)
            """
        ),
        {
            "source_case_id": source_case_id,
            "target_case_id": target_case_id,
            "org_id": org_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO stored_objects
              (id, organization_id, provider, bucket, object_key, status, created_at)
            VALUES
              (:stored_object_id, :org_id, 's3', 'risk-local', 'object-key', 'available', :now)
            """
        ),
        {"stored_object_id": stored_object_id, "org_id": org_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO documents
              (
                id,
                organization_id,
                case_id,
                stored_object_id,
                filename,
                status,
                created_at,
                updated_at
              )
            VALUES
              (
                :document_id,
                :org_id,
                :source_case_id,
                :stored_object_id,
                'financials.pdf',
                'uploaded',
                :now,
                :now
              )
            """
        ),
        {
            "document_id": document_id,
            "org_id": org_id,
            "source_case_id": source_case_id,
            "stored_object_id": stored_object_id,
            "now": now,
        },
    )

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO financial_source_rows
                  (id, organization_id, case_id, document_id, row_index, created_at)
                VALUES
                  (:source_row_id, :org_id, :target_case_id, :document_id, 0, :now)
                """
            ),
            {
                "source_row_id": source_row_id,
                "org_id": org_id,
                "target_case_id": target_case_id,
                "document_id": document_id,
                "now": now,
            },
        )
        db_session.flush()

    db_session.rollback()


def test_financial_manual_edits_reject_cross_tenant_editors(db_session: Session) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    case_id = db_uuid(db_session, uuid4())
    account_id = db_uuid(db_session, uuid4())
    manual_edit_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:case_id, :org_id, 'Financial case', 'vendor', 'active', :now, :now)
            """
        ),
        {"case_id": case_id, "org_id": org_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_accounts
              (id, organization_id, case_id, dedupe_key, account_name, created_at, updated_at)
            VALUES
              (
                :account_id,
                :org_id,
                :case_id,
                'test:account:manual-edit',
                'Operating Account',
                :now,
                :now
              )
            """
        ),
        {"account_id": account_id, "org_id": org_id, "case_id": case_id, "now": now},
    )

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO financial_manual_edit_history
                  (
                    id,
                    organization_id,
                    case_id,
                    record_table,
                    record_id,
                    field_name,
                    edited_by,
                    created_at
                  )
                VALUES
                  (
                    :manual_edit_id,
                    :org_id,
                    :case_id,
                    'financial_accounts',
                    :account_id,
                    'account_name',
                    :wrong_tenant_user_id,
                    :now
                  )
                """
            ),
            {
                "manual_edit_id": manual_edit_id,
                "org_id": org_id,
                "case_id": case_id,
                "account_id": account_id,
                "wrong_tenant_user_id": db_uuid(db_session, USER_2),
                "now": now,
            },
        )
        db_session.flush()

    db_session.rollback()


def test_financial_source_rows_reject_unknown_document_extractions(db_session: Session) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    case_id = db_uuid(db_session, uuid4())
    source_row_id = db_uuid(db_session, uuid4())
    missing_extraction_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:case_id, :org_id, 'Financial case', 'vendor', 'active', :now, :now)
            """
        ),
        {"case_id": case_id, "org_id": org_id, "now": now},
    )

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO financial_source_rows
                  (
                    id,
                    organization_id,
                    case_id,
                    document_extraction_id,
                    row_index,
                    created_at
                  )
                VALUES
                  (:source_row_id, :org_id, :case_id, :missing_extraction_id, 0, :now)
                """
            ),
            {
                "source_row_id": source_row_id,
                "org_id": org_id,
                "case_id": case_id,
                "missing_extraction_id": missing_extraction_id,
                "now": now,
            },
        )
        db_session.flush()

    db_session.rollback()


def test_financial_record_source_links_reject_cross_tenant_source_rows(
    db_session: Session,
) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    other_org_id = db_uuid(db_session, ORG_2)
    case_id = db_uuid(db_session, uuid4())
    other_case_id = db_uuid(db_session, uuid4())
    other_source_row_id = db_uuid(db_session, uuid4())
    source_link_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:case_id, :org_id, 'Financial case', 'vendor', 'active', :now, :now),
              (:other_case_id, :other_org_id, 'Other case', 'vendor', 'active', :now, :now)
            """
        ),
        {
            "case_id": case_id,
            "org_id": org_id,
            "other_case_id": other_case_id,
            "other_org_id": other_org_id,
            "now": now,
        },
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_source_rows
              (id, organization_id, case_id, row_index, created_at)
            VALUES
              (:other_source_row_id, :other_org_id, :other_case_id, 0, :now)
            """
        ),
        {
            "other_source_row_id": other_source_row_id,
            "other_org_id": other_org_id,
            "other_case_id": other_case_id,
            "now": now,
        },
    )

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                """
                INSERT INTO financial_record_source_links
                  (id, organization_id, case_id, record_table, record_id, source_row_id, created_at)
                VALUES
                  (
                    :source_link_id,
                    :org_id,
                    :case_id,
                    'financial_balances',
                    :record_id,
                    :other_source_row_id,
                    :now
                  )
                """
            ),
            {
                "source_link_id": source_link_id,
                "org_id": org_id,
                "case_id": case_id,
                "record_id": db_uuid(db_session, uuid4()),
                "other_source_row_id": other_source_row_id,
                "now": now,
            },
        )
        db_session.flush()

    db_session.rollback()


def test_financial_workspace_rejects_invalid_domain_values(db_session: Session) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    case_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:case_id, :org_id, 'Financial case', 'vendor', 'active', :now, :now)
            """
        ),
        {"case_id": case_id, "org_id": org_id, "now": now},
    )
    db_session.commit()

    expect_integrity_error(
        db_session,
        """
        INSERT INTO financial_reporting_periods
          (id, organization_id, case_id, dedupe_key, period_type, created_at, updated_at)
        VALUES
          (:id, :org_id, :case_id, 'test:period:invalid-domain', 'decade', :now, :now)
        """,
        {"id": db_uuid(db_session, uuid4()), "org_id": org_id, "case_id": case_id, "now": now},
    )
    expect_integrity_error(
        db_session,
        """
        INSERT INTO financial_accounts
          (id, organization_id, case_id, dedupe_key, account_name, currency, created_at, updated_at)
        VALUES
          (
            :id,
            :org_id,
            :case_id,
            'test:account:invalid-currency',
            'Operating Account',
            'usd',
            :now,
            :now
          )
        """,
        {"id": db_uuid(db_session, uuid4()), "org_id": org_id, "case_id": case_id, "now": now},
    )
    expect_integrity_error(
        db_session,
        """
        INSERT INTO financial_accounts
          (id, organization_id, case_id, dedupe_key, account_name, status, created_at, updated_at)
        VALUES
          (
            :id,
            :org_id,
            :case_id,
            'test:account:invalid-status',
            'Operating Account',
            'pending',
            :now,
            :now
          )
        """,
        {"id": db_uuid(db_session, uuid4()), "org_id": org_id, "case_id": case_id, "now": now},
    )
    expect_integrity_error(
        db_session,
        """
        INSERT INTO financial_balances
          (
            id,
            organization_id,
            case_id,
            dedupe_key,
            balance_type,
            amount,
            currency,
            created_at,
            updated_at
          )
        VALUES
          (:id, :org_id, :case_id, 'test:balance:invalid-currency', 'cash', 100, 'US', :now, :now)
        """,
        {"id": db_uuid(db_session, uuid4()), "org_id": org_id, "case_id": case_id, "now": now},
    )
    expect_integrity_error(
        db_session,
        """
        INSERT INTO financial_obligations
          (
            id,
            organization_id,
            case_id,
            dedupe_key,
            obligation_type,
            status,
            created_at,
            updated_at
          )
        VALUES
          (:id, :org_id, :case_id, 'test:obligation:invalid-status', 'lease', 'pending', :now, :now)
        """,
        {"id": db_uuid(db_session, uuid4()), "org_id": org_id, "case_id": case_id, "now": now},
    )


def test_financial_support_records_reject_invalid_record_references(
    db_session: Session,
) -> None:
    now = datetime.now(UTC).isoformat()
    org_id = db_uuid(db_session, ORG_1)
    case_id = db_uuid(db_session, uuid4())
    source_row_id = db_uuid(db_session, uuid4())

    db_session.execute(
        text(
            """
            INSERT INTO risk_cases
              (id, organization_id, title, case_type, status, created_at, updated_at)
            VALUES
              (:case_id, :org_id, 'Financial case', 'vendor', 'active', :now, :now)
            """
        ),
        {"case_id": case_id, "org_id": org_id, "now": now},
    )
    db_session.execute(
        text(
            """
            INSERT INTO financial_source_rows
              (id, organization_id, case_id, created_at)
            VALUES
              (:source_row_id, :org_id, :case_id, :now)
            """
        ),
        {"source_row_id": source_row_id, "org_id": org_id, "case_id": case_id, "now": now},
    )
    db_session.commit()

    expect_integrity_error(
        db_session,
        """
        INSERT INTO financial_record_source_links
          (id, organization_id, case_id, record_table, record_id, source_row_id, created_at)
        VALUES
          (
            :id,
            :org_id,
            :case_id,
            'financial_source_rows',
            :record_id,
            :source_row_id,
            :now
          )
        """,
        {
            "id": db_uuid(db_session, uuid4()),
            "org_id": org_id,
            "case_id": case_id,
            "record_id": db_uuid(db_session, uuid4()),
            "source_row_id": source_row_id,
            "now": now,
        },
    )
    expect_integrity_error(
        db_session,
        """
        INSERT INTO financial_manual_edit_history
          (id, organization_id, case_id, record_table, record_id, field_name, created_at)
        VALUES
          (
            :id,
            :org_id,
            :case_id,
            'financial_source_rows',
            :record_id,
            'row_index',
            :now
          )
        """,
        {
            "id": db_uuid(db_session, uuid4()),
            "org_id": org_id,
            "case_id": case_id,
            "record_id": db_uuid(db_session, uuid4()),
            "now": now,
        },
    )
    expect_integrity_error(
        db_session,
        """
        INSERT INTO financial_validation_issues
          (id, organization_id, case_id, record_id, severity, status, message, created_at)
        VALUES
          (:id, :org_id, :case_id, :record_id, 'low', 'open', 'Missing table.', :now)
        """,
        {
            "id": db_uuid(db_session, uuid4()),
            "org_id": org_id,
            "case_id": case_id,
            "record_id": db_uuid(db_session, uuid4()),
            "now": now,
        },
    )
    expect_integrity_error(
        db_session,
        """
        INSERT INTO financial_validation_issues
          (id, organization_id, case_id, record_table, severity, status, message, created_at)
        VALUES
          (:id, :org_id, :case_id, 'financial_balances', 'low', 'open', 'Missing ID.', :now)
        """,
        {"id": db_uuid(db_session, uuid4()), "org_id": org_id, "case_id": case_id, "now": now},
    )


def expect_integrity_error(
    db_session: Session,
    sql: str,
    params: dict[str, Any],
) -> None:
    with pytest.raises(IntegrityError):
        db_session.execute(text(sql), params)
        db_session.flush()
    db_session.rollback()
