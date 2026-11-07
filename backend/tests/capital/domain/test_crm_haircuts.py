"""Hand-verified golden tests for CRM supervisory-haircut recognition (issue 815).

Basis: Basel II comprehensive-approach supervisory haircuts (¶151 table) as
coded in ``app.capital.domain.crm_haircuts.DEFAULT_CRM_HAIRCUTS``, and BoG CRD
(June 2018) ¶98 netting: recognized collateral value nets its loan family's
credit exposure once, capped at the exposure. Every expected value is an
independently hand-computed Decimal literal in reporting-currency units
(haircut percentages as whole numbers); no expected value calls through an
engine helper.

Boundary notes (current interface, verified by source):
- Zero collateral, over-collateralisation, every default haircut bundle, and
  the allocation cap's ± one-money-unit sides are covered below.
- Maturity: the Basel comprehensive-approach maturity adjustment is NOT part
  of this interface — recognition depends only on the collateral value, its
  class haircut and the loan family (``app/capital/domain/engine.py``,
  ``credit_collateral_by_category``). No maturity boundary is asserted and
  none may be invented here; a maturity dimension would be a product change.
- The class-absent-from-mapping zero-recognition case is already pinned by
  ``tests/capital/service/test_ecl_and_crm_integration.py`` through
  ``compute_rwa``; those goldens are not duplicated here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.capital.domain.crm_haircuts import DEFAULT_CRM_HAIRCUTS
from app.capital.domain.engine import (
    CapitalFact,
    CapitalParams,
    compute_rwa,
    credit_collateral_by_category,
)

M = Decimal("1000000")


def _params(haircuts: dict[str, Decimal]) -> CapitalParams:
    return CapitalParams(
        risk_weights={"RW100": Decimal("100"), "RW0": Decimal("0")},
        bia_alpha_pct=Decimal("15"),
        fx_charge_pct=Decimal("10"),
        rwa_multiplier_pct=Decimal("1250"),
        tier2_gp_cap_pct_credit_rwa=Decimal("1.25"),
        cet1_min_pct=Decimal("6.5"),
        tier1_min_pct=Decimal("8"),
        car_min_pct=Decimal("13"),
        leverage_min_pct=Decimal("6"),
        car_early_warning_pct=Decimal("14"),
        car_critical_pct=Decimal("10"),
        crm_haircuts=haircuts,
    )


def _loan(family: str, amount: Decimal) -> CapitalFact:
    return CapitalFact(
        fact_group="loan_exposure", category=family, amount=amount, risk_weight_code="RW100"
    )


def _collateral(family: str, collateral_class: str, amount: Decimal) -> CapitalFact:
    return CapitalFact(
        fact_group="crm_collateral",
        category=f"{family}:{collateral_class}",
        amount=amount,
    )


def test_zero_collateral_leaves_credit_exposure_untouched() -> None:
    """Haircuts configured but no ``crm_collateral`` facts: nothing is netted.

    Recognition must never invent a haircut's collateral out of thin air, so
    the allocation for every exposure family is exactly zero.
    """
    facts = (_loan("commercial_loans", Decimal("50000000")),)
    allocated = credit_collateral_by_category(facts, _params({"CASH": Decimal("0")}))
    assert allocated == {"commercial_loans": Decimal("0")}


def test_over_collateral_caps_at_exposure_and_never_spills_across_families() -> None:
    """100M recognized against a 50M exposure nets it to zero; the 50M excess
    stays stranded in its own family — the 10M loan of another family, which
    has no collateral of its own, keeps its full gross exposure.
    """
    facts = (
        _loan("commercial_loans", Decimal("50000000")),
        _loan("sme_loans", Decimal("10000000")),
        _collateral("commercial_loans", "CASH", Decimal("100000000")),
    )
    allocated = credit_collateral_by_category(facts, _params({"CASH": Decimal("0")}))
    assert allocated == {
        "commercial_loans": Decimal("50000000"),
        "sme_loans": Decimal("0"),
    }


@pytest.mark.parametrize(
    ("exposure", "collateral_value", "allocated_expected"),
    (
        # Exposure one money unit above the recognition pool: capped at the
        # pool, netting leaves 0.0001 of exposure uncollateralized.
        (Decimal("50000000.0001"), Decimal("50000000"), Decimal("50000000")),
        # Exposure one money unit below the pool: capped at the exposure
        # itself, so the whole exposure is collateralized to zero.
        (Decimal("49999999.9999"), Decimal("50000000"), Decimal("49999999.9999")),
    ),
)
def test_allocation_cap_boundary_sides_within_one_money_unit(
    exposure: Decimal, collateral_value: Decimal, allocated_expected: Decimal
) -> None:
    """BoG CRD ¶98: recognition nets a bucket at most to zero, never below."""
    facts = (
        _loan("commercial_loans", exposure),
        _collateral("commercial_loans", "CASH", collateral_value),
    )
    allocated = credit_collateral_by_category(facts, _params({"CASH": Decimal("0")}))
    assert allocated == {"commercial_loans": allocated_expected}


@pytest.mark.parametrize(
    ("collateral_class", "expected_recognized"),
    (
        # Hand-computed from an 8,000,000.00 CASH collateral bundle at each
        # DEFAULT_CRM_HAIRCUTS supervisory percentage: value x (100 - pct)/100.
        ("CASH", Decimal("8000000")),  # 8,000,000 x (100-0)/100
        ("GOLD", Decimal("6800000")),  # 8,000,000 x (100-15)/100
        ("SOVEREIGN_DEBT", Decimal("7680000")),  # 8,000,000 x (100-4)/100
        ("BANK_DEBT", Decimal("7360000")),  # 8,000,000 x (100-8)/100
        ("CORPORATE_DEBT", Decimal("7360000")),  # 8,000,000 x (100-8)/100
        ("EQUITY_MAIN_INDEX", Decimal("6800000")),  # 8,000,000 x (100-15)/100
        ("EQUITY_OTHER", Decimal("6000000")),  # 8,000,000 x (100-25)/100
    ),
)
def test_every_default_haircut_bundle_recognizes_at_its_supervisory_percentage(
    collateral_class: str, expected_recognized: Decimal
) -> None:
    """The code-default table drives real percentage conversion per class.

    The engine reads each class from ``DEFAULT_CRM_HAIRCUTS`` itself; the
    expected value is a hand-computed literal, so a table drift breaks the
    golden instead of passing silently.
    """
    family = f"loans_{collateral_class.lower()}"
    facts = (
        _loan(family, Decimal("10000000")),
        _collateral(family, collateral_class, Decimal("8000000")),
    )
    allocated = credit_collateral_by_category(facts, _params(dict(DEFAULT_CRM_HAIRCUTS)))
    assert allocated == {family: expected_recognized}


def test_haircut_percentage_conversion_rounds_half_up_at_the_money_boundary() -> None:
    """1,000,000.005 x (100-15)/100 = 850,000.00425 -> money(4dp, HALF_UP).

    Recognition quantizes to money units after the percentage conversion, so
    the fourth decimal's 5 rounds up: 850,000.0043.
    """
    facts = (
        _loan("commercial_loans", Decimal("2000000")),
        _collateral("commercial_loans", "GOLD", Decimal("1000000.005")),
    )
    allocated = credit_collateral_by_category(facts, _params({"GOLD": Decimal("15")}))
    assert allocated == {"commercial_loans": Decimal("850000.0043")}


def test_collateral_facts_accumulate_within_one_family_before_allocation() -> None:
    """A family's pool sums every class's post-haircut value before netting.

    30,000,000 CASH (0%) + 20,000,000 GOLD at 15% -> 20,000,000 x 85/100 =
    17,000,000; the family pool is 47,000,000 against a 50,000,000 exposure.
    """
    facts = (
        _loan("commercial_loans", Decimal("50000000")),
        _collateral("commercial_loans", "CASH", Decimal("30000000")),
        _collateral("commercial_loans", "GOLD", Decimal("20000000")),
    )
    allocated = credit_collateral_by_category(
        facts, _params({"CASH": Decimal("0"), "GOLD": Decimal("15")})
    )
    assert allocated == {"commercial_loans": Decimal("47000000")}


def test_collateral_category_without_a_family_is_never_recognized() -> None:
    """A ``crm_collateral`` category with no ``<family>:<class>`` separator
    cannot be attributed to any loan family, so it recognizes nothing — even
    a same-named class after a separator is the only recognized value.
    """
    facts = (
        _loan("commercial_loans", Decimal("10000000")),
        CapitalFact(
            fact_group="crm_collateral", category="ORPHANED_CASH", amount=Decimal("90000000")
        ),
        _collateral("commercial_loans", "CASH", Decimal("5000000")),
    )
    allocated = credit_collateral_by_category(facts, _params({"CASH": Decimal("0")}))
    assert allocated == {"commercial_loans": Decimal("5000000")}


def test_zero_value_collateral_recognizes_nothing() -> None:
    """A zero-amount collateral fact is the value domain's floor: recognition
    0 x (100-pct)/100 = 0, so nothing nets against the exposure.
    """
    facts = (
        _loan("commercial_loans", Decimal("10000000")),
        _collateral("commercial_loans", "GOLD", Decimal("0")),
    )
    allocated = credit_collateral_by_category(facts, _params({"GOLD": Decimal("15")}))
    assert allocated == {"commercial_loans": Decimal("0")}


def test_over_collateralized_credit_exposure_nets_stage_buckets_in_order() -> None:
    """The CRD ¶98 credit-exposure path: one family pool fills its buckets in
    category order, each capped at the bucket, never negative.

    25,000,000 CASH (0%) against "corporate_loans:stage1" 20,000,000 nets it
    to exactly zero; the 5,000,000 remainder flows to "corporate_loans:stage2"
    (10,000,000), leaving 5,000,000 of exposure. Reported through
    ``compute_rwa`` line items so the netting reaches the reported RWA basis.
    """
    facts = (
        CapitalFact(
            fact_group="credit_exposure",
            category="corporate_loans:stage1",
            amount=Decimal("20000000"),
            risk_weight_code="RW100",
        ),
        CapitalFact(
            fact_group="credit_exposure",
            category="corporate_loans:stage2",
            amount=Decimal("10000000"),
            risk_weight_code="RW100",
        ),
        _collateral("corporate_loans", "CASH", Decimal("25000000")),
        CapitalFact(
            fact_group="operational_income",
            category="gross_income",
            amount=Decimal("10000000"),
            income_year=2025,
        ),
    )
    rwa = compute_rwa(facts, _params({"CASH": Decimal("0")}))
    stage1 = next(item for item in rwa.line_items if item.line_code == "corporate_loans:stage1")
    stage2 = next(item for item in rwa.line_items if item.line_code == "corporate_loans:stage2")
    assert stage1.exposure_amount == Decimal("0.0000")
    assert stage1.weighted_amount == Decimal("0.0000")
    assert stage2.exposure_amount == Decimal("5000000.0000")
    assert stage2.weighted_amount == Decimal("5000000.0000")
