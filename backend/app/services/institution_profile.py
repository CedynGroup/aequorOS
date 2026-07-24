"""Corporate profile & related-party register service (plan W4).

The register mirrors the master data the regulator's portal holds about each
institution (ORASS Reporting Institution Profile + Related Parties). Every
mutation follows the canonical-mutation convention: a non-empty ``reason`` in
the payload, an audit event recording it, and the refreshed read model in the
response — no silent writes. Rows enter by manual entry or Data Engine
templates, never by seeding.

Validation:
- an ultimate-beneficial-owner link must reference an *individual* related
  party (UBOs are natural persons; they may be linked from corporate
  shareholders' holdings) — 409 otherwise;
- ownership percentages are range-checked 0..100; a local+foreign split that
  does not sum to 100 is surfaced as an advisory warning, never an error;
- ``parent_country_code`` must exist in the jurisdictions registry.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import (
    Bank,
    BankLicense,
    BankNameHistory,
    BankProduct,
    InstitutionProfile,
    Jurisdiction,
    Outlet,
    RelatedParty,
    RelatedPartyRole,
    Shareholding,
)
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
    RelatedPartyRoleRead,
    RelatedPartyUpdate,
    ShareholdingCreate,
    ShareholdingRead,
    ShareholdingUpdate,
)
from app.services.audit import record_event

_OWNERSHIP_SUM_TOLERANCE = Decimal("0.01")


# --- shared lookups ------------------------------------------------------------


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_party_or_404(
    db: Session, ctx: TenantContext, bank_id: UUID, party_id: UUID
) -> RelatedParty:
    party = db.scalar(
        select(RelatedParty).where(
            RelatedParty.id == party_id,
            RelatedParty.organization_id == ctx.organization_id,
            RelatedParty.bank_id == bank_id,
        )
    )
    if party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Related party not found."
        )
    return party


def _profile_row(db: Session, ctx: TenantContext, bank_id: UUID) -> InstitutionProfile | None:
    return db.scalar(
        select(InstitutionProfile).where(
            InstitutionProfile.organization_id == ctx.organization_id,
            InstitutionProfile.bank_id == bank_id,
        )
    )


def _require_jurisdiction(db: Session, code: str) -> None:
    exists = db.scalar(select(Jurisdiction.code).where(Jurisdiction.code == code))
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Jurisdiction code '{code}' is not in the registry.",
        )


def _require_individual_ubo(
    db: Session, ctx: TenantContext, bank_id: UUID, ubo_party_id: UUID
) -> None:
    """UBOs are natural persons: the linked party must exist and be an individual."""
    ubo = db.scalar(
        select(RelatedParty).where(
            RelatedParty.id == ubo_party_id,
            RelatedParty.organization_id == ctx.organization_id,
            RelatedParty.bank_id == bank_id,
        )
    )
    if ubo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ultimate-beneficial-owner party not found.",
        )
    if ubo.party_type != "individual":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "An ultimate beneficial owner must be an individual related "
                f"party; '{ubo.full_name}' is a legal entity."
            ),
        )


# --- read builders ---------------------------------------------------------------


def _profile_warnings(profile: InstitutionProfile) -> list[str]:
    warnings: list[str] = []
    local = profile.ownership_local_pct
    foreign = profile.ownership_foreign_pct
    if (
        local is not None
        and foreign is not None
        and abs((local + foreign) - Decimal(100)) > _OWNERSHIP_SUM_TOLERANCE
    ):
        warnings.append(
            f"Local and foreign ownership percentages sum to {local + foreign} (expected 100)."
        )
    return warnings


def _profile_read(profile: InstitutionProfile) -> InstitutionProfileRead:
    return InstitutionProfileRead(
        id=profile.id,
        bank_id=profile.bank_id,
        institution_type=profile.institution_type,
        legal_entity_structure=profile.legal_entity_structure,
        authorisation_date=profile.authorisation_date,
        approved_capital=profile.approved_capital,
        incorporation_date=profile.incorporation_date,
        tin=profile.tin,
        registration_number=profile.registration_number,
        orass_institution_code=profile.orass_institution_code,
        traded_on_exchange=profile.traded_on_exchange,
        exchange_name=profile.exchange_name,
        isin=profile.isin,
        ownership_local_pct=profile.ownership_local_pct,
        ownership_foreign_pct=profile.ownership_foreign_pct,
        parent_country_code=profile.parent_country_code,
        warnings=_profile_warnings(profile),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _role_read(role: RelatedPartyRole) -> RelatedPartyRoleRead:
    return RelatedPartyRoleRead(
        id=role.id,
        role=role.role,  # type: ignore[arg-type]
        other_responsibilities=role.other_responsibilities,
        appointed_on=role.appointed_on,
        term_of_appointment=role.term_of_appointment,
        sitting_allowance=role.sitting_allowance,
        travel_allowance=role.travel_allowance,
        annual_fees=role.annual_fees,
        icag_registration=role.icag_registration,
    )


def _shareholding_read(holding: Shareholding) -> ShareholdingRead:
    return ShareholdingRead(
        id=holding.id,
        party_id=holding.party_id,
        share_type=holding.share_type,
        share_subtype=holding.share_subtype,
        shareholder_rights=holding.shareholder_rights,  # type: ignore[arg-type]
        number_of_shares=holding.number_of_shares,
        pct_shareholding=holding.pct_shareholding,
        ubo_party_id=holding.ubo_party_id,
    )


def _party_children(
    db: Session, party_ids: list[UUID]
) -> tuple[dict[UUID, list[RelatedPartyRole]], dict[UUID, list[Shareholding]]]:
    roles_by_party: dict[UUID, list[RelatedPartyRole]] = {pid: [] for pid in party_ids}
    holdings_by_party: dict[UUID, list[Shareholding]] = {pid: [] for pid in party_ids}
    if not party_ids:
        return roles_by_party, holdings_by_party
    roles = db.scalars(
        select(RelatedPartyRole)
        .where(RelatedPartyRole.party_id.in_(party_ids))
        .order_by(RelatedPartyRole.role, RelatedPartyRole.id)
    )
    for role in roles:
        roles_by_party[role.party_id].append(role)
    holdings = db.scalars(
        select(Shareholding)
        .where(Shareholding.party_id.in_(party_ids))
        .order_by(Shareholding.created_at, Shareholding.id)
    )
    for holding in holdings:
        holdings_by_party[holding.party_id].append(holding)
    return roles_by_party, holdings_by_party


def _party_read(
    party: RelatedParty,
    roles: list[RelatedPartyRole],
    shareholdings: list[Shareholding],
) -> RelatedPartyRead:
    return RelatedPartyRead(
        id=party.id,
        bank_id=party.bank_id,
        party_type=party.party_type,  # type: ignore[arg-type]
        full_name=party.full_name,
        contact=party.contact,
        regulated_elsewhere=party.regulated_elsewhere,
        regulated_jurisdiction=party.regulated_jurisdiction,
        status=party.status,  # type: ignore[arg-type]
        roles=[_role_read(role) for role in roles],
        shareholdings=[_shareholding_read(holding) for holding in shareholdings],
        created_at=party.created_at,
        updated_at=party.updated_at,
    )


def _read_party(db: Session, party: RelatedParty) -> RelatedPartyRead:
    roles_by_party, holdings_by_party = _party_children(db, [party.id])
    return _party_read(party, roles_by_party[party.id], holdings_by_party[party.id])


def _outlet_read(outlet: Outlet) -> OutletRead:
    return OutletRead(
        id=outlet.id,
        bank_id=outlet.bank_id,
        outlet_type=outlet.outlet_type,  # type: ignore[arg-type]
        name=outlet.name,
        outlet_number=outlet.outlet_number,
        address=outlet.address,
        status=outlet.status,  # type: ignore[arg-type]
        opened_on=outlet.opened_on,
        closed_on=outlet.closed_on,
        relocated_from=outlet.relocated_from,
        created_at=outlet.created_at,
        updated_at=outlet.updated_at,
    )


def _product_read(product: BankProduct) -> BankProductRead:
    return BankProductRead(
        id=product.id,
        bank_id=product.bank_id,
        name=product.name,
        product_type=product.product_type,
        status=product.status,  # type: ignore[arg-type]
        approval_reference=product.approval_reference,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _license_read(license_row: BankLicense) -> BankLicenseRead:
    return BankLicenseRead(
        id=license_row.id,
        bank_id=license_row.bank_id,
        license_name=license_row.license_name,
        license_class=license_row.license_class,
        issued_on=license_row.issued_on,
        status=license_row.status,  # type: ignore[arg-type]
        created_at=license_row.created_at,
        updated_at=license_row.updated_at,
    )


def _name_history_read(entry: BankNameHistory) -> BankNameHistoryRead:
    return BankNameHistoryRead(
        id=entry.id,
        bank_id=entry.bank_id,
        previous_name=entry.previous_name,
        changed_on=entry.changed_on,
        change_reason=entry.reason,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _bank_children[T](db: Session, model: type[T], ctx: TenantContext, bank_id: UUID) -> list[T]:
    return list(
        db.scalars(
            select(model)
            .where(
                model.organization_id == ctx.organization_id,  # type: ignore[attr-defined]
                model.bank_id == bank_id,  # type: ignore[attr-defined]
            )
            .order_by(model.created_at, model.id)  # type: ignore[attr-defined]
        )
    )


# --- composed read ---------------------------------------------------------------


def get_full_profile(db: Session, ctx: TenantContext, bank_id: UUID) -> InstitutionProfileFullRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    profile = _profile_row(db, ctx, bank.id)
    parties = _bank_children(db, RelatedParty, ctx, bank.id)
    roles_by_party, holdings_by_party = _party_children(db, [party.id for party in parties])
    return InstitutionProfileFullRead(
        bank_id=bank.id,
        profile=_profile_read(profile) if profile is not None else None,
        related_parties=[
            _party_read(party, roles_by_party[party.id], holdings_by_party[party.id])
            for party in parties
        ],
        outlets=[_outlet_read(row) for row in _bank_children(db, Outlet, ctx, bank.id)],
        products=[_product_read(row) for row in _bank_children(db, BankProduct, ctx, bank.id)],
        licenses=[_license_read(row) for row in _bank_children(db, BankLicense, ctx, bank.id)],
        name_history=[
            _name_history_read(row) for row in _bank_children(db, BankNameHistory, ctx, bank.id)
        ],
    )


# --- institution profile ----------------------------------------------------------


def upsert_profile(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: InstitutionProfilePut
) -> InstitutionProfileRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    if payload.parent_country_code is not None:
        _require_jurisdiction(db, payload.parent_country_code)
    profile = _profile_row(db, ctx, bank.id)
    created = profile is None
    if profile is None:
        profile = InstitutionProfile(
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            institution_type=payload.institution_type,
            legal_entity_structure=payload.legal_entity_structure,
        )
        db.add(profile)
    profile.institution_type = payload.institution_type
    profile.legal_entity_structure = payload.legal_entity_structure
    profile.authorisation_date = payload.authorisation_date
    profile.approved_capital = payload.approved_capital
    profile.incorporation_date = payload.incorporation_date
    profile.tin = payload.tin
    profile.registration_number = payload.registration_number
    profile.orass_institution_code = payload.orass_institution_code
    profile.traded_on_exchange = payload.traded_on_exchange
    profile.exchange_name = payload.exchange_name
    profile.isin = payload.isin
    profile.ownership_local_pct = payload.ownership_local_pct
    profile.ownership_foreign_pct = payload.ownership_foreign_pct
    profile.parent_country_code = payload.parent_country_code
    db.flush()
    record_event(
        db,
        ctx,
        event_type="institution_profile.created" if created else "institution_profile.updated",
        entity_type="institution_profile",
        entity_id=profile.id,
        details={
            "bank_id": str(bank.id),
            "reason": payload.reason,
            "orass_institution_code": payload.orass_institution_code,
        },
    )
    db.commit()
    return _profile_read(profile)


# --- related parties ----------------------------------------------------------------


def _apply_party_fields(
    party: RelatedParty, payload: RelatedPartyCreate | RelatedPartyUpdate
) -> None:
    party.party_type = payload.party_type
    party.full_name = payload.full_name
    party.contact = payload.contact
    party.regulated_elsewhere = payload.regulated_elsewhere
    party.regulated_jurisdiction = payload.regulated_jurisdiction
    party.status = payload.status


def _validate_party_rules(
    db: Session,
    ctx: TenantContext,
    payload: RelatedPartyCreate | RelatedPartyUpdate,
    party_id: UUID | None,
) -> None:
    if payload.party_type != "individual" and any(
        role.role == "ultimate_beneficial_owner" for role in payload.roles
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only an individual related party can hold the ultimate-beneficial-owner role.",
        )
    if payload.party_type != "individual" and party_id is not None:
        linked = db.scalar(
            select(Shareholding.id).where(
                Shareholding.organization_id == ctx.organization_id,
                Shareholding.ubo_party_id == party_id,
            )
        )
        if linked is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This party is linked as an ultimate beneficial owner on a "
                    "shareholding and must remain an individual."
                ),
            )


def _replace_roles(
    db: Session, ctx: TenantContext, party: RelatedParty, payload: RelatedPartyCreate
) -> None:
    for existing in db.scalars(
        select(RelatedPartyRole).where(
            RelatedPartyRole.organization_id == ctx.organization_id,
            RelatedPartyRole.party_id == party.id,
        )
    ):
        db.delete(existing)
    for role_input in payload.roles:
        db.add(
            RelatedPartyRole(
                organization_id=ctx.organization_id,
                party_id=party.id,
                role=role_input.role,
                other_responsibilities=role_input.other_responsibilities,
                appointed_on=role_input.appointed_on,
                term_of_appointment=role_input.term_of_appointment,
                sitting_allowance=role_input.sitting_allowance,
                travel_allowance=role_input.travel_allowance,
                annual_fees=role_input.annual_fees,
                icag_registration=role_input.icag_registration,
            )
        )


def create_related_party(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: RelatedPartyCreate
) -> RelatedPartyRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    _validate_party_rules(db, ctx, payload, party_id=None)
    party = RelatedParty(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        party_type=payload.party_type,
        full_name=payload.full_name,
    )
    _apply_party_fields(party, payload)
    db.add(party)
    db.flush()
    _replace_roles(db, ctx, party, payload)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="related_party.created",
        entity_type="related_party",
        entity_id=party.id,
        details={
            "bank_id": str(bank.id),
            "reason": payload.reason,
            "party_type": payload.party_type,
            "full_name": payload.full_name,
            "roles": [role.role for role in payload.roles],
        },
    )
    db.commit()
    return _read_party(db, party)


def update_related_party(
    db: Session, ctx: TenantContext, bank_id: UUID, party_id: UUID, payload: RelatedPartyUpdate
) -> RelatedPartyRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    party = _get_party_or_404(db, ctx, bank.id, party_id)
    _validate_party_rules(db, ctx, payload, party_id=party.id)
    _apply_party_fields(party, payload)
    _replace_roles(db, ctx, party, payload)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="related_party.updated",
        entity_type="related_party",
        entity_id=party.id,
        details={
            "bank_id": str(bank.id),
            "reason": payload.reason,
            "party_type": payload.party_type,
            "full_name": payload.full_name,
            "roles": [role.role for role in payload.roles],
        },
    )
    db.commit()
    return _read_party(db, party)


# --- shareholdings ---------------------------------------------------------------------


def _apply_shareholding_fields(
    holding: Shareholding, payload: ShareholdingCreate | ShareholdingUpdate
) -> None:
    holding.share_type = payload.share_type
    holding.share_subtype = payload.share_subtype
    holding.shareholder_rights = payload.shareholder_rights
    holding.number_of_shares = payload.number_of_shares
    holding.pct_shareholding = payload.pct_shareholding
    holding.ubo_party_id = payload.ubo_party_id


def add_shareholding(
    db: Session, ctx: TenantContext, bank_id: UUID, party_id: UUID, payload: ShareholdingCreate
) -> RelatedPartyRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    party = _get_party_or_404(db, ctx, bank.id, party_id)
    if payload.ubo_party_id is not None:
        _require_individual_ubo(db, ctx, bank.id, payload.ubo_party_id)
    holding = Shareholding(
        organization_id=ctx.organization_id,
        party_id=party.id,
        share_type=payload.share_type,
        shareholder_rights=payload.shareholder_rights,
        number_of_shares=payload.number_of_shares,
        pct_shareholding=payload.pct_shareholding,
    )
    _apply_shareholding_fields(holding, payload)
    db.add(holding)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="shareholding.created",
        entity_type="shareholding",
        entity_id=holding.id,
        details={
            "bank_id": str(bank.id),
            "party_id": str(party.id),
            "reason": payload.reason,
            "share_type": payload.share_type,
            "ubo_party_id": str(payload.ubo_party_id) if payload.ubo_party_id else None,
        },
    )
    db.commit()
    return _read_party(db, party)


def update_shareholding(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    party_id: UUID,
    shareholding_id: UUID,
    payload: ShareholdingUpdate,
) -> RelatedPartyRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    party = _get_party_or_404(db, ctx, bank.id, party_id)
    holding = db.scalar(
        select(Shareholding).where(
            Shareholding.id == shareholding_id,
            Shareholding.organization_id == ctx.organization_id,
            Shareholding.party_id == party.id,
        )
    )
    if holding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shareholding not found.")
    if payload.ubo_party_id is not None:
        _require_individual_ubo(db, ctx, bank.id, payload.ubo_party_id)
    _apply_shareholding_fields(holding, payload)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="shareholding.updated",
        entity_type="shareholding",
        entity_id=holding.id,
        details={
            "bank_id": str(bank.id),
            "party_id": str(party.id),
            "reason": payload.reason,
            "share_type": payload.share_type,
            "ubo_party_id": str(payload.ubo_party_id) if payload.ubo_party_id else None,
        },
    )
    db.commit()
    return _read_party(db, party)


# --- outlets ------------------------------------------------------------------------------


def _apply_outlet_fields(outlet: Outlet, payload: OutletCreate | OutletUpdate) -> None:
    outlet.outlet_type = payload.outlet_type
    outlet.name = payload.name
    outlet.outlet_number = payload.outlet_number
    outlet.address = payload.address
    outlet.status = payload.status
    outlet.opened_on = payload.opened_on
    outlet.closed_on = payload.closed_on
    outlet.relocated_from = payload.relocated_from
    # Closure always carries the closure date.
    if outlet.status == "closed" and outlet.closed_on is None:
        outlet.closed_on = utc_now().date()


def create_outlet(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: OutletCreate
) -> OutletRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    outlet = Outlet(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        outlet_type=payload.outlet_type,
        name=payload.name,
    )
    _apply_outlet_fields(outlet, payload)
    db.add(outlet)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="outlet.created",
        entity_type="outlet",
        entity_id=outlet.id,
        details={
            "bank_id": str(bank.id),
            "reason": payload.reason,
            "outlet_type": payload.outlet_type,
            "name": payload.name,
        },
    )
    db.commit()
    return _outlet_read(outlet)


def update_outlet(
    db: Session, ctx: TenantContext, bank_id: UUID, outlet_id: UUID, payload: OutletUpdate
) -> OutletRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    outlet = db.scalar(
        select(Outlet).where(
            Outlet.id == outlet_id,
            Outlet.organization_id == ctx.organization_id,
            Outlet.bank_id == bank.id,
        )
    )
    if outlet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outlet not found.")
    _apply_outlet_fields(outlet, payload)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="outlet.updated",
        entity_type="outlet",
        entity_id=outlet.id,
        details={
            "bank_id": str(bank.id),
            "reason": payload.reason,
            "status": outlet.status,
            "closed_on": outlet.closed_on.isoformat() if outlet.closed_on else None,
        },
    )
    db.commit()
    return _outlet_read(outlet)


# --- products, licenses, name history ---------------------------------------------------


def create_product(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: BankProductCreate
) -> BankProductRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    product = BankProduct(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        name=payload.name,
        product_type=payload.product_type,
        status=payload.status,
        approval_reference=payload.approval_reference,
    )
    db.add(product)
    db.flush()
    _record_bank_child_event(
        db, ctx, "bank_product.created", "bank_product", product.id, bank.id, payload.reason
    )
    db.commit()
    return _product_read(product)


def update_product(
    db: Session, ctx: TenantContext, bank_id: UUID, product_id: UUID, payload: BankProductUpdate
) -> BankProductRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    product = db.scalar(
        select(BankProduct).where(
            BankProduct.id == product_id,
            BankProduct.organization_id == ctx.organization_id,
            BankProduct.bank_id == bank.id,
        )
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")
    product.name = payload.name
    product.product_type = payload.product_type
    product.status = payload.status
    product.approval_reference = payload.approval_reference
    db.flush()
    _record_bank_child_event(
        db, ctx, "bank_product.updated", "bank_product", product.id, bank.id, payload.reason
    )
    db.commit()
    return _product_read(product)


def create_license(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: BankLicenseCreate
) -> BankLicenseRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    license_row = BankLicense(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        license_name=payload.license_name,
        license_class=payload.license_class,
        issued_on=payload.issued_on,
        status=payload.status,
    )
    db.add(license_row)
    db.flush()
    _record_bank_child_event(
        db, ctx, "bank_license.created", "bank_license", license_row.id, bank.id, payload.reason
    )
    db.commit()
    return _license_read(license_row)


def update_license(
    db: Session, ctx: TenantContext, bank_id: UUID, license_id: UUID, payload: BankLicenseUpdate
) -> BankLicenseRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    license_row = db.scalar(
        select(BankLicense).where(
            BankLicense.id == license_id,
            BankLicense.organization_id == ctx.organization_id,
            BankLicense.bank_id == bank.id,
        )
    )
    if license_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found.")
    license_row.license_name = payload.license_name
    license_row.license_class = payload.license_class
    license_row.issued_on = payload.issued_on
    license_row.status = payload.status
    db.flush()
    _record_bank_child_event(
        db, ctx, "bank_license.updated", "bank_license", license_row.id, bank.id, payload.reason
    )
    db.commit()
    return _license_read(license_row)


def create_name_history_entry(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: BankNameHistoryCreate
) -> BankNameHistoryRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    entry = BankNameHistory(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        previous_name=payload.previous_name,
        changed_on=payload.changed_on,
        reason=payload.change_reason,
    )
    db.add(entry)
    db.flush()
    _record_bank_child_event(
        db, ctx, "bank_name_history.created", "bank_name_history", entry.id, bank.id, payload.reason
    )
    db.commit()
    return _name_history_read(entry)


def update_name_history_entry(
    db: Session, ctx: TenantContext, bank_id: UUID, entry_id: UUID, payload: BankNameHistoryUpdate
) -> BankNameHistoryRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    entry = db.scalar(
        select(BankNameHistory).where(
            BankNameHistory.id == entry_id,
            BankNameHistory.organization_id == ctx.organization_id,
            BankNameHistory.bank_id == bank.id,
        )
    )
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Name-history entry not found."
        )
    entry.previous_name = payload.previous_name
    entry.changed_on = payload.changed_on
    entry.reason = payload.change_reason
    db.flush()
    _record_bank_child_event(
        db, ctx, "bank_name_history.updated", "bank_name_history", entry.id, bank.id, payload.reason
    )
    db.commit()
    return _name_history_read(entry)


def _record_bank_child_event(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    event_type: str,
    entity_type: str,
    entity_id: UUID,
    bank_id: UUID,
    reason: str,
) -> None:
    record_event(
        db,
        ctx,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        details={"bank_id": str(bank_id), "reason": reason},
    )


def orass_institution_code(db: Session, ctx: TenantContext, bank_id: UUID) -> str | None:
    """The profile's ORASS institution code, when a profile exists (else None)."""
    return db.scalar(
        select(InstitutionProfile.orass_institution_code).where(
            InstitutionProfile.organization_id == ctx.organization_id,
            InstitutionProfile.bank_id == bank_id,
        )
    )


def profile_exists(db: Session, ctx: TenantContext, bank_id: UUID) -> bool:
    return (
        db.scalar(
            select(InstitutionProfile.id).where(
                InstitutionProfile.organization_id == ctx.organization_id,
                InstitutionProfile.bank_id == bank_id,
            )
        )
        is not None
    )
