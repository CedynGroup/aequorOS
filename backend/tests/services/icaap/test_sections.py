"""Section text: autosave, conflicts, immutable versions and the checklist."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.models import AuditEvent
from app.models.icaap import IcaapCycle, IcaapSectionVersion
from app.schemas.icaap import (
    IcaapCycleArchive,
    IcaapCycleRead,
    IcaapDataBlockCreate,
    IcaapRequirementStateUpdate,
    IcaapSectionCommit,
    IcaapSectionWorkingSave,
)
from app.services.icaap import blocks, cycles, guards, sections

SECTION = "executive_summary"


def _detail(caught: pytest.ExceptionInfo[HTTPException]) -> dict[str, Any]:
    assert isinstance(caught.value.detail, dict)
    return caught.value.detail


def doc(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


def _save(db: Session, access: IcaapAccess, cycle: IcaapCycleRead, text: str, base_rev: int):
    return sections.save_working(
        db, access, cycle.id, SECTION, IcaapSectionWorkingSave(doc=doc(text), base_rev=base_rev)
    )


def test_a_section_opens_with_the_regulators_guidance_and_checklist(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    section = sections.get_section(canonical_book, access, cycle.id, SECTION)
    assert section.title == "Executive Summary"
    assert section.citation.cite_id.endswith(":49(a)")
    assert section.guidance
    assert section.requirements
    assert all(item.status == "open" for item in section.requirements)
    assert section.working_rev == 0
    assert section.editable is True


def test_saving_advances_the_revision(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    first = _save(canonical_book, access, cycle, "First draft", 0)
    assert first.working_rev == 1
    second = _save(canonical_book, access, cycle, "Second draft", 1)
    assert second.working_rev == 2
    assert second.has_uncommitted_changes is True


def test_a_save_from_a_stale_tab_is_refused_with_what_it_missed(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """Two people in two tabs is the normal case, not the edge case."""
    _save(canonical_book, access, cycle, "Mine", 0)
    with pytest.raises(HTTPException) as caught:
        _save(canonical_book, access, cycle, "Theirs", 0)
    detail = _detail(caught)
    assert caught.value.status_code == 409
    assert detail["error_code"] == "section_rev_conflict"
    assert detail["current_rev"] == 1
    assert detail["updated_by"]
    assert detail["updated_at"]
    # The refused save left nothing behind.
    assert sections.get_section(canonical_book, access, cycle.id, SECTION).working_rev == 1


def test_a_document_the_grammar_refuses_is_reported_with_its_location(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    payload = IcaapSectionWorkingSave(
        doc={"type": "doc", "content": [{"type": "image", "attrs": {"src": "x"}}]}, base_rev=0
    )
    with pytest.raises(HTTPException) as caught:
        sections.save_working(canonical_book, access, cycle.id, SECTION, payload)
    detail = _detail(caught)
    assert caught.value.status_code == 422
    assert detail["error_code"] == "invalid_document"
    assert detail["code"] == "unknown_node"
    assert detail["path"]


def test_text_cannot_quote_a_figure_this_report_does_not_hold(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    unknown = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "factRef",
                        "attrs": {
                            "blockId": "018f3a2c-9c1e-7b3d-8a44-0c1d2e3f4a99",
                            "factKey": "car_pct",
                        },
                    }
                ],
            }
        ],
    }
    with pytest.raises(HTTPException) as caught:
        sections.save_working(
            canonical_book,
            access,
            cycle.id,
            SECTION,
            IcaapSectionWorkingSave(doc=unknown, base_rev=0),
        )
    assert _detail(caught)["error_code"] == "unknown_block"


def test_text_cannot_quote_a_figure_the_block_does_not_publish(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    block = blocks.create_block(
        canonical_book, access, cycle.id, IcaapDataBlockCreate(block_type="capital_position")
    )
    payload = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "factRef",
                        "attrs": {"blockId": str(block.id), "factKey": "not_published"},
                    }
                ],
            }
        ],
    }
    with pytest.raises(HTTPException) as caught:
        sections.save_working(
            canonical_book,
            access,
            cycle.id,
            SECTION,
            IcaapSectionWorkingSave(doc=payload, base_rev=0),
        )
    assert _detail(caught)["error_code"] == "unknown_fact"


def test_a_commit_writes_the_text_its_author_actually_read(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    _save(canonical_book, access, cycle, "Draft one", 0)
    _save(canonical_book, access, cycle, "Draft two", 1)
    with pytest.raises(HTTPException) as caught:
        sections.commit_version(
            canonical_book, access, cycle.id, SECTION, IcaapSectionCommit(base_rev=1)
        )
    assert _detail(caught)["error_code"] == "section_rev_conflict"

    version = sections.commit_version(
        canonical_book,
        access,
        cycle.id,
        SECTION,
        IcaapSectionCommit(base_rev=2, note="Reviewed by the CRO"),
    )
    assert version.version_no == 1
    assert version.plain_text == "Draft two"
    assert len(version.doc_sha256) == 64
    assert version.commit_note == "Reviewed by the CRO"


def test_an_empty_section_has_nothing_to_commit(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    with pytest.raises(HTTPException) as caught:
        sections.commit_version(
            canonical_book, access, cycle.id, SECTION, IcaapSectionCommit(base_rev=0)
        )
    assert _detail(caught)["error_code"] == "section_empty"


def test_committing_unchanged_text_is_refused_rather_than_duplicated(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    _save(canonical_book, access, cycle, "Same words", 0)
    sections.commit_version(
        canonical_book, access, cycle.id, SECTION, IcaapSectionCommit(base_rev=1)
    )
    _save(canonical_book, access, cycle, "Same words", 1)
    with pytest.raises(HTTPException) as caught:
        sections.commit_version(
            canonical_book, access, cycle.id, SECTION, IcaapSectionCommit(base_rev=2)
        )
    detail = _detail(caught)
    assert detail["error_code"] == "no_changes"
    assert detail["version_no"] == 1


def test_versions_accumulate_and_are_never_rewritten(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    _save(canonical_book, access, cycle, "One", 0)
    first = sections.commit_version(
        canonical_book, access, cycle.id, SECTION, IcaapSectionCommit(base_rev=1)
    )
    _save(canonical_book, access, cycle, "Two", 1)
    second = sections.commit_version(
        canonical_book, access, cycle.id, SECTION, IcaapSectionCommit(base_rev=2)
    )
    listing = sections.list_versions(canonical_book, access, cycle.id, SECTION)
    assert [entry.version_no for entry in listing.versions] == [2, 1]
    stored = canonical_book.scalars(
        select(IcaapSectionVersion).where(IcaapSectionVersion.cycle_id == cycle.id)
    ).all()
    assert {row.id for row in stored} == {first.id, second.id}
    assert sections.get_version(canonical_book, access, cycle.id, SECTION, 1).plain_text == "One"


def test_a_commit_records_the_figures_the_text_quoted(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    block = blocks.create_block(
        canonical_book, access, cycle.id, IcaapDataBlockCreate(block_type="institution_profile")
    )
    payload = {
        "type": "doc",
        "content": [
            {"type": "dataBlock", "attrs": {"blockId": str(block.id)}},
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "factRef",
                        "attrs": {"blockId": str(block.id), "factKey": "institution_type"},
                    }
                ],
            },
        ],
    }
    sections.save_working(
        canonical_book, access, cycle.id, SECTION, IcaapSectionWorkingSave(doc=payload, base_rev=0)
    )
    version = sections.commit_version(
        canonical_book, access, cycle.id, SECTION, IcaapSectionCommit(base_rev=1)
    )
    assert version.block_refs == [str(block.id)]
    assert version.fact_refs == [
        {"block_id": str(block.id), "fact_key": "institution_type", "suggestion_id": None}
    ]


def test_marking_an_item_not_applicable_needs_a_waiver_and_a_reason(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    section = sections.get_section(canonical_book, access, cycle.id, SECTION)
    mandatory = next(item for item in section.requirements if not item.item.waivable)
    with pytest.raises(HTTPException) as caught:
        sections.set_requirement_state(
            canonical_book,
            access,
            cycle.id,
            SECTION,
            mandatory.item.id,
            IcaapRequirementStateUpdate(status="not_applicable", reason="Not relevant to us"),
        )
    assert _detail(caught)["error_code"] == "not_waivable"

    waivable = next((item for item in section.requirements if item.item.waivable), None)
    if waivable is not None:
        with pytest.raises(HTTPException) as short:
            sections.set_requirement_state(
                canonical_book,
                access,
                cycle.id,
                SECTION,
                waivable.item.id,
                IcaapRequirementStateUpdate(status="not_applicable", reason="n/a"),
            )
        assert _detail(short)["error_code"] == "reason_required"


def test_marking_an_item_met_is_recorded_with_who_and_when(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    section = sections.get_section(canonical_book, access, cycle.id, SECTION)
    item_id = section.requirements[0].item.id
    updated = sections.set_requirement_state(
        canonical_book,
        access,
        cycle.id,
        SECTION,
        item_id,
        IcaapRequirementStateUpdate(status="met"),
    )
    state = next(item for item in updated.requirements if item.item.id == item_id)
    assert state.status == "met"
    assert state.updated_by is not None
    assert state.updated_at is not None
    assert updated.requirement_counts["met"] == 1
    events = canonical_book.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "icaap.requirement.state_changed")
    ).all()
    assert events and events[-1].details["item_id"] == item_id


def test_a_checklist_item_shows_its_governed_figure_and_says_it_is_provisional(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """D-024: the horizon is a console value, not a number in the data."""
    section = sections.get_section(canonical_book, access, cycle.id, "stress_testing")
    quoted = [item for item in section.requirements if item.item.param_refs]
    assert quoted, "the stress checklist quotes a governed horizon"
    rendered = quoted[0].resolved_text
    assert "{param:" not in rendered
    assert "3 years" in rendered
    assert "pending" in rendered


def test_a_retired_cycle_is_no_longer_editable(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    cycles.archive_cycle(canonical_book, access, cycle.id, IcaapCycleArchive(reason="Wrong year"))
    with pytest.raises(HTTPException) as caught:
        _save(canonical_book, access, cycle, "Too late", 0)
    assert _detail(caught)["error_code"] == "cycle_locked"


def test_a_frozen_cycle_keeps_the_text_the_board_approved(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    row = canonical_book.get(IcaapCycle, cycle.id)
    assert row is not None
    row.frozen_at = row.created_at
    row.status = "in_review"
    canonical_book.flush()
    with pytest.raises(HTTPException) as caught:
        _save(canonical_book, access, cycle, "Edit after freeze", 0)
    assert _detail(caught)["error_code"] == "cycle_sealed"


def test_autosaves_do_not_flood_the_audit_log(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    for index in range(5):
        _save(canonical_book, access, cycle, f"Draft {index}", index)
    events = canonical_book.scalars(
        select(AuditEvent).where(AuditEvent.event_type.like("icaap.section%"))
    ).all()
    assert events == []
    sections.commit_version(
        canonical_book, access, cycle.id, SECTION, IcaapSectionCommit(base_rev=5)
    )
    committed = canonical_book.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "icaap.section.version_committed")
    ).all()
    assert len(committed) == 1


def test_section_states_describe_what_a_freeze_would_seal(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    _save(canonical_book, access, cycle, "Committed text", 0)
    sections.commit_version(
        canonical_book, access, cycle.id, SECTION, IcaapSectionCommit(base_rev=1)
    )
    _save(canonical_book, access, cycle, "Later edit", 1)
    row = canonical_book.get(IcaapCycle, cycle.id)
    assert row is not None

    framework = guards.require_framework(row)
    states = {
        state.key: state
        for state in sections.section_states(canonical_book, access, row, framework)
    }
    assert states[SECTION].committed_version_no == 1
    assert states[SECTION].committed_has_content is True
    assert states[SECTION].uncommitted_changes is True
    assert states["governance"].committed_version_no is None
