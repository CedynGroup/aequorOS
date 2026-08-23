"""A governed HQLA haircut that is CONSUMED must reach ``input_hash``.

Forensic re-audit 2026-08-22 D-7. ``_snapshot_parameters`` recorded the HQLA
haircut/cap block only when the book held a NON-Level-1 asset, on the reasoning
that a Level-1-only book takes a 0% haircut. But ``hqla_l1_haircut_pct`` is a
governed control-plane row, not a code constant, and ``compute_lcr`` calls
``_hqla_haircut(params, "L1")`` for every Level-1 asset — refusing the run when
it is unresolved. So a supervisor (or a board register override) could move
every Level-1-only bank's filed LCR while its ``input_hash`` stood still.

These tests pin the corrected rule in BOTH directions: the levels a book
consumes are in the hash, and the ones it does not consume stay out — because
"record everything the loader queried" is the other way to be wrong, and it is
the reason a Level-1-only book must not carry an unseeded Level-2B rate.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import CurrentFinancialFact
from app.services import regulatory_forecasting, regulatory_liquidity, regulatory_parameters

_HAIRCUTS = {"L1": Decimal("0"), "L2A": Decimal("15"), "L2B": Decimal("50")}


def _fact(fact_group: str, category: str, hqla_level: str | None) -> CurrentFinancialFact:
    return CurrentFinancialFact(
        fact_group=fact_group,
        category=category,
        amount=Decimal("100"),
        hqla_level=hqla_level,
        attributes={},
    )


def _hqla(**overrides: object) -> regulatory_parameters.HqlaParameters:
    values: dict[str, object] = {
        "haircut_pct": dict(_HAIRCUTS),
        "level2_cap_pct": Decimal("40"),
        "level2b_cap_pct": Decimal("15"),
    }
    values.update(overrides)
    return regulatory_parameters.HqlaParameters(**values)  # pyright: ignore[reportArgumentType]


def _active(
    hqla: regulatory_parameters.HqlaParameters,
) -> regulatory_liquidity._ActiveLiquidityParams:  # pyright: ignore[reportPrivateUsage]
    return regulatory_liquidity._ActiveLiquidityParams(  # pyright: ignore[reportPrivateUsage]
        outflow_rates={"retail_stable": Decimal("5")},
        inflow_rates={"interbank_maturing": Decimal("100")},
        asf_weights={"retail_stable": Decimal("95")},
        rsf_weights={"loan_retail": Decimal("85")},
        thresholds={"lcr_min": Decimal("100")},
        hqla=hqla,
    )


def _params(
    facts: list[CurrentFinancialFact], hqla: regulatory_parameters.HqlaParameters | None = None
) -> dict[str, object]:
    return regulatory_liquidity._snapshot_parameters(  # pyright: ignore[reportPrivateUsage]
        _active(hqla or _hqla()), facts
    )


def test_a_level_1_only_book_carries_the_governed_l1_haircut() -> None:
    """The defect itself: an all-L1 book consumes ``hqla_l1_haircut_pct``."""
    parameters = _params([_fact("securities", "bog_bills", "L1")])
    assert parameters["hqla_haircuts_pct"] == {"L1": "0"}
    # It cannot bind either cap, so neither is recorded — the "only when
    # consumed" half of the rule, which is what keeps an unseeded Level-2B rate
    # from blocking or hashing a Level-1-only bank.
    assert "hqla_caps_pct" not in parameters


def test_moving_the_governed_l1_haircut_moves_the_snapshot() -> None:
    """Two runs under different governed haircuts must not agree."""
    facts = [_fact("securities", "bog_bills", "L1")]
    seeded = _params(facts)
    governed = _params(facts, _hqla(haircut_pct={**_HAIRCUTS, "L1": Decimal("2")}))
    assert seeded["hqla_haircuts_pct"] == {"L1": "0"}
    assert governed["hqla_haircuts_pct"] == {"L1": "2"}
    assert seeded != governed


def test_only_the_levels_the_book_holds_are_recorded() -> None:
    parameters = _params(
        [_fact("securities", "bog_bills", "L1"), _fact("securities", "hqla_level2a", "L2A")]
    )
    assert parameters["hqla_haircuts_pct"] == {"L1": "0", "L2A": "15"}
    # Level 2A is present, so both caps are resolved by ``_compute_hqla``.
    assert parameters["hqla_caps_pct"] == {
        regulatory_parameters.HQLA_LEVEL2_CAP_CODE: "40",
        regulatory_parameters.HQLA_LEVEL2B_CAP_CODE: "15",
    }


def test_an_unclassifiable_holding_consumes_no_rate() -> None:
    """``hqla_level=None`` is filtered out of the stock, so it charges nothing."""
    parameters = _params([_fact("securities", "hqla_unclassified", None)])
    assert "hqla_haircuts_pct" not in parameters
    assert "hqla_caps_pct" not in parameters


def test_the_filter_matches_the_engine_not_the_whole_fact_set() -> None:
    """Only ``securities`` facts reach ``_compute_hqla``; the snapshot agrees."""
    parameters = _params([_fact("balance_sheet", "cash_vault", "L1")])
    assert "hqla_haircuts_pct" not in parameters


def test_an_unrecognised_level_is_not_smuggled_into_the_hash() -> None:
    """The engine raises on it, so no run and no rate exist to record."""
    parameters = _params([_fact("securities", "mystery", "L3")])
    assert "hqla_haircuts_pct" not in parameters


def test_the_forecast_snapshot_applies_the_identical_rule() -> None:
    """The projected LCR calls the same engine, so it consumes the same rate."""
    facts = [_fact("securities", "bog_bills", "L1")]
    consumed = regulatory_forecasting._consumed_hqla_levels(facts)  # pyright: ignore[reportPrivateUsage]
    assert consumed == {"L1"}
    assert regulatory_liquidity._consumed_hqla_levels(facts) == consumed  # pyright: ignore[reportPrivateUsage]
