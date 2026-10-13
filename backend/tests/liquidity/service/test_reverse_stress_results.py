from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.results import Computed, FigureResult
from app.domain.liquidity import engine
from app.domain.liquidity.engine import LcrResult, LiquidityFact, LiquidityParams
from app.models import Bank, BankFinancialFact, BankReportingPeriod
from app.services import regulatory_liquidity
from tests.liquidity.helpers import inputs


@pytest.fixture(autouse=True)
def forbid_nsfr_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_nsfr(_facts: object, _params: object) -> None:
        pytest.fail("The LCR-only search must not evaluate NSFR")

    monkeypatch.setattr(engine, "compute_nsfr", unexpected_nsfr)


def _params() -> LiquidityParams[Decimal | None]:
    return LiquidityParams(
        outflow_rates={"deposit": Decimal("10")},
        inflow_rates={},
        asf_weights={},
        rsf_weights={},
        inflow_cap_pct=Decimal("75"),
        lcr_min_pct=Decimal("100"),
        lcr_amber_floor_pct=Decimal("90"),
        nsfr_min_pct=None,
        nsfr_amber_floor_pct=None,
        hqla_haircut_pct={"L1": Decimal("0")},
        hqla_level2_cap_pct=None,
        hqla_level2b_cap_pct=None,
    )


def _probe(
    monkeypatch: pytest.MonkeyPatch,
    params: LiquidityParams[Decimal | None],
    *,
    haircut: Decimal = Decimal("20"),
    runoff: Decimal = Decimal("30"),
    unclassified: bool = False,
) -> dict[str, object]:
    facts, _ = inputs()
    rows = [
        BankFinancialFact(
            fact_group=fact.fact_group,
            category=fact.category,
            amount=fact.amount,
            hqla_level="unknown" if unclassified and index == 0 else fact.hqla_level,
            attributes={"side": fact.side},
        )
        for index, fact in enumerate(facts)
    ]

    def load_facts(*_args: object) -> list[BankFinancialFact]:
        return rows

    def load_params(*_args: object) -> LiquidityParams[Decimal | None]:
        return params

    def shocks(*_args: object) -> dict[str, Decimal]:
        return {"runoff:deposit": runoff, "hqla_securities_haircut_pct": haircut}

    monkeypatch.setattr(regulatory_liquidity, "_load_facts", load_facts)
    monkeypatch.setattr(regulatory_liquidity, "_load_active_params", load_params)
    monkeypatch.setattr(regulatory_liquidity, "_engine_params", load_params)
    monkeypatch.setattr(regulatory_liquidity, "_load_shocks", shocks)
    with Session() as db:
        return cast(
            dict[str, object],
            regulatory_liquidity.liquidity_breach_multiplier(
                db,
                TenantContext(organization_id="OR-1234ABCD"),
                Bank(id="BK-1234ABCD", organization_id="OR-1234ABCD"),
                BankReportingPeriod(period_end=date(2026, 9, 30)),
            ),
        )


@pytest.mark.parametrize("breached", [True, False])
def test_lcr_search_ignores_missing_nsfr_weights_and_thresholds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], breached: bool
) -> None:
    results: list[FigureResult[LcrResult]] = []

    def compute_lcr(
        facts: Sequence[LiquidityFact], params: LiquidityParams[Decimal | None]
    ) -> FigureResult[LcrResult]:
        result = engine.compute_lcr_result(facts, params)
        results.append(result)
        return result

    monkeypatch.setattr(regulatory_liquidity, "compute_lcr_result", compute_lcr)
    output = _probe(monkeypatch, _params(), haircut=Decimal("20") if breached else Decimal("0"))
    assert output["breached"] is breached
    assert Decimal(str(output["baseline_lcr_pct"])) == 1000
    assert len(results) == (10 if breached else 2)
    assert all(isinstance(result, Computed) for result in results)
    if breached:
        assert Decimal(str(output["breach_multiplier"])) == Decimal("2.27")
        assert Decimal(str(output["lcr_at_breach_pct"])) == Decimal("98.555957")
    else:
        assert Decimal(str(output["lcr_at_k_max_pct"])) == 100
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("threshold", ["inflow_cap_pct", "lcr_min_pct", "lcr_amber_floor_pct"])
def test_lcr_search_still_refuses_missing_lcr_thresholds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], threshold: str
) -> None:
    with pytest.raises(regulatory_liquidity.LiquidityRunError) as caught:
        _ = _probe(monkeypatch, replace(_params(), **{threshold: None}))
    assert caught.value.code == "missing_parameter"
    output = capsys.readouterr().err
    if threshold == "lcr_min_pct":
        assert output == ""
    else:
        payload = cast(dict[str, object], json.loads(output))
        assert payload["figure_id"] == "lcr_pct"
        assert payload["reason_code"] == "missing_parameter"
        assert payload["row_ref"] == []


def test_lcr_search_logs_internal_rows_for_unclassified_hqla(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(regulatory_liquidity.LiquidityRunError) as caught:
        _ = _probe(monkeypatch, _params(), unclassified=True)
    assert caught.value.code == "unclassified_hqla"
    payload = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert payload["figure_id"] == "lcr_pct"
    assert payload["row_ref"] == [1]
    assert "row_count" not in payload


def test_lcr_search_preserves_unavailable_denominator_at_maximum(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _probe(monkeypatch, _params(), haircut=Decimal("0"), runoff=Decimal("0"))
    assert output["breached"] is False
    assert output["lcr_at_k_max_pct"] is None
    assert Decimal(str(output["baseline_lcr_pct"])) == 1000
    payload = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert payload["figure_id"] == "lcr_pct"
    assert payload["reason_code"] == "non_positive_denominator"
    assert payload["row_ref"] == []


def test_lcr_search_propagates_bugs_without_logging_customer_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "Borrower Jane Private account 123456789012 balance 9876543.21 rate 13.75%"
    error = RuntimeError(secret)

    def broken(_facts: object, _params: object) -> None:
        raise error

    monkeypatch.setattr(engine, "compute_lcr", broken)
    with pytest.raises(RuntimeError) as caught:
        _ = _probe(monkeypatch, _params())
    assert caught.value is error
    output = capsys.readouterr().err
    payload = cast(dict[str, object], json.loads(output))
    assert payload["reason_code"] == "unexpected_error"
    assert payload["tenant_id"] == "OR-1234ABCD"
    for value in ("Jane Private", "123456789012", "9876543.21", "13.75"):
        assert value not in output
