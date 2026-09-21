"""Capital add-ons the supervisor imposed: recorded, confirmed, never published.

These are the regulator's own instructions to one bank. Three properties make
them different from everything else in the ICAAP workspace, and each is
enforced rather than documented:

* **Bank-scoped, not cycle-scoped.** A letter that takes effect in March
  applies to the FY2026 ICAAP and to the FY2027 one, until it is superseded or
  withdrawn. Storing it on a cycle would lose it at the next cycle.
* **Sealed once active.** A confirmed add-on is a record of what the regulator
  said. It can be superseded or withdrawn — both of which leave the original
  row intact — but never rewritten. The database enforces that, and it also
  enforces that the person who recorded it is not the person who confirmed it.
* **Never public.** The disclosure regime lets a bank choose what to publish;
  it never gets to choose this. The block carries ``never_public=True`` and
  nothing here offers an override.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.domain.icaap.units import Basis, Denominators, UnitConversionError, to_amount
from app.models.icaap import IcaapCycle
from app.models.icaap_risk_capital import BankSupervisoryAddon
from app.schemas.icaap_risk_capital import (
    IcaapReason,
    IcaapSupervisoryAddonListRead,
    IcaapSupervisoryAddonRead,
)
from app.services import jurisdictions
from app.services.audit import record_event
from app.services.icaap import attachments as attachments_service
from app.services.icaap import blocks as blocks_service
from app.services.icaap import guards, pillar2_inputs
from app.storage import ObjectMetadata, StorageClient, StorageLocation

_STORAGE_TIER = "outputs"
#: Derived from the column, so a stored name can never exceed what it holds.
_FILENAME_MAX = int(
    getattr(BankSupervisoryAddon.__table__.c.letter_original_filename.type, "length", 0) or 0
)
#: Statuses an impersonated examiner may see. A draft is the bank's working
#: note about a letter it has not yet confirmed, not a supervisory fact.
EXAMINER_VISIBLE = ("active", "superseded")


def _rows(
    db: Session, access: IcaapAccess, *, include_inactive: bool
) -> list[BankSupervisoryAddon]:
    statement = select(BankSupervisoryAddon).where(
        BankSupervisoryAddon.organization_id == access.ctx.organization_id,
        BankSupervisoryAddon.bank_id == access.bank.id,
    )
    if access.examiner:
        statement = statement.where(BankSupervisoryAddon.status.in_(EXAMINER_VISIBLE))
    elif not include_inactive:
        statement = statement.where(BankSupervisoryAddon.status == "active")
    return list(
        db.scalars(
            statement.order_by(
                BankSupervisoryAddon.effective_from.desc(),
                BankSupervisoryAddon.letter_date.desc(),
            )
        )
    )


def _addon_or_404(db: Session, access: IcaapAccess, addon_id: UUID) -> BankSupervisoryAddon:
    row = db.scalar(
        select(BankSupervisoryAddon).where(
            BankSupervisoryAddon.id == addon_id,
            BankSupervisoryAddon.organization_id == access.ctx.organization_id,
            BankSupervisoryAddon.bank_id == access.bank.id,
        )
    )
    if row is None or (access.examiner and row.status not in EXAMINER_VISIBLE):
        guards.not_found()
    return row


def _in_force(row: BankSupervisoryAddon, as_of: date, basis: str | None) -> bool:
    if row.status != "active":
        return False
    if row.effective_from > as_of:
        return False
    if row.effective_to is not None and row.effective_to <= as_of:
        return False
    return basis is None or row.applies_to_basis in {basis, "both"}


def active_for(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> list[BankSupervisoryAddon]:
    """Add-ons in force at the cycle's as-of date, on the cycle's basis."""
    return [
        row
        for row in _rows(db, access, include_inactive=True)
        if _in_force(row, cycle.as_of_date, cycle.basis)
    ]


def _denominators(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> Denominators | None:
    bound = blocks_service.current_bindings_by_type(db, access, cycle)
    pillar1 = bound.get(pillar2_inputs.BLOCK_PILLAR1) or bound.get(pillar2_inputs.BLOCK_CAPITAL)
    if pillar1 is None:
        return None
    from app.services.icaap import floors  # noqa: PLC0415 - avoid an import cycle

    total = blocks_service.fact_value(pillar1, "total_rwa")
    credit = blocks_service.fact_value(pillar1, "credit_rwa")
    try:
        car = floors.car_min_pct(db, access, as_of=cycle.as_of_date).value_pct
    except Exception:  # noqa: BLE001 - an unresolved floor simply leaves the amount unknown
        car = None
    return Denominators(
        total_rwa=None if total is None else Decimal(total),
        credit_rwa=None if credit is None else Decimal(credit),
        car_min_pct=car,
    )


def _amount(row: BankSupervisoryAddon, denominators: Denominators | None) -> Decimal | None:
    if denominators is None:
        return None
    try:
        return to_amount(Basis(row.basis), row.basis_value, denominators)
    except (UnitConversionError, ValueError):
        return None


def amounts_by_table5_row(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, rows: Sequence[str]
) -> dict[str, Decimal | None]:
    """Active add-ons converted to amounts and totalled by Table 5 row.

    An add-on with no row attribution is deliberately NOT spread across rows:
    it is an unattributed total, and the reconciliation shows it as its own
    line rather than guessing which risk the supervisor meant.
    """
    denominators = _denominators(db, access, cycle)
    out: dict[str, Decimal | None] = dict.fromkeys(rows)
    for row in active_for(db, access, cycle):
        if row.table5_row is None or row.table5_row not in set(rows):
            continue
        amount = _amount(row, denominators)
        if amount is None:
            continue
        out[row.table5_row] = (out.get(row.table5_row) or Decimal(0)) + amount
    return out


def unattributed_amount(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, rows: Sequence[str]
) -> Decimal:
    denominators = _denominators(db, access, cycle)
    total = Decimal(0)
    for row in active_for(db, access, cycle):
        if row.table5_row is not None and row.table5_row in set(rows):
            continue
        total += _amount(row, denominators) or Decimal(0)
    return total


def _read(row: BankSupervisoryAddon, amount: Decimal | None) -> IcaapSupervisoryAddonRead:
    return IcaapSupervisoryAddonRead(
        id=row.id,
        status=row.status,  # pyright: ignore[reportArgumentType]
        letter_reference=row.letter_reference,
        letter_date=row.letter_date,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        applies_to_basis=row.applies_to_basis,
        table5_row=row.table5_row,
        component_key=row.component_key,
        basis=row.basis,  # pyright: ignore[reportArgumentType]
        basis_value=row.basis_value,
        currency=row.currency,
        description=row.description,
        letter_original_filename=row.letter_original_filename,
        letter_media_type=row.letter_media_type,
        letter_byte_size=row.letter_byte_size,
        letter_sha256=row.letter_sha256,
        supersedes_addon_id=row.supersedes_addon_id,
        superseded_by_addon_id=row.superseded_by_addon_id,
        created_by=row.created_by,
        confirmed_by=row.confirmed_by,
        confirmed_at=row.confirmed_at,
        withdrawn_at=row.withdrawn_at,
        withdrawal_reason=row.withdrawal_reason,
        amount_at_as_of=amount,
        created_at=row.created_at,
    )


def list_addons(
    db: Session,
    access: IcaapAccess,
    *,
    as_of: date | None = None,
    include_inactive: bool = False,
) -> IcaapSupervisoryAddonListRead:
    rows = _rows(db, access, include_inactive=include_inactive)
    if as_of is not None:
        rows = [row for row in rows if row.status != "active" or _in_force(row, as_of, None)]
    reads = [_read(row, None) for row in rows]
    return IcaapSupervisoryAddonListRead(
        bank_id=access.bank.id,
        as_of=as_of,
        currency=jurisdictions.base_currency(access.bank),
        addons=reads,
        total_amount_at_as_of=None,
        never_public=True,
    )


def create_addon(  # noqa: PLR0913 - a recorded letter is its named parts
    db: Session,
    access: IcaapAccess,
    storage: StorageClient,
    *,
    letter_reference: str,
    letter_date: date,
    effective_from: date,
    applies_to_basis: str,
    basis: str,
    basis_value: Decimal,
    table5_row: str | None,
    component_key: str | None,
    description: str | None,
    filename: str,
    content: bytes,
    supersedes_addon_id: UUID | None,
) -> IcaapSupervisoryAddonRead:
    """Record a supervisory letter as a DRAFT. A second person confirms it."""
    media_type = attachments_service.sniff_media_type(content)
    if media_type is None:
        from fastapi import HTTPException, status  # noqa: PLC0415

        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "error_code": "unsupported_media_type",
                "message": "The supervisor's letter must be a PDF or an Office document.",
            },
        )
    if basis not in {member.value for member in Basis}:
        raise guards.unprocessable(
            "method_not_allowed", "That is not a basis an add-on can be quoted on.", basis=basis
        )
    if basis_value < Decimal(0):
        raise guards.unprocessable(
            "zero_amount_justification_required",
            "A supervisory add-on cannot be negative.",
        )
    if supersedes_addon_id is not None:
        predecessor = _addon_or_404(db, access, supersedes_addon_id)
        if predecessor.status != "active":
            raise guards.conflict(
                "addon_not_draft",
                "Only an add-on that is currently in force can be superseded.",
                status=predecessor.status,
            )
    digest = hashlib.sha256(content).hexdigest()

    from app.services.ingestion import bank_slug  # noqa: PLC0415 - avoid an import cycle

    slug = bank_slug(db, access.bank)
    storage.ensure_institution(slug)
    extension = attachments_service.ALLOWED_MEDIA[media_type]
    object_path = f"icaap/supervisory/{digest}.{extension}"
    stored = storage.write(
        StorageLocation(slug, _STORAGE_TIER, object_path),
        BytesIO(content),
        ObjectMetadata(
            institution_slug=slug,
            tier=_STORAGE_TIER,
            checksum_sha256=digest,
            written_at=utc_now(),
            written_by=str(guards.actor_id(access)),
            as_of_date=effective_from.isoformat(),
            source_reference=letter_reference,
        ),
        content_type=media_type,
    )
    row = BankSupervisoryAddon(
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        status="draft",
        letter_reference=letter_reference,
        letter_date=letter_date,
        effective_from=effective_from,
        applies_to_basis=applies_to_basis,
        table5_row=table5_row,
        component_key=component_key,
        basis=basis,
        basis_value=basis_value,
        currency=jurisdictions.base_currency(access.bank),
        description=description,
        letter_original_filename=filename[:_FILENAME_MAX],
        letter_media_type=media_type,
        letter_byte_size=len(content),
        letter_sha256=digest,
        letter_storage_tier=_STORAGE_TIER,
        letter_object_path=object_path,
        letter_storage_version_id=stored.version_id,
        supersedes_addon_id=supersedes_addon_id,
        created_by=guards.actor_id(access),
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        access.ctx,
        event_type="icaap.supervisory_addon.created",
        entity_type="bank_supervisory_addon",
        entity_id=row.id,
        details={
            "letter_reference": letter_reference,
            "effective_from": effective_from.isoformat(),
            "basis": basis,
            "basis_value": str(basis_value),
            "table5_row": table5_row,
            "sha256": digest,
        },
    )
    db.commit()
    db.refresh(row)
    return _read(row, None)


def confirm_addon(
    db: Session, access: IcaapAccess, addon_id: UUID, payload: IcaapReason
) -> IcaapSupervisoryAddonRead:
    """A second person confirms the letter. The database refuses the first."""
    row = _addon_or_404(db, access, addon_id)
    if row.status != "draft":
        raise guards.conflict(
            "addon_already_active",
            "This add-on has already been confirmed.",
            status=row.status,
        )
    actor = guards.actor_id(access)
    if row.created_by == actor:
        raise guards.conflict(
            "self_approval",
            "The person who recorded a supervisory add-on cannot confirm it.",
        )
    now = utc_now()
    row.status = "active"
    row.confirmed_by = actor
    row.confirmed_at = now
    if row.supersedes_addon_id is not None:
        predecessor = _addon_or_404(db, access, row.supersedes_addon_id)
        if predecessor.status == "active":
            predecessor.status = "superseded"
            predecessor.superseded_at = now
            predecessor.superseded_by_addon_id = row.id
            predecessor.effective_to = row.effective_from
            record_event(
                db,
                access.ctx,
                event_type="icaap.supervisory_addon.superseded",
                entity_type="bank_supervisory_addon",
                entity_id=predecessor.id,
                details={
                    "superseded_by": str(row.id),
                    "effective_to": row.effective_from.isoformat(),
                },
            )
    record_event(
        db,
        access.ctx,
        event_type="icaap.supervisory_addon.confirmed",
        entity_type="bank_supervisory_addon",
        entity_id=row.id,
        details={"letter_reference": row.letter_reference, "reason": payload.reason},
    )
    db.commit()
    db.refresh(row)
    return _read(row, None)


def withdraw_addon(
    db: Session, access: IcaapAccess, addon_id: UUID, payload: IcaapReason
) -> IcaapSupervisoryAddonRead:
    row = _addon_or_404(db, access, addon_id)
    if row.status not in {"draft", "active"}:
        raise guards.conflict(
            "addon_not_draft",
            "This add-on is no longer in force, so it cannot be withdrawn.",
            status=row.status,
        )
    row.status = "withdrawn"
    row.withdrawn_at = utc_now()
    row.withdrawn_by = guards.actor_id(access)
    row.withdrawal_reason = payload.reason
    record_event(
        db,
        access.ctx,
        event_type="icaap.supervisory_addon.withdrawn",
        entity_type="bank_supervisory_addon",
        entity_id=row.id,
        details={"letter_reference": row.letter_reference, "reason": payload.reason},
    )
    db.commit()
    db.refresh(row)
    return _read(row, None)


def prepare_letter_download(
    db: Session, access: IcaapAccess, addon_id: UUID
) -> tuple[BankSupervisoryAddon, str]:
    from app.services.ingestion import bank_slug  # noqa: PLC0415 - avoid an import cycle

    row = _addon_or_404(db, access, addon_id)
    slug = bank_slug(db, access.bank)
    record_event(
        db,
        access.ctx,
        event_type="icaap.supervisory_addon.letter_downloaded",
        entity_type="bank_supervisory_addon",
        entity_id=row.id,
        details={"letter_reference": row.letter_reference},
    )
    db.commit()
    return row, slug


def addons_payload(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> tuple[dict[str, Any], Decimal | None]:
    """The block body: active add-ons at the cycle's as-of date, with amounts."""
    denominators = _denominators(db, access, cycle)
    rows = active_for(db, access, cycle)
    total: Decimal | None = None
    entries: list[dict[str, Any]] = []
    for row in rows:
        amount = _amount(row, denominators)
        if amount is not None:
            total = (total or Decimal(0)) + amount
        entries.append(
            {
                "letter_reference": row.letter_reference,
                "letter_date": row.letter_date.isoformat(),
                "effective_from": row.effective_from.isoformat(),
                "table5_row": row.table5_row,
                "basis": row.basis,
                "basis_value": str(row.basis_value),
                "amount": None if amount is None else str(amount),
            }
        )
    return {"addons": entries}, total


__all__ = [
    "EXAMINER_VISIBLE",
    "active_for",
    "addons_payload",
    "amounts_by_table5_row",
    "confirm_addon",
    "confirmation_conditions",
    "create_addon",
    "list_addons",
    "prepare_letter_download",
    "unattributed_amount",
    "withdraw_addon",
]


def confirmation_conditions(db: Session, ctx: Any, addon_id: UUID) -> tuple[Any, ...]:
    """Four eyes on a supervisory add-on, resolved before authority is evaluated.

    The database enforces ``confirmed_by <> created_by`` as well. This layer
    exists so the caller is told they are not an eligible approver, rather than
    meeting a constraint error after the authority check has already passed.
    """
    from app.core.authorization import ConditionCheck, ConditionKind  # noqa: PLC0415

    row = db.scalar(
        select(BankSupervisoryAddon).where(
            BankSupervisoryAddon.id == addon_id,
            BankSupervisoryAddon.organization_id == ctx.organization_id,
        )
    )
    distinct = row is None or ctx.actor_user_id is None or row.created_by != ctx.actor_user_id
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=distinct,
            reason=(
                "supervisory add-on approver is distinct from the person who recorded it"
                if distinct
                else "the person who recorded a supervisory add-on cannot confirm it"
            ),
        ),
    )
