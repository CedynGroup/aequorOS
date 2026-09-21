"""ICAAP data blocks: binding a figure, refreshing it, and pinning it deliberately.

A block is created once per cycle and bound many times. Each binding is an
append-only row that records the source it pinned, the payload it produced and
the named facts a sentence may quote, so "what did this report say in March, and
where did that number come from" is answerable from the database alone.

Refreshing is not the same as changing. A refresh that resolves the same source
and produces the same payload writes nothing — otherwise every page load would
grow the evidence trail. A refresh that resolves something different writes a
new binding, clears any pin, and reports which figures moved.

Pinning is the deliberate opposite of refreshing: "newer figures exist, this
report keeps the earlier ones, because ...". It requires a reason and it is
never available when the source's own inputs have been withdrawn.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.domain.icaap.blocks import (
    AVAILABLE_BLOCK_TYPES,
    BLOCK_CATALOGUE,
    BindingSnapshot,
    BlockSpec,
    BlockStatus,
    SourceProbe,
    evaluate_status,
)
from app.domain.icaap.readiness import BlockState
from app.models.icaap import IcaapAttachment, IcaapBlockBinding, IcaapCycle, IcaapDataBlock
from app.schemas.icaap import (
    IcaapBlockBindingListRead,
    IcaapBlockBindingRead,
    IcaapBlockRefreshRead,
    IcaapDataBlockCreate,
    IcaapDataBlockListRead,
    IcaapDataBlockPin,
    IcaapDataBlockRead,
    IcaapDataBlockRefresh,
    IcaapDataBlockRetire,
    IcaapFactChangeRead,
    IcaapFactValueRead,
    IcaapManualTablePut,
)
from app.services import jurisdictions
from app.services.audit import record_event
from app.services.icaap import guards, resolvers, serializers, sources
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable
from app.services.icaap.resolvers import manual as manual_resolver

_SLUG = re.compile(r"[^a-z0-9]+")
_MAX_SLUG = 40


def _spec(block_type: str) -> BlockSpec:
    spec = BLOCK_CATALOGUE.get(block_type)
    if spec is None:
        raise guards.unprocessable(
            "block_type_unavailable", "That kind of figure block does not exist."
        )
    return spec


def _context(db: Session, access: IcaapAccess, cycle: IcaapCycle, block: IcaapDataBlock):
    return ResolveContext(
        db=db,
        access=access,
        cycle=cycle,
        block=block,
        period=sources.period_for(db, access, cycle.as_of_date),
        currency=jurisdictions.base_currency(access.bank),
    )


def _block_or_404(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, block_id: UUID
) -> IcaapDataBlock:
    block = db.scalar(
        select(IcaapDataBlock).where(
            IcaapDataBlock.id == block_id,
            IcaapDataBlock.organization_id == access.ctx.organization_id,
            IcaapDataBlock.cycle_id == cycle.id,
        )
    )
    if block is None:
        guards.not_found()
    return block


def _bindings(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> dict[UUID, IcaapBlockBinding]:
    latest: dict[UUID, IcaapBlockBinding] = {}
    for binding in db.scalars(
        select(IcaapBlockBinding)
        .where(
            IcaapBlockBinding.organization_id == access.ctx.organization_id,
            IcaapBlockBinding.cycle_id == cycle.id,
        )
        .order_by(IcaapBlockBinding.seq.asc())
    ):
        latest[binding.block_id] = binding
    return latest


def current_bindings(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> Mapping[UUID, IcaapBlockBinding]:
    """The newest binding of every block — what the exports and the UI render."""
    return _bindings(db, access, cycle)


def current_bindings_by_type(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> dict[str, IcaapBlockBinding]:
    """The newest binding of each block TYPE, for callers that quote a figure.

    Pillar 2 methods and appetite metrics read figures through their block
    bindings rather than from the engines directly, so a computation and the
    sentence beside it in the report cannot cite different numbers. A retired
    block is excluded: retiring it is how a preparer says the report no longer
    rests on it.
    """
    latest = _bindings(db, access, cycle)
    blocks_by_id = {
        block.id: block
        for block in db.scalars(
            select(IcaapDataBlock).where(
                IcaapDataBlock.organization_id == access.ctx.organization_id,
                IcaapDataBlock.cycle_id == cycle.id,
                IcaapDataBlock.retired_at.is_(None),
            )
        )
    }
    by_type: dict[str, IcaapBlockBinding] = {}
    for block_id, binding in latest.items():
        block = blocks_by_id.get(block_id)
        if block is None:
            continue
        existing = by_type.get(block.block_type)
        if existing is None or binding.seq > existing.seq:
            by_type[block.block_type] = binding
    return by_type


def fact_value(binding: IcaapBlockBinding | None, fact_key: str) -> str | None:
    """One quoted figure from a binding, as the string the binding stored."""
    if binding is None:
        return None
    entry = (binding.facts or {}).get(fact_key)
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    return None if value is None else str(value)


def _current_binding(
    db: Session, access: IcaapAccess, block: IcaapDataBlock
) -> IcaapBlockBinding | None:
    return db.scalar(
        select(IcaapBlockBinding)
        .where(
            IcaapBlockBinding.organization_id == access.ctx.organization_id,
            IcaapBlockBinding.block_id == block.id,
        )
        .order_by(IcaapBlockBinding.seq.desc())
        .limit(1)
    )


def _probe(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, block: IcaapDataBlock
) -> SourceProbe:
    resolver = resolvers.registry().get(block.block_type)
    if resolver is None:
        return SourceProbe(current_key=None, reason="no resolver for this block type")
    try:
        return resolver.probe(_context(db, access, cycle, block))
    except Unavailable as exc:
        return SourceProbe(current_key=None, reason=exc.reason)


def _status(
    spec: BlockSpec,
    block: IcaapDataBlock,
    binding: IcaapBlockBinding | None,
    probe: SourceProbe,
    cycle: IcaapCycle,
) -> BlockStatus:
    snapshot = (
        None
        if binding is None
        else BindingSnapshot(
            seq=binding.seq,
            source_key=binding.source_key,
            source_as_of=binding.source_as_of,
            manual=binding.source_kind == "manual",
        )
    )
    return evaluate_status(
        spec,
        snapshot,
        probe,
        cycle_as_of=cycle.as_of_date,
        pinned_seq=block.pinned_binding_seq,
    )


def _fact_reads(facts: Mapping[str, Any]) -> dict[str, IcaapFactValueRead]:
    out: dict[str, IcaapFactValueRead] = {}
    for key, value in (facts or {}).items():
        if not isinstance(value, dict):
            continue
        out[key] = IcaapFactValueRead(
            key=key,
            label=str(value.get("label", key)),
            kind=str(value.get("kind", "text")),
            value=value.get("value"),
            unit=value.get("unit"),
            currency=value.get("currency"),
        )
    return out


def _binding_read(binding: IcaapBlockBinding, *, include_payload: bool) -> IcaapBlockBindingRead:
    return IcaapBlockBindingRead(
        id=binding.id,
        seq=binding.seq,
        resolver=binding.resolver,
        resolver_version=binding.resolver_version,
        source_kind=binding.source_kind,
        source_ref=binding.source_ref,
        source_key=binding.source_key,
        source_as_of=binding.source_as_of,
        payload=binding.payload if include_payload else None,
        facts=_fact_reads(binding.facts),
        payload_sha256=binding.payload_sha256,
        evidence_attachment_id=binding.evidence_attachment_id,
        reason=binding.reason,
        bound_by=binding.bound_by,
        created_at=binding.created_at,
    )


_STATUS_COPY: Mapping[BlockStatus, str] = {
    BlockStatus.UNBOUND: "Not linked yet.",
    BlockStatus.FRESH: "Up to date.",
    BlockStatus.STALE: "Newer figures are available.",
    BlockStatus.AS_OF_MISMATCH: "These figures are not as at this ICAAP's position date.",
    BlockStatus.SOURCE_WITHDRAWN: "The data behind these figures was withdrawn.",
    BlockStatus.SOURCE_MISSING: "The source for these figures is no longer available.",
    BlockStatus.PINNED: "Kept at earlier figures.",
}


def _read(  # noqa: PLR0913 - a block read is its five parts
    block: IcaapDataBlock,
    spec: BlockSpec,
    binding: IcaapBlockBinding | None,
    status: BlockStatus,
    probe: SourceProbe,
    *,
    include_payload: bool,
) -> IcaapDataBlockRead:
    detail = probe.reason if status in {BlockStatus.UNBOUND, BlockStatus.SOURCE_MISSING} else None
    return IcaapDataBlockRead(
        id=block.id,
        cycle_id=block.cycle_id,
        block_type=block.block_type,
        block_key=block.block_key,
        title=block.title,
        params={key: value for key, value in (block.params or {}).items() if key != "table"},
        status=status.value,  # pyright: ignore[reportArgumentType]
        status_detail=detail or _STATUS_COPY[status],
        pin_reason=block.pin_reason,
        pinned_at=block.pinned_at,
        retired_at=block.retired_at,
        current_binding=(
            None if binding is None else _binding_read(binding, include_payload=include_payload)
        ),
        spec=serializers.block_type_read(spec),
    )


def _read_one(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    block: IcaapDataBlock,
    *,
    include_payload: bool = True,
) -> IcaapDataBlockRead:
    spec = _spec(block.block_type)
    binding = _current_binding(db, access, block)
    probe = _probe(db, access, cycle, block)
    status = _status(spec, block, binding, probe, cycle)
    return _read(block, spec, binding, status, probe, include_payload=include_payload)


def list_blocks(
    db: Session, access: IcaapAccess, cycle_id: UUID, *, include_payload: bool = False
) -> IcaapDataBlockListRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    rows = db.scalars(
        select(IcaapDataBlock)
        .where(
            IcaapDataBlock.organization_id == access.ctx.organization_id,
            IcaapDataBlock.cycle_id == cycle.id,
        )
        .order_by(IcaapDataBlock.created_at.asc())
    ).all()
    return IcaapDataBlockListRead(
        blocks=[
            _read_one(db, access, cycle, block, include_payload=include_payload) for block in rows
        ]
    )


def get_block(
    db: Session, access: IcaapAccess, cycle_id: UUID, block_id: UUID
) -> IcaapDataBlockRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    return _read_one(db, access, cycle, _block_or_404(db, access, cycle, block_id))


def _block_key(spec: BlockSpec, title: str | None) -> str:
    if not spec.dynamic_facts or spec.type == "financials":
        return spec.type
    slug = _SLUG.sub("_", (title or "table").casefold()).strip("_")[:_MAX_SLUG] or "table"
    return f"{spec.type}:{slug}"


def create_block(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapDataBlockCreate
) -> IcaapDataBlockRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    spec = _spec(payload.block_type)
    if spec.type not in AVAILABLE_BLOCK_TYPES:
        raise guards.unprocessable(
            "block_type_unavailable",
            f"{spec.title} is not available yet.",
            block_type=spec.type,
            phase=spec.phase,
        )
    params = dict(payload.params or {})
    if spec.type == "financials":
        params.setdefault("template", manual_resolver.financials_template(cycle.fiscal_year))
    block = IcaapDataBlock(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        block_type=spec.type,
        block_key=_block_key(spec, payload.title),
        title=payload.title or spec.title,
        params=params,
        created_by=guards.actor_id(access),
    )
    db.add(block)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise guards.conflict("block_exists", "This ICAAP already has that figure block.") from exc
    record_event(
        db,
        access.ctx,
        event_type="icaap.block.created",
        entity_type="icaap_data_block",
        entity_id=block.id,
        details={"cycle_id": str(cycle.id), "block_type": spec.type, "block_key": block.block_key},
    )
    # A first resolution is a convenience, not a precondition: an unavailable
    # source is reported on the card, never as a failed creation.
    if not spec.manual:
        _try_bind(db, access, cycle, block)
    db.commit()
    db.refresh(block)
    return _read_one(db, access, cycle, block)


def _try_bind(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    block: IcaapDataBlock,
    *,
    reason: str | None = None,
) -> tuple[str, str | None, IcaapBlockBinding | None, list[IcaapFactChangeRead]]:
    resolver = resolvers.registry().get(block.block_type)
    if resolver is None:  # pragma: no cover - catalogue and registry are pinned equal
        return "unavailable", "No resolver for this block type.", None, []
    try:
        resolution = resolver.resolve(_context(db, access, cycle, block))
    except Unavailable as exc:
        return "unavailable", exc.reason, None, []
    return _store(db, access, cycle, block, resolution, resolver.version, reason=reason)


def _store(  # noqa: PLR0913 - the binding is written from six explicit parts
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    block: IcaapDataBlock,
    resolution: Resolution,
    resolver_version: str,
    *,
    reason: str | None = None,
    evidence_attachment_id: UUID | None = None,
) -> tuple[str, str | None, IcaapBlockBinding | None, list[IcaapFactChangeRead]]:
    digest = resolvers.payload_digest(resolution.payload, resolution.facts)
    current = _current_binding(db, access, block)
    if (
        current is not None
        and current.source_key == resolution.source_key
        and current.payload_sha256 == digest
    ):
        return "unchanged", None, current, []
    binding = IcaapBlockBinding(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        block_id=block.id,
        seq=(current.seq + 1) if current is not None else 1,
        resolver=block.block_type,
        resolver_version=resolver_version,
        source_kind=resolution.source_kind,
        source_ref=resolution.source_ref,
        source_key=resolution.source_key,
        source_as_of=resolution.source_as_of,
        source_run_ids=list(resolution.source_run_ids),
        payload=resolution.payload,
        facts=resolution.facts,
        payload_sha256=digest,
        evidence_attachment_id=evidence_attachment_id,
        reason=reason,
        bound_by=guards.actor_id(access),
    )
    db.add(binding)
    db.flush()
    # A new binding supersedes any pin: the pin recorded a choice about the
    # binding that has just been replaced.
    block.pin_reason = None
    block.pinned_binding_seq = None
    block.pinned_by = None
    block.pinned_at = None
    changes = _changed_facts(current, binding)
    record_event(
        db,
        access.ctx,
        event_type="icaap.block.bound",
        entity_type="icaap_block_binding",
        entity_id=binding.id,
        details={
            "cycle_id": str(cycle.id),
            "block_id": str(block.id),
            "block_type": block.block_type,
            "seq": binding.seq,
            "source_kind": binding.source_kind,
            "source_key": binding.source_key,
            "payload_sha256": digest,
            "changed_facts": [change.key for change in changes],
            "reason": reason,
        },
    )
    return "bound", None, binding, changes


def _changed_facts(
    previous: IcaapBlockBinding | None, current: IcaapBlockBinding
) -> list[IcaapFactChangeRead]:
    before = {} if previous is None else (previous.facts or {})
    after = current.facts or {}
    changes: list[IcaapFactChangeRead] = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key, {}).get("value") if isinstance(before.get(key), dict) else None
        new = after.get(key, {}).get("value") if isinstance(after.get(key), dict) else None
        if old != new:
            changes.append(IcaapFactChangeRead(key=key, before=old, after=new))
    return changes


def refresh_block(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    block_id: UUID,
    payload: IcaapDataBlockRefresh,
) -> IcaapBlockRefreshRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    block = _block_or_404(db, access, cycle, block_id)
    if block.retired_at is not None:
        raise guards.conflict("block_retired", "This figure block has been retired.")
    spec = _spec(block.block_type)
    if spec.manual:
        raise guards.conflict(
            "block_is_manual",
            "This table is filled in by hand. Save the figures to update it.",
        )
    outcome, reason, _binding, changes = _try_bind(db, access, cycle, block, reason=payload.reason)
    db.commit()
    db.refresh(block)
    return IcaapBlockRefreshRead(
        outcome=outcome,  # pyright: ignore[reportArgumentType]
        reason=reason,
        block=_read_one(db, access, cycle, block),
        changed_facts=changes,
    )


def list_bindings(
    db: Session, access: IcaapAccess, cycle_id: UUID, block_id: UUID
) -> IcaapBlockBindingListRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    block = _block_or_404(db, access, cycle, block_id)
    rows = db.scalars(
        select(IcaapBlockBinding)
        .where(
            IcaapBlockBinding.organization_id == access.ctx.organization_id,
            IcaapBlockBinding.block_id == block.id,
        )
        .order_by(IcaapBlockBinding.seq.desc())
    ).all()
    return IcaapBlockBindingListRead(
        bindings=[_binding_read(binding, include_payload=False) for binding in rows]
    )


def pin_block(
    db: Session, access: IcaapAccess, cycle_id: UUID, block_id: UUID, payload: IcaapDataBlockPin
) -> IcaapDataBlockRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    block = _block_or_404(db, access, cycle, block_id)
    binding = _current_binding(db, access, block)
    if binding is None:
        raise guards.conflict(
            "pin_not_allowed", "There is nothing to keep: this block is not linked yet."
        )
    probe = _probe(db, access, cycle, block)
    if probe.withdrawn:
        raise guards.conflict(
            "pin_not_allowed",
            "The data behind these figures was withdrawn, so they cannot be kept. "
            "Refresh the block or remove it from the report.",
        )
    block.pin_reason = payload.reason
    block.pinned_binding_seq = binding.seq
    block.pinned_by = guards.actor_id(access)
    block.pinned_at = utc_now()
    record_event(
        db,
        access.ctx,
        event_type="icaap.block.pinned",
        entity_type="icaap_data_block",
        entity_id=block.id,
        details={
            "cycle_id": str(cycle.id),
            "binding_seq": binding.seq,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(block)
    return _read_one(db, access, cycle, block)


def unpin_block(
    db: Session, access: IcaapAccess, cycle_id: UUID, block_id: UUID
) -> IcaapDataBlockRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    block = _block_or_404(db, access, cycle, block_id)
    block.pin_reason = None
    block.pinned_binding_seq = None
    block.pinned_by = None
    block.pinned_at = None
    record_event(
        db,
        access.ctx,
        event_type="icaap.block.unpinned",
        entity_type="icaap_data_block",
        entity_id=block.id,
        details={"cycle_id": str(cycle.id)},
    )
    db.commit()
    db.refresh(block)
    return _read_one(db, access, cycle, block)


def put_manual_table(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    block_id: UUID,
    payload: IcaapManualTablePut,
) -> IcaapDataBlockRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    block = _block_or_404(db, access, cycle, block_id)
    spec = _spec(block.block_type)
    if not spec.manual:
        raise guards.conflict(
            "block_not_manual", "This block is computed from the engines, not typed in."
        )
    column_keys = {column.key for column in payload.columns}
    for row in payload.rows:
        unknown = set(row.cells) - column_keys
        if unknown:
            raise guards.unprocessable(
                "invalid_manual_cell",
                "This table has a value in a column it does not have.",
                row=row.key,
                columns=sorted(unknown),
            )
    if payload.evidence_attachment_id is not None:
        evidence = db.scalar(
            select(IcaapAttachment).where(
                IcaapAttachment.id == payload.evidence_attachment_id,
                IcaapAttachment.organization_id == access.ctx.organization_id,
                IcaapAttachment.cycle_id == cycle.id,
            )
        )
        if evidence is None:
            raise guards.unprocessable(
                "unknown_attachment", "That evidence file is not part of this ICAAP."
            )
    columns = [column.model_dump() for column in payload.columns]
    rows = [row.model_dump() for row in payload.rows]
    params = dict(block.params or {})
    params["table"] = {"columns": columns, "rows": rows}
    block.params = params
    current = _current_binding(db, access, block)
    resolution = manual_resolver.build_manual_resolution(
        _context(db, access, cycle, block),
        columns=columns,
        rows=rows,
        seq=(current.seq + 1) if current is not None else 1,
    )
    _store(
        db,
        access,
        cycle,
        block,
        resolution,
        "1",
        reason=payload.reason,
        evidence_attachment_id=payload.evidence_attachment_id,
    )
    record_event(
        db,
        access.ctx,
        event_type="icaap.block.manual_table_saved",
        entity_type="icaap_data_block",
        entity_id=block.id,
        details={
            "cycle_id": str(cycle.id),
            "rows": len(rows),
            "columns": len(columns),
            "evidence_attachment_id": (
                None
                if payload.evidence_attachment_id is None
                else str(payload.evidence_attachment_id)
            ),
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(block)
    return _read_one(db, access, cycle, block)


def retire_block(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    block_id: UUID,
    payload: IcaapDataBlockRetire,
) -> IcaapDataBlockRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    block = _block_or_404(db, access, cycle, block_id)
    from app.services.icaap import sections as sections_service  # noqa: PLC0415 - mutual read

    if sections_service.working_references(db, access, cycle, str(block.id)):
        raise guards.conflict(
            "block_referenced",
            "This figure is still used in a section. Remove it from the text first.",
        )
    block.retired_at = utc_now()
    block.retired_by = guards.actor_id(access)
    block.retire_reason = payload.reason
    record_event(
        db,
        access.ctx,
        event_type="icaap.block.retired",
        entity_type="icaap_data_block",
        entity_id=block.id,
        details={"cycle_id": str(cycle.id), "reason": payload.reason},
    )
    db.commit()
    db.refresh(block)
    return _read_one(db, access, cycle, block)


def block_has_fact(db: Session, access: IcaapAccess, block: IcaapDataBlock, fact_key: str) -> bool:
    """Does this block publish that figure? Manual tables declare theirs."""
    spec = _spec(block.block_type)
    if not spec.dynamic_facts:
        return fact_key in spec.fact_keys
    binding = _current_binding(db, access, block)
    return binding is not None and fact_key in (binding.facts or {})


def block_states(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> tuple[BlockState, ...]:
    """Readiness's view of every live block in the cycle."""
    active_evidence = _active_evidence_ids(db, access, cycle)
    states: list[BlockState] = []
    for block in db.scalars(
        select(IcaapDataBlock)
        .where(
            IcaapDataBlock.organization_id == access.ctx.organization_id,
            IcaapDataBlock.cycle_id == cycle.id,
        )
        .order_by(IcaapDataBlock.created_at.asc())
    ):
        spec = _spec(block.block_type)
        binding = _current_binding(db, access, block)
        probe = _probe(db, access, cycle, block)
        states.append(
            BlockState(
                block_id=str(block.id),
                block_type=block.block_type,
                status=_status(spec, block, binding, probe, cycle),
                pin_reason=block.pin_reason,
                manual=spec.manual,
                evidence_active=(
                    binding is not None
                    and binding.evidence_attachment_id is not None
                    and binding.evidence_attachment_id in active_evidence
                ),
                retired=block.retired_at is not None,
            )
        )
    return tuple(states)


def _active_evidence_ids(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> set[UUID]:
    from app.services.icaap import attachments as attachments_service  # noqa: PLC0415

    return attachments_service.active_attachment_ids(db, access, cycle)


__all__ = [
    "block_has_fact",
    "block_states",
    "create_block",
    "current_bindings",
    "current_bindings_by_type",
    "fact_value",
    "get_block",
    "list_bindings",
    "list_blocks",
    "pin_block",
    "put_manual_table",
    "refresh_block",
    "retire_block",
    "unpin_block",
]
