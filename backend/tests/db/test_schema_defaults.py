"""Tests for test_schema_defaults."""

from __future__ import annotations

from typing import cast

from sqlalchemy import Table

from app.case.models.financial import (
    FinancialAccount,
    FinancialBalance,
    FinancialCashFlow,
    FinancialCovenant,
    FinancialInstitution,
    FinancialManualEditHistory,
    FinancialObligation,
    FinancialRecordSourceLink,
    FinancialReportingPeriod,
    FinancialSourceRow,
    FinancialValidationIssue,
)


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
    assert index_names(FinancialCashFlow) == {
        "ix_financial_cash_flows_case_id",
        "uq_financial_cash_flows_dedupe_key",
    }
    assert index_names(FinancialObligation) == {
        "ix_financial_obligations_case_id",
        "uq_financial_obligations_dedupe_key",
    }
    assert index_names(FinancialCovenant) == {
        "ix_financial_covenants_case_id",
        "ix_financial_covenants_obligation_id",
        "uq_financial_covenants_dedupe_key",
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
        "uq_financial_validation_issues_current_natural_key",
    }

    assert index_columns(FinancialInstitution, "ix_financial_institutions_case_id") == ("case_id",)
    assert index_columns(FinancialAccount, "ix_financial_accounts_case_id") == ("case_id",)
    assert index_columns(FinancialReportingPeriod, "ix_financial_reporting_periods_case_id") == (
        "case_id",
    )
    assert index_columns(FinancialBalance, "ix_financial_balances_case_id") == ("case_id",)
    assert index_columns(FinancialCashFlow, "ix_financial_cash_flows_case_id") == ("case_id",)
    assert index_columns(FinancialObligation, "ix_financial_obligations_case_id") == ("case_id",)
    assert index_columns(FinancialCovenant, "ix_financial_covenants_case_id") == ("case_id",)
    assert index_columns(FinancialCovenant, "ix_financial_covenants_obligation_id") == (
        "obligation_id",
    )
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
    assert index_columns(FinancialCashFlow, "uq_financial_cash_flows_dedupe_key") == (
        "dedupe_key",
        "organization_id",
        "case_id",
    )
    assert index_columns(FinancialObligation, "uq_financial_obligations_dedupe_key") == (
        "dedupe_key",
        "organization_id",
        "case_id",
    )
    assert index_columns(FinancialCovenant, "uq_financial_covenants_dedupe_key") == (
        "dedupe_key",
        "organization_id",
        "case_id",
    )
    assert index_columns(
        FinancialManualEditHistory, "ix_financial_manual_edit_history_case_id"
    ) == ("case_id",)
    assert index_columns(
        FinancialValidationIssue, "uq_financial_validation_issues_current_natural_key"
    ) == (
        "organization_id",
        "case_id",
        "record_table",
        "record_id",
        "rule_id",
        "field_name",
    )
