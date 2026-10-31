"""Synthetic five-horizon ladders and monitoring-threshold inputs."""

from dataclasses import dataclass
from decimal import Decimal

from hypothesis import strategies as st

from app.liquidity.domain.engine import (
    LiquidityFact,
    LiquidityParams,
    compute_lcr,
    compute_nsfr,
    money,
)
from app.policy.service.parameter_register import (
    BANK_CAPITAL_THRESHOLDS,
    BANK_LCR_INFLOWS,
    BANK_LCR_OUTFLOWS,
    BANK_NSFR_ASF,
    BANK_NSFR_RSF,
)
from app.policy.service.regulatory_parameters import SEED_PARAMETERS


def liquidity_params() -> LiquidityParams:
    """BANK_* register constants and SEED_PARAMETERS HQLA rates/caps are the sources.

    LCR/NSFR 100% are institution monitoring targets, not BoG requirements:
    parameter_register.BANK_CAPITAL_THRESHOLDS documents this distinction.
    """
    board = {k: Decimal(v) for k, v in BANK_CAPITAL_THRESHOLDS.items()}
    seeds = {
        s.param_code: Decimal(s.value)
        for s in SEED_PARAMETERS
        if s.scope_key == "bank" and s.value is not None
    }
    return LiquidityParams(
        outflow_rates={k: Decimal(v) for k, v in BANK_LCR_OUTFLOWS.items()},
        inflow_rates={k: Decimal(v) for k, v in BANK_LCR_INFLOWS.items()},
        asf_weights={k: Decimal(v) for k, v in BANK_NSFR_ASF.items()},
        rsf_weights={k: Decimal(v) for k, v in BANK_NSFR_RSF.items()},
        inflow_cap_pct=board["lcr_inflow_cap_pct"],
        lcr_min_pct=board["lcr_min"],
        lcr_amber_floor_pct=board["lcr_amber_floor"],
        nsfr_min_pct=board["nsfr_min"],
        nsfr_amber_floor_pct=board["lcr_amber_floor"],
        hqla_haircut_pct={
            level: seeds[f"hqla_{level.lower()}_haircut_pct"] for level in ("L1", "L2A", "L2B")
        },
        hqla_level2_cap_pct=seeds["hqla_level2_cap_pct"],
        hqla_level2b_cap_pct=seeds["hqla_level2b_cap_pct"],
    )


@dataclass(frozen=True)
class LiquidityBook:
    facts: tuple[LiquidityFact, ...]
    target_lcr_pct: Decimal
    target_nsfr_pct: Decimal


@st.composite
def liquidity_books(draw: st.DrawFn) -> LiquidityBook:
    """Register rates above; engine.py MONEY/RATIO_PCT and L1 HQLA taxonomy.

    Synthetic retail funding, corporate loans and Level-1 securities calibrated
    around internal LCR/NSFR targets. Include exact thresholds and wider edges.
    The funding mix is calibrated with public engine observations; assertions compare runs.
    """
    params = liquidity_params()
    deposits = Decimal(draw(st.integers(10_000_000, 1_000_000_000)))
    offsets = st.one_of(
        st.sampled_from(tuple(Decimal(v) for v in ("-0.000001", "0", "0.000001", "-1", "1"))),
        st.decimals(Decimal("-10"), Decimal("10"), places=2),
    )
    lcr = params.lcr_min_pct + draw(offsets)
    nsfr = params.nsfr_min_pct + draw(offsets)
    equity = money(deposits / 10)

    def candidate(wholesale: Decimal) -> tuple[LiquidityFact, ...]:
        funding = (
            LiquidityFact(
                "balance_sheet", "retail_deposits_stable", deposits - wholesale, side="liability"
            ),
            LiquidityFact("balance_sheet", "wholesale_operational", wholesale, side="liability"),
            LiquidityFact("balance_sheet", "capital_total", equity, side="equity"),
        )
        outflow = compute_lcr(funding, params).net_outflows_total
        stock = money(outflow * lcr / 100)
        loans = deposits + equity - stock
        return (
            *funding,
            LiquidityFact("balance_sheet", "securities_gog_bonds", stock, side="asset"),
            LiquidityFact("balance_sheet", "loans_gross", loans, side="asset"),
            LiquidityFact("loan_exposure", "corporate_unrated", loans),
            LiquidityFact("securities", "securities_gog_bonds", stock, hqla_level="L1"),
        )

    # Calibrate the synthetic funding mix with public engine observations. Keep
    # both balance-sheet equality and loan/security tie-outs during every step.
    lower, upper = Decimal(0), deposits
    facts = candidate(lower)
    for _ in range(50):
        midpoint = money((lower + upper) / 2)
        facts = candidate(midpoint)
        actual = compute_nsfr(facts, params).nsfr_pct
        if actual == nsfr:
            break
        if actual > nsfr:
            lower = midpoint
        else:
            upper = midpoint
    return LiquidityBook(facts, lcr, nsfr)


@st.composite
def liquidity_ladders(draw: st.DrawFn) -> dict[str, dict[str, list[Decimal]]]:
    """compute_currency_gaps: five LMTD horizons, GHS equivalents, MONEY 4dp.

    Positive assets/liabilities generate signed gaps; zero buckets are deliberate.
    Totals are derived from these buckets by the public engine, never invented.
    """
    bucket = st.one_of(
        st.just(Decimal(0)),
        st.decimals(Decimal("100000"), Decimal("100000000"), places=4, allow_nan=False),
    )
    currencies = draw(
        st.lists(st.sampled_from(("GHS", "USD", "EUR")), min_size=1, max_size=3, unique=True)
    )
    return {
        currency: {
            side: draw(st.lists(bucket, min_size=5, max_size=5))
            for side in ("assets", "liabilities")
        }
        for currency in currencies
    }
