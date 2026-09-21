"""Creating, listing, retiring and re-basing an ICAAP cycle."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.models import AuditEvent
from app.models.icaap import IcaapCycle, IcaapSection
from app.schemas.icaap import IcaapCycleArchive, IcaapCycleRebase, IcaapCycleUpdate
from app.services.icaap import cycles
from tests.services.icaap.conftest import AS_OF, GH_FRAMEWORK, rehearsal_payload


def _detail(caught: pytest.ExceptionInfo[HTTPException]) -> dict[str, Any]:
    """The refusal body, narrowed once so each assertion reads plainly."""
    detail = caught.value.detail
    assert isinstance(detail, dict)
    return detail


def test_a_new_cycle_opens_every_section_of_the_regulators_report(
    canonical_book: Session, access: IcaapAccess
) -> None:
    cycle = cycles.create_cycle(canonical_book, access, rehearsal_payload())
    rows = canonical_book.scalars(
        select(IcaapSection).where(IcaapSection.cycle_id == cycle.id)
    ).all()
    assert len(rows) == len(cycle.sections) == 17
    assert [row.letter for row in sorted(rows, key=lambda r: r.position)][:3] == ["a", "b", "c"]
    assert all(row.working_doc == {"type": "doc", "content": []} for row in rows)
    assert all(row.working_rev == 0 for row in rows)


def test_the_cycle_pins_the_framework_it_was_started_under(
    canonical_book: Session, access: IcaapAccess
) -> None:
    cycle = cycles.create_cycle(canonical_book, access, rehearsal_payload())
    row = canonical_book.get(IcaapCycle, cycle.id)
    assert row is not None
    assert (row.framework_code, row.framework_version) == GH_FRAMEWORK
    assert len(row.framework_sha256) == 64
    assert cycle.framework_digest_matches is True


def test_the_position_date_is_the_regulators_year_end_not_the_clients(
    canonical_book: Session, access: IcaapAccess
) -> None:
    """A client-supplied date for an annual cycle is ignored, not honoured."""
    cycle = cycles.create_cycle(
        canonical_book, access, rehearsal_payload(as_of_date=date(2025, 6, 30))
    )
    assert cycle.as_of_date == AS_OF


def test_the_due_date_comes_from_the_governed_filing_period(
    canonical_book: Session, access: IcaapAccess
) -> None:
    cycle = cycles.create_cycle(canonical_book, access, rehearsal_payload())
    assert cycle.due_date == date(2026, 3, 31)
    assert cycle.due_date_basis == "framework"


def test_a_real_filing_cycle_cannot_pin_a_framework_that_does_not_cover_it(
    canonical_book: Session, access: IcaapAccess
) -> None:
    with pytest.raises(HTTPException) as caught:
        cycles.create_cycle(canonical_book, access, rehearsal_payload(cycle_kind="annual"))
    assert caught.value.status_code == 409
    assert _detail(caught)["error_code"] == "framework_not_applicable"


def test_only_one_open_cycle_per_year_kind_and_basis(
    canonical_book: Session, access: IcaapAccess
) -> None:
    cycles.create_cycle(canonical_book, access, rehearsal_payload())
    with pytest.raises(HTTPException) as caught:
        cycles.create_cycle(canonical_book, access, rehearsal_payload())
    assert _detail(caught)["error_code"] == "cycle_exists"


def test_solo_and_consolidated_are_separate_cycles(
    canonical_book: Session, access: IcaapAccess
) -> None:
    solo = cycles.create_cycle(canonical_book, access, rehearsal_payload())
    consolidated = cycles.create_cycle(
        canonical_book, access, rehearsal_payload(basis="consolidated")
    )
    assert solo.id != consolidated.id
    assert {solo.basis, consolidated.basis} == {"solo", "consolidated"}


def test_a_retired_cycle_frees_the_year_for_another_attempt(
    canonical_book: Session, access: IcaapAccess
) -> None:
    first = cycles.create_cycle(canonical_book, access, rehearsal_payload())
    cycles.archive_cycle(
        canonical_book, access, first.id, IcaapCycleArchive(reason="Started against the wrong year")
    )
    second = cycles.create_cycle(canonical_book, access, rehearsal_payload())
    assert second.id != first.id
    listed = cycles.list_cycles(canonical_book, access)
    assert [entry.id for entry in listed.cycles] == [second.id]
    with_archived = cycles.list_cycles(canonical_book, access, include_archived=True)
    assert {entry.id for entry in with_archived.cycles} == {first.id, second.id}


def test_an_off_cycle_update_names_the_change_that_prompted_it(
    canonical_book: Session, access: IcaapAccess, synthetic_registry
) -> None:
    one, _two = synthetic_registry
    payload = rehearsal_payload(
        framework_code=one.code,
        framework_version=one.version,
        cycle_kind="material_change",
        as_of_date=date(2025, 12, 31),
        change_trigger="not_a_real_trigger",
        reason="Merger",
    )
    with pytest.raises(HTTPException) as caught:
        cycles.create_cycle(canonical_book, access, payload)
    assert _detail(caught)["error_code"] == "change_trigger_unknown"


def test_a_material_change_update_never_gets_an_invented_deadline(
    canonical_book: Session, access: IcaapAccess, synthetic_registry
) -> None:
    """The Guideline says "in a timely manner" and names no date."""
    one, _two = synthetic_registry
    cycle = cycles.create_cycle(
        canonical_book,
        access,
        rehearsal_payload(
            framework_code=one.code,
            framework_version=one.version,
            cycle_kind="material_change",
            as_of_date=date(2025, 12, 31),
            change_trigger="other",
            reason="Acquisition of a subsidiary",
        ),
    )
    assert cycle.due_date is None
    assert cycle.due_date_basis == "timely"


def test_a_supervisory_request_records_the_letter_and_its_date(
    canonical_book: Session, access: IcaapAccess, synthetic_registry
) -> None:
    one, _two = synthetic_registry
    requested = date(2026, 6, 30)
    cycle = cycles.create_cycle(
        canonical_book,
        access,
        rehearsal_payload(
            framework_code=one.code,
            framework_version=one.version,
            cycle_kind="regulator_request",
            as_of_date=date(2025, 12, 31),
            regulator_request_ref="BSD/2026/0042",
            requested_due_date=requested,
            reason="Supervisory request",
        ),
    )
    assert (cycle.due_date, cycle.due_date_basis) == (requested, "regulator_set")
    assert cycle.regulator_request_ref == "BSD/2026/0042"


def test_a_supervisory_request_without_a_date_is_refused(
    canonical_book: Session, access: IcaapAccess, synthetic_registry
) -> None:
    one, _two = synthetic_registry
    with pytest.raises(HTTPException) as caught:
        cycles.create_cycle(
            canonical_book,
            access,
            rehearsal_payload(
                framework_code=one.code,
                framework_version=one.version,
                cycle_kind="regulator_request",
                as_of_date=date(2025, 12, 31),
                regulator_request_ref="BSD/2026/0042",
                reason="Supervisory request",
            ),
        )
    assert _detail(caught)["error_code"] == "requested_due_date_required"


def test_a_position_date_in_the_future_is_refused(
    canonical_book: Session, access: IcaapAccess, synthetic_registry
) -> None:
    one, _two = synthetic_registry
    with pytest.raises(HTTPException) as caught:
        cycles.create_cycle(
            canonical_book,
            access,
            rehearsal_payload(
                framework_code=one.code,
                framework_version=one.version,
                cycle_kind="material_change",
                as_of_date=date.today() + timedelta(days=1),
                change_trigger="other",
                reason="Future",
            ),
        )
    assert _detail(caught)["error_code"] == "as_of_in_future"


def test_the_governed_deadline_is_not_the_banks_to_move(
    canonical_book: Session, access: IcaapAccess
) -> None:
    cycle = cycles.create_cycle(canonical_book, access, rehearsal_payload())
    with pytest.raises(HTTPException) as caught:
        cycles.update_cycle(
            canonical_book,
            access,
            cycle.id,
            IcaapCycleUpdate(due_date=date(2026, 6, 30), reason="Wishful thinking"),
        )
    assert _detail(caught)["error_code"] == "due_date_is_governed"


def test_creation_and_retirement_are_recorded_in_the_audit_log(
    canonical_book: Session, access: IcaapAccess
) -> None:
    cycle = cycles.create_cycle(canonical_book, access, rehearsal_payload())
    cycles.archive_cycle(
        canonical_book, access, cycle.id, IcaapCycleArchive(reason="Superseded by a real cycle")
    )
    events = canonical_book.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == str(cycle.id))
    ).all()
    kinds = {event.event_type for event in events}
    assert {"icaap.cycle.created", "icaap.cycle.archived"} <= kinds
    created = next(event for event in events if event.event_type == "icaap.cycle.created")
    assert created.details["framework"] == f"{GH_FRAMEWORK[0]} {GH_FRAMEWORK[1]}"
    assert created.details["reason"]


def test_a_cycle_that_was_never_frozen_is_the_only_kind_that_can_be_retired(
    canonical_book: Session, access: IcaapAccess
) -> None:
    cycle = cycles.create_cycle(canonical_book, access, rehearsal_payload())
    row = canonical_book.get(IcaapCycle, cycle.id)
    assert row is not None
    row.status = "in_review"
    canonical_book.flush()
    with pytest.raises(HTTPException) as caught:
        cycles.archive_cycle(
            canonical_book, access, cycle.id, IcaapCycleArchive(reason="No longer needed")
        )
    assert _detail(caught)["error_code"] == "cycle_not_archivable"


def test_conditions_describe_the_institution_the_checklist_is_for(
    canonical_book: Session, access: IcaapAccess
) -> None:
    cycle = cycles.create_cycle(canonical_book, access, rehearsal_payload())
    row = canonical_book.get(IcaapCycle, cycle.id)
    assert row is not None
    assert cycles.cycle_conditions(canonical_book, access, row) == {"cycle_kind:rehearsal"}
    row.subsidiaries_declared = True
    canonical_book.flush()
    conditions = cycles.cycle_conditions(canonical_book, access, row)
    assert {"has_subsidiaries", "group_member"} <= conditions


def test_the_framework_list_offers_what_this_institution_may_assess_against(
    canonical_book: Session, access: IcaapAccess
) -> None:
    listed = cycles.list_frameworks(canonical_book, access)
    assert [(entry.code, entry.version) for entry in listed.frameworks] == [GH_FRAMEWORK]
    assert listed.frameworks[0].status == "exposure_draft"


def test_the_framework_detail_resolves_its_governed_filing_period(
    canonical_book: Session, access: IcaapAccess
) -> None:
    detail = cycles.get_framework(canonical_book, access, *GH_FRAMEWORK)
    assert detail.deadline.months_param_code == "icaap_submission_months"
    assert detail.deadline.months_after_fy_end == 3
    assert detail.deadline.months_confirmation_status == "pending"
    assert len(detail.sections) == 17


def test_the_block_catalogue_marks_later_phase_blocks_unavailable() -> None:
    listed = cycles.list_block_types()
    available = {entry.type for entry in listed.block_types if entry.available}
    unavailable = {entry.type for entry in listed.block_types if not entry.available}
    assert {"capital_position", "risk_register", "irrbb_sf"} <= available
    assert {"workflow_summary"} <= unavailable


def test_a_rebase_creates_a_successor_and_supersedes_the_original(
    canonical_book: Session, access: IcaapAccess, synthetic_registry
) -> None:
    one, two = synthetic_registry
    source = cycles.create_cycle(
        canonical_book,
        access,
        rehearsal_payload(framework_code=one.code, framework_version=one.version),
    )
    rebased = cycles.rebase_cycle(
        canonical_book,
        access,
        source.id,
        _rebase_payload(two.code, two.version),
    )
    assert rebased.rebased_from_cycle_id == source.id
    assert (rebased.framework.code, rebased.framework.version) == (two.code, two.version)
    assert {section.key for section in rebased.sections} == {
        section.key for section in two.sections
    }
    original = canonical_book.get(IcaapCycle, source.id)
    assert original is not None
    assert original.status == "superseded"


def _rebase_payload(code: str, version: str):

    return IcaapCycleRebase(
        framework_code=code, framework_version=version, reason="Final text published"
    )
