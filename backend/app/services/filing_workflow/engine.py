"""The stage engine in motion, with the plane taken out of it.

P3 built this for the ICAAP: a chain of stages, decisions carrying a round and a
review digest, send-back to a named stage, officer titles checked against
``users.job_title``, and separation of duties enforced server-side. The BoG
package plane needs precisely that shape. So it is lifted here and both planes
run it — because the alternative is two implementations of "may this officer
decide this stage", and a rule with two implementations is a rule with two
behaviours.

What is shared is the part that is the same wherever a chain runs:

* the BLOCKER LADDER — the ordered reasons a caller may not decide the stage in
  front of them, written as a list rather than a chain of early returns so the
  whole policy is readable at once and a new reason cannot silently shadow an
  earlier one;
* the SEPARATION rules, read out of ``domain/workflow/chain.py``: whoever made
  this round cannot check it, and whoever has already checked an earlier stage
  of this round cannot take the next one;
* the OFFICER TITLE check, which is what stops a decision misstating who took
  it;
* the SEND-BACK rules: an earlier stage by name, and a comment somebody can act
  on.

What is NOT shared is each plane's structural chain rules and its own refusal
sentences, which are passed in. An ICAAP that is "not under review" and a return
that is "not with a reviewer" are different sentences about the same position,
and flattening them would make both worse.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.services.attestation import identity
from app.services.filing_workflow.errors import conflict, unprocessable

#: The substring that marks a blocker as a SEPARATION refusal rather than a
#: position one. The authorization condition builders key on it, so a caller who
#: simply is not the current holder does not get a maker-checker denial in the
#: binding trace.
SEPARATION_MARKER = "officer must"

#: A send-back with fewer characters than this is one the preparer cannot act on.
MIN_RETURN_COMMENT = 10


@dataclass(frozen=True)
class BlockerMessages:
    """The plane's own sentences for each rung of the ladder.

    Named rather than formatted from a noun, because "This ICAAP is not under
    review." and "This return is not with a reviewer." are not the same sentence
    with a word swapped, and pretending otherwise produces the kind of copy that
    sends a filing officer to support.
    """

    signed_in: str
    no_such_stage: str
    not_open: str
    not_current_stage: str
    is_maker: str
    is_checker: str


def decision_blocker(  # noqa: PLR0913 - the ladder is exactly these named parts
    *,
    actor: str | None,
    stage_exists: bool,
    chain_open: bool,
    is_current_stage: bool,
    makers: frozenset[str],
    checkers: frozenset[str],
    messages: BlockerMessages,
    after_stage: Sequence[tuple[bool, str]] = (),
) -> str | None:
    """Why this caller may not take the decision at a stage — in plain language.

    ``after_stage`` is the plane's own extra rungs, slotted in immediately after
    "does the stage exist" and before the position and separation checks. ICAAP
    uses it for the Board attestation stage, which is decided by a signature
    rather than here.
    """
    reasons: list[tuple[bool, str]] = [
        (actor is None, messages.signed_in),
        (not stage_exists, messages.no_such_stage),
        *after_stage,
        (not chain_open, messages.not_open),
        (not is_current_stage, messages.not_current_stage),
        (actor is not None and actor in makers, messages.is_maker),
        (actor is not None and actor in checkers, messages.is_checker),
    ]
    return next((message for blocked, message in reasons if blocked), None)


def is_separation_blocker(blocker: str | None) -> bool:
    """Is this refusal about WHO the caller is, rather than where the chain is?"""
    return blocker is not None and SEPARATION_MARKER in blocker


def require_officer_title(
    *,
    stage_seq: int,
    stage_title: str,
    officer_titles: Sequence[object],
    job_title: str | None,
    subject: str,
) -> None:
    """The stage's officer titles, checked against the signer's recorded title.

    Empty ``officer_titles`` means role-only, which is the default: enforcing a
    guessed officer title would block a legitimate reviewer.
    """
    if not officer_titles:
        return
    allowed = [str(entry) for entry in officer_titles]
    if job_title and any(
        entry.strip().casefold() == job_title.strip().casefold() for entry in allowed
    ):
        return
    raise conflict(
        "officer_title_mismatch",
        f"{stage_title} is taken by one of: {', '.join(allowed)}. Your recorded job "
        f"title does not match, so this decision would misstate who approved {subject}.",
        stage_seq=stage_seq,
        allowed=allowed,
        job_title=job_title,
    )


def require_send_back(*, target: int | None, stage_seq: int, comment: str | None) -> int:
    """A send-back names an EARLIER stage and says why.

    Both halves matter and both are refused here rather than in one plane: a
    send-back with no target is a status going backwards that nobody can route,
    and one with no comment is a rejection the preparer cannot act on.
    """
    if target is None or target >= stage_seq or target < 1:
        raise unprocessable(
            "return_target_invalid",
            "A send-back names an earlier stage of this review.",
            stage_seq=stage_seq,
        )
    if not comment or len(comment.strip()) < MIN_RETURN_COMMENT:
        raise unprocessable(
            "return_comment_required",
            "Say what has to change. A send-back with no comment is one the preparer "
            "cannot act on.",
        )
    return target


def signer_display(db: Session, ctx: TenantContext, actor: UUID) -> tuple[str, str | None]:
    """The actor's name and job title AS THEY STAND NOW, copied into the record.

    A later rename must not rewrite who approved a filing, so the decision row
    keeps the value rather than a foreign key to the live user.
    """
    name, title = identity.resolve_signer_display(db, ctx, actor)
    return name or "Name not recorded", title


__all__ = [
    "MIN_RETURN_COMMENT",
    "SEPARATION_MARKER",
    "BlockerMessages",
    "decision_blocker",
    "is_separation_blocker",
    "require_officer_title",
    "require_send_back",
    "signer_display",
]
