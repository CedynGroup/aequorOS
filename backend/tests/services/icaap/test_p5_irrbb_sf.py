"""Binding the IRRBB standardised framework into ICAAP Pillar 2 (P5-DESIGN §1.6).

Four things have to be true, and each one has cost somebody something before:

* the block binds the SEALED run for the cycle's exact date, and the Pillar 2
  figure the register capitalises is the figure that run measured;
* a refused run reaches the card and the register AS A REFUSAL — under the one
  name D-061 fixed — and never as a zero, because an understated capital
  number that looks complete is worse than an honest gap;
* the commencement date is the governed row, so moving it in the console moves
  the mandate with no code change (D-024);
* resolving that row from readiness or a freeze preflight records NO parameter
  provenance (D-078): those paths seal no run, and a row they left in the
  session's ledger would be swallowed by whatever run is sealed next, inside
  the ``content_digest`` an officer signs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.domain.icaap.blocks import BlockStatus
from app.domain.icaap.pillar2 import irrbb_sf_method as sf_method
from app.models import Bank, BankReportingPeriod, RegulatoryParameter, RegulatoryRun
from app.schemas.icaap import (
    IcaapCycleRead,
    IcaapDataBlockCreate,
    IcaapDataBlockRefresh,
)
from app.schemas.icaap_risk_capital import IcaapPillar2Compute, IcaapPillar2ItemCreate
from app.schemas.regulatory_irr_sf import IrrbbSfRunCreate
from app.services import regulatory_irr_sf, regulatory_parameters
from app.services.icaap import blocks, cycles, pillar2, readiness, sf_state
from tests.api.helpers import ORG_1, USER_1
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.services.icaap.conftest import AS_OF, rehearsal_payload
from tests.services.sf_book import seed_book

CODE = regulatory_irr_sf.CODE_MANDATORY_FROM


def _seed(db: Session, *, with_options: bool = False) -> IcaapAccess:
    """The sample bank with the small framework book at the cycle's own date.

    Seeded BEFORE any cycle exists: the shared book fixture re-materialises the
    bank, so a cycle created first would be swept away with it. That is why the
    option-refusal tests take their own fixture rather than re-seeding on top of
    an existing one.
    """
    seed_book(db, with_options=with_options, as_of=AS_OF)
    db.commit()
    bank = db.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    return IcaapAccess(
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
        bank=bank,
    )


@pytest.fixture
def sf_access(db_session: Session) -> IcaapAccess:
    return _seed(db_session)


@pytest.fixture
def sf_cycle(db_session: Session, sf_access: IcaapAccess) -> IcaapCycleRead:
    return cycles.create_cycle(db_session, sf_access, rehearsal_payload())


@pytest.fixture
def options_access(db_session: Session) -> IcaapAccess:
    """A book holding an interest-rate option, which the framework refuses."""
    return _seed(db_session, with_options=True)


@pytest.fixture
def options_cycle(db_session: Session, options_access: IcaapAccess) -> IcaapCycleRead:
    return cycles.create_cycle(db_session, options_access, rehearsal_payload())


def _period_id(db: Session, access: IcaapAccess):
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == access.ctx.organization_id,
            BankReportingPeriod.bank_id == access.bank.id,
            BankReportingPeriod.period_end == AS_OF,
        )
    )
    assert period_id is not None
    return period_id


def _run_framework(db: Session, access: IcaapAccess) -> RegulatoryRun:
    read = regulatory_irr_sf.run_standardised_framework(
        db,
        access.ctx,
        access.bank.id,
        IrrbbSfRunCreate(reporting_period_id=_period_id(db, access)),
    )
    run = db.get(RegulatoryRun, read.id)
    assert run is not None
    return run


def _set_commencement(db: Session, when: date) -> None:
    """A console generation moving the governed commencement date (D-024)."""
    db.execute(
        update(RegulatoryParameter)
        .where(RegulatoryParameter.param_code == CODE)
        .values(value_json={"schema": "icaap-effective-date-v1", "date": when.isoformat()})
    )
    db.commit()


def _create_sf_item(db: Session, access: IcaapAccess, cycle: IcaapCycleRead):
    return pillar2.create_item(
        db,
        access,
        cycle.id,
        IcaapPillar2ItemCreate(
            component_key="irrbb",
            method=sf_method.METHOD,
            input_mode="bound_blocks",
            reason="Quantify interest rate risk with the standardised framework.",
        ),
    )


def _compute(db: Session, access: IcaapAccess, cycle: IcaapCycleRead, item: Any):
    return pillar2.compute_item(
        db,
        access,
        cycle.id,
        item.id,
        IcaapPillar2Compute(
            base_revision_no=item.current_revision_no,
            reason="Read the standardised framework result.",
        ),
    )


def _codes(report: Any) -> dict[str, Any]:
    return {entry.code: entry for entry in report.items}


# --- the block --------------------------------------------------------------


def test_the_block_binds_the_sealed_framework_run_for_the_cycles_own_date(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    run = _run_framework(db_session, sf_access)
    assert run.status == "succeeded", (run.error_code, run.error_message)

    block = blocks.create_block(
        db_session, sf_access, sf_cycle.id, IcaapDataBlockCreate(block_type="irrbb_sf")
    )

    assert block.status == BlockStatus.FRESH.value
    binding = block.current_binding
    assert binding is not None
    assert binding.source_kind == "run"
    assert binding.source_as_of == AS_OF
    assert binding.source_key == f"run:{run.id}"
    assert binding.source_ref["input_hash"] == run.input_hash
    facts = binding.facts
    assert facts["eve_risk_measure"].value is not None
    assert facts["tier1"].value is not None
    assert facts["outlier"].value in {"true", "false"}
    # The mandate the RUN recorded, not one re-resolved on the read path.
    assert facts["sf_mandatory"].value in {"true", "false"}


def test_the_block_payload_keeps_every_assumption_and_its_count(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    """A default applied forty times is not the same exposure as one applied once."""
    _run_framework(db_session, sf_access)
    block = blocks.create_block(
        db_session, sf_access, sf_cycle.id, IcaapDataBlockCreate(block_type="irrbb_sf")
    )
    binding = block.current_binding
    assert binding is not None
    raw = (binding.payload or {})["raw"]["irrbb_sf"]
    tallies = raw["assumption_tallies"]
    assert tallies, "the small fixture book applies at least one modelling default"
    assert all(isinstance(count, int) and count > 0 for count in tallies.values())
    total = int(binding.facts["assumption_defaults_applied"].value or 0)
    assert total == sum(tallies.values())
    table = next(
        entry
        for entry in (binding.payload or {})["tables"]
        if entry["key"] == "assumption_tallies"
    )
    # Production copy, never a raw marker, on a surface a bank reads.
    assert all(row["cells"]["assumption"] not in tallies for row in table["rows"])


def test_a_refused_run_reaches_the_card_in_words_and_binds_nothing(
    db_session: Session, options_access: IcaapAccess, options_cycle: IcaapCycleRead
) -> None:
    """DV-010 / D-061: one name, and never an understated economic value."""
    run = _run_framework(db_session, options_access)
    assert run.status == "failed"
    assert run.error_code == "irrbb_sf_options_unsupported"

    block = blocks.create_block(
        db_session, options_access, options_cycle.id, IcaapDataBlockCreate(block_type="irrbb_sf")
    )
    assert block.status == BlockStatus.UNBOUND.value
    assert block.current_binding is None

    refreshed = blocks.refresh_block(
        db_session,
        options_access,
        options_cycle.id,
        block.id,
        IcaapDataBlockRefresh(reason="Link the standardised framework result."),
    )
    assert refreshed.outcome == "unavailable"
    message = refreshed.reason or ""
    assert "interest-rate options" in message
    assert "understate" in message
    assert "irrbb_sf_options_unsupported" not in message, "a bank reads sentences, not codes"


# --- the Pillar 2 method ----------------------------------------------------


def test_the_pillar2_figure_is_the_figure_the_run_measured(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    run = _run_framework(db_session, sf_access)
    blocks.create_block(
        db_session, sf_access, sf_cycle.id, IcaapDataBlockCreate(block_type="irrbb_sf")
    )
    item = _create_sf_item(db_session, sf_access, sf_cycle)

    computed = _compute(db_session, sf_access, sf_cycle, item)

    assert computed.method_status == "computed"
    measure = Decimal((run.metrics or {})["measures"]["outlier_set"]["measure"])
    assert computed.baseline_amount == measure.quantize(Decimal("0.0001"))
    assert computed.basis == "absolute"
    detail = (computed.computation or {})["detail"]
    assert detail["sf_run_id"] == str(run.id)
    assert detail["sf_input_hash"] == run.input_hash
    assert detail["sf_eve_risk_measure"] == str(measure)


def test_the_refusal_reaches_the_register_as_a_refusal_not_a_zero(
    db_session: Session, options_access: IcaapAccess, options_cycle: IcaapCycleRead
) -> None:
    _run_framework(db_session, options_access)
    item = _create_sf_item(db_session, options_access, options_cycle)

    computed = _compute(db_session, options_access, options_cycle, item)

    assert computed.method_status == "not_computable"
    assert computed.baseline_amount is None, "a refusal must never present an amount"
    reasons = (computed.computation or {})["reasons"]
    # D-061: ONE name, carried from the engine to the register with no mapping
    # table and no second spelling.
    assert reasons == ["standardised_framework_refused:irrbb_sf_options_unsupported"]
    assert computed.status_detail is not None
    assert "interest-rate options" in computed.status_detail
    assert "irrbb_sf_options_unsupported" not in computed.status_detail


def test_a_framework_item_with_no_linked_result_says_so_rather_than_guessing(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    item = _create_sf_item(db_session, sf_access, sf_cycle)
    computed = _compute(db_session, sf_access, sf_cycle, item)
    assert computed.method_status == "not_computable"
    assert computed.baseline_amount is None
    assert "binding_missing:irrbb_sf" in (computed.computation or {})["reasons"]
    assert computed.status_detail is not None
    assert "IRRBB standardised framework is not linked to this ICAAP" in computed.status_detail


def test_the_items_representative_calibrations_are_named_for_the_reader(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    """M14: a figure resting on a representative row has to keep saying so."""
    run = _run_framework(db_session, sf_access)
    representative = (run.metrics or {})["representative_parameters"]
    assert representative, "the fixture book leans on at least one representative row"
    blocks.create_block(
        db_session, sf_access, sf_cycle.id, IcaapDataBlockCreate(block_type="irrbb_sf")
    )
    item = _create_sf_item(db_session, sf_access, sf_cycle)
    computed = _compute(db_session, sf_access, sf_cycle, item)

    # The item names every representative row the RUN rested on, with its
    # governed provenance, so nothing that shaped the figure is invisible.
    named = {row.param_code for row in computed.parameters}
    assert set(representative) <= named
    detail = (computed.computation or {})["detail"]
    assert detail["representative_parameters"] == ", ".join(sorted(representative))

    # And readiness says so in a sentence, driven by the run's own list rather
    # than by the register's citation-prefix convention — see P5-D's report for
    # why those two definitions of "representative" do not agree today.
    report = readiness.get_readiness(db_session, sf_access, sf_cycle.id)
    entry = _codes(report)["irrbb_sf_representative_parameters"]
    assert entry.severity == "warning"
    assert all(code in entry.message for code in representative)


# --- the mandate ------------------------------------------------------------


def test_before_the_commencement_date_the_interim_method_is_still_fine(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    _set_commencement(db_session, date(AS_OF.year + 1, 12, 31))
    report = readiness.get_readiness(db_session, sf_access, sf_cycle.id)
    codes = _codes(report)
    assert "irrbb_interim_not_permitted" not in codes
    assert "irrbb_sf_required_missing" not in codes


def test_from_the_commencement_date_the_framework_is_required(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    """The date is the governed row, so a console edit moves the rule (D-024)."""
    _set_commencement(db_session, date(AS_OF.year - 1, 12, 31))
    report = readiness.get_readiness(db_session, sf_access, sf_cycle.id)
    codes = _codes(report)
    assert "irrbb_sf_required_missing" in codes
    message = codes["irrbb_sf_required_missing"].message
    assert f"{AS_OF.year - 1}-12-31" in message
    assert "Run the framework" in message


def test_the_interim_method_is_refused_once_the_framework_is_mandatory(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    pillar2.create_item(
        db_session,
        sf_access,
        sf_cycle.id,
        IcaapPillar2ItemCreate(
            component_key="irrbb",
            method="irrbb_interim_delta_eve",
            input_mode="manual_with_evidence",
            rationale="Group ALM figures.",
            reason="Quantify interest rate risk in the banking book.",
        ),
    )
    _set_commencement(db_session, date(AS_OF.year - 1, 12, 31))

    report = readiness.get_readiness(db_session, sf_access, sf_cycle.id)
    entry = _codes(report)["irrbb_interim_not_permitted"]
    # This cycle is a rehearsal, which is never filed, so the same gap is a
    # warning here and blocking for anything a regulator would receive.
    assert entry.severity == "warning"
    assert f"{AS_OF.year - 1}-12-31" in entry.message

    from app.services.icaap import guards, pillar2_irrbb_hook  # noqa: PLC0415

    cycle_row = guards.get_cycle_or_404(db_session, sf_access, sf_cycle.id)
    assert pillar2_irrbb_hook.interim_blocking(db_session, sf_access, cycle_row) is None
    cycle_row.cycle_kind = "annual"
    assert (
        pillar2_irrbb_hook.interim_blocking(db_session, sf_access, cycle_row)
        == pillar2_irrbb_hook.INTERIM_NOT_PERMITTED
    )


def test_a_framework_with_no_method_mandate_asserts_nothing(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    """NG and KE declare no mandate, so a Ghanaian row can never block them."""
    from app.domain.icaap.frameworks import registry  # noqa: PLC0415

    others = [
        framework
        for framework in registry.all_frameworks()
        if not framework.code.startswith("bog_")
    ]
    assert others, "the registry publishes at least one non-Ghanaian framework"
    for framework in others:
        decisions = sf_state.method_mandates(
            db_session, sf_access.bank, framework, as_of=AS_OF, today=date.today()
        )
        assert decisions == (), framework.code


# --- the mandate, on the register the Pillar 2 card reads (GAP-4 item 3) -----
#
# The card used to point at readiness, because the commencement rule lived only
# on a SUCCEEDED run's read model — so a bank that had not run the framework
# could not be told whether it had to. The register now carries the mandate, as
# the server's own sentence, resolved through the one non-recording seam.


def _mandate_finding(db_session: Session, access: IcaapAccess, cycle_id: Any) -> Any:
    register = pillar2.get_register(db_session, access, cycle_id)
    return next(
        (
            finding
            for finding in register.findings
            if finding.code == pillar2.MANDATE_FINDING
            and finding.params.get("method") == sf_method.METHOD
        ),
        None,
    )


def test_the_register_states_the_mandate_before_the_commencement_date(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    _set_commencement(db_session, date(AS_OF.year + 1, 12, 31))

    finding = _mandate_finding(db_session, sf_access, sf_cycle.id)

    assert finding is not None
    assert finding.params["mandatory"] == "false"
    assert finding.params["mandatory_from"] == f"{AS_OF.year + 1}-12-31"
    assert finding.params["as_of"] == AS_OF.isoformat()
    assert finding.params["param_code"] == CODE
    # The sentence is the SERVER's, the same one the IRRBB workspace prints.
    assert finding.params["statement"] == (
        "The Standardised Framework becomes mandatory for reporting dates from "
        f"{AS_OF.year + 1}-12-31. This date is pending confirmation with the supervisor."
    )


def test_the_register_states_the_mandate_once_it_bites(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    """No run has succeeded for this cycle, which is exactly the gap."""
    _set_commencement(db_session, date(AS_OF.year - 1, 12, 31))

    finding = _mandate_finding(db_session, sf_access, sf_cycle.id)

    assert finding is not None
    assert finding.params["mandatory"] == "true"
    assert finding.params["statement"].startswith(
        "The Standardised Framework applies to reporting dates from "
        f"{AS_OF.year - 1}-12-31."
    )
    # A console edit moves the rule, with no code change (D-024).
    _set_commencement(db_session, date(AS_OF.year + 5, 12, 31))
    moved = _mandate_finding(db_session, sf_access, sf_cycle.id)
    assert moved is not None
    assert moved.params["mandatory"] == "false"


def test_the_register_reading_the_mandate_records_no_provenance(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    """D-078. A governed row resolved on a dispatch path must not be credited
    to whichever run seals next — that is what moved ``content_digest``."""
    regulatory_parameters.consume_parameter_provenance(db_session)

    assert _mandate_finding(db_session, sf_access, sf_cycle.id) is not None

    recorded = {
        entry["param_code"]
        for entry in regulatory_parameters.consume_parameter_provenance(db_session)
    }
    assert CODE not in recorded


def test_a_framework_that_declares_no_mandate_puts_nothing_on_the_register(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    """Silence is correct where nothing is declared — never "not required"."""
    from app.domain.icaap.frameworks import registry  # noqa: PLC0415
    from app.services.icaap import guards  # noqa: PLC0415

    other = next(
        framework
        for framework in registry.all_frameworks()
        if not framework.code.startswith("bog_")
    )
    cycle_row = guards.get_cycle_or_404(db_session, sf_access, sf_cycle.id)
    findings = pillar2._mandate_findings(  # noqa: SLF001 - the unit under test
        db_session, sf_access, cycle_row, other
    )

    assert findings == []


# --- D-078: dispatch reads record no provenance ------------------------------


def test_a_readiness_check_records_no_parameter_provenance(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    """D-078: the dispatch plane must not write into the calculation plane's ledger.

    The check is deliberately narrow — the commencement row specifically — so
    it cannot be satisfied by an unrelated part of readiness happening to drain
    the ledger first, and it fails the moment somebody calls ``sf_mandate``
    from a dispatch path without ``record=False``.
    """
    regulatory_parameters.consume_parameter_provenance(db_session)

    readiness.get_readiness(db_session, sf_access, sf_cycle.id)

    recorded = {
        entry["param_code"]
        for entry in regulatory_parameters.consume_parameter_provenance(db_session)
    }
    assert CODE not in recorded


def test_a_freeze_preflight_records_no_commencement_row_either(
    db_session: Session, sf_access: IcaapAccess, sf_cycle: IcaapCycleRead
) -> None:
    from app.services.icaap import freeze, guards  # noqa: PLC0415

    cycle_row = guards.get_cycle_or_404(db_session, sf_access, sf_cycle.id)
    regulatory_parameters.consume_parameter_provenance(db_session)

    freeze.freeze_preflight(db_session, sf_access, cycle_row)

    recorded = {
        entry["param_code"]
        for entry in regulatory_parameters.consume_parameter_provenance(db_session)
    }
    assert CODE not in recorded


def test_the_mandate_seam_still_records_when_a_calculation_asks_for_it(
    db_session: Session, sf_access: IcaapAccess
) -> None:
    """The run that seals immediately after IS the one that owns the row."""
    regulatory_parameters.consume_parameter_provenance(db_session)
    regulatory_irr_sf.sf_mandate(
        db_session, sf_access.bank, as_of=AS_OF, today=date.today(), record=True
    )
    recorded = {
        entry["param_code"]
        for entry in regulatory_parameters.consume_parameter_provenance(db_session)
    }
    assert CODE in recorded


def test_the_framework_run_carries_the_commencement_row_in_its_own_provenance(
    db_session: Session, sf_access: IcaapAccess
) -> None:
    run = _run_framework(db_session, sf_access)
    codes = {entry["param_code"] for entry in run.parameter_provenance or []}
    assert CODE in codes


# --- the reconciliation explanation code (D-015) -----------------------------


def _control(**values: Any) -> SimpleNamespace:
    body: dict[str, Any] = {
        "comparison_key": "stressed:stress_overlay:irrbb",
        "basis": "stressed",
        "comparator": "stress_overlay",
        "row": "irrbb",
        "status": "inconsistent",
        "explanation": None,
        "explanation_current": False,
    }
    body.update(values)
    return SimpleNamespace(**body)


def _register(method: str, control: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        items=[SimpleNamespace(method=method, table5_row="irrbb")],
        consistency=[control],
    )


def test_the_stress_overlay_difference_is_labelled_a_method_difference() -> None:
    """D-015: the annex uses the earlier engine, the ICAAP uses the framework.

    Those two cannot agree and are not meant to. Calling it a break would train
    preparers to explain away the control that catches real ones.
    """
    from app.services.icaap import readiness_p2  # noqa: PLC0415

    found = readiness_p2._consistency_findings(  # noqa: SLF001 - the rule under test
        _register(sf_method.METHOD, _control())
    )
    assert [entry.code for entry in found] == ["irrbb_method_difference"]
    assert found[0].severity == "warning"


def test_the_same_difference_under_the_interim_method_is_still_a_break() -> None:
    """Both sides then come from the SAME engine, so a gap is a real gap."""
    from app.services.icaap import readiness_p2  # noqa: PLC0415

    found = readiness_p2._consistency_findings(  # noqa: SLF001 - the rule under test
        _register("irrbb_interim_delta_eve", _control())
    )
    assert [entry.code for entry in found] == ["source_consistency_unexplained"]
    assert found[0].severity == "blocking"


def test_a_recorded_explanation_still_answers_the_control() -> None:
    from app.services.icaap import readiness_p2  # noqa: PLC0415

    found = readiness_p2._consistency_findings(  # noqa: SLF001 - the rule under test
        _register(
            sf_method.METHOD,
            _control(explanation="Measured on different engines.", explanation_current=True),
        )
    )
    assert found == []


def test_a_baseline_difference_is_never_excused_by_the_method_difference() -> None:
    """The overlay is a STRESSED comparison; the baseline sides must still agree."""
    from app.services.icaap import readiness_p2  # noqa: PLC0415

    found = readiness_p2._consistency_findings(  # noqa: SLF001 - the rule under test
        _register(
            sf_method.METHOD,
            _control(
                comparison_key="baseline:capital_plan:irrbb",
                basis="baseline",
                comparator="capital_plan",
            ),
        )
    )
    assert [entry.code for entry in found] == ["source_consistency_unexplained"]


def test_the_method_difference_reads_as_something_to_record_not_to_correct() -> None:
    from app.services.icaap import readiness_p2  # noqa: PLC0415

    message = readiness_p2.MESSAGES["irrbb_method_difference"]
    assert "differ because the methods differ" in message
    assert "nothing needs correcting" in message
