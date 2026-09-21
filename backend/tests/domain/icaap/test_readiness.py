"""Readiness: what stops a freeze, what is only a warning, and why."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from app.domain.icaap import readiness
from app.domain.icaap.blocks import BlockStatus
from app.domain.icaap.frameworks import registry
from app.domain.icaap.frameworks.schema import Framework

AS_OF = date(2026, 12, 31)
DUE = date(2027, 3, 31)
AMBER_DAYS = 30
BLOCK_ID = "018f3a2c-9c1e-7b3d-8a44-0c1d2e3f4a5b"


@pytest.fixture
def gh() -> Framework:
    return registry.get("bog_icaap", "2026.02-ed.1")


def _sections(  # noqa: PLR0913 - one switch per section state under test
    framework: Framework,
    *,
    committed: bool = True,
    has_content: bool = True,
    all_items_met: bool = True,
    uncommitted: bool = False,
    referenced: frozenset[str] = frozenset(),
) -> tuple[readiness.SectionState, ...]:
    states = []
    for index, section in enumerate(framework.sections):
        item_states = {
            item.id: readiness.ItemState(status="met" if all_items_met else "open")
            for item in section.requirements
        }
        states.append(
            readiness.SectionState(
                key=section.key,
                committed_version_no=(index + 1) if committed else None,
                committed_has_content=committed and has_content,
                uncommitted_changes=uncommitted,
                item_states=item_states,
                referenced_block_ids=referenced if index == 0 else frozenset(),
            )
        )
    return tuple(states)


def _expected_blocks(framework: Framework) -> dict[str, readiness.BlockState]:
    """A sound block for every type the framework declares as section evidence.

    The baseline for :func:`_state`, since 2026-09-20. A declared block that is
    absent is now BLOCKING (independent audit F2), so a state with no blocks is
    not a neutral starting point for testing deadlines and attachments — it is a
    report with no capital figures in it, which is exactly what the freeze
    refuses. A test that wants a particular block states it and the entry of that
    TYPE is replaced, so each test still says only what it is about.
    """
    types = {
        block_type for section in framework.sections for block_type in section.data_blocks
    }
    return {
        block_type: readiness.BlockState(
            block_id=f"expected-{block_type}",
            block_type=block_type,
            status=BlockStatus.FRESH,
            pin_reason=None,
            manual=False,
            evidence_active=True,
            retired=False,
        )
        for block_type in sorted(types)
    }


def _state(  # noqa: PLR0913 - one switch per cycle state under test
    framework: Framework,
    *,
    cycle_kind: str = "annual",
    blocks: tuple[readiness.BlockState, ...] = (),
    attachments: dict[str, int] | None = None,
    conditions: frozenset[str] | None = None,
    companion: bool = True,
    digest_matches: bool = True,
    due_date: date | None = DUE,
    **section_kwargs: object,
) -> readiness.CycleState:
    counts = {attachment.kind: 1 for attachment in framework.attachments}
    counts.update(attachments or {})
    resolved = _expected_blocks(framework) | {block.block_type: block for block in blocks}
    return readiness.CycleState(
        cycle_kind=cycle_kind,
        status="draft",
        as_of=AS_OF,
        due_date=due_date,
        basis="solo",
        conditions=(
            conditions if conditions is not None else frozenset({f"cycle_kind:{cycle_kind}"})
        ),
        framework_digest_matches=digest_matches,
        companion_basis_cycle_exists=companion,
        sections=_sections(framework, **section_kwargs),  # pyright: ignore[reportArgumentType]
        blocks=tuple(resolved.values()),
        active_attachment_counts=counts,
    )


def _codes(report: readiness.ReadinessReport, severity: str | None = None) -> set[str]:
    return {item.code for item in report.items if severity is None or item.severity == severity}


def _evaluate(framework: Framework, state: readiness.CycleState) -> readiness.ReadinessReport:
    return readiness.evaluate(framework, state, today=date(2026, 12, 31), amber_days=AMBER_DAYS)


def test_pending_regulator_text_stops_a_real_filing_but_not_a_rehearsal(gh: Framework) -> None:
    """D-006: a rehearsal exists to be run before the final text arrives."""
    annual = _evaluate(gh, _state(gh))
    assert "section_pending_primary_text" in _codes(annual, "blocking")
    assert annual.ready_for_freeze is False
    assert len(annual.pending_primary_text_sections) == len(gh.sections) - 2

    rehearsal = _evaluate(gh, _state(gh, cycle_kind="rehearsal"))
    assert "section_pending_primary_text" in _codes(rehearsal, "warning")
    assert "section_pending_primary_text" not in _codes(rehearsal, "blocking")
    assert rehearsal.ready_for_freeze is True


def test_a_rehearsal_says_out_loud_that_it_is_not_a_filing(gh: Framework) -> None:
    report = _evaluate(gh, _state(gh, cycle_kind="rehearsal"))
    assert "rehearsal_not_fileable" in _codes(report, "info")


def test_an_exposure_draft_is_flagged_as_provisional(gh: Framework) -> None:
    report = _evaluate(gh, _state(gh, cycle_kind="rehearsal"))
    assert "framework_exposure_draft" in _codes(report, "info")


def test_a_framework_edited_under_a_cycle_blocks_the_freeze(gh: Framework) -> None:
    report = _evaluate(gh, _state(gh, cycle_kind="rehearsal", digest_matches=False))
    assert "framework_digest_mismatch" in _codes(report, "blocking")
    assert report.ready_for_freeze is False


def test_uncommitted_and_empty_sections_are_told_apart(gh: Framework) -> None:
    uncommitted = _evaluate(gh, _state(gh, cycle_kind="rehearsal", committed=False))
    assert "section_not_committed" in _codes(uncommitted, "blocking")

    empty = _evaluate(gh, _state(gh, cycle_kind="rehearsal", has_content=False))
    assert "section_empty" in _codes(empty, "blocking")

    edited = _evaluate(gh, _state(gh, cycle_kind="rehearsal", uncommitted=True))
    assert "section_uncommitted_changes" in _codes(edited, "warning")
    assert edited.ready_for_freeze is True


def test_an_open_checklist_item_blocks_and_is_counted(gh: Framework) -> None:
    report = _evaluate(gh, _state(gh, cycle_kind="rehearsal", all_items_met=False))
    assert "requirement_open" in _codes(report, "blocking")
    summary = report.section_counts["executive_summary"]
    assert summary["open"] == len(gh.section("executive_summary").requirements)
    assert summary["met"] == 0


def test_an_item_whose_condition_does_not_hold_needs_nobody_to_mark_it(gh: Framework) -> None:
    conditional = next(item for item in gh.all_items() if item.applies_when == "has_subsidiaries")
    section = next(
        s for s in gh.sections if any(item.id == conditional.id for item in s.requirements)
    )
    report = _evaluate(gh, _state(gh, cycle_kind="rehearsal"))
    assert report.section_counts[section.key]["auto_not_applicable"] >= 1
    open_items = {item.ref for item in report.items if item.code == "requirement_open"}
    assert conditional.id not in open_items


def _block(
    status: BlockStatus,
    *,
    block_type: str = "capital_position",
    manual: bool = False,
    evidence: bool = True,
    pin_reason: str | None = None,
) -> readiness.BlockState:
    return readiness.BlockState(
        block_id=BLOCK_ID,
        block_type=block_type,
        status=status,
        pin_reason=pin_reason,
        manual=manual,
        evidence_active=evidence,
        retired=False,
    )


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (BlockStatus.UNBOUND, "block_referenced_unbound"),
        (BlockStatus.STALE, "block_stale"),
        (BlockStatus.AS_OF_MISMATCH, "block_as_of_mismatch"),
        (BlockStatus.SOURCE_MISSING, "block_source_missing"),
        (BlockStatus.SOURCE_WITHDRAWN, "block_source_withdrawn"),
    ],
)
def test_a_figure_the_text_relies_on_must_be_sound(
    gh: Framework, status: BlockStatus, code: str
) -> None:
    state = _state(
        gh, cycle_kind="rehearsal", blocks=(_block(status),), referenced=frozenset({BLOCK_ID})
    )
    report = _evaluate(gh, state)
    assert code in _codes(report, "blocking")


def test_a_figure_neither_quoted_nor_expected_does_not_stop_the_freeze(gh: Framework) -> None:
    """A block a preparer added for themselves is not the filing's evidence.

    NARROWED 2026-09-20 (independent audit F2). It used to use
    ``capital_position`` — a type the framework DECLARES for two of its sections
    — and so asserted that an unsound capital figure is fine as long as no
    sentence quotes it. It is not: a live block's facts ride into the frozen
    snapshot and its run into ``source_runs`` whether prose cites it or not, and
    the headline reads the capital block directly. The rule the name refers to
    is real and survives here, on a type the framework expects nowhere.
    """
    state = _state(
        gh,
        cycle_kind="rehearsal",
        blocks=(_block(BlockStatus.STALE, block_type="capital_allocation"),),
    )
    report = _evaluate(gh, state)
    assert "block_stale" not in _codes(report, "blocking")


def test_an_unsound_figure_the_framework_expects_stops_the_freeze_unquoted(
    gh: Framework,
) -> None:
    """The other half of the narrowing above, and what makes it non-vacuous."""
    state = _state(
        gh,
        cycle_kind="rehearsal",
        blocks=(_block(BlockStatus.STALE, block_type="capital_position"),),
    )
    report = _evaluate(gh, state)
    assert "block_stale" in _codes(report, "blocking")
    assert report.ready_for_freeze is False


def test_a_pinned_figure_is_a_recorded_judgement_not_a_blocker(gh: Framework) -> None:
    state = _state(
        gh,
        cycle_kind="rehearsal",
        blocks=(_block(BlockStatus.PINNED, pin_reason="Board reviewed the December figures"),),
        referenced=frozenset({BLOCK_ID}),
    )
    report = _evaluate(gh, state)
    assert "block_pinned" in _codes(report, "warning")
    assert report.ready_for_freeze is True


def test_a_retired_figure_is_ignored(gh: Framework) -> None:
    retired = readiness.BlockState(
        block_id=BLOCK_ID,
        block_type="capital_position",
        status=BlockStatus.STALE,
        pin_reason=None,
        manual=False,
        evidence_active=False,
        retired=True,
    )
    report = _evaluate(
        gh, _state(gh, cycle_kind="rehearsal", blocks=(retired,), referenced=frozenset({BLOCK_ID}))
    )
    assert "block_stale" not in _codes(report)


def test_a_typed_table_needs_the_evidence_it_was_typed_from(gh: Framework) -> None:
    state = _state(
        gh,
        cycle_kind="rehearsal",
        blocks=(_block(BlockStatus.FRESH, block_type="financials", manual=True, evidence=False),),
    )
    report = _evaluate(gh, state)
    assert "manual_table_without_evidence" in _codes(report, "blocking")


def test_a_section_missing_an_expected_figure_refuses_the_freeze(gh: Framework) -> None:
    """REWRITTEN 2026-09-20 — the old test pinned the wrong behaviour.

    It asserted ``block_expected_absent`` was a WARNING and that such a cycle was
    ``ready_for_freeze``. Independent audit F2: the validation rules that would
    catch a bad figure are themselves conditional on the block being present, so
    the two soft gates in series let a report freeze with no capital figures in
    it at all — while a capital figure with the WRONG as-of date was an ERROR.
    The absence of a figure cannot be quieter than the figure being wrong.

    The framework's ``data_blocks`` is the instrument's own statement of the
    evidence a section carries, so this stays jurisdiction-neutral: the test
    framework declares none and nothing blocks there.
    """
    without_capital = tuple(
        block
        for block in _expected_blocks(gh).values()
        if block.block_type != "capital_position"
    )
    state = _state(gh, cycle_kind="rehearsal")
    report = _evaluate(gh, replace(state, blocks=without_capital))
    absent = [item for item in report.items if item.code == "block_expected_absent"]
    assert absent, "the missing capital position was not reported at all"
    assert all(item.severity == "blocking" for item in absent)
    assert {item.params["block_type"] for item in absent} == {"capital_position"}
    assert report.ready_for_freeze is False

    # Non-vacuity: the same framework with its expected evidence present is ready.
    assert _evaluate(gh, state).ready_for_freeze is True


def test_freeze_and_submission_documents_are_gated_differently(gh: Framework) -> None:
    missing_freeze = _evaluate(
        gh, _state(gh, cycle_kind="rehearsal", attachments={"senior_management_report": 0})
    )
    freeze_items = [i for i in missing_freeze.items if i.code == "attachment_missing"]
    assert any(item.severity == "blocking" for item in freeze_items)

    missing_submission = _evaluate(
        gh, _state(gh, cycle_kind="rehearsal", attachments={"board_resolution": 0})
    )
    submission_items = [i for i in missing_submission.items if i.code == "attachment_missing"]
    assert submission_items and all(item.severity == "info" for item in submission_items)
    assert missing_submission.ready_for_freeze is True


def test_a_document_only_some_institutions_need_is_asked_for_only_of_them(
    gh: Framework,
) -> None:
    conditional = next(
        a
        for a in gh.attachments
        if a.applies_when is not None and a.gate in {"freeze", "submission"}
    )
    absent = _evaluate(gh, _state(gh, cycle_kind="rehearsal", attachments={conditional.kind: 0}))
    assert conditional.kind not in {
        item.ref for item in absent.items if item.code == "attachment_missing"
    }
    applies = _evaluate(
        gh,
        _state(
            gh,
            cycle_kind="rehearsal",
            attachments={conditional.kind: 0},
            conditions=frozenset({"cycle_kind:rehearsal", conditional.applies_when or ""}),
        ),
    )
    assert conditional.kind in {
        item.ref for item in applies.items if item.code == "attachment_missing"
    }


def test_a_group_needs_both_a_solo_and_a_consolidated_assessment(gh: Framework) -> None:
    """D-018: they are different numbers, so they are different cycles."""
    state = _state(
        gh,
        cycle_kind="rehearsal",
        conditions=frozenset({"cycle_kind:rehearsal", "has_subsidiaries"}),
        companion=False,
    )
    report = _evaluate(gh, state)
    assert "basis_companion_missing" in _codes(report, "blocking")


@pytest.mark.parametrize(
    ("due", "expected"),
    [
        (date(2027, 3, 31), "green"),
        (date(2027, 1, 31), "green"),
        (date(2027, 1, 30), "amber"),
        (date(2026, 12, 31), "amber"),
        (date(2026, 12, 30), "red"),
        (None, "none"),
    ],
)
def test_the_deadline_is_reported_but_never_blocks(
    gh: Framework, due: date | None, expected: str
) -> None:
    """Refusing to freeze an overdue report would make the breach worse."""
    report = _evaluate(gh, _state(gh, cycle_kind="rehearsal", due_date=due))
    assert report.deadline.rag == expected
    assert report.ready_for_freeze is True


def test_with_no_amber_window_the_countdown_is_uncoloured_but_still_reported(
    gh: Framework,
) -> None:
    """D-036: a display policy nobody configured is said, not invented."""
    state = _state(gh, cycle_kind="rehearsal", due_date=date(2027, 2, 1))
    report = readiness.evaluate(gh, state, today=date(2026, 12, 31), amber_days=None)
    assert report.deadline.days_remaining == 32
    assert report.deadline.rag == "none"
    assert "deadline_window_not_configured" in _codes(report, "info")
    assert report.ready_for_freeze is True

    overdue = readiness.evaluate(
        gh,
        _state(gh, cycle_kind="rehearsal", due_date=date(2026, 12, 30)),
        today=date(2026, 12, 31),
        amber_days=None,
    )
    assert overdue.deadline.rag == "red", "overdue needs no window to establish"


def test_a_framework_the_platform_cannot_file_blocks_a_real_cycle(gh: Framework) -> None:
    """A7: reference data is publishable long before a filing route exists."""

    reference_only = replace(gh, filing_return_code=None)
    report = _evaluate(reference_only, _state(reference_only))
    assert "filing_not_available_for_framework" in _codes(report, "blocking")

    rehearsal = _evaluate(reference_only, _state(reference_only, cycle_kind="rehearsal"))
    assert "filing_not_available_for_framework" not in _codes(rehearsal)


def test_every_finding_carries_a_code_a_service_can_render(gh: Framework) -> None:
    report = _evaluate(gh, _state(gh))
    for item in report.items:
        assert item.code
        assert item.severity in {"blocking", "warning", "info"}
        assert item.scope in {"cycle", "section", "requirement", "block", "attachment"}
        assert all(isinstance(value, str) for value in item.params.values())
