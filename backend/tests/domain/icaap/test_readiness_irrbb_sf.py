"""The standardised framework's readiness rules (P5-DESIGN §1.6 item 4).

Severity is the whole subject. A rehearsal exists precisely to be run before
everything is in place, so the two mandate refusals are warnings there and
blocking for anything a regulator would receive — the same shape D-006 gives
``section_pending_primary_text``. And a framework that declares no method
mandate at all (Nigeria's, Kenya's) must assert nothing, so a Ghanaian
commencement date can never block a Nigerian filing.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.icaap import readiness
from app.domain.icaap.frameworks import registry
from app.domain.icaap.frameworks.schema import Framework

AS_OF = date(2026, 12, 31)
MANDATORY_FROM = date(2026, 12, 31)


@pytest.fixture
def gh() -> Framework:
    return registry.get("bog_icaap", "2026.02-ed.1")


def _state(
    framework: Framework, *, cycle_kind: str = "annual", **sf_kwargs: object
) -> readiness.CycleState:
    sections = tuple(
        readiness.SectionState(
            key=section.key,
            committed_version_no=1,
            committed_has_content=True,
            uncommitted_changes=False,
            item_states={
                item.id: readiness.ItemState(status="met") for item in section.requirements
            },
            referenced_block_ids=frozenset(),
        )
        for section in framework.sections
    )
    return readiness.CycleState(
        cycle_kind=cycle_kind,
        status="draft",
        as_of=AS_OF,
        due_date=None,
        basis="solo",
        conditions=frozenset({f"cycle_kind:{cycle_kind}"}),
        framework_digest_matches=True,
        companion_basis_cycle_exists=True,
        sections=sections,
        blocks=(),
        active_attachment_counts={attachment.kind: 1 for attachment in framework.attachments},
        irrbb_sf=readiness.IrrbbSfState(**sf_kwargs),  # pyright: ignore[reportArgumentType]
    )


def _items(framework: Framework, state: readiness.CycleState) -> dict[str, readiness.ReadinessItem]:
    report = readiness.evaluate(framework, state, today=AS_OF, amber_days=None)
    return {item.code: item for item in report.items}


def test_a_framework_with_no_declared_mandate_asserts_nothing(gh: Framework) -> None:
    items = _items(gh, _state(gh, declared=False, mandatory=True, run_present=False))
    assert "irrbb_sf_required_missing" not in items
    assert "irrbb_interim_not_permitted" not in items


def test_before_the_commencement_date_nothing_is_required(gh: Framework) -> None:
    items = _items(
        gh,
        _state(
            gh,
            declared=True,
            mandatory=False,
            mandatory_from=date(2027, 12, 31),
            superseded_methods_in_use=("irrbb_interim_delta_eve",),
        ),
    )
    assert "irrbb_sf_required_missing" not in items
    assert "irrbb_interim_not_permitted" not in items


def test_from_the_commencement_date_a_filing_is_blocked_without_the_framework(
    gh: Framework,
) -> None:
    items = _items(
        gh, _state(gh, declared=True, mandatory=True, mandatory_from=MANDATORY_FROM)
    )
    assert items["irrbb_sf_required_missing"].severity == "blocking"
    assert items["irrbb_sf_required_missing"].params["mandatory_from"] == MANDATORY_FROM.isoformat()


def test_a_rehearsal_is_warned_rather_than_refused(gh: Framework) -> None:
    items = _items(
        gh,
        _state(
            gh,
            cycle_kind="rehearsal",
            declared=True,
            mandatory=True,
            mandatory_from=MANDATORY_FROM,
            superseded_methods_in_use=("irrbb_interim_delta_eve",),
        ),
    )
    assert items["irrbb_sf_required_missing"].severity == "warning"
    assert items["irrbb_interim_not_permitted"].severity == "warning"


def test_the_interim_method_is_refused_once_the_framework_is_mandatory(gh: Framework) -> None:
    items = _items(
        gh,
        _state(
            gh,
            declared=True,
            mandatory=True,
            mandatory_from=MANDATORY_FROM,
            superseded_methods_in_use=("irrbb_interim_delta_eve",),
        ),
    )
    assert items["irrbb_interim_not_permitted"].severity == "blocking"


def test_a_refusal_replaces_the_missing_result_finding_and_names_itself(
    gh: Framework,
) -> None:
    """"Nobody has run it" and "we ran it and it refused" are different answers."""
    items = _items(
        gh,
        _state(
            gh,
            declared=True,
            mandatory=True,
            mandatory_from=MANDATORY_FROM,
            run_refusal_code="irrbb_sf_options_unsupported",
            run_refusal_message="The book holds automatic interest-rate options.",
        ),
    )
    assert "irrbb_sf_required_missing" not in items
    assert items["irrbb_sf_refused"].severity == "blocking"
    assert items["irrbb_sf_refused"].params["refusal_code"] == "irrbb_sf_options_unsupported"


def test_a_refusal_before_the_commencement_date_is_still_reported(gh: Framework) -> None:
    items = _items(
        gh,
        _state(
            gh,
            declared=True,
            mandatory=False,
            mandatory_from=date(2027, 12, 31),
            run_refusal_code="irrbb_sf_options_unsupported",
        ),
    )
    assert items["irrbb_sf_refused"].severity == "warning"


def test_assumptions_and_representative_calibrations_are_warned_with_their_counts(
    gh: Framework,
) -> None:
    items = _items(
        gh,
        _state(
            gh,
            declared=True,
            mandatory=True,
            mandatory_from=MANDATORY_FROM,
            block_usable=True,
            assumption_defaults_applied=41,
            representative_parameters=("irrbb_sf_default_cash_flow_profile",),
            outlier=True,
        ),
    )
    assert items["irrbb_sf_assumption_defaults"].severity == "warning"
    assert items["irrbb_sf_assumption_defaults"].params["applied"] == "41"
    assert items["irrbb_sf_representative_parameters"].severity == "warning"
    assert items["irrbb_outlier"].severity == "info"
    assert "irrbb_sf_required_missing" not in items


def test_assumption_counts_are_silent_until_a_result_is_actually_bound(
    gh: Framework,
) -> None:
    """A tally from a run nothing cites is not this report's disclosure."""
    items = _items(
        gh,
        _state(
            gh,
            declared=True,
            mandatory=False,
            block_usable=False,
            assumption_defaults_applied=41,
            outlier=True,
        ),
    )
    assert "irrbb_sf_assumption_defaults" not in items
    assert "irrbb_outlier" not in items
