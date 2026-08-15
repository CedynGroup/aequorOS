"""Hand-verified tests for the SR 11-7 validation-metrics layer (§9).

Every expected number is derived independently here (closed-form or a direct
recomputation with the documented formula), never read back from the module.
"""

from __future__ import annotations

import random
from decimal import Decimal
from math import log, sqrt

import pytest

from app.domain.rating.validation import (
    GradedExposure,
    ValidationError,
    accuracy_ratio,
    binomial_test,
    hosmer_lemeshow,
    population_stability_index,
    roc_auc,
    traffic_light,
    validate,
)

D = Decimal


# --------------------------------------------------------------------------- #
# 9.1 Discrimination                                                          #
# --------------------------------------------------------------------------- #


def test_roc_auc_perfect_separation_is_one() -> None:
    # Every defaulter outranks every non-defaulter.
    scores = [D("0.1"), D("0.2"), D("0.8"), D("0.9")]
    outcomes = [0, 0, 1, 1]
    assert roc_auc(scores, outcomes) == D("1")


def test_roc_auc_perfect_reversal_is_zero() -> None:
    # Defaulters carry the lowest scores.
    scores = [D("0.1"), D("0.2"), D("0.8"), D("0.9")]
    outcomes = [1, 1, 0, 0]
    assert roc_auc(scores, outcomes) == D("0")


def test_roc_auc_symmetric_is_one_half() -> None:
    # defaulters at ranks 1 and 4, non-defaulters at 2 and 3 -> AUC exactly 0.5.
    scores = [D("1"), D("2"), D("3"), D("4")]
    outcomes = [1, 0, 0, 1]
    assert roc_auc(scores, outcomes) == D("0.5")


def test_roc_auc_all_ties_is_one_half() -> None:
    scores = [D("0.5"), D("0.5"), D("0.5"), D("0.5")]
    outcomes = [1, 1, 0, 0]
    assert roc_auc(scores, outcomes) == D("0.5")


def test_roc_auc_random_labels_near_one_half() -> None:
    rng = random.Random(20260814)
    scores = [D(str(rng.random())) for _ in range(2000)]
    outcomes = [rng.randint(0, 1) for _ in range(2000)]  # independent of scores
    auc = roc_auc(scores, outcomes)
    assert abs(auc - D("0.5")) < D("0.05")


def test_roc_auc_degenerate_all_same_outcome_raises() -> None:
    with pytest.raises(ValidationError):
        roc_auc([D("0.1"), D("0.2"), D("0.3")], [0, 0, 0])
    with pytest.raises(ValidationError):
        roc_auc([D("0.1"), D("0.2"), D("0.3")], [1, 1, 1])


def test_roc_auc_length_mismatch_raises() -> None:
    with pytest.raises(ValidationError):
        roc_auc([D("0.1"), D("0.2")], [1])


def test_accuracy_ratio_relation() -> None:
    # AR = 2*AUC - 1, exact at the anchor points.
    assert accuracy_ratio(D("1")) == D("1")
    assert accuracy_ratio(D("0.5")) == D("0")
    assert accuracy_ratio(D("0")) == D("-1")
    assert accuracy_ratio(D("0.75")) == D("0.5")
    assert accuracy_ratio(D("0.9")) == D("0.8")


def test_accuracy_ratio_composed_with_auc() -> None:
    perfect = roc_auc([D("0.1"), D("0.9")], [0, 1])
    assert accuracy_ratio(perfect) == D("1")
    reversed_auc = roc_auc([D("0.1"), D("0.9")], [1, 0])
    assert accuracy_ratio(reversed_auc) == D("-1")


def test_accuracy_ratio_out_of_range_raises() -> None:
    with pytest.raises(ValidationError):
        accuracy_ratio(D("1.5"))
    with pytest.raises(ValidationError):
        accuracy_ratio(D("-0.1"))


# --------------------------------------------------------------------------- #
# 9.2 Calibration                                                             #
# --------------------------------------------------------------------------- #


def test_binomial_z_positive_when_defaults_exceed_prediction() -> None:
    # predicted 2%, observed 5% -> understated PD -> z > 0.
    result = binomial_test(D("0.02"), 1000, 50)
    expected_z = (50 - 1000 * 0.02) / sqrt(1000 * 0.02 * (1 - 0.02))
    assert result.z_score > 0
    assert result.z_score == D(str(expected_z)).quantize(D("0.000001"))
    assert result.observed_rate == D("0.05")
    assert result.expected_defaults == D("20")
    assert result.exact_recommended is False  # expected defaults 20 >= 5
    assert result.p_value < D("0.01")


def test_binomial_z_negative_when_conservative() -> None:
    # predicted 10%, observed 2.5% -> overstated PD -> z < 0.
    result = binomial_test(D("0.10"), 200, 5)
    expected_z = (5 - 200 * 0.10) / sqrt(200 * 0.10 * (1 - 0.10))
    assert result.z_score < 0
    assert result.z_score == D(str(expected_z)).quantize(D("0.000001"))


def test_binomial_small_sample_flag() -> None:
    result = binomial_test(D("0.01"), 100, 1)  # expected defaults = 1 < 5
    assert result.exact_recommended is True
    assert "exact binomial" in result.note


def test_binomial_invalid_inputs_raise() -> None:
    with pytest.raises(ValidationError):
        binomial_test(D("0.02"), 0, 0)
    with pytest.raises(ValidationError):
        binomial_test(D("0.02"), 100, 200)
    with pytest.raises(ValidationError):
        binomial_test(D("0"), 100, 1)
    with pytest.raises(ValidationError):
        binomial_test(D("1"), 100, 1)


def test_hosmer_lemeshow_df_and_value() -> None:
    groups = [
        (D("0.01"), 1000, 8),
        (D("0.02"), 1000, 25),
        (D("0.05"), 500, 30),
        (D("0.10"), 200, 25),
    ]
    result = hosmer_lemeshow(groups)
    expected_chi = sum(
        (float(d) - float(g) * float(p)) ** 2 / (float(g) * float(p) * (1 - float(p)))
        for p, g, d in groups
    )
    assert result.degrees_of_freedom == len(groups) - 2  # 2
    assert result.groups == 4
    assert result.chi_square == D(str(expected_chi)).quantize(D("0.000001"))


def test_hosmer_lemeshow_needs_three_groups() -> None:
    with pytest.raises(ValidationError):
        hosmer_lemeshow([(D("0.01"), 100, 1), (D("0.05"), 100, 5)])


def test_traffic_light_bands() -> None:
    # ratio = observed / predicted; one-sided escalation.
    assert traffic_light(D("0.02"), D("0.02")) == "green"  # ratio 1.0
    assert traffic_light(D("0.01"), D("0.02")) == "green"  # conservative, ratio 0.5
    assert traffic_light(D("0.030"), D("0.02")) == "green"  # ratio 1.5 boundary
    assert traffic_light(D("0.036"), D("0.02")) == "amber"  # ratio 1.8
    assert traffic_light(D("0.040"), D("0.02")) == "amber"  # ratio 2.0 boundary
    assert traffic_light(D("0.050"), D("0.02")) == "red"  # ratio 2.5


def test_traffic_light_invalid_raises() -> None:
    with pytest.raises(ValidationError):
        traffic_light(D("0.02"), D("0"))
    with pytest.raises(ValidationError):
        traffic_light(D("1.5"), D("0.02"))


# --------------------------------------------------------------------------- #
# 9.3 Stability                                                               #
# --------------------------------------------------------------------------- #


def test_psi_identical_distribution_is_zero() -> None:
    props = [D("0.25"), D("0.25"), D("0.25"), D("0.25")]
    result = population_stability_index(props, list(props))
    assert result.psi == D("0")
    assert result.interpretation == "stable"


def test_psi_small_shift_matches_hand_value_and_band() -> None:
    expected = [D("0.5"), D("0.5")]
    actual = [D("0.6"), D("0.4")]
    hand = (0.6 - 0.5) * log(0.6 / 0.5) + (0.4 - 0.5) * log(0.4 / 0.5)
    result = population_stability_index(expected, actual)
    assert result.psi == D(str(hand)).quantize(D("0.000001"))  # 0.040547
    assert result.interpretation == "stable"  # < 0.10


def test_psi_moderate_shift_lands_in_minor_band() -> None:
    expected = [D("0.7"), D("0.3")]
    actual = [D("0.5"), D("0.5")]
    hand = (0.5 - 0.7) * log(0.5 / 0.7) + (0.5 - 0.3) * log(0.5 / 0.3)
    result = population_stability_index(expected, actual)
    assert result.psi == D(str(hand)).quantize(D("0.000001"))  # 0.169460
    assert result.interpretation == "minor_shift"  # 0.10 - 0.25


def test_psi_invalid_inputs_raise() -> None:
    with pytest.raises(ValidationError):  # zero bucket -> undefined log
        population_stability_index([D("0.5"), D("0.5")], [D("1.0"), D("0")])
    with pytest.raises(ValidationError):  # does not sum to 1
        population_stability_index([D("0.5"), D("0.4")], [D("0.5"), D("0.5")])
    with pytest.raises(ValidationError):  # length mismatch
        population_stability_index([D("0.5"), D("0.5")], [D("1.0")])


# --------------------------------------------------------------------------- #
# validate entrypoint                                                         #
# --------------------------------------------------------------------------- #


def _portfolio(
    spec: list[tuple[str, str, int, int]],
) -> list[GradedExposure]:
    """Expand (grade, pd, obligors, defaults) rows into obligor-level exposures."""
    exposures: list[GradedExposure] = []
    for grade, pd, obligors, defaults in spec:
        for index in range(obligors):
            exposures.append(GradedExposure(grade, D(pd), defaulted=index < defaults))
    return exposures


def test_validate_full_report_when_sufficient() -> None:
    # 150 obligors, 10 defaults, 4 grades with defaults rising with PD.
    exposures = _portfolio(
        [
            ("aa", "0.005", 40, 0),
            ("bbb", "0.02", 40, 1),
            ("bb", "0.05", 40, 3),
            ("b", "0.12", 30, 6),
        ]
    )
    report = validate(
        exposures,
        expected_distribution=[D("0.25"), D("0.25"), D("0.25"), D("0.25")],
        actual_distribution=[D("0.20"), D("0.30"), D("0.25"), D("0.25")],
    )
    assert report.verdict == "ok"
    assert report.sufficiency.sufficient is True
    assert report.sufficiency.total_obligors == 150
    assert report.sufficiency.total_defaults == 10

    assert report.discrimination is not None
    assert report.discrimination.defaulters == 10
    assert report.discrimination.non_defaulters == 140
    # Defaults concentrated in the riskier grades -> better than a coin flip.
    assert report.discrimination.auc > D("0.5")
    assert report.discrimination.accuracy_ratio == D(2) * report.discrimination.auc - D(1)

    assert report.calibration is not None
    assert len(report.calibration.grades) == 4
    assert report.calibration.hosmer_lemeshow is not None
    assert report.calibration.hosmer_lemeshow.degrees_of_freedom == 2
    # grades ordered by predicted PD
    assert [g.grade for g in report.calibration.grades] == ["aa", "bbb", "bb", "b"]

    assert report.stability is not None
    assert report.stability.interpretation in {"stable", "minor_shift", "significant_shift"}


def test_validate_insufficient_data_verdict() -> None:
    # 40 obligors, 3 defaults -> below the default 100 obs / 5 defaults floor.
    exposures = _portfolio([("bb", "0.05", 40, 3)])
    report = validate(exposures)
    assert report.verdict == "insufficient_data"
    assert report.sufficiency.sufficient is False
    assert report.sufficiency.total_defaults == 3
    assert report.discrimination is None
    assert report.calibration is None
    assert report.sufficiency.reason
    assert "low-default" in report.sufficiency.reason


def test_validate_insufficient_still_reports_stability() -> None:
    exposures = _portfolio([("bb", "0.05", 40, 3)])
    report = validate(
        exposures,
        expected_distribution=[D("0.5"), D("0.5")],
        actual_distribution=[D("0.6"), D("0.4")],
    )
    assert report.verdict == "insufficient_data"
    assert report.stability is not None  # PSI does not depend on outcomes


def test_validate_custom_thresholds_flip_verdict() -> None:
    exposures = _portfolio(
        [
            ("bbb", "0.02", 20, 1),
            ("bb", "0.05", 20, 2),
            ("b", "0.12", 20, 3),
        ]
    )
    # Below defaults -> insufficient under the defaults.
    assert validate(exposures).verdict == "insufficient_data"
    # Relaxed thresholds make the same portfolio sufficient.
    report = validate(exposures, min_observations=50, min_defaults=5)
    assert report.verdict == "ok"
    assert report.discrimination is not None
    assert report.calibration is not None


def test_validate_rejects_bad_inputs() -> None:
    with pytest.raises(ValidationError):
        validate([])
    with pytest.raises(ValidationError):
        validate([GradedExposure("bb", D("0"), defaulted=False)])
    with pytest.raises(ValidationError):
        validate([GradedExposure("bb", D("1"), defaulted=True)])
