"""Synthetic #582 journeys: calendar evidence from derivation through saved FX runs.

This module's other-suite cases live in ``test_fx_var_calendar_integration.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.fx import service as regulatory_fx
from app.fx.domain.engine import FxComputationError, FxPosition, compute_var
from app.live.service.fact_derivation import (
    _Canonical,  # pyright: ignore[reportPrivateUsage]
    _derive_fx_returns,  # pyright: ignore[reportPrivateUsage]
)
from app.models import BankFinancialFact


@pytest.mark.parametrize("source", ["reference", "market"])
def test_reported_spot_reproduction_preserves_interval_evidence(source: str) -> None:
    canonical = _Canonical(
        as_of=date(2026, 1, 3), base_currency="GHS", positions=[], gl_accounts=[], refs={}
    )
    observations = {
        "USD": [(1, "100"), (2, "90"), (3, "99")],
        "EUR": [(2, "100"), (3, "110")],
    }
    for currency, values in observations.items():
        if source == "market":
            canonical.market_fx_history[currency] = [
                (date(2026, 1, day), Decimal(rate)) for day, rate in values
            ]
        else:
            canonical.refs.setdefault("fx_rates_historical", []).extend(
                {"currency": currency, "date": date(2026, 1, day).isoformat(), "spot_rate": rate}
                for day, rate in values
            )
    specs = _derive_fx_returns(canonical, set(observations), [])
    facts = [BankFinancialFact(fact_group=s.fact_group, attributes=s.attributes) for s in specs]
    histories = regulatory_fx._returns_from_facts(facts)  # pyright: ignore[reportPrivateUsage]
    assert histories["USD"][0].start_date == date(2026, 1, 1)
    assert histories["USD"][0].value == Decimal("-0.1")
    assert histories["EUR"][0].end_date == date(2026, 1, 3)
    positions = [
        FxPosition(
            currency,
            Decimal(net),
            Decimal("1"),
            Decimal(net),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        )
        for currency, net in [("USD", "100"), ("EUR", "-100")]
    ]
    result = compute_var(positions, histories, Decimal("99"))
    assert result.portfolio_var == Decimal("0.0000")
    assert result.observations == 1


def test_undated_legacy_facts_require_rederivation() -> None:
    fact = BankFinancialFact(
        fact_group="fx_return_history", attributes={"currency": "USD", "returns": [-0.1]}
    )
    with pytest.raises(FxComputationError, match="re-derive"):
        regulatory_fx._returns_from_facts([fact])  # pyright: ignore[reportPrivateUsage]
