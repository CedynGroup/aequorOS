"""Who worked on this ICAAP — the set an independent reviewer must not be in.

¶49(n) asks for an INDEPENDENT review of the ICAAP. Independence is not a job
title here, it is a fact about this cycle: somebody who wrote its text, bound
its figures, quantified its Pillar 2 items or answered its challenges cannot
also be the person who reviews it. The list below is that fact, gathered from
the rows themselves rather than from a flag somebody could forget to set.

The reviewer's OWN report attachment is deliberately excluded: uploading the
independent review report is the reviewer's act, and counting it would make
every reviewer ineligible the moment they filed their report.

P3 appends the stage deciders (a reviewer who approved a stage is likewise not
independent of the result). The registry below is the extension point, so that
addition is one entry rather than a rewrite.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.models.icaap import (
    IcaapAttachment,
    IcaapBlockBinding,
    IcaapCycle,
    IcaapSection,
    IcaapSectionVersion,
)
from app.models.icaap_risk_capital import (
    IcaapAppetiteMetric,
    IcaapCapitalAllocation,
    IcaapChallengeResponse,
    IcaapControlExplanation,
    IcaapPillar2ItemRevision,
    IcaapRequirementReconciliationLine,
    IcaapResourcesReconciliationLine,
    IcaapRiskAssessment,
)

#: An independent reviewer uploads this; it must not make them a participant.
REVIEWER_ATTACHMENT_KIND = "independent_review_report"

_Source = Callable[[str, UUID], "Select[Any]"]


def _column(model: type, name: str) -> Any:
    return getattr(model, name)


def _selector(model: type, author: str) -> _Source:
    """One statement per way of taking part, built from the model's own columns."""

    def build(organization_id: str, cycle_id: UUID) -> Select[Any]:
        return select(_column(model, author)).where(
            _column(model, "organization_id") == organization_id,
            _column(model, "cycle_id") == cycle_id,
        )

    return build


def _attachment_selector(organization_id: str, cycle_id: UUID) -> Select[Any]:
    return select(IcaapAttachment.uploaded_by).where(
        IcaapAttachment.organization_id == organization_id,
        IcaapAttachment.cycle_id == cycle_id,
        IcaapAttachment.kind != REVIEWER_ATTACHMENT_KIND,
    )


#: Every way a person becomes part of the work this cycle records.
PARTICIPANT_SOURCES: tuple[tuple[str, _Source], ...] = (
    ("section_committed", _selector(IcaapSectionVersion, "committed_by")),
    ("section_edited", _selector(IcaapSection, "working_updated_by")),
    ("block_bound", _selector(IcaapBlockBinding, "bound_by")),
    ("attachment_uploaded", _attachment_selector),
    ("risk_assessed", _selector(IcaapRiskAssessment, "updated_by")),
    ("appetite_metric_saved", _selector(IcaapAppetiteMetric, "updated_by")),
    ("pillar2_revision_authored", _selector(IcaapPillar2ItemRevision, "created_by")),
    ("requirement_computed", _selector(IcaapRequirementReconciliationLine, "computed_by")),
    ("requirement_explained", _selector(IcaapRequirementReconciliationLine, "explanation_by")),
    ("resources_line_saved", _selector(IcaapResourcesReconciliationLine, "updated_by")),
    ("control_explained", _selector(IcaapControlExplanation, "explained_by")),
    ("allocation_saved", _selector(IcaapCapitalAllocation, "updated_by")),
    ("challenge_responded", _selector(IcaapChallengeResponse, "responded_by")),
)


def cycle_participants(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> frozenset[UUID]:
    """Everyone who made or changed part of this ICAAP."""
    found: set[UUID] = {cycle.created_by}
    for _name, build in PARTICIPANT_SOURCES:
        for value in db.scalars(build(access.ctx.organization_id, cycle.id)):
            if value is not None:
                found.add(value)
    return frozenset(found)


def participation_reasons(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, user_id: UUID
) -> tuple[str, ...]:
    """Why this person counts as a participant — for an honest refusal message."""
    reasons: list[str] = []
    if cycle.created_by == user_id:
        reasons.append("cycle_created")
    for name, build in PARTICIPANT_SOURCES:
        statement = build(access.ctx.organization_id, cycle.id)
        if any(value == user_id for value in db.scalars(statement)):
            reasons.append(name)
    return tuple(reasons)


def is_participant(db: Session, access: IcaapAccess, cycle: IcaapCycle, user_id: UUID) -> bool:
    return user_id in cycle_participants(db, access, cycle)


def source_names() -> Sequence[str]:
    return tuple(name for name, _build in PARTICIPANT_SOURCES)


__all__ = [
    "PARTICIPANT_SOURCES",
    "REVIEWER_ATTACHMENT_KIND",
    "cycle_participants",
    "is_participant",
    "participation_reasons",
    "source_names",
]
