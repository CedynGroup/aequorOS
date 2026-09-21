"""The frozen ICAAP: everything the filed report says, captured once.

A freeze turns a living workspace into a document. From that moment the report
must be able to answer, without reading a single live table: what did each
section say, which figures did it quote and where did they come from, which
checklist items were met or waived and why, which documents were attached, who
decided what and on which digest, and which governed parameters the numbers
were measured against.

So the snapshot is exhaustive by design. The exporters (WS-D) render only this;
neither of them touches a live row. A supervisor asking "show me exactly what
you filed" gets the same bytes in a year's time, after the register has moved
on, after a block has been refreshed, after a person has been renamed.

Everything is sorted, and nothing volatile goes in. The package's
``content_digest`` is value-based (``digests.content_digest`` strips
``generated_at``), so two freezes of identical content produce the identical
digest — which is what makes "has anything changed since the Board saw it?" a
question with an answer.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.icaap import prosemirror
from app.domain.icaap.blocks import BLOCK_CATALOGUE
from app.domain.icaap.frameworks.schema import Framework
from app.models import Bank, RegulatoryPackage, RegulatoryRun
from app.models.icaap import (
    IcaapAttachment,
    IcaapAttachmentWithdrawal,
    IcaapBlockBinding,
    IcaapCycle,
    IcaapCycleStage,
    IcaapDataBlock,
    IcaapSection,
    IcaapSectionVersion,
    IcaapStageDecision,
)
from app.services import jurisdictions
from app.services.attestation import digests, register_state
from app.services.icaap import exports_draft, guards, workflow
from app.services.icaap import parameters as icaap_parameters

#: The snapshot contract version. Bump it when the SHAPE changes, never for a
#: content change — filed packages keep the version they were built under.
SNAPSHOT_SCHEMA = "icaap-report-snapshot-v1"


def _sorted_sections(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> tuple[IcaapSection, ...]:
    return tuple(
        db.scalars(
            select(IcaapSection)
            .where(
                IcaapSection.organization_id == access.ctx.organization_id,
                IcaapSection.cycle_id == cycle.id,
            )
            .order_by(IcaapSection.position.asc())
        )
    )


def _committed(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> dict[str, IcaapSectionVersion]:
    latest: dict[str, IcaapSectionVersion] = {}
    for version in db.scalars(
        select(IcaapSectionVersion)
        .where(
            IcaapSectionVersion.organization_id == access.ctx.organization_id,
            IcaapSectionVersion.cycle_id == cycle.id,
        )
        .order_by(IcaapSectionVersion.version_no.asc())
    ):
        latest[version.section_key] = version
    return latest


def live_blocks(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> list[tuple[IcaapDataBlock, IcaapBlockBinding | None]]:
    """Every block the report still rests on, with its newest binding."""
    bindings: dict[UUID, IcaapBlockBinding] = {}
    for binding in db.scalars(
        select(IcaapBlockBinding)
        .where(
            IcaapBlockBinding.organization_id == access.ctx.organization_id,
            IcaapBlockBinding.cycle_id == cycle.id,
        )
        .order_by(IcaapBlockBinding.seq.asc())
    ):
        bindings[binding.block_id] = binding
    blocks = db.scalars(
        select(IcaapDataBlock)
        .where(
            IcaapDataBlock.organization_id == access.ctx.organization_id,
            IcaapDataBlock.cycle_id == cycle.id,
            IcaapDataBlock.retired_at.is_(None),
        )
        .order_by(IcaapDataBlock.block_key.asc())
    ).all()
    return [(block, bindings.get(block.id)) for block in blocks]


def active_attachments(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> list[IcaapAttachment]:
    withdrawn = {
        row.attachment_id
        for row in db.scalars(
            select(IcaapAttachmentWithdrawal).where(
                IcaapAttachmentWithdrawal.organization_id == access.ctx.organization_id,
                IcaapAttachmentWithdrawal.cycle_id == cycle.id,
            )
        )
    }
    rows = db.scalars(
        select(IcaapAttachment)
        .where(
            IcaapAttachment.organization_id == access.ctx.organization_id,
            IcaapAttachment.cycle_id == cycle.id,
        )
        .order_by(IcaapAttachment.created_at.asc())
    ).all()
    return [row for row in rows if row.id not in withdrawn]


def _fact_text(binding: IcaapBlockBinding | None, key: str) -> str:
    if binding is None:
        return "Not available"
    fact = (binding.facts or {}).get(key)
    if isinstance(fact, dict):
        value = fact.get("display") or fact.get("value")
        return "Not available" if value is None else str(value)
    return "Not available" if fact is None else str(fact)


_HEADLINE_UNITS: dict[str, str] = {
    "car_pct": "pct",
    "cet1_ratio_pct": "pct",
    "tier1_ratio_pct": "pct",
    "leverage_ratio_pct": "pct",
    "internal_capital_coverage_pct": "pct",
}


def _headline(
    blocks: list[tuple[IcaapDataBlock, IcaapBlockBinding | None]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The figures a supervisor reads first, as both rows and totals.

    A figure with no bound source renders "Not available" rather than a zero:
    a missing capital ratio is not a capital ratio of nothing. They are
    repeated as totals so the movement rule (C-8) has something to compare a
    later version against.
    """
    by_type = {block.block_type: binding for block, binding in blocks}
    capital = by_type.get("capital_position")
    pillar2 = by_type.get("pillar2_summary")
    reconciliation = by_type.get("capital_reconciliation")
    # Every key here is a fact the block catalogue declares, so a renamed fact
    # shows up as "Not available" rather than as a silently dropped headline.
    wanted = (
        ("car_pct", "Capital adequacy ratio", capital),
        ("cet1_ratio_pct", "CET1 ratio", capital),
        ("tier1_ratio_pct", "Tier 1 ratio", capital),
        ("leverage_ratio_pct", "Leverage ratio", capital),
        ("total_capital", "Total regulatory capital", capital),
        ("total_rwa", "Total risk-weighted assets", capital),
        ("pillar2_total_baseline", "Pillar 2 capital requirement", pillar2),
        ("total_internal_requirement", "Total internal capital requirement", reconciliation),
        ("available_internal_capital", "Available internal capital", reconciliation),
        ("internal_capital_coverage_pct", "Internal capital coverage", reconciliation),
    )
    from app.services.regulatory_reporting import generation  # noqa: PLC0415 - avoid a cycle

    rows = [
        generation.snapshot_row(
            code, label, _fact_text(binding, code), unit=_HEADLINE_UNITS.get(code, "")
        )
        for code, label, binding in wanted
    ]
    totals = [
        generation.snapshot_total(
            code, label, _fact_text(binding, code), unit=_HEADLINE_UNITS.get(code, "")
        )
        for code, label, binding in wanted
    ]
    return rows, totals


def _requirement_entries(section_row: IcaapSection, framework: Framework) -> list[dict[str, Any]]:
    definition = framework.section(section_row.section_key)
    state = section_row.checklist_state or {}
    entries: list[dict[str, Any]] = []
    for item in definition.requirements:
        recorded = state.get(item.id) if isinstance(state, dict) else None
        recorded = recorded if isinstance(recorded, dict) else {}
        entries.append(
            {
                "item_id": item.id,
                # The filed checklist appendix prints the requirement itself and
                # the reference a reader can look up. Neither can be recovered
                # from an id after the framework moves on, so both are frozen.
                "text": item.text,
                "citation_label": exports_draft.citation_label(
                    framework, item.citations[0] if item.citations else None
                ),
                "status": str(recorded.get("status", "open")),
                "reason": recorded.get("reason"),
                "citations": sorted(citation.cite_id for citation in item.citations),
            }
        )
    return sorted(entries, key=lambda entry: entry["item_id"])


def _annex_packages(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, framework: Framework
) -> list[RegulatoryPackage]:
    """The current package for each annex the framework declares."""
    if framework.filing is None:
        return []
    found: list[RegulatoryPackage] = []
    for annex in framework.filing.annexes:
        package = db.scalar(
            select(RegulatoryPackage)
            .where(
                RegulatoryPackage.organization_id == access.ctx.organization_id,
                RegulatoryPackage.bank_id == access.bank.id,
                RegulatoryPackage.return_code == annex.return_code,
                RegulatoryPackage.reporting_date == cycle.as_of_date,
                RegulatoryPackage.basis == cycle.basis,
                RegulatoryPackage.status != "superseded",
            )
            .order_by(RegulatoryPackage.version.desc())
            .limit(1)
        )
        if package is not None:
            found.append(package)
    return found


def _parameter_provenance(
    db: Session, bank: Bank, framework: Framework, as_of: date
) -> list[dict[str, Any]]:
    """Every governed number this report was measured against, with its source.

    D-024: the values are console rows, so the report records WHICH row it read
    — the value, its confirmation status and its effective date — and a reader
    can tell a confirmed floor from one still awaiting stakeholder confirmation.
    """
    entries: list[dict[str, Any]] = []
    for code in sorted(framework.param_refs()):
        # Through the ICAAP plane's own non-recording door: this block IS the
        # report's governed-row record, so the read must not also be credited
        # to whichever run the session seals next (D-078 residual).
        resolved = icaap_parameters.try_resolve(db, bank, code, as_of=as_of)
        if resolved is None:
            entries.append({"param_code": code, "resolved": False})
            continue
        entries.append(
            {
                "param_code": code,
                "resolved": True,
                "value": None if resolved.value is None else str(resolved.value),
                "value_json": resolved.value_json,
                "unit": resolved.unit,
                "confirmation_status": resolved.confirmation_status,
                "source_citation": resolved.source_citation,
                "effective_from": resolved.effective_from.isoformat(),
                "scope": f"{resolved.scope_type}:{resolved.scope_key}",
            }
        )
    return entries


def _stage_entries(
    stages: tuple[IcaapCycleStage, ...], decisions: tuple[IcaapStageDecision, ...]
) -> list[dict[str, Any]]:
    by_seq: dict[int, list[IcaapStageDecision]] = {}
    for row in decisions:
        by_seq.setdefault(row.stage_seq, []).append(row)
    return [
        {
            "seq": stage.seq,
            "stage_key": stage.stage_key,
            "title": stage.title,
            "decision_kind": stage.decision_kind,
            "officer_titles": sorted(str(entry) for entry in stage.officer_titles),
            "freeze_on_approve": stage.freeze_on_approve,
            "source": stage.source,
            "decisions": [
                {
                    "decision": row.decision,
                    "round": row.round,
                    "decided_by_name": row.decided_by_name,
                    "officer_title": row.officer_title,
                    "decided_at": row.created_at.isoformat(),
                    "review_digest": row.review_digest,
                    "comment": row.comment,
                    "return_to_seq": row.return_to_seq,
                }
                for row in sorted(by_seq.get(stage.seq, []), key=lambda entry: entry.created_at)
            ],
        }
        for stage in stages
    ]


def _filing_block(framework: Framework) -> dict[str, Any]:
    if framework.filing is None:  # pragma: no cover - freeze refuses first
        return {}
    attestation = framework.filing.attestation
    return {
        "attestation_source_status": attestation.source_status,
        "attestation_lines": dict(attestation.lines),
        "statements": dict(attestation.statements),
        "exposure_draft_line": (
            framework.filing.exposure_draft_line if framework.status == "exposure_draft" else None
        ),
    }


def build(  # noqa: PLR0913 - the snapshot is assembled from named parts
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    *,
    review_digest: str,
    bank: Bank,
    period: Any,
    definition: Any,
) -> Any:
    """Assemble the frozen snapshot. Called inside the freeze transaction."""
    from app.services.regulatory_reporting import generation  # noqa: PLC0415 - avoid a cycle

    framework = guards.require_framework(cycle)
    section_rows = _sorted_sections(db, access, cycle)
    committed = _committed(db, access, cycle)
    blocks = live_blocks(db, access, cycle)
    attachments = active_attachments(db, access, cycle)
    state = workflow.load_state(db, access, cycle)
    annexes = _annex_packages(db, access, cycle, framework)

    from app.services.icaap import cycles as cycles_service  # noqa: PLC0415 - mutual read

    conditions = cycles_service.cycle_conditions(db, access, cycle)

    headline_rows, headline_totals = _headline(blocks)
    headline = generation.snapshot_section(
        "icaap_headline", "Capital adequacy summary", headline_rows
    )
    sections: list[dict[str, Any]] = [headline]
    for row in section_rows:
        if not framework.has_section(
            row.section_key
        ):  # pragma: no cover - rebase keeps them aligned
            continue
        definition_section = framework.section(row.section_key)
        version = committed.get(row.section_key)
        paragraphs = prosemirror.plain_text(version.doc).splitlines() if version is not None else []
        sections.append(
            generation.snapshot_section(
                f"s_{row.section_key}",
                f"{definition_section.letter}. {definition_section.title}",
                [
                    generation.snapshot_row(f"p{index + 1}", "", text)
                    for index, text in enumerate(paragraphs)
                    if text.strip()
                ],
                optional=False,
            )
        )
    for package in annexes:
        for annex_section in package.snapshot.get("sections", []) or []:
            copied = dict(annex_section)
            copied["code"] = f"annex_{package.return_code}__{annex_section.get('code')}"
            sections.append(copied)

    source_run_ids: set[str] = set()
    for _block, binding in blocks:
        if binding is not None:
            source_run_ids.update(str(run_id) for run_id in (binding.source_run_ids or []))
    annex_entries: list[dict[str, Any]] = []
    for package in annexes:
        annex_entries.append(
            {
                "return_code": package.return_code,
                "package_id": str(package.id),
                "version": package.version,
                "content_digest": package.content_digest,
                "source_runs": package.source_runs,
            }
        )
        source_run_ids.update(str(entry["run_id"]) for entry in (package.source_runs or []))

    runs = (
        list(
            db.scalars(
                select(RegulatoryRun).where(
                    RegulatoryRun.organization_id == access.ctx.organization_id,
                    RegulatoryRun.id.in_([UUID(entry) for entry in sorted(source_run_ids)]),
                )
            )
        )
        if source_run_ids
        else []
    )
    source_runs = sorted(
        (generation.source_run_entry(run) for run in runs),
        key=lambda entry: (entry["module"], str(entry["run_id"])),
    )

    profile_rows = register_state.register_state_rows(db, access.ctx, access.bank.id)
    metadata = {
        "icaap": {
            "schema": SNAPSHOT_SCHEMA,
            "cycle": {
                "id": str(cycle.id),
                "kind": cycle.cycle_kind,
                "fiscal_year": cycle.fiscal_year,
                "as_of_date": cycle.as_of_date.isoformat(),
                "basis": cycle.basis,
                "round": cycle.round,
                "title": cycle.title,
                "supersedes_cycle_id": (
                    None if cycle.supersedes_cycle_id is None else str(cycle.supersedes_cycle_id)
                ),
            },
            "framework": {
                "code": framework.code,
                "version": framework.version,
                "status": framework.status,
                "digest": framework.digest,
                "title": framework.title,
                "short_title": framework.short_title,
                "exposure_draft": framework.status == "exposure_draft",
                "effective_from": framework.effective_from.isoformat(),
            },
            "institution_profile": {
                "register_state_digest": digests.register_state_digest(profile_rows),
                "row_count": len(profile_rows),
            },
            # Frozen because the exporters may not resolve it live: the filed
            # document names the regulator from the jurisdiction registry, and
            # a renderer that re-read it could name a different one after a
            # registry edit.
            "regulator_short": jurisdictions.regulator_short(db, bank),
            "sections": [
                {
                    "key": row.section_key,
                    "letter": framework.section(row.section_key).letter,
                    "order": framework.section(row.section_key).order,
                    "title": framework.section(row.section_key).title,
                    "citation": framework.section(row.section_key).citation.cite_id,
                    "citation_label": exports_draft.citation_label(
                        framework, framework.section(row.section_key).citation
                    ),
                    "source_status": framework.section(row.section_key).source_status,
                    "version_no": row.committed_version_no,
                    "doc": (
                        committed[row.section_key].doc if row.section_key in committed else None
                    ),
                    "doc_sha256": (
                        committed[row.section_key].doc_sha256
                        if row.section_key in committed
                        else None
                    ),
                    "editor_schema_version": (
                        committed[row.section_key].editor_schema_version
                        if row.section_key in committed
                        else None
                    ),
                    "ai_assisted_paragraphs": 0,
                    "requirements": _requirement_entries(row, framework),
                }
                for row in section_rows
                if framework.has_section(row.section_key)
            ],
            "blocks": sorted(
                (
                    {
                        # The editor's ``dataBlock``/``factRef`` nodes reference
                        # this UUID (``editor_schema.json`` ``blockId``), so the
                        # exporters cannot resolve a table or a quoted figure
                        # without it — a frozen section would print "no longer
                        # part of this cycle" over a block that is right here.
                        "block_id": str(block.id),
                        "block_key": block.block_key,
                        "block_type": block.block_type,
                        "title": block.title or BLOCK_CATALOGUE[block.block_type].title,
                        "seq": None if binding is None else binding.seq,
                        "payload_sha256": None if binding is None else binding.payload_sha256,
                        "source_kind": None if binding is None else binding.source_kind,
                        "source_key": None if binding is None else binding.source_key,
                        "source_as_of": (
                            None
                            if binding is None or binding.source_as_of is None
                            else binding.source_as_of.isoformat()
                        ),
                        "pin_reason": block.pin_reason,
                        "payload": None if binding is None else binding.payload,
                        "facts": None if binding is None else binding.facts,
                        "never_public": block.block_type
                        in (
                            framework.disclosure.never_public_block_types
                            if framework.disclosure is not None
                            else ()
                        ),
                    }
                    for block, binding in blocks
                ),
                key=lambda entry: entry["block_key"],
            ),
            "attachments": sorted(
                (
                    {
                        "kind": row.kind,
                        "title": row.title,
                        "sha256": row.sha256,
                        "byte_size": row.byte_size,
                        "media_type": row.media_type,
                        "gate": (
                            framework.attachment(row.kind).gate
                            if any(entry.kind == row.kind for entry in framework.attachments)
                            else "optional"
                        ),
                        "attributes": row.attributes or {},
                    }
                    for row in attachments
                ),
                key=lambda entry: (entry["kind"], entry["sha256"]),
            ),
            "attachment_requirements": [
                {
                    "kind": requirement.kind,
                    "title": requirement.title,
                    "gate": requirement.gate,
                    "min_count": requirement.min_count,
                    "applies": requirement.applies_when is None
                    or requirement.applies_when in conditions,
                }
                for requirement in framework.attachments
            ],
            "stages": _stage_entries(state.stages, state.decisions),
            "annexes": sorted(annex_entries, key=lambda entry: entry["return_code"]),
            "parameters": _parameter_provenance(db, bank, framework, cycle.as_of_date),
            "filing": _filing_block(framework),
            "review_digest": review_digest,
        }
    }
    return generation.FrozenSnapshot(
        snapshot=generation.build_envelope(
            bank, period, definition, sections, headline_totals, metadata
        ),
        source_runs=source_runs,
    )


def builder(  # noqa: PLR0913 - the closure captures the whole mint context
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    *,
    review_digest: str,
    period: Any,
    definition: Any,
) -> Callable[[], Any]:
    """The zero-argument snapshot callable ``generate_frozen_package`` invokes.

    A closure over the cycle rather than a registry generator, deliberately: an
    ICAAP report has no meaning outside the cycle that was reviewed, and a
    generator reachable from the registry would be a way to mint a filing
    nobody approved. The registry entry's generator refuses by name for the
    same reason.
    """

    def _build() -> Any:
        return build(
            db,
            access,
            cycle,
            review_digest=review_digest,
            bank=access.bank,
            period=period,
            definition=definition,
        )

    return _build


__all__ = ["SNAPSHOT_SCHEMA", "active_attachments", "build", "builder", "live_blocks"]
