"""The governed GA rows as the console seeds them, and the reference books.

These are FIXTURES, not defaults. The domain refuses without them (D-024), and
every figure the reference cases assert is a figure these bodies produced — so
changing a value here must change a test expectation. That is the point: the
calibration lives in the control plane, and these tests prove the control plane
is what moves the number.

Each row is seeded REPRESENTATIVE and pending confirmation. The correlation and
maturity coefficients are the Basel IRB functions; δ, γ and the effective-name
floor are platform methodology with no published regulator basis, which is why
the method's own output says so.

The books below are the exposure sets P5-DESIGN §4.6 names. They are shared by
the pure-domain cases and the Pillar 2 adapter cases so both assert the same
arithmetic on the same input, rather than two nearly-identical books drifting
apart.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.domain.credit.granularity import (
    SEGMENT_CORPORATE,
    SEGMENT_RETAIL_OTHER,
    Correlation,
    GaExposure,
    GaParams,
    MaturityAdjustment,
    Segment,
)

# --- console rows, as the operator stores them ------------------------------

CONFIDENCE_Q = "0.999"
DELTA = "4.83"
LGD_VARIANCE_GAMMA = "0.25"
MIN_EFFECTIVE_NAMES = "50"
DEFAULT_ELGD_PCT = "45"

ASSET_CORRELATION_BODY: dict[str, Any] = {
    SEGMENT_CORPORATE: {"r_min": "0.12", "r_max": "0.24", "k": "50"},
    SEGMENT_RETAIL_OTHER: {"r_min": "0.03", "r_max": "0.16", "k": "35"},
}
MATURITY_ADJUSTMENT_BODY: dict[str, Any] = {
    "apply": False,
    "b_intercept": "0.11852",
    "b_slope": "0.05478",
    "m_centre": "2.5",
    "m_scale": "1.5",
}
MATURITY_ADJUSTMENT_ON_BODY: dict[str, Any] = {**MATURITY_ADJUSTMENT_BODY, "apply": True}

#: The exact δ the gamma-factor derivation gives (ξ = 0.25, α = 0.999). The
#: seeded row rounds it; a reference case proves what that rounding costs.
DELTA_EXACT = "4.833601258193016"

# --- the same rows, parsed ---------------------------------------------------

CORRELATIONS: dict[str, Correlation] = {
    SEGMENT_CORPORATE: (Decimal("0.12"), Decimal("0.24"), Decimal(50)),
    SEGMENT_RETAIL_OTHER: (Decimal("0.03"), Decimal("0.16"), Decimal(35)),
}
MATURITY_OFF = MaturityAdjustment(
    apply=False,
    b_intercept=Decimal("0.11852"),
    b_slope=Decimal("0.05478"),
    m_centre=Decimal("2.5"),
    m_scale=Decimal("1.5"),
)
MATURITY_ON = MaturityAdjustment(
    apply=True,
    b_intercept=MATURITY_OFF.b_intercept,
    b_slope=MATURITY_OFF.b_slope,
    m_centre=MATURITY_OFF.m_centre,
    m_scale=MATURITY_OFF.m_scale,
)


def params(
    *,
    delta: str = DELTA,
    min_effective_names: str = MIN_EFFECTIVE_NAMES,
    ma: MaturityAdjustment = MATURITY_OFF,
) -> GaParams:
    """The seeded calibration, with the one row a case varies overridden."""
    return GaParams(
        q=Decimal(CONFIDENCE_Q),
        delta=Decimal(delta),
        gamma=Decimal(LGD_VARIANCE_GAMMA),
        min_effective_names=Decimal(min_effective_names),
        corr=CORRELATIONS,
        ma=ma,
    )


#: The gate, switched off, so a reference case can state the formula's value for
#: a book the seeded floor would decline. Never a production configuration.
def ungated(**kwargs: Any) -> GaParams:
    return params(min_effective_names="0", **kwargs)


# --- the reference books (P5-DESIGN §4.6) -----------------------------------

PD_ONE_PCT = Decimal("0.01")
PD_TWO_PCT = Decimal("0.02")
PD_THREE_PCT = Decimal("0.03")
PD_FIVE_PCT = Decimal("0.05")
ELGD_45 = Decimal("0.45")
ELGD_25 = Decimal("0.25")


def names(  # noqa: PLR0913 - one argument per dimension of a reference book
    count: int,
    *,
    ead: str,
    pd: Decimal,
    elgd: Decimal = ELGD_45,
    segment: Segment = SEGMENT_CORPORATE,
    maturity: str | None = None,
    start: int = 1,
) -> list[GaExposure]:
    """``count`` single-exposure obligors, each its own connected group."""
    return [
        GaExposure(
            ref=f"E{index:05d}",
            group_key=f"G{index:05d}",
            ead=Decimal(ead),
            pd=pd,
            elgd=elgd,
            segment=segment,
            maturity_years=None if maturity is None else Decimal(maturity),
            pd_source="exposure",
            lgd_source="exposure",
        )
        for index in range(start, start + count)
    ]


def homogeneous(
    count: int,
    *,
    segment: Segment = SEGMENT_CORPORATE,
    maturity: str | None = None,
) -> list[GaExposure]:
    """``count`` equal names at PD 2% — the design's scaling cases."""
    return names(count, ead="1", pd=PD_TWO_PCT, segment=segment, maturity=maturity)


def three_name_book() -> list[GaExposure]:
    """EAD [600, 300, 100] at PD [2%, 1%, 5%]; N_eff 2.17, GA above K*."""
    return [
        GaExposure("A", "A", Decimal(600), PD_TWO_PCT, ELGD_45, SEGMENT_CORPORATE),
        GaExposure("B", "B", Decimal(300), PD_ONE_PCT, ELGD_45, SEGMENT_CORPORATE),
        GaExposure("C", "C", Decimal(100), PD_FIVE_PCT, ELGD_45, SEGMENT_CORPORATE),
    ]


def sixty_name_book(small_elgd: Decimal = ELGD_45) -> list[GaExposure]:
    """10 x EAD 100 @ PD 1% + 50 x EAD 20 @ PD 3%; N_eff 33.33, so gated out."""
    return [
        *names(10, ead="100", pd=PD_ONE_PCT),
        *names(50, ead="20", pd=PD_THREE_PCT, elgd=small_elgd, start=11),
    ]


def gate_passing_book() -> list[GaExposure]:
    """20 x EAD 50 @ PD 1% + 100 x EAD 10 @ PD 3%; N_eff 66.67, so it runs."""
    return [
        *names(20, ead="50", pd=PD_ONE_PCT),
        *names(100, ead="10", pd=PD_THREE_PCT, start=21),
    ]
