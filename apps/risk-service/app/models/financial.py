from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin, utc_now


class FinancialInstitution(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "financial_institutions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        Index("ix_financial_institutions_case_id", "case_id"),
        Index(
            "uq_financial_institutions_dedupe_key",
            "dedupe_key",
            "organization_id",
            "case_id",
            unique=True,
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_institutions_id_organization_id_case_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    institution_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reference_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class FinancialAccount(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "financial_accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        ForeignKeyConstraint(
            ["institution_id", "organization_id", "case_id"],
            [
                "financial_institutions.id",
                "financial_institutions.organization_id",
                "financial_institutions.case_id",
            ],
        ),
        CheckConstraint(
            "currency IS NULL OR (length(currency) = 3 AND upper(currency) = currency)",
            name="ck_financial_accounts_currency",
        ),
        CheckConstraint(
            "status IS NULL OR status IN ('active', 'inactive', 'closed', 'unknown')",
            name="ck_financial_accounts_status",
        ),
        Index("ix_financial_accounts_case_id", "case_id"),
        Index(
            "uq_financial_accounts_dedupe_key",
            "dedupe_key",
            "organization_id",
            "case_id",
            unique=True,
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_accounts_id_organization_id_case_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(96), nullable=False)
    institution_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    account_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_name: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class FinancialReportingPeriod(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "financial_reporting_periods"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        CheckConstraint(
            "period_type IN ('as_of', 'day', 'month', 'quarter', 'year', 'custom')",
            name="ck_financial_reporting_periods_period_type",
        ),
        Index(
            "ix_financial_reporting_periods_case_id",
            "case_id",
        ),
        Index(
            "uq_financial_reporting_periods_dedupe_key",
            "dedupe_key",
            "organization_id",
            "case_id",
            unique=True,
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_reporting_periods_id_organization_id_case_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(96), nullable=False)
    period_type: Mapped[str] = mapped_column(String(40), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class FinancialBalance(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "financial_balances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        ForeignKeyConstraint(
            ["account_id", "organization_id", "case_id"],
            [
                "financial_accounts.id",
                "financial_accounts.organization_id",
                "financial_accounts.case_id",
            ],
        ),
        ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "case_id"],
            [
                "financial_reporting_periods.id",
                "financial_reporting_periods.organization_id",
                "financial_reporting_periods.case_id",
            ],
        ),
        CheckConstraint(
            "currency IS NULL OR (length(currency) = 3 AND upper(currency) = currency)",
            name="ck_financial_balances_currency",
        ),
        Index("ix_financial_balances_case_id", "case_id"),
        Index(
            "uq_financial_balances_dedupe_key",
            "dedupe_key",
            "organization_id",
            "case_id",
            unique=True,
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_balances_id_organization_id_case_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(96), nullable=False)
    account_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reporting_period_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    balance_type: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class FinancialCashFlow(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "financial_cash_flows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        ForeignKeyConstraint(
            ["account_id", "organization_id", "case_id"],
            [
                "financial_accounts.id",
                "financial_accounts.organization_id",
                "financial_accounts.case_id",
            ],
        ),
        ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "case_id"],
            [
                "financial_reporting_periods.id",
                "financial_reporting_periods.organization_id",
                "financial_reporting_periods.case_id",
            ],
        ),
        CheckConstraint(
            "currency IS NULL OR (length(currency) = 3 AND upper(currency) = currency)",
            name="ck_financial_cash_flows_currency",
        ),
        CheckConstraint(
            "direction IN ('inflow', 'outflow')",
            name="ck_financial_cash_flows_direction",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_financial_cash_flows_amount_positive",
        ),
        CheckConstraint(
            "length(trim(category)) > 0",
            name="ck_financial_cash_flows_category",
        ),
        Index("ix_financial_cash_flows_case_id", "case_id"),
        Index(
            "uq_financial_cash_flows_dedupe_key",
            "dedupe_key",
            "organization_id",
            "case_id",
            unique=True,
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(96), nullable=False)
    account_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reporting_period_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    cash_flow_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    direction: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class FinancialObligation(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "financial_obligations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        ForeignKeyConstraint(
            ["account_id", "organization_id", "case_id"],
            [
                "financial_accounts.id",
                "financial_accounts.organization_id",
                "financial_accounts.case_id",
            ],
        ),
        ForeignKeyConstraint(
            ["institution_id", "organization_id", "case_id"],
            [
                "financial_institutions.id",
                "financial_institutions.organization_id",
                "financial_institutions.case_id",
            ],
        ),
        ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "case_id"],
            [
                "financial_reporting_periods.id",
                "financial_reporting_periods.organization_id",
                "financial_reporting_periods.case_id",
            ],
        ),
        CheckConstraint(
            "currency IS NULL OR (length(currency) = 3 AND upper(currency) = currency)",
            name="ck_financial_obligations_currency",
        ),
        CheckConstraint(
            "status IS NULL OR status IN "
            "('active', 'inactive', 'closed', 'matured', 'defaulted', 'unknown')",
            name="ck_financial_obligations_status",
        ),
        Index("ix_financial_obligations_case_id", "case_id"),
        Index(
            "uq_financial_obligations_dedupe_key",
            "dedupe_key",
            "organization_id",
            "case_id",
            unique=True,
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_obligations_id_organization_id_case_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(96), nullable=False)
    institution_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    account_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reporting_period_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    obligation_type: Mapped[str] = mapped_column(String(120), nullable=False)
    facility_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    principal_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    outstanding_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    maturity_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class FinancialSourceRow(UuidV7PrimaryKeyMixin, Base):
    __tablename__ = "financial_source_rows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        ForeignKeyConstraint(
            ["document_id", "organization_id", "case_id"],
            ["documents.id", "documents.organization_id", "documents.case_id"],
        ),
        CheckConstraint(
            "row_index IS NULL OR row_index >= 0",
            name="ck_financial_source_rows_row_index",
        ),
        Index("ix_financial_source_rows_case_id", "case_id"),
        Index(
            "uq_financial_source_rows_extraction_row",
            "document_extraction_id",
            "row_index",
            unique=True,
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_source_rows_id_organization_id_case_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    document_extraction_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_extractions.id"), nullable=True
    )
    row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    locator: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class FinancialRecordSourceLink(UuidV7PrimaryKeyMixin, Base):
    __tablename__ = "financial_record_source_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        ForeignKeyConstraint(
            ["source_row_id", "organization_id", "case_id"],
            [
                "financial_source_rows.id",
                "financial_source_rows.organization_id",
                "financial_source_rows.case_id",
            ],
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_financial_record_source_links_confidence",
        ),
        CheckConstraint(
            "record_table IN "
            "('financial_institutions', 'financial_accounts', "
            "'financial_reporting_periods', 'financial_balances', "
            "'financial_cash_flows', 'financial_obligations')",
            name="ck_financial_record_source_links_record_table",
        ),
        Index(
            "ix_financial_record_source_links_case_id",
            "case_id",
        ),
        Index(
            "uq_financial_record_source_links_field",
            "source_row_id",
            "record_id",
            "record_table",
            "field_name",
            "source_field",
            unique=True,
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    record_table: Mapped[str] = mapped_column(String(120), nullable=False)
    record_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_field: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class FinancialManualEditHistory(UuidV7PrimaryKeyMixin, Base):
    __tablename__ = "financial_manual_edit_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        ForeignKeyConstraint(
            ["edited_by", "organization_id"],
            ["users.id", "users.organization_id"],
        ),
        CheckConstraint(
            "record_table IN "
            "('financial_institutions', 'financial_accounts', "
            "'financial_reporting_periods', 'financial_balances', "
            "'financial_cash_flows', 'financial_obligations')",
            name="ck_financial_manual_edit_history_record_table",
        ),
        Index(
            "ix_financial_manual_edit_history_case_id",
            "case_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    record_table: Mapped[str] = mapped_column(String(120), nullable=False)
    record_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    field_name: Mapped[str] = mapped_column(String(120), nullable=False)
    previous_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    edited_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class FinancialValidationIssue(UuidV7PrimaryKeyMixin, Base):
    __tablename__ = "financial_validation_issues"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        CheckConstraint(
            "severity IN ('error', 'warning', 'info')",
            name="ck_financial_validation_issues_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')",
            name="ck_financial_validation_issues_status",
        ),
        CheckConstraint(
            "((record_table IS NULL AND record_id IS NULL) OR "
            "(record_table IS NOT NULL AND "
            "record_table IN ('financial_institutions', 'financial_accounts', "
            "'financial_reporting_periods', 'financial_balances', "
            "'financial_cash_flows', 'financial_obligations') AND record_id IS NOT NULL))",
            name="ck_financial_validation_issues_record_reference",
        ),
        Index(
            "uq_financial_validation_issues_current_natural_key",
            "organization_id",
            "case_id",
            "record_table",
            "record_id",
            "rule_id",
            "field_name",
            unique=True,
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    record_table: Mapped[str | None] = mapped_column(String(120), nullable=True)
    record_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    issue_key: Mapped[str] = mapped_column(String(96), nullable=False)
    field_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    severity: Mapped[Literal["error", "warning", "info"]] = mapped_column(
        String(40), nullable=False
    )
    status: Mapped[Literal["open", "resolved", "dismissed"]] = mapped_column(
        String(40), nullable=False
    )
    rule_id: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
