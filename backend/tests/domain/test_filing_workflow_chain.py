"""The shared review-chain engine, and the filing chain's own shape (pure).

Two of these tests exist because the ICAAP shipped the rule and not the test.
``test_a_send_back_reopens_the_stage_it_returned_to`` pins the ``+ 1`` in
``reset_round_for``; ``test_the_same_amount_at_two_scales_is_one_digest_input``
pins D-067, the defect that expired a reviewer's approval because SQLAlchemy had
reloaded a numeric column. Both rules now live in one place, so one test each
covers both planes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.filing import review_digest
from app.domain.filing import workflow as filing
from app.domain.icaap import workflow as icaap
from app.domain.workflow import chain


def _fact(
    stage_seq: int,
    round_no: int,
    decision: str,
    *,
    return_to_seq: int | None = None,
    by: str = "officer",
) -> chain.DecisionFact:
    return chain.DecisionFact(
        stage_seq=stage_seq,
        round=round_no,
        decision=decision,
        return_to_seq=return_to_seq,
        review_digest="0" * 64,
        decided_by=by,
    )


# ---------------------------------------------------------------------------
# The default chain is Bernard's three, and it is DATA
# ---------------------------------------------------------------------------


def test_the_default_chain_is_preparer_approver_validator() -> None:
    stages = filing.default_stages()
    assert [stage.title for stage in stages] == ["Preparer", "Approver", "Validator"]
    assert [stage.decision_kind for stage in stages] == ["prepare", "approve", "approve"]
    # Exactly one stage transmits, and it is the last one.
    assert [stage.transmit_on_approve for stage in stages] == [False, False, True]
    assert filing.transmit_seq(stages) == len(stages)


def test_a_bank_may_name_a_fourth_stage_and_call_the_last_one_what_it_likes() -> None:
    """The point of the engine: three is the DEFAULT, not the only possibility."""
    stages = filing.validate_stages(
        [
            {"seq": 1, "stage_key": "preparation", "title": "Preparer", "decision_kind": "prepare"},
            {"seq": 2, "stage_key": "review", "title": "Risk Review", "decision_kind": "review"},
            {"seq": 3, "stage_key": "approval", "title": "Approver", "decision_kind": "approve"},
            {
                "seq": 4,
                "stage_key": "signoff",
                "title": "Compliance Sign-off",
                "decision_kind": "approve",
                "transmit_on_approve": True,
                "officer_titles": ["Head of Compliance"],
            },
        ]
    )
    assert len(stages) == 4
    assert stages[3].title == "Compliance Sign-off"
    assert stages[3].officer_titles == ("Head of Compliance",)


@pytest.mark.parametrize(
    ("stages", "code"),
    [
        pytest.param(
            [{"seq": 1, "stage_key": "approval", "title": "A", "decision_kind": "approve",
              "transmit_on_approve": True}],
            "stage_prepare_required",
            id="no-preparation",
        ),
        pytest.param(
            [
                {"seq": 1, "stage_key": "preparation", "title": "P", "decision_kind": "prepare"},
                {"seq": 2, "stage_key": "approval", "title": "A", "decision_kind": "approve"},
            ],
            "stage_transmit_required",
            id="nobody-transmits",
        ),
        pytest.param(
            [
                {"seq": 1, "stage_key": "preparation", "title": "P", "decision_kind": "prepare"},
                {"seq": 2, "stage_key": "approval", "title": "A", "decision_kind": "approve",
                 "transmit_on_approve": True},
                {"seq": 3, "stage_key": "second", "title": "B", "decision_kind": "approve"},
            ],
            "stage_transmit_not_last",
            id="transmits-in-the-middle",
        ),
        pytest.param(
            [
                {"seq": 1, "stage_key": "preparation", "title": "P", "decision_kind": "prepare"},
                {"seq": 2, "stage_key": "approval", "title": "A", "decision_kind": "review",
                 "transmit_on_approve": True},
            ],
            "stage_transmit_not_approval",
            id="a-review-cannot-transmit",
        ),
        pytest.param(
            [
                {"seq": 1, "stage_key": "preparation", "title": "P", "decision_kind": "prepare"},
                {"seq": 2, "stage_key": "board", "title": "B", "decision_kind": "attest",
                 "transmit_on_approve": True},
            ],
            "stage_decision_kind_invalid",
            id="the-board-is-an-icaap-concept",
        ),
    ],
)
def test_a_filing_chain_the_platform_cannot_run_is_refused(
    stages: list[dict[str, object]], code: str
) -> None:
    with pytest.raises(filing.StageChainError) as exc:
        filing.validate_stages(stages)
    assert exc.value.code == code


def test_the_icaap_chain_keeps_its_own_rules() -> None:
    """Lifting the engine must not let an ICAAP chain end anywhere but the Board."""
    with pytest.raises(icaap.StageChainError) as exc:
        icaap.validate_stages(
            [
                {"seq": 1, "stage_key": "preparation", "title": "P", "decision_kind": "prepare"},
                {"seq": 2, "stage_key": "approval", "title": "A", "decision_kind": "approve",
                 "freeze_on_approve": True},
            ]
        )
    assert exc.value.code == "stage_attest_required"


# ---------------------------------------------------------------------------
# Rounds: the send-back rule, including the off-by-one
# ---------------------------------------------------------------------------


def test_a_send_back_reopens_the_stage_it_returned_to() -> None:
    """``reset_round_for`` floors at the round AFTER the return, not the return's own.

    Using the return's own round would leave the decision it reopened still
    counting: the Approver's round-1 approval would satisfy a send-back taken in
    round 1, and "this stage must look again" would be true on the timeline and
    false in the rule.
    """
    decisions = [
        _fact(1, 1, "submitted", by="preparer"),
        _fact(2, 1, "approved", by="approver"),
        _fact(3, 1, "returned", return_to_seq=2, by="validator"),
    ]
    # Stage 2 was reopened: nothing it decided in round 1 still stands.
    assert chain.reset_round_for(decisions, 2) == 2
    assert chain.stage_decided(decisions, 2, current_round=2) is False
    # Stage 1 was NOT reopened; a return to stage 2 leaves it alone.
    assert chain.reset_round_for(decisions, 1) == 1


def test_a_send_back_to_the_preparer_reopens_everything_after_it() -> None:
    decisions = [
        _fact(1, 1, "submitted", by="preparer"),
        _fact(2, 1, "approved", by="approver"),
        _fact(3, 1, "returned", return_to_seq=1, by="validator"),
    ]
    assert chain.reset_round_for(decisions, 2) == 2
    assert chain.reset_round_for(decisions, 3) == 2


def test_whoever_prepared_this_round_is_a_maker_and_whoever_approved_is_a_checker() -> None:
    decisions = [
        _fact(1, 1, "submitted", by="preparer"),
        _fact(2, 1, "approved", by="approver"),
    ]
    assert chain.makers(decisions, content_authors={"preparer"}) == frozenset({"preparer"})
    # The Approver bars themselves from the Validator's stage — §3.3 layer 3.
    assert chain.checkers(decisions, current_round=1, exclude_seq=3) == frozenset({"approver"})
    # …but re-deciding their own stage after a send-back is the normal path.
    assert chain.checkers(decisions, current_round=1, exclude_seq=2) == frozenset()


def test_the_chain_is_complete_only_when_every_reviewing_stage_still_stands() -> None:
    stages = filing.default_stages()
    approved_once = [
        _fact(1, 1, "submitted"),
        _fact(2, 1, "approved", by="approver"),
    ]
    assert filing.chain_complete(stages, approved_once, current_round=1) is False
    both = [*approved_once, _fact(3, 1, "approved", by="validator")]
    assert filing.chain_complete(stages, both, current_round=1) is True
    # A send-back to the Approver un-completes it, even though stage 3 approved.
    reopened = [*both, _fact(3, 1, "returned", return_to_seq=2, by="validator")]
    assert filing.chain_complete(stages, reopened, current_round=2) is False


# ---------------------------------------------------------------------------
# D-067: the digest must not move when nothing has
# ---------------------------------------------------------------------------


def test_the_same_amount_at_two_scales_is_one_digest_input() -> None:
    assert chain.normalise_amount(Decimal("1")) == chain.normalise_amount(Decimal("1.000000"))
    assert chain.normalise_amount(Decimal("0.00")) == "0"
    assert chain.normalise_amount(Decimal("1500.50")) == "1500.5"
    assert chain.normalise_amount(None) is None


def test_a_reloaded_numeric_does_not_expire_a_filing_approval() -> None:
    """The D-067 shape, on the filing plane's own digest body.

    ``Decimal("1")`` and ``Decimal("1.000000")`` are the same amount; which one
    the session holds depends on whether the row has been reloaded from its
    numeric column. A digest that moved between them would expire a reviewer's
    approval because nothing had happened.
    """

    def body(value: Decimal) -> dict[str, object]:
        return review_digest.digest_body(
            return_code="BSD1",
            reporting_date=date(2026, 6, 30),
            basis="solo",
            version=1,
            content_digest="a" * 64,
            snapshot_sha256="b" * 64,
            register_state_digest=None,
            checks_passed=True,
            validation_report={
                "passed": True,
                "error_count": 0,
                "warning_count": value,
                "findings": [],
            },
        )

    assert body(Decimal("1")) == body(Decimal("1.000000"))
    # Nested values are normalised too: a figure one level down is exactly as
    # capable of arriving at a different scale.
    assert review_digest.normalise({"x": [Decimal("2.50")]}) == review_digest.normalise(
        {"x": [Decimal("2.5000")]}
    )


def test_a_different_figure_does_move_the_digest() -> None:
    """The digest must still expire an approval when the return actually changed."""

    def digest(version: int) -> str:
        return review_digest.compute(
            return_code="BSD1",
            reporting_date=date(2026, 6, 30),
            basis="solo",
            version=version,
            content_digest="a" * 64,
            snapshot_sha256="b" * 64,
            register_state_digest=None,
            checks_passed=True,
            validation_report={"passed": True, "error_count": 0, "warning_count": 0},
        )

    assert digest(1) != digest(2)


def test_the_regulators_outcomes_are_not_stage_decisions() -> None:
    """ORASS parity: ``rejected`` and ``declined`` are the REGULATOR's, not ours.

    Conflating them with an internal send-back would make the audit trail unable
    to distinguish "BoG rejected this" from "our Approver sent it back".
    """
    assert "rejected" not in filing.FILING_STAGE_DECISIONS
    assert "declined" not in filing.FILING_STAGE_DECISIONS
    assert "returned" in filing.FILING_STAGE_DECISIONS
