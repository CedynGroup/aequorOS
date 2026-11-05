"""Notice BG/FMD/2026/07 ¶1(a)–(b): every currency enters the NOP basis.

This module's other-suite cases live in ``test_nop_currency_coverage_integration.py``.
"""

__all__ = ["_canonical", "_derive_fx_positions", "_row"]
from decimal import Decimal

import pytest

from app.domain.authority.outcomes import NotComputable, OutcomeState
from app.fx import service as regulatory_fx
from app.fx.domain.engine import compute_nop
from app.live.service.fact_derivation import (
    GroupResult,
    _derive_fx_positions,  # pyright: ignore[reportPrivateUsage]
)
from app.models import (
    BankFinancialFact,
)
from tests.services.test_derivation_fail_closed_defaults import (
    _canonical,  # pyright: ignore[reportPrivateUsage]
    _row,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.requirement("Notice BG/FMD/2026/07 ¶1(a)–(b)")


@pytest.mark.parametrize("currency", ["USD", "CHF", "JPY"])
@pytest.mark.parametrize("quoted", [False, True])
def test_currency_without_history_is_written_and_measured_or_refused(
    currency: str, quoted: bool
) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b), "NET OPEN POSITION COMPUTATION": no omission."""
    canonical = _canonical(
        _row("FX/LOAN", "LOAN", currency=currency, balance="1000", balance_ghs="12850")
    )
    if quoted:
        canonical.refs["fx_rates_current"] = [{"currency": currency, "spot_rate": "12.85"}]
    groups: list[GroupResult] = []
    specs, included = _derive_fx_positions(canonical, groups)
    assert included == {currency}
    position_specs = [spec for spec in specs if spec.fact_group == "fx_position"]
    assert len(position_specs) == 1
    assert position_specs[0].amount == Decimal("12850")
    facts = [
        BankFinancialFact(
            fact_group=spec.fact_group,
            category=spec.category,
            amount=spec.amount,
            attributes=spec.attributes,
        )
        for spec in position_specs
    ]
    if not quoted:
        with pytest.raises(NotComputable) as exc:
            regulatory_fx._read_positions(facts)  # pyright: ignore[reportPrivateUsage]
        assert exc.value.blocks_filing
        assert currency in str(exc.value)
        return
    read = regulatory_fx._read_positions(facts)  # pyright: ignore[reportPrivateUsage]
    nop = compute_nop(list(read.positions), Decimal("50000"), Decimal("10"), Decimal("20"))
    assert nop.overall_nop == Decimal("12850")
    assert nop.single_ccy_max_pct == Decimal("25.700000")
    assert not nop.within_single_limit
    assert not nop.within_aggregate_limit
    assert any("return history" in warning for group in groups for warning in group.warnings)


@pytest.mark.parametrize("source", ["reference", "market"])
def test_one_historical_spot_is_not_a_return_history_or_a_current_quote(source: str) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b): one historical spot cannot license NOP omission."""
    canonical = _canonical(
        _row("USD/ONE-SPOT", "LOAN", currency="USD", balance="1000", balance_ghs="12850")
    )
    if source == "reference":
        canonical.refs["fx_rates_historical"] = [
            {"currency": "USD", "spot_rate": "12.85", "date": canonical.as_of.isoformat()}
        ]
    else:
        canonical.market_fx_history["USD"] = [(canonical.as_of, Decimal("12.85"))]
    specs, included = _derive_fx_positions(canonical, [])
    assert included == {"USD"}
    position = next(spec for spec in specs if spec.fact_group == "fx_position")
    fact = BankFinancialFact(
        fact_group=position.fact_group,
        category=position.category,
        amount=position.amount,
        attributes=position.attributes,
    )
    with pytest.raises(NotComputable) as exc:
        regulatory_fx._read_positions([fact])  # pyright: ignore[reportPrivateUsage]
    assert exc.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert "USD" in str(exc.value)


@pytest.mark.parametrize(
    ("hedge_sale", "capital_long", "capital_short", "deposit_ghs"),
    [("500", "6425", "0", None), ("2000", "0", "12850", None), ("500", "0", "0", "12000")],
)
def test_stricter_nop_quotes_preserve_the_capital_fx_basis(
    hedge_sale: str, capital_long: str, capital_short: str, deposit_ghs: str | None
) -> None:
    """Notice BG/FMD/2026/07 ¶1(a)–(b): NOP quote rules do not rewrite the CAR basis."""
    canonical = _canonical(
        _row("USD/LOAN", "LOAN", currency="USD", balance="1000", balance_ghs="12850"),
        _row(
            "USD/HEDGE",
            "FX_HEDGE",
            currency="USD",
            balance=hedge_sale,
            attributes={"sell_currency": "USD", "buy_currency": "GHS", "contract_rate": "12.85"},
        ),
    )
    if deposit_ghs is not None:
        canonical.positions.append(
            _row("USD/DEPOSIT", "DEPOSIT", currency="USD", balance="1000", balance_ghs=deposit_ghs)
        )
    specs, included = _derive_fx_positions(canonical, [])
    assert included == {"USD"}
    market = {spec.category: spec.amount for spec in specs if spec.fact_group == "market_risk"}
    assert market == {
        "net_long_fx": Decimal(capital_long),
        "net_short_fx": Decimal(capital_short),
    }
    position = next(spec for spec in specs if spec.fact_group == "fx_position")
    fact = BankFinancialFact(
        fact_group=position.fact_group,
        category=position.category,
        amount=position.amount,
        attributes=position.attributes,
    )
    with pytest.raises(NotComputable) as exc:
        regulatory_fx._read_positions([fact])  # pyright: ignore[reportPrivateUsage]
    assert exc.value.blocks_filing
    assert "USD" in str(exc.value)
