"""¶74 revisions and updates, the post-freeze send-back, and the ¶82 disclosure."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RegulatoryPackage
from app.models.icaap import IcaapCycle, IcaapStageDecision
from app.schemas.icaap import (
    IcaapCloneCreate,
    IcaapDisclosureDecision,
    IcaapDisclosurePut,
    IcaapDisclosureSubmit,
    IcaapFreezeCreate,
)
from app.services.icaap import clone, disclosure, post_freeze, workflow
from tests.services.icaap.p3_support import (
    access_for,
    make_user,
    return_payload,
)
from tests.services.icaap.test_p3_freeze import _build


def _detail(exc: HTTPException) -> dict[str, object]:
    detail = exc.detail
    assert isinstance(detail, dict)
    return detail


def _error(exc: HTTPException) -> str:
    return str(_detail(exc)["error_code"])


def _frozen(db: Session):  # noqa: ANN202
    access, reviewer, approver, cycle = _build(db)
    digest = workflow.get_stages(db, access, cycle.id).review_digest
    out = freeze_it(db, access, cycle.id, digest)
    return access, reviewer, approver, cycle, out


def freeze_it(db: Session, access, cycle_id, digest):  # noqa: ANN001, ANN201
    from app.services.icaap import freeze  # noqa: PLC0415

    return freeze.freeze_cycle(
        db, access, cycle_id, IcaapFreezeCreate(review_digest=digest, reason="Seal the report.")
    )


def _board_approve(db: Session, cycle_id) -> None:  # noqa: ANN001
    """Stand in for the Board signature, which WS-A's ceremony produces.

    The cycle transition itself is the family hook's and is exercised in its
    own test; the ¶74 and ¶82 paths only need a cycle that has been through the
    Board.
    """
    from app.db.base import utc_now  # noqa: PLC0415

    row = db.get(IcaapCycle, cycle_id)
    assert row is not None
    row.status = "board_approved"
    row.board_approved_at = utc_now()
    db.commit()


def _file(db: Session, cycle_id, package_id) -> None:  # noqa: ANN001
    """The filing has gone to the regulator: the year end is no longer open."""
    from app.db.base import utc_now  # noqa: PLC0415

    _board_approve(db, cycle_id)
    row = db.get(IcaapCycle, cycle_id)
    assert row is not None
    row.status = "submitted"
    row.submitted_at = utc_now()
    package = db.get(RegulatoryPackage, package_id)
    assert package is not None
    package.status = "submitted"
    db.commit()


class TestRevision:
    def test_a_revision_carries_the_text_and_keeps_the_assessment_date(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, out = _frozen(db)
        _file(db, cycle.id, out.package.id)
        revised = clone.clone_cycle(
            db,
            access,
            cycle.id,
            IcaapCloneCreate(mode="revision", reason="Correcting the concentration figure."),
        )
        assert revised.id != cycle.id
        assert revised.as_of_date == cycle.as_of_date
        assert revised.status == "draft"
        assert revised.supersedes_cycle_id == cycle.id
        # Every section arrives committed, so the revision starts from what was
        # filed rather than from a blank page.
        assert all(section.committed_version_no == 1 for section in revised.sections)

    def test_a_revision_may_not_move_the_assessment_date(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, out = _frozen(db)
        _file(db, cycle.id, out.package.id)
        with pytest.raises(HTTPException) as caught:
            clone.clone_cycle(
                db,
                access,
                cycle.id,
                IcaapCloneCreate(
                    mode="revision",
                    as_of_date=date(2024, 12, 31),
                    reason="Trying to move the date.",
                ),
            )
        assert _error(caught.value) == "revision_keeps_as_of"

    def test_an_approved_but_unfiled_icaap_is_sent_back_rather_than_revised(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """Two open assessments for one year end is the state the index prevents."""
        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        _board_approve(db, cycle.id)
        with pytest.raises(HTTPException) as caught:
            clone.clone_cycle(
                db,
                access,
                cycle.id,
                IcaapCloneCreate(mode="revision", reason="Correcting before filing."),
            )
        assert _error(caught.value) == "revision_requires_a_filing"

    def test_a_cycle_the_board_has_not_approved_cannot_be_revised(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        with pytest.raises(HTTPException) as caught:
            clone.clone_cycle(
                db, access, cycle.id, IcaapCloneCreate(mode="revision", reason="Too early.")
            )
        assert _error(caught.value) == "cycle_not_cloneable"

    def test_an_acknowledged_filing_needs_a_granted_resubmission(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """A return the regulator has acknowledged is final until they grant a redo."""
        from app.db.base import utc_now  # noqa: PLC0415

        db = canonical_book
        access, _r, _a, cycle, out = _frozen(db)
        row = db.get(IcaapCycle, cycle.id)
        assert row is not None
        row.status = "acknowledged"
        row.board_approved_at = utc_now()
        row.submitted_at = utc_now()
        row.acknowledged_at = utc_now()
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        package.status = "acknowledged"
        db.commit()
        with pytest.raises(HTTPException) as caught:
            clone.clone_cycle(
                db,
                access,
                cycle.id,
                IcaapCloneCreate(mode="revision", reason="Correcting an acknowledged filing."),
            )
        assert _error(caught.value) == "resubmission_required"


class TestUpdate:
    def test_a_material_change_update_gets_its_own_date_and_never_an_invented_deadline(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        _board_approve(db, cycle.id)
        updated = clone.clone_cycle(
            db,
            access,
            cycle.id,
            IcaapCloneCreate(
                mode="update",
                cycle_kind="material_change",
                as_of_date=date(2026, 3, 31),
                change_trigger="other",
                change_description="A material acquisition changed the risk profile.",
                reason="¶74 update after a material change.",
            ),
        )
        assert updated.cycle_kind == "material_change"
        assert updated.as_of_date == date(2026, 3, 31)
        # REG-ICAAP-073: the Guideline says "in a timely manner" and names no date.
        assert updated.due_date is None
        assert updated.due_date_basis == "timely"
        assert updated.supersedes_cycle_id is None

    def test_a_supervisory_request_needs_the_supervisors_own_date(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        _board_approve(db, cycle.id)
        with pytest.raises(HTTPException) as caught:
            clone.clone_cycle(
                db,
                access,
                cycle.id,
                IcaapCloneCreate(
                    mode="update",
                    cycle_kind="regulator_request",
                    as_of_date=date(2026, 3, 31),
                    regulator_request_ref="BOG/BSD/2026/17",
                    reason="The supervisor asked for an updated assessment.",
                ),
            )
        assert _error(caught.value) == "requested_due_date_required"
        granted = clone.clone_cycle(
            db,
            access,
            cycle.id,
            IcaapCloneCreate(
                mode="update",
                cycle_kind="regulator_request",
                as_of_date=date(2026, 3, 31),
                regulator_request_ref="BOG/BSD/2026/17",
                requested_due_date=date(2026, 6, 30),
                reason="The supervisor asked for an updated assessment.",
            ),
        )
        assert granted.due_date == date(2026, 6, 30)
        assert granted.due_date_basis == "regulator_set"


class TestPostFreezeReturn:
    def test_a_send_back_unlinks_the_package_and_reopens_the_review(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, reviewer, _a, cycle, out = _frozen(db)
        post_freeze.return_cycle(
            db,
            reviewer,
            cycle.id,
            return_payload(
                db,
                reviewer,
                cycle.id,
                return_to_seq=1,
                reason="The Board asked for the buffer to be explained.",
            ),
        )
        row = db.get(IcaapCycle, cycle.id)
        assert row is not None
        assert row.status == "returned"
        assert row.package_id is None
        assert row.frozen_at is None
        assert row.round == 2
        # The package itself survives as what was frozen on that date.
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        recorded = db.scalars(
            select(IcaapStageDecision).where(
                IcaapStageDecision.cycle_id == cycle.id,
                IcaapStageDecision.decision == "returned",
            )
        ).all()
        assert [entry.package_id for entry in recorded] == [out.package.id]

    def test_a_send_back_past_the_freeze_stage_is_refused(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, reviewer, _a, cycle, _out = _frozen(db)
        with pytest.raises(HTTPException) as caught:
            post_freeze.return_cycle(
                db,
                reviewer,
                cycle.id,
                return_payload(
                    db,
                    reviewer,
                    cycle.id,
                    return_to_seq=4,
                    reason="Trying to reopen the Board stage.",
                ),
            )
        assert _error(caught.value) == "return_target_invalid"

    def test_an_unfrozen_cycle_has_nothing_to_send_back(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, reviewer, _a, cycle = _build(db)
        with pytest.raises(HTTPException) as caught:
            post_freeze.return_cycle(
                db,
                reviewer,
                cycle.id,
                return_payload(
                    db, reviewer, cycle.id, return_to_seq=1, reason="Nothing to undo."
                ),
            )
        assert _error(caught.value) == "cycle_not_returnable"


    def test_the_officer_who_froze_it_cannot_be_the_one_who_unseals_it(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """Four eyes on the more destructive send-back (independent audit F1).

        This was the ONLY ICAAP decision dependency with no ``conditions``: one
        holder of an APPROVER bundle could void the CRO's, the CEO's and the
        Board's signatures on a ``board_approved`` cycle in a single call. The
        rule is the mirror of D-030 — the officer who reviewed a round may not
        freeze it, and the officer who froze it may not unseal it.
        """
        db = canonical_book
        access, reviewer, _a, cycle, _out = _frozen(db)

        with pytest.raises(HTTPException) as caught:
            post_freeze.return_cycle(
                db,
                access,
                cycle.id,
                return_payload(
                    db, access, cycle.id, return_to_seq=1, reason="Undo my own seal."
                ),
            )
        assert _error(caught.value) == "maker_checker"
        assert "froze this ICAAP" in str(_detail(caught.value)["message"])

        # Nothing moved: the refusal is a refusal, not a partial unseal.
        row = db.get(IcaapCycle, cycle.id)
        assert row is not None
        assert row.status == "frozen"
        assert row.package_id is not None

        # ...and the same answer reaches the authorization evaluator, so the
        # refusal lands in the binding trace with its reason rather than being
        # a check buried in the service.
        blocked = post_freeze.return_authority(db, access.ctx, access.bank, cycle.id)
        assert [check.passed for check in blocked] == [False]
        allowed = post_freeze.return_authority(db, reviewer.ctx, reviewer.bank, cycle.id)
        assert [check.passed for check in allowed] == [True]

    def test_a_send_back_from_a_stale_page_is_refused(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """The round and digest checks its in-review sibling has always had."""
        db = canonical_book
        _access, reviewer, _a, cycle, _out = _frozen(db)
        current = return_payload(
            db, reviewer, cycle.id, return_to_seq=1, reason="The Board asked for a correction."
        )

        stale_round = current.model_copy(update={"round": current.round + 1})
        with pytest.raises(HTTPException) as caught:
            post_freeze.return_cycle(db, reviewer, cycle.id, stale_round)
        assert _error(caught.value) == "stage_round_moved"

        stale_digest = current.model_copy(update={"review_digest": "0" * 64})
        with pytest.raises(HTTPException) as caught:
            post_freeze.return_cycle(db, reviewer, cycle.id, stale_digest)
        assert _error(caught.value) == "review_basis_changed"

        row = db.get(IcaapCycle, cycle.id)
        assert row is not None
        assert row.status == "frozen"

        # Non-vacuity: the SAME call with the current basis succeeds.
        post_freeze.return_cycle(db, reviewer, cycle.id, current)
        db.refresh(row)
        assert row.status == "returned"


class TestDisclosure:
    def test_nothing_is_public_until_the_bank_chooses_it(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        _board_approve(db, cycle.id)
        view = disclosure.get_disclosure(db, access, cycle.id)
        assert view.available is True
        assert all(section.selected is False for section in view.sections)
        assert {section.key for section in view.sections if section.selectable} == {
            "executive_summary",
            "capital_adequacy",
        }

    def test_a_section_the_framework_withholds_cannot_be_published(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        _board_approve(db, cycle.id)
        with pytest.raises(HTTPException) as caught:
            disclosure.put_disclosure(
                db,
                access,
                cycle.id,
                IcaapDisclosurePut(
                    selected_section_keys=["supervisory_measures"],
                    reason="Publishing the supervisory section.",
                ),
            )
        assert _error(caught.value) == "section_not_publishable"

    def test_a_disclosure_before_the_board_has_approved_is_unavailable(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        view = disclosure.get_disclosure(db, access, cycle.id)
        assert view.available is False
        assert view.unavailable_reason is not None

    def test_whoever_chose_what_to_publish_cannot_approve_publishing_it(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        _board_approve(db, cycle.id)
        disclosure.put_disclosure(
            db,
            access,
            cycle.id,
            IcaapDisclosurePut(
                selected_section_keys=["executive_summary"],
                reason="Publishing the summary of the outcome.",
            ),
        )
        disclosure.submit_disclosure(
            db, access, cycle.id, IcaapDisclosureSubmit(reason="Ready for approval.")
        )
        with pytest.raises(HTTPException) as caught:
            disclosure.decide_disclosure(
                db,
                access,
                cycle.id,
                IcaapDisclosureDecision(decision="approved", reason="Approving my own selection."),
            )
        assert _error(caught.value) == "maker_checker"

    def test_approval_mints_the_disclosure_return_from_the_filed_report(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle, out = _frozen(db)
        _board_approve(db, cycle.id)
        disclosure.put_disclosure(
            db,
            access,
            cycle.id,
            IcaapDisclosurePut(
                selected_section_keys=["executive_summary", "capital_adequacy"],
                reason="Publishing the outcome and the capital discussion.",
            ),
        )
        disclosure.submit_disclosure(
            db, access, cycle.id, IcaapDisclosureSubmit(reason="Ready for approval.")
        )
        checker = access_for(db, make_user(db, email="pub@example.com", name="Pub"))
        decided = disclosure.decide_disclosure(
            db,
            checker,
            cycle.id,
            IcaapDisclosureDecision(decision="approved", reason="The selection is appropriate."),
        )
        assert decided.status == "approved"
        assert decided.package_id is not None
        package = db.get(RegulatoryPackage, decided.package_id)
        assert package is not None
        assert package.return_code == "ICAAP-DISCLOSURE"
        block = package.snapshot["metadata"]["icaap_disclosure"]
        assert block["source_package_id"] == str(out.package.id)
        assert block["source_content_digest"] == out.package.content_digest
        assert sorted(block["selected_section_keys"]) == [
            "capital_adequacy",
            "executive_summary",
        ]
        # Only the chosen sections travel.
        assert {entry["code"] for entry in package.snapshot["sections"]} == {
            "s_executive_summary",
            "s_capital_adequacy",
        }


class TestExaminerReadsTheDisclosure:
    def test_a_supervisor_never_reads_a_disclosure_the_board_has_not_decided(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """Audit S-7, second instance: the examiner branch and unapproved rows.

        A draft disclosure is the bank's proposal about what it will publish and
        what it will WITHHOLD, before anyone has approved it. It sits on a
        frozen cycle, so ``get_cycle_or_404`` admits the supervisor — and the
        row filter is then the only thing between them and a working paper.
        Once approved it is a decision, and a supervisor reads decisions.
        """
        from dataclasses import replace  # noqa: PLC0415

        db = canonical_book
        access, _r, _a, cycle, _out = _frozen(db)
        _board_approve(db, cycle.id)
        drafted = disclosure.put_disclosure(
            db,
            access,
            cycle.id,
            IcaapDisclosurePut(
                selected_section_keys=["executive_summary"],
                reason="Publishing the summary of the outcome.",
            ),
        )
        assert drafted.status == "draft"
        examiner = replace(access, examiner=True)

        hidden = disclosure.get_disclosure(db, examiner, cycle.id)
        assert hidden.id is None
        assert not any(section.selected for section in hidden.sections)
        assert hidden.withheld == []
        # The bank's own reader still sees its draft.
        assert disclosure.get_disclosure(db, access, cycle.id).id == drafted.id

        disclosure.submit_disclosure(
            db, access, cycle.id, IcaapDisclosureSubmit(reason="Ready for approval.")
        )
        # Submitted is not decided either.
        assert disclosure.get_disclosure(db, examiner, cycle.id).id is None

        checker = access_for(db, make_user(db, email="examiner-pub@example.com", name="Pub"))
        decided = disclosure.decide_disclosure(
            db,
            checker,
            cycle.id,
            IcaapDisclosureDecision(decision="approved", reason="The selection is appropriate."),
        )
        assert disclosure.get_disclosure(db, examiner, cycle.id).id == decided.id


class TestSupervisoryRedaction:
    def test_a_supervisory_figure_can_never_be_published(self) -> None:
        """Pure: no code path emits a never-public block into a disclosure."""
        from app.domain.icaap import disclosure as domain  # noqa: PLC0415

        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "The add-on is "},
                        {
                            "type": "factRef",
                            "attrs": {"blockId": "block-1", "factKey": "supervisory_addon_total"},
                        },
                    ],
                },
                {"type": "dataBlock", "attrs": {"blockId": "block-1"}},
            ],
        }
        result = domain.redact_section(
            "capital_adequacy",
            doc,
            never_public={"block-1": ("supervisory_addons", "supervisory_addons")},
        )
        assert len(result.withheld) == 2
        assert not domain.contains_never_public(result.doc, ["block-1"])
        assert domain.WITHHELD_TEXT in str(result.doc)
