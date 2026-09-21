"""The IRRBB Standardised Framework service: lifecycle, refusals, provenance.

These tests are about the SERVICE, not the arithmetic. The eight golden vectors
of P5-DESIGN §3 already pin every number the engine produces
(``tests/domain/irr/``), so nothing here asserts a magnitude the engine chose.
What is asserted instead:

* a run is minted, sealed and reproducible — the same book hashes the same way;
* every refusal is DATA on the run row with a typed code, never an HTTP 500;
* the option refusal carries exactly one name (D-061);
* which governed rows the run resolved is recorded, and moving one in the
  console moves the answer with no code change (D-024, SF-8);
* the legacy IRRBB engine is untouched by an SF run (P5-DESIGN §1.7).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.irr import standardised as sf
from app.domain.irr import standardised_cash_flows as sfcf
from app.domain.irr import standardised_params as sfp
from app.models import (
    Bank,
    BankReportingPeriod,
    RegulatoryMetricResult,
    RegulatoryParameter,
    RegulatoryRun,
    RegulatoryValidation,
)
from app.schemas.regulatory_irr import IrrScenarioBatchCreate
from app.schemas.regulatory_irr_sf import IrrbbSfRunCreate
from app.services import jurisdictions, regulatory_irr, regulatory_irr_sf
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.services.sf_book import AS_OF, seed_book, seed_fx

CTX = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
SDI_BANK_ID = "BK-SFSDI001"


def _bank(session: Session, bank_id: str = SAMPLE_BANK_ID) -> Bank:
    bank = session.get(Bank, bank_id)
    assert bank is not None
    return bank


def _run(session: Session, period_id: UUID, bank_id: str = SAMPLE_BANK_ID) -> RegulatoryRun:
    read = regulatory_irr_sf.run_standardised_framework(
        session, CTX, bank_id, IrrbbSfRunCreate(reporting_period_id=period_id)
    )
    row = session.get(RegulatoryRun, read.id)
    assert row is not None
    return row


def _retire(session: Session, param_code: str) -> None:
    """Close the governed row, as a console generation would."""
    session.execute(
        update(RegulatoryParameter)
        .where(RegulatoryParameter.param_code == param_code)
        .values(status="draft")
    )
    session.flush()


# --- the happy path ---------------------------------------------------------


def test_a_run_on_the_sample_bank_succeeds_and_seals_its_whole_result(
    db_session: Session,
) -> None:
    period_id = seed_book(db_session)

    run = _run(db_session, period_id)

    assert run.status == "succeeded", (run.error_code, run.error_message)
    assert run.module == regulatory_irr_sf.MODULE_IRR_SF
    assert run.scenario_code == regulatory_irr_sf.SF_SCENARIO_CODE
    assert run.engine_version == regulatory_irr_sf.ENGINE_VERSION
    assert run.input_schema_version == regulatory_irr_sf.INPUT_SCHEMA_VERSION
    metrics = run.metrics
    # All six prescribed shapes are reported together; the framework does not
    # let a bank choose one.
    assert [row["code"] for row in metrics["scenarios"]] == list(sfp.SCENARIOS)
    assert set(metrics["measures"]) == {"all", "mandatory", "outlier_set"}
    assert Decimal(metrics["tier1"]) > 0
    assert metrics["currencies"][0]["currency"] == "GHS"
    assert metrics["currencies"][0]["material"] is True
    assert len(metrics["bucket_keys"]) == 19


def test_the_headline_metrics_and_line_items_are_persisted_as_rows(
    db_session: Session,
) -> None:
    period_id = seed_book(db_session)
    run = _run(db_session, period_id)

    metrics = {
        row.metric_code: row
        for row in db_session.scalars(
            select(RegulatoryMetricResult).where(RegulatoryMetricResult.run_id == run.id)
        )
    }
    assert set(metrics) == {
        regulatory_irr_sf.METRIC_EVE_RISK_MEASURE,
        regulatory_irr_sf.METRIC_EVE_RISK_MEASURE_PCT_TIER1,
        regulatory_irr_sf.METRIC_MAX_DELTA_NII,
    }
    pct = metrics[regulatory_irr_sf.METRIC_EVE_RISK_MEASURE_PCT_TIER1]
    # The threshold travels with the metric, so a reader never has to guess
    # what the status was measured against.
    assert pct.threshold_min == Decimal(run.metrics["outlier_threshold_pct"])
    assert pct.status == ("red" if run.metrics["outlier"] else "green")

    validations = {
        row.rule_code: row
        for row in db_session.scalars(
            select(RegulatoryValidation).where(RegulatoryValidation.run_id == run.id)
        )
    }
    assert regulatory_irr_sf.RULE_OUTLIER in validations
    assert regulatory_irr_sf.RULE_CURRENCY_SCOPE in validations
    # Every message is production copy: no raw marker, no bare enum.
    for row in validations.values():
        assert "irrbb_sf_" not in row.message
        assert "_" not in row.message.split(":")[0]


def test_the_reporting_currency_is_resolved_from_the_bank_not_written_down(
    db_session: Session,
) -> None:
    """Nothing a bank reads is labelled from a currency literal.

    The stored metric UNIT is the platform's shared wire key (the CHECK admits
    only ``pct``/``ghs``/``years``, and capital, liquidity, FX and legacy IRRBB
    all write the same one). The bank's actual reporting currency is resolved
    through ``jurisdictions.base_currency`` and travels on the snapshot, the
    metrics and the read model, so the wire key is never mistaken for it.
    """
    period_id = seed_book(db_session)
    run = _run(db_session, period_id)
    bank = _bank(db_session)

    expected = jurisdictions.base_currency(bank)
    assert run.inputs["reporting_currency"] == expected
    assert run.metrics["reporting_currency"] == expected

    units = {
        row.metric_code: row.unit
        for row in db_session.scalars(
            select(RegulatoryMetricResult).where(RegulatoryMetricResult.run_id == run.id)
        )
    }
    assert units[regulatory_irr_sf.METRIC_EVE_RISK_MEASURE] == (
        regulatory_irr_sf.METRIC_UNIT_REPORTING_CURRENCY
    )
    assert units[regulatory_irr_sf.METRIC_MAX_DELTA_NII] == (
        regulatory_irr_sf.METRIC_UNIT_REPORTING_CURRENCY
    )
    # No statement spells a currency out. The currency-scope message DOES name
    # currencies — that is its job — but only ones the book actually holds, so
    # every code it prints must come from the run's own scope.
    for statement in run.metrics["statements"]:
        for code in ("GHS", "NGN", "KES", "ZAR"):
            assert code not in statement, statement
    scope = db_session.scalar(
        select(RegulatoryValidation).where(
            RegulatoryValidation.run_id == run.id,
            RegulatoryValidation.rule_code == regulatory_irr_sf.RULE_CURRENCY_SCOPE,
        )
    )
    assert scope is not None
    held = {row["currency"] for row in run.metrics["currencies"]}
    for code in ("GHS", "NGN", "KES", "ZAR"):
        if code in scope.message:
            assert code in held, code


def test_the_metric_constants_are_the_codes_actually_persisted(
    db_session: Session,
) -> None:
    """The persistence site names each metric with a LITERAL, on purpose.

    ``tests/domain/authority/test_registry_completeness`` reads every metric
    persistence site by AST to check that no filed figure reaches the database
    without a registered authority, and it refuses to guess — a site it cannot
    resolve fails the gate rather than disappearing from it. So the literals
    stay at the write site and the constants stay for the callers, and this is
    what stops the two drifting apart.
    """
    period_id = seed_book(db_session)
    run = _run(db_session, period_id)

    persisted = {
        row.metric_code
        for row in db_session.scalars(
            select(RegulatoryMetricResult).where(RegulatoryMetricResult.run_id == run.id)
        )
    }
    assert persisted == {
        regulatory_irr_sf.METRIC_EVE_RISK_MEASURE,
        regulatory_irr_sf.METRIC_EVE_RISK_MEASURE_PCT_TIER1,
        regulatory_irr_sf.METRIC_MAX_DELTA_NII,
    }
    # And every one of them is owned by a registered authority, so the figure
    # cannot be filed while nothing states what it rests on.
    from app.domain.authority.registry import REGISTRY  # noqa: PLC0415

    for code in persisted:
        assert REGISTRY.for_metric(code), code


def test_the_input_hash_is_value_based_and_stable_across_reruns(
    db_session: Session,
) -> None:
    period_id = seed_book(db_session)

    first = _run(db_session, period_id)
    second = _run(db_session, period_id)

    assert first.id != second.id
    assert first.input_hash == second.input_hash
    # Value-based: no row id, no timestamp, no ingestion identity in the seal.
    snapshot = first.inputs
    assert "instrument_digest" in snapshot["data_quality"]
    assert str(first.id) not in str(snapshot)


def test_positions_the_framework_does_not_measure_are_counted_not_dropped(
    db_session: Session,
) -> None:
    period_id = seed_book(db_session)
    run = _run(db_session, period_id)

    excluded = run.inputs["data_quality"]["excluded"]
    assert regulatory_irr_sf.EXCLUDED_OUT_OF_SCOPE in excluded
    assert excluded[regulatory_irr_sf.EXCLUDED_OUT_OF_SCOPE]["count"] == 1
    assert Decimal(excluded[regulatory_irr_sf.EXCLUDED_OUT_OF_SCOPE]["amount_reporting"]) == (
        Decimal("50000")
    )


def test_a_behavioural_rate_from_the_reference_dataset_reaches_the_ladder(
    db_session: Session,
) -> None:
    """A prepayment rate is the bank's own input, not a platform assumption."""
    period_id = seed_book(db_session, with_prepayment=True)
    run = _run(db_session, period_id)

    ladders = run.inputs["ladders"]
    prepayable = [row for row in ladders if row["kind"] == "prepayable"]
    assert prepayable, ladders
    assert Decimal(prepayable[0]["cpr0"]) == Decimal("0.10")


def test_a_loan_with_no_prepayment_rate_runs_contractually_and_says_so(
    db_session: Session,
) -> None:
    """An absent behavioural rate is a DISCLOSED default, never a zero.

    The contractual schedule is the honest fallback, but a reader must be able
    to see that the bank supplied no prepayment behaviour rather than that its
    borrowers never prepay.
    """
    period_id = seed_book(db_session, with_prepayment=False)
    run = _run(db_session, period_id)

    assert "no_prepayment_rate" in run.inputs["data_quality"]["defaulted"]
    assert all(row["kind"] != "prepayable" for row in run.inputs["ladders"])


# --- refusals, all of them data on the run row ------------------------------


def test_a_book_with_automatic_options_is_refused_under_one_name(
    db_session: Session,
) -> None:
    """D-061: the run row carries ``irrbb_sf_options_unsupported`` and nothing else.

    Refusing is the feature (DV-010). Valuing the rest of the book and calling
    the answer the Standardised Framework measure would understate the change
    in economic value by exactly the option value, invisibly.
    """
    period_id = seed_book(db_session, with_options=True)

    run = _run(db_session, period_id)

    assert run.status == "failed"
    assert run.error_code == "irrbb_sf_options_unsupported"
    assert run.error_details is not None
    assert run.error_details["count"] == 1
    assert run.error_details["option_types"] == ["cap"]
    assert "automatic_options_not_modelled" not in str(run.error_details)
    assert "automatic_options_not_modelled" not in (run.error_message or "")


def test_a_material_currency_without_a_curve_refuses_missing_curve(
    db_session: Session,
) -> None:
    period_id = seed_book(db_session, with_curve=False)

    run = _run(db_session, period_id)

    assert run.status == "failed"
    assert run.error_code == "missing_curve"


def test_a_currency_with_no_exchange_rate_refuses_missing_fx_rate(
    db_session: Session,
) -> None:
    period_id = seed_book(db_session, with_usd=True, with_usd_fx=False)

    run = _run(db_session, period_id)

    assert run.status == "failed"
    assert run.error_code == "missing_fx_rate"
    assert run.error_details is not None
    assert run.error_details["currency"] == "USD"


def test_a_foreign_currency_with_a_rate_is_measured_and_scoped(
    db_session: Session,
) -> None:
    period_id = seed_book(db_session, with_usd=True, with_usd_fx=True)

    run = _run(db_session, period_id)

    assert run.status == "succeeded", (run.error_code, run.error_message)
    currencies = {row["currency"]: row for row in run.metrics["currencies"]}
    assert set(currencies) == {"GHS", "USD"}
    # Materiality is the governed share test, not a code rule: a small currency
    # is reported as excluded rather than silently ignored.
    assert currencies["GHS"]["material"] is True
    assert set(run.metrics["excluded_currencies"]) <= {"USD"}


def test_an_absent_governed_row_refuses_rather_than_defaulting(
    db_session: Session,
) -> None:
    """D-024: there is no code fallback for a regulatory number."""
    period_id = seed_book(db_session)
    _retire(db_session, "irrbb_sf_parallel_shock_bp")

    run = _run(db_session, period_id)

    assert run.status == "failed"
    assert run.error_code == "missing_parameter"
    assert run.error_details is not None
    assert run.error_details["param_code"] == "irrbb_sf_parallel_shock_bp"


def test_an_sdi_has_no_standardised_framework_calibration(db_session: Session) -> None:
    """The framework is seeded for banks; an SDI gets a refusal, not a guess."""
    period_id = seed_book(db_session)
    db_session.add(
        Bank(
            id=SDI_BANK_ID,
            organization_id=DEMO_ORG_ID,
            name="Sample Savings and Loans",
            short_name="Sample S&L",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="savings_and_loans",
            institution_type="savings_and_loans",
        )
    )
    db_session.add(
        BankReportingPeriod(
            organization_id=DEMO_ORG_ID,
            bank_id=SDI_BANK_ID,
            period_start=date(2026, 3, 1),
            period_end=AS_OF,
            label="2026-03",
            status="open",
        )
    )
    db_session.flush()
    sdi_period = db_session.scalar(
        select(BankReportingPeriod.id).where(BankReportingPeriod.bank_id == SDI_BANK_ID)
    )
    assert sdi_period is not None
    del period_id

    run = _run(db_session, sdi_period, bank_id=SDI_BANK_ID)

    assert run.status == "failed"
    assert run.error_code == "missing_parameter"


def test_a_reporting_date_with_no_banking_book_refuses(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    period_id = db_session.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == AS_OF,
        )
    )
    assert period_id is not None

    run = _run(db_session, period_id)

    assert run.status == "failed"
    assert run.error_code == "no_positions_as_of"


def test_an_unknown_bank_or_period_is_a_404_before_any_run_row(
    db_session: Session,
) -> None:
    from fastapi import HTTPException  # noqa: PLC0415 - narrow, local assertion

    seed_book(db_session)
    before = db_session.scalar(
        select(RegulatoryRun).where(RegulatoryRun.module == regulatory_irr_sf.MODULE_IRR_SF)
    )
    assert before is None

    with pytest.raises(HTTPException) as caught:
        regulatory_irr_sf.run_standardised_framework(
            db_session, CTX, SAMPLE_BANK_ID, IrrbbSfRunCreate(reporting_period_id=uuid4())
        )
    assert caught.value.status_code == 404

    after = db_session.scalar(
        select(RegulatoryRun).where(RegulatoryRun.module == regulatory_irr_sf.MODULE_IRR_SF)
    )
    assert after is None


# --- governed parameters ----------------------------------------------------


def test_the_run_records_which_governed_rows_it_resolved(db_session: Session) -> None:
    period_id = seed_book(db_session)
    run = _run(db_session, period_id)

    provenance = run.parameter_provenance or []
    resolved = {entry["param_code"] for entry in provenance}
    assert set(sfp.REQUIRED_CODES) <= resolved
    for entry in provenance:
        if entry["param_code"] in sfp.REQUIRED_CODES:
            assert entry["parameter_id"]
            assert entry["scope_type"] == "institution_class"
            assert entry["confirmation_status"] in {"confirmed", "pending"}
    # Values live in the snapshot and are sealed by the hash; identity lives
    # beside it, never inside (audit D-18).
    assert "parameter_id" not in str(run.inputs["parameters"])


def test_every_standardised_framework_row_ships_pending_and_says_so(
    db_session: Session,
) -> None:
    """The guideline is an exposure draft; a printed value is not a confirmed one."""
    period_id = seed_book(db_session)
    run = _run(db_session, period_id)

    pending = set(run.metrics["parameters_pending_confirmation"])
    assert {"irrbb_sf_parallel_shock_bp", "irrbb_sf_time_buckets"} <= pending
    assert any("pending confirmation" in line for line in run.metrics["statements"])


def test_a_representative_default_is_labelled_and_every_use_is_counted(
    db_session: Session,
) -> None:
    period_id = seed_book(db_session)
    run = _run(db_session, period_id)

    assert "irrbb_sf_default_cash_flow_profile" in run.metrics["representative_parameters"]
    assert any(
        "representative only" in line and "not a published supervisory value" in line
        for line in run.metrics["statements"]
    )
    tallies = run.metrics["assumption_tallies"]
    # The deposit and the borrowing state no amortisation, so the platform
    # profile is applied — and COUNTED, which is what makes the disclosure a
    # measure of exposure rather than a footnote.
    assert sum(tallies.values()) > 0


def test_moving_the_console_value_moves_the_answer_with_no_code_change(
    db_session: Session,
) -> None:
    """SF-8: the governed shock is what decides, and it is editable."""
    period_id = seed_book(db_session)
    baseline = _run(db_session, period_id)
    assert baseline.status == "succeeded", baseline.error_code
    before = Decimal(baseline.metrics["measures"]["outlier_set"]["measure"])

    row = db_session.scalar(
        select(RegulatoryParameter).where(
            RegulatoryParameter.param_code == "irrbb_sf_parallel_shock_bp",
            RegulatoryParameter.status == "approved",
        )
    )
    assert row is not None
    body = dict(row.value_json or {})
    body["GHS"] = "200"
    row.value_json = body
    db_session.flush()
    db_session.expire_all()

    after_run = _run(db_session, period_id)

    assert after_run.status == "succeeded", after_run.error_code
    after = Decimal(after_run.metrics["measures"]["outlier_set"]["measure"])
    assert after != before
    assert after_run.input_hash != baseline.input_hash


def test_the_commencement_rule_is_governed_and_travels_with_the_run(
    db_session: Session,
) -> None:
    period_id = seed_book(db_session)
    run = _run(db_session, period_id)

    mandate = run.metrics["mandate"]
    assert mandate["mandatory_from"] == "2026-12-31"
    # The fixture's newest reporting date precedes the commencement date, so
    # the framework is not yet mandatory for it — and the payload says which
    # date it becomes mandatory from rather than leaving the reader to infer.
    assert mandate["mandatory"] is False
    assert "2026-12-31" in mandate["statement"]

    bank = _bank(db_session)
    later = regulatory_irr_sf.sf_mandate(
        db_session, bank, as_of=date(2026, 12, 31), today=date(2026, 9, 19)
    )
    assert later.mandatory is True


# --- legacy protection (P5-DESIGN §1.7) -------------------------------------


def test_running_the_standardised_framework_leaves_the_legacy_irr_untouched(
    db_session: Session,
) -> None:
    """The framework is a NEW module, not a revision of the existing engine."""
    period_id = seed_book(db_session)
    seed_fx(db_session, base="USD", quote="GHS", rate="15")

    legacy_before = regulatory_irr.run_all_irr_scenarios(
        db_session, CTX, SAMPLE_BANK_ID, IrrScenarioBatchCreate(reporting_period_id=period_id)
    )
    dashboard_before = regulatory_irr.get_irr_dashboard(
        db_session, CTX, SAMPLE_BANK_ID, period_id
    )

    sf_run = _run(db_session, period_id)
    assert sf_run.status == "succeeded", sf_run.error_code

    legacy_after = regulatory_irr.run_all_irr_scenarios(
        db_session, CTX, SAMPLE_BANK_ID, IrrScenarioBatchCreate(reporting_period_id=period_id)
    )
    dashboard_after = regulatory_irr.get_irr_dashboard(
        db_session, CTX, SAMPLE_BANK_ID, period_id
    )

    assert [run.input_hash for run in legacy_after.runs] == [
        run.input_hash for run in legacy_before.runs
    ]
    assert [run.metrics for run in legacy_after.runs] == [
        run.metrics for run in legacy_before.runs
    ]
    assert dashboard_after.metrics == dashboard_before.metrics

    # And the legacy runs never acquire the new module value.
    legacy_modules = {
        row.module
        for row in db_session.scalars(
            select(RegulatoryRun).where(RegulatoryRun.reporting_period_id == period_id)
        )
    }
    assert legacy_modules == {"irr", regulatory_irr_sf.MODULE_IRR_SF}


def test_latest_sf_run_is_the_newest_succeeded_run_for_the_exact_period(
    db_session: Session,
) -> None:
    period_id = seed_book(db_session)
    first = _run(db_session, period_id)
    second = _run(db_session, period_id)
    bank = _bank(db_session)
    period = db_session.get(BankReportingPeriod, period_id)
    assert period is not None

    latest = regulatory_irr_sf.latest_sf_run(db_session, CTX, bank, period)

    assert latest is not None
    assert latest.id == second.id
    assert latest.id != first.id


# --- assumptions that change the measure must be visible ---------------------


def test_past_due_principal_is_tallied_labelled_and_disclosed(
    db_session: Session,
) -> None:
    """Audit R-3: the placement is defensible, the silence was not.

    A loan that matured before the reporting date has no remaining schedule, so
    its principal is slotted overnight rather than dropped. That is the right
    answer to "where does it go" and the least conservative one available —
    overnight money barely moves when rates move — so a bank with a material
    non-performing book files an understated measure. Until 2026-09-20 nothing
    counted it and nothing said it: the loader never read performing status and
    the projection raised no marker, so the run's disclosed assumptions were
    silent about the one substitution that changes the answer downwards.

    Three things are asserted, because the finding was that all three were
    missing: the tally, the bank-facing label, and the statement that names the
    direction of the error.
    """
    period_id = seed_book(db_session, with_past_due=True)
    run = _run(db_session, period_id)
    assert run.status == "succeeded", run.error_code

    tallies = run.metrics["assumption_tallies"]
    assert tallies.get(sfcf.TALLY_PAST_DUE) == 1
    assert (
        regulatory_irr_sf.ASSUMPTION_LABELS[sfcf.TALLY_PAST_DUE]
        == "Principal already past due at the reporting date; placed in the shortest bucket"
    )
    statement = next(
        line for line in run.metrics["statements"] if "past due" in line
    )
    assert "understates" in statement


def test_a_book_with_nothing_past_due_says_nothing_about_it(db_session: Session) -> None:
    """A disclosure that always prints is one a reader learns to skip."""
    period_id = seed_book(db_session)
    run = _run(db_session, period_id)

    assert sfcf.TALLY_PAST_DUE not in run.metrics["assumption_tallies"]
    assert not any("past due" in line for line in run.metrics["statements"])


def test_a_wholesale_term_deposit_declares_the_book_it_is_priced_on(
    db_session: Session,
) -> None:
    """Audit W1: the treatment is strong, so it has to be said out loud.

    Every non-retail term deposit with no early-withdrawal evidence — which
    includes every term deposit with no counterparty row at all — is priced as
    repayable on demand at face value, on the base position and the shocked
    position alike. That removes the term funding protection the contractual
    schedule would have given, so it is an assumption a reader must be able to
    see and to act on by ingesting the penalty terms.

    It was previously reported under the retail label "contractual schedule
    used", which was the opposite of what the engine did.
    """
    period_id = seed_book(db_session, with_wholesale_term_deposit=True)
    run = _run(db_session, period_id)
    assert run.status == "succeeded", run.error_code

    tallies = run.metrics["assumption_tallies"]
    assert tallies.get(regulatory_irr_sf.TALLY_TD_WHOLESALE_DEMANDABLE) == 1
    # And NOT under the label that says the contractual schedule was used.
    assert sf.TALLY_NO_REDEMPTION_RATE not in tallies
    statement = next(
        line for line in run.metrics["statements"] if "repayable on demand" in line
    )
    assert "most disadvantageous" in statement
    assert "base position and every shocked position alike" in statement


def test_the_post_shock_floor_statement_claims_only_what_the_platform_knows(
    db_session: Session,
) -> None:
    """Audit U-3: it used to assert a fact about a supervisory standard.

    The old sentence read "The framework text prescribes no post-shock rate
    floor". The guideline it refers to is not held in this system, and the
    international standardised framework these shock shapes follow does
    prescribe a floor, so the claim was one the platform could not support and
    that a supervisor could contradict. The engine's behaviour is unchanged —
    no floor is applied — and the value is governed; only the sentence is ours.
    """
    period_id = seed_book(db_session)
    run = _run(db_session, period_id)

    statement = regulatory_irr_sf.post_shock_floor_statement(run.metrics["post_shock_floor"])

    assert statement.startswith("No post-shock rate floor was applied")
    assert "does prescribe one" in statement
    assert "open point for the supervisor" in statement
    # The discarded claim must not come back by copy-paste.
    assert "framework text prescribes no post-shock rate floor" not in statement


def test_every_assumption_marker_carries_bank_facing_copy() -> None:
    """A raw marker must never print, so the map must be COMPLETE, not mostly.

    ``ASSUMPTION_LABELS.get(marker, marker)`` falls back to the raw key at
    three surfaces — the validation row, the read model and the ICAAP
    assumptions table — so a marker added without copy degrades quietly into
    jargon on a document a supervisor reads. Collected by reflection rather
    than listed, so the guard cannot go stale behind a new marker.
    """
    engine = {name: value for name, value in vars(sf).items() if name.startswith("TALLY_")}
    markers = (
        set(sfcf.TALLY_MARKERS)
        | set(engine.values())
        | {regulatory_irr_sf.TALLY_TD_WHOLESALE_DEMANDABLE}
    )

    missing = markers - set(regulatory_irr_sf.ASSUMPTION_LABELS)

    assert missing == set(), f"no bank-facing copy for {sorted(missing)}"
    assert all(
        label != marker for marker, label in regulatory_irr_sf.ASSUMPTION_LABELS.items()
    )
