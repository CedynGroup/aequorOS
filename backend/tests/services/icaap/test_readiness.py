"""Readiness read end to end: real state, real sentences, no country in the copy."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.models import RegulatoryParameter
from app.schemas.icaap import (
    IcaapCycleRead,
    IcaapSectionCommit,
    IcaapSectionWorkingSave,
)
from app.services import jurisdictions
from app.services.icaap import parameters, readiness, sections


def _detail(caught: pytest.ExceptionInfo[HTTPException]) -> dict[str, Any]:
    """The refusal body, narrowed once so each assertion reads plainly."""
    detail = caught.value.detail
    assert isinstance(detail, dict)
    return detail


def _doc(text: str) -> dict:
    return {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


def test_a_new_cycle_reports_what_is_outstanding(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    report = readiness.get_readiness(canonical_book, access, cycle.id)
    assert report.ready_for_freeze is False
    codes = {item.code for item in report.items}
    assert "section_not_committed" in codes
    assert "rehearsal_not_fileable" in codes
    assert report.deadline.due_date is not None
    assert report.deadline.basis == "framework"


def test_committing_a_section_clears_its_finding(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    sections.save_working(
        canonical_book,
        access,
        cycle.id,
        "executive_summary",
        IcaapSectionWorkingSave(
            doc=_doc("The assessment concludes the institution is adequately capitalised."),
            base_rev=0,
        ),
    )
    sections.commit_version(
        canonical_book, access, cycle.id, "executive_summary", IcaapSectionCommit(base_rev=1)
    )
    report = readiness.get_readiness(canonical_book, access, cycle.id)
    uncommitted = {item.ref for item in report.items if item.code == "section_not_committed"}
    assert "executive_summary" not in uncommitted


def test_the_regulators_name_comes_from_the_registry_not_the_copy(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """A Nigerian bank must read its own supervisor's name in this sentence."""
    report = readiness.get_readiness(canonical_book, access, cycle.id)
    pending = next(item for item in report.items if item.code == "section_pending_primary_text")

    assert jurisdictions.regulator_short(canonical_book, access.bank) in pending.message
    assert "pending" in pending.message.casefold()


def test_every_finding_is_a_sentence_not_an_enum(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    report = readiness.get_readiness(canonical_book, access, cycle.id)
    assert report.items
    for item in report.items:
        assert item.message
        assert "{" not in item.message
        assert "_" not in item.message.split(" ")[0]


def test_the_per_section_summary_counts_what_each_one_still_needs(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    report = readiness.get_readiness(canonical_book, access, cycle.id)
    summary = {entry.key: entry for entry in report.sections}
    assert summary["executive_summary"].blocking >= 1
    assert report.counts["executive_summary"]["open"] >= 1


def test_the_amber_window_is_a_console_value_and_its_absence_degrades_gracefully(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """D-024 and D-036 together: nothing is substituted, nothing is refused.

    The amber window is a display policy, not a regulatory floor. With no row
    configured, readiness still answers: the date and the days remaining are
    facts, and the platform simply declines to colour the middle ground rather
    than inventing a window and presenting it as somebody's decision.
    """
    coloured = readiness.get_readiness(canonical_book, access, cycle.id)
    assert coloured.deadline.rag in {"green", "amber", "red"}

    rows = canonical_book.scalars(
        select(RegulatoryParameter).where(
            RegulatoryParameter.param_code == parameters.DEADLINE_AMBER_DAYS
        )
    ).all()
    assert rows, "the amber window is seeded as a governed parameter"
    for row in rows:
        canonical_book.delete(row)
    canonical_book.flush()

    report = readiness.get_readiness(canonical_book, access, cycle.id)
    assert report.deadline.due_date is not None
    assert report.deadline.days_remaining is not None
    # Overdue needs no window to establish, so it is still reported; the
    # uncoloured middle ground is the part that goes away.
    assert report.deadline.rag in {"none", "red"}
    assert report.deadline.rag != "amber"
    note = next(item for item in report.items if item.code == "deadline_window_not_configured")
    assert note.severity == "info"
    assert "colour" in note.message


def test_a_figure_a_checklist_item_quotes_is_refused_rather_than_guessed(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """D-024 §4: a number inside a regulatory requirement has no fallback."""
    rows = canonical_book.scalars(
        select(RegulatoryParameter).where(
            RegulatoryParameter.param_code == parameters.STRESS_HORIZON_YEARS_MIN
        )
    ).all()
    assert rows
    for row in rows:
        canonical_book.delete(row)
    canonical_book.flush()

    with pytest.raises(HTTPException) as caught:
        sections.get_section(canonical_book, access, cycle.id, "stress_testing")
    assert caught.value.status_code == 409
    assert _detail(caught)["error_code"] == "missing_parameter"
    assert _detail(caught)["param_code"] == parameters.STRESS_HORIZON_YEARS_MIN
