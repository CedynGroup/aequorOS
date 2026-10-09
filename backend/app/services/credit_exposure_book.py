"""The bank's canonical exposure book, flattened once for every reader of it.

Several engines need the same thing: the current-generation position snapshots
at an as-of date, with the counterparty, the product and the connected-group
identity already resolved, and the SQLAlchemy ``Row`` unpacked exactly once.
The enterprise stress test built that first and kept it private; the ICAAP
granularity adjustment needs the identical slice, so it lives here now and both
read it. Nothing in this module interprets an exposure — no risk weight, no PD,
no exposure class. It reads rows and flattens them; every judgement about what
a row MEANS belongs to the engine that consumes it.

**Current generation** means what ``le_generation`` means by it: the snapshot
for that exact date that has not been superseded or withdrawn and whose
validation status is accepted or warning. The query is deliberately duplicated
rather than imported from ``le_generation``, which drags in the regulatory
reporting services.

**An amount that could not be stated in the reporting currency is ``None``, not
zero.** That distinction is the reason this module exists as a separate shape
rather than a straight lift: a foreign-currency position whose conversion was
never ingested and a position that genuinely holds nothing are not the same
fact, and a reader that cannot tell them apart either silently drops real
exposure or counts an unknown as an empty one. Enterprise credit stress requires
official facts with an established net credit basis before using these rows;
an unconverted credit exposure carries no risk weight and refuses calculation.
The granularity adjustment instead EXCLUDES unconverted rows and discloses how
many it excluded.

The conversion itself is read, never performed: an ingested
``attributes.balance_ghs`` wins, otherwise a position already denominated in the
bank's own currency is taken at face value, and anything else is unconverted.
The attribute keys ``balance_ghs`` and ``notional_ghs`` are the ingestion wire
contract and keep their historical spelling in every jurisdiction — like the
``bog_*`` fact categories, they are DB/wire keys, not a country claim. The
dataclass field names say what they actually mean: an amount in the institution's
own reporting currency, whichever that is.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.outcomes import NotComputable, OutcomeDetail, OutcomeState
from app.domain.positions.credit import credit_classification_attributes
from app.models import (
    Bank,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
)
from app.services import jurisdictions

#: The validation statuses a filed figure may rest on. ``warning`` is included
#: because a warning is a flag on a row that was still accepted, not a rejection.
INCLUDED_VALIDATION_STATUSES: tuple[str, ...] = ("accepted", "warning")

#: The migrating credit book: loans plus interbank placements — the counterparty
#: credit exposures a downgrade moves.
CREDIT_POSITION_TYPES: tuple[str, ...] = ("LOAN", "INTERBANK_PLACEMENT")
#: Adds the securities book, because issuer and sovereign concentration is real
#: concentration even where the holding is not a counterparty credit exposure.
CONCENTRATION_POSITION_TYPES: tuple[str, ...] = (
    "LOAN",
    "INTERBANK_PLACEMENT",
    "SECURITY_HOLDING",
)
FUNDING_POSITION_TYPES: tuple[str, ...] = ("DEPOSIT", "INTERBANK_BORROWING")
DERIVATIVE_POSITION_TYPES: tuple[str, ...] = ("DERIVATIVE", "FX_HEDGE", "INTEREST_RATE_SWAP")

#: The ingested conversion attributes. Wire keys; see the module docstring.
ATTRIBUTE_BALANCE_REPORTING = "balance_ghs"
ATTRIBUTE_NOTIONAL_REPORTING = "notional_ghs"

#: IFRS 9 staging. Stage 3 is the credit-impaired bucket: the exposure has
#: already defaulted, so the loss is an ECL question rather than an unexpected-
#: loss one. A stage identifier, not a calibration.
STAGE_CREDIT_IMPAIRED = 3

_GROUP_ATTRIBUTE_KEYS = ("group_reference", "group", "parent")


@dataclass(frozen=True)
class ExposureRow:
    """One current-generation position snapshot, flattened.

    ``balance_rep`` and ``notional_rep`` are amounts in the institution's own
    reporting currency, or ``None`` when no such amount could be established —
    see the module docstring. ``is_foreign_currency`` says whether the position
    is denominated in something other than that currency, so a consumer can tell
    a missing conversion from a missing figure.
    """

    source_reference: str
    position_type: str
    currency: str
    balance_rep: Decimal | None
    is_foreign_currency: bool
    notional_rep: Decimal | None
    ifrs9_stage: int | None
    attributes: dict[str, Any]
    counterparty_type: str | None
    counterparty_resident: bool | None
    counterparty_country: str | None
    group_key: str
    regulatory_category: str | None
    product_risk_weight_code: str | None
    product_code: str | None
    #: The date the contract runs to, as the source stated it. NOT an effective
    #: maturity in years: converting one into the other is a day-count and an
    #: IRB definition, both of which belong to the engine that needs them.
    contractual_maturity: date | None = None
    origination_date: date | None = None

    @property
    def unconverted(self) -> bool:
        """No amount in the reporting currency could be established."""
        return self.balance_rep is None

    @property
    def credit_impaired(self) -> bool:
        """IFRS 9 stage 3 — already defaulted."""
        return self.ifrs9_stage == STAGE_CREDIT_IMPAIRED


def canonical_group_key(source_reference: str, counterparty: Any) -> str:
    """Connected-group / single-name identity (``le_generation``'s pattern).

    A connected group beats the counterparty's own name, because an
    idiosyncratic default lands on the group. With no counterparty at all the
    position is its own name — never merged with another unknown.
    """
    if counterparty is not None:
        if counterparty.group_reference:
            return f"group:{counterparty.group_reference}"
        cp_attributes = counterparty.attributes or {}
        for key in _GROUP_ATTRIBUTE_KEYS:
            value = cp_attributes.get(key)
            if value:
                return f"group:{value}"
        return f"cp:{counterparty.name}"
    return f"pos:{source_reference}"


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _ingested(raw_attribute: Any) -> Decimal | None:
    """The ingested conversion, when one was supplied. An empty string is not."""
    if raw_attribute is not None and raw_attribute != "":
        return _dec(raw_attribute)
    return None


def _reporting_balance(
    raw_attribute: Any, native: Any, *, is_base_currency: bool
) -> Decimal | None:
    """The drawn balance in the reporting currency, or ``None`` if unconverted.

    A position already denominated in the bank's own currency is taken at face
    value, and a balance the source left unstated is nothing rather than
    unknown — a position with no balance holds none.
    """
    ingested = _ingested(raw_attribute)
    if ingested is not None:
        return ingested
    if not is_base_currency:
        return None
    return _dec(native or 0)


def _reporting_notional(
    raw_attribute: Any, native: Any, *, is_base_currency: bool
) -> Decimal | None:
    """The notional in the reporting currency, or ``None`` if there is not one.

    Unlike a balance, an unstated notional is genuinely absent: most positions
    do not have one at all.
    """
    ingested = _ingested(raw_attribute)
    if ingested is not None:
        return ingested
    if is_base_currency and native is not None:
        return _dec(native)
    return None


_SourceRecord = tuple[
    CanonicalPositionSnapshot,
    CanonicalPosition,
    CanonicalCounterparty | None,
    CanonicalProduct | None,
]


def load_position_records(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    position_types: tuple[str, ...] | None = None,
) -> list[_SourceRecord]:
    return cast(
        list[_SourceRecord],
        (
            db.execute(
                select(
                    CanonicalPositionSnapshot,
                    CanonicalPosition,
                    CanonicalCounterparty,
                    CanonicalProduct,
                )
                .join(
                    CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id
                )
                .outerjoin(
                    CanonicalCounterparty,
                    CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
                )
                .outerjoin(
                    CanonicalProduct,
                    CanonicalPositionSnapshot.product_id == CanonicalProduct.id,
                )
                .where(
                    CanonicalPositionSnapshot.organization_id == ctx.organization_id,
                    CanonicalPositionSnapshot.bank_id == bank.id,
                    CanonicalPositionSnapshot.as_of_date == as_of,
                    CanonicalPositionSnapshot.superseded_by.is_(None),
                    CanonicalPositionSnapshot.withdrawn_at.is_(None),
                    CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
                    *(
                        (CanonicalPosition.position_type.in_(position_types),)
                        if position_types is not None
                        else ()
                    ),
                )
                .order_by(CanonicalPositionSnapshot.source_reference)
            )
            .tuples()
            .all()
        ),
    )


def _source_value(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=UTC).isoformat()
            if value.tzinfo is None
            else value.astimezone(UTC).isoformat()
        )
    return str(value)


def credit_source_basis(records: Sequence[_SourceRecord]) -> str:
    """Serialize the exact accepted credit source records, including an empty book."""
    versions = [
        [
            None
            if model is None
            else {
                column.key: cast(object, getattr(model, column.key))
                for column in model.__table__.columns
                if column.key not in ("created_at", "updated_at", "ingested_at")
            }
            for model in record
        ]
        for record in records
        if record[1].position_type in CREDIT_POSITION_TYPES
    ]
    return json.dumps(
        sorted(
            versions, key=lambda version: json.dumps(version, sort_keys=True, default=_source_value)
        ),
        sort_keys=True,
        default=_source_value,
    )


def require_credit_source_basis(official: str | None, current: str) -> None:
    """BoG CRD (June 2018) ¶98, ¶123–124: stress only the officially derived source book."""
    if official != current:
        raise NotComputable(
            OutcomeDetail(
                state=OutcomeState.RECONCILIATION_FAILED,
                metric_id="stressed_credit_rwa",
                reason=(
                    "The official facts are stale: their accepted loan and placement source "
                    "versions differ from the current book. Re-derive the official facts for "
                    "this reporting period before running enterprise stress."
                ),
                items=("source:credit_book",),
            )
        )


def load_exposure_rows(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    position_types: tuple[str, ...],
) -> list[ExposureRow]:
    """The current-generation slice for ``position_types``, flattened.

    The single place a SQLAlchemy ``Row`` from this query is unpacked. Ordered
    by source reference, so the list a caller receives is stable.
    """
    return _exposure_rows(load_position_records(db, ctx, bank, as_of, position_types), bank)


def load_credit_book(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> tuple[list[ExposureRow], str]:
    """Load stress exposures and source versions from the same accepted record set."""
    records = load_position_records(db, ctx, bank, as_of, CREDIT_POSITION_TYPES)
    return _exposure_rows(records, bank), credit_source_basis(records)


def _exposure_rows(records: Sequence[_SourceRecord], bank: Bank) -> list[ExposureRow]:
    base_currency = jurisdictions.base_currency(bank)
    rows: list[ExposureRow] = []
    for record in records:
        snapshot, position, counterparty, product = record
        attributes: dict[str, Any] = credit_classification_attributes(
            snapshot.attributes or {},
            counterparty.attributes or {} if counterparty is not None else {},
        )
        currency = str(position.currency).strip().upper()
        is_base_currency = currency == base_currency
        balance_rep = _reporting_balance(
            attributes.get(ATTRIBUTE_BALANCE_REPORTING),
            snapshot.balance,
            is_base_currency=is_base_currency,
        )
        notional_rep = _reporting_notional(
            attributes.get(ATTRIBUTE_NOTIONAL_REPORTING),
            snapshot.notional,
            is_base_currency=is_base_currency,
        )
        issuer = attributes.get("issuer")
        group_key = canonical_group_key(str(snapshot.source_reference), counterparty)
        if counterparty is None and issuer:
            group_key = f"issuer:{issuer}"
        rows.append(
            ExposureRow(
                source_reference=str(snapshot.source_reference),
                position_type=str(position.position_type),
                currency=currency,
                balance_rep=balance_rep,
                is_foreign_currency=not is_base_currency,
                notional_rep=notional_rep,
                ifrs9_stage=snapshot.ifrs9_stage,
                attributes=attributes,
                counterparty_type=(
                    counterparty.counterparty_type if counterparty is not None else None
                ),
                counterparty_resident=(counterparty.resident if counterparty is not None else None),
                counterparty_country=(
                    counterparty.country_code if counterparty is not None else None
                ),
                group_key=group_key,
                regulatory_category=(product.regulatory_category if product is not None else None),
                product_risk_weight_code=(
                    product.risk_weight_code if product is not None else None
                ),
                product_code=(product.product_code if product is not None else None),
                contractual_maturity=snapshot.contractual_maturity,
                origination_date=position.origination_date,
            )
        )
    return rows


__all__ = [
    "ATTRIBUTE_BALANCE_REPORTING",
    "ATTRIBUTE_NOTIONAL_REPORTING",
    "CONCENTRATION_POSITION_TYPES",
    "CREDIT_POSITION_TYPES",
    "DERIVATIVE_POSITION_TYPES",
    "FUNDING_POSITION_TYPES",
    "INCLUDED_VALIDATION_STATUSES",
    "STAGE_CREDIT_IMPAIRED",
    "ExposureRow",
    "canonical_group_key",
    "credit_source_basis",
    "load_credit_book",
    "load_position_records",
    "require_credit_source_basis",
    "load_exposure_rows",
]
