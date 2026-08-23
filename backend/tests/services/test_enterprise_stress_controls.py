"""Service-layer controls for the enterprise stress run (P0-8, P0-9, P0-13).

Three defects from the 2026-08-20 enterprise audit, at the seam where the pure
engines meet the database:

* **P0-8** — the Basel HQLA haircuts and Level-2 caps must reach ``compute_lcr``
  from the regulatory-parameter control plane, never from a literal.
* **P0-9** — a scenario that does not carry every macro variable the run reads is
  refused BEFORE any engine runs, with the missing variables named.
* **P0-13** — a run whose base-case plan falls back to platform constants records
  that fact field by field, so a Board-attested ICAAP cannot silently rest on
  assumptions the institution never made.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.domain.authority.outcomes import OutcomeState
from app.domain.capital.engine import CapitalParams
from app.domain.stress.translation import MacroPathPoint
from app.models import Bank
from app.schemas.enterprise_stress import EnterpriseStressRunCreate, PlanAssumptionsIn
from app.services import enterprise_stress as svc
from app.services import regulatory_parameters
from tests.api.helpers import ORG_1

AS_OF = date(2026, 6, 30)


def _bank(db: Session, *, institution_type: str = "universal_bank") -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Control Test Bank",
        short_name="CTB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def _p(variable: str, year: int, base: str, stress: str) -> MacroPathPoint:
    return MacroPathPoint(variable, year, Decimal(base), Decimal(stress))


def _complete_paths(horizon: int = 3) -> list[MacroPathPoint]:
    levels = {
        "gdp_growth": ("0.05", "0.00"),
        "interest_rate": ("0.20", "0.25"),
        "inflation": ("0.15", "0.21"),
        "unemployment": ("0.06", "0.09"),
        "fx_usd_ghs": ("12.5", "15.0"),
        "gse_index": ("5000", "3500"),
        "gog_yield": ("0.22", "0.26"),
    }
    return [
        _p(variable, year, base, stress)
        for variable, (base, stress) in levels.items()
        for year in range(1, horizon + 1)
    ]


def _detail(error: svc.EnterpriseStressError) -> dict[str, Any]:
    """``HTTPException.detail`` is typed ``Any``; the service always builds a dict."""
    detail = error.detail
    assert isinstance(detail, dict)
    return detail


def _payload(**kwargs) -> EnterpriseStressRunCreate:
    return EnterpriseStressRunCreate(
        scenario_id=uuid4(),
        reporting_period_id=uuid4(),
        reason="control test",
        **kwargs,
    )


# --- P0-8: the HQLA rates come from the control plane ------------------------


def test_the_basel_hqla_haircuts_and_caps_resolve_from_the_control_plane(
    db_session: Session,
) -> None:
    """The engine names no rate; these are the seeded, cited, governed values."""
    hqla = regulatory_parameters.resolve_hqla_parameters(db_session, _bank(db_session), as_of=AS_OF)

    assert hqla.haircut_pct["L1"] == Decimal("0")
    assert hqla.haircut_pct["L2A"] == Decimal("15")
    assert hqla.haircut_pct["L2B"] == Decimal("50")
    assert hqla.level2_cap_pct == Decimal("40")
    assert hqla.level2b_cap_pct == Decimal("15")
    assert hqla.unresolved_codes == ()


def test_every_hqla_parameter_carries_a_citation_and_a_confirmation_status() -> None:
    """A regulatory number without a source is not governed, it is a constant."""
    specs = {
        spec.param_code: spec
        for spec in regulatory_parameters.SEED_PARAMETERS
        if spec.param_code.startswith("hqla_")
    }
    expected = {
        *regulatory_parameters.HQLA_HAIRCUT_CODES.values(),
        regulatory_parameters.HQLA_LEVEL2_CAP_CODE,
        regulatory_parameters.HQLA_LEVEL2B_CAP_CODE,
    }
    assert set(specs) == expected
    for spec in specs.values():
        assert spec.scope_key == "bank"  # LCR is a Basel, bank-only measure
        assert "BCBS 238" in spec.source_citation
        assert spec.confirmation_status in {"confirmed", "pending"}
    # The Level-2B haircut is a conservative bound of the 25-50% Basel range
    # (the fact model carries no L2B sub-class), so it is NOT presented as a
    # confirmed regulatory value.
    assert specs["hqla_l2b_haircut_pct"].confirmation_status == "pending"
    assert specs["hqla_l2a_haircut_pct"].confirmation_status == "confirmed"


def test_an_sdi_resolves_no_hqla_parameters(db_session: Session) -> None:
    """An SDI never runs the Basel LCR, so no HQLA rate is seeded for it."""
    sdi = _bank(db_session, institution_type="savings_and_loans")
    hqla = regulatory_parameters.resolve_hqla_parameters(db_session, sdi, as_of=AS_OF)
    assert hqla.haircut_pct == {}
    assert hqla.level2_cap_pct is None
    assert set(hqla.unresolved_codes) == {
        *regulatory_parameters.HQLA_HAIRCUT_CODES.values(),
        regulatory_parameters.HQLA_LEVEL2_CAP_CODE,
        regulatory_parameters.HQLA_LEVEL2B_CAP_CODE,
    }


# --- P0-9: the run refuses an incomplete scenario ----------------------------


def test_a_complete_scenario_passes_the_guard() -> None:
    svc._require_complete_scenario("adverse_2027", _complete_paths(), _payload())


def test_a_scenario_missing_a_driver_is_refused_by_name() -> None:
    paths = [point for point in _complete_paths() if point.variable != "fx_usd_ghs"]
    with pytest.raises(svc.EnterpriseStressError) as exc:
        svc._require_complete_scenario("adverse_2027", paths, _payload())
    detail = _detail(exc.value)
    assert detail["error_code"] == "scenario_incomplete_paths"
    assert "fx_usd_ghs" in detail["details"]["missing_by_module"]["liquidity"]
    assert "fx_usd_ghs" in detail["details"]["missing_by_module"]["fx"]


def test_a_scenario_with_a_gap_year_is_refused_by_year() -> None:
    """Years 1 and 3 authored, year 2 missing: the middle year would be unstressed."""
    paths = [point for point in _complete_paths() if point.year_index != 2]
    with pytest.raises(svc.EnterpriseStressError) as exc:
        svc._require_complete_scenario("adverse_2027", paths, _payload())
    detail = _detail(exc.value)
    assert detail["error_code"] == "scenario_year_coverage_incomplete"
    assert set(detail["details"]["missing_by_year"]["2"]) == {
        "gdp_growth",
        "gse_index",
        "unemployment",
    }


def test_excluding_fx_from_the_run_relaxes_only_the_fx_driver() -> None:
    """``include_fx=False`` drops the FX module — but liquidity still reads FX."""
    paths = [point for point in _complete_paths() if point.variable != "fx_usd_ghs"]
    with pytest.raises(svc.EnterpriseStressError) as exc:
        svc._require_complete_scenario("adverse_2027", paths, _payload(include_fx=False))
    missing = _detail(exc.value)["details"]["missing_by_module"]
    assert "fx" not in missing
    assert "liquidity" in missing


# --- P0-13: invented ICAAP assumptions are bounded and disclosed --------------


def test_an_omitted_plan_records_every_platform_default() -> None:
    resolved = svc._resolve_plan(None, _complete_paths())

    assert resolved.fully_supplied_by_institution is False
    # Growth is derived from the scenario's own base macro, not a constant.
    for field in ("loan_growth_pct", "deposit_growth_pct"):
        assert resolved.provenance[field]["source"] == svc.MACRO_SCENARIO_SOURCE
        assert resolved.provenance[field]["value"] == "20.00"  # (0.05 + 0.15) x 100
    # The other eight fall back to platform constants, each with a written basis.
    assert set(resolved.platform_default_fields) == {
        "nim_pct",
        "cost_to_income_pct",
        "credit_loss_rate_pct",
        "fx_depreciation_pct",
        "dividend_payout_pct",
        "fee_income_pct_assets",
        "tax_rate_pct",
        "securities_shift_pp",
    }
    for field in resolved.platform_default_fields:
        entry = resolved.provenance[field]
        assert entry["source"] == svc.PLATFORM_DEFAULT_SOURCE
        assert entry["basis"]


def test_a_fully_supplied_plan_is_recorded_as_the_institutions_own() -> None:
    plan = PlanAssumptionsIn(
        loan_growth_pct=Decimal("12"),
        deposit_growth_pct=Decimal("10"),
        nim_pct=Decimal("5"),
        cost_to_income_pct=Decimal("55"),
        credit_loss_rate_pct=Decimal("2"),
        fx_depreciation_pct=Decimal("8"),
        dividend_payout_pct=Decimal("20"),
        fee_income_pct_assets=Decimal("1"),
        tax_rate_pct=Decimal("25"),
        securities_shift_pp=Decimal("0"),
    )
    resolved = svc._resolve_plan(plan, _complete_paths())
    assert resolved.fully_supplied_by_institution is True
    assert resolved.platform_default_fields == ()
    assert all(entry["source"] == svc.BANK_PLAN_SOURCE for entry in resolved.provenance.values())
    # The effective values are the institution's, not the platform's.
    assert resolved.assumptions.tax_rate_pct == Decimal("25")
    assert resolved.assumptions.nim_pct == Decimal("5")


def test_a_partially_supplied_plan_is_recorded_field_by_field() -> None:
    resolved = svc._resolve_plan(PlanAssumptionsIn(nim_pct=Decimal("6")), _complete_paths())
    assert resolved.provenance["nim_pct"]["source"] == svc.BANK_PLAN_SOURCE
    assert resolved.provenance["nim_pct"]["value"] == "6"
    assert resolved.provenance["tax_rate_pct"]["source"] == svc.PLATFORM_DEFAULT_SOURCE
    assert resolved.fully_supplied_by_institution is False


def test_growth_falls_back_to_a_platform_default_only_without_a_macro_base() -> None:
    """No GDP/inflation base path ⇒ the growth constant, and it says so."""
    paths = [point for point in _complete_paths() if point.variable != "inflation"]
    resolved = svc._resolve_plan(None, paths)
    assert resolved.provenance["loan_growth_pct"]["source"] == svc.PLATFORM_DEFAULT_SOURCE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tax_rate_pct", Decimal("-9999")),  # the audit's own example
        ("tax_rate_pct", Decimal("101")),
        ("dividend_payout_pct", Decimal("-1")),
        ("dividend_payout_pct", Decimal("150")),
        ("credit_loss_rate_pct", Decimal("-5")),
        ("cost_to_income_pct", Decimal("-1")),
        ("fee_income_pct_assets", Decimal("-2")),
        ("loan_growth_pct", Decimal("-101")),
        ("securities_shift_pp", Decimal("500")),
        ("fx_depreciation_pct", Decimal("-101")),
        ("nim_pct", Decimal("-80")),
    ],
)
def test_out_of_range_plan_assumptions_are_rejected(field: str, value: Decimal) -> None:
    with pytest.raises(ValueError):
        PlanAssumptionsIn(**{field: value})


def test_in_range_plan_assumptions_are_accepted() -> None:
    plan = PlanAssumptionsIn(tax_rate_pct=Decimal("0"), dividend_payout_pct=Decimal("100"))
    assert plan.tax_rate_pct == Decimal("0")
    assert plan.dividend_payout_pct == Decimal("100")


# --- D-8a: a credit exposure is never risk weighted by assumption ------------
#
# Audit 2026-08-22 D-8a. ``_exposure_risk_weight`` used to read
# ``risk_weights.get(code, 100)`` and hand back a flat 100% for a row carrying no
# code at all, so a stressed CAR could be complete, plausible and built entirely
# on an assumed weight while ``capital.engine`` refused the identical input one
# module away. The fix routes both callers through the ONE registered authority,
# ``capital.engine.resolve_risk_weight``. Nothing pinned the refusal itself: the
# only tests that exercised this path used a fixture with no codes, so they were
# green under the fail-open default and red under the fix — a defect detector
# pointing the wrong way. These four are the behavioural anchor.


def _exposure_row(reference: str, *, code: str | None, product_code: str | None = None) -> Any:
    """A minimal flattened credit exposure. ``code`` rides on the snapshot
    attributes (the source-system path); ``product_code`` on the product register
    (the ingested ``CanonicalProduct.risk_weight_code`` path)."""
    return svc._ExposureRow(  # noqa: SLF001 - the unit under test is module-private
        source_reference=reference,
        position_type="LOAN",
        currency="GHS",
        balance_ghs=Decimal("1000000"),
        is_foreign_currency=False,
        notional_ghs=Decimal("0"),
        ifrs9_stage=1,
        attributes={"risk_weight_code": code} if code is not None else {},
        counterparty_type="CORPORATE",
        counterparty_resident=True,
        counterparty_country="GH",
        group_key="cp:Test Corp",
        regulatory_category="CORPORATE_UNRATED",
        product_risk_weight_code=product_code,
        product_code="LN.CORP",
    )


def _capital_params(**weights: str) -> Any:
    return CapitalParams(
        risk_weights={code: Decimal(value) for code, value in weights.items()},
        bia_alpha_pct=Decimal("15"),
        fx_charge_pct=Decimal("8"),
        rwa_multiplier_pct=Decimal("100"),
        tier2_gp_cap_pct_credit_rwa=Decimal("1.25"),
        cet1_min_pct=Decimal("6.5"),
        tier1_min_pct=Decimal("8"),
        car_min_pct=Decimal("10"),
        leverage_min_pct=Decimal("6"),
        car_early_warning_pct=Decimal("10.5"),
        car_critical_pct=Decimal("9"),
    )


def test_an_exposure_with_no_risk_weight_code_refuses_the_run() -> None:
    """The old flat 100%. A risk weight is a determination about the exposure."""
    with pytest.raises(svc.EnterpriseStressError) as exc:
        svc._build_credit_exposures(  # noqa: SLF001
            [_exposure_row("LOAN/NOCODE", code=None)], _capital_params(RW100="100")
        )

    detail = _detail(exc.value)
    assert detail["error_code"] == "risk_weight_unresolved"
    outcome = detail["details"]["outcome"]
    assert outcome["state"] == OutcomeState.MISSING_REQUIRED_INPUT.value
    assert outcome["metric_id"] == "stressed_credit_rwa"
    assert outcome["items"] == ["exposure:LOAN/NOCODE"]
    assert detail["details"]["exposures"] == ["LOAN/NOCODE"]


def test_a_code_with_no_governed_row_refuses_and_names_the_code() -> None:
    """Coverage the parameter register does not carry is a policy gap, not a 100%."""
    with pytest.raises(svc.EnterpriseStressError) as exc:
        svc._build_credit_exposures(  # noqa: SLF001
            [_exposure_row("LOAN/UNGOVERNED", code="RW250")], _capital_params(RW100="100")
        )

    detail = _detail(exc.value)
    assert detail["error_code"] == "risk_weight_unresolved"
    outcome = detail["details"]["outcome"]
    assert outcome["state"] == OutcomeState.POLICY_UNRESOLVED.value
    assert outcome["items"] == ["param:risk_weight:RW250"]
    assert detail["details"]["unresolved_codes"] == ["RW250"]


def test_the_refusal_names_every_offending_exposure_not_just_the_first() -> None:
    """A book with no governed coverage at all must not report one arbitrary row —
    an operator has to be able to act on the refusal without re-running anything."""
    rows = [_exposure_row(f"LOAN/{index}", code=None) for index in range(3)]
    with pytest.raises(svc.EnterpriseStressError) as exc:
        svc._build_credit_exposures(rows, _capital_params(RW100="100"))  # noqa: SLF001

    detail = _detail(exc.value)
    assert detail["details"]["exposure_count"] == 3
    assert detail["details"]["outcome"]["items"] == [
        "exposure:LOAN/0",
        "exposure:LOAN/1",
        "exposure:LOAN/2",
    ]


def test_a_governed_code_resolves_from_either_the_snapshot_or_the_product() -> None:
    """Both ingestion paths reach the same authority and the same weight."""
    exposures = svc._build_credit_exposures(  # noqa: SLF001
        [
            _exposure_row("LOAN/ATTR", code="RW100"),
            _exposure_row("LOAN/PRODUCT", code=None, product_code="RW50"),
        ],
        _capital_params(RW100="100", RW50="50"),
    )

    assert [(item.exposure_id, item.risk_weight_pct) for item in exposures] == [
        ("LOAN/ATTR", Decimal("100")),
        ("LOAN/PRODUCT", Decimal("50")),
    ]
