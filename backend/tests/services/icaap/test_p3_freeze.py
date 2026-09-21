"""Freezing: every refusal by name, who may freeze, and that it is atomic.

The preflight is served to the preparer before they press anything, so each
refusal has to be individually reachable and individually named. The
transaction test is the one that matters most: a failure part-way through must
leave no package, no superseded predecessor, and the cycle still in review.
"""

from __future__ import annotations

import copy
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RegulatoryPackage
from app.models.icaap import IcaapCycle, IcaapStageDecision
from app.schemas.icaap import IcaapFreezeCreate
from app.services.icaap import cycles, freeze, guards, workflow
from tests.services.icaap.p3_support import (
    AS_OF,
    access_for,
    annual_payload,
    answer_every_requirement,
    attach,
    decide,
    govern_first_as_of,
    make_p2_ready,
    make_user,
    seal_capital_run,
    submit,
    write_every_section,
)


def _codes(report) -> set[str]:  # noqa: ANN001
    return {item.code for item in report.items if item.severity == "blocking"}


def _detail(exc: HTTPException) -> dict[str, object]:
    detail = exc.detail
    assert isinstance(detail, dict)
    return detail


def _error(exc: HTTPException) -> str:
    return str(_detail(exc)["error_code"])


def _build(db: Session, *, attachments: bool = True, effective: bool = True):  # noqa: ANN202
    """A cycle that has passed its approval stage and is awaiting freeze."""
    access = access_for(db)
    if effective:
        govern_first_as_of(db, AS_OF)
    seal_capital_run(db, access)
    cycle = cycles.create_cycle(db, access, annual_payload())
    write_every_section(db, access, cycle.id)
    answer_every_requirement(db, access, cycle.id)
    make_p2_ready(db, access, cycle.id)
    if attachments:
        attach(db, access, cycle.id, "senior_management_report", sha="a" * 64)
    submit(db, access, cycle.id)
    reviewer = access_for(
        db, make_user(db, email="cro@example.com", name="Cro", job_title="Chief Risk Officer")
    )
    decide(db, reviewer, cycle.id, 2, "reviewed")
    approver = access_for(db, make_user(db, email="brc@example.com", name="Brc"))
    decide(db, approver, cycle.id, 3, "approved")
    return access, reviewer, approver, cycle


class TestPreflight:
    def test_a_complete_cycle_is_ready(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _reviewer, _approver, cycle = _build(db)
        report = freeze.freeze_preflight(db, access, guards.get_cycle_or_404(db, access, cycle.id))
        assert report.ready, sorted(_codes(report))

    def test_a_missing_freeze_gate_document_is_named(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle = _build(db, attachments=False)
        report = freeze.freeze_preflight(db, access, guards.get_cycle_or_404(db, access, cycle.id))
        assert "attachment_missing" in _codes(report)
        assert not report.ready

    def test_a_return_before_the_governed_commencement_date_is_refused(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """D-032: the date is a console row, so the refusal names it, not a literal."""
        db = canonical_book
        access, _r, _a, cycle = _build(db)
        govern_first_as_of(db, date(2099, 12, 31))
        report = freeze.freeze_preflight(db, access, guards.get_cycle_or_404(db, access, cycle.id))
        assert "return_not_yet_effective" in _codes(report)
        item = next(entry for entry in report.items if entry.code == "return_not_yet_effective")
        assert "2099-12-31" in item.message

    def test_a_cycle_the_approval_stage_has_not_reached_cannot_be_frozen(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access = access_for(db)
        govern_first_as_of(db, AS_OF)
        cycle = cycles.create_cycle(db, access, annual_payload())
        write_every_section(db, access, cycle.id)
        submit(db, access, cycle.id)
        report = freeze.freeze_preflight(db, access, guards.get_cycle_or_404(db, access, cycle.id))
        assert "cycle_not_awaiting_freeze" in _codes(report)

    def test_a_rehearsal_says_it_is_never_filed_without_blocking_the_freeze(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """D-068: a rehearsal runs the FULL lifecycle, freeze included.

        This previously asserted the notice was ``blocking``, which made every
        rehearsal unfreezable — the behaviour the ruling rejected, and the one
        migration 202609190062 exists to permit. The guarantee the name refers
        to is real but lives elsewhere: the package is marked ``is_rehearsal``,
        three CHECK constraints stop it reaching a filed state, and submission
        refuses it. The preflight's job is to SAY so, not to prevent the dry
        run the founder asked for.
        """
        db = canonical_book
        access = access_for(db)
        cycle = cycles.create_cycle(
            db, access, annual_payload(cycle_kind="rehearsal", reason="A dry run before filing.")
        )
        report = freeze.freeze_preflight(db, access, guards.get_cycle_or_404(db, access, cycle.id))
        notice = next(item for item in report.items if item.code == "rehearsal_not_fileable")
        assert notice.severity == "info"
        assert "never" in notice.message and "filed" in notice.message
        assert "rehearsal_not_fileable" not in _codes(report)

    def test_a_framework_awaiting_the_regulators_text_is_refused(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """D-006: 15 of Ghana's 17 sections are still ``pending_primary_text``.

        FY2026, because the Ghana instrument's first reporting date is
        2026-12-31 — the same fact that makes it unfileable for FY2025.
        """
        db = canonical_book
        access = access_for(db)
        cycle = cycles.create_cycle(
            db,
            access,
            annual_payload(
                fiscal_year=2026,
                framework_code="bog_icaap",
                framework_version="2026.02-ed.1",
            ),
        )
        report = freeze.freeze_preflight(db, access, guards.get_cycle_or_404(db, access, cycle.id))
        assert "framework_pending_primary_text" in _codes(report)

    def test_a_framework_the_platform_cannot_file_is_refused(
        self, canonical_book: Session, extra_frameworks: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reference-only instrument: published as data, with no return to carry it."""
        import dataclasses  # noqa: PLC0415

        db = canonical_book
        access, _r, _a, cycle = _build(db)
        row = guards.get_cycle_or_404(db, access, cycle.id)
        original = guards.framework_for(row)[0]
        monkeypatch.setattr(
            guards,
            "framework_for",
            lambda _cycle: (dataclasses.replace(original, filing=None), True),
        )
        report = freeze.freeze_preflight(db, access, row)
        assert "filing_not_available_for_framework" in _codes(report)

    def test_a_report_that_changed_after_the_approval_cannot_be_frozen(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle = _build(db)
        row = guards.get_cycle_or_404(db, access, cycle.id)
        report = freeze.freeze_preflight(db, access, row, review_digest="0" * 64)
        assert "review_basis_changed" in _codes(report)

    def test_a_prior_version_with_the_regulator_blocks_another_freeze(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle = _build(db)
        row = guards.get_cycle_or_404(db, access, cycle.id)
        digest = workflow.get_stages(db, access, cycle.id).review_digest
        frozen = freeze.freeze_cycle(
            db, access, cycle.id, IcaapFreezeCreate(review_digest=digest, reason="Seal the report.")
        )
        package = db.get(RegulatoryPackage, frozen.package.id)
        assert package is not None
        package.status = "submitted"
        db.commit()
        report = freeze.freeze_preflight(db, access, row)
        assert "prior_filing_with_regulator" in _codes(report)


class TestWhoMayFreeze:
    def test_a_reviewer_cannot_freeze_because_it_would_cost_them_their_signature(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """D-030 / DV-011: the freezer becomes ``generated_by`` and cannot approve."""
        db = canonical_book
        access, reviewer, approver, cycle = _build(db)
        digest = workflow.get_stages(db, access, cycle.id).review_digest
        for actor in (reviewer, approver):
            with pytest.raises(HTTPException) as caught:
                freeze.freeze_cycle(
                    db,
                    actor,
                    cycle.id,
                    IcaapFreezeCreate(review_digest=digest, reason="Seal the report."),
                )
            assert _error(caught.value) == "maker_checker"

    def test_the_viewer_projection_agrees_with_the_service(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, reviewer, _approver, cycle = _build(db)
        assert workflow.get_stages(db, access, cycle.id).viewer.can_freeze is True
        assert workflow.get_stages(db, reviewer, cycle.id).viewer.can_freeze is False


class TestTheTransaction:
    def test_a_freeze_seals_the_cycle_and_mints_one_package(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle = _build(db)
        digest = workflow.get_stages(db, access, cycle.id).review_digest
        out = freeze.freeze_cycle(
            db, access, cycle.id, IcaapFreezeCreate(review_digest=digest, reason="Seal the report.")
        )
        assert out.package.return_code == "ICAAP-REPORT"
        assert out.package.version == 1
        assert out.cycle.status == "frozen"
        row = db.get(IcaapCycle, cycle.id)
        assert row is not None
        assert row.package_id == out.package.id
        assert row.frozen_at is not None
        recorded = db.scalar(
            select(IcaapStageDecision).where(
                IcaapStageDecision.cycle_id == cycle.id,
                IcaapStageDecision.decision == "frozen",
            )
        )
        assert recorded is not None
        assert recorded.package_id == out.package.id
        assert recorded.review_digest == digest

    def test_the_frozen_snapshot_carries_the_whole_assessment(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        db = canonical_book
        access, _r, _a, cycle = _build(db)
        digest = workflow.get_stages(db, access, cycle.id).review_digest
        out = freeze.freeze_cycle(
            db, access, cycle.id, IcaapFreezeCreate(review_digest=digest, reason="Seal the report.")
        )
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None
        block = package.snapshot["metadata"]["icaap"]
        assert block["review_digest"] == digest
        assert block["framework"]["code"] == "test_icaap"
        assert {entry["key"] for entry in block["sections"]} == {
            "executive_summary",
            "capital_adequacy",
            "supervisory_measures",
        }
        assert [stage["stage_key"] for stage in block["stages"]] == [
            "preparation",
            "cro_review",
            "brc_review",
            "board_approval",
        ]
        assert any(entry["kind"] == "senior_management_report" for entry in block["attachments"])
        # Every governed figure the framework depends on, with its provenance.
        assert {entry["param_code"] for entry in block["parameters"]}
        assert block["filing"]["statements"]["preparer"]

    def test_the_frozen_package_states_whose_figures_these_are(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """Provenance is stamped at the FROZEN mint site too (architecture audit M3).

        ``_stamp_provenance`` carries the invariant "a new generator cannot ship
        without a stated authority" — and it was only ever called from the
        GENERIC mint, so every freeze-minted package shipped with no
        ``snapshot["provenance"]`` at all: ``declared_methodologies`` returned
        ``[]`` and no section carried an authority. That disclosure (CF-1 / audit
        D-20) exists so an examiner can ask "which figure does this return
        mean?", and this is the one return that prints CAR, CET1, Tier 1 and
        leverage beside LCR, IRRBB and stress figures.
        """
        from app.services.regulatory_reporting import common  # noqa: PLC0415

        db = canonical_book
        access, _r, _a, cycle = _build(db)
        digest = workflow.get_stages(db, access, cycle.id).review_digest
        out = freeze.freeze_cycle(
            db, access, cycle.id, IcaapFreezeCreate(review_digest=digest, reason="Seal the report.")
        )
        package = db.get(RegulatoryPackage, out.package.id)
        assert package is not None

        provenance = package.snapshot["provenance"]
        assert provenance["return_code"] == "ICAAP-REPORT"
        # The ICAAP report binds sealed engine runs, so an ENGINE_RUN authority
        # is the honest one and the lineage is stated in full.
        assert provenance["authority"] == "engine_run"
        assert [entry["run_id"] for entry in provenance["source_runs"]] == [
            entry["run_id"] for entry in package.source_runs
        ]

        # The CF-1 disclosure answers rather than being empty: ``car_pct`` has
        # four registered methodologies and the filed record names the one this
        # report means.
        notes = common.declared_methodologies(package.snapshot)
        declared = {note.metric_id: note for note in notes}
        assert declared, "the ICAAP report declares no methodology at all"
        car = declared["car_pct"]
        assert car.methodology_id == "crd_basel_capital_run"
        assert car.registry_status == "registered"
        assert car.alternate_methodologies, "no alternates disclosed - nothing to disambiguate"

        # Every section resolves to an authority: ``row.authority ?? section.authority``
        # has something to resolve to on this family.
        assert package.snapshot["sections"]
        assert all(section.get("authority") for section in package.snapshot["sections"])

    def test_a_failure_after_the_package_is_flushed_leaves_nothing_behind(
        self, canonical_book: Session, extra_frameworks: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of one transaction: no half-frozen ICAAP."""
        db = canonical_book
        access, _r, _a, cycle = _build(db)
        digest = workflow.get_stages(db, access, cycle.id).review_digest

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("object storage is unreachable")

        # Raises AFTER generate_frozen_package has flushed the package row, so
        # the rollback has something real to undo.
        monkeypatch.setattr(freeze, "_copy_attachments", _boom)
        with pytest.raises(RuntimeError):
            freeze.freeze_cycle(
                db,
                access,
                cycle.id,
                IcaapFreezeCreate(review_digest=digest, reason="Seal the report."),
            )
        assert (
            db.scalars(
                select(RegulatoryPackage).where(RegulatoryPackage.return_code == "ICAAP-REPORT")
            ).all()
            == []
        )
        row = db.get(IcaapCycle, cycle.id)
        assert row is not None
        assert row.status == "in_review"
        assert row.package_id is None
        assert row.frozen_at is None
        assert (
            db.scalar(
                select(IcaapStageDecision).where(
                    IcaapStageDecision.cycle_id == cycle.id,
                    IcaapStageDecision.decision == "frozen",
                )
            )
            is None
        )

    def test_two_freezes_of_identical_content_produce_the_same_digest(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        """Value-based, like ``input_hash``: nothing volatile is in the digest."""
        db = canonical_book
        access, _r, _a, cycle = _build(db)
        row = guards.get_cycle_or_404(db, access, cycle.id)
        digest = workflow.get_stages(db, access, cycle.id).review_digest
        period = freeze.resolve_period(db, access, row, "ICAAP-REPORT")
        definition = freeze._definition("ICAAP-REPORT")  # noqa: SLF001 - the test names the seam
        from app.services.attestation import digests as digest_service  # noqa: PLC0415
        from app.services.icaap import snapshot  # noqa: PLC0415

        first = snapshot.build(
            db,
            access,
            row,
            review_digest=digest,
            bank=access.bank,
            period=period,
            definition=definition,
        )
        second = snapshot.build(
            db,
            access,
            row,
            review_digest=digest,
            bank=access.bank,
            period=period,
            definition=definition,
        )
        # The volatile field must BE there — a digest that is stable because the
        # snapshot never carried ``generated_at`` proves nothing.
        assert "generated_at" in first.snapshot["metadata"]
        assert "generated_at" in second.snapshot["metadata"]

        # Force the divergence rather than trusting the clock's resolution: two
        # builds in the same test can land on the same microsecond, and the
        # property under test is that the digest ignores the field whatever it
        # holds. (Until 2026-09-20 this line ended ``or True`` and could not
        # fail; the digests then matched for either reason.)
        second.snapshot["metadata"]["generated_at"] = "2099-01-01T00:00:00+00:00"
        assert (
            first.snapshot["metadata"]["generated_at"]
            != second.snapshot["metadata"]["generated_at"]
        )
        assert digest_service.content_digest(first.snapshot) == digest_service.content_digest(
            second.snapshot
        )

        # Positive control: the digest is not stable because it ignores
        # everything. A change to CONTENT moves it.
        moved = copy.deepcopy(first.snapshot)
        assert moved["metadata"]["icaap"]["review_digest"] == digest
        moved["metadata"]["icaap"]["review_digest"] = "f" * 64
        assert digest_service.content_digest(moved) != digest_service.content_digest(
            first.snapshot
        )


class TestTheCycleIsSealedAfterwards:
    def test_editing_a_frozen_report_is_refused(
        self, canonical_book: Session, extra_frameworks: None
    ) -> None:
        from app.schemas.icaap import IcaapSectionWorkingSave  # noqa: PLC0415
        from app.services.icaap import sections  # noqa: PLC0415

        db = canonical_book
        access, _r, _a, cycle = _build(db)
        digest = workflow.get_stages(db, access, cycle.id).review_digest
        freeze.freeze_cycle(
            db, access, cycle.id, IcaapFreezeCreate(review_digest=digest, reason="Seal the report.")
        )
        with pytest.raises(HTTPException) as caught:
            sections.save_working(
                db,
                access,
                cycle.id,
                "executive_summary",
                IcaapSectionWorkingSave(doc={"type": "doc", "content": []}, base_rev=1),
            )
        assert _error(caught.value) == "cycle_sealed"
