"""The ICAAP review chain: its shape, and what a decision round means (pure domain).

Two things live here, and both are pure functions over immutable inputs.

**The chain's shape.** A review chain is data — a bank whose Board Risk
Committee sits between the CRO and the CEO does not have the framework's
default chain — but it is not free-form. It starts with exactly one preparation
stage, ends with exactly one Board attestation, has at least one review or
approval in between, and names exactly one approval stage whose approval makes
the report ready to freeze. :func:`validate_stages` refuses anything else, and
the same function validates the framework defaults and a bank's own template,
so a template can never express a chain the platform cannot run.

**What a decision applies to.** A forward decision is taken on a ROUND and on a
review digest. When a stage sends the report back, the round increases and
every stage from the return target onwards has to decide again; stages earlier
than the target keep their decisions, because nothing they looked at changed.
``reset_round_for`` is that rule.

Since 2026-09-20 the round rules and the shared field parsing live in
``app/domain/workflow/chain.py``, because the BoG filing chain runs the same
engine and two implementations of "does this approval still stand" would drift.
The names below are re-exported unchanged, and what stays here is the part that
is genuinely about an ICAAP: a chain that ends at the Board and names the point
at which the report is ready to freeze.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.domain.workflow import chain

StageDecisionKind = Literal["prepare", "review", "approve", "attest"]

#: Decisions that move the chain forward. At most one may exist per
#: (cycle, round, stage) — the database's partial unique index enforces it.
FORWARD_DECISIONS: frozenset[str] = chain.FORWARD_DECISIONS
#: The forward decision a stage of each kind records.
DECISION_FOR_KIND: dict[str, str] = {
    "prepare": "submitted",
    "review": "reviewed",
    "approve": "approved",
}

StageChainError = chain.StageChainError
DecisionFact = chain.DecisionFact
epoch_start = chain.epoch_start
reset_round_for = chain.reset_round_for
valid_forward_decisions = chain.valid_forward_decisions
makers = chain.makers
checkers = chain.checkers

_fail = chain.fail


@dataclass(frozen=True)
class Stage:
    """One step of a review chain, independent of where it came from."""

    seq: int
    stage_key: str
    title: str
    decision_kind: StageDecisionKind
    officer_titles: tuple[str, ...]
    freeze_on_approve: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "stage_key": self.stage_key,
            "title": self.title,
            "decision_kind": self.decision_kind,
            "officer_titles": list(self.officer_titles),
            "freeze_on_approve": self.freeze_on_approve,
        }


def _one_stage(raw: object, index: int) -> Stage:
    if not isinstance(raw, dict):
        raise _fail("stage_invalid", f"Stage {index + 1} is not an object.")
    seq = chain.parse_seq(raw.get("seq"), index)
    stage_key = chain.parse_stage_key(raw.get("stage_key") or raw.get("key"), seq)
    title = chain.parse_stage_title(raw.get("title"), seq)
    kind = raw.get("decision_kind") or raw.get("decision")
    if kind not in DECISION_FOR_KIND and kind != "attest":
        raise _fail(
            "stage_decision_kind_invalid",
            f"Stage {seq}: a stage is a preparation, a review, an approval or the "
            "Board attestation.",
        )
    freeze = chain.parse_flag(
        raw.get("freeze_on_approve"),
        seq,
        code="stage_freeze_flag_invalid",
        field="freeze_on_approve",
    )
    return Stage(
        seq=seq,
        stage_key=stage_key,
        title=title,
        decision_kind=kind,  # pyright: ignore[reportArgumentType]
        officer_titles=chain.parse_officer_titles(
            raw.get("officer_titles") or raw.get("default_officer_titles"), seq
        ),
        freeze_on_approve=freeze,
    )


def validate_stages(raw_stages: Sequence[object]) -> tuple[Stage, ...]:
    """The one authority on whether a review chain is runnable.

    Every rule here exists because breaking it would make a cycle unable to
    finish: without a preparation stage nothing can be submitted, without an
    attestation stage the Board never signs, and without exactly one
    freeze-on-approve stage the platform cannot say when the report is ready to
    be sealed. The freeze stage must sit immediately before the attestation,
    because the document the Board signs is the one the freeze produced.
    """
    chain.require_chain_length(raw_stages)
    stages = tuple(_one_stage(raw, index) for index, raw in enumerate(raw_stages))
    chain.require_contiguous_unique(
        [stage.seq for stage in stages], [stage.stage_key for stage in stages]
    )

    prepare = [stage for stage in stages if stage.decision_kind == "prepare"]
    if len(prepare) != 1 or prepare[0].seq != 1:
        raise _fail(
            "stage_prepare_required",
            "A review chain starts with exactly one preparation stage.",
        )
    attest = [stage for stage in stages if stage.decision_kind == "attest"]
    if len(attest) != 1 or attest[0].seq != len(stages):
        raise _fail(
            "stage_attest_required",
            "A review chain ends with exactly one Board attestation stage.",
        )
    middle = [stage for stage in stages if stage.decision_kind in {"review", "approve"}]
    if not middle:
        raise _fail(
            "stage_review_required",
            "A review chain needs at least one review or approval between "
            "preparation and the Board.",
        )
    freeze = [stage for stage in stages if stage.freeze_on_approve]
    if len(freeze) != 1:
        raise _fail(
            "stage_freeze_required",
            "Exactly one approval stage must be the point at which the report is ready to freeze.",
        )
    if freeze[0].decision_kind != "approve":
        raise _fail(
            "stage_freeze_not_approval",
            "Only an approval stage can be the point at which the report is ready to freeze.",
        )
    if freeze[0].seq != attest[0].seq - 1:
        raise _fail(
            "stage_freeze_not_before_attest",
            "The freeze stage must be the one immediately before the Board attestation: "
            "the Board signs the document the freeze produced.",
        )
    if attest[0].freeze_on_approve:  # pragma: no cover - excluded by the checks above
        raise _fail(
            "stage_freeze_not_approval", "The Board attestation cannot also be the freeze stage."
        )
    return stages


def stages_from_framework(framework_stages: Iterable[Any]) -> tuple[Stage, ...]:
    """The framework's default chain, validated by the same rules as a template."""
    return validate_stages(
        [
            {
                "seq": stage.seq,
                "stage_key": stage.key,
                "title": stage.title,
                "decision_kind": stage.decision,
                "officer_titles": list(stage.default_officer_titles),
                "freeze_on_approve": stage.freeze_on_approve,
            }
            for stage in framework_stages
        ]
    )


__all__ = [
    "DECISION_FOR_KIND",
    "FORWARD_DECISIONS",
    "DecisionFact",
    "Stage",
    "StageChainError",
    "StageDecisionKind",
    "checkers",
    "epoch_start",
    "makers",
    "reset_round_for",
    "stages_from_framework",
    "valid_forward_decisions",
    "validate_stages",
]
