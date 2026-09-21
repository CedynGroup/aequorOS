"""The Pillar 2 adapter for the FULL granularity adjustment (P5-DESIGN §4.5).

Two things are proved here that the pure module cannot prove on its own.

**Every governed row is refused by code when it is absent** — six codes, no
fallback, no default calibration hiding in the adapter (D-024).

**The full adjustment and the simplified charge are never mistakable for each
other** (D-016). The heuristic stamps its own sentence saying it is not a
granularity adjustment; this one stamps the machine variant and the matching
label, on the refusals as well as the figures. A test asserts each result
carries exactly one of the two strings, because a bank that switched method
silently would be reporting a different quantity under the same name.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.credit.granularity import (
    REASON_BELOW_MIN_EFFECTIVE_NAMES,
    SEGMENT_CORPORATE,
    WARNING_EXCEEDS_K_STAR,
    GaExposure,
    granularity_adjustment,
)
from app.domain.icaap.pillar2.concentration import HEURISTIC_LABEL
from app.domain.icaap.pillar2.granularity_method import (
    CALIBRATION_NOTE,
    FULL_LABEL,
    METHOD,
    METHOD_VARIANT,
    PARAM_ASSET_CORRELATION,
    PARAM_CONFIDENCE_Q,
    PARAM_DELTA,
    PARAM_LGD_VARIANCE_GAMMA,
    PARAM_MATURITY_ADJUSTMENT,
    PARAM_MIN_EFFECTIVE_NAMES,
    REASON_STRESSED_BOOK_NOT_ASSESSED,
    granularity_adjustment_method,
    inputs_digest,
    parse_params,
)
from app.domain.icaap.pillar2.types import MethodStatus, MissingParameter
from app.domain.icaap.units import Basis
from tests.domain.credit.ga_fixtures import (
    ASSET_CORRELATION_BODY,
    CONFIDENCE_Q,
    CORRELATIONS,
    DELTA,
    LGD_VARIANCE_GAMMA,
    MATURITY_ADJUSTMENT_BODY,
    MATURITY_ADJUSTMENT_ON_BODY,
    MATURITY_OFF,
    MATURITY_ON,
    MIN_EFFECTIVE_NAMES,
    gate_passing_book,
    params,
    sixty_name_book,
    three_name_book,
)

SEEDED = {
    "confidence_q": CONFIDENCE_Q,
    "delta": DELTA,
    "lgd_variance_gamma": LGD_VARIANCE_GAMMA,
    "min_effective_names": MIN_EFFECTIVE_NAMES,
    "asset_correlation": ASSET_CORRELATION_BODY,
    "maturity_adjustment": MATURITY_ADJUSTMENT_BODY,
}


# --- the governed rows ------------------------------------------------------


def test_the_seeded_console_bodies_parse_into_the_calibration_the_cases_use() -> None:
    parsed = parse_params(**SEEDED)
    assert parsed.q == Decimal(CONFIDENCE_Q)
    assert parsed.delta == Decimal(DELTA)
    assert parsed.gamma == Decimal(LGD_VARIANCE_GAMMA)
    assert parsed.min_effective_names == Decimal(MIN_EFFECTIVE_NAMES)
    assert parsed.corr == CORRELATIONS
    assert parsed.ma == MATURITY_OFF


def test_the_maturity_switch_comes_from_the_console_not_from_code() -> None:
    parsed = parse_params(**{**SEEDED, "maturity_adjustment": MATURITY_ADJUSTMENT_ON_BODY})
    assert parsed.ma == MATURITY_ON


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("confidence_q", PARAM_CONFIDENCE_Q),
        ("delta", PARAM_DELTA),
        ("lgd_variance_gamma", PARAM_LGD_VARIANCE_GAMMA),
        ("min_effective_names", PARAM_MIN_EFFECTIVE_NAMES),
        ("asset_correlation", PARAM_ASSET_CORRELATION),
        ("maturity_adjustment", PARAM_MATURITY_ADJUSTMENT),
    ],
)
def test_an_absent_governed_row_refuses_by_code(field: str, code: str) -> None:
    with pytest.raises(MissingParameter) as raised:
        parse_params(**{**SEEDED, field: None})
    assert raised.value.param_code == code


@pytest.mark.parametrize(
    "body",
    [
        {},
        {SEGMENT_CORPORATE: "0.12"},
        {SEGMENT_CORPORATE: {"r_min": "0.12", "r_max": "0.24"}},
        {SEGMENT_CORPORATE: {"r_min": "not-a-number", "r_max": "0.24", "k": "50"}},
    ],
)
def test_a_malformed_correlation_body_refuses_by_code(body: dict[str, object]) -> None:
    with pytest.raises(MissingParameter) as raised:
        parse_params(**{**SEEDED, "asset_correlation": body})
    assert raised.value.param_code == PARAM_ASSET_CORRELATION


def test_a_malformed_maturity_switch_refuses_by_code() -> None:
    body = {**MATURITY_ADJUSTMENT_BODY, "apply": "sometimes"}
    with pytest.raises(MissingParameter) as raised:
        parse_params(**{**SEEDED, "maturity_adjustment": body})
    assert raised.value.param_code == PARAM_MATURITY_ADJUSTMENT


# --- the computed result ----------------------------------------------------


def test_a_gate_passing_book_answers_in_the_method_protocol() -> None:
    result = granularity_adjustment_method(exposures=gate_passing_book(), params=params())

    assert result.method == METHOD
    assert result.status is MethodStatus.COMPUTED
    assert result.computed
    assert result.basis is Basis.ABSOLUTE
    assert result.baseline_amount == Decimal("32.7446")
    assert result.basis_value == result.baseline_amount
    assert result.baseline_derivation == "method"
    assert result.detail["ga"] == "0.0163723170"
    assert result.detail["k_star"] == "0.0732515932"
    assert result.detail["n_eff"] == "66.666667"
    assert result.detail["n_obligors"] == "120"
    assert result.detail["min_effective_names"] == MIN_EFFECTIVE_NAMES
    assert {use.code for use in result.parameters_used} == {
        PARAM_CONFIDENCE_Q,
        PARAM_DELTA,
        PARAM_LGD_VARIANCE_GAMMA,
        PARAM_MIN_EFFECTIVE_NAMES,
        PARAM_ASSET_CORRELATION,
        PARAM_MATURITY_ADJUSTMENT,
    }


def test_the_result_discloses_where_each_pd_and_lgd_came_from() -> None:
    book = gate_passing_book()
    proxied = [replace(exposure, pd_source="proxy_rw_code") for exposure in book[:20]]
    result = granularity_adjustment_method(
        exposures=[*proxied, *book[20:]],
        params=params(),
        excluded={"ifrs9_stage_3": 4, "risk_weight_zero": 2},
    )
    assert result.detail["pd_source:proxy_rw_code"] == "20"
    assert result.detail["pd_source:exposure"] == "100"
    assert result.detail["lgd_source:exposure"] == "120"
    assert result.detail["excluded:ifrs9_stage_3"] == "4"
    assert result.detail["excluded:risk_weight_zero"] == "2"


def test_the_sanity_warning_reaches_the_method_reasons() -> None:
    result = granularity_adjustment_method(
        exposures=three_name_book(), params=params(min_effective_names="0")
    )
    assert result.status is MethodStatus.COMPUTED
    assert WARNING_EXCEEDS_K_STAR in result.reasons


def test_the_stressed_side_is_declared_not_assessed_without_a_stressed_book() -> None:
    result = granularity_adjustment_method(exposures=gate_passing_book(), params=params())
    assert result.stressed_amount is None
    assert result.stressed_derivation == "not_assessed"
    assert REASON_STRESSED_BOOK_NOT_ASSESSED in result.reasons


def test_a_stressed_book_is_reported_as_a_method_figure() -> None:
    book = gate_passing_book()
    stressed_book = [replace(exposure, pd=exposure.pd * 2) for exposure in book]
    stressed = granularity_adjustment(stressed_book, params())
    result = granularity_adjustment_method(exposures=book, params=params(), stressed=stressed)
    assert result.stressed_derivation == "method"
    assert result.stressed_amount is not None
    assert result.stressed_amount > (result.baseline_amount or Decimal(0))
    assert REASON_STRESSED_BOOK_NOT_ASSESSED not in result.reasons


# --- the refusal ------------------------------------------------------------


def test_a_book_below_the_effective_name_floor_is_not_computable() -> None:
    result = granularity_adjustment_method(exposures=sixty_name_book(), params=params())

    assert result.status is MethodStatus.NOT_COMPUTABLE
    assert not result.computed
    assert result.reasons == (REASON_BELOW_MIN_EFFECTIVE_NAMES,)
    assert result.baseline_amount is None
    assert result.stressed_amount is None
    assert result.baseline_derivation == "not_applicable"
    # The refusal still explains itself and names what would change it.
    assert result.detail["n_eff"] == "33.333333"
    assert result.detail["min_effective_names"] == MIN_EFFECTIVE_NAMES
    assert result.detail["ga"] is None
    assert result.detail["label"] == FULL_LABEL


# --- which method ran -------------------------------------------------------


@pytest.mark.parametrize("book", [gate_passing_book(), sixty_name_book()])
def test_every_result_says_which_derivation_produced_it(book: list[GaExposure]) -> None:
    """Computed or refused, the full adjustment is always identified as such."""
    result = granularity_adjustment_method(exposures=book, params=params())
    assert result.detail["method_variant"] == METHOD_VARIANT
    assert result.detail["label"] == FULL_LABEL
    assert result.detail["calibration"] == CALIBRATION_NOTE
    # ... and never as the simplified charge.
    assert HEURISTIC_LABEL not in set(result.detail.values())
    assert FULL_LABEL != HEURISTIC_LABEL


def test_the_calibration_is_printed_with_every_figure() -> None:
    """A REPRESENTATIVE calibration must travel with the number it produced."""
    result = granularity_adjustment_method(exposures=gate_passing_book(), params=params())
    assert result.detail["confidence_q"] == CONFIDENCE_Q
    assert result.detail["delta"] == DELTA
    assert result.detail["lgd_variance_gamma"] == LGD_VARIANCE_GAMMA
    assert result.detail["maturity_adjustment_applied"] == "false"
    assert result.detail[f"correlation:{SEGMENT_CORPORATE}"] == "0.12:0.24:50"


# --- the digest -------------------------------------------------------------


def test_the_digest_is_value_based_and_order_invariant() -> None:
    book = gate_passing_book()
    shuffled = [*book[60:], *reversed(book[:60])]
    assert inputs_digest(book, params()) == inputs_digest(shuffled, params())


def test_the_digest_moves_when_the_book_or_the_calibration_moves() -> None:
    book = gate_passing_book()
    baseline = inputs_digest(book, params())
    repriced = [replace(book[0], pd=Decimal("0.05")), *book[1:]]
    assert inputs_digest(repriced, params()) != baseline
    assert inputs_digest(book, params(delta="4.9")) != baseline
    assert inputs_digest(book, params(min_effective_names="30")) != baseline
    assert inputs_digest(book, params(ma=MATURITY_ON)) != baseline


def test_the_digest_travels_with_the_result() -> None:
    book = gate_passing_book()
    result = granularity_adjustment_method(exposures=book, params=params())
    assert result.detail["inputs_digest"] == inputs_digest(book, params())
