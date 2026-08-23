"""Hand-verified tests for the Basel HQLA haircuts and Level-2 caps (P0-8).

Before 2026-08-21 ``compute_lcr`` summed every fact carrying any ``hqla_level``
at FACE VALUE: no 15% Level-2A haircut, no Level-2B haircut, no 40% Level-2 cap,
no 15% Level-2B sub-cap, and an unrecognised level counted as though it were
Level 1. The LCR was correct only for a book that is entirely Level 1, and
overstated otherwise.

Every expected figure below is derived independently inside the test with
explicit Decimal literals against BCBS 238 (Basel III LCR) §II.A, so the goldens
are never self-referential. Where a cap binds, the test ALSO asserts the
economically meaningful invariant — that the capped tier ends up at exactly its
governed share of the resulting stock — which is a check the implementation
formula cannot fake.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.liquidity.engine import (
    HQLA_LEVELS,
    LINE_CODE_LEVEL2_CAP,
    LINE_CODE_LEVEL2B_CAP,
    PARAM_HQLA_LEVEL2_CAP,
    PARAM_HQLA_LEVEL2B_CAP,
    LiquidityFact,
    LiquidityParams,
    MissingParameterError,
    UnclassifiedHqlaError,
    compute_lcr,
    hqla_haircut_param_code,
)
from app.services.regulatory_parameters import (
    HQLA_HAIRCUT_CODES,
    HQLA_LEVEL2_CAP_CODE,
    HQLA_LEVEL2B_CAP_CODE,
)

M = Decimal("1000000")
_HUNDRED = Decimal("100")
MONEY = Decimal("0.0001")
FOUR_DP = Decimal("0.0001")

# The governed HQLA parameter set: BCBS 238 ¶47 (40% / 15% caps), ¶50 (Level 1
# un-haircut), ¶52 (15% Level 2A), ¶54 (Level 2B; the platform applies the
# conservative 50% bound of the 25-50% range because the fact model carries no
# L2B sub-class). Written here as literals because a TEST is allowed to state the
# regulatory number it is checking; the ENGINE resolves them from the control
# plane and names none of them.
BASEL_HQLA_HAIRCUTS = {
    "L1": Decimal("0"),
    "L2A": Decimal("15"),
    "L2B": Decimal("50"),
}
LEVEL2_CAP_PCT = Decimal("40")
LEVEL2B_CAP_PCT = Decimal("15")


def _params(
    *,
    haircuts: dict[str, Decimal] | None = None,
    level2_cap: Decimal | None = LEVEL2_CAP_PCT,
    level2b_cap: Decimal | None = LEVEL2B_CAP_PCT,
) -> LiquidityParams:
    """A minimal LCR parameter set: one deposit run-off rate, no inflows."""
    return LiquidityParams(
        outflow_rates={"retail_deposits_stable": Decimal("10")},
        inflow_rates={},
        asf_weights={},
        rsf_weights={},
        inflow_cap_pct=Decimal("75"),
        lcr_min_pct=Decimal("100"),
        lcr_amber_floor_pct=Decimal("90"),
        nsfr_min_pct=Decimal("100"),
        nsfr_amber_floor_pct=Decimal("90"),
        hqla_haircut_pct=BASEL_HQLA_HAIRCUTS if haircuts is None else haircuts,
        hqla_level2_cap_pct=level2_cap,
        hqla_level2b_cap_pct=level2b_cap,
    )


def _sec(category: str, millions: str, level: str) -> LiquidityFact:
    return LiquidityFact(
        fact_group="securities",
        category=category,
        amount=Decimal(millions) * M,
        hqla_level=level,
    )


def _deposits(millions: str) -> LiquidityFact:
    return LiquidityFact(
        fact_group="balance_sheet",
        category="retail_deposits_stable",
        amount=Decimal(millions) * M,
        side="liability",
    )


def _hqla_lines(result: object) -> dict[str, Decimal]:
    return {
        item.line_code: item.weighted_amount
        for item in result.line_items  # type: ignore[attr-defined]
        if item.section == "hqla"
    }


# --- 1. Haircuts -------------------------------------------------------------


def test_level1_book_is_unchanged_by_the_control() -> None:
    """The regression guard: an all-Level-1 book weighs at face value, as before.

    This is why no existing LCR golden in the suite moves, and why the change
    moves no filed figure on the primary — where every current-generation
    ``SECURITY_HOLDING`` row is cedi-denominated sovereign paper, i.e. Level 1
    under BCBS 238 ¶50(d)-(e).

    The sentence that stood here until 2026-08-22 — *"every fact the platform
    derives today is emitted as L1"* — was true, and was the DEFECT (re-audit
    D-6): derivation stamped the literal, so this engine's Level-2 arithmetic
    was unreachable. ``fact_derivation._classify_security_hqla`` now establishes
    the level from the canonical evidence and refuses where it cannot, so a
    Level-1 stock is a finding about the book rather than a property of the code.
    """
    facts = (_sec("bog_bills", "260", "L1"), _sec("gog_bonds", "360", "L1"), _deposits("1000"))
    result = compute_lcr(facts, _params())

    assert result.hqla_total == (Decimal("620") * M).quantize(MONEY)
    assert result.hqla_composition.level1 == (Decimal("620") * M).quantize(MONEY)
    assert result.hqla_composition.level2a == Decimal("0").quantize(MONEY)
    assert result.hqla_composition.level2b == Decimal("0").quantize(MONEY)
    assert result.hqla_composition.level2_cap_adjustment == Decimal("0")
    assert result.hqla_composition.level2b_cap_adjustment == Decimal("0")
    assert result.all_hqla_level1 is True
    # No synthetic cap lines, and the un-haircut lines keep ``rate_pct=None`` so
    # the persisted line items are unchanged too.
    lines = _hqla_lines(result)
    assert set(lines) == {"bog_bills", "gog_bonds"}
    for item in result.line_items:
        if item.section == "hqla":
            assert item.rate_pct is None


def test_level_2a_takes_the_15_percent_haircut() -> None:
    # 200M Level 2A x (1 - 15%) = 170M. With 100M of Level 1 the caps cannot
    # bind (Level 2 share = 170/270 = 63% > 40%) — so this test isolates the
    # haircut by checking the composition, and the cap test below checks the cap.
    facts = (_sec("l1", "100", "L1"), _sec("l2a", "200", "L2A"), _deposits("1000"))
    result = compute_lcr(facts, _params())

    assert result.hqla_composition.level1 == (Decimal("100") * M).quantize(MONEY)
    assert result.hqla_composition.level2a == (Decimal("170") * M).quantize(MONEY)
    lines = _hqla_lines(result)
    assert lines["l2a"] == (Decimal("170") * M).quantize(MONEY)
    # The haircut is visible on the line item as the rate that was charged.
    l2a_item = next(item for item in result.line_items if item.line_code == "l2a")
    assert l2a_item.rate_pct == Decimal("15")
    assert l2a_item.exposure_amount == (Decimal("200") * M).quantize(MONEY)


def test_level_2b_takes_the_50_percent_haircut() -> None:
    facts = (_sec("l1", "1000", "L1"), _sec("l2b", "100", "L2B"), _deposits("2000"))
    result = compute_lcr(facts, _params())

    assert result.hqla_composition.level2b == (Decimal("50") * M).quantize(MONEY)
    l2b_item = next(item for item in result.line_items if item.line_code == "l2b")
    assert l2b_item.rate_pct == Decimal("50")


def test_the_lcr_falls_when_level_2_replaces_level_1() -> None:
    """The control's whole point: the same face value buys less liquidity.

    Two books with identical FACE value (300M) and identical outflows. Under the
    old face-value sum both reported the same LCR; under Basel the Level-2A book
    is worth 15% less and its LCR is lower.
    """
    outflow = _deposits("1000")  # 10% run-off => 100M net outflows
    all_l1 = compute_lcr((_sec("a", "300", "L1"), outflow), _params())
    with_l2a = compute_lcr(
        (_sec("a", "200", "L1"), _sec("b", "100", "L2A"), outflow), _params()
    )
    # 300M / 100M = 300%; (200 + 85) / 100 = 285%.
    assert all_l1.lcr_pct.quantize(FOUR_DP) == Decimal("300.0000")
    assert with_l2a.lcr_pct.quantize(FOUR_DP) == Decimal("285.0000")
    assert with_l2a.lcr_pct < all_l1.lcr_pct


# --- 2. Caps -----------------------------------------------------------------


def test_the_40_percent_level_2_cap_binds() -> None:
    """BCBS 238 Annex 1, 40% cap.

    Post-haircut: L1 = 100M, L2A = 60 x 0.85 = 51M, L2B = 40 x 0.50 = 20M.
      15% leg: max(20 - (15/85)(100+51), 20 - (15/60)(100), 0)
             = max(20 - 26.647, 20 - 25, 0) = 0  -> the sub-cap does NOT bind
      40% leg: max((51 + 20 - 0) - (40/60)(100), 0) = 71 - 66.667 = 4.333333M
      stock  = 100 + 51 + 20 - 0 - 4.333333 = 166.666667M
    """
    facts = (
        _sec("l1", "100", "L1"),
        _sec("l2a", "60", "L2A"),
        _sec("l2b", "40", "L2B"),
        _deposits("1000"),
    )
    result = compute_lcr(facts, _params())
    comp = result.hqla_composition

    assert comp.level1 == (Decimal("100") * M).quantize(MONEY)
    assert comp.level2a == (Decimal("51") * M).quantize(MONEY)
    assert comp.level2b == (Decimal("20") * M).quantize(MONEY)
    assert comp.level2b_cap_adjustment == Decimal("0")
    assert comp.level2_cap_adjustment == Decimal("4333333.3333")
    assert comp.total == Decimal("166666666.6667")
    assert comp.level2_cap_applied is True
    assert comp.level2b_cap_applied is False

    # The invariant the formula cannot fake: Level 2 ends at exactly 40% of the
    # resulting stock of HQLA.
    admitted_level2 = comp.level2a + comp.level2b - comp.level2_cap_adjustment
    assert (admitted_level2 / comp.total * Decimal("100")).quantize(FOUR_DP) == Decimal("40.0000")

    # The deduction is an auditable line, and the stock is the sum of its lines.
    lines = _hqla_lines(result)
    assert lines[LINE_CODE_LEVEL2_CAP] == Decimal("-4333333.3333")
    assert LINE_CODE_LEVEL2B_CAP not in lines
    assert sum(lines.values()) == comp.total
    assert result.hqla_total == comp.total
    assert result.all_hqla_level1 is False


def test_the_15_percent_level_2b_sub_cap_binds() -> None:
    """BCBS 238 Annex 1, 15% sub-cap.

    Post-haircut: L1 = 100M, L2A = 0, L2B = 60 x 0.50 = 30M.
      15% leg: max(30 - (15/85)(100), 30 - (15/60)(100), 0)
             = max(30 - 17.647059, 30 - 25, 0) = 12.352941M
      40% leg: max((0 + 30 - 12.352941) - 66.666667, 0) = 0
      stock  = 100 + 30 - 12.352941 = 117.647059M
    """
    facts = (_sec("l1", "100", "L1"), _sec("l2b", "60", "L2B"), _deposits("1000"))
    result = compute_lcr(facts, _params())
    comp = result.hqla_composition

    assert comp.level2b == (Decimal("30") * M).quantize(MONEY)
    assert comp.level2b_cap_adjustment == Decimal("12352941.1765")
    assert comp.level2_cap_adjustment == Decimal("0")
    assert comp.total == Decimal("117647058.8235")
    assert comp.level2b_cap_applied is True

    admitted_l2b = comp.level2b - comp.level2b_cap_adjustment
    assert (admitted_l2b / comp.total * Decimal("100")).quantize(FOUR_DP) == Decimal("15.0000")

    lines = _hqla_lines(result)
    assert lines[LINE_CODE_LEVEL2B_CAP] == Decimal("-12352941.1765")
    assert sum(lines.values()) == comp.total


def test_the_second_leg_of_the_15_percent_sub_cap_binds() -> None:
    """BCBS 238 Annex 1, the ``15/60 x Level 1`` leg — and both caps at once.

    The Annex-1 sub-cap is a MAXIMUM of two legs, and every case above is decided
    by the first (``15/85 x (L1 + L2A)``). This is the book that makes the second
    leg the binding one: a large Level 2A pool lifts the first leg's allowance
    above the second's, so the ``15/60 x Level 1`` leg is what actually caps
    Level 2B. (The two legs cross at ``L2A = 5/12 x L1``.)

    Post-haircut: L1 = 100M, L2A = 200 x 0.85 = 170M, L2B = 60 x 0.50 = 30M.
      15% leg: max(30 - (15/85)(100+170), 30 - (15/60)(100), 0)
             = max(30 - 47.647059, 30 - 25, 0) = 5M          <- second leg
      40% leg: max((170 + 30 - 5) - (40/60)(100), 0)
             = 195 - 66.666667 = 128.333333M
      stock  = 100 + 170 + 30 - 5 - 128.333333 = 166.666667M
    """
    facts = (
        _sec("l1", "100", "L1"),
        _sec("l2a", "200", "L2A"),
        _sec("l2b", "60", "L2B"),
        _deposits("1000"),
    )
    result = compute_lcr(facts, _params())
    comp = result.hqla_composition

    assert comp.level1 == (Decimal("100") * M).quantize(MONEY)
    assert comp.level2a == (Decimal("170") * M).quantize(MONEY)
    assert comp.level2b == (Decimal("30") * M).quantize(MONEY)
    assert comp.level2b_cap_adjustment == Decimal("5000000.0000")
    assert comp.level2_cap_adjustment == Decimal("128333333.3333")
    assert comp.total == Decimal("166666666.6667")
    assert comp.level2_cap_applied is True
    assert comp.level2b_cap_applied is True

    # BOTH governed shares are hit exactly — the check the formula cannot fake.
    admitted_level2 = (
        comp.level2a + comp.level2b - comp.level2b_cap_adjustment - comp.level2_cap_adjustment
    )
    admitted_level2b = comp.level2b - comp.level2b_cap_adjustment
    assert (admitted_level2 / comp.total * Decimal("100")).quantize(FOUR_DP) == Decimal("40.0000")
    assert (admitted_level2b / comp.total * Decimal("100")).quantize(FOUR_DP) == Decimal("15.0000")

    lines = _hqla_lines(result)
    assert lines[LINE_CODE_LEVEL2B_CAP] == Decimal("-5000000.0000")
    assert lines[LINE_CODE_LEVEL2_CAP] == Decimal("-128333333.3333")
    assert sum(lines.values()) == comp.total


@pytest.mark.parametrize(
    ("l1", "l2a", "l2b"),
    [
        ("100", "60", "40"),  # 40% cap binds alone
        ("100", "0", "60"),  # 15% sub-cap binds alone (first leg)
        ("100", "200", "60"),  # both bind; sub-cap decided by the second leg
        ("0", "0", "500"),  # no Level 1 at all
        ("300", "10", "5"),  # neither cap binds
    ],
)
def test_the_generalised_ratio_form_reproduces_the_basel_literals(
    l1: str, l2a: str, l2b: str
) -> None:
    """The engine writes no ratio as a literal — this proves it does not need to.

    ``_hqla_stock`` expresses the Annex-1 legs as ``cap2b/(100-cap2b)``,
    ``cap2b/(100-cap2)`` and ``cap2/(100-cap2)`` so no Basel fraction is hard
    coded in the calculation. At the governed 40 / 15 caps those must be exactly
    Basel's 15/85, 15/60 and 2/3 — computed here as literal fractions and
    compared against the engine over five books, so a re-derivation of the
    generalised form that happens to agree on ONE book cannot pass.
    """
    haircut = {"L1": Decimal("0"), "L2A": Decimal("15"), "L2B": Decimal("50")}
    level1 = (Decimal(l1) * M * (_HUNDRED - haircut["L1"]) / _HUNDRED).quantize(MONEY)
    level2a = (Decimal(l2a) * M * (_HUNDRED - haircut["L2A"]) / _HUNDRED).quantize(MONEY)
    level2b = (Decimal(l2b) * M * (_HUNDRED - haircut["L2B"]) / _HUNDRED).quantize(MONEY)

    # Basel's own fractions, written out.
    fifteen_over_eighty_five = Decimal("15") / Decimal("85")
    fifteen_over_sixty = Decimal("15") / Decimal("60")
    two_thirds = Decimal("2") / Decimal("3")
    adjustment_2b = max(
        level2b - fifteen_over_eighty_five * (level1 + level2a),
        level2b - fifteen_over_sixty * level1,
        Decimal("0"),
    ).quantize(MONEY)
    adjustment_2 = max(
        (level2a + level2b - adjustment_2b) - two_thirds * level1, Decimal("0")
    ).quantize(MONEY)
    expected_stock = (level1 + level2a + level2b - adjustment_2b - adjustment_2).quantize(MONEY)

    facts = [_deposits("1000")]
    for category, amount, level in (("l1", l1, "L1"), ("l2a", l2a, "L2A"), ("l2b", l2b, "L2B")):
        if Decimal(amount) > 0:
            facts.append(_sec(category, amount, level))
    comp = compute_lcr(tuple(facts), _params()).hqla_composition

    assert comp.level2b_cap_adjustment == adjustment_2b
    assert comp.level2_cap_adjustment == adjustment_2
    assert comp.total == expected_stock


def test_a_book_of_only_level_2b_is_capped_to_nothing() -> None:
    """With no Level 1 at all, both cap legs drive the admissible Level 2B to zero.

    ``max(L2B - (15/85)(0 + 0), L2B - (15/60)(0), 0) = L2B``: every post-haircut
    Level 2B unit is deducted. The bank has liquid-looking securities and no
    HQLA, which is exactly what Basel intends and the opposite of what the
    face-value sum reported.
    """
    facts = (_sec("l2b", "500", "L2B"), _deposits("1000"))
    result = compute_lcr(facts, _params())
    assert result.hqla_composition.level2b == (Decimal("250") * M).quantize(MONEY)
    assert result.hqla_composition.level2b_cap_adjustment == (Decimal("250") * M).quantize(MONEY)
    assert result.hqla_total == Decimal("0").quantize(MONEY)
    assert result.lcr_pct == Decimal("0").quantize(Decimal("0.000001"))
    assert result.status == "red"


def test_caps_are_not_required_when_no_level_2_is_held() -> None:
    """A Level-1-only bank is never blocked on a cap that provably cannot bind."""
    facts = (_sec("l1", "300", "L1"), _deposits("1000"))
    result = compute_lcr(facts, _params(level2_cap=None, level2b_cap=None))
    assert result.hqla_total == (Decimal("300") * M).quantize(MONEY)


# --- 3. Fail closed ----------------------------------------------------------


def test_an_unclassified_hqla_level_refuses_rather_than_counting_as_level_1() -> None:
    facts = (_sec("mystery", "100", "L3"), _deposits("1000"))
    with pytest.raises(UnclassifiedHqlaError) as exc:
        compute_lcr(facts, _params())
    assert exc.value.category == "mystery"
    assert exc.value.level == "L3"


def test_a_blank_hqla_level_refuses() -> None:
    facts = (
        LiquidityFact(fact_group="securities", category="blank", amount=M, hqla_level="  "),
        _deposits("1000"),
    )
    with pytest.raises(UnclassifiedHqlaError):
        compute_lcr(facts, _params())


def test_lowercase_levels_normalise_rather_than_refusing() -> None:
    facts = (_sec("l2a", "200", "l2a"), _sec("l1", "1000", "l1"), _deposits("2000"))
    result = compute_lcr(facts, _params())
    assert result.hqla_composition.level2a == (Decimal("170") * M).quantize(MONEY)


def test_a_level_held_with_no_resolved_haircut_refuses() -> None:
    """POLICY_UNRESOLVED, not a zero haircut: the rate is never assumed."""
    facts = (_sec("l2a", "100", "L2A"), _deposits("1000"))
    with pytest.raises(MissingParameterError) as exc:
        compute_lcr(facts, _params(haircuts={"L1": Decimal("0")}))
    # The message must name the row an operator can actually go and configure.
    assert "hqla_l2a_haircut_pct" in str(exc.value)
    assert exc.value.category == "hqla_l2a_haircut_pct"


def test_every_fail_closed_hqla_code_exists_in_the_control_plane() -> None:
    """The refusal names a real parameter row, for every level and both caps.

    Forensic re-audit 2026-08-22 **NEW-A1-2**: the engine used to refuse with
    ``'hqla_haircut:L2A'``, a code the control plane has never stored, and the
    comment above it claimed it was *"the exact control-plane code to
    configure"*. Because the engine is pure it cannot import the code table, so
    the two are bound HERE — a rename on either side fails this test rather than
    silently sending the next operator hunting for a row that does not exist.
    """
    for level in HQLA_LEVELS:
        assert hqla_haircut_param_code(level) == HQLA_HAIRCUT_CODES[level]
    assert PARAM_HQLA_LEVEL2_CAP == HQLA_LEVEL2_CAP_CODE
    assert PARAM_HQLA_LEVEL2B_CAP == HQLA_LEVEL2B_CAP_CODE


def test_level_2_held_with_no_resolved_cap_refuses() -> None:
    facts = (_sec("l1", "100", "L1"), _sec("l2a", "100", "L2A"), _deposits("1000"))
    with pytest.raises(MissingParameterError) as exc:
        compute_lcr(facts, _params(level2_cap=None))
    assert "hqla_level2_cap_pct" in str(exc.value)

    with pytest.raises(MissingParameterError) as exc2:
        compute_lcr(facts, _params(level2b_cap=None))
    assert "hqla_level2b_cap_pct" in str(exc2.value)


def test_a_degenerate_cap_configuration_refuses() -> None:
    """A 100% cap would divide by zero in the Annex-1 ratio form."""
    facts = (_sec("l1", "100", "L1"), _sec("l2a", "100", "L2A"), _deposits("1000"))
    with pytest.raises(MissingParameterError):
        compute_lcr(facts, _params(level2_cap=Decimal("100")))
