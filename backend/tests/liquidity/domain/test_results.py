from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.authority.results import Computed, Refused
from app.domain.liquidity import engine
from app.domain.liquidity.engine import compute_liquidity
from tests.liquidity.helpers import inputs


def test_computed_figures_keep_the_existing_golden_values() -> None:
    facts, params = inputs()
    figures = compute_liquidity(facts, params)
    assert isinstance(figures.lcr, Computed)
    assert isinstance(figures.nsfr, Computed)
    assert figures.lcr.value.lcr_pct == Decimal("1000.000000")
    assert figures.nsfr.value.nsfr_pct == Decimal("190.000000")
    assert figures.lcr.value == engine.compute_lcr(facts, params)
    assert figures.nsfr.value == engine.compute_nsfr(facts, params)


def test_missing_runoff_refuses_lcr_without_suppressing_nsfr() -> None:
    facts, params = inputs()
    figures = compute_liquidity(facts, replace(params, outflow_rates={}))
    assert isinstance(figures.lcr, Refused)
    assert figures.lcr.reason_code == "missing_parameter"
    assert figures.lcr.rule_citation == "BCBS 238"
    assert figures.lcr.row_ref == (2,)
    assert figures.lcr.detail is not None
    assert figures.lcr.detail.blocks_filing
    assert figures.lcr.detail.items == ("param:deposit",)
    assert isinstance(figures.nsfr, Computed)
    assert figures.nsfr.value.nsfr_pct == Decimal("190.000000")


def test_missing_rsf_refuses_nsfr_without_suppressing_lcr() -> None:
    facts, params = inputs()
    figures = compute_liquidity(facts, replace(params, rsf_weights={}))
    assert isinstance(figures.nsfr, Refused)
    assert figures.nsfr.reason_code == "missing_parameter"
    assert figures.nsfr.rule_citation == "BCBS 295"
    assert figures.nsfr.row_ref == (3,)
    assert isinstance(figures.lcr, Computed)
    assert figures.lcr.value.lcr_pct == Decimal("1000.000000")


def test_unclassified_security_refuses_only_lcr_with_affected_row() -> None:
    facts, params = inputs()
    facts = (replace(facts[0], hqla_level="unknown"), *facts[1:])
    figures = compute_liquidity(facts, params)
    assert isinstance(figures.lcr, Refused)
    assert figures.lcr.reason_code == "unclassified_hqla"
    assert figures.lcr.row_ref == (1,)
    assert isinstance(figures.nsfr, Computed)


def test_missing_haircut_locates_all_affected_rows() -> None:
    facts, params = inputs()
    figures = compute_liquidity((*facts, facts[0]), replace(params, hqla_haircut_pct={}))
    assert isinstance(figures.lcr, Refused)
    assert figures.lcr.row_ref == (1, 4)


def test_undefined_ratio_is_a_refusal_without_an_invented_zero() -> None:
    facts, params = inputs()
    figures = compute_liquidity(facts, replace(params, outflow_rates={"deposit": Decimal("0")}))
    assert isinstance(figures.lcr, Refused)
    assert figures.lcr.reason_code == "non_positive_denominator"
    assert isinstance(figures.nsfr, Computed)


def test_both_figures_can_be_refused_independently() -> None:
    facts, params = inputs()
    figures = compute_liquidity(facts, replace(params, outflow_rates={}, rsf_weights={}))
    assert isinstance(figures.lcr, Refused)
    assert isinstance(figures.nsfr, Refused)
    assert figures.lcr.row_ref == (2,)
    assert figures.nsfr.row_ref == (3,)


def test_unexpected_bugs_still_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_facts: object, _params: object) -> None:
        raise RuntimeError("unexpected engine bug")

    monkeypatch.setattr(engine, "compute_lcr", broken)
    facts, params = inputs()
    with pytest.raises(RuntimeError, match="unexpected engine bug"):
        _ = compute_liquidity(facts, params)
