"""Register-state projection for the master-data return packs (gap G16).

The five ``LRT-*`` corporate packs are generated exclusively from the
institution-profile register and bind NO engine run (``source_runs == []``), so
they have no reproducible ``input_hash`` for a signature to rest on
(docs/attestation_esignature.md §1.7 Class B, §3.1). This module supplies the
analogue: a projection of the register rows that feed those packs into the
shape :func:`app.services.attestation.digests.register_state_digest` consumes,
so a later mutation of a shareholding or an outlet is detectable against a
signed pack even though the register itself stays live and mutable.

``values`` carries REPORTABLE fields only — never the row's own primary key,
the tenant/bank scoping columns, or ``created_at``/``updated_at`` — so the
digest moves if and only if data a regulator would see has changed. Relational
foreign keys (``party_id``, ``ubo_party_id``) DO ride in ``values``: re-pointing
a role or a beneficial-owner link changes what the pack states, and leaving
them out would make that change invisible to the digest.

Scope is the whole register for the bank rather than per-pack subsets. The five
packs between them read all eight tables, and ``lrt_generation`` loads the full
profile for every one of them, so the coarser scope costs only sensitivity: an
edit to a table a given pack does not print shows as drift on that pack. That
is the conservative direction — it never misses a real change.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    BankLicense,
    BankNameHistory,
    BankProduct,
    InstitutionProfile,
    Outlet,
    RelatedParty,
    RelatedPartyRole,
    Shareholding,
)

#: Every table this projection covers, in the order rows are emitted. The
#: digest sorts entries itself, so this order is documentation, not semantics.
REGISTER_TABLES: tuple[str, ...] = (
    "institution_profiles",
    "related_parties",
    "related_party_roles",
    "shareholdings",
    "outlets",
    "bank_products",
    "bank_licenses",
    "bank_name_history",
)


def _canonical_value(value: Any) -> Any:
    """Pre-stringify to a JSON-native, driver-independent form.

    ``digests.canonical_json`` deliberately has no ``default=`` handler, so
    every value must arrive JSON-native. Decimals are normalised because a
    ``Numeric`` column comes back with driver-dependent scale (``10`` on
    SQLite, ``10.00`` on Postgres) and a stored digest must not depend on
    which engine read the row.
    """
    if value is None or isinstance(value, bool | str | int | float):
        return value
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, date | datetime):  # datetime subclasses date
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return str(value)


def _entry(
    table: str, row_id: UUID, updated_at: datetime, values: dict[str, Any]
) -> dict[str, Any]:
    return {
        "table": table,
        "id": str(row_id),
        "updated_at": updated_at.isoformat(),
        "values": {key: _canonical_value(value) for key, value in values.items()},
    }


def register_state_rows(db: Session, ctx: TenantContext, bank_id: str) -> list[dict[str, Any]]:
    """Project the bank's institution-profile register for digesting."""
    rows: list[dict[str, Any]] = []

    profiles = db.scalars(
        select(InstitutionProfile).where(
            InstitutionProfile.organization_id == ctx.organization_id,
            InstitutionProfile.bank_id == bank_id,
        )
    )
    for profile in profiles:
        rows.append(
            _entry(
                "institution_profiles",
                profile.id,
                profile.updated_at,
                {
                    "institution_type": profile.institution_type,
                    "legal_entity_structure": profile.legal_entity_structure,
                    "authorisation_date": profile.authorisation_date,
                    "approved_capital": profile.approved_capital,
                    "incorporation_date": profile.incorporation_date,
                    "tin": profile.tin,
                    "registration_number": profile.registration_number,
                    "orass_institution_code": profile.orass_institution_code,
                    "traded_on_exchange": profile.traded_on_exchange,
                    "exchange_name": profile.exchange_name,
                    "isin": profile.isin,
                    "ownership_local_pct": profile.ownership_local_pct,
                    "ownership_foreign_pct": profile.ownership_foreign_pct,
                    "parent_country_code": profile.parent_country_code,
                },
            )
        )

    parties = list(
        db.scalars(
            select(RelatedParty).where(
                RelatedParty.organization_id == ctx.organization_id,
                RelatedParty.bank_id == bank_id,
            )
        )
    )
    for party in parties:
        rows.append(
            _entry(
                "related_parties",
                party.id,
                party.updated_at,
                {
                    "party_type": party.party_type,
                    "full_name": party.full_name,
                    "contact": party.contact,
                    "regulated_elsewhere": party.regulated_elsewhere,
                    "regulated_jurisdiction": party.regulated_jurisdiction,
                    "status": party.status,
                },
            )
        )

    # Roles and shareholdings hang off the party, not the bank, so they are
    # reached through the party ids just resolved (still org-filtered).
    party_ids = [party.id for party in parties]
    if party_ids:
        roles = db.scalars(
            select(RelatedPartyRole).where(
                RelatedPartyRole.organization_id == ctx.organization_id,
                RelatedPartyRole.party_id.in_(party_ids),
            )
        )
        for role in roles:
            rows.append(
                _entry(
                    "related_party_roles",
                    role.id,
                    role.updated_at,
                    {
                        "party_id": role.party_id,
                        "role": role.role,
                        "other_responsibilities": role.other_responsibilities,
                        "appointed_on": role.appointed_on,
                        "term_of_appointment": role.term_of_appointment,
                        "sitting_allowance": role.sitting_allowance,
                        "travel_allowance": role.travel_allowance,
                        "annual_fees": role.annual_fees,
                        "icag_registration": role.icag_registration,
                    },
                )
            )

        holdings = db.scalars(
            select(Shareholding).where(
                Shareholding.organization_id == ctx.organization_id,
                Shareholding.party_id.in_(party_ids),
            )
        )
        for holding in holdings:
            rows.append(
                _entry(
                    "shareholdings",
                    holding.id,
                    holding.updated_at,
                    {
                        "party_id": holding.party_id,
                        "share_type": holding.share_type,
                        "share_subtype": holding.share_subtype,
                        "shareholder_rights": holding.shareholder_rights,
                        "number_of_shares": holding.number_of_shares,
                        "pct_shareholding": holding.pct_shareholding,
                        "ubo_party_id": holding.ubo_party_id,
                    },
                )
            )

    outlets = db.scalars(
        select(Outlet).where(
            Outlet.organization_id == ctx.organization_id,
            Outlet.bank_id == bank_id,
        )
    )
    for outlet in outlets:
        rows.append(
            _entry(
                "outlets",
                outlet.id,
                outlet.updated_at,
                {
                    "outlet_type": outlet.outlet_type,
                    "name": outlet.name,
                    "outlet_number": outlet.outlet_number,
                    "address": outlet.address,
                    "status": outlet.status,
                    "opened_on": outlet.opened_on,
                    "closed_on": outlet.closed_on,
                    "relocated_from": outlet.relocated_from,
                },
            )
        )

    products = db.scalars(
        select(BankProduct).where(
            BankProduct.organization_id == ctx.organization_id,
            BankProduct.bank_id == bank_id,
        )
    )
    for product in products:
        rows.append(
            _entry(
                "bank_products",
                product.id,
                product.updated_at,
                {
                    "name": product.name,
                    "product_type": product.product_type,
                    "status": product.status,
                    "approval_reference": product.approval_reference,
                },
            )
        )

    licenses = db.scalars(
        select(BankLicense).where(
            BankLicense.organization_id == ctx.organization_id,
            BankLicense.bank_id == bank_id,
        )
    )
    for license_row in licenses:
        rows.append(
            _entry(
                "bank_licenses",
                license_row.id,
                license_row.updated_at,
                {
                    "license_name": license_row.license_name,
                    "license_class": license_row.license_class,
                    "issued_on": license_row.issued_on,
                    "status": license_row.status,
                },
            )
        )

    history = db.scalars(
        select(BankNameHistory).where(
            BankNameHistory.organization_id == ctx.organization_id,
            BankNameHistory.bank_id == bank_id,
        )
    )
    for entry in history:
        rows.append(
            _entry(
                "bank_name_history",
                entry.id,
                entry.updated_at,
                {
                    "previous_name": entry.previous_name,
                    "changed_on": entry.changed_on,
                    "reason": entry.reason,
                },
            )
        )

    return rows


__all__ = ["REGISTER_TABLES", "register_state_rows"]
