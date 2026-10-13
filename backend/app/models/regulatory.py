"""The bank facts spine: reporting periods and the financial facts the engines read."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
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

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin


class BankReportingPeriod(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bank_reporting_periods"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed')", name="ck_bank_reporting_periods_status"),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("bank_id", "period_end", name="uq_bank_reporting_periods_bank_period_end"),
        UniqueConstraint(
            "id", "organization_id", "bank_id", name="uq_bank_reporting_periods_id_org_bank"
        ),
        Index(
            "ix_bank_reporting_periods_org_bank_period_end",
            "organization_id",
            "bank_id",
            "period_end",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    credit_source_basis: Mapped[str | None] = mapped_column(Text, nullable=True)


class BankFinancialFact(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bank_financial_facts"
    __table_args__ = (
        CheckConstraint(
            "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
            "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
            "'deposit_behavior', 'irr_position', 'irr_swap', 'fx_position', "
            "'fx_return_history', 'fx_hedge', 'ftp_curve_point', 'ftp_product', "
            "'ftp_branch', 'ftp_nmd', 'ecl_exposure', 'crm_collateral', "
            "'provision_held', 'cashflow', 'credit_exposure')",
            name="ck_bank_financial_facts_fact_group",
        ),
        ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "reporting_period_id",
            "fact_group",
            "category",
            name="uq_bank_financial_facts_period_group_category",
        ),
        Index(
            "ix_bank_financial_facts_org_bank_period_group",
            "organization_id",
            "bank_id",
            "reporting_period_id",
            "fact_group",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    fact_group: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    # NO default (enterprise audit 2026-08-20 §6). ``default="GHS"`` here meant a
    # fact row inserted without an explicit currency silently became a cedi amount
    # — the same trap ``banks.currency`` was made mandatory to prevent, one table
    # further down. Every writer sets it: ``fact_derivation._fact`` passes
    # ``spec.currency or bank.currency``.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    risk_weight_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    hqla_level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ccf_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    income_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capital_tier: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_deduction: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
