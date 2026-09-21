"""Turning framework data and block specs into the wire contract.

The one piece of real logic here is parameter resolution. A requirement written
as "span no less than {param:icaap_stress_horizon_years_min} years" carries no
number in the data (D-024); the value comes from the governed control plane at
render time, so staff change it in the console and every bank's checklist
changes with it. When a referenced parameter is still awaiting stakeholder
confirmation the rendered text says so, because a checklist that states a
horizon as settled when it is not is the kind of quiet overclaim these reports
are audited for.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.domain.icaap.blocks import AVAILABLE_BLOCK_TYPES, BLOCK_CATALOGUE, BlockSpec
from app.domain.icaap.frameworks.schema import (
    Citation,
    Framework,
    RequirementItem,
    SectionDef,
)
from app.models import Bank
from app.schemas.icaap import (
    IcaapAttachmentRequirementRead,
    IcaapBlockTypeRead,
    IcaapCitationRead,
    IcaapDeadlineRead,
    IcaapFactSpecRead,
    IcaapFrameworkRead,
    IcaapFrameworkSummaryRead,
    IcaapPillar2ComponentRead,
    IcaapRequirementItemRead,
    IcaapRiskCategoryRead,
    IcaapSectionDefRead,
    IcaapStageTemplateRead,
)
from app.services.icaap import parameters

_PLACEHOLDER = re.compile(r"\{param:([a-z][a-z0-9_]{2,60})\}")
#: What a checklist sentence says about a figure whose row is not confirmed.
#:
#: It used to read "pending stakeholder confirmation", which made a claim the
#: platform cannot make for every row. Ghana's figures come from a BoG exposure
#: draft, so somebody could reasonably be waiting on a stakeholder; Nigeria's
#: and Kenya's come from an extraction record of a text nobody here has read
#: (D-076), where the outstanding step is reading the regulator, not hearing
#: back from a stakeholder. The note now says only what is true of every
#: pending row — the figure is not confirmed — and the row's own
#: ``source_citation``, which rides the frozen snapshot's parameter
#: provenance, says what it rests on.
PENDING_FIGURE_NOTE = " (figure pending confirmation)"


def citation_read(framework: Framework, citation: Citation) -> IcaapCitationRead:
    label = citation.ref
    for document in framework.documents:
        if document.id == citation.doc:
            label = f"{document.short_label} ¶{citation.ref}"
            break
    return IcaapCitationRead(
        cite_id=citation.cite_id, doc=citation.doc, ref=citation.ref, label=label
    )


def resolve_param_values(
    db: Session, bank: Bank, codes: frozenset[str]
) -> tuple[dict[str, str], frozenset[str]]:
    """``(code -> printable value, codes still pending confirmation)``.

    A code with no approved row is a typed ``missing_parameter`` refusal, never
    a substituted number (D-024 §4).
    """
    values: dict[str, str] = {}
    pending: set[str] = set()
    for code in sorted(codes):
        resolved = parameters.resolve(
            db,
            bank,
            code,
            purpose="This ICAAP checklist item quotes a governed figure.",
        )
        if resolved.value is None:
            raise parameters.missing_parameter(
                code, "This ICAAP checklist item quotes a governed figure."
            )
        values[code] = str(resolved.normalized_value)
        if resolved.is_pending:
            pending.add(code)
    return values, frozenset(pending)


def render_requirement_text(
    item: RequirementItem, values: Mapping[str, str], pending: frozenset[str]
) -> str:
    """The checklist sentence with its governed figures filled in."""
    if not item.param_refs:
        return item.text
    rendered = _PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), item.text)
    if any(code in pending for code in item.param_refs):
        rendered = f"{rendered}{PENDING_FIGURE_NOTE}"
    return rendered


def requirement_item_read(framework: Framework, item: RequirementItem) -> IcaapRequirementItemRead:
    return IcaapRequirementItemRead(
        id=item.id,
        text=item.text,
        citations=[citation_read(framework, citation) for citation in item.citations],
        evidence=list(item.evidence),
        waivable=item.waivable,
        applies_when=item.applies_when,
        param_refs=list(item.param_refs),
    )


def section_def_read(framework: Framework, section: SectionDef) -> IcaapSectionDefRead:
    return IcaapSectionDefRead(
        key=section.key,
        letter=section.letter,
        order=section.order,
        title=section.title,
        citation=citation_read(framework, section.citation),
        source_status=section.source_status,
        guidance=section.guidance,
        data_blocks=list(section.data_blocks),
        ai_draftable=section.ai_draftable,
        public_disclosure=section.public_disclosure,
        requirements=[requirement_item_read(framework, item) for item in section.requirements],
    )


def framework_summary(framework: Framework) -> IcaapFrameworkSummaryRead:
    return IcaapFrameworkSummaryRead(
        code=framework.code,
        version=framework.version,
        title=framework.title,
        short_title=framework.short_title,
        regulator=framework.regulator,
        status=framework.status,
        digest=framework.digest,
        effective_from=framework.effective_from,
        first_as_of_date=framework.first_as_of_date,
        filing_return_code=framework.filing_return_code,
    )


def framework_read(db: Session, bank: Bank, framework: Framework) -> IcaapFrameworkRead:
    months = parameters.resolve(
        db,
        bank,
        framework.deadline.months_after_fye_param,
        purpose="The ICAAP filing deadline is measured in months after the year end.",
    )
    summary = framework_summary(framework)
    return IcaapFrameworkRead(
        **summary.model_dump(),
        jurisdiction=framework.jurisdiction,
        first_as_of_basis=framework.first_as_of_basis,
        first_as_of_note=framework.first_as_of_note,
        institution_classes=list(framework.institution_classes),
        deadline=IcaapDeadlineRead(
            as_of=framework.deadline.as_of,
            fy_end_month_day=framework.deadline.fy_end_month_day,
            months_after_fy_end=None if months.value is None else int(months.value),
            months_param_code=framework.deadline.months_after_fye_param,
            months_confirmation_status=months.confirmation_status,
        ),
        sections=[section_def_read(framework, section) for section in framework.sections],
        risk_categories=[
            IcaapRiskCategoryRead(
                key=category.key,
                number=category.number,
                title=category.title,
                title_status=category.title_status,
                citation=citation_read(framework, category.citation),
                pillar1_coverage=category.pillar1_coverage,
                custom=category.custom,
                components=[
                    IcaapPillar2ComponentRead(
                        key=component.key,
                        table5_row=component.table5_row,
                        p29_class=component.p29_class,
                        p29_basis=component.p29_basis,
                        allowed_methods=list(component.allowed_methods),
                    )
                    for component in category.components
                ],
                sub_requirements=[
                    requirement_item_read(framework, item) for item in category.sub_requirements
                ],
            )
            for category in framework.risk_categories
        ],
        attachments=[
            IcaapAttachmentRequirementRead(
                kind=attachment.kind,
                title=attachment.title,
                gate=attachment.gate,
                min_count=attachment.min_count,
                max_count=attachment.max_count,
                media_types=list(attachment.media_types),
                applies_when=attachment.applies_when,
                section_keys=list(attachment.section_keys),
                citations=[citation_read(framework, c) for c in attachment.citations],
            )
            for attachment in framework.attachments
        ],
        stages=[
            IcaapStageTemplateRead(
                seq=stage.seq,
                key=stage.key,
                title=stage.title,
                decision=stage.decision,
                freeze_on_approve=stage.freeze_on_approve,
            )
            for stage in framework.stages
        ],
        documents=[
            {
                "id": document.id,
                "title": document.title,
                "short_label": document.short_label,
                "issuer": document.issuer,
                "issued": document.issued,
                "status": document.status,
                "url": document.url,
            }
            for document in framework.documents
        ],
        notes=[
            {
                "code": note.code,
                "text": note.text,
                "citations": [c.cite_id for c in note.citations],
            }
            for note in framework.notes
        ],
    )


def block_type_read(spec: BlockSpec) -> IcaapBlockTypeRead:
    return IcaapBlockTypeRead(
        type=spec.type,
        title=spec.title,
        phase=spec.phase,
        available=spec.type in AVAILABLE_BLOCK_TYPES,
        manual=spec.manual,
        dynamic_facts=spec.dynamic_facts,
        facts=[
            IcaapFactSpecRead(key=fact.key, label=fact.label, kind=fact.kind) for fact in spec.facts
        ],
    )


def block_types() -> list[IcaapBlockTypeRead]:
    return [
        block_type_read(spec)
        for spec in sorted(BLOCK_CATALOGUE.values(), key=lambda s: (s.phase, s.type))
    ]


__all__ = [
    "PENDING_FIGURE_NOTE",
    "block_type_read",
    "block_types",
    "citation_read",
    "framework_read",
    "framework_summary",
    "render_requirement_text",
    "requirement_item_read",
    "resolve_param_values",
    "section_def_read",
]
