"""What a review chain is, and what a decision round means (pure, shared).

This is the ICAAP stage engine (``domain/icaap/workflow.py``, P3) with the
ICAAP out of it. Two planes run it — the ICAAP review chain and the BoG filing
chain — and they run the SAME code, because the alternative is two
implementations of "does this approval still stand" that drift.

Three things live here:

**Stage field parsing.** A chain is data a bank can configure, but it is not
free-form: a stage key is an identifier, a title is something people recognise,
officer titles are a short list of distinct names. Each plane adds its own
structural rules (ICAAP ends at the Board; a filing chain ends at whoever
transmits) on top of these shared field rules.

**What a decision applies to.** A forward decision is taken on a ROUND. When a
stage sends the work back, the round increases and every stage from the return
target onwards must decide again; earlier stages keep their decisions, because
nothing they looked at was reopened. :func:`reset_round_for` is that rule,
written once so the service, the timeline and any readiness gate cannot
disagree — including the ``+ 1``, which is the fix described in its docstring.

**Value normalisation for a review digest.** :func:`normalise_amount` is the
D-067 fix: ``Decimal("1")`` and ``Decimal("1.000000")`` are the same amount, and
which one a session holds depends on whether the row has been reloaded since it
was written. A digest over the raw value therefore moved when nothing had
changed, and a reviewer's approval expired for no reason. It ships with the
engine so the next plane cannot reintroduce the bug by writing its own digest.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

#: Decisions that move a chain forward. At most one may exist per
#: (subject, round, stage) — each plane's partial unique index enforces it.
FORWARD_DECISIONS: frozenset[str] = frozenset({"submitted", "reviewed", "approved"})

STAGE_KEY = re.compile(r"^[a-z][a-z0-9_]{1,59}$")
MAX_STAGES = 20
MAX_OFFICER_TITLES = 10
MAX_TITLE_LEN = 120
MAX_STAGE_TITLE_LEN = 200


class StageChainError(ValueError):
    """A review chain the platform refuses, with a machine code and a sentence."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def fail(code: str, message: str) -> StageChainError:
    return StageChainError(code, message)


# ---------------------------------------------------------------------------
# Stage field parsing — the rules every plane shares
# ---------------------------------------------------------------------------


def parse_seq(raw: object, index: int) -> int:
    seq = raw if raw is not None else index + 1
    if not isinstance(seq, int) or isinstance(seq, bool):
        raise fail("stage_seq_invalid", f"Stage {index + 1}: seq must be a whole number.")
    return seq


def parse_stage_key(raw: object, seq: int) -> str:
    if not isinstance(raw, str) or not STAGE_KEY.match(raw):
        raise fail(
            "stage_key_invalid",
            f"Stage {seq}: the key must be lower-case letters, digits and underscores.",
        )
    return raw


def parse_stage_title(raw: object, seq: int) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise fail("stage_title_required", f"Stage {seq}: a stage needs a title people recognise.")
    if len(raw) > MAX_STAGE_TITLE_LEN:
        raise fail(
            "stage_title_invalid",
            f"Stage {seq}: a stage title may not exceed {MAX_STAGE_TITLE_LEN} characters.",
        )
    return raw.strip()


def parse_officer_titles(raw: object, seq: int) -> tuple[str, ...]:
    """The job titles that may take a stage; empty means role-only."""
    if raw is None:
        return ()
    if not isinstance(raw, list | tuple):
        raise fail("stage_officer_titles_invalid", f"Stage {seq}: officer titles must be a list.")
    titles: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise fail(
                "stage_officer_titles_invalid",
                f"Stage {seq}: each officer title must be a non-empty name.",
            )
        if len(entry) > MAX_TITLE_LEN:
            raise fail(
                "stage_officer_titles_invalid",
                f"Stage {seq}: an officer title may not exceed {MAX_TITLE_LEN} characters.",
            )
        titles.append(entry.strip())
    if len(titles) > MAX_OFFICER_TITLES:
        raise fail(
            "stage_officer_titles_invalid",
            f"Stage {seq}: at most {MAX_OFFICER_TITLES} officer titles.",
        )
    if len({title.casefold() for title in titles}) != len(titles):
        raise fail("stage_officer_titles_invalid", f"Stage {seq}: officer titles repeat.")
    return tuple(titles)


def parse_flag(raw: object, seq: int, *, code: str, field: str) -> bool:
    value = False if raw is None else raw
    if not isinstance(value, bool):
        raise fail(code, f"Stage {seq}: {field} must be true or false.")
    return value


def require_chain_length(raw_stages: Sequence[object]) -> None:
    if not raw_stages:
        raise fail("stages_required", "A review chain needs at least one stage.")
    if len(raw_stages) > MAX_STAGES:
        raise fail("stages_too_many", f"A review chain may not exceed {MAX_STAGES} stages.")


def require_contiguous_unique(seqs: Sequence[int], keys: Sequence[str]) -> None:
    if list(seqs) != list(range(1, len(seqs) + 1)):
        raise fail("stage_seq_not_contiguous", "Stages must be numbered 1, 2, 3 … with no gaps.")
    if len(set(keys)) != len(keys):
        raise fail("stage_keys_not_unique", "Two stages share the same key.")


# ---------------------------------------------------------------------------
# Rounds: which decisions still stand
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionFact:
    """The parts of a recorded decision that decide whether it still stands."""

    stage_seq: int
    round: int
    decision: str
    return_to_seq: int | None
    review_digest: str
    decided_by: str


def epoch_start(decisions: Sequence[DecisionFact]) -> int:
    """The round of the latest submission — the epoch the current content belongs to.

    Content can only change while the subject is open for preparation, and every
    resubmission records a ``submitted`` decision, so a decision taken before the
    latest submission was taken on content that has since been replaced.
    """
    submitted = [fact.round for fact in decisions if fact.decision == "submitted"]
    return max(submitted) if submitted else 1


def reset_round_for(decisions: Sequence[DecisionFact], stage_seq: int) -> int:
    """The first round in which a decision at ``stage_seq`` still counts.

    A return to stage *k* invalidates every stage from *k* onwards: those
    reviewers are being asked to look again. Stages before *k* keep their
    decisions — nothing they reviewed has been reopened.

    The floor is the round AFTER the return, not the return's own round,
    because a return bumps the subject's round. Using the return's round would
    leave the decision it reopened still counting: the Approver's review from
    round 1 would satisfy a send-back taken in round 1, and "this stage must
    look again" would be true on the timeline and false in the rule.
    """
    floor = epoch_start(decisions)
    for fact in decisions:
        if fact.decision != "returned" or fact.return_to_seq is None:
            continue
        if fact.return_to_seq <= stage_seq:
            floor = max(floor, fact.round + 1)
    return floor


def valid_forward_decisions(
    decisions: Sequence[DecisionFact], *, current_round: int
) -> tuple[DecisionFact, ...]:
    """Forward decisions that have not been invalidated by a later return."""
    return tuple(
        fact
        for fact in decisions
        if fact.decision in FORWARD_DECISIONS
        and fact.round >= reset_round_for(decisions, fact.stage_seq)
        and fact.round <= current_round
    )


def stage_decided(decisions: Sequence[DecisionFact], stage_seq: int, *, current_round: int) -> bool:
    """Has ``stage_seq`` taken a forward decision that still stands?"""
    return any(
        fact.stage_seq == stage_seq
        for fact in valid_forward_decisions(decisions, current_round=current_round)
    )


def makers(
    decisions: Sequence[DecisionFact],
    *,
    content_authors: Iterable[str],
) -> frozenset[str]:
    """Everybody who WROTE the thing under review, in the current epoch.

    A maker is anyone whose own work is in what is being reviewed, plus whoever
    submitted it. None of them may review or approve it: that is the whole point
    of four eyes.
    """
    start = epoch_start(decisions)
    submitters = {
        fact.decided_by
        for fact in decisions
        if fact.decision == "submitted" and fact.round >= start
    }
    return frozenset({*content_authors, *submitters})


def checkers(
    decisions: Sequence[DecisionFact], *, current_round: int, exclude_seq: int | None
) -> frozenset[str]:
    """Everybody who has already decided a review or approval that still stands.

    ``exclude_seq`` is the stage being decided now: re-deciding your own stage
    after a return is the normal path, so your earlier decision at that stage
    does not bar you.
    """
    return frozenset(
        fact.decided_by
        for fact in valid_forward_decisions(decisions, current_round=current_round)
        if fact.decision != "submitted" and fact.stage_seq != exclude_seq
    )


# ---------------------------------------------------------------------------
# Digest inputs
# ---------------------------------------------------------------------------


def canonical_json(payload: object) -> str:
    """Byte-identical to ``services/attestation/digests.canonical_json``.

    Here so a digest taken by the shared engine cannot drift from the one the
    attestation plane takes; the ICAAP framework registry carries the same
    function for the same reason.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def normalise_amount(value: Decimal | None) -> str | None:
    """A numeric value as ONE canonical string, whatever its scale (D-067).

    ``Decimal("1")`` and ``Decimal("1.000000")`` are the same amount, and which
    one the session holds depends on whether the row has been reloaded from its
    numeric column since it was written. Stringifying the raw value therefore
    made a review digest change when nothing about the subject had — a
    reviewer's approval would have expired because SQLAlchemy refreshed an
    object. The digest is over the VALUE, so the scale is normalised away.

    Every plane's review digest must run its numbers through this. It lives in
    the shared engine precisely so a second plane cannot reintroduce the bug by
    writing ``str(value)`` in a digest body of its own.
    """
    if value is None:
        return None
    try:
        normalised = Decimal(value).normalize()
    except (InvalidOperation, TypeError, ValueError):  # pragma: no cover - column is numeric
        return str(value)
    if normalised == 0:
        return "0"
    return format(normalised, "f")


__all__ = [
    "FORWARD_DECISIONS",
    "MAX_OFFICER_TITLES",
    "MAX_STAGES",
    "MAX_STAGE_TITLE_LEN",
    "MAX_TITLE_LEN",
    "STAGE_KEY",
    "DecisionFact",
    "StageChainError",
    "canonical_json",
    "checkers",
    "epoch_start",
    "fail",
    "makers",
    "normalise_amount",
    "parse_flag",
    "parse_officer_titles",
    "parse_seq",
    "parse_stage_key",
    "parse_stage_title",
    "require_chain_length",
    "require_contiguous_unique",
    "reset_round_for",
    "stage_decided",
    "valid_forward_decisions",
]
