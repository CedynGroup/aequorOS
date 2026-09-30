"""Grounding: does this draft say only what the platform can stand behind?

Thin on purpose. The rules are in ``app/domain/ai/grounding.py``, where they are
pure and exhaustively tested, and the tenant-specific material they need — the
bank's own names, the global jurisdiction registry, the shipped lexicon, the
shape limits from settings — is assembled by ``app/services/ai/grounding.py``.
This module only says what a BI commentary draft's context IS:

* ``facts`` is the availability map the minimiser built, so a ``{{F:...}}`` id
  the payload never offered is ``unknown_fact`` and one whose figure the platform
  does not hold is ``unsupported_fact``;
* ``entities`` is the key set the payload offered, so ``{{E:framework}}`` — which
  BI never offers — is ``unknown_entity``;
* ``requirement_ids`` is EMPTY. A regulatory requirement is an ICAAP idea; BI
  commentary answers to no checklist, and an empty set means any requirement tag
  the model invents is refused rather than quietly accepted.

Every failure is a code, a location and a span — never the offending text. The
text is model output about a bank's capital position and the whole point of
refusing it is not putting it in front of somebody, so it is stored for audit and
evals and never serialised to a client.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.domain.ai import grounding as grounding_domain
from app.models import Bank
from app.services.ai import grounding as grounding_service

__all__ = ["error_entries", "validate_draft"]


def validate_draft(  # noqa: PLR0913 - the draft, whose it is, and what it may cite
    db: Session,
    *,
    organization_id: str,
    bank: Bank,
    availability: Mapping[str, bool],
    entity_keys: Sequence[str],
    paragraphs: Sequence[str],
    open_questions: Sequence[str],
) -> grounding_domain.GroundingResult:
    """Validate one commentary draft. Never raises: a bad draft is an outcome."""
    ctx = grounding_service.build_context(
        db,
        organization_id=organization_id,
        bank=bank,
        facts=dict(availability),
        entity_keys=[str(key) for key in entity_keys],
        requirement_ids=(),
    )
    return grounding_domain.validate(
        [(text, ()) for text in paragraphs],
        list(open_questions),
        ctx,
        grounding_service.limits_from_settings(),
    )


def error_entries(result: grounding_domain.GroundingResult) -> list[dict[str, Any]]:
    """The ``validation_errors`` column: codes and locations, never quotations."""
    return [
        {
            "code": error.code,
            "where": error.where,
            "span": list(error.span) if error.span else None,
        }
        for error in result.errors
    ]
