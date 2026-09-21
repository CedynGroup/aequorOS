"""The review chain: order, rounds, send-backs, and who may decide.

Each test here answers a way an approval record can be made worthless — a
decision taken on text that has since changed, a decision taken by the person
who wrote it, a send-back that quietly leaves later stages still "approved".
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.domain.icaap import workflow as domain
from app.schemas.icaap import (
    IcaapSectionCommit,
    IcaapSectionWorkingSave,
    IcaapStageDecisionCreate,
    IcaapSubmitForReview,
)
from app.services.icaap import cycles, sections, workflow
from tests.services.icaap.p3_support import (
    access_for,
    annual_payload,
    answer_every_requirement,
    decide,
    make_user,
    submit,
    write_every_section,
)


def _detail(exc: HTTPException) -> dict[str, object]:
    detail = exc.detail
    assert isinstance(detail, dict)
    return detail


def _error(exc: HTTPException) -> str:
    return str(_detail(exc)["error_code"])


@pytest.fixture
def prepared(canonical_book: Session, extra_frameworks: None):  # noqa: ANN201
    """A cycle with committed text in every section, ready to submit."""
    db = canonical_book
    access = access_for(db)
    cycle = cycles.create_cycle(db, access, annual_payload())
    write_every_section(db, access, cycle.id)
    answer_every_requirement(db, access, cycle.id)
    return db, access, cycle


class TestSubmission:
    def test_the_chain_is_pinned_at_the_first_submission(self, prepared) -> None:
        db, access, cycle = prepared
        before = workflow.get_stages(db, access, cycle.id)
        assert [stage.stage_key for stage in before.stages] == [
            "preparation",
            "cro_review",
            "brc_review",
            "board_approval",
        ]
        submit(db, access, cycle.id)
        after = workflow.get_stages(db, access, cycle.id)
        assert after.status == "in_review"
        assert after.current_stage_seq == 2
        assert after.source == "framework_default"

    def test_uncommitted_text_is_refused_because_reviewers_cannot_review_it(self, prepared) -> None:
        db, access, cycle = prepared
        sections.save_working(
            db,
            access,
            cycle.id,
            "executive_summary",
            IcaapSectionWorkingSave(
                doc={
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Draft edit."}]}
                    ],
                },
                base_rev=1,
            ),
        )
        digest = workflow.get_stages(db, access, cycle.id).review_digest
        with pytest.raises(HTTPException) as caught:
            workflow.submit_for_review(
                db, access, cycle.id, IcaapSubmitForReview(review_digest=digest)
            )
        assert _error(caught.value) == "uncommitted_changes"

    def test_a_stale_digest_is_refused(self, prepared) -> None:
        db, access, cycle = prepared
        with pytest.raises(HTTPException) as caught:
            workflow.submit_for_review(
                db, access, cycle.id, IcaapSubmitForReview(review_digest="0" * 64)
            )
        assert _error(caught.value) == "review_basis_changed"


class TestStageOrder:
    def test_a_stage_out_of_turn_is_refused(self, prepared) -> None:
        db, access, cycle = prepared
        submit(db, access, cycle.id)
        approver = access_for(db, make_user(db, email="a@example.com", name="A"))
        with pytest.raises(HTTPException) as caught:
            decide(db, approver, cycle.id, 3, "approved")
        assert _error(caught.value) == "stage_moved"

    def test_the_board_stage_refuses_a_decision_because_it_is_a_signature(self, prepared) -> None:
        db, access, cycle = prepared
        submit(db, access, cycle.id)
        reviewer = access_for(
            db, make_user(db, email="c@example.com", name="C", job_title="Chief Risk Officer")
        )
        decide(db, reviewer, cycle.id, 2, "reviewed")
        approver = access_for(db, make_user(db, email="b@example.com", name="B"))
        decide(db, approver, cycle.id, 3, "approved")
        board = access_for(db, make_user(db, email="d@example.com", name="D"))
        with pytest.raises(HTTPException) as caught:
            decide(db, board, cycle.id, 4, "approved")
        assert _error(caught.value) == "stage_decided_by_signature"

    def test_a_review_stage_records_reviewed_and_not_approved(self, prepared) -> None:
        db, access, cycle = prepared
        submit(db, access, cycle.id)
        reviewer = access_for(
            db, make_user(db, email="c@example.com", name="C", job_title="Chief Risk Officer")
        )
        with pytest.raises(HTTPException) as caught:
            decide(db, reviewer, cycle.id, 2, "approved")
        assert _error(caught.value) == "stage_decision_mismatch"


class TestMakerChecker:
    def test_the_preparer_cannot_review_their_own_report(self, prepared) -> None:
        db, access, cycle = prepared
        submit(db, access, cycle.id)
        with pytest.raises(HTTPException) as caught:
            decide(db, access, cycle.id, 2, "reviewed")
        assert _error(caught.value) == "maker_checker"
        assert "wrote part of this report" in str(_detail(caught.value)["message"])

    def test_an_earlier_reviewer_cannot_also_take_the_approval(self, prepared) -> None:
        db, access, cycle = prepared
        submit(db, access, cycle.id)
        reviewer = access_for(
            db, make_user(db, email="c@example.com", name="C", job_title="Chief Risk Officer")
        )
        decide(db, reviewer, cycle.id, 2, "reviewed")
        with pytest.raises(HTTPException) as caught:
            decide(db, reviewer, cycle.id, 3, "approved")
        assert _error(caught.value) == "maker_checker"
        assert "already decided an earlier stage" in str(_detail(caught.value)["message"])

    def test_the_viewer_projection_gives_the_same_answer_as_the_service(self, prepared) -> None:
        db, access, cycle = prepared
        submit(db, access, cycle.id)
        view = workflow.get_stages(db, access, cycle.id)
        assert view.viewer.can_decide is False
        assert view.viewer.blocked_reason is not None
        reviewer = access_for(
            db, make_user(db, email="c@example.com", name="C", job_title="Chief Risk Officer")
        )
        assert workflow.get_stages(db, reviewer, cycle.id).viewer.can_decide is True


class TestOfficerTitle:
    def test_a_stage_with_named_titles_refuses_an_officer_who_does_not_hold_one(
        self, prepared
    ) -> None:
        db, access, cycle = prepared
        submit(db, access, cycle.id)
        wrong = access_for(
            db, make_user(db, email="t@example.com", name="T", job_title="Head of Treasury")
        )
        with pytest.raises(HTTPException) as caught:
            decide(db, wrong, cycle.id, 2, "reviewed")
        assert _error(caught.value) == "officer_title_mismatch"

    def test_a_stage_with_no_named_titles_accepts_any_authorised_officer(self, prepared) -> None:
        db, access, cycle = prepared
        submit(db, access, cycle.id)
        reviewer = access_for(
            db, make_user(db, email="c@example.com", name="C", job_title="Chief Risk Officer")
        )
        decide(db, reviewer, cycle.id, 2, "reviewed")
        untitled = access_for(db, make_user(db, email="u@example.com", name="U"))
        decide(db, untitled, cycle.id, 3, "approved")
        assert workflow.get_stages(db, access, cycle.id).awaiting_freeze is True


class TestReturns:
    def test_a_return_to_preparation_bumps_the_round_and_unlocks_the_text(self, prepared) -> None:
        db, access, cycle = prepared
        submit(db, access, cycle.id)
        reviewer = access_for(
            db, make_user(db, email="c@example.com", name="C", job_title="Chief Risk Officer")
        )
        decide(
            db,
            reviewer,
            cycle.id,
            2,
            "returned",
            return_to_seq=1,
            comment="The capital section does not explain the buffer.",
        )
        view = workflow.get_stages(db, access, cycle.id)
        assert view.status == "returned"
        assert view.round == 2
        assert view.current_stage_seq == 1
        # The text is editable again, which is the point of returning to stage 1.
        sections.save_working(
            db,
            access,
            cycle.id,
            "capital_adequacy",
            IcaapSectionWorkingSave(
                doc={
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "The buffer is explained."}],
                        }
                    ],
                },
                base_rev=1,
            ),
        )
        sections.commit_version(
            db,
            access,
            cycle.id,
            "capital_adequacy",
            IcaapSectionCommit(base_rev=2, note="Answering the review"),
        )
        submit(db, access, cycle.id)
        assert workflow.get_stages(db, access, cycle.id).current_stage_seq == 2

    def test_a_return_with_no_comment_is_refused(self, prepared) -> None:
        db, access, cycle = prepared
        submit(db, access, cycle.id)
        reviewer = access_for(
            db, make_user(db, email="c@example.com", name="C", job_title="Chief Risk Officer")
        )
        stages = workflow.get_stages(db, reviewer, cycle.id)
        with pytest.raises(HTTPException) as caught:
            workflow.decide_stage(
                db,
                reviewer,
                cycle.id,
                2,
                IcaapStageDecisionCreate(
                    decision="returned",
                    round=stages.round,
                    review_digest=stages.review_digest,
                    return_to_seq=1,
                    comment=None,
                ),
            )
        assert _error(caught.value) == "return_comment_required"

    def test_a_return_to_a_later_stage_keeps_the_earlier_decisions(self, prepared) -> None:
        """Returning to stage k reopens k onwards, and nothing before it."""
        db, access, cycle = prepared
        submit(db, access, cycle.id)
        reviewer = access_for(
            db, make_user(db, email="c@example.com", name="C", job_title="Chief Risk Officer")
        )
        decide(db, reviewer, cycle.id, 2, "reviewed")
        approver = access_for(db, make_user(db, email="b@example.com", name="B"))
        decide(
            db,
            approver,
            cycle.id,
            3,
            "returned",
            return_to_seq=2,
            comment="The committee wants the CRO to look again at the concentration.",
        )
        view = workflow.get_stages(db, access, cycle.id)
        # Still under review: nothing the preparer wrote was reopened.
        assert view.status == "in_review"
        assert view.round == 2
        assert view.current_stage_seq == 2
        reviewed = next(stage for stage in view.stages if stage.seq == 2)
        assert [
            entry.still_stands for entry in reviewed.decisions if entry.decision == "reviewed"
        ] == [False]

    def test_the_round_rule_is_the_domains_and_is_not_restated(self) -> None:
        facts = (
            domain.DecisionFact(1, 1, "submitted", None, "a" * 64, "u1"),
            domain.DecisionFact(2, 1, "reviewed", None, "a" * 64, "u2"),
            domain.DecisionFact(3, 1, "returned", 2, "a" * 64, "u3"),
        )
        assert domain.reset_round_for(facts, 1) == 1
        # The return above reopened stage 2 onwards, so a round-1 review there
        # no longer counts while stage 1's submission still does.
        assert domain.reset_round_for(facts, 2) == 2
        assert domain.reset_round_for(facts, 3) == 2
        later = (*facts, domain.DecisionFact(3, 2, "returned", 1, "a" * 64, "u3"))
        assert domain.reset_round_for(later, 1) == 3
