"""The FX run never invents a rate and never files a position it cannot state.

Audit 2026-08-22 D-13 (invented FX parity on a filed Net Open Position line) and
D-21 (unconvertible positions silently zeroed), for the FX/NOP path.

Two facts about the writer set the shape of these tests, and both were measured
rather than assumed:

* ``fact_derivation._resolve_spot`` returns ``None`` — and the writer stores an
  EMPTY ``spot_ghs`` — ONLY when no spot was ingested AND none can be implied,
  which requires ``net_ccy == 0``. An absent rate is therefore normally the
  harmless case (no exposure, no rate needed), NOT the dangerous one. Refusing
  on absence alone would fail a filed run over a matched book.
* ``spot_ghs`` is carried for DISCLOSURE only. Every measured figure — NOP, VaR,
  stressed VaR, every scenario — computes on ``net_ghs`` and ``net_ccy``
  (``app/domain/fx/engine.py``). So the dangerous state is not a missing rate,
  it is a ``net_ghs`` that contradicts its own ``net_ccy``: the signature of
  ``fact_derivation._position_row`` converting an unconvertible position to
  zero.

Every refusal below is a CONTRADICTION, never a tolerance. A booked-rate versus
period-end-spot difference is legitimate and is deliberately not flagged, so no
threshold is invented anywhere in this module.

Hermetic: the reader is pure over ORM rows, so no session is needed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.authority.outcomes import NotComputable, OutcomeState
from app.models import BankFinancialFact
from app.services.regulatory_fx import (
    _fx_error_from,
    _positions_from_facts,
    _read_positions,
    _spot_or_none,
)


def _fx_position(
    currency: str,
    spot: str,
    *,
    net_ccy: str = "80000",
    net_ghs: str = "1000000",
) -> BankFinancialFact:
    return BankFinancialFact(
        fact_group="fx_position",
        category=currency,
        amount=Decimal(net_ghs),
        currency=currency,
        attributes={
            "currency": currency,
            "side": "long",
            "spot_ghs": spot,
            "net_ccy": net_ccy,
            "assets_ccy": net_ccy,
            "liabilities_ccy": "0",
            "net_derivatives_ccy": "0",
        },
    )


# ---------------------------------------------------------------------------
# The rate reader
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   ", "not-a-number"])
def test_an_unusable_rate_is_absence_not_a_number(raw: str | None) -> None:
    assert _spot_or_none(raw) is None


def test_a_usable_rate_survives_verbatim() -> None:
    assert _spot_or_none("15.4321") == Decimal("15.4321")


# ---------------------------------------------------------------------------
# D-13 — a real exposure with no governed rate is not computable
# ---------------------------------------------------------------------------


def test_a_rateable_currency_reports_its_position() -> None:
    positions = _positions_from_facts([_fx_position("USD", "12.5")])
    assert [(p.currency, p.spot_ghs) for p in positions] == [("USD", Decimal("12.5"))]


def test_an_exposure_with_no_rate_is_not_computable_and_names_its_currency() -> None:
    with pytest.raises(NotComputable) as excinfo:
        _positions_from_facts([_fx_position("USD", "12.5"), _fx_position("JPY", "")])
    detail = excinfo.value.details[0]
    assert detail.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert detail.metric_id == "nop_ghs"
    assert detail.items == ("fact:fx_position:JPY",)
    assert "JPY" in detail.reason
    assert detail.blocks_filing
    # never counted at parity: the refusal is the whole point
    assert "par" in detail.reason


def test_a_non_positive_rate_is_not_a_rate() -> None:
    with pytest.raises(NotComputable) as excinfo:
        _positions_from_facts([_fx_position("JPY", "0")])
    detail = excinfo.value.details[0]
    assert detail.state is OutcomeState.DATA_QUALITY_BLOCK
    assert "JPY" in detail.reason


def test_every_blocked_currency_is_named_in_one_refusal() -> None:
    with pytest.raises(NotComputable) as excinfo:
        _positions_from_facts(
            [_fx_position("JPY", ""), _fx_position("CHF", "   "), _fx_position("USD", "12.5")]
        )
    named = sorted(item for detail in excinfo.value.details for item in detail.items)
    assert named == ["fact:fx_position:CHF", "fact:fx_position:JPY"]


# ---------------------------------------------------------------------------
# D-21 — an unconvertible position is never carried as a zero
# ---------------------------------------------------------------------------


def test_a_real_position_carried_at_zero_refuses() -> None:
    """``fact_derivation._position_row`` zeroes a position with no reporting-
    currency balance. Zero asserts the position does not exist; it must not file."""
    with pytest.raises(NotComputable) as excinfo:
        _positions_from_facts([_fx_position("USD", "12.85", net_ccy="214819919", net_ghs="0")])
    detail = excinfo.value.details[0]
    assert detail.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert detail.context["net_ccy"] == "214819919"
    assert detail.context["net_ghs"] == "0"
    assert detail.blocks_filing


def test_a_position_long_in_its_currency_and_short_in_the_reporting_unit_refuses() -> None:
    """Measured on the primary 2026-06-30: USD net_ccy +214,819,919.09 against
    net_ghs -810,341,254.34. No positive rate produces that reversal — part of
    the book was converted and part was not."""
    with pytest.raises(NotComputable) as excinfo:
        _positions_from_facts(
            [_fx_position("USD", "12.85", net_ccy="214819919.09", net_ghs="-810341254.34")]
        )
    detail = excinfo.value.details[0]
    assert detail.state is OutcomeState.DATA_QUALITY_BLOCK
    assert detail.blocks_filing
    assert "USD" in detail.reason


def test_an_ordinary_revaluation_difference_is_not_flagged() -> None:
    """net_ccy x spot != net_ghs is NORMAL: positions convert at their own booked
    rates, not at period-end spot. Only contradictions refuse, never a tolerance."""
    positions = _positions_from_facts(
        [_fx_position("USD", "12.85", net_ccy="5708836.29", net_ghs="73872365.44")]
    )
    assert [p.currency for p in positions] == ["USD"]


# ---------------------------------------------------------------------------
# The case that must NOT refuse — no exposure, so no rate was ever required
# ---------------------------------------------------------------------------


def test_a_flat_currency_with_no_rate_is_excluded_and_counted_not_refused() -> None:
    """A matched book (assets == liabilities) implies no rate and needs none.
    Refusing a filed run over an absent DISCLOSURE rate on an empty position
    would be a false refusal; dropping it silently is the D-21 defect. It is
    excluded and counted."""
    read = _read_positions(
        [_fx_position("USD", "12.5"), _fx_position("CHF", "", net_ccy="0", net_ghs="0")]
    )
    assert [p.currency for p in read.positions] == ["USD"]
    assert read.rate_not_required == ("CHF",)
    assert read.rate_not_required_count == 1


def test_a_flat_currency_that_does_carry_a_rate_still_files() -> None:
    read = _read_positions([_fx_position("CHF", "10.0", net_ccy="0", net_ghs="0")])
    assert [p.currency for p in read.positions] == ["CHF"]
    assert read.rate_not_required == ()


# ---------------------------------------------------------------------------
# The refusal survives the run lifecycle as data, not as a bare 500
# ---------------------------------------------------------------------------


def test_a_refusal_persists_its_state_and_every_named_currency() -> None:
    with pytest.raises(NotComputable) as excinfo:
        _positions_from_facts([_fx_position("JPY", ""), _fx_position("CHF", "")])
    error = _fx_error_from(excinfo.value)
    assert error.code == OutcomeState.MISSING_REQUIRED_INPUT.value
    assert error.details is not None
    assert error.details["currencies"] == ["CHF", "JPY"]
    assert error.details["blocks_filing"] is True
    assert [d["metric_id"] for d in error.details["details"]] == ["nop_ghs", "nop_ghs"]
