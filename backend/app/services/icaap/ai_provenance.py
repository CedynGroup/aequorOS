"""What a committed section says about the AI that helped write it.

Two records, doing different jobs:

* the **decision log** (``icaap_ai_suggestion_decisions``) is unalterable and is
  the tamper-proof answer to "who accepted AI text into this report";
* the **document markers** (``paragraph.aiSuggestionId``) drive the visible badge
  and the count, and they are what an export prints.

They must agree, and the server is what makes them agree: a marker naming a
suggestion that is not this cycle's, or one nobody accepted, is refused at save.
Without that check the badge would be a claim the editor could forge — in either
direction, since a bank might want text to look human-written just as easily as
the reverse.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.icaap import ai_convert
from app.models.icaap import IcaapCycle
from app.models.icaap_ai import IcaapAiSuggestion, IcaapAiSuggestionDecision

PROVENANCE_SCHEMA = "icaap-ai-provenance-v1"


def accepted_suggestion_ids(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> frozenset[str]:
    """Suggestions of THIS cycle that a human actually accepted."""
    rows = db.execute(
        select(IcaapAiSuggestion.id)
        .join(
            IcaapAiSuggestionDecision,
            IcaapAiSuggestionDecision.suggestion_id == IcaapAiSuggestion.id,
        )
        .where(
            IcaapAiSuggestion.organization_id == access.ctx.organization_id,
            IcaapAiSuggestion.cycle_id == cycle.id,
            IcaapAiSuggestionDecision.decision == "accepted",
        )
    )
    return frozenset(str(row[0]) for row in rows)


def unknown_markers(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, doc: Mapping[str, Any]
) -> tuple[str, ...]:
    """Markers in ``doc`` that name no accepted suggestion of this cycle."""
    claimed = set(ai_convert.ai_paragraph_ids(doc))
    from app.domain.icaap.prosemirror import collect_refs  # noqa: PLC0415 - shared walker

    claimed.update(
        str(ref.suggestion_id) for ref in collect_refs(doc).fact_refs if ref.suggestion_id
    )
    if not claimed:
        return ()
    return tuple(sorted(claimed - accepted_suggestion_ids(db, access, cycle)))


def provenance_for_doc(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, doc: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The provenance summary stored on a committed version, or None.

    ``None`` when the text carries no AI marker at all, so a wholly
    human-written section records nothing rather than recording a zero.
    """
    suggestion_ids = ai_convert.ai_paragraph_ids(doc)
    if not suggestion_ids:
        return None
    rows = db.scalars(
        select(IcaapAiSuggestion).where(
            IcaapAiSuggestion.organization_id == access.ctx.organization_id,
            IcaapAiSuggestion.cycle_id == cycle.id,
            IcaapAiSuggestion.id.in_([UUID(value) for value in suggestion_ids]),
        )
    ).all()
    return {
        "schema": PROVENANCE_SCHEMA,
        "ai_paragraph_count": ai_convert.count_ai_paragraphs(doc),
        "paragraph_count": ai_convert.count_paragraphs(doc),
        "suggestion_ids": list(suggestion_ids),
        "models": sorted(
            (
                {
                    "requested": row.model_requested,
                    "served": row.model_served,
                    "fallback_used": bool(row.fallback_used),
                }
                for row in rows
            ),
            key=lambda entry: (entry["requested"], entry["served"] or ""),
        ),
        "prompt_versions": sorted({row.prompt_version for row in rows}),
    }


__all__ = [
    "PROVENANCE_SCHEMA",
    "accepted_suggestion_ids",
    "provenance_for_doc",
    "unknown_markers",
]
