"""What is sent to the model, screened and frozen — and what is never sent.

Three properties, and each of them is a promise the consent document makes.

**No tenant figure, ever.** The payload is the question plus catalogue METADATA:
ids, labels, descriptions, declared value vocabularies. Every byte of that is
static code under ``app/domain/bi/catalogue/``; none of it is read from a tenant
table. There is no balance, no ratio, no counterparty, no branch code and no row
count in it, so the "aggregated data only" posture the BI risk table states is not
a rule this surface has to be careful about — it is a shape it cannot violate.

**No date.** Not even the reporting date the reader selected. The model emits an
intent (a single date or a trailing window) and the PLATFORM computes every date
from the reader's own selection, so a date never travels in either direction. Absolute
dates are on the consent document's withheld list, and a question does not need one.

**The question is screened before it is sent, not after.**
``pseudonymise.scrub_label`` is the platform's existing screen for the one free-text
field a user can put into a model request, and it is reused here rather than
reimplemented: it refuses control characters, the ``{{``/``}}`` placeholder channel,
email addresses, URLs, and any name in the tenant's own registers (the institution,
its former names, related parties, this organisation's users) or in the global
jurisdiction registry (country, currency, central bank, regulator). A question it
refuses is never sent.

What that screen CANNOT catch is a customer name the tenant's registers do not hold
and a user types anyway. That is stated in the report rather than papered over: the
words of a question are the input, so the feature cannot exist without sending them,
and the consent text has to say so before a tenant is offered it.

The payload is hashed with ``attestation.digests`` and the worker re-hashes it before
calling the model, so a queued request can never send something other than what the
process holding the reader's authority froze.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models import Bank
from app.schemas.bi_nlq import QUESTION_MAX_CHARS
from app.services.ai import pseudonymise
from app.services.attestation.digests import canonical_json, sha256_hex
from app.services.bi.nlq.candidates import CandidateSet

#: Why a question was not sent at all. Closed, and each has its own production copy
#: in :data:`SCREEN_MESSAGES`: a reader who is refused must be able to fix it.
NlqScreenCode = Literal["question_empty", "question_too_long", "question_withheld"]

SCREEN_MESSAGES: dict[NlqScreenCode, str] = {
    "question_empty": "Type a question about the figures you want to see.",
    "question_too_long": (
        f"Questions are limited to {QUESTION_MAX_CHARS} characters. Ask for one thing at a time."
    ),
    "question_withheld": (
        "That question was not sent. A question may not contain a web address, an email "
        "address, or the name of your institution, a related party or a colleague — ask "
        "for it by the name of the figure instead, such as the ratio or the balance you "
        "want."
    ),
}


class QuestionWithheld(Exception):
    """The question may not leave the platform. Carries the reason the reader sees."""

    def __init__(self, code: NlqScreenCode) -> None:
        super().__init__(code)
        self.code: NlqScreenCode = code
        self.message = SCREEN_MESSAGES[code]


def screen(db: Session, *, organization_id: str, bank: Bank, question: str) -> str:
    """The question as it may be sent, or raise :class:`QuestionWithheld`.

    Returns the STRIPPED text, which is what is hashed and stored: two questions
    differing only in trailing whitespace are one question.
    """

    if pseudonymise.sendable_free_text(question, frozenset(), max_chars=QUESTION_MAX_CHARS) is None:
        # Length and emptiness first, so the reader is told the fixable thing.
        text = question.strip()
        if not text:
            raise QuestionWithheld("question_empty")
        if len(text) > QUESTION_MAX_CHARS:
            raise QuestionWithheld("question_too_long")
        raise QuestionWithheld("question_withheld")
    deny = pseudonymise.tenant_deny_terms(db, organization_id, bank) | (
        pseudonymise.jurisdiction_deny_terms(db)
    )
    sendable = pseudonymise.sendable_free_text(question, deny, max_chars=QUESTION_MAX_CHARS)
    if sendable is None:
        raise QuestionWithheld("question_withheld")
    return sendable


def _measure_entry(measure: Any, dimension_ids: frozenset[str]) -> dict[str, Any]:
    """One offered figure. ``can_be_grouped_by`` is intersected with what is offered,
    so the model is never shown a grouping it would then be refused for naming."""

    return {
        "id": measure.id,
        "name": measure.label,
        "what_it_is": measure.description,
        "unit": measure.value_type,
        "measured_over": measure.time_behaviour,
        "whole_institution_only": measure.grain == "institution",
        "can_be_grouped_by": sorted(
            dimension_id
            for dimension_id in measure.allowed_dimensions
            if dimension_id in dimension_ids
        ),
    }


def _dimension_entry(dimension: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": dimension.id,
        "name": dimension.label,
        "value_type": dimension.value_type,
    }
    if dimension.description:
        entry["what_it_is"] = dimension.description
    if dimension.values:
        entry["allowed_values"] = [
            {"value": value.code, "name": value.label} for value in dimension.values
        ]
    return entry


@dataclass(frozen=True, slots=True)
class BuiltPayload:
    """The frozen request: what is sent, its digest, and the ids it offered."""

    payload: dict[str, Any]
    sha256: str
    offered_member_ids: tuple[str, ...]


def build(candidates: CandidateSet, *, question: str) -> BuiltPayload:
    """The complete user turn for one question. Contains no tenant value."""

    dimension_ids = candidates.dimension_ids
    payload: dict[str, Any] = {
        "question": question,
        "figures": [_measure_entry(measure, dimension_ids) for measure in candidates.measures],
        "groupings": [_dimension_entry(dimension) for dimension in candidates.dimensions],
        "drill_paths": [
            {"id": hierarchy_id, "levels": list(levels)}
            for hierarchy_id, levels in candidates.hierarchies
        ],
    }
    return BuiltPayload(
        payload=payload,
        sha256=digest(payload),
        offered_member_ids=candidates.member_ids,
    )


def digest(payload: dict[str, Any]) -> str:
    """The value-based digest of a payload, stable across equal payloads."""

    return sha256_hex(canonical_json(payload))


__all__ = [
    "SCREEN_MESSAGES",
    "BuiltPayload",
    "NlqScreenCode",
    "QuestionWithheld",
    "build",
    "digest",
    "screen",
]
