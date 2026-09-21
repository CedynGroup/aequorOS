"""¶74: revising a filed ICAAP, and updating it after a material change.

Two different things wear the same button, and keeping them apart is the whole
point of this module.

A **revision** corrects what was filed. Same year end, same basis, same return
code — a superseding VERSION of the filing already with the regulator. It is
allowed to exist while the original is still ``submitted`` (the open-cycle index
deliberately excludes submitted and acknowledged cycles), because a bank that
has spotted an error must be able to start the correction before the supervisor
answers. Once the original has been ACKNOWLEDGED it is final at the regulator,
and a correction needs a granted resubmission — checked here so the preparer
finds out before writing anything, and enforced again at generation.

An **update** is a new assessment after a material change or on a supervisor's
request. New as-of date, its own return code, and the original is untouched —
it was true when it was filed.

Neither invents a deadline. An update triggered by a material change is due "in
a timely manner" under the Guideline, so it is either a date the bank sets for
itself or nothing at all, recorded as ``timely`` (REG-ICAAP-073).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.icaap import prosemirror
from app.domain.icaap.frameworks.schema import Framework
from app.models import RegulatoryPackage
from app.models.icaap import (
    IcaapAttachment,
    IcaapCycle,
    IcaapDataBlock,
    IcaapSection,
    IcaapSectionVersion,
)
from app.schemas.icaap import IcaapCloneCreate, IcaapCycleRead
from app.services.audit import record_event
from app.services.icaap import freeze as freeze_service
from app.services.icaap import guards

#: A cycle can only be revised or updated once it has been through the Board.
CLONEABLE_STATUSES: frozenset[str] = frozenset({"board_approved", "submitted", "acknowledged"})
_UPDATE_KINDS: frozenset[str] = frozenset({"material_change", "regulator_request"})
#: The ``icaap_cycles.title`` column length.
_TITLE_MAX = 200


def _source_or_404(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapCycle:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    if cycle.status not in CLONEABLE_STATUSES:
        raise guards.conflict(
            "cycle_not_cloneable",
            "An ICAAP can be revised or updated once the Board has approved it.",
            status=cycle.status,
        )
    return cycle


def _require_resubmission_grant(db: Session, access: IcaapAccess, source: IcaapCycle) -> None:
    """An acknowledged filing is final at the regulator until they grant a redo."""
    if source.status != "acknowledged" or source.package_id is None:
        return
    package = db.get(RegulatoryPackage, source.package_id)
    if package is None:  # pragma: no cover - the FK forbids it
        return
    from app.services.regulatory_reporting.workflow import (  # noqa: PLC0415 - avoid a cycle
        granted_unconsumed_resubmission,
    )

    if granted_unconsumed_resubmission(db, package) is None:
        raise guards.conflict(
            "resubmission_required",
            "The regulator acknowledged this return. Correcting it needs a granted "
            "resubmission request on the acknowledged package first.",
            package_id=str(package.id),
        )


def _copy_sections(
    db: Session, access: IcaapAccess, source: IcaapCycle, target: IcaapCycle, framework: Framework
) -> int:
    """Carry each section's committed text into the new cycle as version 1.

    The person cloning becomes the committer, and therefore a MAKER of the new
    cycle: they are putting this text forward again, so they cannot then review
    it. ``carried_from`` records where it came from, so the new report can say
    which parts are unchanged from the filing it revises.
    """
    latest: dict[str, IcaapSectionVersion] = {}
    for version in db.scalars(
        select(IcaapSectionVersion)
        .where(
            IcaapSectionVersion.organization_id == access.ctx.organization_id,
            IcaapSectionVersion.cycle_id == source.id,
        )
        .order_by(IcaapSectionVersion.version_no.asc())
    ):
        latest[version.section_key] = version
    source_rows = {
        row.section_key: row
        for row in db.scalars(
            select(IcaapSection).where(
                IcaapSection.organization_id == access.ctx.organization_id,
                IcaapSection.cycle_id == source.id,
            )
        )
    }
    actor = guards.actor_id(access)
    carried = 0
    for section in framework.sections:
        source_row = source_rows.get(section.key)
        version = latest.get(section.key)
        row = IcaapSection(
            organization_id=target.organization_id,
            bank_id=target.bank_id,
            cycle_id=target.id,
            section_key=section.key,
            letter=section.letter,
            position=section.order,
            working_doc=dict(version.doc) if version is not None else dict(prosemirror.EMPTY_DOC),
            working_rev=1 if version is not None else 0,
            checklist_state=dict(source_row.checklist_state or {}) if source_row else {},
            carried_from=(
                None
                if version is None
                else {
                    "cycle_id": str(source.id),
                    "section_key": section.key,
                    "version_no": version.version_no,
                    "doc_sha256": version.doc_sha256,
                }
            ),
        )
        db.add(row)
        db.flush()
        if version is None:
            continue
        carried += 1
        copy = IcaapSectionVersion(
            organization_id=target.organization_id,
            bank_id=target.bank_id,
            cycle_id=target.id,
            section_id=row.id,
            section_key=section.key,
            version_no=1,
            round=1,
            source_rev=1,
            editor_schema_version=version.editor_schema_version,
            doc=dict(version.doc),
            plain_text=version.plain_text,
            doc_sha256=version.doc_sha256,
            fact_refs=list(version.fact_refs or []),
            block_refs=list(version.block_refs or []),
            commit_note=f"Carried from {source.title}",
            committed_by=actor,
        )
        db.add(copy)
        db.flush()
        row.committed_version_no = 1
        row.committed_from_rev = 1
    return carried


def _copy_blocks(db: Session, access: IcaapAccess, source: IcaapCycle, target: IcaapCycle) -> int:
    """Recreate the blocks, UNBOUND.

    A figure is bound to an as-of date and a sealed run. Carrying the binding
    across would present last year's number as this assessment's, so every
    block is resolved again against the new cycle's own date.
    """
    actor = guards.actor_id(access)
    count = 0
    for block in db.scalars(
        select(IcaapDataBlock).where(
            IcaapDataBlock.organization_id == access.ctx.organization_id,
            IcaapDataBlock.cycle_id == source.id,
            IcaapDataBlock.retired_at.is_(None),
        )
    ):
        db.add(
            IcaapDataBlock(
                organization_id=target.organization_id,
                bank_id=target.bank_id,
                cycle_id=target.id,
                block_type=block.block_type,
                block_key=block.block_key,
                title=block.title,
                params=dict(block.params or {}),
                created_by=actor,
            )
        )
        count += 1
    db.flush()
    return count


def _copy_attachments(
    db: Session, access: IcaapAccess, source: IcaapCycle, target: IcaapCycle
) -> int:
    """Carry the evidence BY REFERENCE: new rows, same object, same digest.

    ``icaap_attachments`` is append-only and the stored object is retained, so
    pointing a second row at the same bytes is safe and keeps the filed
    document byte-identical to the one that was filed before.
    """
    from app.services.icaap import snapshot  # noqa: PLC0415 - mutual read

    actor = guards.actor_id(access)
    count = 0
    for row in snapshot.active_attachments(db, access, source):
        attributes = dict(row.attributes or {})
        attributes["carried_from"] = {
            "cycle_id": str(source.id),
            "attachment_id": str(row.id),
        }
        db.add(
            IcaapAttachment(
                organization_id=target.organization_id,
                bank_id=target.bank_id,
                cycle_id=target.id,
                kind=row.kind,
                title=row.title,
                original_filename=row.original_filename,
                media_type=row.media_type,
                byte_size=row.byte_size,
                sha256=row.sha256,
                storage_tier=row.storage_tier,
                object_path=row.object_path,
                storage_version_id=row.storage_version_id,
                section_key=row.section_key,
                attributes=attributes,
                uploaded_by=actor,
            )
        )
        count += 1
    db.flush()
    return count


def _revision_target(source: IcaapCycle, payload: IcaapCloneCreate) -> tuple[str, date, str | None]:
    if payload.as_of_date is not None and payload.as_of_date != source.as_of_date:
        raise guards.unprocessable(
            "revision_keeps_as_of",
            "A revision corrects the same assessment date. Use an update for a new one.",
        )
    if source.status == "board_approved":
        # A revision reuses the year end, and an ICAAP the Board has approved
        # but nobody has filed still occupies it. Two open assessments for one
        # year end is the state the index exists to prevent; the honest answer
        # is "file this one, or send it back", not a second draft beside it.
        raise guards.conflict(
            "revision_requires_a_filing",
            "This ICAAP has been approved but not yet filed. Send it back to a review "
            "stage to change it, or file it first and then revise the filing.",
            status=source.status,
        )
    return source.cycle_kind, source.as_of_date, None


def _update_target(payload: IcaapCloneCreate, framework: Framework) -> tuple[str, date, str | None]:
    kind = payload.cycle_kind or "material_change"
    if kind not in _UPDATE_KINDS:
        raise guards.unprocessable(
            "update_kind_invalid",
            "An update is either a material change or a supervisory request.",
            allowed=sorted(_UPDATE_KINDS),
        )
    if payload.as_of_date is None:
        raise guards.unprocessable(
            "as_of_required", "An update needs the position date it assesses."
        )
    if payload.as_of_date > date.today():
        raise guards.unprocessable(
            "as_of_in_future",
            "An ICAAP cannot assess a position date that has not happened yet.",
        )
    if kind == "material_change":
        codes = {trigger.code for trigger in framework.material_change_triggers}
        if payload.change_trigger not in codes:
            raise guards.unprocessable(
                "change_trigger_unknown",
                "Name which material change prompted this update.",
                allowed=sorted(codes),
            )
    elif not payload.regulator_request_ref:
        raise guards.unprocessable(
            "regulator_request_ref_required",
            "Record the supervisor's reference for the request.",
        )
    return kind, payload.as_of_date, payload.change_trigger


def _due_date(payload: IcaapCloneCreate, kind: str) -> tuple[date | None, str]:
    """Never invented. The supervisor's date, the bank's own, or ``timely``."""
    if kind == "regulator_request":
        if payload.requested_due_date is None:
            raise guards.unprocessable(
                "requested_due_date_required",
                "Record the date the supervisor asked for. The platform does not "
                "invent a deadline for a requested ICAAP.",
            )
        return payload.requested_due_date, "regulator_set"
    if payload.requested_due_date is not None:
        return payload.requested_due_date, "bank_set"
    return None, "timely"


def clone_cycle(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapCloneCreate
) -> IcaapCycleRead:
    source = _source_or_404(db, access, cycle_id)
    framework = guards.require_framework(source)
    _require_resubmission_grant(db, access, source)

    if payload.mode == "revision":
        kind, as_of, trigger = _revision_target(source, payload)
        due_date, due_basis = source.due_date, source.due_date_basis
        title = f"{source.title} — revision"
        supersedes = source.id
        description = payload.change_description
        request_ref = source.regulator_request_ref
    else:
        kind, as_of, trigger = _update_target(payload, framework)
        due_date, due_basis = _due_date(payload, kind)
        label = "material change" if kind == "material_change" else "supervisory request"
        title = f"ICAAP update — {label} as at {as_of.isoformat()} ({source.basis})"
        supersedes = None
        description = payload.change_description
        request_ref = payload.regulator_request_ref
    if framework.filing is None or freeze_service.return_code_for(framework, source) is None:
        # Refused up front rather than at freeze: a preparer should not write a
        # whole report before learning the platform cannot file it.
        raise guards.conflict(
            "filing_not_available_for_framework",
            f"{framework.short_title} is published for reference only; the platform "
            "cannot file an assessment to that regulator yet.",
        )

    target = IcaapCycle(
        organization_id=source.organization_id,
        bank_id=source.bank_id,
        fiscal_year=source.fiscal_year if payload.mode == "revision" else as_of.year,
        as_of_date=as_of,
        cycle_kind=kind,
        basis=source.basis,
        subsidiaries_declared=source.subsidiaries_declared,
        title=title[:_TITLE_MAX],
        framework_code=source.framework_code,
        framework_version=source.framework_version,
        framework_sha256=source.framework_sha256,
        status="draft",
        round=1,
        due_date=due_date,
        due_date_basis=due_basis,
        change_trigger=trigger,
        change_description=description,
        regulator_request_ref=request_ref,
        supersedes_cycle_id=supersedes,
        created_by=guards.actor_id(access),
    )
    db.add(target)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise guards.conflict(
            "cycle_exists",
            "An open ICAAP already exists for that year, kind and basis. Finish or "
            "archive it before starting another.",
        ) from exc

    carried = _copy_sections(db, access, source, target, framework)
    blocks = _copy_blocks(db, access, source, target)
    attachments = _copy_attachments(db, access, source, target)
    record_event(
        db,
        access.ctx,
        event_type="icaap.cycle.cloned",
        entity_type="icaap_cycle",
        entity_id=target.id,
        details={
            "source_cycle_id": str(source.id),
            "mode": payload.mode,
            "cycle_kind": kind,
            "as_of_date": as_of.isoformat(),
            "sections_carried": carried,
            "blocks_copied": blocks,
            "attachments_carried": attachments,
            "due_date": None if due_date is None else due_date.isoformat(),
            "due_date_basis": due_basis,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(target)
    from app.services.icaap import cycles as cycles_service  # noqa: PLC0415 - mutual read

    return cycles_service.read(db, access, target)


__all__ = ["CLONEABLE_STATUSES", "clone_cycle"]
