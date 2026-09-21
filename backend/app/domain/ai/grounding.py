"""The grounding validator: what a drafted paragraph is allowed to contain.

This module is the enforcement behind the one promise the AI feature makes to a
bank: **the model never invents a number, and never writes a name.** Every
figure reaches prose as a ``{{F:...}}`` placeholder that the platform resolves
from a bound block fact; every name reaches prose as ``{{E:...}}`` resolved from
the tenant's own registers. Anything else containing a digit, a percent, a
currency, a registry name or a tenant name is refused — and a refused draft is
never shown, only its status.

Two design rules make that safe to rely on:

* **Deny by default over the literal characters.** The digit rule is
  ``str.isdigit`` over the text outside placeholders, so Arabic-Indic and
  full-width digits are caught as surely as ASCII ones. Only terms the shipped
  lexicon names are masked before the scan.
* **Errors carry a code, a location and a span — never the offending text.**
  The text is model output about a bank's capital position; an error list is
  logged, serialised and shown. Copying the sentence into the error would put
  ungrounded prose on the surfaces built to keep it out.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.domain.ai import placeholders
from app.domain.ai.lexicon import Lexicon

#: Replacement character for a masked span. U+FFFC (object replacement) is not
#: a digit, not a letter and not a word character, so a masked term can never
#: make two neighbouring words look like one token.
_MASK: Final = "￼"


@dataclass(frozen=True)
class GroundingContext:
    """Everything the validator is allowed to accept, assembled per request."""

    #: fid -> whether the fact sheet marked it available. A fact that exists but
    #: is unavailable is a DIFFERENT error from one that does not exist: the
    #: first means the model characterised a figure it was told it could not.
    facts: Mapping[str, bool]
    entities: frozenset[str]
    requirement_ids: frozenset[str]
    #: Names from the tenant's own registers (bank, former names, parties,
    #: users). Matched case-insensitively on word boundaries.
    deny_terms: frozenset[str]
    #: Country, currency and regulator names from the GLOBAL registry. These
    #: must arrive through an ``E`` placeholder, so a literal is both a leak
    #: risk and a wrong-regulator hallucination risk.
    jurisdiction_terms: frozenset[str]
    lexicon: Lexicon


@dataclass(frozen=True)
class Limits:
    """Shape limits, all from settings — no number lives in this module."""

    max_paragraphs: int
    max_paragraph_chars: int
    max_open_questions: int


@dataclass(frozen=True)
class GroundingError:
    """One refusal. Code and location only; never a quotation."""

    code: str
    #: ``paragraph[0]``, ``open_question[2]``, or ``draft`` for shape errors.
    where: str
    span: tuple[int, int] | None = None


@dataclass(frozen=True)
class GroundingResult:
    ok: bool
    errors: tuple[GroundingError, ...]

    @property
    def codes(self) -> tuple[str, ...]:
        """The distinct codes, sorted — what the API and the UI show."""
        return tuple(sorted({error.code for error in self.errors}))


def _mask_terms(text: str, terms: Sequence[str]) -> str:
    """Blank out allow-listed vocabulary before the content scan.

    Masking preserves offsets (one mask char per source char) so every span a
    later rule reports still points at the original text.
    """
    masked = text
    for term in terms:
        if not term:
            continue
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        masked = pattern.sub(lambda match: _MASK * (match.end() - match.start()), masked)
    return masked


def _has_digit(text: str) -> int | None:
    """Offset of the first Unicode decimal digit, or None.

    ``str.isdigit`` rather than ``str.isdecimal`` or ``c in "0123456789"``:
    a superscript two and an Arabic-Indic four are both figures a reader would
    read as numbers, and both would pass an ASCII-only check.
    """
    for index, char in enumerate(text):
        if char.isdigit() or unicodedata.category(char) == "Nd":
            return index
    return None


def _word_tokens(text: str) -> list[tuple[int, str]]:
    return [(match.start(), match.group(0)) for match in re.finditer(r"[A-Za-z']+", text)]


def _scan_text(  # noqa: PLR0912, PLR0915 - one branch per refusal reads better than a map
    text: str,
    where: str,
    ctx: GroundingContext,
) -> list[GroundingError]:
    """Every content rule, applied to one paragraph or one open question."""
    errors: list[GroundingError] = []
    lexicon = ctx.lexicon

    if not text.strip():
        return [GroundingError(code="empty_paragraph", where=where)]

    # 1. Grammar. A brace that is not a well-formed placeholder is malformed,
    #    whatever it looks like.
    for span in placeholders.malformed_spans(text):
        errors.append(GroundingError(code="placeholder_malformed", where=where, span=span))

    # 2. Placeholder references. Unknown ids are the model inventing a binding.
    for placeholder in placeholders.extract(text):
        span = (placeholder.start, placeholder.end)
        if placeholder.kind == "fact":
            available = ctx.facts.get(placeholder.value)
            if available is None:
                errors.append(GroundingError(code="unknown_fact", where=where, span=span))
            elif not available:
                errors.append(GroundingError(code="unsupported_fact", where=where, span=span))
        elif placeholder.value not in ctx.entities:
            errors.append(GroundingError(code="unknown_entity", where=where, span=span))

    # 3. Content rules, over the text OUTSIDE placeholders, with allow-listed
    #    vocabulary masked. Offsets stay absolute throughout.
    for offset, run in placeholders.outside_text(text):
        masked = _mask_terms(run, lexicon.maskable_terms)

        digit_at = _has_digit(masked)
        if digit_at is not None:
            errors.append(
                GroundingError(
                    code="digit", where=where, span=(offset + digit_at, offset + digit_at + 1)
                )
            )

        for start, token in _word_tokens(masked):
            folded = token.casefold()
            if folded in lexicon.number_words:
                errors.append(
                    GroundingError(
                        code="number_word",
                        where=where,
                        span=(offset + start, offset + start + len(token)),
                    )
                )
                break

        percent_at = _first_of(masked, ("%", "‰"))
        if percent_at is None:
            percent_at = _first_phrase(masked, lexicon.percent_words)
        if percent_at is not None:
            errors.append(
                GroundingError(
                    code="percent", where=where, span=(offset + percent_at, offset + percent_at + 1)
                )
            )

        symbol_at = _first_of(masked, tuple(sorted(lexicon.currency_symbols)))
        if symbol_at is None:
            symbol_at = _first_token(masked, lexicon.currency_symbol_tokens, case_sensitive=True)
        if symbol_at is not None:
            errors.append(
                GroundingError(
                    code="currency_symbol",
                    where=where,
                    span=(offset + symbol_at, offset + symbol_at + 1),
                )
            )

        # Currency codes are matched as whole UPPERCASE tokens only, so risk
        # acronyms (CAR, LCR, NPL, RWA, EVE, NII) can never collide with them.
        for match in re.finditer(r"\b[A-Z]{3}\b", masked):
            if match.group(0) in lexicon.currency_codes:
                errors.append(
                    GroundingError(
                        code="currency_code",
                        where=where,
                        span=(offset + match.start(), offset + match.end()),
                    )
                )
                break

        jurisdiction_at = _first_term(masked, ctx.jurisdiction_terms)
        if jurisdiction_at is not None:
            errors.append(
                GroundingError(
                    code="jurisdiction_name",
                    where=where,
                    span=(offset + jurisdiction_at[0], offset + jurisdiction_at[1]),
                )
            )

        tenant_at = _first_term(masked, ctx.deny_terms)
        if tenant_at is not None:
            errors.append(
                GroundingError(
                    code="tenant_name",
                    where=where,
                    span=(offset + tenant_at[0], offset + tenant_at[1]),
                )
            )

        person = lexicon.person_pattern.search(masked)
        if person is not None:
            errors.append(
                GroundingError(
                    code="person_reference",
                    where=where,
                    span=(offset + person.start(), offset + person.end()),
                )
            )

        for pattern, code in (
            *((pattern, "url") for pattern in lexicon.url_patterns),
            (lexicon.email_pattern, "email"),
            *((pattern, "advice_language") for pattern in lexicon.advice_patterns),
            *((pattern, "markup") for pattern in lexicon.markup_patterns),
        ):
            match = pattern.search(masked)
            if match is not None:
                errors.append(
                    GroundingError(
                        code=code, where=where, span=(offset + match.start(), offset + match.end())
                    )
                )

    # De-duplicate by (code, span) while keeping the first occurrence's order.
    seen: set[tuple[str, tuple[int, int] | None]] = set()
    unique: list[GroundingError] = []
    for error in errors:
        key = (error.code, error.span)
        if key in seen:
            continue
        seen.add(key)
        unique.append(error)
    return unique


def _first_of(text: str, characters: Sequence[str]) -> int | None:
    for index, char in enumerate(text):
        if char in characters:
            return index
    return None


def _first_phrase(text: str, phrases: Sequence[str]) -> int | None:
    """The earliest phrase match, on WORD BOUNDARIES.

    Boundaries are not cosmetic here. A plain substring search made the short
    lexicon entries fire inside ordinary words — ``pp`` matched inside
    "approved", "applied" and "supplement", so six honest sentences in the
    corpus were rejected as containing a percentage. An over-strict validator
    fails as a feature that never works, which is harder to notice than one that
    leaks, so this rule is exactly as important as the leak rules.
    """
    return _first_bounded(text, phrases, case_sensitive=False)


def _first_token(text: str, tokens: Sequence[str], *, case_sensitive: bool) -> int | None:
    return _first_bounded(text, tokens, case_sensitive=case_sensitive)


def _first_bounded(text: str, needles: Sequence[str], *, case_sensitive: bool) -> int | None:
    flags = 0 if case_sensitive else re.IGNORECASE
    best: int | None = None
    for needle in needles:
        cleaned = needle.strip()
        if not cleaned:
            continue
        # ``(?<!\w)``/``(?!\w)`` rather than ``\b``: the currency tokens include
        # non-word characters ("US$", "GH₵") where ``\b`` would not anchor.
        pattern = re.compile(rf"(?<!\w){re.escape(cleaned)}(?!\w)", flags)
        match = pattern.search(text)
        if match is not None and (best is None or match.start() < best):
            best = match.start()
    return best


def _first_term(text: str, terms: frozenset[str]) -> tuple[int, int] | None:
    """The earliest deny-term match, on word boundaries, case-insensitively.

    Longest term first at a given position, so "Examplar Savings Bank" reports
    the whole name rather than just "Examplar".
    """
    best: tuple[int, int] | None = None
    for term in sorted(terms, key=len, reverse=True):
        cleaned = term.strip()
        if not cleaned:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(cleaned)}(?!\w)", re.IGNORECASE)
        match = pattern.search(text)
        if match is None:
            continue
        span = (match.start(), match.end())
        if best is None or span[0] < best[0]:
            best = span
    return best


def validate(
    paragraphs: Sequence[tuple[str, Sequence[str]]],
    open_questions: Sequence[str],
    ctx: GroundingContext,
    limits: Limits,
) -> GroundingResult:
    """Validate a whole draft. ``paragraphs`` is ``(text, requirement_ids)``.

    Never raises: a malformed draft is an outcome, not an exception. The caller
    stores the codes and shows the user a status.
    """
    errors: list[GroundingError] = []

    if not paragraphs:
        errors.append(GroundingError(code="no_paragraphs", where="draft"))
    if len(paragraphs) > limits.max_paragraphs:
        errors.append(GroundingError(code="too_many_paragraphs", where="draft"))
    if len(open_questions) > limits.max_open_questions:
        errors.append(GroundingError(code="too_many_open_questions", where="draft"))

    for index, (text, requirement_ids) in enumerate(paragraphs):
        where = f"paragraph[{index}]"
        if len(text) > limits.max_paragraph_chars:
            errors.append(GroundingError(code="paragraph_too_long", where=where))
        errors.extend(_scan_text(text, where, ctx))
        for requirement_id in requirement_ids:
            if requirement_id not in ctx.requirement_ids:
                errors.append(GroundingError(code="unknown_requirement", where=where))
                break

    for index, question in enumerate(open_questions):
        where = f"open_question[{index}]"
        if len(question) > limits.max_paragraph_chars:
            errors.append(GroundingError(code="paragraph_too_long", where=where))
        errors.extend(_scan_text(question, where, ctx))

    return GroundingResult(ok=not errors, errors=tuple(errors))


__all__ = [
    "GroundingContext",
    "GroundingError",
    "GroundingResult",
    "Limits",
    "validate",
]
