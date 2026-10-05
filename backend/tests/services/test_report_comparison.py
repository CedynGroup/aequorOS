"""Report-comparison engine: deltas, favorability, both modes, and isolation.

The assertions pin the substantive behaviour a governance user relies on — a
stronger capital ratio reads *favorable*, a fatter risk figure reads *adverse*, a
raw balance stays *neutral* — plus the resolution rules (version vs period) and
the tenant boundary.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, RegulatoryRun
from app.schemas.report_comparison import ReportComparisonRequest
from app.services import report_comparison
from app.services.report_comparison import favorable_direction
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2

CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)
OTHER_CTX = TenantContext(organization_id=ORG_2, actor_user_id=USER_2, authorization_version=1)


def _bank(session: Session, org_id: str = ORG_1) -> str:
    bank = Bank(
        organization_id=org_id,
        name="Comparison Bank",
        short_name="CMPB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    session.add(bank)
    session.flush()
    return bank.id


def _period(session: Session, bank_id: str, end: date, label: str, org_id: str = ORG_1) -> UUID:
    period = BankReportingPeriod(
        organization_id=org_id,
        bank_id=bank_id,
        period_start=date(end.year, end.month, 1),
        period_end=end,
        label=label,
        status="closed",
    )
    session.add(period)
    session.flush()
    return period.id


def _run(  # noqa: PLR0913 - a run needs its full scoping
    session: Session,
    bank_id: str,
    period_id: UUID,
    metrics: dict[str, object],
    *,
    module: str = "capital",
    scenario_code: str = "baseline",
    created_at: datetime,
    org_id: str = ORG_1,
    created_by: UUID = USER_1,
) -> UUID:
    run = RegulatoryRun(
        organization_id=org_id,
        bank_id=bank_id,
        reporting_period_id=period_id,
        module=module,
        scenario_code=scenario_code,
        status="succeeded",
        engine_version="regulatory-capital-v1.0.0",
        input_schema_version="bank-facts-v2",
        output_schema_version="v1",
        input_hash=f"hash-{created_at.isoformat()}",
        inputs={"source": "test"},
        metrics=metrics,
        created_at=created_at,
        started_at=created_at,
        completed_at=created_at,
        created_by=created_by,
    )
    session.add(run)
    session.flush()
    return run.id


def _line(result, key: str):
    for group in result.groups:
        for line in group.lines:
            if line.key == key:
                return line
    raise AssertionError(f"line {key} not found in comparison")


# --- the registry, as a pure function --------------------------------------


def test_favorable_direction_registry() -> None:
    assert favorable_direction("car_pct") == "higher_better"
    assert favorable_direction("lcr_pct") == "higher_better"
    assert favorable_direction("cet1_ratio_pct") == "higher_better"
    assert favorable_direction("npl_ratio_pct") == "lower_better"
    assert favorable_direction("total_rwa_ghs") == "lower_better"
    assert favorable_direction("nop_pct_tier1") == "lower_better"
    # Raw balances / unknown keys carry no favorable direction.
    assert favorable_direction("total_assets_ghs") == "neutral"
    assert favorable_direction("deposits_ghs") == "neutral"
    assert favorable_direction("something_unmapped") == "neutral"
    # Family fallback: an unlisted RWA amount is still risk.
    assert favorable_direction("sovereign_rwa_ghs") == "lower_better"
    # Signed ΔEVE is judged on magnitude, never on the raw direction.
    assert favorable_direction("worst_eve_change_pct_tier1") == "magnitude_lower_better"
    assert favorable_direction("worst_eve_change_ghs") == "magnitude_lower_better"
    assert favorable_direction("delta_eve_pct_tier1") == "magnitude_lower_better"
    assert "worst_eve_change_pct_tier1" not in report_comparison.LOWER_BETTER
    # Earnings-at-risk is signed ΔNII judged on |value| by the limit test, so
    # the four EaR keys carry magnitude semantics and the old ``ear_`` prefix
    # fallback (which read a gain as adverse) is gone.
    for key in ("ear_up_200_ghs", "ear_down_200_ghs", "ear_up_450_ghs", "ear_down_450_ghs"):
        assert favorable_direction(key) == "magnitude_lower_better"
        assert key not in report_comparison.LOWER_BETTER
    assert favorable_direction("ear_unlisted_ghs") == "neutral"


def test_signed_earnings_at_risk_is_judged_on_magnitude(db_session: Session) -> None:
    """``compute_ear`` returns the SIGNED ΔNII of a parallel shock (a gain is
    positive) and ``ear_within_limit`` compares ``max(|up|, |down|)`` to base
    NII. So +5 → −10 is adverse (more earnings at risk), −10 → +5 favorable,
    and a sign flip at equal magnitude (+5 → −5) is no change in risk — the
    old ``lower_better`` listing read the −10 → +5 recovery as adverse."""
    bank_id = _bank(db_session)
    period_id = _period(db_session, bank_id, date(2026, 3, 31), "2026-Q1")
    gain = _run(
        db_session,
        bank_id,
        period_id,
        {"ear_up_200_ghs": "5", "ear_down_200_ghs": "-5"},
        module="irr",
        created_at=datetime(2026, 4, 1, 10, tzinfo=UTC),
    )
    loss = _run(
        db_session,
        bank_id,
        period_id,
        {"ear_up_200_ghs": "-10", "ear_down_200_ghs": "10"},
        module="irr",
        created_at=datetime(2026, 4, 2, 10, tzinfo=UTC),
    )
    flipped = _run(
        db_session,
        bank_id,
        period_id,
        {"ear_up_200_ghs": "-5", "ear_down_200_ghs": "5"},
        module="irr",
        created_at=datetime(2026, 4, 3, 10, tzinfo=UTC),
    )
    db_session.commit()

    worse = report_comparison.build_comparison(
        db_session,
        CTX,
        bank_id,
        ReportComparisonRequest(mode="version", module="irr", left=gain, right=loss),
    )
    up = _line(worse, "ear_up_200_ghs")
    assert up.direction == "down"  # +5 → −10: the raw figure fell...
    assert up.favorability == "adverse"  # ...and |ΔNII| grew.
    down = _line(worse, "ear_down_200_ghs")
    assert down.direction == "up"  # −5 → +10: the raw figure rose...
    assert down.favorability == "adverse"  # ...and |ΔNII| grew.
    assert worse.adverse_count == 2
    assert worse.favorable_count == 0

    better = report_comparison.build_comparison(
        db_session,
        CTX,
        bank_id,
        ReportComparisonRequest(mode="version", module="irr", left=loss, right=gain),
    )
    assert _line(better, "ear_up_200_ghs").direction == "up"  # −10 → +5
    assert _line(better, "ear_up_200_ghs").favorability == "favorable"
    assert _line(better, "ear_down_200_ghs").direction == "down"  # +10 → −5
    assert _line(better, "ear_down_200_ghs").favorability == "favorable"
    assert better.favorable_count == 2
    assert better.adverse_count == 0

    same_risk = report_comparison.build_comparison(
        db_session,
        CTX,
        bank_id,
        ReportComparisonRequest(mode="version", module="irr", left=gain, right=flipped),
    )
    assert _line(same_risk, "ear_up_200_ghs").direction == "down"  # +5 → −5
    assert _line(same_risk, "ear_up_200_ghs").favorability == "neutral"
    assert _line(same_risk, "ear_down_200_ghs").favorability == "neutral"


def test_signed_delta_eve_is_judged_on_magnitude(db_session: Session) -> None:
    """ΔEVE / Tier 1 is stored signed (loss negative) and the IRRBB outlier test
    compares |ΔEVE| to the limit, so a deeper loss is adverse even though the
    number went DOWN, and a shallower loss is favorable even though it went UP."""
    bank_id = _bank(db_session)
    period_id = _period(db_session, bank_id, date(2026, 3, 31), "2026-Q1")
    shallow = _run(
        db_session,
        bank_id,
        period_id,
        {"worst_eve_change_pct_tier1": "-8.0", "worst_eve_change_ghs": "-800"},
        module="irr",
        created_at=datetime(2026, 4, 1, 10, tzinfo=UTC),
    )
    deep = _run(
        db_session,
        bank_id,
        period_id,
        {"worst_eve_change_pct_tier1": "-12.0", "worst_eve_change_ghs": "-1200"},
        module="irr",
        created_at=datetime(2026, 4, 2, 10, tzinfo=UTC),
    )
    flipped = _run(
        db_session,
        bank_id,
        period_id,
        {"worst_eve_change_pct_tier1": "8.0", "worst_eve_change_ghs": "800"},
        module="irr",
        created_at=datetime(2026, 4, 3, 10, tzinfo=UTC),
    )
    db_session.commit()

    worse = report_comparison.build_comparison(
        db_session,
        CTX,
        bank_id,
        ReportComparisonRequest(mode="version", module="irr", left=shallow, right=deep),
    )
    line = _line(worse, "worst_eve_change_pct_tier1")
    assert line.direction == "down"  # −8 → −12: the raw figure fell...
    assert line.favorability == "adverse"  # ...but |ΔEVE| grew.
    assert _line(worse, "worst_eve_change_ghs").favorability == "adverse"
    assert worse.adverse_count == 2
    assert worse.favorable_count == 0

    better = report_comparison.build_comparison(
        db_session,
        CTX,
        bank_id,
        ReportComparisonRequest(mode="version", module="irr", left=deep, right=shallow),
    )
    line = _line(better, "worst_eve_change_pct_tier1")
    assert line.direction == "up"  # −12 → −8: the raw figure rose...
    assert line.favorability == "favorable"  # ...and |ΔEVE| shrank.
    assert better.favorable_count == 2

    # A sign flip at the same magnitude is a direction change with no change in
    # risk: the outlier test would read both sides identically.
    same_risk = report_comparison.build_comparison(
        db_session,
        CTX,
        bank_id,
        ReportComparisonRequest(mode="version", module="irr", left=shallow, right=flipped),
    )
    line = _line(same_risk, "worst_eve_change_pct_tier1")
    assert line.direction == "up"
    assert line.favorability == "neutral"


# --- version vs version -----------------------------------------------------


def test_version_vs_version_delta_and_favorability(db_session: Session) -> None:
    bank_id = _bank(db_session)
    period_id = _period(db_session, bank_id, date(2026, 3, 31), "2026-Q1")
    v1 = _run(
        db_session,
        bank_id,
        period_id,
        {
            "car_pct": "12.5",
            "total_rwa_ghs": "1000",
            "npl_ratio_pct": "8.0",
            "total_capital_ghs": "5000",
        },
        created_at=datetime(2026, 4, 1, 10, tzinfo=UTC),
    )
    v2 = _run(
        db_session,
        bank_id,
        period_id,
        {
            "car_pct": "13.0",
            "total_rwa_ghs": "1100",
            "npl_ratio_pct": "9.0",
            "total_capital_ghs": "5000",
        },
        created_at=datetime(2026, 4, 2, 10, tzinfo=UTC),
    )
    db_session.commit()

    req = ReportComparisonRequest(mode="version", module="capital", left=v1, right=v2)
    result = report_comparison.build_comparison(db_session, CTX, bank_id, req)

    assert result.mode == "version"
    assert result.left.run_id == v1
    assert result.left.version == 1
    assert result.right.run_id == v2
    assert result.right.version == 2
    assert result.left.period_label == "2026-Q1"

    # CAR rose: higher_better + up => favorable.
    car = _line(result, "car_pct")
    assert car.left_value == "12.5"
    assert car.right_value == "13.0"
    assert car.delta_ccy == "0.5"
    assert car.delta_pct == "4.00"
    assert car.direction == "up"
    assert car.favorability == "favorable"
    assert car.unit == "pct"
    assert car.new is False

    # RWA rose: lower_better + up => adverse; money unit.
    rwa = _line(result, "total_rwa_ghs")
    assert rwa.delta_ccy == "100"
    assert rwa.direction == "up"
    assert rwa.favorability == "adverse"
    assert rwa.unit == "ccy"

    # NPL rose: lower_better + up => adverse.
    assert _line(result, "npl_ratio_pct").favorability == "adverse"

    # A raw balance that did not move: neutral + flat.
    capital = _line(result, "total_capital_ghs")
    assert capital.direction == "flat"
    assert capital.favorability == "neutral"
    assert capital.delta_ccy == "0"

    assert result.favorable_count == 1
    assert result.adverse_count == 2
    assert result.neutral_count == 1


# --- period vs period -------------------------------------------------------


def test_period_vs_period_uses_latest_run_per_period(db_session: Session) -> None:
    bank_id = _bank(db_session)
    mar = _period(db_session, bank_id, date(2026, 3, 31), "2026-Q1")
    jun = _period(db_session, bank_id, date(2026, 6, 30), "2026-Q2")
    _run(
        db_session,
        bank_id,
        mar,
        {"car_pct": "12.0", "total_rwa_ghs": "1000"},
        created_at=datetime(2026, 4, 1, 10, tzinfo=UTC),
    )
    # Two versions in June: the LATEST (v2) must be the one compared.
    _run(
        db_session,
        bank_id,
        jun,
        {"car_pct": "11.0", "total_rwa_ghs": "1300"},
        created_at=datetime(2026, 7, 1, 10, tzinfo=UTC),
    )
    _run(
        db_session,
        bank_id,
        jun,
        {"car_pct": "12.6", "total_rwa_ghs": "1050"},
        created_at=datetime(2026, 7, 2, 10, tzinfo=UTC),
    )
    db_session.commit()

    req = ReportComparisonRequest(mode="period", module="capital", left=mar, right=jun)
    result = report_comparison.build_comparison(db_session, CTX, bank_id, req)

    assert result.mode == "period"
    assert result.left.period_label == "2026-Q1"
    assert result.right.period_label == "2026-Q2"
    assert result.right.version == 2  # latest of two June runs

    car = _line(result, "car_pct")
    assert car.left_value == "12.0"
    assert car.right_value == "12.6"  # the v2 figure, not v1's 11.0
    assert car.direction == "up"
    assert car.favorability == "favorable"

    rwa = _line(result, "total_rwa_ghs")
    assert rwa.right_value == "1050"
    assert rwa.favorability == "adverse"


# --- divide-by-zero / new-line handling -------------------------------------


def test_zero_base_and_one_sided_lines(db_session: Session) -> None:
    bank_id = _bank(db_session)
    period_id = _period(db_session, bank_id, date(2026, 3, 31), "2026-Q1")
    left = _run(
        db_session,
        bank_id,
        period_id,
        {"car_pct": "0", "dropped_only_ghs": "42"},
        created_at=datetime(2026, 4, 1, 10, tzinfo=UTC),
    )
    right = _run(
        db_session,
        bank_id,
        period_id,
        {"car_pct": "12.0", "ecl_total_ghs": "7"},
        created_at=datetime(2026, 4, 2, 10, tzinfo=UTC),
    )
    db_session.commit()

    req = ReportComparisonRequest(mode="version", module="capital", left=left, right=right)
    result = report_comparison.build_comparison(db_session, CTX, bank_id, req)

    # Base zero: percentage undefined, flagged new, absolute delta still present.
    car = _line(result, "car_pct")
    assert car.delta_pct is None
    assert car.delta_ccy == "12.0"
    assert car.new is True
    assert car.direction == "up"

    # Present only on the comparison side: new, no delta.
    added = _line(result, "ecl_total_ghs")
    assert added.left_value is None
    assert added.right_value == "7"
    assert added.delta_ccy is None
    assert added.delta_pct is None
    assert added.new is True

    # Present only on the base side: not new, right value absent.
    dropped = _line(result, "dropped_only_ghs")
    assert dropped.left_value == "42"
    assert dropped.right_value is None
    assert dropped.new is False


# --- non-comparable & missing -----------------------------------------------


@pytest.mark.parametrize("wrong_side", ["left", "right"])
def test_version_mode_run_outside_declared_module_is_404(
    db_session: Session, wrong_side: str
) -> None:
    bank_id = _bank(db_session)
    period_id = _period(db_session, bank_id, date(2026, 3, 31), "2026-Q1")
    cap = _run(
        db_session,
        bank_id,
        period_id,
        {"car_pct": "12.0"},
        module="capital",
        created_at=datetime(2026, 4, 1, 10, tzinfo=UTC),
    )
    liq = _run(
        db_session,
        bank_id,
        period_id,
        {"lcr_pct": "150"},
        module="liquidity",
        created_at=datetime(2026, 4, 1, 11, tzinfo=UTC),
    )
    db_session.commit()

    left, right = (liq, cap) if wrong_side == "left" else (cap, liq)
    req = ReportComparisonRequest(mode="version", module="capital", left=left, right=right)
    with pytest.raises(HTTPException) as exc:
        report_comparison.build_comparison(db_session, CTX, bank_id, req)
    assert exc.value.status_code == 404


def test_version_mode_different_periods_is_422(db_session: Session) -> None:
    bank_id = _bank(db_session)
    mar = _period(db_session, bank_id, date(2026, 3, 31), "2026-Q1")
    jun = _period(db_session, bank_id, date(2026, 6, 30), "2026-Q2")
    at = datetime(2026, 4, 1, tzinfo=UTC)
    a = _run(db_session, bank_id, mar, {"car_pct": "12"}, created_at=at)
    b = _run(db_session, bank_id, jun, {"car_pct": "13"}, created_at=at)
    db_session.commit()

    req = ReportComparisonRequest(mode="version", module="capital", left=a, right=b)
    with pytest.raises(HTTPException) as exc:
        report_comparison.build_comparison(db_session, CTX, bank_id, req)
    assert exc.value.status_code == 422


def test_period_mode_missing_run_is_404(db_session: Session) -> None:
    bank_id = _bank(db_session)
    mar = _period(db_session, bank_id, date(2026, 3, 31), "2026-Q1")
    jun = _period(db_session, bank_id, date(2026, 6, 30), "2026-Q2")
    _run(db_session, bank_id, mar, {"car_pct": "12"}, created_at=datetime(2026, 4, 1, tzinfo=UTC))
    # June has no succeeded capital/baseline run.
    db_session.commit()

    req = ReportComparisonRequest(mode="period", module="capital", left=mar, right=jun)
    with pytest.raises(HTTPException) as exc:
        report_comparison.build_comparison(db_session, CTX, bank_id, req)
    assert exc.value.status_code == 404


def test_missing_run_is_404(db_session: Session) -> None:
    bank_id = _bank(db_session)
    period_id = _period(db_session, bank_id, date(2026, 3, 31), "2026-Q1")
    real = _run(
        db_session,
        bank_id,
        period_id,
        {"car_pct": "12"},
        created_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    db_session.commit()

    req = ReportComparisonRequest(mode="version", module="capital", left=real, right=uuid4())
    with pytest.raises(HTTPException) as exc:
        report_comparison.build_comparison(db_session, CTX, bank_id, req)
    assert exc.value.status_code == 404


# --- tenant isolation -------------------------------------------------------


def test_other_tenant_run_is_not_visible(db_session: Session) -> None:
    # A run that lives entirely under ORG_2.
    other_bank = _bank(db_session, org_id=ORG_2)
    other_period = _period(db_session, other_bank, date(2026, 3, 31), "2026-Q1", org_id=ORG_2)
    other_run = _run(
        db_session,
        other_bank,
        other_period,
        {"car_pct": "12"},
        created_at=datetime(2026, 4, 1, tzinfo=UTC),
        org_id=ORG_2,
        created_by=USER_2,
    )
    db_session.commit()

    # ORG_1 cannot resolve ORG_2's bank at all.
    req = ReportComparisonRequest(mode="version", module="capital", left=other_run, right=other_run)
    with pytest.raises(HTTPException) as exc:
        report_comparison.build_comparison(db_session, CTX, other_bank, req)
    assert exc.value.status_code == 404


def test_credit_module_comparison_judges_the_notice_metrics(db_session: Session) -> None:
    """Credit PR-9: a credit-vs-credit period comparison judges the Notice
    2025/23 figures with the right directions — NPL/PAR/concentration up is
    adverse, recoveries up favorable, raw balances neutral — grouped under
    Asset quality / Credit, with honest keys skipped when a side lacks them."""
    bank_id = _bank(db_session)
    mar = _period(db_session, bank_id, date(2026, 3, 31), "2026-Q1")
    jun = _period(db_session, bank_id, date(2026, 6, 30), "2026-Q2")
    _run(
        db_session,
        bank_id,
        mar,
        {
            "npl_ratio_pct": "8.2",
            "par_30_pct": "11.0",
            "provision_coverage_pct": "58.0",
            "gross_loans_ghs": "9000000",
            "recovery_12m_ghs": "40000",
            "employer_hhi": "900",
        },
        module="credit",
        created_at=datetime(2026, 4, 5, 10, tzinfo=UTC),
    )
    _run(
        db_session,
        bank_id,
        jun,
        {
            "npl_ratio_pct": "9.6",
            "par_30_pct": "10.0",
            "provision_coverage_pct": "61.0",
            "gross_loans_ghs": "9500000",
            "recovery_12m_ghs": "65000",
            "employer_hhi": "1250",
        },
        module="credit",
        created_at=datetime(2026, 7, 5, 10, tzinfo=UTC),
    )
    db_session.commit()

    req = ReportComparisonRequest(mode="period", module="credit", left=mar, right=jun)
    result = report_comparison.build_comparison(db_session, CTX, bank_id, req)

    assert _line(result, "npl_ratio_pct").favorability == "adverse"
    assert _line(result, "par_30_pct").favorability == "favorable"  # PAR fell
    assert _line(result, "provision_coverage_pct").favorability == "favorable"
    assert _line(result, "recovery_12m_ghs").favorability == "favorable"
    assert _line(result, "employer_hhi").favorability == "adverse"
    assert _line(result, "gross_loans_ghs").favorability == "neutral"

    sections = {group.title for group in result.groups}
    assert "Asset quality" in sections
    assert "Credit" in sections  # module-section fallback for the raw keys
