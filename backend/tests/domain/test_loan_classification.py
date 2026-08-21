"""Pure loan-classification engine (docs/sdi.md §2.2, §4, §7 Phase G).

Class-aware DPD classification + provisioning with NO DB in sight: the grids are
built from hand-supplied regulatory values (the service test proves those values
come from the control plane). Pins:

- the bank 5-grade grid (standard/OLEM/substandard/doubtful/loss) vs the SDI
  4-grade grid (no OLEM), each with its own DPD boundaries and provisioning
  rates (bank 1/10/25/50/100, SDI 0/20/50/100);
- deterministic classification at the exact boundaries (90 / 180 / 360 days);
- the NPL cutoff at 90 days (substandard and worse are non-performing, so OLEM
  stays performing on the bank grid);
- book roll-up: per-grade buckets, NPL exposure, NPL ratio, total provision;
- the IFRS 9 stage proxy when DPD is not stated, and the ``unclassified`` bucket
  when neither a DPD nor a stage is known (never silently performing).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.capital import loan_classification as lc

# Seeded regulatory values (regulatory_parameters.SEED_PARAMETERS) reproduced as
# plain test inputs — the pure engine never reaches for the control plane.
SDI = lc.sdi_grid(
    dpd_substandard_min=90,
    dpd_doubtful_min=180,
    dpd_loss_min=360,
    npl_dpd_threshold=90,
    prov_standard_pct=Decimal("0"),
    prov_substandard_pct=Decimal("20"),
    prov_doubtful_pct=Decimal("50"),
    prov_loss_pct=Decimal("100"),
)
BANK = lc.bank_grid(
    dpd_olem_min=30,
    dpd_substandard_min=90,
    dpd_doubtful_min=180,
    dpd_loss_min=360,
    npl_dpd_threshold=90,
    prov_standard_pct=Decimal("1"),
    prov_olem_pct=Decimal("10"),
    prov_substandard_pct=Decimal("25"),
    prov_doubtful_pct=Decimal("50"),
    prov_loss_pct=Decimal("100"),
)


# ---------------------------------------------------------------------------
# grid shape + rates
# ---------------------------------------------------------------------------


def test_grid_shapes_differ_by_class() -> None:
    assert SDI.institution_class == "sdi"
    assert SDI.grades == lc.SDI_GRADE_ORDER == ("standard", "substandard", "doubtful", "loss")
    assert BANK.institution_class == "bank"
    assert BANK.grades == lc.BANK_GRADE_ORDER == (
        "standard",
        "olem",
        "substandard",
        "doubtful",
        "loss",
    )


def test_sdi_provision_rates_are_0_20_50_100() -> None:
    rates = {band.grade: band.provision_rate for band in SDI.bands}
    assert rates == {
        "standard": Decimal("0"),
        "substandard": Decimal("0.2"),
        "doubtful": Decimal("0.5"),
        "loss": Decimal("1"),
    }


def test_bank_provision_rates_are_1_10_25_50_100() -> None:
    rates = {band.grade: band.provision_rate for band in BANK.bands}
    assert rates == {
        "standard": Decimal("0.01"),
        "olem": Decimal("0.1"),
        "substandard": Decimal("0.25"),
        "doubtful": Decimal("0.5"),
        "loss": Decimal("1"),
    }


# ---------------------------------------------------------------------------
# classification boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dpd", "grade"),
    [
        (0, "standard"),
        (89, "standard"),
        (90, "substandard"),  # boundary: substandard opens at exactly 90
        (179, "substandard"),
        (180, "doubtful"),  # boundary: doubtful opens at exactly 180
        (359, "doubtful"),
        (360, "loss"),  # boundary: loss opens at exactly 360
        (5000, "loss"),
    ],
)
def test_sdi_classification_boundaries(dpd: int, grade: str) -> None:
    assert lc.classify(dpd, SDI) == grade


@pytest.mark.parametrize(
    ("dpd", "grade"),
    [
        (0, "standard"),
        (29, "standard"),
        (30, "olem"),  # boundary: OLEM opens at exactly 30 (bank only)
        (89, "olem"),
        (90, "substandard"),
        (179, "substandard"),
        (180, "doubtful"),
        (359, "doubtful"),
        (360, "loss"),
    ],
)
def test_bank_classification_boundaries(dpd: int, grade: str) -> None:
    assert lc.classify(dpd, BANK) == grade


def test_npl_cutoff_is_90_days_olem_stays_performing() -> None:
    # SDI: everything from substandard down is non-performing.
    sdi_npl = {band.grade: band.non_performing for band in SDI.bands}
    assert sdi_npl == {
        "standard": False,
        "substandard": True,
        "doubtful": True,
        "loss": True,
    }
    # Bank: OLEM (30d) is below the 90d NPL threshold, so it is performing.
    bank_npl = {band.grade: band.non_performing for band in BANK.bands}
    assert bank_npl == {
        "standard": False,
        "olem": False,
        "substandard": True,
        "doubtful": True,
        "loss": True,
    }
    assert SDI.entry_npl_grade == "substandard"
    assert BANK.entry_npl_grade == "substandard"


def test_negative_dpd_is_rejected_not_floored_to_standard() -> None:
    with pytest.raises(lc.LoanClassificationError):
        lc.classify(-1, SDI)


# ---------------------------------------------------------------------------
# book roll-up
# ---------------------------------------------------------------------------


def _exp(exposure: str, dpd: int | None, stage: int | None = None) -> lc.LoanExposure:
    return lc.LoanExposure(Decimal(exposure), dpd, stage)


def test_sdi_book_rollup_npl_ratio_and_provisions() -> None:
    book = [
        _exp("1000000", 0),  # standard   — prov 0
        _exp("500000", 90),  # substandard — 20% -> 100000, NPL
        _exp("200000", 200),  # doubtful   — 50% -> 100000, NPL
        _exp("100000", 400),  # loss       — 100% -> 100000, NPL
    ]
    result = lc.classify_book(book, SDI)

    assert result.total_exposure_ghs == Decimal("1800000.0000")
    assert result.npl_exposure_ghs == Decimal("800000.0000")
    assert result.performing_exposure_ghs == Decimal("1000000.0000")
    # 800,000 / 1,800,000 = 0.444444 (6dp)
    assert result.npl_ratio == Decimal("0.444444")
    assert result.total_provision_required_ghs == Decimal("300000.0000")

    substandard = result.bucket_for("substandard")
    assert substandard.count == 1
    assert substandard.exposure_ghs == Decimal("500000.0000")
    assert substandard.provision_required_ghs == Decimal("100000.0000")
    assert substandard.non_performing is True
    assert result.bucket_for("standard").provision_required_ghs == Decimal("0.0000")


def test_bank_book_rollup_uses_five_grade_rates() -> None:
    book = [
        _exp("1000000", 10),  # standard 1%  -> 10000
        _exp("400000", 45),  # olem 10%     -> 40000 (performing)
        _exp("300000", 100),  # substandard 25% -> 75000 (NPL)
        _exp("200000", 250),  # doubtful 50% -> 100000 (NPL)
        _exp("100000", 500),  # loss 100%    -> 100000 (NPL)
    ]
    result = lc.classify_book(book, BANK)

    assert result.total_exposure_ghs == Decimal("2000000.0000")
    assert result.npl_exposure_ghs == Decimal("600000.0000")  # sub+doubtful+loss
    assert result.performing_exposure_ghs == Decimal("1400000.0000")  # standard+olem
    assert result.npl_ratio == Decimal("0.300000")
    assert result.total_provision_required_ghs == Decimal("325000.0000")
    olem = result.bucket_for("olem")
    assert olem.count == 1
    assert olem.provision_required_ghs == Decimal("40000.0000")
    assert olem.non_performing is False


def test_empty_book_has_zero_ratio_not_division_error() -> None:
    result = lc.classify_book([], SDI)
    assert result.total_exposure_ghs == Decimal("0.0000")
    assert result.npl_ratio == Decimal("0")
    assert all(bucket.count == 0 for bucket in result.buckets)


# ---------------------------------------------------------------------------
# stage proxy + unclassified fallback
# ---------------------------------------------------------------------------


def test_stage3_without_dpd_falls_back_to_entry_npl_grade() -> None:
    result = lc.classify_book([_exp("100000", None, 3)], SDI)
    loan = result.loans[0]
    assert loan.grade == "substandard"
    assert loan.non_performing is True
    assert loan.classification_basis == lc.BASIS_STAGE_PROXY
    assert loan.provision_required_ghs == Decimal("20000.0000")  # 20% of 100k
    assert result.npl_exposure_ghs == Decimal("100000.0000")
    assert result.stage_proxy_count == 1


def test_stage1_and_stage2_without_dpd_are_performing_standard() -> None:
    result = lc.classify_book([_exp("100000", None, 1), _exp("50000", None, 2)], BANK)
    assert {loan.grade for loan in result.loans} == {"standard"}
    assert all(loan.classification_basis == lc.BASIS_STAGE_PROXY for loan in result.loans)
    assert result.npl_exposure_ghs == Decimal("0.0000")
    assert result.performing_exposure_ghs == Decimal("150000.0000")


def test_dpd_takes_precedence_over_stage_when_both_present() -> None:
    # A stage-1 loan that is 200 days overdue is classified doubtful on the DPD,
    # not standard on the stage — the delinquency backstop wins.
    result = lc.classify_book([_exp("100000", 200, 1)], SDI)
    loan = result.loans[0]
    assert loan.grade == "doubtful"
    assert loan.classification_basis == lc.BASIS_DAYS_PAST_DUE


def test_missing_dpd_and_missing_stage_is_unclassified_never_performing() -> None:
    result = lc.classify_book([_exp("100000", None, None)], SDI)
    loan = result.loans[0]
    assert loan.grade == lc.UNCLASSIFIED
    assert loan.classification_basis == lc.BASIS_UNCLASSIFIED
    assert loan.non_performing is False
    # Present in total exposure but in neither the performing nor the NPL leg.
    assert result.total_exposure_ghs == Decimal("100000.0000")
    assert result.unclassified_exposure_ghs == Decimal("100000.0000")
    assert result.performing_exposure_ghs == Decimal("0.0000")
    assert result.npl_exposure_ghs == Decimal("0.0000")
    assert result.unclassified_count == 1
    assert result.bucket_for(lc.UNCLASSIFIED).count == 1


# ---------------------------------------------------------------------------
# grid_from_params dispatch
# ---------------------------------------------------------------------------


def test_grid_from_params_dispatches_by_class() -> None:
    sdi_params = {
        "dpd_substandard_min": Decimal("90"),
        "dpd_doubtful_min": Decimal("180"),
        "dpd_loss_min": Decimal("360"),
        "npl_dpd_threshold": Decimal("90"),
        "prov_standard": Decimal("0"),
        "prov_substandard": Decimal("20"),
        "prov_doubtful": Decimal("50"),
        "prov_loss": Decimal("100"),
    }
    grid = lc.grid_from_params("sdi", sdi_params)
    assert grid.grades == lc.SDI_GRADE_ORDER
    assert grid.band_for_grade("substandard").provision_rate == Decimal("0.2")


def test_grid_from_params_rejects_unknown_class() -> None:
    with pytest.raises(lc.LoanClassificationError):
        lc.grid_from_params("corporate", {})
