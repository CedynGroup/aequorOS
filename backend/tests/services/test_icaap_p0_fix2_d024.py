"""ICAAP P0 fix round 2 — founder directive D-024: every regulatory number comes
from the console.

* **Pending is informational** (item 1). ``cet1_min`` / ``tier1_min`` ship
  ``confirmation_status='pending'``; the value still resolves, clamps, computes
  and files, and every output that shows it says "pending confirmation" (item 2).
* **A console change flows through with no code change** (item 3). A newer
  generation proposed and approved through the operator service the console
  calls (maker, then a different checker) moves the capital-plan headroom, the
  enterprise-stress minima and the Appendix II report notes; one that starts
  after the as-of moves nothing.
* **The AT1 / Tier 2 recognition caps are governed** (item 5, audit M21): Appendix
  II Table 2 and the management-action overlay take them from the control plane,
  a Basel build without them refuses, and a changed cap changes Table 2.
"""

from __future__ import annotations

import io
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pdfplumber
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.authority.outcomes import NotComputable, OutcomeState
from app.domain.stress.appendix_ii import build_appendix_ii
from app.domain.stress.management_actions import (
    ActionTrigger,
    ManagementAction,
    ManagementActionNotComputable,
    ManagementActionPlan,
    RecognitionCaps,
    apply_management_actions,
)
from app.domain.stress.projection import (
    EnterpriseProjectionInputs,
    project_enterprise,
)
from app.models import (
    Bank,
    BankReportingPeriod,
    RegulatoryPackage,
    RegulatoryParameter,
    RegulatoryRun,
)
from app.operator.services import regulatory_parameters as console
from app.schemas.operator import RegulatoryParameterProposeRequest
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services import capital_plan, regulatory_capital, regulatory_parameters
from app.services.enterprise_stress import EnterpriseStressError
from app.services.regulatory_reporting import generation
from app.services.regulatory_reporting.exports import export_package
from app.services.regulatory_reporting.templates import report_notes
from tests.domain.stress_fixtures import (
    BASE_ASSUMPTIONS,
    bog_capital_params,
    bog_forecast_params,
    sample_bank_latest_facts,
    severe_paths,
)
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
    set_board_threshold,
)
from tests.services.test_capital_plan import _run_forecast
from tests.services.test_icaap_stress_appendix2_report import (
    MAKER,
    REPORTING_DATE,
    _approved_scenario,
    _attested_signoff,
    _generate,
    _run_enterprise_stress,
    _seed_checker,
    storage,
)
from tests.storage.inmemory import InMemoryStorageClient

__all__ = ["storage"]

# The enterprise stress run this suite prepares requires scoped FX and IRRBB
# calculation authority (an unconditional IRRBB check landed on the run
# service), so it takes the same two fixtures as the suite it borrows
# ``_run_enterprise_stress`` from.
pytestmark = pytest.mark.usefixtures("fx_run_authority", "irrbb_run_authority")

#: The date the console action is taken on in the flow-through tests: before
#: the fixture's as-of (2026-03-31), so a generation effective from 2026-01-01 is
#: a forward-dated change the console accepts (it refuses a past date).
CONSOLE_DAY = date(2025, 12, 1)
CHANGE_EFFECTIVE = date(2026, 1, 1)


class _ConsoleDay(date):
    @classmethod
    def today(cls) -> _ConsoleDay:  # type: ignore[override]
        return cls(CONSOLE_DAY.year, CONSOLE_DAY.month, CONSOLE_DAY.day)


def _bank(db: Session) -> Bank:
    bank = db.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    return bank


def _console_change(db: Session, code: str, value: str, effective_from: date) -> None:
    """Propose → approve through the operator service the console calls."""
    draft = console.propose(
        db,
        payload=RegulatoryParameterProposeRequest(
            scope_type="institution_class",
            scope_key="bank",
            param_code=code,
            jurisdiction_code="GH",
            value_numeric=Decimal(value),
            unit="percent",
            source_citation="Stakeholder-confirmed value (test)",
            confirmation_status="confirmed",
            effective_from=effective_from,
            change_rationale="Stakeholders confirmed the conservation-buffer treatment.",
        ),
        proposed_by="maker@aequoros.example",
    )
    approved = console.approve(
        db,
        param_id=draft.id,
        approved_by="checker@aequoros.example",
        change_rationale="Four-eyes approval.",
    )
    assert approved.status == "approved"
    db.commit()


def _thresholds(run: RegulatoryRun) -> dict[str, Decimal]:
    capital = run.inputs["parameters"]["capital"]["thresholds_pct"]
    return {code: Decimal(str(value)) for code, value in capital.items()}


def _pdf_text(payload: bytes) -> str:
    with pdfplumber.open(io.BytesIO(payload)) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)
    return " ".join(text.split())


def _export_pdf(
    db: Session, storage_client: InMemoryStorageClient, package: RegulatoryPackage
) -> str:
    artifact = export_package(db, MAKER, package, "pdf")
    slug = db.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    for obj in storage_client.list(slug, "outputs"):
        if obj.location.object_path == artifact.object_path:
            return _pdf_text(storage_client.read(obj.location)[1].read())
    raise AssertionError(artifact.object_path)


def _stress_package(db: Session) -> tuple[RegulatoryRun, RegulatoryPackage]:
    _seed_checker(db)
    run_id = _run_enterprise_stress(db, _approved_scenario(db))
    _attested_signoff(db, run_id)
    run = db.get(RegulatoryRun, run_id)
    assert run is not None
    return run, _generate(db)


def _period_id(db: Session) -> UUID:
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    return period_id


# --- item 1: pending is informational only ------------------------------------------


def test_cet1_and_tier1_are_seeded_pending_with_the_confirmation_note(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    for code, value in (("cet1_min", "6.5"), ("tier1_min", "8")):
        resolved = regulatory_parameters.resolve(db_session, bank, code, as_of=REPORTING_DATE)
        assert resolved.decimal == Decimal(value)
        assert resolved.is_pending
        assert (
            "pending stakeholder confirmation of the conservation-buffer treatment"
            in resolved.source_citation
        )
        assert "M20" not in resolved.source_citation  # D-042: no internal reference printed
    leverage = regulatory_parameters.resolve(db_session, bank, "leverage_min", as_of=REPORTING_DATE)
    assert leverage.confirmation_status == "confirmed"


def test_a_pending_minimum_blocks_no_calculation_or_return(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """Resolution, the capital run, the forecast-based capital plan, the ICAAP
    stress run and both returns that show the minima all complete; pending is
    stated, never enforced."""
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    report = regulatory_parameters.clamp_overrides(
        db_session, bank, {"cet1_min": Decimal("5")}, as_of=REPORTING_DATE
    )
    assert report.values["cet1_min"] == Decimal("6.5")  # a pending floor still clamps

    capital_run = regulatory_capital.create_capital_run(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=_period_id(db_session), scenario_code="baseline"
        ),
    )
    assert capital_run.status == "succeeded"
    car_rwa = generation.generate_package(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="CAR-RWA", reporting_date=REPORTING_DATE),
    )
    car_row = db_session.get(RegulatoryPackage, car_rwa.id)
    assert car_row is not None
    notes = report_notes(car_row.snapshot)
    assert any(
        "minimum CET1 ratio of 6.5% is governed parameter cet1_min, pending confirmation" in n
        for n in notes
    ), notes
    assert any("minimum Tier 1 ratio of 8% is governed parameter tier1_min" in n for n in notes)
    assert not any("leverage" in n or "total capital" in n for n in notes)
    assert "pending confirmation" in _export_pdf(db_session, storage, car_row)

    _run_forecast(db_session)
    summary = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID)
    assert summary.projection is not None and summary.projection_unavailable is None
    assert summary.projection.cet1_min is not None
    assert summary.projection.cet1_min.confirmation_status == "pending"
    assert summary.projection.tier1_min is not None
    assert summary.projection.tier1_min.confirmation_status == "pending"
    assert summary.projection.pillar1_min.confirmation_status == "confirmed"

    run, package = _stress_package(db_session)
    assert run.status == "succeeded"
    assert package.status == "generated"
    findings = package.snapshot["metadata"].get("generation_findings", [])
    assert not [f for f in findings if f.get("severity") == "ERROR"], findings
    text = _export_pdf(db_session, storage, package)
    assert "Minimum CET1 ratio applied: 6.5% (pending confirmation)." in text
    assert "Minimum Tier 1 ratio applied: 8% (pending confirmation)." in text
    assert "Minimum total capital ratio applied: 13%." in text  # confirmed: no label
    assert "Minimum leverage ratio applied: 6%." in text


def test_the_capital_dashboard_states_which_minima_await_confirmation(
    db_session: Session,
) -> None:
    """The Basel overview's bars read their minima from the capital dashboard;
    it carries the governed confirmation status beside them."""
    materialize_canonical_test_book(db_session)
    dashboard = regulatory_capital.get_capital_dashboard(
        db_session, MAKER, SAMPLE_BANK_ID, _period_id(db_session)
    )
    buffers = dashboard.buffers
    assert buffers.cet1_min_pct == Decimal("6.5")
    assert buffers.minimum_confirmation_status == {
        "car_min": "confirmed",
        "cet1_min": "pending",
        "tier1_min": "pending",
        "leverage_min": "confirmed",
    }


# --- item 3: a console change flows through ------------------------------------------


@pytest.mark.parametrize(
    ("code", "value", "label", "headroom_field", "floor_field"),
    [
        ("cet1_min", "9.5", "Minimum CET1 ratio", "cet1_headroom_pp", "cet1_min"),
        ("tier1_min", "11", "Minimum Tier 1 ratio", "tier1_headroom_pp", "tier1_min"),
        ("leverage_min", "7.5", "Minimum leverage ratio", None, None),
    ],
)
def test_a_console_change_reaches_every_consumer_without_a_code_change(  # noqa: PLR0913
    db_session: Session,
    storage: InMemoryStorageClient,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    value: str,
    label: str,
    headroom_field: str | None,
    floor_field: str | None,
) -> None:
    materialize_canonical_test_book(db_session)
    monkeypatch.setattr(console, "date", _ConsoleDay)
    _console_change(db_session, code, value, CHANGE_EFFECTIVE)
    expected = Decimal(value)

    # The capital plan's minimum and headroom (projection as-of 2026-03-31).
    _run_forecast(db_session)
    projection = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID).projection
    assert projection is not None
    if floor_field is not None:
        floor = getattr(projection, floor_field)
        assert floor is not None and floor.value_pct == expected
        assert floor.confirmation_status == "confirmed"
        ratio_field = headroom_field.replace("_headroom_pp", "_pct") if headroom_field else ""
        checked = 0
        for scenario in projection.scenarios:
            for year in scenario.years:
                ratio = getattr(year, ratio_field)
                if ratio is not None:
                    assert getattr(year, headroom_field or "") == ratio - expected
                    checked += 1
        assert checked

    # The enterprise-stress minima and the Appendix II report note.
    run, package = _stress_package(db_session)
    assert _thresholds(run)[code] == expected
    governed_rows = [
        entry
        for entry in run.parameter_provenance or []
        if entry["param_code"] == code and entry["effective_from"] == CHANGE_EFFECTIVE.isoformat()
    ]
    assert governed_rows, "the run's provenance names the console generation"
    provenance = {e["param_code"]: e for e in package.snapshot["metadata"]["parameter_provenance"]}
    assert Decimal(str(provenance[code]["applied_value"])) == expected
    assert provenance[code]["basis"] == "governed"
    assert provenance[code]["governed"]["effective_from"] == CHANGE_EFFECTIVE.isoformat()
    text = _export_pdf(db_session, storage, package)
    shown = f"{Decimal(value).normalize():f}%"
    assert f"{label} applied: {shown}." in text
    assert f"Governed parameter {code} = {shown}" in text


def _cet1_everywhere(db: Session) -> tuple[Decimal, Decimal]:
    """The CET1 minimum the capital plan and a fresh ICAAP stress run apply."""
    _run_forecast(db)
    projection = capital_plan.get_capital_plan(db, MAKER, SAMPLE_BANK_ID).projection
    assert projection is not None and projection.cet1_min is not None
    _seed_checker(db)
    run_id = _run_enterprise_stress(db, _approved_scenario(db, code=f"cet1_{uuid4().hex[:8]}"))
    run = db.get(RegulatoryRun, run_id)
    assert run is not None
    return projection.cet1_min.value_pct, _thresholds(run)["cet1_min"]


def test_lowering_a_value_in_the_console_reaches_a_tenant_without_a_board_row(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Founder directive D-042: the register carries no governed minimum of its
    own, so the console decides in BOTH directions. Raised to 9.5 and then lowered
    to 7, CET1 is 7 for a tenant with no board row; a board row of 8 is stricter
    than 7 and stands."""
    materialize_canonical_test_book(db_session)
    monkeypatch.setattr(console, "date", _ConsoleDay)
    _console_change(db_session, "cet1_min", "9.5", date(2026, 1, 1))
    _console_change(db_session, "cet1_min", "7", date(2026, 2, 1))

    assert _cet1_everywhere(db_session) == (Decimal("7"), Decimal("7"))

    set_board_threshold(db_session, "cet1_min", "8")
    db_session.commit()
    plan_floor, stress_floor = _cet1_everywhere(db_session)
    assert (plan_floor, stress_floor) == (Decimal("8"), Decimal("8"))
    projection = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID).projection
    assert projection is not None and projection.cet1_min is not None
    assert projection.cet1_min.source == "board_register"
    assert projection.cet1_min.regulatory_value_pct == Decimal("7")


def test_a_governed_minimum_missing_from_the_console_refuses_at_calculation_time(
    db_session: Session,
) -> None:
    """D-042: nothing is seeded to fall back on, so a governed minimum the
    control plane cannot supply refuses the calculation with a typed
    ``missing_parameter`` — never a literal."""
    materialize_canonical_test_book(db_session)
    for row in db_session.scalars(
        select(RegulatoryParameter).where(RegulatoryParameter.param_code == "cet1_min")
    ).all():
        db_session.delete(row)
    db_session.commit()
    run = regulatory_capital.create_capital_run(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="capital",
            reporting_period_id=_period_id(db_session),
            scenario_code="baseline",
        ),
    )
    # The capital run persists its refusal on the run row (never a 500).
    assert run.status == "failed"
    assert run.error is not None and run.error.code == "missing_parameter"
    assert "cet1_min" in (run.error.details or {})["threshold_codes"]
    _seed_checker(db_session)
    with pytest.raises(EnterpriseStressError) as stress:
        _run_enterprise_stress(db_session, _approved_scenario(db_session))
    stress_detail = cast("dict[str, Any]", stress.value.detail)
    assert stress_detail["error_code"] == "missing_parameter"
    assert "cet1_min" in stress_detail["details"]["threshold_codes"]


def test_a_console_change_effective_after_the_as_of_is_not_applied(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    materialize_canonical_test_book(db_session)
    _console_change(db_session, "cet1_min", "9.5", date(2026, 12, 1))

    _run_forecast(db_session)
    projection = capital_plan.get_capital_plan(db_session, MAKER, SAMPLE_BANK_ID).projection
    assert projection is not None and projection.cet1_min is not None
    assert projection.cet1_min.value_pct == Decimal("6.5")
    assert projection.cet1_min.confirmation_status == "pending"

    run, package = _stress_package(db_session)
    assert _thresholds(run)["cet1_min"] == Decimal("6.5")
    text = _export_pdf(db_session, storage, package)
    assert "Minimum CET1 ratio applied: 6.5% (pending confirmation)." in text
    assert "cet1_min = 9.5%" not in text


# --- item 5: the AT1 / Tier 2 recognition caps are governed (M21) --------------------


def _projection() -> Any:
    return project_enterprise(
        EnterpriseProjectionInputs(
            scenario_code="severe",
            scenario_paths=severe_paths(),
            facts=sample_bank_latest_facts(),
            params=bog_forecast_params(),
            plan=BASE_ASSUMPTIONS,
            horizon_years=3,
        )
    )


def test_table2_recognises_capital_up_to_the_governed_caps() -> None:
    projection = _projection()
    tight = build_appendix_ii(
        projection,
        severe_paths(),
        currency="GHS",
        car_target_pct=Decimal("13"),
        recognition_caps=RecognitionCaps(at1_pct_rwa=Decimal("0.1"), tier2_pct_rwa=Decimal("0.1")),
    )
    loose = build_appendix_ii(
        projection,
        severe_paths(),
        currency="GHS",
        car_target_pct=Decimal("13"),
        recognition_caps=RecognitionCaps(at1_pct_rwa=Decimal("50"), tier2_pct_rwa=Decimal("50")),
    )
    for row_tight, row_loose in zip(
        tight.table2_capital.rows, loose.table2_capital.rows, strict=True
    ):
        assert row_tight.at1_cap < row_loose.at1_cap
        assert row_tight.tier2_cap < row_loose.tier2_cap
        assert row_tight.at1_eligible == min(row_tight.at1_nominal, row_tight.at1_cap)
        assert row_tight.tier2_eligible == min(row_tight.tier2_nominal, row_tight.tier2_cap)


def test_a_basel_table2_without_governed_caps_refuses() -> None:
    with pytest.raises(NotComputable) as caught:
        build_appendix_ii(
            _projection(),
            severe_paths(),
            currency="GHS",
            car_target_pct=Decimal("13"),
            recognition_caps=None,
        )
    assert caught.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert caught.value.details[0].items == ("param:at1_cap_pct_rwa", "param:tier2_cap_pct_rwa")


def test_an_at1_raise_without_a_recognition_cap_refuses() -> None:
    plan = ManagementActionPlan(
        plan_id="p1",
        name="AT1 issuance plan",
        actions=(
            ManagementAction(
                action_id="a1",
                kind="raise_capital",
                label="AT1 issuance",
                trigger=ActionTrigger(kind="always"),
                capital_raise_ghs=Decimal("1000000"),
                capital_raise_tier="at1",
                effective_year=1,
            ),
        ),
    )
    with pytest.raises(ManagementActionNotComputable) as caught:
        apply_management_actions(
            _projection(),
            plan,
            severity="severe",
            capital_params=bog_capital_params(),
            paid_up_min=Decimal("0"),
            car_target_pct=Decimal("13"),
            recognition_caps=None,
        )
    assert caught.value.code == "recognition_cap_unresolved"


def test_a_stress_run_records_and_applies_the_governed_caps(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _seed_checker(db_session)
    run_id = _run_enterprise_stress(db_session, _approved_scenario(db_session))
    run = db_session.get(RegulatoryRun, run_id)
    assert run is not None
    caps = run.inputs["recognition_caps_pct_rwa"]
    assert caps == {"at1_cap_pct_rwa": "1.5", "tier2_cap_pct_rwa": "2"}
    assert {"at1_cap_pct_rwa", "tier2_cap_pct_rwa"} <= {
        entry["param_code"] for entry in run.parameter_provenance or []
    }
    rows = run.metrics["appendix_ii"]["table2_capital"]
    assert rows
    for row in rows:
        rwa = Decimal(str(row["total_rwa"]))  # GHS'000, rounded to three places
        tolerance = Decimal("0.002")
        assert abs(Decimal(str(row["at1_cap"])) - rwa * Decimal("1.5") / 100) <= tolerance
        assert abs(Decimal(str(row["tier2_cap"])) - rwa * Decimal("2") / 100) <= tolerance


def test_an_sdi_plan_adding_at1_and_tier2_behaves_as_before_p0(db_session: Session) -> None:
    """D-042: the SDI class carries the caps the platform applied as literals
    before 2026-09-19 (1.5 / 2, pending), so an SDI plan that adds AT1 and Tier 2
    capital produces exactly the pre-P0 result."""
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    bank.institution_type = "savings_and_loans"
    db_session.flush()
    resolved = {
        code: regulatory_parameters.resolve(db_session, bank, code, as_of=REPORTING_DATE)
        for code in ("at1_cap_pct_rwa", "tier2_cap_pct_rwa")
    }
    assert all(param.is_pending for param in resolved.values())
    governed = RecognitionCaps(
        at1_pct_rwa=resolved["at1_cap_pct_rwa"].decimal,
        tier2_pct_rwa=resolved["tier2_cap_pct_rwa"].decimal,
    )
    #: The literals ``management_actions`` applied to every institution before P0.
    pre_p0 = RecognitionCaps(at1_pct_rwa=Decimal("1.5"), tier2_pct_rwa=Decimal("2"))
    plan = ManagementActionPlan(
        plan_id="sdi-tiers",
        name="Hybrid and subordinated issuance",
        actions=tuple(
            ManagementAction(
                action_id=f"raise_{tier}",
                kind="raise_capital",
                label=f"{tier} issuance",
                trigger=ActionTrigger(kind="always"),
                capital_raise_ghs=Decimal("1000000000000"),
                capital_raise_tier=tier,  # type: ignore[arg-type]
            )
            for tier in ("at1", "tier2")
        ),
    )
    sdi_params = replace(bog_capital_params(), basel_applicable=False)

    def overlay(caps: RecognitionCaps) -> dict[str, object]:
        return apply_management_actions(
            _projection(),
            plan,
            severity="severe",
            capital_params=sdi_params,
            paid_up_min=Decimal("0"),
            car_target_pct=Decimal("10"),
            recognition_caps=caps,
        ).serialize()

    now = overlay(governed)
    assert now == overlay(pre_p0)
    # The caps bind (the raise is far larger), so the comparison is not vacuous.
    for year in now["post_action"]:  # type: ignore[union-attr]
        rwa = Decimal(str(year["total_rwa"]))
        assert Decimal(str(year["at1"])) >= (rwa * Decimal("1.5") / 100).quantize(
            Decimal("0.0001")
        ) - Decimal("0.0001")

    # And the ICAAP stress run resolves the same values for the SDI.
    _seed_checker(db_session)
    run_id = _run_enterprise_stress(db_session, _approved_scenario(db_session, code="sdi_caps"))
    run = db_session.get(RegulatoryRun, run_id)
    assert run is not None
    assert run.inputs["recognition_caps_pct_rwa"] == {
        "at1_cap_pct_rwa": "1.5",
        "tier2_cap_pct_rwa": "2",
    }


def test_a_stress_run_refuses_when_a_cap_is_not_configured(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    for row in db_session.scalars(
        select(RegulatoryParameter).where(RegulatoryParameter.param_code == "tier2_cap_pct_rwa")
    ).all():
        db_session.delete(row)
    db_session.commit()
    _seed_checker(db_session)
    with pytest.raises(EnterpriseStressError) as caught:
        _run_enterprise_stress(db_session, _approved_scenario(db_session))
    detail = cast("dict[str, Any]", caught.value.detail)  # the service's typed 409 body
    assert detail["error_code"] == "missing_parameter"
    assert detail["details"]["param_codes"] == ["tier2_cap_pct_rwa"]
