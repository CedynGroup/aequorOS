"""Shared synthetic repricing cash flows and governed IRRBB shocks."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from hypothesis import strategies as st

from app.icaap.domain.pillar2.irrbb import ScenarioDelta
from app.irrbb.domain.standardised import CurrencyInputs, Ladder, LadderKind, SfInputs
from app.irrbb.domain.standardised import run as sf_run
from tests.irrbb.domain.test_sf_fixtures import running_outstanding, sf_parameters

PARAMS = sf_parameters()
AS_OF = date(2026, 12, 31)
ZERO = Decimal(0)

# Preserve mixed-sign cancellation probes from test_sf_properties explicitly.
adversarial_amounts = st.lists(
    st.integers(-200_000, 200_000).map(lambda cents: Decimal(cents) / 100),
    min_size=PARAMS.bucket_count,
    max_size=PARAMS.bucket_count,
)


@st.composite
def repricing_amounts(draw: st.DrawFn) -> list[Decimal]:
    """Signed zero-coupon asset/funding portfolios with consistent outstanding.

    Source: domain/irr/standardised.py Ladder: assets positive, funding negative;
    six-place HALF_UP results. Bucket/shock calibration comes from the existing
    test_sf_fixtures.py governed parser fixture (IRRBB exposure draft, pending).
    Synthetic 100k–5m bullet cash flows favour the first five years; a zero book
    and long-tail maturities are deliberate edges, not production limits.
    """
    amounts = [ZERO] * PARAMS.bucket_count
    if draw(st.integers(0, 9)) == 0:
        return amounts
    sign = draw(st.sampled_from((1, -1)))
    for _ in range(draw(st.integers(1, 4))):
        bucket = draw(st.one_of(st.integers(0, 10), st.integers(0, PARAMS.bucket_count - 1)))
        amounts[bucket] += sign * Decimal(draw(st.integers(100_000, 5_000_000)))
    return amounts


amounts = st.one_of(
    repricing_amounts(), repricing_amounts(), repricing_amounts(), adversarial_amounts
)
behavioural_rates = st.one_of(
    st.sampled_from((ZERO, Decimal(1))), st.integers(0, 100).map(lambda pct: Decimal(pct) / 100)
)
zero_rates = st.one_of(
    st.integers(500, 3000).map(lambda bp: Decimal(bp) / 10000),
    st.integers(-200, 3000).map(lambda bp: Decimal(bp) / 10000),
)


def ladder(currency: str, principal: list[Decimal], kind: LadderKind = "fixed") -> Ladder:
    """Construct scheduled outstanding from signed principal, not from risk output."""
    return Ladder(
        currency=currency,
        portfolio=f"{currency}|{kind}",
        kind=kind,
        principal=tuple(principal),
        interest=tuple(ZERO for _ in principal),
        outstanding_end=running_outstanding(principal),
    )


def currency_inputs(currency: str, rate: Decimal, size: Decimal) -> CurrencyInputs:
    """Synthetic flat continuous curve; no external market data is consumed."""
    return CurrencyInputs(
        currency, tuple(rate for _ in range(PARAMS.bucket_count)), Decimal(1), size, size
    )


@st.composite
def irrbb_books(draw: st.DrawFn) -> SfInputs:
    """Asset and funding ladders under the parsed standard shock set.

    Source: test_sf_fixtures.py seed_rows / sf_parameters; the 15% Tier 1 outlier
    threshold is pending exposure-draft data, never a hardcoded engine fallback.
    Capital is chosen around that governed boundary using a public engine run;
    the generator does not duplicate discounting or scenario aggregation.
    """
    assets = [abs(value) for value in draw(repricing_amounts())]
    funding = [-abs(value) for value in draw(repricing_amounts())]
    book = SfInputs(
        AS_OF,
        "GHS",
        (
            replace(ladder("GHS", assets), portfolio="synthetic_assets"),
            replace(ladder("GHS", funding), portfolio="synthetic_funding"),
        ),
        (),
        (
            CurrencyInputs(
                "GHS",
                tuple(Decimal("0.25") for _ in assets),
                Decimal(1),
                sum(assets, ZERO),
                -sum(funding, ZERO),
            ),
        ),
        Decimal(1_000_000),
    )
    measure = sf_run(book, PARAMS).measures.outlier_set.measure
    target = PARAMS.outlier_threshold_pct + draw(
        st.sampled_from((Decimal("-0.000001"), ZERO, Decimal("0.000001")))
    )
    return replace(book, tier1=measure * 100 / target if measure > 0 else Decimal(1_000_000))


def interim_deltas(book: SfInputs) -> tuple[ScenarioDelta, ...]:
    """Bridge sign conventions: SF positive is loss; interim negative is loss."""
    return tuple(ScenarioDelta(row.code, -row.net) for row in sf_run(book, PARAMS).scenarios)
