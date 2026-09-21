"""Freeze exactly what will be sent to the model, and nothing else.

The fact sheet is the request's whole factual content, and it is frozen at
enqueue: the worker re-hashes it before sending and refuses on a mismatch, so
what the audit row says was sent is what was sent.

What goes in:

* the block facts of the section's **FRESH or PINNED** blocks only, as
  descriptors, plus values for the kinds that may carry one;
* a ``blocks_not_available`` list naming the blocks that could not contribute
  and why, so the model raises an open question instead of filling the gap.

What never goes in, in either mode: a UUID, a person, a bank or organisation
name, a country, a currency, a regulator, a date, a monetary amount, a payload
table, a narrative, an attester or an attachment. The ``no_leaks`` test asserts
that against the tenant's own registers rather than against a list kept here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.core.config import get_settings
from app.domain.icaap import ai_facts
from app.domain.icaap.blocks import BLOCK_CATALOGUE, BlockStatus
from app.domain.icaap.frameworks.schema import SectionDef
from app.domain.icaap.prosemirror import collect_refs
from app.models.icaap import IcaapBlockBinding, IcaapCycle, IcaapDataBlock, IcaapSection
from app.services.ai import pseudonymise
from app.services.attestation.digests import canonical_json, sha256_hex
from app.services.icaap import blocks as blocks_service

FACT_SHEET_SCHEMA = "icaap-ai-fact-sheet-v1"

#: Block statuses that may contribute a figure. Everything else is reported as
#: unavailable with its reason: a figure whose source moved is not a figure.
_CONTRIBUTING = frozenset({BlockStatus.FRESH, BlockStatus.PINNED})
_UNAVAILABLE_REASONS: Mapping[BlockStatus, str] = {
    BlockStatus.UNBOUND: "not_linked",
    BlockStatus.STALE: "stale",
    BlockStatus.AS_OF_MISMATCH: "as_of_mismatch",
    BlockStatus.SOURCE_MISSING: "source_missing",
    BlockStatus.SOURCE_WITHDRAWN: "source_withdrawn",
}


@dataclass(frozen=True)
class FactBindingRecord:
    """Where a fid pointed when the sheet was frozen. NEVER sent."""

    block_id: str
    fact_key: str
    binding_seq: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "fact_key": self.fact_key,
            "binding_seq": self.binding_seq,
        }


@dataclass
class FactSheetBuild:
    sheet: dict[str, Any]
    sha256: str
    mode: str
    bindings: dict[str, FactBindingRecord]
    entity_map: pseudonymise.EntityMap
    availability: dict[str, bool] = field(default_factory=dict)
    dropped_facts: int = 0

    @property
    def fact_count(self) -> int:
        return len(self.sheet.get("facts", []))


class NoUsableFactsError(RuntimeError):
    """The section has nothing a grounded draft could rest on."""


def _block_alias(block: IcaapDataBlock, manual_index: Mapping[UUID, int]) -> str:
    """The alias a fid is built from — a block TYPE, never an id.

    Manual tables get ``manual_1``, ``manual_2`` by creation order: their titles
    are user-entered text and would otherwise become the one identifier the
    sheet carries.
    """
    if block.id in manual_index:
        return f"manual_{manual_index[block.id]}"
    return block.block_type


def _candidate_block_ids(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    section: IcaapSection,
    section_def: SectionDef,
) -> set[str]:
    """Blocks this section already quotes, plus the ones it is expected to."""
    wanted: set[str] = set()
    doc = section.working_doc or {"type": "doc", "content": []}
    wanted.update(collect_refs(doc).block_ids)
    expected_types = set(section_def.data_blocks)
    for block in db.scalars(
        select(IcaapDataBlock).where(
            IcaapDataBlock.organization_id == access.ctx.organization_id,
            IcaapDataBlock.cycle_id == cycle.id,
            IcaapDataBlock.retired_at.is_(None),
        )
    ):
        if block.block_type in expected_types:
            wanted.add(str(block.id))
    return wanted


def _fact_entry(  # noqa: PLR0913 - one fact is its seven explicit parts
    fid: str,
    label: str,
    kind: str,
    block_title: str,
    raw_value: str | None,
    descriptors: ai_facts.Descriptors,
    *,
    descriptor_only: bool,
) -> dict[str, Any]:
    available = raw_value is not None
    entry: dict[str, Any] = {
        "id": fid,
        "label": label,
        "kind": kind,
        "block": block_title,
        "available": available,
    }
    if not available:
        entry["descriptors"] = descriptors.as_dict()
        return entry
    if kind in ai_facts.WITHHELD_KINDS:
        # Amounts and dates identify an institution; the model places them and
        # the platform fills them in.
        entry["value_withheld"] = True
    elif kind == "text":
        if ai_facts.text_value_is_sendable(fid.split(".", 1)[-1], raw_value):
            entry["value"] = str(raw_value)
        else:
            entry["value_withheld"] = True
    elif kind == "boolean":
        pass  # carried entirely by the ``flag`` descriptor
    elif ai_facts.value_is_sendable(kind, descriptor_only=descriptor_only):
        entry["value"] = str(raw_value)
        if kind == "ratio_pct":
            entry["unit"] = "percent"
    else:
        entry["value_withheld"] = True
    entry["descriptors"] = descriptors.as_dict()
    return entry


def _descriptors_for(  # noqa: PLR0913 - a descriptor set is its six inputs
    fid: str,
    kind: str,
    raw_value: str | None,
    limits: Mapping[str, ai_facts.LimitInput],
    prior_values: Mapping[str, Decimal],
    quanta: Mapping[str, Decimal],
) -> ai_facts.Descriptors:
    if kind == "boolean":
        return ai_facts.Descriptors(flag=ai_facts.boolean_flag(raw_value))
    value = ai_facts.to_decimal(raw_value)
    limit_code = ai_facts.LIMIT_PARAMS.get(fid)
    limit = limits.get(limit_code) if limit_code else None
    base = ai_facts.compare_to_limit(value, limit)
    return ai_facts.Descriptors(
        vs_limit=base.vs_limit,
        limit_kind=base.limit_kind,
        limit_status=base.limit_status,
        vs_prior_year=ai_facts.compare_to_prior_year(
            value, prior_values.get(fid), quantum=quanta.get(kind)
        ),
        # P2's appetite comparators are not wired to fids yet, so the honest
        # answer is "not assessed" rather than a comparison nobody made.
        vs_appetite="not_assessed",
    )


def build_fact_sheet(  # noqa: PLR0913, PLR0912, PLR0915 - the sheet is assembled in one pass
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    section: IcaapSection,
    section_def: SectionDef,
    *,
    descriptor_only: bool,
    limits: Mapping[str, ai_facts.LimitInput] | None = None,
    prior_values: Mapping[str, Decimal] | None = None,
) -> FactSheetBuild:
    """Freeze the pseudonymised fact sheet for one section."""
    settings = get_settings()
    limits = limits or {}
    prior_values = prior_values or {}
    quanta = _display_quanta()

    states = {state.block_id: state for state in blocks_service.block_states(db, access, cycle)}
    bindings = blocks_service.current_bindings(db, access, cycle)
    candidates = _candidate_block_ids(db, access, cycle, section, section_def)

    block_rows = {
        str(block.id): block
        for block in db.scalars(
            select(IcaapDataBlock).where(
                IcaapDataBlock.organization_id == access.ctx.organization_id,
                IcaapDataBlock.cycle_id == cycle.id,
            )
        )
    }
    manual_index = _manual_index(block_rows)
    deny_terms = pseudonymise.tenant_deny_terms(db, access.ctx.organization_id, access.bank)

    facts: list[dict[str, Any]] = []
    fact_bindings: dict[str, FactBindingRecord] = {}
    availability: dict[str, bool] = {}
    unavailable: list[dict[str, str]] = []
    dropped = 0

    for block_id in sorted(candidates):
        block = block_rows.get(block_id)
        state = states.get(block_id)
        if block is None or state is None or state.retired:
            continue
        spec = BLOCK_CATALOGUE.get(block.block_type)
        title = spec.title if spec is not None else block.block_type
        if state.status not in _CONTRIBUTING:
            unavailable.append(
                {"block": title, "reason": _UNAVAILABLE_REASONS.get(state.status, "not_linked")}
            )
            continue
        binding = bindings.get(block.id)
        if binding is None:
            unavailable.append({"block": title, "reason": "not_linked"})
            continue
        alias = _block_alias(block, manual_index)
        for fact_key, payload in sorted((binding.facts or {}).items()):
            if not isinstance(payload, dict):
                continue
            if len(facts) >= settings.ai.max_facts_per_sheet:
                dropped += 1
                continue
            label = _label_for(spec, fact_key, payload, deny_terms, manual=block.id in manual_index)
            if label is None:
                dropped += 1
                continue
            kind = str(payload.get("kind", "text"))
            raw_value = payload.get("value")
            raw_value = None if raw_value is None else str(raw_value)
            fid = f"{alias}.{fact_key}"
            descriptors = _descriptors_for(fid, kind, raw_value, limits, prior_values, quanta)
            facts.append(
                _fact_entry(
                    fid,
                    label,
                    kind,
                    title,
                    raw_value,
                    descriptors,
                    descriptor_only=descriptor_only,
                )
            )
            availability[fid] = raw_value is not None
            fact_bindings[fid] = FactBindingRecord(
                block_id=str(block.id), fact_key=fact_key, binding_seq=binding.seq
            )

    if not any(availability.values()):
        message = "This section has no linked figure a grounded draft could rest on."
        raise NoUsableFactsError(message)

    entity_map = pseudonymise.build_entity_map(
        db,
        access.bank,
        as_of_label=_as_of_label(cycle.as_of_date),
        fiscal_year_label=str(cycle.as_of_date.year),
        framework_label=f"{cycle.framework_code} {cycle.framework_version}",
        basis_label=str(cycle.basis),
    )

    sheet: dict[str, Any] = {
        "schema": FACT_SHEET_SCHEMA,
        "mode": "descriptor_only" if descriptor_only else "standard",
        "cycle": {
            "kind": str(cycle.cycle_kind),
            "exposure_draft": bool(getattr(cycle, "exposure_draft", False)),
            "rehearsal": cycle.cycle_kind == "rehearsal",
        },
        "section": {"key": section_def.key},
        "entities": [dict(entry) for entry in entity_map.offered()],
        "facts": facts,
        "blocks_not_available": sorted(
            unavailable, key=lambda entry: (entry["block"], entry["reason"])
        ),
    }
    return FactSheetBuild(
        sheet=sheet,
        sha256=sha256_hex(canonical_json(sheet)),
        mode=sheet["mode"],
        bindings=fact_bindings,
        entity_map=entity_map,
        availability=availability,
        dropped_facts=dropped,
    )


def digest(sheet: Mapping[str, Any]) -> str:
    """The canonical digest the worker re-checks before sending."""
    return sha256_hex(canonical_json(dict(sheet)))


def _manual_index(blocks: Mapping[str, IcaapDataBlock]) -> dict[UUID, int]:
    manual = sorted(
        (
            block
            for block in blocks.values()
            if (spec := BLOCK_CATALOGUE.get(block.block_type)) is not None and spec.manual
        ),
        key=lambda block: (block.created_at, str(block.id)),
    )
    return {block.id: index for index, block in enumerate(manual, start=1)}


def _label_for(
    spec: Any,
    fact_key: str,
    payload: Mapping[str, Any],
    deny_terms: frozenset[str],
    *,
    manual: bool,
) -> str | None:
    """A static catalogue label, or a scrubbed manual one, or None to drop."""
    if not manual and spec is not None:
        for fact_spec in spec.facts:
            if fact_spec.key == fact_key:
                return fact_spec.label
    # Manual tables (and dynamic facts) carry a user-entered label: the one
    # free-text channel into the sheet, so it is scrubbed or the fact is dropped.
    return pseudonymise.scrub_label(str(payload.get("label", fact_key)), deny_terms)


def _display_quanta() -> dict[str, Decimal]:
    """Display precision per fact kind, mirroring ``render/format.py``.

    Prior-year equality is compared at the precision the report PRINTS, so
    "unchanged" means "the report shows the same number" rather than a
    tolerance somebody had to choose.
    """
    return {
        "ratio_pct": Decimal("0.01"),
        "multiplier": Decimal("0.01"),
        "years": Decimal("0.1"),
        "count": Decimal("1"),
        "amount": Decimal("0.01"),
    }


def _as_of_label(as_of: date) -> str:
    return as_of.isoformat()


def collect_prior_year_values(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
) -> dict[str, Decimal]:
    """Last year's SEALED figures for the same bank, basis and cycle kind.

    Only a sealed cycle counts: a draft's working figures are not a prior-year
    comparison, they are somebody's work in progress.
    """
    from app.models.icaap import ICAAP_SEALED_STATUSES  # noqa: PLC0415 - avoid a model cycle

    prior = db.scalar(
        select(IcaapCycle)
        .where(
            IcaapCycle.organization_id == access.ctx.organization_id,
            IcaapCycle.bank_id == access.bank.id,
            IcaapCycle.basis == cycle.basis,
            IcaapCycle.cycle_kind == cycle.cycle_kind,
            IcaapCycle.as_of_date < cycle.as_of_date,
            IcaapCycle.status.in_(sorted(ICAAP_SEALED_STATUSES)),
        )
        .order_by(IcaapCycle.as_of_date.desc())
        .limit(1)
    )
    if prior is None:
        return {}
    values: dict[str, Decimal] = {}
    prior_blocks = {
        block.id: block
        for block in db.scalars(
            select(IcaapDataBlock).where(
                IcaapDataBlock.organization_id == access.ctx.organization_id,
                IcaapDataBlock.cycle_id == prior.id,
            )
        )
    }
    manual_index = _manual_index({str(key): value for key, value in prior_blocks.items()})
    for binding in db.scalars(
        select(IcaapBlockBinding)
        .where(
            IcaapBlockBinding.organization_id == access.ctx.organization_id,
            IcaapBlockBinding.cycle_id == prior.id,
        )
        .order_by(IcaapBlockBinding.seq.asc())
    ):
        block = prior_blocks.get(binding.block_id)
        if block is None:
            continue
        alias = _block_alias(block, manual_index)
        for fact_key, payload in (binding.facts or {}).items():
            if not isinstance(payload, dict):
                continue
            value = ai_facts.to_decimal(
                None if payload.get("value") is None else str(payload["value"])
            )
            if value is not None:
                values[f"{alias}.{fact_key}"] = value
    return values


def resolve_limits(
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    fids: Sequence[str],
) -> dict[str, ai_facts.LimitInput]:
    """The governed limits behind ``vs_limit``, resolved the engines' way.

    Capital codes come through ``capital_plan.effective_capital_floors`` so the
    narrative is measured against exactly the floor the capital plan shows
    (clamped tighten-only, with its confirmation status). A code that does not
    resolve is simply absent, which makes its descriptor ``not_assessed`` — a
    fallback minimum in a regulatory narrative would be a fabricated statement.
    """
    from app.domain.policy.resolver import PARAMETER_DIRECTION  # noqa: PLC0415
    from app.services import capital_plan  # noqa: PLC0415 - additive public seam

    codes = tuple(
        dict.fromkeys(code for fid in fids if (code := ai_facts.LIMIT_PARAMS.get(fid)) is not None)
    )
    if not codes:
        return {}
    try:
        floors = capital_plan.effective_capital_floors(
            db, access.ctx, access.bank, cycle.as_of_date, codes
        )
    except Exception:  # noqa: BLE001 - an unresolvable regime means not_assessed
        return {}
    resolved: dict[str, ai_facts.LimitInput] = {}
    for code, floor in floors.items():
        kind = ai_facts.limit_kind_for(PARAMETER_DIRECTION.get(code))
        if kind is None:
            continue
        resolved[code] = ai_facts.LimitInput(
            value=Decimal(str(floor.value_pct)),
            kind=kind,
            status=(
                "confirmed"
                if str(floor.confirmation_status or "").casefold() == "confirmed"
                else "pending_confirmation"
            ),
        )
    return resolved


__all__ = [
    "FACT_SHEET_SCHEMA",
    "FactBindingRecord",
    "FactSheetBuild",
    "NoUsableFactsError",
    "build_fact_sheet",
    "collect_prior_year_values",
    "digest",
    "resolve_limits",
]
