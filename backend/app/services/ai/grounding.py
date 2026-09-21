"""Assemble a ``GroundingContext`` from a tenant's own registers.

Thin by design: the rules live in ``app/domain/ai/grounding.py`` where they are
pure and exhaustively testable. This is the seam that fills them with a specific
bank's names, the global jurisdiction registry, and the limits from settings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.ai.grounding import GroundingContext, Limits
from app.domain.ai.lexicon import load_lexicon
from app.models import Bank
from app.services.ai import pseudonymise


def limits_from_settings() -> Limits:
    ai = get_settings().ai
    return Limits(
        max_paragraphs=ai.max_paragraphs,
        max_paragraph_chars=ai.max_paragraph_chars,
        max_open_questions=ai.max_open_questions,
    )


def build_context(  # noqa: PLR0913 - the grounding context is its six sources
    db: Session,
    *,
    organization_id: str,
    bank: Bank,
    facts: Mapping[str, bool],
    entity_keys: Sequence[str],
    requirement_ids: Sequence[str],
) -> GroundingContext:
    return GroundingContext(
        facts=dict(facts),
        entities=frozenset(entity_keys),
        requirement_ids=frozenset(requirement_ids),
        deny_terms=pseudonymise.tenant_deny_terms(db, organization_id, bank),
        jurisdiction_terms=pseudonymise.jurisdiction_deny_terms(db),
        lexicon=load_lexicon(),
    )


__all__ = ["build_context", "limits_from_settings"]
