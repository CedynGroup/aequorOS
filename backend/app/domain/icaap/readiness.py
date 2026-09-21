"""Is this ICAAP cycle ready to be frozen? (pure domain)

Readiness is the one place that answers "what is still missing", and P3's
freeze calls exactly this function before it seals anything, so the list a
preparer sees and the list that blocks a filing cannot drift apart.

Every finding is a CODE plus parameters. The service renders the sentence,
because the sentence names the regulator and that name is data, not a literal
(``regulator_short`` from the jurisdictions registry). Nothing here knows which
country it is describing.

Severity has a deliberate shape:

* **blocking** stops a freeze — a section with no committed text, an open
  checklist item, a figure whose source moved on, a missing freeze-gate
  attachment;
* **warning** is a judgement the preparer has already made and recorded — a
  pinned block, uncommitted edits;
* **info** is context — the framework is still an exposure draft, a submission
  attachment is not needed until later, this cycle is a rehearsal.

The deadline is never blocking. A late filing is still a filing, and refusing
to freeze an overdue report would be the platform making the breach worse.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.domain.icaap.blocks import BLOCK_CATALOGUE, BlockStatus, blocks_freeze
from app.domain.icaap.frameworks.schema import Framework, RequirementItem

Severity = Literal["blocking", "warning", "info"]
Scope = Literal["cycle", "section", "requirement", "block", "attachment"]
ItemStatus = Literal["open", "met", "not_applicable"]
Rag = Literal["green", "amber", "red", "none"]


@dataclass(frozen=True)
class ItemState:
    status: ItemStatus
    reason: str | None = None


@dataclass(frozen=True)
class SectionState:
    key: str
    committed_version_no: int | None
    committed_has_content: bool
    uncommitted_changes: bool
    item_states: Mapping[str, ItemState]
    referenced_block_ids: frozenset[str]


@dataclass(frozen=True)
class BlockState:
    block_id: str
    block_type: str
    status: BlockStatus
    pin_reason: str | None
    manual: bool
    evidence_active: bool
    retired: bool


@dataclass(frozen=True)
class IrrbbSfState:
    """What the IRRBB standardised framework says about this cycle (P5).

    ``declared`` is False for a framework that names no ``method_mandates`` at
    all — Nigeria's and Kenya's do not — and then nothing here is asserted about
    the cycle, so a Ghanaian commencement date can never block a Nigerian
    filing.

    ``assumption_defaults_applied`` and ``representative_parameters`` are the
    counted and the named form of the same discipline: a modelling default
    applied to forty positions is a different exposure from one applied to a
    single position, and a representative calibration has to keep saying so
    wherever the figure it shaped turns up.
    """

    mandatory: bool = False
    mandatory_from: date | None = None
    mandate_statement: str = ""
    declared: bool = False
    run_present: bool = False
    run_refusal_code: str | None = None
    run_refusal_message: str | None = None
    block_usable: bool = False
    #: Methods a mandate has superseded that a live Pillar 2 item still uses.
    superseded_methods_in_use: tuple[str, ...] = ()
    assumption_defaults_applied: int = 0
    representative_parameters: tuple[str, ...] = ()
    outlier: bool = False


@dataclass(frozen=True)
class CycleState:
    cycle_kind: str
    status: str
    as_of: date
    due_date: date | None
    basis: str
    #: ``has_subsidiaries``, ``group_member``, ``foreign_bank_subsidiary``,
    #: ``cycle_kind:<kind>`` — what the framework's ``applies_when`` tests.
    conditions: frozenset[str]
    framework_digest_matches: bool
    companion_basis_cycle_exists: bool
    sections: tuple[SectionState, ...]
    blocks: tuple[BlockState, ...]
    active_attachment_counts: Mapping[str, int]
    #: Defaulted so a caller that predates P5 constructs the same state it did
    #: before, and a framework with no method mandates asserts nothing.
    irrbb_sf: IrrbbSfState = IrrbbSfState()


@dataclass(frozen=True)
class ReadinessItem:
    code: str
    severity: Severity
    scope: Scope
    ref: str | None
    params: Mapping[str, str]


@dataclass(frozen=True)
class DeadlineStatus:
    due_date: date | None
    days_remaining: int | None
    rag: Rag


@dataclass(frozen=True)
class ReadinessReport:
    ready_for_freeze: bool
    items: tuple[ReadinessItem, ...]
    deadline: DeadlineStatus
    pending_primary_text_sections: tuple[str, ...]
    #: Per section: met / not_applicable / auto_not_applicable / open.
    section_counts: Mapping[str, Mapping[str, int]]


def _applies(item: RequirementItem, conditions: frozenset[str]) -> bool:
    return item.applies_when is None or item.applies_when in conditions


def deadline_status(
    due_date: date | None, *, today: date, amber_days: int | None
) -> DeadlineStatus:
    """Green, amber or red — the amber window is a governed parameter (D-034).

    ``amber_days`` is ``None`` when no window is configured for the
    institution. The platform then says how many days are left and whether the
    date has passed, but does not colour the middle ground: inventing a window
    would be a platform policy presented as if it were settled.
    """
    if due_date is None:
        return DeadlineStatus(due_date=None, days_remaining=None, rag="none")
    days = (due_date - today).days
    if days < 0:
        rag: Rag = "red"
    elif amber_days is None:
        rag = "none"
    elif days <= amber_days:
        rag = "amber"
    else:
        rag = "green"
    return DeadlineStatus(due_date=due_date, days_remaining=days, rag=rag)


def _section_items(  # noqa: PLR0913 - the finding context is explicit
    framework: Framework,
    state: CycleState,
    section: SectionState,
    items: list[ReadinessItem],
    counts: dict[str, dict[str, int]],
    pending: list[str],
) -> None:
    definition = framework.section(section.key)
    if definition.source_status == "pending_primary_text":
        pending.append(section.key)
        items.append(
            ReadinessItem(
                code="section_pending_primary_text",
                # A rehearsal exists precisely to be run before the final text
                # arrives, so the same gap is a warning there and a refusal for
                # anything a regulator would receive (D-006).
                severity="warning" if state.cycle_kind == "rehearsal" else "blocking",
                scope="section",
                ref=section.key,
                params={"section_title": definition.title, "letter": definition.letter},
            )
        )
    if section.committed_version_no is None:
        items.append(
            ReadinessItem(
                code="section_not_committed",
                severity="blocking",
                scope="section",
                ref=section.key,
                params={"section_title": definition.title},
            )
        )
    elif not section.committed_has_content:
        items.append(
            ReadinessItem(
                code="section_empty",
                severity="blocking",
                scope="section",
                ref=section.key,
                params={"section_title": definition.title},
            )
        )
    if section.uncommitted_changes:
        items.append(
            ReadinessItem(
                code="section_uncommitted_changes",
                severity="warning",
                scope="section",
                ref=section.key,
                params={"section_title": definition.title},
            )
        )

    tally = {"met": 0, "not_applicable": 0, "auto_not_applicable": 0, "open": 0}
    for item in definition.requirements:
        if not _applies(item, state.conditions):
            tally["auto_not_applicable"] += 1
            continue
        recorded = section.item_states.get(item.id, ItemState(status="open"))
        if recorded.status == "met":
            tally["met"] += 1
        elif recorded.status == "not_applicable":
            tally["not_applicable"] += 1
        else:
            tally["open"] += 1
            items.append(
                ReadinessItem(
                    code="requirement_open",
                    severity="blocking",
                    scope="requirement",
                    ref=item.id,
                    params={"section_key": section.key, "requirement": item.text},
                )
            )
    counts[section.key] = tally

    present_types = {block.block_type for block in state.blocks if not block.retired}
    for block_type in definition.data_blocks:
        if block_type not in present_types:
            items.append(
                ReadinessItem(
                    # BLOCKING, changed 2026-09-20 (independent audit F2). It was
                    # a warning, and the validation rules that would catch a bad
                    # figure are conditional on the block being PRESENT
                    # (``_pillar2_reconciliation`` returns early, ``_as_of``
                    # iterates only over blocks that exist) — so two soft gates in
                    # series let a report freeze with no capital figures in it at
                    # all, while a WRONG capital figure was an ERROR. The absence
                    # of a figure cannot be quieter than the figure being wrong.
                    #
                    # ``data_blocks`` is the instrument's own statement of the
                    # evidence a section carries, per jurisdiction, so this stays
                    # jurisdiction-neutral: a framework that expects nothing here
                    # blocks nothing. A preparer who genuinely must not carry a
                    # block RETIRES it, which is a recorded act and also removes
                    # it from the frozen snapshot — not a warning nobody reads.
                    code="block_expected_absent",
                    severity="blocking",
                    scope="section",
                    ref=section.key,
                    params={
                        "block_title": BLOCK_CATALOGUE[block_type].title,
                        "block_type": block_type,
                    },
                )
            )


_BLOCK_CODES: Mapping[BlockStatus, str] = {
    BlockStatus.UNBOUND: "block_referenced_unbound",
    BlockStatus.STALE: "block_stale",
    BlockStatus.AS_OF_MISMATCH: "block_as_of_mismatch",
    BlockStatus.SOURCE_MISSING: "block_source_missing",
    BlockStatus.SOURCE_WITHDRAWN: "block_source_withdrawn",
}


def _block_items(state: CycleState, items: list[ReadinessItem], referenced: frozenset[str]) -> None:
    for block in state.blocks:
        if block.retired:
            continue
        title = BLOCK_CATALOGUE[block.block_type].title
        params = {"block_title": title, "block_type": block.block_type}
        if block.block_id in referenced and blocks_freeze(block.status):
            items.append(
                ReadinessItem(
                    code=_BLOCK_CODES[block.status],
                    severity="blocking",
                    scope="block",
                    ref=block.block_id,
                    params=params,
                )
            )
        if block.status is BlockStatus.PINNED:
            items.append(
                ReadinessItem(
                    code="block_pinned",
                    severity="warning",
                    scope="block",
                    ref=block.block_id,
                    params={**params, "reason": block.pin_reason or ""},
                )
            )
        if block.manual and not block.evidence_active:
            items.append(
                ReadinessItem(
                    code="manual_table_without_evidence",
                    severity="blocking",
                    scope="block",
                    ref=block.block_id,
                    params=params,
                )
            )


def _irrbb_sf_items(state: CycleState, items: list[ReadinessItem]) -> None:
    """The standardised framework's own rules (P5-DESIGN §1.6 item 4).

    A rehearsal exists to be run before everything is in place, so the two
    mandate refusals are warnings there and blocking for anything a regulator
    would receive — the same shape ``section_pending_primary_text`` already
    uses (D-006).
    """
    sf = state.irrbb_sf
    if not sf.declared:
        return
    rehearsal = state.cycle_kind == "rehearsal"
    gate: Severity = "warning" if rehearsal else "blocking"

    if sf.mandatory and sf.superseded_methods_in_use:
        items.append(
            ReadinessItem(
                code="irrbb_interim_not_permitted",
                severity=gate,
                scope="cycle",
                ref=None,
                params={
                    "mandatory_from": (
                        "" if sf.mandatory_from is None else sf.mandatory_from.isoformat()
                    )
                },
            )
        )
    if sf.run_refusal_code is not None:
        items.append(
            ReadinessItem(
                code="irrbb_sf_refused",
                severity=gate if sf.mandatory else "warning",
                scope="cycle",
                ref=None,
                params={
                    "refusal_code": sf.run_refusal_code,
                    "refusal_message": sf.run_refusal_message or "",
                },
            )
        )
    elif sf.mandatory and not sf.block_usable:
        items.append(
            ReadinessItem(
                code="irrbb_sf_required_missing",
                severity=gate,
                scope="cycle",
                ref=None,
                params={
                    "mandatory_from": (
                        "" if sf.mandatory_from is None else sf.mandatory_from.isoformat()
                    ),
                    "has_result": "true" if sf.run_present else "false",
                },
            )
        )
    if sf.block_usable and sf.assumption_defaults_applied:
        items.append(
            ReadinessItem(
                code="irrbb_sf_assumption_defaults",
                severity="warning",
                scope="cycle",
                ref=None,
                params={"applied": str(sf.assumption_defaults_applied)},
            )
        )
    if sf.block_usable and sf.representative_parameters:
        items.append(
            ReadinessItem(
                code="irrbb_sf_representative_parameters",
                severity="warning",
                scope="cycle",
                ref=None,
                params={"param_codes": ", ".join(sf.representative_parameters)},
            )
        )
    if sf.block_usable and sf.outlier:
        items.append(
            ReadinessItem(
                code="irrbb_outlier",
                severity="info",
                scope="cycle",
                ref=None,
                params={},
            )
        )


def _attachment_items(framework: Framework, state: CycleState, items: list[ReadinessItem]) -> None:
    for requirement in framework.attachments:
        if requirement.gate in {"optional", "per_block"}:
            continue
        condition = requirement.applies_when
        if condition is not None and condition not in state.conditions:
            continue
        active = state.active_attachment_counts.get(requirement.kind, 0)
        if active >= requirement.min_count:
            continue
        items.append(
            ReadinessItem(
                code="attachment_missing",
                # A submission-gate document is not late until the filing is
                # sent, so it must not stop the Board seeing a frozen report.
                severity="blocking" if requirement.gate == "freeze" else "info",
                scope="attachment",
                ref=requirement.kind,
                params={
                    "attachment_title": requirement.title,
                    "gate": requirement.gate,
                    "required": str(requirement.min_count),
                    "present": str(active),
                },
            )
        )


def evaluate(
    framework: Framework,
    state: CycleState,
    *,
    today: date,
    amber_days: int | None,
) -> ReadinessReport:
    """Every outstanding thing between this cycle and a freeze."""
    items: list[ReadinessItem] = []
    counts: dict[str, dict[str, int]] = {}
    pending: list[str] = []

    if not state.framework_digest_matches:
        items.append(
            ReadinessItem(
                code="framework_digest_mismatch",
                severity="blocking",
                scope="cycle",
                ref=None,
                params={"framework": f"{framework.code} {framework.version}"},
            )
        )
    if framework.status == "exposure_draft":
        items.append(
            ReadinessItem(
                code="framework_exposure_draft",
                severity="info",
                scope="cycle",
                ref=None,
                params={"framework_title": framework.short_title},
            )
        )
    if state.cycle_kind == "rehearsal":
        items.append(
            ReadinessItem(
                code="rehearsal_not_fileable",
                severity="info",
                scope="cycle",
                ref=None,
                params={},
            )
        )
    elif framework.filing_return_code is None:
        # A7: the framework is published as reference data but the platform has
        # no return family that can carry it to this regulator yet.
        items.append(
            ReadinessItem(
                code="filing_not_available_for_framework",
                severity="blocking",
                scope="cycle",
                ref=None,
                params={"framework_title": framework.short_title},
            )
        )

    for section in state.sections:
        _section_items(framework, state, section, items, counts, pending)

    # Prose citation is not the only thing that puts a block in the filing. A
    # block whose TYPE the framework declares as a section's evidence is IN the
    # report whether or not a sentence quotes it — its facts ride into the
    # snapshot, its run enters ``source_runs``, and the headline reads it. So an
    # expected block that cannot bind is blocking on the same footing as one the
    # text cites; otherwise the fix for the severity above is to add an empty
    # capital block and freeze anyway (independent audit F2).
    expected_types = frozenset(
        block_type
        for section in state.sections
        if framework.has_section(section.key)
        for block_type in framework.section(section.key).data_blocks
    )
    referenced = frozenset(
        block_id for section in state.sections for block_id in section.referenced_block_ids
    ) | frozenset(
        block.block_id
        for block in state.blocks
        if not block.retired and block.block_type in expected_types
    )
    _block_items(state, items, referenced)
    _irrbb_sf_items(state, items)
    _attachment_items(framework, state, items)

    if amber_days is None and state.due_date is not None:
        items.append(
            ReadinessItem(
                code="deadline_window_not_configured",
                severity="info",
                scope="cycle",
                ref=None,
                params={},
            )
        )

    if "has_subsidiaries" in state.conditions and not state.companion_basis_cycle_exists:
        items.append(
            ReadinessItem(
                code="basis_companion_missing",
                severity="blocking",
                scope="cycle",
                ref=None,
                params={
                    "basis": state.basis,
                    "other_basis": "consolidated" if state.basis == "solo" else "solo",
                },
            )
        )

    return ReadinessReport(
        ready_for_freeze=not any(item.severity == "blocking" for item in items),
        items=tuple(items),
        deadline=deadline_status(state.due_date, today=today, amber_days=amber_days),
        pending_primary_text_sections=tuple(pending),
        section_counts=counts,
    )


__all__ = [
    "BlockState",
    "CycleState",
    "DeadlineStatus",
    "IrrbbSfState",
    "ItemState",
    "ItemStatus",
    "Rag",
    "ReadinessItem",
    "ReadinessReport",
    "Scope",
    "SectionState",
    "Severity",
    "deadline_status",
    "evaluate",
]
