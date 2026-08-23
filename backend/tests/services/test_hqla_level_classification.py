"""The Basel HQLA level is established from evidence, or it is refused.

Forensic re-audit 2026-08-22 D-6. ``fact_derivation`` stamped a literal
``hqla_level="L1"`` at all four securities emission sites, so:

* no Level-2 fact could exist anywhere in the platform — the per-level haircuts
  and the 40% / 15% Level-2 caps built for enterprise audit P0-8 were
  unreachable code, and their governed parameters never entered ``input_hash``;
* a holding whose tier the data could NOT settle was given L1 — 0% haircut, no
  cap, the single most favourable treatment Basel defines.

These tests pin the replacement in both directions: what earns Level 1, what
earns Level 2A/2B, and — the half that matters most — what is refused and why.
The functions are exercised directly because they are pure over a canonical
snapshot; the plumbing that carries their output into a run is covered by the
liquidity and BoG suites.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.services.fact_derivation import (
    GroupResult,
    _Canonical,  # pyright: ignore[reportPrivateUsage]
    _classify_security_hqla,  # pyright: ignore[reportPrivateUsage]
    _derive_securities,  # pyright: ignore[reportPrivateUsage]
    _PositionRow,  # pyright: ignore[reportPrivateUsage]
    _split_securities,  # pyright: ignore[reportPrivateUsage]
)

AS_OF = date(2026, 3, 31)
BASE = "GHS"


def _row(  # noqa: PLR0913 - one keyword per canonical evidence dimension
    reference: str,
    *,
    balance: str = "100",
    currency: str = BASE,
    counterparty_type: str | None = "SOVEREIGN",
    product_code: str | None = "TBILL.91D",
    attributes: dict[str, Any] | None = None,
) -> _PositionRow:
    return _PositionRow(
        source_reference=reference,
        source_system="EXCEL_CSV",
        position_type="SECURITY_HOLDING",
        currency=currency,
        balance=Decimal(balance),
        balance_ghs=Decimal(balance),
        interest_rate=None,
        rate_type=None,
        contractual_maturity=None,
        next_repricing_date=None,
        ifrs9_stage=None,
        product_code=product_code,
        regulatory_category=None,
        counterparty_type=counterparty_type,
        branch_id=None,
        ecl_ghs=Decimal("0"),
        notional_ghs=Decimal("0"),
        ccf=None,
        attributes=attributes or {},
    )


def _canonical(*rows: _PositionRow) -> _Canonical:
    return _Canonical(
        as_of=AS_OF,
        base_currency=BASE,
        positions=list(rows),
        gl_accounts=[],
        refs={},
    )


def _cash() -> dict[str, Decimal]:
    return {"cash_vault": Decimal("10"), "bog_excess_reserves": Decimal("20")}


# ---------------------------------------------------------------------------
# 1. what the evidence settles
# ---------------------------------------------------------------------------


def test_domestic_sovereign_paper_in_the_reporting_currency_is_level_1() -> None:
    """BCBS 238 ¶50(d)-(e) — the one case the canonical book settles alone."""
    level, basis = _classify_security_hqla(_row("SEC/1"), _canonical())

    assert level == "L1"
    assert "domestic sovereign" in basis


def test_an_ingested_classification_is_read_not_inferred() -> None:
    """The bank's own Basel determination wins, for every level it may state."""
    for declared in ("L1", "L2A", "L2B", "l2a"):
        level, basis = _classify_security_hqla(
            _row("SEC/1", attributes={"hqla_level": declared}), _canonical()
        )
        assert level == declared.upper()
        assert basis == "ingested attributes.hqla_level"


# ---------------------------------------------------------------------------
# 2. what it refuses — the half that was silently L1
# ---------------------------------------------------------------------------


def test_an_unrecognised_ingested_level_is_refused_never_treated_as_level_1() -> None:
    level, basis = _classify_security_hqla(
        _row("SEC/1", attributes={"hqla_level": "L3"}), _canonical()
    )

    assert level is None
    assert "not one of" in basis
    assert "is not Level 1" in basis


def test_public_sector_and_multilateral_paper_is_refused_without_a_risk_weight() -> None:
    """¶50(c) Level 1 and ¶52(a) Level 2A differ ONLY by the issuer's risk weight.

    The canonical book carries no per-issuer Basel risk weight for a security,
    so neither tier is establishable and neither may be assumed. Before the fix
    both landed on Level 1, the more favourable of the two.
    """
    for counterparty_type in ("GOVERNMENT_ENTITY", "MULTILATERAL_DEV_BANK"):
        level, basis = _classify_security_hqla(
            _row("SEC/1", counterparty_type=counterparty_type), _canonical()
        )
        assert level is None, counterparty_type
        assert "risk weight" in basis

    # Same conclusion through the documented attribute rather than the type.
    level, _ = _classify_security_hqla(
        _row("SEC/1", counterparty_type=None, attributes={"issuer_class": "public_enterprise"}),
        _canonical(),
    )
    assert level is None


def test_foreign_currency_sovereign_paper_is_refused() -> None:
    """¶50(c) needs the 0% risk weight; ¶50(e) needs the outflow in that currency."""
    level, basis = _classify_security_hqla(_row("SEC/1", currency="USD"), _canonical())

    assert level is None
    assert "USD" in basis
    assert BASE in basis


def test_the_bank_can_classify_what_the_platform_refuses_to_guess() -> None:
    """The refusals are not dead ends: every message names the way out."""
    level, _ = _classify_security_hqla(
        _row(
            "SEC/1",
            counterparty_type="MULTILATERAL_DEV_BANK",
            attributes={"hqla_level": "L2A"},
        ),
        _canonical(),
    )

    assert level == "L2A"


# ---------------------------------------------------------------------------
# 3. the split, and what reaches the fact rows
# ---------------------------------------------------------------------------


def test_the_split_partitions_the_same_total_it_always_reported() -> None:
    """Balance sheet unchanged; only the HQLA claim is partitioned."""
    warnings: list[str] = []
    split, non_sovereign = _split_securities(
        _canonical(
            _row("SEC/L1", balance="100"),
            _row("SEC/L2A", balance="40", attributes={"hqla_level": "L2A"}),
            _row("SEC/L2B", balance="25", attributes={"hqla_level": "L2B"}),
            _row("SEC/PSE", balance="30", counterparty_type="GOVERNMENT_ENTITY"),
            _row("SEC/CORP", balance="55", counterparty_type="CORPORATE", product_code="CORP.BOND"),
        ),
        warnings,
    )

    # The corporate bond never reaches the securities book at all — unchanged
    # behaviour, and the only figure that leaves the balance-sheet line.
    assert non_sovereign == Decimal("55")
    assert split.bills + split.bonds == Decimal("195")
    # Every cedi of the sovereign book is accounted for by exactly one tier.
    assert (
        split.l1_bills + split.l1_bonds + split.level2a + split.level2b + split.unclassified
        == split.bills + split.bonds
    )
    assert split.l1_bills == Decimal("100")
    assert split.level2a == Decimal("40")
    assert split.level2b == Decimal("25")
    assert split.unclassified == Decimal("30")
    assert len(split.exclusions) == 1
    reason, amount, references = split.exclusions[0]
    assert amount == Decimal("30")
    assert references == ("SEC/PSE",)
    assert "risk weight" in reason


def test_the_facts_carry_the_level_and_the_refusal_carries_none() -> None:
    warnings: list[str] = []
    split, _ = _split_securities(
        _canonical(
            _row("SEC/L1", balance="100"),
            _row("SEC/L2A", balance="40", attributes={"hqla_level": "L2A"}),
            _row("SEC/PSE", balance="30", counterparty_type="GOVERNMENT_ENTITY"),
        ),
        warnings,
    )
    groups: list[GroupResult] = []
    specs = _derive_securities(split, _cash(), groups)
    by_category = {spec.category: spec for spec in specs}

    assert by_category["bog_bills"].hqla_level == "L1"
    assert by_category["hqla_level2a"].hqla_level == "L2A"
    assert by_category["hqla_level2a"].amount == Decimal("40")
    # The refused holding is EMITTED (so the group still ties to the
    # balance-sheet securities lines, and no capital or NSFR figure moves) but
    # carries no level, which is what keeps it out of the LCR stock.
    assert by_category["hqla_unclassified"].hqla_level is None
    assert by_category["hqla_unclassified"].amount == Decimal("30")

    securities_group = next(group for group in groups if group.group == "securities")
    assert any("EXCLUDED from HQLA" in warning for warning in securities_group.warnings)
    assert any("SEC/PSE" in warning for warning in securities_group.warnings)


def test_a_level_1_only_book_emits_exactly_the_rows_it_always_did() -> None:
    """The regression guard: no golden may move for a purely domestic book.

    This is why the change moves no filed figure on the primary, where every
    current-generation SECURITY_HOLDING row is cedi-denominated sovereign paper.
    """
    warnings: list[str] = []
    split, non_sovereign = _split_securities(
        _canonical(_row("SEC/1", balance="260"), _row("SEC/2", balance="360")), warnings
    )
    specs = _derive_securities(split, _cash(), [])

    assert non_sovereign == Decimal("0")
    assert [spec.category for spec in specs] == [
        "bog_bills",
        "gog_bonds",
        "cash_vault_hqla",
        "bog_excess_reserves_hqla",
    ]
    assert all(spec.hqla_level == "L1" for spec in specs)
    assert split.l1_bills == split.bills
    assert split.l1_bonds == split.bonds
