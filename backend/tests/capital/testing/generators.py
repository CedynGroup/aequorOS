"""Synthetic balanced bank books around the governed capital floor."""

from dataclasses import dataclass
from decimal import Decimal

from hypothesis import strategies as st

from app.domain.capital.engine import CapitalFact, CapitalParams, compute_rwa, money
from app.services.parameter_register import BANK_CAPITAL_THRESHOLDS, BANK_RISK_WEIGHTS
from app.services.regulatory_parameters import SEED_PARAMETERS


def capital_params() -> CapitalParams:
    """Rates: parameter_register.BANK_*; floors: regulatory_parameters.SEED_PARAMETERS.

    Bank CAR includes CRD 2018 paragraphs 71/75's conservation buffer. Pending
    CET1/Tier 1 seeds retain their documented status; these are synthetic inputs,
    not a claim of resolved authority for a real institution.
    """
    floors = {
        s.param_code: Decimal(s.value)
        for s in SEED_PARAMETERS
        if s.scope_key == "bank" and s.value is not None
    }
    board = {k: Decimal(v) for k, v in BANK_CAPITAL_THRESHOLDS.items()}
    return CapitalParams(
        risk_weights={k: Decimal(v) for k, v in BANK_RISK_WEIGHTS.items()},
        bia_alpha_pct=board["bia_alpha_pct"],
        fx_charge_pct=board["fx_charge_pct"],
        rwa_multiplier_pct=board["rwa_multiplier"],
        tier2_gp_cap_pct_credit_rwa=board["tier2_gp_cap_pct_credit_rwa"],
        cet1_min_pct=floors["cet1_min"],
        tier1_min_pct=floors["tier1_min"],
        car_min_pct=floors["car_min"],
        leverage_min_pct=floors["leverage_min"],
        car_early_warning_pct=board["car_early_warning"],
        car_critical_pct=board["car_critical"],
    )


@dataclass(frozen=True)
class CapitalBook:
    facts: tuple[CapitalFact, ...]
    target_car_pct: Decimal


@st.composite
def capital_books(draw: st.DrawFn) -> CapitalBook:
    """CRD floor source: SEED_PARAMETERS; 4dp MONEY in domain/capital/engine.py.

    Synthetic GHS loans/deposits, three positive BIA years and paid-up equity.
    Concentrate on either side of the governed CAR minimum, mixing broader
    solvent books. Calibrate equity with the public RWA engine rather than
    copying its weighting or BIA formula. No customer data is used.
    """
    params = capital_params()
    assets = Decimal(draw(st.integers(10_000_000, 1_000_000_000)))
    category, rw = draw(
        st.sampled_from(
            (
                ("residential_mortgage", "RW35"),
                ("sme_retail", "RW75"),
                ("corporate_unrated", "RW100"),
            )
        )
    )
    offset = draw(
        st.one_of(
            st.sampled_from(
                tuple(Decimal(v) for v in ("-0.000001", "0", "0.000001", "-0.5", "0.5"))
            ),
            st.decimals(Decimal("-2"), Decimal("5"), places=2),
        )
    )
    target = params.car_min_pct + offset
    income = money(assets * Decimal(draw(st.integers(1, 5))) / 100)
    base = (
        CapitalFact("balance_sheet", "loans_gross", assets, side="asset"),
        CapitalFact("loan_exposure", category, assets, risk_weight_code=rw),
        *(
            CapitalFact("operational_income", f"gross_income_{year}", income, income_year=year)
            for year in (2023, 2024, 2025)
        ),
    )
    equity = money(compute_rwa(base, params).total_rwa * target / 100)
    retained = money(equity / 4)
    return CapitalBook(
        (
            *base,
            CapitalFact(
                "balance_sheet", "retail_deposits_stable", assets - equity, side="liability"
            ),
            CapitalFact("balance_sheet", "capital_total", equity, side="equity"),
            CapitalFact(
                "capital_component", "paid_up_capital", equity - retained, capital_tier="CET1"
            ),
            CapitalFact("capital_component", "retained_earnings", retained, capital_tier="CET1"),
        ),
        target,
    )


def capital_percentages() -> st.SearchStrategy[Decimal]:
    """ICAAP units accept nonnegative percentages; CRD CAR floor comes from seeds."""
    floor = capital_params().car_min_pct
    return st.one_of(
        st.sampled_from((Decimal(0), floor - Decimal("0.01"), floor, floor + Decimal("0.01"))),
        st.decimals(Decimal(0), Decimal(100), places=4, allow_nan=False),
    )


def rwa_amounts() -> st.SearchStrategy[int]:
    """Synthetic GHS denominators; units.py needs positive RWA, engine MONEY is 4dp."""
    return st.one_of(st.sampled_from((1, 1000, 1_000_000)), st.integers(1_000_000, 1_000_000_000))
