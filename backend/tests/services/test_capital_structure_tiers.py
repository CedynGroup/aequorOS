"""The ``capital_structure`` tier is a closed vocabulary, end to end.

Audit evidence 12.1: the tier parser read every value it did not recognise —
"Tier 2", "TIER_2", "Additional Tier 1", "" and "garbage" — as CET1, so a
subordinated loan tagged "Tier 2" landed in CET1, uncapped. And the SDI sample
booked the BoG Credit Risk Reserve as CET1, which the reserve's own cited rule
excludes from the capital base.

This module's other-suite cases live in ``test_capital_structure_tiers_integration.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.capital.service import sdi_capital
from app.data_engine.domain.ingestion.capital_tiers import parse_capital_tier
from app.data_engine.domain.ingestion.contracts import (
    ReferenceRowData,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CET1", ("CET1", False)),
        ("AT1", ("AT1", False)),
        ("TIER2", ("T2", False)),
        ("T2", ("T2", False)),
        ("Tier 2", ("T2", False)),
        ("TIER_2", ("T2", False)),
        ("Tier-2", ("T2", False)),
        ("Additional Tier 1", ("AT1", False)),
        ("AT 1", ("AT1", False)),
        ("CET1_DEDUCTION", ("CET1", True)),
        ("TIER2_DEDUCTION", ("T2", True)),
        ("t2_deduction", ("T2", True)),
        ("T1", None),
        ("Tier 1", None),
        ("", None),
        ("garbage", None),
        ("DEDUCTION", None),
        (None, None),
    ],
)
def test_capital_tier_vocabulary_is_closed(raw: str | None, expected: object) -> None:
    """BoG CRD June 2018 ¶32 (repo-cited): a component's tier decides the ratio it
    counts toward, so an unrecognised or ambiguous tier is refused, never CET1."""
    assert parse_capital_tier(raw) == expected


def _row(tier: str, amount: str = "20000000") -> ReferenceRowData:
    return ReferenceRowData(
        dataset_kind="capital_structure",
        source_locator="capital_structure.csv!A2",
        row_index=1,
        payload={"capital_component": "subordinated_debt", "amount_ghs": amount, "tier": tier},
    )


def test_unknown_capital_tier_is_rejected_at_ingestion() -> None:
    """BoG CRD June 2018 ¶32 (repo-cited): an unrecognised tier is refused at the door."""
    assert _row("Tier 2").row_index == 1
    with pytest.raises(ValueError, match="field 'tier' must be one of"):
        _row("garbage")
    with pytest.raises(ValueError, match="missing required field 'tier'"):
        _row("")


@pytest.mark.parametrize(
    "amount",
    ["-20,000,000", "-20%", "NaN", "sNaN", "Infinity", "-Infinity", float("nan"), float("inf")],
)
def test_capital_amount_requires_an_unchanged_finite_decimal(amount: object) -> None:
    """BoG CRD 2018 ¶32: refused capital amounts cannot reach Decimal consumers."""
    with pytest.raises(ValueError, match="amount_ghs.*finite Decimal"):
        _row("CET1_DEDUCTION", str(amount))


@pytest.mark.parametrize(
    "amount", ["-20000000", "0", "20000000.123456", "2e7", Decimal("-20.25"), 20, 20.25]
)
def test_finite_capital_amounts_reach_consumers_without_value_changes(amount: object) -> None:
    """BoG CRD 2018 ¶32: finite Decimal representations retain their signed amount."""
    row = _row("CET1_DEDUCTION", str(amount))
    assert sdi_capital.signed_component_amount(row.payload) == -abs(Decimal(str(amount)))
