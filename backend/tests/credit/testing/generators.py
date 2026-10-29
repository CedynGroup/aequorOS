"""Synthetic bank loan books and connected-group exposures."""

from dataclasses import dataclass
from decimal import Decimal

from hypothesis import strategies as st

from app.domain.capital.loan_classification import (
    ClassificationGrid,
    LoanExposure,
    classify_book,
    grid_from_params,
)
from app.domain.stress.concentration import ConcentrationExposure
from app.policy.service.regulatory_parameters import SEED_PARAMETERS


def bank_parameters() -> dict[str, Decimal]:
    """Source: regulatory_parameters.SEED_PARAMETERS, institution_class=bank.

    Classification rates/boundaries cite the BoG five-grade grid; the large
    exposure limit cites the September 2025 Large Exposures Directive.
    """
    return {
        s.param_code: Decimal(s.value)
        for s in SEED_PARAMETERS
        if s.scope_key == "bank" and s.value is not None
    }


def credit_grid() -> ClassificationGrid:
    """Source: loan_classification.grid_from_params and SEED_PARAMETERS bank rows."""
    return grid_from_params("bank", bank_parameters())


@dataclass(frozen=True)
class CreditBook:
    loans: tuple[LoanExposure, ...]
    grades: tuple[str, ...]
    booked_provisions: tuple[Decimal, ...]


@st.composite
def credit_books(draw: st.DrawFn) -> CreditBook:
    """Five-grade seed catalogue; loan_classification.py uses 4dp money, HALF_EVEN.

    Synthetic facilities of GHS 10k–10m, mixing zero and DPD boundary neighbors.
    Stage 1 is standard, stage 2 is the watch grade, stage 3 is non-performing.
    Booked provisions use the public classification engine's requirement so
    grade/stage/allowance stay consistent without a duplicate formula.
    """
    grid = credit_grid()
    edges = tuple(sorted({max(0, b.min_dpd + d) for b in grid.bands for d in (-1, 0, 1)}))
    performing = st.integers(0, grid.bands[1].min_dpd - 1)
    watch = st.integers(grid.bands[1].min_dpd, grid.npl_dpd_threshold - 1)
    dpd_strategy = st.integers(0, 9).flatmap(
        lambda choice: (
            performing
            if choice < 7
            else watch
            if choice == 7
            else st.integers(grid.npl_dpd_threshold, 730)
            if choice == 8
            else st.sampled_from(edges)
        )
    )
    dpds = draw(st.lists(dpd_strategy, min_size=1, max_size=12))
    positive = st.decimals(Decimal(10_000), Decimal(10_000_000), places=4)
    loans: list[LoanExposure] = []
    for dpd in dpds:
        amount = draw(
            st.integers(0, 9).flatmap(
                lambda choice: st.just(Decimal(0)) if choice == 9 else positive
            )
        )
        stage = 3 if dpd >= grid.npl_dpd_threshold else 2 if dpd >= grid.bands[1].min_dpd else 1
        loans.append(LoanExposure(amount, dpd, stage))
    result = classify_book(loans, grid)
    return CreditBook(
        tuple(loans),
        tuple(loan.grade for loan in result.loans),
        tuple(loan.provision_required_ghs for loan in result.loans),
    )


@dataclass(frozen=True)
class LargeExposureBook:
    exposures: tuple[ConcentrationExposure, ...]
    capital: Decimal
    target_pct: Decimal
    limit_pct: Decimal


@st.composite
def large_exposure_books(draw: st.DrawFn) -> LargeExposureBook:
    """Limit: SEED_PARAMETERS.large_exposure_limit_pct (bank); monitor has 6dp pct.

    The monitor accepts Board limits, so the statutory value is supplied as a
    synthetic Board limit for boundary exercises, not as an implicit default.
    Split one connected group across two facilities to exercise aggregation.
    """
    capital = Decimal(draw(st.integers(10_000_000, 500_000_000)))
    limit = bank_parameters()["large_exposure_limit_pct"]
    offset = draw(
        st.one_of(
            st.sampled_from(
                tuple(Decimal(x) for x in ("-0.000001", "0", "0.000001", "-0.5", "0.5"))
            ),
            st.decimals(Decimal(-2), Decimal(2), places=2),
        )
    )
    target = limit + offset
    amount = (capital * target / 100).quantize(Decimal("0.0001"))
    first = (amount / 2).quantize(Decimal("0.0001"))
    return LargeExposureBook(
        (
            ConcentrationExposure("synthetic-1", first, "synthetic-group", sector="retail"),
            ConcentrationExposure(
                "synthetic-2", amount - first, "synthetic-group", sector="retail"
            ),
            ConcentrationExposure(
                "synthetic-3", capital / 10, "synthetic-other", sector="services"
            ),
        ),
        capital,
        target,
        limit,
    )
