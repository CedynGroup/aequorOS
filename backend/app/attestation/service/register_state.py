"""Complete bank register commitments over stored values and key-version evidence.

Protected columns remain opaque: numeric filings and ICAAP provenance can commit
register state without access to the bank's identity key. Record identity,
update timestamps, stored ciphertext and the current key version bind the state;
rotation or re-encryption requires an in-review filing to be regenerated.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Column, ColumnElement, Table, select, type_coerce
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.attestation.service.digests import RegisterState, RegisterStateRow
from app.core.key_management.fields import ProtectedField
from app.core.key_management.models import BankEncryptionKey
from app.identity.public import (
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
) -> RegisterStateRow:
    return {
        "table": table,
        "id": str(row_id),
        "updated_at": updated_at.isoformat(),
        "values": {key: _canonical_value(value) for key, value in values.items()},
    }


def project_register_state(db: Session, ctx: TenantContext, bank_id: str) -> RegisterState:
    """Project all eight register tables without decrypting protected values."""
    key_version = db.scalar(
        select(BankEncryptionKey.current_version_id).where(
            BankEncryptionKey.organization_id == ctx.organization_id,
            BankEncryptionKey.bank_id == bank_id,
        )
    )
    projections: tuple[tuple[Table, tuple[str, ...]], ...] = (
        (
            cast(Table, InstitutionProfile.__table__),
            (
                "institution_type",
                "legal_entity_structure",
                "authorisation_date",
                "approved_capital",
                "incorporation_date",
                "tin",
                "registration_number",
                "orass_institution_code",
                "traded_on_exchange",
                "exchange_name",
                "isin",
                "ownership_local_pct",
                "ownership_foreign_pct",
                "parent_country_code",
            ),
        ),
        (
            cast(Table, RelatedParty.__table__),
            (
                "party_type",
                "full_name",
                "contact",
                "regulated_elsewhere",
                "regulated_jurisdiction",
                "status",
            ),
        ),
        (
            cast(Table, Outlet.__table__),
            (
                "outlet_type",
                "name",
                "outlet_number",
                "address",
                "status",
                "opened_on",
                "closed_on",
                "relocated_from",
            ),
        ),
        (
            cast(Table, BankProduct.__table__),
            (
                "name",
                "product_type",
                "status",
                "approval_reference",
            ),
        ),
        (
            cast(Table, BankLicense.__table__),
            (
                "license_name",
                "license_class",
                "issued_on",
                "status",
            ),
        ),
        (
            cast(Table, BankNameHistory.__table__),
            (
                "previous_name",
                "changed_on",
                "reason",
            ),
        ),
        (
            cast(Table, RelatedPartyRole.__table__),
            (
                "party_id",
                "role",
                "other_responsibilities",
                "appointed_on",
                "term_of_appointment",
                "sitting_allowance",
                "travel_allowance",
                "annual_fees",
                "icag_registration",
            ),
        ),
        (
            cast(Table, Shareholding.__table__),
            (
                "party_id",
                "share_type",
                "share_subtype",
                "shareholder_rights",
                "number_of_shares",
                "pct_shareholding",
                "ubo_party_id",
            ),
        ),
    )
    rows: list[RegisterStateRow] = []
    parties = cast(Table, RelatedParty.__table__)
    for table, fields in projections:
        columns: list[ColumnElement[object]] = [table.c.id, table.c.updated_at]
        for name in fields:
            column = cast(Column[object], table.c[name])
            columns.append(
                type_coerce(column, column.type.impl).label(name)
                if isinstance(column.type, ProtectedField)
                else column
            )
        statement = select(*columns).where(table.c.organization_id == ctx.organization_id)
        if table.name in ("related_party_roles", "shareholdings"):
            statement = statement.join(parties, table.c.party_id == parties.c.id).where(
                parties.c.organization_id == ctx.organization_id,
                parties.c.bank_id == bank_id,
            )
        else:
            statement = statement.where(table.c.bank_id == bank_id)
        for result in db.execute(statement).mappings():
            record = cast(Mapping[str, object], result)
            rows.append(
                _entry(
                    table.name,
                    cast(UUID, record["id"]),
                    cast(datetime, record["updated_at"]),
                    {name: record[name] for name in fields},
                )
            )
    return {
        "organization_id": ctx.organization_id,
        "bank_id": bank_id,
        "key_version": str(key_version) if key_version is not None else None,
        "rows": rows,
    }


__all__ = ["REGISTER_TABLES", "project_register_state"]
