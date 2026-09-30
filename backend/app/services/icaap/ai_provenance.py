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

**v2 (D-053) names the VENDOR.** The platform now fails over between providers on
availability, so two runs of the same cycle with the same figures can be drafted
by different companies' models. On a FILED report that is not a logging detail:
the stamp rides the committed section version, which is what the frozen package
and its export are built from, so a supervisor asking "who wrote this" gets the
vendor, the model it served, the tier position it was reached at, and the request
features that vendor could not honour. ``degraded_capabilities`` is how the
Anthropic-only behaviour — prompt caching on the static prompt, the server-side
fallback beta, adaptive thinking, effort control — stays visible instead of
vanishing when another tier answers.
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

PROVENANCE_SCHEMA = "icaap-ai-provenance-v2"


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


def _model_entry(row: IcaapAiSuggestion) -> dict[str, Any]:
    """One suggestion's model provenance, vendor included.

    The vendor half lives in the sealed row's call record rather than in a column
    of its own, beside the served geography that is already kept there for the
    same reason: it is evidence about the call, not a parameter of it. A row
    written before the tier existed has no call record and reports ``None``, which
    is honest — that draft came from the single-vendor path.
    """
    record = row.usage if isinstance(row.usage, dict) else {}
    return {
        "vendor": record.get("vendor"),
        "requested": row.model_requested,
        "served": row.model_served,
        "fallback_used": bool(row.fallback_used),
        "tier_position": record.get("tier_position"),
        "degraded_capabilities": list(record.get("degraded_capabilities") or []),
    }


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
            (_model_entry(row) for row in rows),
            key=lambda entry: (
                str(entry["vendor"] or ""),
                str(entry["requested"]),
                str(entry["served"] or ""),
            ),
        ),
        "prompt_versions": sorted({row.prompt_version for row in rows}),
    }


__all__ = [
    "PROVENANCE_SCHEMA",
    "accepted_suggestion_ids",
    "provenance_for_doc",
    "unknown_markers",
]
