"""The enterprise-stress ``input_hash`` must carry the governed numbers it consumed.

Forensic re-audit 2026-08-22 **NEW-A1-1** — the same defect ``D-7`` named on the
liquidity plane, one module wider. ``enterprise_stress`` resolves the capital
risk weights, the capital thresholds, the CRM haircuts, the LCR/NSFR runoff and
weight tables, the liquidity thresholds and the Basel HQLA haircuts and caps
from the regulatory-parameter control plane, feeds every one of them into the
engines, and then sealed the run with an ``input_hash`` payload that carried
**none of them**: only the scenario paths, the plan and the facts. A supervisor
moving any governed number moved the filed ICAAP stress result while its
reproducibility hash stood still.

The rule these tests pin is the repo's value-based one, unchanged: a block joins
the hash when the arithmetic CONSUMES it. So the Basel liquidity block is absent
for an SDI (which never runs ``compute_lcr``), the HQLA haircuts cover exactly
the levels the stressed engine will charge, and the Level-2 caps join only when
a Level-2 holding exists.
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.domain.capital.ecl import EclAssumption
from app.domain.capital.engine import CapitalParams
from app.domain.liquidity.engine import LiquidityFact, LiquidityParams, consumed_hqla_levels
from app.models import CurrentFinancialFact
from app.services import enterprise_stress as svc
from app.services import regulatory_forecasting, regulatory_liquidity

_GOVERNED = svc._governed_parameters  # pyright: ignore[reportPrivateUsage]


def _capital(**overrides: object) -> CapitalParams:
    values: dict[str, object] = {
        "risk_weights": {"RW0": Decimal("0"), "RW100": Decimal("100")},
        "bia_alpha_pct": Decimal("15"),
        "fx_charge_pct": Decimal("100"),
        "rwa_multiplier_pct": Decimal("100"),
        "tier2_gp_cap_pct_credit_rwa": Decimal("1.25"),
        "cet1_min_pct": Decimal("6.5"),
        "tier1_min_pct": Decimal("8.5"),
        "car_min_pct": Decimal("13"),
        "leverage_min_pct": Decimal("6"),
        "car_early_warning_pct": Decimal("14"),
        "car_critical_pct": Decimal("11.5"),
    }
    values.update(overrides)
    return CapitalParams(**values)  # pyright: ignore[reportArgumentType]


def _liquidity(**overrides: object) -> LiquidityParams:
    values: dict[str, object] = {
        "outflow_rates": {"retail_stable": Decimal("5")},
        "inflow_rates": {"interbank_maturing": Decimal("100")},
        "asf_weights": {"retail_stable": Decimal("95")},
        "rsf_weights": {"loan_retail": Decimal("85")},
        "inflow_cap_pct": Decimal("75"),
        "lcr_min_pct": Decimal("100"),
        "lcr_amber_floor_pct": Decimal("110"),
        "nsfr_min_pct": Decimal("100"),
        "nsfr_amber_floor_pct": Decimal("110"),
        "hqla_haircut_pct": {"L1": Decimal("0"), "L2A": Decimal("15"), "L2B": Decimal("50")},
        "hqla_level2_cap_pct": Decimal("40"),
        "hqla_level2b_cap_pct": Decimal("15"),
    }
    values.update(overrides)
    return LiquidityParams(**values)  # pyright: ignore[reportArgumentType]


def _sec(level: str | None) -> LiquidityFact:
    return LiquidityFact(
        fact_group="securities",
        category="bog_bills",
        amount=Decimal("100"),
        hqla_level=level,
    )


# --- the block exists at all -------------------------------------------------


def test_the_governed_capital_parameters_reach_the_snapshot() -> None:
    """The defect itself: none of these were in the sealed payload."""
    block = _GOVERNED(_capital(), _liquidity(), [_sec("L1")], [])["capital"]

    assert block["risk_weights_pct"] == {"RW0": "0", "RW100": "100"}
    assert block["thresholds_pct"]["car_min"] == "13"
    assert block["thresholds_pct"]["bia_alpha_pct"] == "15"
    assert block["thresholds_pct"]["leverage_min"] == "6"
    assert block["basel_applicable"] is True


def test_moving_a_governed_risk_weight_moves_the_snapshot() -> None:
    baseline = _GOVERNED(_capital(), _liquidity(), [_sec("L1")], [])
    moved = _GOVERNED(
        _capital(risk_weights={"RW0": Decimal("0"), "RW100": Decimal("150")}),
        _liquidity(),
        [_sec("L1")],
        [],
    )
    assert baseline != moved


def test_moving_a_governed_car_floor_moves_the_snapshot() -> None:
    baseline = _GOVERNED(_capital(), _liquidity(), [_sec("L1")], [])
    moved = _GOVERNED(_capital(car_min_pct=Decimal("14")), _liquidity(), [_sec("L1")], [])
    assert baseline != moved


def test_the_governed_liquidity_parameters_reach_the_snapshot() -> None:
    block = _GOVERNED(_capital(), _liquidity(), [_sec("L1")], [])["liquidity"]

    assert block["outflow_runoff_rates_pct"] == {"retail_stable": "5"}
    assert block["inflow_rates_pct"] == {"interbank_maturing": "100"}
    assert block["asf_weights_pct"] == {"retail_stable": "95"}
    assert block["rsf_weights_pct"] == {"loan_retail": "85"}
    assert block["thresholds_pct"]["lcr_min"] == "100"
    assert block["thresholds_pct"]["lcr_inflow_cap_pct"] == "75"


def test_moving_a_governed_runoff_rate_moves_the_snapshot() -> None:
    baseline = _GOVERNED(_capital(), _liquidity(), [_sec("L1")], [])
    moved = _GOVERNED(
        _capital(),
        _liquidity(outflow_rates={"retail_stable": Decimal("10")}),
        [_sec("L1")],
        [],
    )
    assert baseline != moved


# --- HQLA: exactly the levels the stressed engine will charge ----------------


def test_a_level_1_only_book_carries_the_governed_l1_haircut_and_no_cap() -> None:
    """D-7's shape: the L1 rate is consumed by every book holding any HQLA."""
    block = _GOVERNED(_capital(), _liquidity(), [_sec("L1")], [])["liquidity"]
    assert block["hqla_haircuts_pct"] == {"L1": "0"}
    assert "hqla_caps_pct" not in block


def test_moving_the_governed_l1_haircut_moves_the_snapshot() -> None:
    baseline = _GOVERNED(_capital(), _liquidity(), [_sec("L1")], [])
    moved = _GOVERNED(
        _capital(),
        _liquidity(hqla_haircut_pct={"L1": Decimal("2"), "L2A": Decimal("15")}),
        [_sec("L1")],
        [],
    )
    assert baseline != moved


def test_a_level_2_book_carries_the_caps_it_can_bind() -> None:
    block = _GOVERNED(_capital(), _liquidity(), [_sec("L1"), _sec("L2A")], [])["liquidity"]
    assert block["hqla_haircuts_pct"] == {"L1": "0", "L2A": "15"}
    assert block["hqla_caps_pct"] == {"hqla_level2_cap_pct": "40", "hqla_level2b_cap_pct": "15"}


def test_an_unclassifiable_holding_consumes_no_rate() -> None:
    """``hqla_level=None`` is filtered out of the stock, so it hashes nothing."""
    block = _GOVERNED(_capital(), _liquidity(), [_sec("L1"), _sec(None)], [])["liquidity"]
    assert block["hqla_haircuts_pct"] == {"L1": "0"}


def test_a_book_with_no_hqla_at_all_records_no_haircut() -> None:
    block = _GOVERNED(_capital(), _liquidity(), [], [])["liquidity"]
    assert "hqla_haircuts_pct" not in block
    assert "hqla_caps_pct" not in block


# --- the SDI regime ----------------------------------------------------------


def test_an_sdi_run_hashes_no_basel_liquidity_claim() -> None:
    """An SDI never resolves these and never runs ``compute_lcr`` (docs/sdi.md §4.6)."""
    parameters = _GOVERNED(_capital(basel_applicable=False), None, [], [])
    assert "liquidity" not in parameters
    assert parameters["capital"]["basel_applicable"] is False


def test_the_sdi_prescribed_charges_join_when_they_move_rwa() -> None:
    parameters = _GOVERNED(
        _capital(basel_applicable=False, rwa_pct_of_credit_rwa={"operational": Decimal("15")}),
        None,
        [],
        [],
    )
    assert parameters["capital"]["sdi_rwa_charges_pct_of_credit_rwa"] == {"operational": "15"}


# --- book-shaped blocks join only when consumed ------------------------------


def test_crm_haircuts_join_only_when_configured() -> None:
    assert "crm_haircuts_pct" not in _GOVERNED(_capital(), _liquidity(), [], [])["capital"]
    configured = _GOVERNED(
        _capital(crm_haircuts={"cash": Decimal("0"), "sovereign_debt": Decimal("4")}),
        _liquidity(),
        [],
        [],
    )["capital"]
    assert configured["crm_haircuts_pct"] == {"cash": "0", "sovereign_debt": "4"}


def test_ecl_assumptions_join_only_when_the_register_exists() -> None:
    assert "ecl_assumptions" not in _GOVERNED(_capital(), _liquidity(), [], [])
    rows = [
        EclAssumption(segment="retail", stage=2, pd_pct=Decimal("8"), lgd_pct=Decimal("45")),
        EclAssumption(segment="corporate", stage=1, pd_pct=Decimal("2"), lgd_pct=Decimal("40")),
    ]
    parameters = _GOVERNED(_capital(), _liquidity(), [], rows)
    assert parameters["ecl_assumptions"] == [
        {"lgd_pct": "40", "pd_pct": "2", "segment": "corporate", "stage": 1},
        {"lgd_pct": "45", "pd_pct": "8", "segment": "retail", "stage": 2},
    ]


def test_the_block_is_canonically_ordered_and_json_serialisable() -> None:
    """The payload is hashed through ``json.dumps(sort_keys=True)``; nothing in it
    may be a Decimal, a set, or otherwise order-dependent."""
    parameters = _GOVERNED(
        _capital(crm_haircuts={"sovereign_debt": Decimal("4"), "cash": Decimal("0")}),
        _liquidity(),
        [_sec("L2B"), _sec("L1")],
        [EclAssumption(segment="retail", stage=1, pd_pct=Decimal("1"), lgd_pct=Decimal("2"))],
    )
    text = json.dumps(parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert json.loads(text) == parameters


# --- the HQLA filter is the ENGINE's, on every plane -------------------------


def test_the_consumed_level_filter_matches_the_engines_own_hqla_filter() -> None:
    """One rule, three planes. ``regulatory_liquidity`` and
    ``regulatory_forecasting`` keep their own copies over the persisted fact row;
    all three must answer identically or a plane hashes a rate it never charged."""
    cases: list[tuple[str, str | None]] = [
        ("securities", "L1"),
        ("securities", "L2A"),
        ("securities", "l2b"),
        ("securities", " L2A "),
        ("securities", None),
        ("securities", "L3"),
        ("securities", ""),
        ("balance_sheet", "L1"),
    ]
    engine_input = [
        LiquidityFact(fact_group=group, category="c", amount=Decimal("1"), hqla_level=level)
        for group, level in cases
    ]
    row_input = [
        CurrentFinancialFact(
            fact_group=group, category="c", amount=Decimal("1"), hqla_level=level, attributes={}
        )
        for group, level in cases
    ]
    expected = {"L1", "L2A", "L2B"}
    assert consumed_hqla_levels(engine_input) == expected
    assert regulatory_liquidity._consumed_hqla_levels(row_input) == expected  # pyright: ignore[reportPrivateUsage]
    assert regulatory_forecasting._consumed_hqla_levels(row_input) == expected  # pyright: ignore[reportPrivateUsage]
