"""ICAAP cycles: creating one, listing them, retiring them, moving a framework version.

A cycle is one bank's ICAAP for one year end on one reporting basis. Three
decisions in here are worth reading before changing anything.

**The as-of date is the regulator's, not the client's.** For an annual or a
rehearsal cycle it is the framework's own financial year end for the chosen
fiscal year; a client-supplied date is ignored rather than honoured. The same
mistake in the Returns workspace once made the filing calendar a function of
ingestion cadence.

**A due date is never invented.** It comes from the framework's governed
deadline for an annual cycle, from the regulator's letter for a requested one,
and for a material change — where the Guideline says only "in a timely manner"
— it is either what the bank sets for itself or nothing at all, recorded as
``timely``. Nothing here guesses a date a supervisor might hold a bank to.

**A bank with subsidiaries needs two cycles.** Solo and consolidated are
separate assessments with different numbers, so they are separate cycles with a
companion relationship that readiness checks, not one cycle with a flag.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.core.config import get_settings
from app.db.base import utc_now
from app.domain.icaap.frameworks import rebase as rebase_domain
from app.domain.icaap.frameworks import registry
from app.domain.icaap.frameworks.schema import Framework
from app.domain.icaap.prosemirror import EMPTY_DOC
from app.models import InstitutionProfile
from app.models.icaap import IcaapCycle, IcaapDataBlock, IcaapSection
from app.schemas.icaap import (
    IcaapBlockTypeListRead,
    IcaapCycleArchive,
    IcaapCycleCreate,
    IcaapCycleListRead,
    IcaapCycleRead,
    IcaapCycleRebase,
    IcaapCycleSummaryRead,
    IcaapCycleUpdate,
    IcaapFrameworkListRead,
    IcaapFrameworkRead,
)
from app.services import institution_types
from app.services.audit import record_event
from app.services.icaap import frameworks, guards, parameters, sections, serializers

_ARCHIVABLE = frozenset({"draft", "returned"})


def _enabled_codes() -> frozenset[str]:
    return get_settings().icaap.enabled_framework_codes


def _applicable(
    db: Session, access: IcaapAccess, as_of: date, cycle_kind: str
) -> tuple[Framework, ...]:
    frameworks.sync_extra_roots()
    return registry.applicable(
        access.bank.jurisdiction_code,
        institution_types.institution_class(db, access.bank),
        as_of,
        cycle_kind=cycle_kind,
        enabled_codes=_enabled_codes(),
    )


def list_frameworks(db: Session, access: IcaapAccess) -> IcaapFrameworkListRead:
    """Every framework this institution could pin, for either kind of cycle."""
    today = date.today()
    seen: dict[tuple[str, str], Framework] = {}
    for cycle_kind in ("annual", "rehearsal"):
        for framework in _applicable(db, access, today, cycle_kind):
            seen[(framework.code, framework.version)] = framework
    ordered = sorted(seen.values(), key=lambda f: (f.effective_from, f.version), reverse=True)
    return IcaapFrameworkListRead(
        frameworks=[serializers.framework_summary(framework) for framework in ordered]
    )


def get_framework(db: Session, access: IcaapAccess, code: str, version: str) -> IcaapFrameworkRead:
    frameworks.sync_extra_roots()
    try:
        framework = registry.get(code, version)
    except registry.FrameworkNotFound:
        guards.not_found()
    if framework.code not in _enabled_codes():
        guards.not_found()
    return serializers.framework_read(db, access.bank, framework)


def default_framework(db: Session, access: IcaapAccess) -> Framework:
    """The newest framework this institution could pin today.

    P3's review-chain settings need a framework to derive the default stages
    from before any cycle exists. Annual first, then rehearsal, because a
    rehearsal is date-agnostic and a bank whose first filing date is still
    ahead would otherwise have no chain to show.
    """
    today = date.today()
    for cycle_kind in ("annual", "rehearsal"):
        applicable = _applicable(db, access, today, cycle_kind)
        if applicable:
            return applicable[0]
    raise guards.conflict(
        "framework_unavailable",
        "No ICAAP framework is published for this institution, so there is no "
        "review chain to derive.",
    )


def list_block_types() -> IcaapBlockTypeListRead:
    return IcaapBlockTypeListRead(block_types=serializers.block_types())


def cycle_conditions(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> frozenset[str]:
    """Which of the framework's ``applies_when`` conditions hold for this cycle."""
    conditions: set[str] = {f"cycle_kind:{cycle.cycle_kind}"}
    if cycle.subsidiaries_declared:
        conditions.add("has_subsidiaries")
        conditions.add("group_member")
    profile = db.scalar(
        select(InstitutionProfile).where(
            InstitutionProfile.organization_id == access.ctx.organization_id,
            InstitutionProfile.bank_id == access.bank.id,
        )
    )
    parent = profile.parent_country_code if profile is not None else None
    if parent and parent != access.bank.jurisdiction_code:
        conditions.add("foreign_bank_subsidiary")
        conditions.add("group_member")
    return frozenset(conditions)


def _summary(
    cycle: IcaapCycle, framework: Framework, digest_matches: bool
) -> IcaapCycleSummaryRead:
    return IcaapCycleSummaryRead(
        id=cycle.id,
        bank_id=cycle.bank_id,
        fiscal_year=cycle.fiscal_year,
        as_of_date=cycle.as_of_date,
        cycle_kind=cycle.cycle_kind,  # pyright: ignore[reportArgumentType]
        basis=cycle.basis,  # pyright: ignore[reportArgumentType]
        title=cycle.title,
        status=cycle.status,
        round=cycle.round,
        due_date=cycle.due_date,
        due_date_basis=cycle.due_date_basis,  # pyright: ignore[reportArgumentType]
        framework=serializers.framework_summary(framework),
        framework_digest_matches=digest_matches,
        editable=cycle.status in guards.EDITABLE_STATUSES,
        created_at=cycle.created_at,
        updated_at=cycle.updated_at,
    )


def read(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> IcaapCycleRead:
    framework, digest_matches = guards.framework_for(cycle)
    summary = _summary(cycle, framework, digest_matches)
    return IcaapCycleRead(
        **summary.model_dump(),
        subsidiaries_declared=cycle.subsidiaries_declared,
        current_stage_seq=cycle.current_stage_seq,
        change_trigger=cycle.change_trigger,
        change_description=cycle.change_description,
        regulator_request_ref=cycle.regulator_request_ref,
        package_id=cycle.package_id,
        supersedes_cycle_id=cycle.supersedes_cycle_id,
        rebased_from_cycle_id=cycle.rebased_from_cycle_id,
        created_by=cycle.created_by,
        sections=sections.section_summaries(db, access, cycle, framework),
    )


def list_cycles(
    db: Session,
    access: IcaapAccess,
    *,
    fiscal_year: int | None = None,
    include_archived: bool = False,
) -> IcaapCycleListRead:
    statement = select(IcaapCycle).where(
        IcaapCycle.organization_id == access.ctx.organization_id,
        IcaapCycle.bank_id == access.bank.id,
    )
    if access.examiner:
        statement = statement.where(
            IcaapCycle.frozen_at.is_not(None),
            IcaapCycle.status.in_(sorted(guards.EXAMINER_VISIBLE_STATUSES)),
        )
    if fiscal_year is not None:
        statement = statement.where(IcaapCycle.fiscal_year == fiscal_year)
    if not include_archived:
        statement = statement.where(IcaapCycle.status != "archived")
    rows = db.scalars(
        statement.order_by(IcaapCycle.fiscal_year.desc(), IcaapCycle.created_at.desc())
    ).all()
    summaries: list[IcaapCycleSummaryRead] = []
    for cycle in rows:
        framework, digest_matches = guards.framework_for(cycle)
        summaries.append(_summary(cycle, framework, digest_matches))
    return IcaapCycleListRead(cycles=summaries)


def _resolve_as_of(framework: Framework, payload: IcaapCycleCreate) -> date:
    if payload.cycle_kind in {"annual", "rehearsal"}:
        return framework.deadline.fy_end(payload.fiscal_year)
    if payload.as_of_date is None:
        raise guards.unprocessable(
            "as_of_required",
            "An off-cycle ICAAP needs the position date it assesses.",
        )
    if payload.as_of_date > date.today():
        raise guards.unprocessable(
            "as_of_in_future",
            "An ICAAP cannot assess a position date that has not happened yet.",
        )
    return payload.as_of_date


def _resolve_due_date(
    db: Session, access: IcaapAccess, framework: Framework, payload: IcaapCycleCreate, as_of: date
) -> tuple[date | None, str]:
    if payload.cycle_kind in {"annual", "rehearsal"}:
        months = parameters.resolve_int(
            db,
            access.bank,
            framework.deadline.months_after_fye_param,
            purpose="The ICAAP filing deadline is measured in months after the year end.",
        )
        return framework.deadline.due_date(as_of, months), "framework"
    if payload.cycle_kind == "regulator_request":
        if payload.requested_due_date is None:
            raise guards.unprocessable(
                "requested_due_date_required",
                "Record the date the supervisor asked for. The platform does not "
                "invent a deadline for a requested ICAAP.",
            )
        return payload.requested_due_date, "regulator_set"
    if payload.requested_due_date is not None:
        return payload.requested_due_date, "bank_set"
    # The Guideline says a material-change update is submitted in a timely
    # manner and names no date. Recording "timely" is the honest answer.
    return None, "timely"


def _default_title(framework: Framework, payload: IcaapCycleCreate, as_of: date) -> str:
    basis = "Solo" if payload.basis == "solo" else "Consolidated"
    if payload.cycle_kind == "rehearsal":
        return f"ICAAP rehearsal FY{payload.fiscal_year} ({basis})"
    if payload.cycle_kind == "annual":
        return f"ICAAP FY{payload.fiscal_year} ({basis})"
    label = "material change" if payload.cycle_kind == "material_change" else "supervisory request"
    return f"ICAAP update — {label} as at {as_of.isoformat()} ({basis})"


def create_cycle(db: Session, access: IcaapAccess, payload: IcaapCycleCreate) -> IcaapCycleRead:
    frameworks.sync_extra_roots()
    try:
        framework = registry.get(payload.framework_code, payload.framework_version)
    except registry.FrameworkNotFound as exc:
        raise guards.unprocessable(
            "framework_unknown", "That ICAAP framework version is not published."
        ) from exc

    as_of = _resolve_as_of(framework, payload)
    if framework not in _applicable(db, access, as_of, payload.cycle_kind):
        raise guards.conflict(
            "framework_not_applicable",
            f"{framework.short_title} does not apply to this institution for {as_of.isoformat()}.",
            framework_code=framework.code,
            framework_version=framework.version,
        )
    if payload.cycle_kind == "material_change":
        codes = {trigger.code for trigger in framework.material_change_triggers}
        if payload.change_trigger not in codes:
            raise guards.unprocessable(
                "change_trigger_unknown",
                "Name which material change prompted this update.",
                allowed=sorted(codes),
            )
    if payload.cycle_kind == "regulator_request" and not payload.regulator_request_ref:
        raise guards.unprocessable(
            "regulator_request_ref_required",
            "Record the supervisor's reference for the request.",
        )

    due_date, due_basis = _resolve_due_date(db, access, framework, payload, as_of)
    cycle = IcaapCycle(
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        fiscal_year=payload.fiscal_year,
        as_of_date=as_of,
        cycle_kind=payload.cycle_kind,
        basis=payload.basis,
        subsidiaries_declared=payload.subsidiaries_declared,
        title=payload.title or _default_title(framework, payload, as_of),
        framework_code=framework.code,
        framework_version=framework.version,
        framework_sha256=framework.digest,
        status="draft",
        round=1,
        due_date=due_date,
        due_date_basis=due_basis,
        change_trigger=payload.change_trigger,
        change_description=payload.change_description,
        regulator_request_ref=payload.regulator_request_ref,
        created_by=guards.actor_id(access),
    )
    db.add(cycle)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise guards.conflict(
            "cycle_exists",
            "An open ICAAP already exists for that year, kind and basis. Archive it "
            "or continue working in it.",
        ) from exc

    for section in framework.sections:
        db.add(
            IcaapSection(
                organization_id=cycle.organization_id,
                bank_id=cycle.bank_id,
                cycle_id=cycle.id,
                section_key=section.key,
                letter=section.letter,
                position=section.order,
                working_doc=dict(EMPTY_DOC),
                working_rev=0,
                checklist_state={},
            )
        )
    record_event(
        db,
        access.ctx,
        event_type="icaap.cycle.created",
        entity_type="icaap_cycle",
        entity_id=cycle.id,
        details={
            "bank_id": cycle.bank_id,
            "fiscal_year": cycle.fiscal_year,
            "cycle_kind": cycle.cycle_kind,
            "basis": cycle.basis,
            "framework": f"{framework.code} {framework.version}",
            "framework_sha256": framework.digest,
            "due_date": None if due_date is None else due_date.isoformat(),
            "due_date_basis": due_basis,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(cycle)
    return read(db, access, cycle)


def get_cycle(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapCycleRead:
    return read(db, access, guards.get_cycle_or_404(db, access, cycle_id))


def update_cycle(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapCycleUpdate
) -> IcaapCycleRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    changed: dict[str, object] = {}
    if payload.title is not None and payload.title != cycle.title:
        cycle.title = payload.title
        changed["title"] = payload.title
    if (
        payload.subsidiaries_declared is not None
        and payload.subsidiaries_declared != cycle.subsidiaries_declared
    ):
        cycle.subsidiaries_declared = payload.subsidiaries_declared
        changed["subsidiaries_declared"] = payload.subsidiaries_declared
    if payload.due_date is not None:
        if cycle.due_date_basis == "framework":
            raise guards.conflict(
                "due_date_is_governed",
                "This ICAAP's deadline comes from the framework and the governed "
                "filing period. Change the period in the operator console.",
            )
        cycle.due_date = payload.due_date
        cycle.due_date_basis = "bank_set"
        changed["due_date"] = payload.due_date.isoformat()
    record_event(
        db,
        access.ctx,
        event_type="icaap.cycle.updated",
        entity_type="icaap_cycle",
        entity_id=cycle.id,
        details={"changed": changed, "reason": payload.reason},
    )
    db.commit()
    db.refresh(cycle)
    return read(db, access, cycle)


def archive_cycle(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapCycleArchive
) -> IcaapCycleRead:
    """Retire a cycle that will never be filed — a rehearsal, or a false start."""
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    if cycle.status not in _ARCHIVABLE or cycle.package_id is not None:
        raise guards.conflict(
            "cycle_not_archivable",
            "Only a draft or returned ICAAP that has never been frozen can be archived.",
            status=cycle.status,
        )
    cycle.status = "archived"
    cycle.archived_at = utc_now()
    cycle.archived_by = guards.actor_id(access)
    cycle.archive_reason = payload.reason
    record_event(
        db,
        access.ctx,
        event_type="icaap.cycle.archived",
        entity_type="icaap_cycle",
        entity_id=cycle.id,
        details={"reason": payload.reason},
    )
    db.commit()
    db.refresh(cycle)
    return read(db, access, cycle)


def rebase_cycle(  # noqa: PLR0915 - one linear carry, kept in one readable place
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapCycleRebase
) -> IcaapCycleRead:
    """Move a cycle onto a newer framework version, carrying what maps cleanly.

    A rebase never edits the old cycle's text. It creates a new cycle and copies
    each section's latest committed text (or its working draft when nothing is
    committed) into the mapped section. Blocks are recreated but NOT rebound:
    the new version may ask a different question of the same figures, so every
    one is resolved again. Anything the mapping calls partial, split or merge is
    marked for review rather than presented as carried over.
    """
    source_cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    if source_cycle.frozen_at is not None or source_cycle.status not in guards.EDITABLE_STATUSES:
        raise guards.conflict(
            "cycle_sealed",
            "A frozen ICAAP keeps the framework version it was approved under.",
            status=source_cycle.status,
        )
    source_framework = guards.require_framework(source_cycle)
    try:
        target = registry.get(payload.framework_code, payload.framework_version)
    except registry.FrameworkNotFound as exc:
        raise guards.unprocessable(
            "framework_unknown", "That ICAAP framework version is not published."
        ) from exc
    try:
        plan = rebase_domain.plan_rebase(source_framework, target)
    except rebase_domain.RebaseNotPossible as exc:
        raise guards.conflict("no_newer_framework", str(exc)) from exc

    new_cycle = IcaapCycle(
        organization_id=source_cycle.organization_id,
        bank_id=source_cycle.bank_id,
        fiscal_year=source_cycle.fiscal_year,
        as_of_date=source_cycle.as_of_date,
        cycle_kind=source_cycle.cycle_kind,
        basis=source_cycle.basis,
        subsidiaries_declared=source_cycle.subsidiaries_declared,
        title=source_cycle.title,
        framework_code=target.code,
        framework_version=target.version,
        framework_sha256=target.digest,
        status="draft",
        round=1,
        due_date=source_cycle.due_date,
        due_date_basis=source_cycle.due_date_basis,
        change_trigger=source_cycle.change_trigger,
        change_description=source_cycle.change_description,
        regulator_request_ref=source_cycle.regulator_request_ref,
        rebased_from_cycle_id=source_cycle.id,
        created_by=guards.actor_id(access),
    )
    source_cycle.status = "superseded"
    source_cycle.superseded_at = utc_now()
    db.add(new_cycle)
    db.flush()

    source_sections = {
        section.section_key: section
        for section in db.scalars(
            select(IcaapSection).where(
                IcaapSection.organization_id == access.ctx.organization_id,
                IcaapSection.cycle_id == source_cycle.id,
            )
        )
    }
    block_id_map = _copy_blocks(db, access, source_cycle, new_cycle)
    carried = sections.carry_sections(
        db,
        access,
        source_cycle=source_cycle,
        new_cycle=new_cycle,
        target=target,
        plan=plan,
        source_sections=source_sections,
        block_id_map=block_id_map,
    )
    record_event(
        db,
        access.ctx,
        event_type="icaap.cycle.rebased",
        entity_type="icaap_cycle",
        entity_id=new_cycle.id,
        details={
            "from_cycle_id": str(source_cycle.id),
            "from_framework": f"{source_framework.code} {source_framework.version}",
            "to_framework": f"{target.code} {target.version}",
            "sections_carried": carried,
            "sections_needing_review": list(plan.needs_review),
            "new_sections": list(plan.new_sections),
            "dropped_requirement_ids": sorted(plan.dropped_items),
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(new_cycle)
    return read(db, access, new_cycle)


def _copy_blocks(
    db: Session, access: IcaapAccess, source_cycle: IcaapCycle, new_cycle: IcaapCycle
) -> dict[str, str]:
    """Recreate the source cycle's live blocks, unbound, and map old id to new."""
    mapping: dict[str, str] = {}
    rows = db.scalars(
        select(IcaapDataBlock).where(
            IcaapDataBlock.organization_id == access.ctx.organization_id,
            IcaapDataBlock.cycle_id == source_cycle.id,
            IcaapDataBlock.retired_at.is_(None),
        )
    ).all()
    for block in rows:
        replacement = IcaapDataBlock(
            organization_id=new_cycle.organization_id,
            bank_id=new_cycle.bank_id,
            cycle_id=new_cycle.id,
            block_type=block.block_type,
            block_key=block.block_key,
            title=block.title,
            params=dict(block.params or {}),
            created_by=guards.actor_id(access),
        )
        db.add(replacement)
        db.flush()
        mapping[str(block.id)] = str(replacement.id)
    return mapping


__all__ = [
    "archive_cycle",
    "create_cycle",
    "default_framework",
    "cycle_conditions",
    "get_cycle",
    "get_framework",
    "list_block_types",
    "list_cycles",
    "list_frameworks",
    "read",
    "rebase_cycle",
    "update_cycle",
]
