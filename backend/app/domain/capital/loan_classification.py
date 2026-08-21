"""Class-aware loan classification + provisioning engine (docs/sdi.md §2.2, §4).

BoG loan classification is class-aware: a bank files the 5-grade grid
(standard / OLEM / substandard / doubtful / loss) and a Significant-Deposit
Institution the 4-grade grid (standard / substandard / doubtful / loss, no
OLEM). Each grade carries a delinquency (days-past-due) boundary and a
regulatory provisioning rate; the non-performing (NPL) cutoff is a further
day-count boundary (90 days for both classes, i.e. substandard and worse).

This module is PURE — no DB, no session, no I/O (mirrors ``ecl.py``). Every
number a caller feeds in (DPD boundaries, provisioning rates, the NPL day
threshold) originates in the regulatory-parameter control plane; nothing is
hardcoded here. The two grid factories only decide the *shape* of the grid
(which grades exist for the class); every *value* arrives as an argument.

Classification is deterministic: a loan lands in the highest-DPD band whose
lower boundary it reaches. When a loan's days-past-due is not stated the engine
falls back to the IFRS 9 stage proxy (stage 3 is credit-impaired → the entry
non-performing grade; stages 1–2 → standard) and stamps the loan's
``classification_basis`` accordingly. A loan with neither a DPD nor a stage is
never silently booked as performing: it lands in a separate ``unclassified``
bucket, counted and surfaced but excluded from both the performing and the NPL
legs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

# Canonical BoG grade vocabulary (the output classification codes).
STANDARD = "standard"
OLEM = "olem"
SUBSTANDARD = "substandard"
DOUBTFUL = "doubtful"
LOSS = "loss"

#: Grade order for the bank 5-grade grid (ascending severity).
BANK_GRADE_ORDER: tuple[str, ...] = (STANDARD, OLEM, SUBSTANDARD, DOUBTFUL, LOSS)
#: Grade order for the SDI 4-grade grid (no OLEM watch grade).
SDI_GRADE_ORDER: tuple[str, ...] = (STANDARD, SUBSTANDARD, DOUBTFUL, LOSS)

#: Pseudo-grade for a loan with neither a stated DPD nor an IFRS 9 stage — kept
#: out of the performing and NPL legs so a missing signal is never read as
#: "performing".
UNCLASSIFIED = "unclassified"

#: Classification-basis stamps.
BASIS_DAYS_PAST_DUE = "days_past_due"
BASIS_STAGE_PROXY = "stage_proxy"
BASIS_UNCLASSIFIED = "unclassified"

_STAGE_IMPAIRED = 3

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_MONEY_Q = Decimal("0.0001")
_RATIO_Q = Decimal("0.000001")


class LoanClassificationError(Exception):
    """A grid or an exposure could not be classified (malformed inputs)."""


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_Q)


@dataclass(frozen=True)
class GradeBand:
    """One classification grade: its inclusive lower DPD boundary and rate.

    ``provision_rate`` is a fraction (0.20), not a percentage (20). ``min_dpd``
    is the inclusive number of days past due at which a loan enters this grade;
    the standard grade always anchors at 0. ``non_performing`` marks the grade
    as at or beyond the NPL day threshold.
    """

    grade: str
    min_dpd: int
    provision_rate: Decimal
    non_performing: bool


@dataclass(frozen=True)
class ClassificationGrid:
    """The class-specific grid: ordered grade bands + the NPL day threshold."""

    institution_class: str
    #: Bands ordered ascending by ``min_dpd`` (standard first).
    bands: tuple[GradeBand, ...]
    npl_dpd_threshold: int

    def __post_init__(self) -> None:
        if not self.bands:
            raise LoanClassificationError("A classification grid needs at least one band.")
        min_dpds = [band.min_dpd for band in self.bands]
        if min_dpds != sorted(min_dpds) or len(set(min_dpds)) != len(min_dpds):
            raise LoanClassificationError(
                "Grade bands must be ordered by a strictly increasing days-past-due boundary."
            )
        if self.bands[0].min_dpd != 0:
            raise LoanClassificationError("The first (standard) band must anchor at 0 days.")

    @property
    def grades(self) -> tuple[str, ...]:
        return tuple(band.grade for band in self.bands)

    def band_for_grade(self, grade: str) -> GradeBand:
        for band in self.bands:
            if band.grade == grade:
                return band
        raise LoanClassificationError(f"Grade {grade!r} is not defined in this grid.")

    @property
    def entry_npl_grade(self) -> str:
        """The least-severe non-performing grade (the NPL floor, e.g. substandard)."""
        for band in self.bands:
            if band.non_performing:
                return band.grade
        raise LoanClassificationError("Grid defines no non-performing grade.")


def _rate_fraction(rate_pct: Decimal) -> Decimal:
    return rate_pct / _HUNDRED


def bank_grid(  # noqa: PLR0913 - one keyword argument per resolved regulatory parameter
    *,
    dpd_olem_min: int,
    dpd_substandard_min: int,
    dpd_doubtful_min: int,
    dpd_loss_min: int,
    npl_dpd_threshold: int,
    prov_standard_pct: Decimal,
    prov_olem_pct: Decimal,
    prov_substandard_pct: Decimal,
    prov_doubtful_pct: Decimal,
    prov_loss_pct: Decimal,
) -> ClassificationGrid:
    """The bank 5-grade grid built from resolved control-plane parameters.

    All day counts and rates are supplied by the caller (resolved from the
    regulatory-parameter control plane); this factory only fixes the *shape*
    (standard / OLEM / substandard / doubtful / loss).
    """
    bands = (
        GradeBand(STANDARD, 0, _rate_fraction(prov_standard_pct), npl_dpd_threshold <= 0),
        GradeBand(
            OLEM, dpd_olem_min, _rate_fraction(prov_olem_pct), dpd_olem_min >= npl_dpd_threshold
        ),
        GradeBand(
            SUBSTANDARD,
            dpd_substandard_min,
            _rate_fraction(prov_substandard_pct),
            dpd_substandard_min >= npl_dpd_threshold,
        ),
        GradeBand(
            DOUBTFUL,
            dpd_doubtful_min,
            _rate_fraction(prov_doubtful_pct),
            dpd_doubtful_min >= npl_dpd_threshold,
        ),
        GradeBand(
            LOSS, dpd_loss_min, _rate_fraction(prov_loss_pct), dpd_loss_min >= npl_dpd_threshold
        ),
    )
    return ClassificationGrid("bank", bands, npl_dpd_threshold)


def sdi_grid(  # noqa: PLR0913 - one keyword argument per resolved regulatory parameter
    *,
    dpd_substandard_min: int,
    dpd_doubtful_min: int,
    dpd_loss_min: int,
    npl_dpd_threshold: int,
    prov_standard_pct: Decimal,
    prov_substandard_pct: Decimal,
    prov_doubtful_pct: Decimal,
    prov_loss_pct: Decimal,
) -> ClassificationGrid:
    """The SDI 4-grade grid (no OLEM) built from resolved control-plane parameters."""
    bands = (
        GradeBand(STANDARD, 0, _rate_fraction(prov_standard_pct), npl_dpd_threshold <= 0),
        GradeBand(
            SUBSTANDARD,
            dpd_substandard_min,
            _rate_fraction(prov_substandard_pct),
            dpd_substandard_min >= npl_dpd_threshold,
        ),
        GradeBand(
            DOUBTFUL,
            dpd_doubtful_min,
            _rate_fraction(prov_doubtful_pct),
            dpd_doubtful_min >= npl_dpd_threshold,
        ),
        GradeBand(
            LOSS, dpd_loss_min, _rate_fraction(prov_loss_pct), dpd_loss_min >= npl_dpd_threshold
        ),
    )
    return ClassificationGrid("sdi", bands, npl_dpd_threshold)


def classify(days_past_due: int, grid: ClassificationGrid) -> str:
    """The grade for a loan ``days_past_due`` days overdue (deterministic).

    Returns the highest-DPD band whose inclusive lower boundary the loan
    reaches. ``days_past_due`` must be non-negative — a negative DPD is a data
    error, never silently floored to standard.
    """
    if days_past_due < 0:
        raise LoanClassificationError(f"days_past_due must be non-negative, got {days_past_due}.")
    for band in reversed(grid.bands):
        if days_past_due >= band.min_dpd:
            return band.grade
    # Unreachable: the standard band anchors at 0 and DPD is non-negative.
    return grid.bands[0].grade


def _stage_proxy_grade(ifrs9_stage: int | None, grid: ClassificationGrid) -> str | None:
    """Coarse IFRS 9 → BoG grade proxy used only when DPD is not stated.

    IFRS 9 stage 3 is credit-impaired ≈ non-performing → the grid's entry NPL
    grade (substandard, the least-overstating NPL band). Stages 1–2 are
    performing → standard. A missing stage yields no proxy (None).
    """
    if ifrs9_stage is None:
        return None
    if ifrs9_stage >= _STAGE_IMPAIRED:
        return grid.entry_npl_grade
    return STANDARD


@dataclass(frozen=True)
class LoanExposure:
    """One loan's classification inputs: exposure + delinquency signals."""

    exposure_ghs: Decimal
    days_past_due: int | None
    ifrs9_stage: int | None = None


@dataclass(frozen=True)
class ClassifiedLoan:
    """One classified loan (grade + provision + how it was classified)."""

    grade: str
    exposure_ghs: Decimal
    provision_required_ghs: Decimal
    non_performing: bool
    classification_basis: str


@dataclass(frozen=True)
class GradeBucket:
    """Per-grade roll-up over the loan book."""

    grade: str
    count: int
    exposure_ghs: Decimal
    provision_required_ghs: Decimal
    non_performing: bool


@dataclass(frozen=True)
class ClassificationResult:
    """The classified loan book: per-grade buckets + book-level totals."""

    buckets: tuple[GradeBucket, ...]
    loans: tuple[ClassifiedLoan, ...]
    total_exposure_ghs: Decimal
    performing_exposure_ghs: Decimal
    npl_exposure_ghs: Decimal
    #: NPL / total exposure as a fraction (0 when total exposure is 0).
    npl_ratio: Decimal
    total_provision_required_ghs: Decimal
    unclassified_exposure_ghs: Decimal
    #: Loan counts by classification basis (audit of how the book was derived).
    stage_proxy_count: int = 0
    unclassified_count: int = 0

    def bucket_for(self, grade: str) -> GradeBucket:
        for bucket in self.buckets:
            if bucket.grade == grade:
                return bucket
        raise LoanClassificationError(f"No bucket for grade {grade!r}.")


def _classify_one(exposure: LoanExposure, grid: ClassificationGrid) -> ClassifiedLoan:
    if exposure.days_past_due is not None:
        grade = classify(exposure.days_past_due, grid)
        basis = BASIS_DAYS_PAST_DUE
    else:
        proxy = _stage_proxy_grade(exposure.ifrs9_stage, grid)
        if proxy is None:
            # Neither DPD nor stage: never booked as performing.
            return ClassifiedLoan(
                grade=UNCLASSIFIED,
                exposure_ghs=_money(exposure.exposure_ghs),
                provision_required_ghs=_ZERO,
                non_performing=False,
                classification_basis=BASIS_UNCLASSIFIED,
            )
        grade = proxy
        basis = BASIS_STAGE_PROXY
    band = grid.band_for_grade(grade)
    provision = _money(exposure.exposure_ghs * band.provision_rate)
    return ClassifiedLoan(
        grade=grade,
        exposure_ghs=_money(exposure.exposure_ghs),
        provision_required_ghs=provision,
        non_performing=band.non_performing,
        classification_basis=basis,
    )


def classify_book(
    exposures: Sequence[LoanExposure], grid: ClassificationGrid
) -> ClassificationResult:
    """Classify a loan book, roll up per grade, and compute the NPL ratio.

    Provisions are ``exposure × grade rate``. NPL exposure is the sum over the
    non-performing grades (substandard and worse); the ratio is NPL / total.
    Loans with neither a DPD nor a stage sit in the ``unclassified`` bucket —
    included in total exposure but in neither the performing nor the NPL leg.
    """
    loans = tuple(_classify_one(exposure, grid) for exposure in exposures)

    counts: dict[str, int] = {}
    exposures_by_grade: dict[str, Decimal] = {}
    provisions_by_grade: dict[str, Decimal] = {}
    for loan in loans:
        counts[loan.grade] = counts.get(loan.grade, 0) + 1
        exposures_by_grade[loan.grade] = (
            exposures_by_grade.get(loan.grade, _ZERO) + loan.exposure_ghs
        )
        provisions_by_grade[loan.grade] = (
            provisions_by_grade.get(loan.grade, _ZERO) + loan.provision_required_ghs
        )

    buckets: list[GradeBucket] = []
    for band in grid.bands:
        buckets.append(
            GradeBucket(
                grade=band.grade,
                count=counts.get(band.grade, 0),
                exposure_ghs=exposures_by_grade.get(band.grade, _ZERO),
                provision_required_ghs=provisions_by_grade.get(band.grade, _ZERO),
                non_performing=band.non_performing,
            )
        )
    if UNCLASSIFIED in counts:
        buckets.append(
            GradeBucket(
                grade=UNCLASSIFIED,
                count=counts[UNCLASSIFIED],
                exposure_ghs=exposures_by_grade.get(UNCLASSIFIED, _ZERO),
                provision_required_ghs=_ZERO,
                non_performing=False,
            )
        )

    total_exposure = sum((loan.exposure_ghs for loan in loans), _ZERO)
    npl_exposure = sum(
        (bucket.exposure_ghs for bucket in buckets if bucket.non_performing), _ZERO
    )
    unclassified_exposure = sum(
        (bucket.exposure_ghs for bucket in buckets if bucket.grade == UNCLASSIFIED), _ZERO
    )
    performing_exposure = total_exposure - npl_exposure - unclassified_exposure
    total_provision = sum((loan.provision_required_ghs for loan in loans), _ZERO)
    npl_ratio = (
        (npl_exposure / total_exposure).quantize(_RATIO_Q) if total_exposure > _ZERO else _ZERO
    )

    return ClassificationResult(
        buckets=tuple(buckets),
        loans=loans,
        total_exposure_ghs=_money(total_exposure),
        performing_exposure_ghs=_money(performing_exposure),
        npl_exposure_ghs=_money(npl_exposure),
        npl_ratio=npl_ratio,
        total_provision_required_ghs=_money(total_provision),
        unclassified_exposure_ghs=_money(unclassified_exposure),
        stage_proxy_count=sum(
            1 for loan in loans if loan.classification_basis == BASIS_STAGE_PROXY
        ),
        unclassified_count=counts.get(UNCLASSIFIED, 0),
    )


# The control-plane parameter codes each grid factory consumes (single source of
# truth for the service resolver and its tests — never a hardcoded rate/day).
BANK_PARAM_CODES: tuple[str, ...] = (
    "dpd_olem_min",
    "dpd_substandard_min",
    "dpd_doubtful_min",
    "dpd_loss_min",
    "npl_dpd_threshold",
    "prov_standard",
    "prov_olem",
    "prov_substandard",
    "prov_doubtful",
    "prov_loss",
)
SDI_PARAM_CODES: tuple[str, ...] = (
    "dpd_substandard_min",
    "dpd_doubtful_min",
    "dpd_loss_min",
    "npl_dpd_threshold",
    "prov_standard",
    "prov_substandard",
    "prov_doubtful",
    "prov_loss",
)


def grid_from_params(
    institution_class: str, params: Mapping[str, Decimal]
) -> ClassificationGrid:
    """Build the class grid from a code → value map of resolved parameters.

    ``institution_class`` selects the grid shape; ``params`` supplies every day
    boundary (as whole days) and provisioning rate (as a percentage). Rates keep
    their control-plane percent unit; day counts are coerced to ``int``.
    """

    def days(code: str) -> int:
        return int(params[code])

    if institution_class == "bank":
        return bank_grid(
            dpd_olem_min=days("dpd_olem_min"),
            dpd_substandard_min=days("dpd_substandard_min"),
            dpd_doubtful_min=days("dpd_doubtful_min"),
            dpd_loss_min=days("dpd_loss_min"),
            npl_dpd_threshold=days("npl_dpd_threshold"),
            prov_standard_pct=params["prov_standard"],
            prov_olem_pct=params["prov_olem"],
            prov_substandard_pct=params["prov_substandard"],
            prov_doubtful_pct=params["prov_doubtful"],
            prov_loss_pct=params["prov_loss"],
        )
    if institution_class == "sdi":
        return sdi_grid(
            dpd_substandard_min=days("dpd_substandard_min"),
            dpd_doubtful_min=days("dpd_doubtful_min"),
            dpd_loss_min=days("dpd_loss_min"),
            npl_dpd_threshold=days("npl_dpd_threshold"),
            prov_standard_pct=params["prov_standard"],
            prov_substandard_pct=params["prov_substandard"],
            prov_doubtful_pct=params["prov_doubtful"],
            prov_loss_pct=params["prov_loss"],
        )
    raise LoanClassificationError(
        f"Unknown institution_class {institution_class!r} (expected 'bank' or 'sdi')."
    )


def param_codes_for_class(institution_class: str) -> tuple[str, ...]:
    """The control-plane parameter codes required to build the class grid."""
    if institution_class == "bank":
        return BANK_PARAM_CODES
    if institution_class == "sdi":
        return SDI_PARAM_CODES
    raise LoanClassificationError(
        f"Unknown institution_class {institution_class!r} (expected 'bank' or 'sdi')."
    )
