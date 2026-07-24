"""Corporate profile & related-party register API (plan W4).

The ORASS master-data mirror for one bank: institution profile, related
parties (shareholders / directors / KMP / auditor / UBOs / outsourced
providers / liquidators) with roles and shareholdings, outlets, products,
licenses, and name history. Reads are tenant-scoped; every mutation requires
the analyst role, a non-empty ``reason``, and is audit-logged.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.institution_profile import (
    BankLicenseCreate,
    BankLicenseRead,
    BankLicenseUpdate,
    BankNameHistoryCreate,
    BankNameHistoryRead,
    BankNameHistoryUpdate,
    BankProductCreate,
    BankProductRead,
    BankProductUpdate,
    InstitutionProfileFullRead,
    InstitutionProfilePut,
    InstitutionProfileRead,
    OutletCreate,
    OutletRead,
    OutletUpdate,
    RelatedPartyCreate,
    RelatedPartyRead,
    RelatedPartyUpdate,
    ShareholdingCreate,
    ShareholdingUpdate,
)
from app.services import institution_profile

router = APIRouter(tags=["institution-profile"])


@router.get(
    "/banks/{bank_id}/institution-profile",
    response_model=InstitutionProfileFullRead,
    operation_id="getInstitutionProfile",
)
def get_institution_profile(
    bank_id: str, db: DbSession, ctx: Tenant
) -> InstitutionProfileFullRead:
    """The composed corporate register: profile + parties + outlets + more."""
    return institution_profile.get_full_profile(db, ctx, bank_id)


@router.put(
    "/banks/{bank_id}/institution-profile",
    response_model=InstitutionProfileRead,
    operation_id="putInstitutionProfile",
)
def put_institution_profile(
    bank_id: str, payload: InstitutionProfilePut, db: DbSession, ctx: MutationTenant
) -> InstitutionProfileRead:
    return institution_profile.upsert_profile(db, ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/related-parties",
    response_model=RelatedPartyRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createRelatedParty",
)
def create_related_party(
    bank_id: str, payload: RelatedPartyCreate, db: DbSession, ctx: MutationTenant
) -> RelatedPartyRead:
    return institution_profile.create_related_party(db, ctx, bank_id, payload)


@router.put(
    "/banks/{bank_id}/related-parties/{party_id}",
    response_model=RelatedPartyRead,
    operation_id="updateRelatedParty",
)
def update_related_party(
    bank_id: str,
    party_id: UUID,
    payload: RelatedPartyUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> RelatedPartyRead:
    return institution_profile.update_related_party(db, ctx, bank_id, party_id, payload)


@router.post(
    "/banks/{bank_id}/related-parties/{party_id}/shareholdings",
    response_model=RelatedPartyRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createShareholding",
)
def create_shareholding(
    bank_id: str,
    party_id: UUID,
    payload: ShareholdingCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RelatedPartyRead:
    return institution_profile.add_shareholding(db, ctx, bank_id, party_id, payload)


@router.put(
    "/banks/{bank_id}/related-parties/{party_id}/shareholdings/{shareholding_id}",
    response_model=RelatedPartyRead,
    operation_id="updateShareholding",
)
def update_shareholding(  # noqa: PLR0913
    bank_id: str,
    party_id: UUID,
    shareholding_id: UUID,
    payload: ShareholdingUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> RelatedPartyRead:
    return institution_profile.update_shareholding(
        db, ctx, bank_id, party_id, shareholding_id, payload
    )


@router.post(
    "/banks/{bank_id}/outlets",
    response_model=OutletRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createOutlet",
)
def create_outlet(
    bank_id: str, payload: OutletCreate, db: DbSession, ctx: MutationTenant
) -> OutletRead:
    return institution_profile.create_outlet(db, ctx, bank_id, payload)


@router.put(
    "/banks/{bank_id}/outlets/{outlet_id}",
    response_model=OutletRead,
    operation_id="updateOutlet",
)
def update_outlet(
    bank_id: str, outlet_id: UUID, payload: OutletUpdate, db: DbSession, ctx: MutationTenant
) -> OutletRead:
    return institution_profile.update_outlet(db, ctx, bank_id, outlet_id, payload)


@router.post(
    "/banks/{bank_id}/products",
    response_model=BankProductRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBankProduct",
)
def create_bank_product(
    bank_id: str, payload: BankProductCreate, db: DbSession, ctx: MutationTenant
) -> BankProductRead:
    return institution_profile.create_product(db, ctx, bank_id, payload)


@router.put(
    "/banks/{bank_id}/products/{product_id}",
    response_model=BankProductRead,
    operation_id="updateBankProduct",
)
def update_bank_product(
    bank_id: str,
    product_id: UUID,
    payload: BankProductUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> BankProductRead:
    return institution_profile.update_product(db, ctx, bank_id, product_id, payload)


@router.post(
    "/banks/{bank_id}/licenses",
    response_model=BankLicenseRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBankLicense",
)
def create_bank_license(
    bank_id: str, payload: BankLicenseCreate, db: DbSession, ctx: MutationTenant
) -> BankLicenseRead:
    return institution_profile.create_license(db, ctx, bank_id, payload)


@router.put(
    "/banks/{bank_id}/licenses/{license_id}",
    response_model=BankLicenseRead,
    operation_id="updateBankLicense",
)
def update_bank_license(
    bank_id: str,
    license_id: UUID,
    payload: BankLicenseUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> BankLicenseRead:
    return institution_profile.update_license(db, ctx, bank_id, license_id, payload)


@router.post(
    "/banks/{bank_id}/name-history",
    response_model=BankNameHistoryRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createNameHistoryEntry",
)
def create_name_history_entry(
    bank_id: str, payload: BankNameHistoryCreate, db: DbSession, ctx: MutationTenant
) -> BankNameHistoryRead:
    return institution_profile.create_name_history_entry(db, ctx, bank_id, payload)


@router.put(
    "/banks/{bank_id}/name-history/{entry_id}",
    response_model=BankNameHistoryRead,
    operation_id="updateNameHistoryEntry",
)
def update_name_history_entry(
    bank_id: str,
    entry_id: UUID,
    payload: BankNameHistoryUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> BankNameHistoryRead:
    return institution_profile.update_name_history_entry(db, ctx, bank_id, entry_id, payload)
