"""The filing review chain: Preparer → Approver → Validator, as DATA (pure domain).

Bernard's requirement is three distinct roles — the Preparer prepares and cannot
validate, the Approver reviews and may send back, the Validator reviews,
approves and is the only officer who transmits to BoG. The temptation is to add
a third hardcoded status. That is exactly the mistake that produced the problem:
"Approver" was hardcoded, and the next bank's process did not fit it. Banks
differ — three stages here, four elsewhere, "Compliance Sign-off" instead of
"Validator".

So the chain is data, validated by the same engine the ICAAP chain uses
(``domain/workflow/chain.py``), and Bernard's three ship as the DEFAULT
template rather than as the only possibility.

**The structural rules of a filing chain**, and why each exists:

* exactly one preparation stage, first — without it nothing can be submitted;
* at least one review or approval after it — a chain where the preparer is also
  the only reviewer is not a review;
* exactly one stage marked ``transmit_on_approve``, and it must be LAST —
  transmission is the end of the chain by definition, and a chain with two
  transmitting stages, or one in the middle, would mean the return reaches the
  regulator before somebody in the chain has seen it;
* the transmitting stage is an approval — a review records an opinion, and the
  act that releases a return to a regulator is an approval.

There is deliberately no ``attest`` kind here. The Board attestation is an ICAAP
concept; a BSD return's signatures are the attestation plane's business
(``docs/attestation_esignature.md``) and are demanded by policy rows, not by a
stage.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.domain.workflow import chain

FilingStageKind = Literal["prepare", "review", "approve"]

FORWARD_DECISIONS: frozenset[str] = chain.FORWARD_DECISIONS
#: The forward decision a stage of each kind records.
DECISION_FOR_KIND: dict[str, str] = {
    "prepare": "submitted",
    "review": "reviewed",
    "approve": "approved",
}
#: Every decision a filing stage can record. ``returned`` is the INTERNAL
#: send-back. It is deliberately not one of the regulator's outcomes: ORASS
#: ``rejected`` (returned for correction) and ``declined`` (final refusal) stay
#: package statuses, because an audit trail that conflated "BoG rejected this"
#: with "our Approver sent it back" would be worse than no trail at all.
FILING_STAGE_DECISIONS: tuple[str, ...] = ("submitted", "reviewed", "approved", "returned")

StageChainError = chain.StageChainError
DecisionFact = chain.DecisionFact
epoch_start = chain.epoch_start
reset_round_for = chain.reset_round_for
valid_forward_decisions = chain.valid_forward_decisions
stage_decided = chain.stage_decided
makers = chain.makers
checkers = chain.checkers

_fail = chain.fail


@dataclass(frozen=True)
class FilingStage:
    """One step of a filing review chain, independent of where it came from."""

    seq: int
    stage_key: str
    title: str
    decision_kind: FilingStageKind
    officer_titles: tuple[str, ...]
    transmit_on_approve: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "stage_key": self.stage_key,
            "title": self.title,
            "decision_kind": self.decision_kind,
            "officer_titles": list(self.officer_titles),
            "transmit_on_approve": self.transmit_on_approve,
        }


#: Bernard's three, seeded as the default. Officer titles are deliberately
#: EMPTY: enforcing a guessed job title would block a legitimate officer, and a
#: bank that wants "Head of Regulatory Reporting" on the Approver stage says so
#: in its own template.
DEFAULT_STAGES: tuple[FilingStage, ...] = (
    FilingStage(
        seq=1,
        stage_key="preparation",
        title="Preparer",
        decision_kind="prepare",
        officer_titles=(),
        transmit_on_approve=False,
    ),
    FilingStage(
        seq=2,
        stage_key="approval",
        title="Approver",
        decision_kind="approve",
        officer_titles=(),
        transmit_on_approve=False,
    ),
    FilingStage(
        seq=3,
        stage_key="validation",
        title="Validator",
        decision_kind="approve",
        officer_titles=(),
        transmit_on_approve=True,
    ),
)


def _one_stage(raw: object, index: int) -> FilingStage:
    if not isinstance(raw, dict):
        raise _fail("stage_invalid", f"Stage {index + 1} is not an object.")
    seq = chain.parse_seq(raw.get("seq"), index)
    stage_key = chain.parse_stage_key(raw.get("stage_key") or raw.get("key"), seq)
    title = chain.parse_stage_title(raw.get("title"), seq)
    kind = raw.get("decision_kind") or raw.get("decision")
    if kind not in DECISION_FOR_KIND:
        raise _fail(
            "stage_decision_kind_invalid",
            f"Stage {seq}: a filing stage is a preparation, a review or an approval.",
        )
    transmit = chain.parse_flag(
        raw.get("transmit_on_approve"),
        seq,
        code="stage_transmit_flag_invalid",
        field="transmit_on_approve",
    )
    return FilingStage(
        seq=seq,
        stage_key=stage_key,
        title=title,
        decision_kind=kind,  # pyright: ignore[reportArgumentType]
        officer_titles=chain.parse_officer_titles(raw.get("officer_titles"), seq),
        transmit_on_approve=transmit,
    )


def validate_stages(raw_stages: Sequence[object]) -> tuple[FilingStage, ...]:
    """The one authority on whether a filing chain is runnable."""
    chain.require_chain_length(raw_stages)
    stages = tuple(_one_stage(raw, index) for index, raw in enumerate(raw_stages))
    chain.require_contiguous_unique(
        [stage.seq for stage in stages], [stage.stage_key for stage in stages]
    )

    prepare = [stage for stage in stages if stage.decision_kind == "prepare"]
    if len(prepare) != 1 or prepare[0].seq != 1:
        raise _fail(
            "stage_prepare_required",
            "A filing chain starts with exactly one preparation stage.",
        )
    reviewing = [stage for stage in stages if stage.decision_kind in {"review", "approve"}]
    if not reviewing:
        raise _fail(
            "stage_review_required",
            "A filing chain needs at least one review or approval after preparation.",
        )
    transmit = [stage for stage in stages if stage.transmit_on_approve]
    if len(transmit) != 1:
        raise _fail(
            "stage_transmit_required",
            "Exactly one stage must be the point at which the return is transmitted "
            "to the regulator.",
        )
    if transmit[0].decision_kind != "approve":
        raise _fail(
            "stage_transmit_not_approval",
            "Only an approval stage can be the point at which the return is transmitted.",
        )
    if transmit[0].seq != len(stages):
        raise _fail(
            "stage_transmit_not_last",
            "The transmitting stage is the last one: nothing reviews a return after it "
            "has reached the regulator.",
        )
    return stages


def default_stages() -> tuple[FilingStage, ...]:
    """Bernard's three, validated by the same rules a bank template is."""
    return validate_stages([stage.as_dict() for stage in DEFAULT_STAGES])


def transmit_seq(stages: Sequence[FilingStage]) -> int | None:
    return next((stage.seq for stage in stages if stage.transmit_on_approve), None)


def chain_complete(
    stages: Sequence[FilingStage], decisions: Sequence[DecisionFact], *, current_round: int
) -> bool:
    """Has every reviewing stage approved in the round that still stands?

    This is the question the transmission gate asks. It is answered from the
    decisions, not from a status, because a status is a projection and the
    chain is the source of truth.
    """
    reviewing = [stage for stage in stages if stage.decision_kind in {"review", "approve"}]
    if not reviewing:  # pragma: no cover - validate_stages forbids it
        return False
    return all(
        stage_decided(decisions, stage.seq, current_round=current_round) for stage in reviewing
    )


__all__ = [
    "DECISION_FOR_KIND",
    "DEFAULT_STAGES",
    "FILING_STAGE_DECISIONS",
    "FORWARD_DECISIONS",
    "DecisionFact",
    "FilingStage",
    "FilingStageKind",
    "StageChainError",
    "chain_complete",
    "checkers",
    "default_stages",
    "epoch_start",
    "makers",
    "reset_round_for",
    "stage_decided",
    "transmit_seq",
    "valid_forward_decisions",
    "validate_stages",
]
