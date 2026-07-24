"""Corporate profile & related-party register (docs/submission_pipeline_plan.md §W4).

Mirrors the master data BoG's ORASS portal holds about each institution
(Reporting Institution Profile + Related Parties, per the ORASS LRT Portal
User Guide): general details, ownership and stock-exchange membership,
shareholders with share classes/rights, directors with allowances, key
management personnel, the external auditor with ICAG registration, ultimate
beneficial owners (individuals only, linkable to corporate shareholders),
outsourced service providers, liquidators, outlets/branches, products,
licenses, and name history.

All tables are tenant-scoped (RLS-forced) with composite FKs to
``banks(id, organization_id)``; rows enter by manual entry through the
canonical-mutation convention (non-empty reason + audit event) — never by
seeding.
"""

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
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin

PARTY_TYPES = ("individual", "legal_entity")
PARTY_STATUSES = ("active", "inactive")
RELATED_PARTY_ROLES = (
    "shareholder",
    "director",
    "board_chairman",
    "board_secretary",
    "chief_executive_officer",
    "chief_finance_officer",
    "chief_risk_officer",
    "chief_compliance_officer",
    "head_internal_audit",
    "other_key_management",
    "external_auditor",
    "ultimate_beneficial_owner",
    "outsourced_company",
    "outsourced_individual",
    "liquidator",
)
SHAREHOLDER_RIGHTS = ("voting", "non_voting")
OUTLET_TYPES = ("head_office", "branch", "agency")
OUTLET_STATUSES = ("active", "closed")
PRODUCT_STATUSES = ("proposed", "approved", "withdrawn")
LICENSE_STATUSES = ("active", "revoked", "expired")


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


class InstitutionProfile(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """1:1 bank corporate profile — the ORASS Reporting Institution Profile."""

    __tablename__ = "institution_profiles"
    __table_args__ = (
        CheckConstraint(
            "ownership_local_pct IS NULL OR "
            "(ownership_local_pct >= 0 AND ownership_local_pct <= 100)",
            name="ck_institution_profiles_ownership_local_pct",
        ),
        CheckConstraint(
            "ownership_foreign_pct IS NULL OR "
            "(ownership_foreign_pct >= 0 AND ownership_foreign_pct <= 100)",
            name="ck_institution_profiles_ownership_foreign_pct",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_institution_profiles_id_org"),
        UniqueConstraint("organization_id", "bank_id", name="uq_institution_profiles_org_bank"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    institution_type: Mapped[str] = mapped_column(String(60), nullable=False)
    legal_entity_structure: Mapped[str] = mapped_column(String(60), nullable=False)
    authorisation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_capital: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    incorporation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    tin: Mapped[str | None] = mapped_column(String(40), nullable=True)
    registration_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    orass_institution_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    traded_on_exchange: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    exchange_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ownership_local_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    ownership_foreign_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    parent_country_code: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("jurisdictions.code"), nullable=True
    )


class RelatedParty(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One natural person or legal entity in the bank's related-party register."""

    __tablename__ = "related_parties"
    __table_args__ = (
        CheckConstraint(
            f"party_type IN ({_values(PARTY_TYPES)})",
            name="ck_related_parties_party_type",
        ),
        CheckConstraint(
            f"status IN ({_values(PARTY_STATUSES)})",
            name="ck_related_parties_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_related_parties_id_org"),
        Index("ix_related_parties_org_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    party_type: Mapped[str] = mapped_column(String(16), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    regulated_elsewhere: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    regulated_jurisdiction: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active", nullable=False)


class RelatedPartyRole(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One capacity a related party acts in (director, shareholder, auditor, ...)."""

    __tablename__ = "related_party_roles"
    __table_args__ = (
        CheckConstraint(
            f"role IN ({_values(RELATED_PARTY_ROLES)})",
            name="ck_related_party_roles_role",
        ),
        ForeignKeyConstraint(
            ["party_id", "organization_id"],
            ["related_parties.id", "related_parties.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "organization_id", name="uq_related_party_roles_id_org"),
        Index("ix_related_party_roles_org_party", "organization_id", "party_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    party_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    other_responsibilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    appointed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    term_of_appointment: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sitting_allowance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    travel_allowance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    annual_fees: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    icag_registration: Mapped[str | None] = mapped_column(String(60), nullable=True)


class Shareholding(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One share-class holding of a shareholder party, optionally UBO-linked."""

    __tablename__ = "shareholdings"
    __table_args__ = (
        CheckConstraint(
            f"shareholder_rights IN ({_values(SHAREHOLDER_RIGHTS)})",
            name="ck_shareholdings_shareholder_rights",
        ),
        CheckConstraint(
            "pct_shareholding >= 0 AND pct_shareholding <= 100",
            name="ck_shareholdings_pct_shareholding",
        ),
        ForeignKeyConstraint(
            ["party_id", "organization_id"],
            ["related_parties.id", "related_parties.organization_id"],
            ondelete="CASCADE",
        ),
        # The ultimate beneficial owner is itself a (natural-person) related party.
        ForeignKeyConstraint(
            ["ubo_party_id", "organization_id"],
            ["related_parties.id", "related_parties.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_shareholdings_id_org"),
        Index("ix_shareholdings_org_party", "organization_id", "party_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    party_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    share_type: Mapped[str] = mapped_column(String(60), nullable=False)
    share_subtype: Mapped[str | None] = mapped_column(String(60), nullable=True)
    shareholder_rights: Mapped[str] = mapped_column(String(12), nullable=False)
    number_of_shares: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    pct_shareholding: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    ubo_party_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class Outlet(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One head office, branch, or agency in the bank's outlet register."""

    __tablename__ = "outlets"
    __table_args__ = (
        CheckConstraint(
            f"outlet_type IN ({_values(OUTLET_TYPES)})",
            name="ck_outlets_outlet_type",
        ),
        CheckConstraint(
            f"status IN ({_values(OUTLET_STATUSES)})",
            name="ck_outlets_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_outlets_id_org"),
        Index("ix_outlets_org_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    outlet_type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    outlet_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(12), default="active", nullable=False)
    opened_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    closed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    relocated_from: Mapped[str | None] = mapped_column(String(200), nullable=True)


class BankProduct(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One product/service in the bank's approval register."""

    __tablename__ = "bank_products"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(PRODUCT_STATUSES)})",
            name="ck_bank_products_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_bank_products_id_org"),
        Index("ix_bank_products_org_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    product_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="proposed", nullable=False)
    approval_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)


class BankLicense(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One license the bank holds (or held)."""

    __tablename__ = "bank_licenses"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(LICENSE_STATUSES)})",
            name="ck_bank_licenses_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_bank_licenses_id_org"),
        Index("ix_bank_licenses_org_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    license_name: Mapped[str] = mapped_column(String(120), nullable=False)
    license_class: Mapped[str | None] = mapped_column(String(60), nullable=True)
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active", nullable=False)


class BankNameHistory(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One prior legal/trading name of the bank."""

    __tablename__ = "bank_name_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_bank_name_history_id_org"),
        Index("ix_bank_name_history_org_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    previous_name: Mapped[str] = mapped_column(String(200), nullable=False)
    changed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
