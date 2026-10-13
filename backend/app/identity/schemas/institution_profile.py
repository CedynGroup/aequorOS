"""Corporate profile & related-party register contracts (plan W4).

Every mutation payload carries a non-empty ``reason`` (canonical-mutation
convention): the service records it in the audit trail and returns the
refreshed read model. Update payloads are full replacements; a related
party's ``roles`` list uses replace-on-write semantics.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type PartyType = Literal["individual", "legal_entity"]
type PartyStatus = Literal["active", "inactive"]
type RelatedPartyRoleCode = Literal[
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
]
type ShareholderRights = Literal["voting", "non_voting"]
type OutletType = Literal["head_office", "branch", "agency"]
type OutletStatus = Literal["active", "closed"]
type ProductStatus = Literal["proposed", "approved", "withdrawn"]
type LicenseStatus = Literal["active", "revoked", "expired"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- institution profile -----------------------------------------------------


class InstitutionProfilePut(ClosedModel):
    reason: str = Field(min_length=1, max_length=500)
    institution_type: str = Field(min_length=1, max_length=60)
    legal_entity_structure: str = Field(min_length=1, max_length=60)
    authorisation_date: date | None = None
    approved_capital: Decimal | None = Field(default=None, ge=0)
    incorporation_date: date | None = None
    tin: str | None = Field(default=None, max_length=40)
    registration_number: str | None = Field(default=None, max_length=40)
    orass_institution_code: str | None = Field(default=None, max_length=40)
    traded_on_exchange: bool = False
    exchange_name: str | None = Field(default=None, max_length=80)
    isin: str | None = Field(default=None, max_length=20)
    ownership_local_pct: Decimal | None = Field(default=None, ge=0, le=100)
    ownership_foreign_pct: Decimal | None = Field(default=None, ge=0, le=100)
    parent_country_code: str | None = Field(default=None, max_length=8)


class InstitutionProfileRead(ClosedModel):
    id: UUID
    bank_id: str
    institution_type: str
    legal_entity_structure: str
    authorisation_date: date | None
    approved_capital: Decimal | None
    incorporation_date: date | None
    tin: str | None
    registration_number: str | None
    orass_institution_code: str | None
    traded_on_exchange: bool
    exchange_name: str | None
    isin: str | None
    ownership_local_pct: Decimal | None
    ownership_foreign_pct: Decimal | None
    parent_country_code: str | None
    # Advisory only (never blocks a write): e.g. local + foreign ownership
    # percentages that do not sum to 100.
    warnings: list[str]
    created_at: datetime
    updated_at: datetime


# --- related parties ----------------------------------------------------------


class RelatedPartyRoleInput(ClosedModel):
    role: RelatedPartyRoleCode
    other_responsibilities: str | None = Field(default=None, max_length=2000)
    appointed_on: date | None = None
    term_of_appointment: str | None = Field(default=None, max_length=80)
    sitting_allowance: Decimal | None = Field(default=None, ge=0)
    travel_allowance: Decimal | None = Field(default=None, ge=0)
    annual_fees: Decimal | None = Field(default=None, ge=0)
    icag_registration: str | None = Field(default=None, max_length=60)


class RelatedPartyCreate(ClosedModel):
    reason: str = Field(min_length=1, max_length=500)
    party_type: PartyType
    full_name: str = Field(min_length=1, max_length=200)
    contact: dict[str, Any] = Field(default_factory=dict)
    regulated_elsewhere: bool = False
    regulated_jurisdiction: str | None = Field(default=None, max_length=80)
    status: PartyStatus = "active"
    roles: list[RelatedPartyRoleInput] = Field(default_factory=list)


class RelatedPartyUpdate(RelatedPartyCreate):
    """Full replacement; ``roles`` is replace-on-write."""


class RelatedPartyRoleRead(ClosedModel):
    id: UUID
    role: RelatedPartyRoleCode
    other_responsibilities: str | None
    appointed_on: date | None
    term_of_appointment: str | None
    sitting_allowance: Decimal | None
    travel_allowance: Decimal | None
    annual_fees: Decimal | None
    icag_registration: str | None


class ShareholdingCreate(ClosedModel):
    reason: str = Field(min_length=1, max_length=500)
    share_type: str = Field(min_length=1, max_length=60)
    share_subtype: str | None = Field(default=None, max_length=60)
    shareholder_rights: ShareholderRights
    number_of_shares: Decimal = Field(ge=0)
    pct_shareholding: Decimal = Field(ge=0, le=100)
    ubo_party_id: UUID | None = None


class ShareholdingUpdate(ShareholdingCreate):
    """Full replacement of one shareholding row."""


class ShareholdingRead(ClosedModel):
    id: UUID
    party_id: UUID
    share_type: str
    share_subtype: str | None
    shareholder_rights: ShareholderRights
    number_of_shares: Decimal
    pct_shareholding: Decimal
    ubo_party_id: UUID | None


class RelatedPartyRead(ClosedModel):
    id: UUID
    bank_id: str
    party_type: PartyType
    full_name: str
    contact: dict[str, Any]
    regulated_elsewhere: bool
    regulated_jurisdiction: str | None
    status: PartyStatus
    roles: list[RelatedPartyRoleRead]
    shareholdings: list[ShareholdingRead]
    created_at: datetime
    updated_at: datetime


# --- outlets, products, licenses, name history --------------------------------


class OutletCreate(ClosedModel):
    reason: str = Field(min_length=1, max_length=500)
    outlet_type: OutletType
    name: str = Field(min_length=1, max_length=120)
    outlet_number: str | None = Field(default=None, max_length=40)
    address: dict[str, Any] = Field(default_factory=dict)
    status: OutletStatus = "active"
    opened_on: date | None = None
    closed_on: date | None = None
    relocated_from: str | None = Field(default=None, max_length=200)


class OutletUpdate(OutletCreate):
    """Full replacement; closing (status='closed') stamps ``closed_on``."""


class OutletRead(ClosedModel):
    id: UUID
    bank_id: str
    outlet_type: OutletType
    name: str
    outlet_number: str | None
    address: dict[str, Any]
    status: OutletStatus
    opened_on: date | None
    closed_on: date | None
    relocated_from: str | None
    created_at: datetime
    updated_at: datetime


class BankProductCreate(ClosedModel):
    reason: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=120)
    product_type: str = Field(min_length=1, max_length=80)
    status: ProductStatus = "proposed"
    approval_reference: str | None = Field(default=None, max_length=80)


class BankProductUpdate(BankProductCreate):
    """Full replacement of one product row."""


class BankProductRead(ClosedModel):
    id: UUID
    bank_id: str
    name: str
    product_type: str
    status: ProductStatus
    approval_reference: str | None
    created_at: datetime
    updated_at: datetime


class BankLicenseCreate(ClosedModel):
    reason: str = Field(min_length=1, max_length=500)
    license_name: str = Field(min_length=1, max_length=120)
    license_class: str | None = Field(default=None, max_length=60)
    issued_on: date | None = None
    status: LicenseStatus = "active"


class BankLicenseUpdate(BankLicenseCreate):
    """Full replacement of one license row."""


class BankLicenseRead(ClosedModel):
    id: UUID
    bank_id: str
    license_name: str
    license_class: str | None
    issued_on: date | None
    status: LicenseStatus
    created_at: datetime
    updated_at: datetime


class BankNameHistoryCreate(ClosedModel):
    reason: str = Field(min_length=1, max_length=500)
    previous_name: str = Field(min_length=1, max_length=200)
    changed_on: date | None = None
    # Why the institution changed its name (stored on the record), distinct
    # from the mutation ``reason`` above (audit trail). The explicit title
    # keeps the generated client alias (NameChangeReason) clear of the
    # scenarios domain's named ChangeReason component.
    change_reason: str | None = Field(default=None, title="Name change reason")


class BankNameHistoryUpdate(BankNameHistoryCreate):
    """Full replacement of one name-history row."""


class BankNameHistoryRead(ClosedModel):
    id: UUID
    bank_id: str
    previous_name: str
    changed_on: date | None
    change_reason: str | None = Field(title="Name change reason")
    created_at: datetime
    updated_at: datetime


# --- composed read -------------------------------------------------------------


class InstitutionProfileFullRead(ClosedModel):
    """The full corporate register for one bank — the ORASS master-data mirror."""

    bank_id: str
    profile: InstitutionProfileRead | None
    related_parties: list[RelatedPartyRead]
    outlets: list[OutletRead]
    products: list[BankProductRead]
    licenses: list[BankLicenseRead]
    name_history: list[BankNameHistoryRead]
