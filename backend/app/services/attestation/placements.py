"""Where each signing role's fields sit on a return.

``pdf_signing`` used to carry two module constants and a guard that the page they
landed on was the rendered attestation page. That was the only defence available
while nobody could see or move the boxes; it is also why the platform could not
offer a signing workspace. Placement is now data, resolved most-specific-first:

    per-package override  →  return template (bank, then organization)
        →  ``pdf_signing.DEFAULT_PLACEMENTS``

The built-in default is the last resort on purpose: a return still has to be
signable when nothing has been placed on it — an API-driven certification, or an
institution that never opens the workspace. It is not what the workspace seeds
from, because those two boxes are a guess about where a form's signature line is
and a wrong guess costs more than an empty page does.

**A signing role has MANY boxes, not one.** A regulator's attestation block asks
for a name, a designation, a signature and a date per officer (BSD3's "Prepared
by (name / designation / signature / date)"), so a placement carries a
``field_type`` and rows repeat per role. Exactly one of them is the signature
field; the rest print values derived from the signature record.

**A placement set is all-or-nothing.** Every box comes from one source rather
than being merged across sources, because they are laid out relative to each
other — an approver box from a template beside a preparer box a colleague dragged
elsewhere would overlap without either person having placed it there. Writing a
template or an override therefore replaces the whole set for that scope.

Every write is reason-required and audited, like the signing-policy endpoints:
the placement decides where an officer's name and permanent signer ID appear on a
document filed with the regulator, and moving it silently is not acceptable even
though it is not itself evidence.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    PackageSignaturePlacement,
    RegulatoryPackage,
    ReturnSignaturePlacement,
)
from app.services.attestation.pdf_signing import (
    DEFAULT_PLACEMENTS,
    FIELD_TYPES,
    ROLE_FIELD_NAMES,
    SIGNATURE_FIELD_TYPE,
    FieldPlacement,
    min_box_size,
    too_small_detail,
)
from app.services.attestation.workflow import AttestationConflict
from app.services.audit import record_event

type PlacementSource = Literal["package", "bank_template", "organization_template", "default"]


@dataclass(frozen=True)
class ResolvedPlacements:
    """The placement set in force, and which of the three sources produced it."""

    source: PlacementSource
    placements: tuple[FieldPlacement, ...]


def resolve(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> ResolvedPlacements:
    """The placements this package's signature fields will be created from."""
    override = _package_rows(db, ctx, package.id)
    if override:
        return ResolvedPlacements("package", _as_placements(override))

    template = _template_rows(db, ctx, package.return_code)
    for_bank = [row for row in template if row.bank_id == package.bank_id]
    if for_bank:
        return ResolvedPlacements("bank_template", _as_placements(for_bank))
    for_org = [row for row in template if row.bank_id is None]
    if for_org:
        return ResolvedPlacements("organization_template", _as_placements(for_org))
    return ResolvedPlacements("default", DEFAULT_PLACEMENTS)


def list_templates(
    db: Session, ctx: TenantContext, *, return_code: str | None = None
) -> list[ReturnSignaturePlacement]:
    statement = select(ReturnSignaturePlacement).where(
        ReturnSignaturePlacement.organization_id == ctx.organization_id
    )
    if return_code is not None:
        statement = statement.where(ReturnSignaturePlacement.return_code == return_code)
    return list(
        db.scalars(
            statement.order_by(
                ReturnSignaturePlacement.return_code,
                ReturnSignaturePlacement.bank_id,
                ReturnSignaturePlacement.signing_role,
                ReturnSignaturePlacement.field_type,
                ReturnSignaturePlacement.field_index,
            )
        )
    )


def upsert_template(  # noqa: PLR0913 - the scope key plus the payload
    db: Session,
    ctx: TenantContext,
    *,
    return_code: str,
    bank_id: str | None,
    placements: Sequence[FieldPlacement],
    reason: str,
) -> list[ReturnSignaturePlacement]:
    """Replace the template placement set for one ``(bank?, return_code)`` scope."""
    placements = _indexed(placements)
    _require_complete(placements)
    scope = (
        ReturnSignaturePlacement.bank_id.is_(None)
        if bank_id is None
        else ReturnSignaturePlacement.bank_id == bank_id
    )
    db.execute(
        delete(ReturnSignaturePlacement).where(
            ReturnSignaturePlacement.organization_id == ctx.organization_id,
            ReturnSignaturePlacement.return_code == return_code,
            scope,
        )
    )
    rows = [
        ReturnSignaturePlacement(
            organization_id=ctx.organization_id,
            bank_id=bank_id,
            return_code=return_code,
            signing_role=placement.signing_role,
            field_type=placement.field_type,
            field_index=placement.field_index,
            page_index=placement.page_index,
            x1=placement.box[0],
            y1=placement.box[1],
            x2=placement.box[2],
            y2=placement.box[3],
            updated_by=ctx.actor_user_id,
            reason=reason,
        )
        for placement in placements
    ]
    db.add_all(rows)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="attestation.signature_placement_template_updated",
        entity_type="return_signature_placement",
        entity_id=return_code,
        details={
            "bank_id": bank_id,
            "return_code": return_code,
            "reason": reason,
            "placements": _audit_detail(placements),
        },
    )
    return rows


def set_package_override(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    placements: Sequence[FieldPlacement],
    reason: str,
) -> list[PackageSignaturePlacement]:
    """Place this package's fields, replacing any earlier override.

    Refused once a signature exists in the current cycle. The fields are part of
    the certified revision, so re-placing them afterwards could only ever mean
    one of two things: a lie (the stored placement no longer describes the filed
    document) or tampering (adding a field the DocMDP policy forbids). An empty
    list clears the override and falls the package back to its return template.
    """
    if package.attestation_state != "unsigned":
        raise AttestationConflict(
            "placement_locked",
            "This return has already been certified, and its signature fields are part "
            "of the certified revision. Void the attestation to place them again — "
            "moving them now would leave a stored placement that does not describe the "
            "filed document.",
        )
    if placements:
        placements = _indexed(placements)
        _require_complete(placements)
    db.execute(
        delete(PackageSignaturePlacement).where(
            PackageSignaturePlacement.organization_id == ctx.organization_id,
            PackageSignaturePlacement.package_id == package.id,
        )
    )
    rows = [
        PackageSignaturePlacement(
            organization_id=ctx.organization_id,
            package_id=package.id,
            signing_role=placement.signing_role,
            field_type=placement.field_type,
            field_index=placement.field_index,
            page_index=placement.page_index,
            x1=placement.box[0],
            y1=placement.box[1],
            x2=placement.box[2],
            y2=placement.box[3],
            placed_by=ctx.actor_user_id,
            reason=reason,
        )
        for placement in placements
    ]
    db.add_all(rows)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="attestation.signature_placement_set",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "reason": reason,
            "cleared": not placements,
            "placements": _audit_detail(placements),
        },
    )
    return rows


# --- internals --------------------------------------------------------------


def _require_complete(placements: Sequence[FieldPlacement]) -> None:
    """Refuse a set the document could not be signed from.

    These are the same rules ``pdf_signing._validate_placements`` enforces
    against the real PDF, minus the ones that need the file (page existence,
    media box). Checking them here too is deliberate: an author must be told
    their box is too small when they save it, not when a colleague's
    certification fails hours later on a filing deadline.
    """
    roles = [placement.signing_role for placement in placements]
    unknown_roles = sorted(set(roles) - set(ROLE_FIELD_NAMES))
    if unknown_roles:
        raise AttestationConflict(
            "placement_role_unsupported",
            f"Signing role(s) {unknown_roles} have no field on the return artifact; only "
            f"{sorted(ROLE_FIELD_NAMES)} can be placed on the document.",
        )
    unknown_types = sorted({p.field_type for p in placements} - set(FIELD_TYPES))
    if unknown_types:
        raise AttestationConflict(
            "placement_field_type_unsupported",
            f"Field type(s) {unknown_types} cannot be placed; the kinds a return "
            f"artifact carries are {list(FIELD_TYPES)}.",
        )
    signature_roles = [
        placement.signing_role
        for placement in placements
        if placement.field_type == SIGNATURE_FIELD_TYPE
    ]
    if len(set(signature_roles)) != len(signature_roles):
        raise AttestationConflict(
            "placement_role_duplicated",
            "Each signing role has exactly one signature field on the document, so the "
            "signature can only be placed once per signer.",
        )
    missing = sorted(set(ROLE_FIELD_NAMES) - set(signature_roles))
    if missing:
        raise AttestationConflict(
            "placement_incomplete",
            f"No signature field was placed for {missing}. Every signature field has to "
            "exist before the preparer certifies — the certification permits only form "
            "filling afterwards — so an unplaced field can never be added later.",
        )
    for placement in placements:
        if placement.page_index < 0:
            raise AttestationConflict(
                "placement_out_of_bounds",
                f"The {placement.signing_role} {placement.field_type} field is placed on "
                f"page {placement.page_index}; pages are numbered from 0.",
            )
        if placement.width <= 0 or placement.height <= 0:
            raise AttestationConflict(
                "placement_out_of_bounds",
                f"The {placement.signing_role} {placement.field_type} field box "
                f"{placement.box} has no area.",
            )
        min_width, min_height = min_box_size(placement.field_type)
        if placement.width < min_width or placement.height < min_height:
            raise AttestationConflict(
                "placement_too_small",
                f"The {placement.signing_role} {placement.field_type} field is "
                f"{placement.width}×{placement.height} points, below the "
                f"{min_width}×{min_height} it needs to print "
                f"{too_small_detail(placement.field_type)}.",
            )


def _indexed(placements: Sequence[FieldPlacement]) -> tuple[FieldPlacement, ...]:
    """Number the boxes: the n-th ``(role, kind)`` box in the list is index n.

    Assigned here rather than sent by the client because it is not a decision
    anyone makes. The index exists only so two date boxes for one signer can be
    told apart — in the AcroForm, which cannot hold two fields of one name, and
    in the unique constraint. A client asked to invent it could collide two
    boxes onto one field without meaning to.
    """
    counts: Counter[tuple[str, str]] = Counter()
    numbered: list[FieldPlacement] = []
    for placement in placements:
        key = (placement.signing_role, placement.field_type)
        counts[key] += 1
        numbered.append(replace(placement, field_index=counts[key]))
    return tuple(numbered)


def _package_rows(
    db: Session, ctx: TenantContext, package_id: UUID
) -> list[PackageSignaturePlacement]:
    return list(
        db.scalars(
            select(PackageSignaturePlacement)
            .where(
                PackageSignaturePlacement.organization_id == ctx.organization_id,
                PackageSignaturePlacement.package_id == package_id,
            )
            .order_by(
                PackageSignaturePlacement.signing_role,
                PackageSignaturePlacement.field_type,
                PackageSignaturePlacement.field_index,
            )
        )
    )


def _template_rows(
    db: Session, ctx: TenantContext, return_code: str
) -> list[ReturnSignaturePlacement]:
    return list(
        db.scalars(
            select(ReturnSignaturePlacement)
            .where(
                ReturnSignaturePlacement.organization_id == ctx.organization_id,
                ReturnSignaturePlacement.return_code == return_code,
            )
            .order_by(
                ReturnSignaturePlacement.signing_role,
                ReturnSignaturePlacement.field_type,
                ReturnSignaturePlacement.field_index,
            )
        )
    )


def _as_placements(
    rows: Sequence[PackageSignaturePlacement] | Sequence[ReturnSignaturePlacement],
) -> tuple[FieldPlacement, ...]:
    return tuple(
        FieldPlacement(
            signing_role=row.signing_role,
            page_index=row.page_index,
            box=(row.x1, row.y1, row.x2, row.y2),
            field_type=row.field_type,
            field_index=row.field_index,
        )
        for row in rows
    )


def _audit_detail(placements: Sequence[FieldPlacement]) -> list[dict[str, object]]:
    return [
        {
            "signing_role": placement.signing_role,
            "field_type": placement.field_type,
            "field_index": placement.field_index,
            "page_index": placement.page_index,
            "box": list(placement.box),
        }
        for placement in placements
    ]


__all__ = [
    "PlacementSource",
    "ResolvedPlacements",
    "list_templates",
    "resolve",
    "set_package_override",
    "upsert_template",
]
