"""Silent substitutions removed from the derivation (audit §3).

Each test names the exact substitution the audit found and pins the fail-closed
behaviour that replaced it. Every case is written against a book that would
previously have produced a plausible, wrong regulatory number in silence.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.domain.authority.outcomes import NotComputable, OutcomeState
from app.domain.capital.engine import (
    CapitalParams,
    RiskWeightUnavailable,
    resolve_risk_weight,
)
from app.models import BankFinancialFact, CanonicalPosition, CanonicalPositionSnapshot
from app.services.fact_derivation import (
    GroupResult,
    _Canonical,
    _classify_loans,
    _derive_crm_collateral,
    _derive_ecl_exposure,
    _derive_fx_positions,
    _derive_irr_positions,
    _derive_irr_swaps,
    _derive_lcr_inflows,
    _derive_loan_exposure,
    _derive_operational_income,
    _FxLeg,
    _is_sovereign_security,
    _position_row,
    _PositionRow,
    _resolve_spot,
    _split_securities,
)
from app.services.regulatory_fx import _read_positions

AS_OF = date(2026, 6, 30)
GHS_NAMES = ("ghana", "bank of ghana", "government of ghana")


def _row(  # noqa: PLR0913 - a position row is wide by nature
    source_reference: str,
    position_type: str,
    *,
    source_system: str = "EXCEL_CSV",
    currency: str = "GHS",
    balance: str = "0",
    balance_ghs: str | None = None,
    notional_ghs: str = "0",
    maturity: date | None = None,
    next_repricing: date | None = None,
    rate_type: str | None = "FIXED",
    interest_rate: str | None = None,
    product_code: str | None = None,
    regulatory_category: str | None = None,
    counterparty_type: str | None = None,
    attributes: dict[str, Any] | None = None,
    converted: bool = True,
) -> _PositionRow:
    """One flattened snapshot. ``converted=False`` is a foreign-currency position
    whose snapshot carried NO ``balance_ghs`` — the D-21 state, which the
    derivation must represent as absence rather than as zero."""
    return _PositionRow(
        source_reference=source_reference,
        source_system=source_system,
        position_type=position_type,
        currency=currency,
        balance=Decimal(balance),
        balance_ghs=(
            Decimal(balance_ghs if balance_ghs is not None else balance) if converted else None
        ),
        interest_rate=Decimal(interest_rate) if interest_rate is not None else None,
        rate_type=rate_type,
        contractual_maturity=maturity,
        next_repricing_date=next_repricing,
        ifrs9_stage=None,
        product_code=product_code,
        regulatory_category=regulatory_category,
        counterparty_type=counterparty_type,
        branch_id=None,
        ecl_ghs=Decimal("0"),
        notional_ghs=Decimal(notional_ghs),
        ccf=None,
        attributes=attributes or {},
    )


def _canonical(*rows: _PositionRow, base_currency: str = "GHS") -> _Canonical:
    return _Canonical(
        as_of=AS_OF,
        base_currency=base_currency,
        positions=list(rows),
        gl_accounts=[],
        refs={},
        sovereign_issuer_names=GHS_NAMES,
    )


# ---------------------------------------------------------------------------
# FX spot: never 1.0
# ---------------------------------------------------------------------------


def test_fx_spot_is_never_invented_as_one() -> None:
    """Audit §3: no ingested spot and a zero net used to return 1.0."""
    warnings: list[str] = []
    assert _resolve_spot("USD", None, _FxLeg(), warnings) is None
    assert warnings == []


def test_fx_spot_prefers_ingested_then_implied() -> None:
    warnings: list[str] = []
    assert _resolve_spot("USD", Decimal("12.85"), _FxLeg(), warnings) == (
        Decimal("12.85"),
        "ingested",
    )
    implied = _resolve_spot(
        "USD",
        None,
        _FxLeg(assets_ccy=Decimal("100"), assets_reporting=Decimal("1285")),
        warnings,
    )
    assert implied is not None
    assert implied[0] == Decimal("12.850000")
    assert implied[1] == "implied_from_position_book"
    assert any("implied rate" in warning for warning in warnings)


def test_unconvertible_hedge_leg_excludes_the_currency_instead_of_valuing_it_at_par() -> None:
    """A hedge delta with no resolvable spot removes the currency, not the risk."""
    canonical = _Canonical(
        as_of=AS_OF,
        base_currency="GHS",
        positions=[
            # Assets and liabilities net to zero in USD, so no rate is implied.
            _row("A/1", "LOAN", currency="USD", balance="100", balance_ghs="1285"),
            _row("L/1", "DEPOSIT", currency="USD", balance="100", balance_ghs="1285"),
            _row(
                "H/1",
                "FX_HEDGE",
                currency="USD",
                balance="500",
                attributes={"hedge_id": "H1", "sell_currency": "USD", "buy_currency": "GHS"},
            ),
        ],
        gl_accounts=[],
        refs={},
        sovereign_issuer_names=GHS_NAMES,
    )
    groups: list[GroupResult] = []
    specs, included = _derive_fx_positions(canonical, groups)

    assert included == set()
    assert not [spec for spec in specs if spec.fact_group == "fx_position"]
    fx_group = next(group for group in groups if group.group == "fx_position")
    assert any("missing_required_input" in warning for warning in fx_group.warnings)


def test_currency_without_return_history_still_carries_the_capital_charge() -> None:
    """Audit §3: it used to vanish from the book, understating the FX charge."""
    canonical = _canonical(
        _row("A/1", "LOAN", currency="USD", balance="1000", balance_ghs="12850"),
    )
    groups: list[GroupResult] = []
    specs, included = _derive_fx_positions(canonical, groups)

    assert included == set()  # no VaR row without a return history
    market = {spec.category: spec.amount for spec in specs if spec.fact_group == "market_risk"}
    assert market["net_long_fx"] == Decimal("12850")
    assert market["net_short_fx"] == Decimal("0")
    fx_group = next(group for group in groups if group.group == "fx_position")
    assert any("IS included in the net open position" in w for w in fx_group.warnings)


# ---------------------------------------------------------------------------
# Securities: no free Level-1 HQLA at 0% risk weight
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [
        _row("S/1", "SECURITY_HOLDING", counterparty_type="SOVEREIGN"),
        _row("S/2", "SECURITY_HOLDING", counterparty_type="CENTRAL_BANK"),
        _row("S/3", "SECURITY_HOLDING", attributes={"instrument": "gog_bond"}),
        _row("S/4", "SECURITY_HOLDING", attributes={"issuer_class": "public_institution"}),
        _row("S/5", "SECURITY_HOLDING", product_code="SEC.TBILL.91"),
        _row("S/6", "SECURITY_HOLDING", attributes={"issuer": "Government of Ghana"}),
    ],
)
def test_sovereign_evidence_is_recognised(row: _PositionRow) -> None:
    assert _is_sovereign_security(row, GHS_NAMES) is True


@pytest.mark.parametrize(
    "row",
    [
        _row("C/1", "SECURITY_HOLDING", counterparty_type="CORPORATE"),
        _row("C/2", "SECURITY_HOLDING", product_code="SEC.CORP.BOND.5Y"),
        _row("C/3", "SECURITY_HOLDING", attributes={"issuer": "Acme Manufacturing plc"}),
        _row("C/4", "SECURITY_HOLDING"),
    ],
)
def test_paper_without_sovereign_evidence_is_not_recognised(row: _PositionRow) -> None:
    assert _is_sovereign_security(row, GHS_NAMES) is False


def test_non_sovereign_holdings_leave_the_hqla_and_zero_weight_lines() -> None:
    """Audit §3: every SECURITY_HOLDING was L1 HQLA at RW0, no issuer test."""
    canonical = _canonical(
        _row("S/1", "SECURITY_HOLDING", balance="15000000", product_code="SEC.TBILL.91"),
        _row("C/1", "SECURITY_HOLDING", balance="8000000", counterparty_type="CORPORATE"),
    )
    warnings: list[str] = []
    split, non_sovereign = _split_securities(canonical, warnings)

    assert split.bills == Decimal("15000000")
    assert split.bonds == Decimal("0")
    assert non_sovereign == Decimal("8000000")
    # The sovereign bill is cedi-denominated domestic paper, so it is still
    # Level 1 — the issuer test and the LEVEL test are independent gates
    # (tests/services/test_hqla_level_classification.py covers the second).
    assert split.l1_bills == Decimal("15000000")
    assert split.unclassified == Decimal("0")
    assert any("NOT counted as Level-1 HQLA" in warning for warning in warnings)
    assert any("C/1" in warning for warning in warnings)


# ---------------------------------------------------------------------------
# LCR inflows: no 2%-of-gross-loans assumption
# ---------------------------------------------------------------------------


def test_lcr_inflows_are_zero_not_two_percent_when_no_maturity_exists() -> None:
    """Audit §3: an invented inflow RAISES the LCR. It is now not computable."""
    canonical = _canonical(
        _row("L/1", "LOAN", balance="50000000", regulatory_category="CORPORATE_UNRATED"),
        _row("L/2", "LOAN", balance="20000000", regulatory_category="RETAIL_UNSECURED"),
    )
    loan_rows = _classify_loans(canonical, [])
    groups: list[GroupResult] = []
    specs = _derive_lcr_inflows(canonical, loan_rows, groups)

    amounts = {spec.category: spec.amount for spec in specs}
    assert amounts["retail_loan_repayments"] == Decimal("0")
    assert amounts["corporate_sme_repayments"] == Decimal("0")
    group = next(item for item in groups if item.group == "lcr_inflow")
    assert any("NOT COMPUTABLE" in warning for warning in group.warnings)
    assert all("not computable" in spec.derived_from for spec in specs[:2])


def test_lcr_inflows_still_use_real_maturities_when_they_exist() -> None:
    canonical = _canonical(
        _row(
            "L/1",
            "LOAN",
            balance="50000000",
            regulatory_category="CORPORATE_UNRATED",
            maturity=AS_OF + timedelta(days=10),
        ),
    )
    loan_rows = _classify_loans(canonical, [])
    groups: list[GroupResult] = []
    specs = _derive_lcr_inflows(canonical, loan_rows, groups)
    amounts = {spec.category: spec.amount for spec in specs}
    assert amounts["corporate_sme_repayments"] == Decimal("50000000")


# ---------------------------------------------------------------------------
# Swaps: no assumed direction
# ---------------------------------------------------------------------------


def test_swap_without_a_direction_is_excluded_not_assumed_pay_fixed() -> None:
    canonical = _canonical(
        _row(
            "SWAP/1",
            "INTEREST_RATE_SWAP",
            balance="20000000",
            notional_ghs="20000000",
            maturity=AS_OF + timedelta(days=1095),
            attributes={"swap_id": "IRS-1", "pay_rate_pct": "24.5"},
        ),
    )
    groups: list[GroupResult] = []
    specs = _derive_irr_swaps(canonical, groups)

    assert specs == []
    group = next(item for item in groups if item.group == "irr_swap")
    assert any("no direction was ingested" in warning for warning in group.warnings)


def test_swap_without_a_receive_index_warns_about_the_documented_reset() -> None:
    canonical = _canonical(
        _row(
            "SWAP/1",
            "INTEREST_RATE_SWAP",
            balance="20000000",
            notional_ghs="20000000",
            maturity=AS_OF + timedelta(days=1095),
            attributes={
                "swap_id": "IRS-1",
                "pay_rate_pct": "24.5",
                "direction": "pay_fixed",
            },
        ),
    )
    groups: list[GroupResult] = []
    specs = _derive_irr_swaps(canonical, groups)

    assert len(specs) == 1
    group = next(item for item in groups if item.group == "irr_swap")
    assert any("no receive_index was ingested" in warning for warning in group.warnings)


# ---------------------------------------------------------------------------
# IRRBB bucketing: the asymmetric defaults are counted and stamped
# ---------------------------------------------------------------------------


def test_default_bucketing_is_counted_warned_and_stamped() -> None:
    """Audit §3: a horizonless asset went to 5y+, an interbank line to
    overnight, and nothing said so."""
    canonical = _canonical(
        _row(
            "L/1",
            "LOAN",
            balance="40000000",
            regulatory_category="CORPORATE_UNRATED",
            interest_rate="0.24",
        ),
        _row("IB/1", "INTERBANK_PLACEMENT", balance="5000000", interest_rate="0.14"),
    )
    loan_rows = _classify_loans(canonical, [])
    groups: list[GroupResult] = []
    specs = _derive_irr_positions(canonical, loan_rows, groups)

    by_category = {spec.category: spec for spec in specs}
    corporate = next(spec for key, spec in by_category.items() if key.endswith("_5yplus"))
    assert corporate.attributes["defaulted_balance"] == "40000000.0000"
    interbank = next(
        spec for key, spec in by_category.items() if key.startswith("interbank_placements")
    )
    assert interbank.attributes["bucket"] == "overnight"
    assert interbank.attributes["defaulted_balance"] == "5000000.0000"

    group = next(item for item in groups if item.group == "irr_position")
    assert any("bucketed by a DEFAULT" in warning for warning in group.warnings)
    assert any("loans with no repricing horizon" in warning for warning in group.warnings)
    assert any("interbank placements with no repricing horizon" in w for w in group.warnings)


def test_a_dated_book_carries_no_default_stamp() -> None:
    """A fully dated book derives — and hashes — exactly as before."""
    canonical = _canonical(
        _row(
            "L/1",
            "LOAN",
            balance="40000000",
            regulatory_category="CORPORATE_UNRATED",
            interest_rate="0.24",
            maturity=AS_OF + timedelta(days=200),
        ),
    )
    loan_rows = _classify_loans(canonical, [])
    groups: list[GroupResult] = []
    specs = _derive_irr_positions(canonical, loan_rows, groups)

    assert all("defaulted_balance" not in spec.attributes for spec in specs)
    group = next(item for item in groups if item.group == "irr_position")
    assert not any("bucketed by a DEFAULT" in warning for warning in group.warnings)


# ---------------------------------------------------------------------------
# ECL / CRM absence is an explicit state, not a silent omission
# ---------------------------------------------------------------------------


def test_absent_ecl_and_crm_report_an_explicit_not_computable_state(
    db_session: Session,
) -> None:
    canonical = _canonical(
        _row("L/1", "LOAN", balance="40000000", regulatory_category="CORPORATE_UNRATED"),
    )
    loan_rows = _classify_loans(canonical, [])
    groups: list[GroupResult] = []
    assert _derive_ecl_exposure(loan_rows, groups) == []
    assert _derive_crm_collateral(loan_rows, groups) == []

    ecl = next(item for item in groups if item.group == "ecl_exposure")
    assert ecl.status == "skipped"
    assert "Not computable" in (ecl.note or "")
    assert "INGESTED provisions" in (ecl.note or "")
    crm = next(item for item in groups if item.group == "crm_collateral")
    assert crm.status == "skipped"
    assert "Not computable" in (crm.note or "")


# ---------------------------------------------------------------------------
# The BIA base is one named series, not the whole operational_income group
# ---------------------------------------------------------------------------


def test_operational_income_series_are_separately_named_and_capped_at_three_years() -> None:
    """The derivation contract behind the operational-RWA finding.

    ``operational_income`` deliberately carries FIVE distinct annual series —
    ``gross_income_*``, ``net_interest_income_*``, ``net_income_*``,
    ``operating_expenses_*``, ``provisions_*``. Only the first is the Basel II
    ¶649 BIA base; the others are consumed elsewhere (``implied_rating``
    ``_latest_income_value`` reads net income, opex and provisions by prefix),
    so the derivation is right to emit them and right to name them apart.

    A consumer that averages the WHOLE group therefore divides by 5x the number
    of years and treats expenses as income. This test pins the derivation half
    of that contract: the series are distinguishable by category prefix, the
    gross-income identity holds, and no more than three annual windows are ever
    emitted.
    """
    months = []
    for year in (2024, 2025, 2026):
        for month in range(1, 13):
            months.append(
                {
                    "period_end": f"{year}-{month:02d}-28",
                    "net_interest_income_ghs": "2000000",
                    "non_interest_income_ghs": "500000",
                    "net_income_ghs": "900000",
                    "operating_expenses_ghs": "1200000",
                    "provisions_ghs": "400000",
                }
            )
    canonical = _Canonical(
        as_of=AS_OF,
        base_currency="GHS",
        positions=[],
        gl_accounts=[],
        refs={"historical_financials": months},
        sovereign_issuer_names=GHS_NAMES,
    )
    groups: list[GroupResult] = []
    specs = _derive_operational_income(canonical, groups)

    by_category = {spec.category: spec.amount for spec in specs}
    years = {2024, 2025, 2026}
    assert {spec.income_year for spec in specs} == years
    # Five distinct series, never merged into one measure.
    for year in years:
        assert by_category[f"gross_income_{year}"] == Decimal("30000000")
        assert by_category[f"net_interest_income_{year}"] == Decimal("24000000")
        assert by_category[f"net_income_{year}"] == Decimal("10800000")
        assert by_category[f"operating_expenses_{year}"] == Decimal("14400000")
        assert by_category[f"provisions_{year}"] == Decimal("4800000")
        # The BIA base is net interest + non-interest income, by construction.
        assert by_category[f"gross_income_{year}"] == by_category[
            f"net_interest_income_{year}"
        ] + Decimal("6000000")
    # At most three annual windows, whatever the history depth.
    assert len([spec for spec in specs if spec.category.startswith("gross_income_")]) == 3
    # The BIA base is a strict subset of the group: averaging the group divides
    # by 15, not 3, and sweeps operating expenses and provisions into "income".
    assert len(specs) == 15


# ---------------------------------------------------------------------------
# D-21: a foreign-currency position with no conversion is absent, never zero
# ---------------------------------------------------------------------------


def _fx_canonical(*rows: _PositionRow) -> _Canonical:
    """A book whose USD leg carries the daily return history the VaR row needs,
    so ``_derive_fx_positions`` emits an ``fx_position`` row for it."""
    history = [
        {"currency": "USD", "date": f"2026-06-{day:02d}", "spot_rate": "12.85"}
        for day in range(1, 30)
    ]
    return _Canonical(
        as_of=AS_OF,
        base_currency="GHS",
        positions=list(rows),
        gl_accounts=[],
        refs={"fx_rates_historical": history},
        sovereign_issuer_names=GHS_NAMES,
    )


def _snapshot(currency: str, balance: str, balance_ghs: str | None) -> CanonicalPositionSnapshot:
    attributes: dict[str, Any] = {}
    if balance_ghs is not None:
        attributes["balance_ghs"] = balance_ghs
    return CanonicalPositionSnapshot(
        source_reference="P/1",
        source_system="EXCEL_CSV",
        balance=Decimal(balance),
        attributes=attributes,
    )


def _position(currency: str) -> CanonicalPosition:
    return CanonicalPosition(position_type="DEPOSIT", currency=currency)


def test_foreign_currency_position_without_a_conversion_is_absent_not_zero() -> None:
    """D-21 at the source. ``_position_row`` used to substitute ``_ZERO`` here.

    Zero is a CLAIM — it states the exposure does not exist — and it was the
    claim that reversed the direction of the two filed FX positions on the
    primary. Absence is the honest representation and is what every downstream
    total now excludes and counts.
    """
    row = _position_row(
        _snapshot("USD", "1000", None), _position("USD"), None, None, base_currency="GHS"
    )
    assert row.balance_ghs is None

    # The reporting-currency book itself still needs no conversion.
    base = _position_row(
        _snapshot("GHS", "1000", None), _position("GHS"), None, None, base_currency="GHS"
    )
    assert base.balance_ghs == Decimal("1000")

    # And an ingested conversion is used exactly as before.
    converted = _position_row(
        _snapshot("USD", "1000", "12850"), _position("USD"), None, None, base_currency="GHS"
    )
    assert converted.balance_ghs == Decimal("12850")


def test_partly_unconverted_fx_book_is_refused_by_the_fx_engine() -> None:
    """The end-to-end D-21 proof, in the two halves the design splits it into.

    The derivation stops manufacturing the zero and records what it left out;
    ``regulatory_fx`` — which owns the filed figure — refuses on the resulting
    contradiction. Neither half is asserted in isolation, because neither half
    alone protects the return.
    """
    canonical = _fx_canonical(
        # Converted: a USD asset of 100 at 12.85.
        _row("A/1", "LOAN", currency="USD", balance="100", balance_ghs="1285"),
        # Unconverted: a much larger USD liability with no reporting-currency
        # balance at all. The old code booked it at zero cedis, leaving a book
        # that is SHORT 900 USD but LONG 1,285 cedis.
        _row("L/1", "DEPOSIT", currency="USD", balance="1000", converted=False),
    )
    groups: list[GroupResult] = []
    specs, _ = _derive_fx_positions(canonical, groups)

    position = next(spec for spec in specs if spec.fact_group == "fx_position")
    # The currency leg carries the WHOLE book; the reporting leg carries only
    # what was actually converted. The two legs therefore disagree in direction,
    # which is exactly the contradiction the engine exists to catch.
    assert position.attributes["net_ccy"] == "-900.0000"
    assert position.amount == Decimal("1285")
    # The omission is recorded rather than implied.
    assert position.attributes["unconverted_position_count"] == "1"
    warnings = [warning for group in groups for warning in group.warnings]
    assert any("carry no ingested GHS balance" in warning for warning in warnings)

    # The engine that FILES the number refuses it.
    fact = BankFinancialFact(
        fact_group="fx_position",
        category="USD",
        amount=position.amount,
        currency="USD",
        attributes=dict(position.attributes),
    )
    with pytest.raises(NotComputable) as excinfo:
        _read_positions([fact])
    assert excinfo.value.details[0].state is OutcomeState.DATA_QUALITY_BLOCK


def test_a_fully_converted_fx_book_is_unchanged_and_carries_no_count() -> None:
    """The stamp is conditional, so a clean book derives — and hashes — as before."""
    canonical = _fx_canonical(
        _row("A/1", "LOAN", currency="USD", balance="100", balance_ghs="1285"),
        _row("L/1", "DEPOSIT", currency="USD", balance="40", balance_ghs="514"),
    )
    groups: list[GroupResult] = []
    specs, _ = _derive_fx_positions(canonical, groups)
    position = next(spec for spec in specs if spec.fact_group == "fx_position")
    assert position.attributes["net_ccy"] == "60.0000"
    assert position.amount == Decimal("771")
    assert "unconverted_position_count" not in position.attributes


def test_implied_spot_is_withdrawn_when_the_reporting_leg_is_incomplete() -> None:
    """An implied rate over a partial leg measures the conversion gap, not a rate.

    On the measured book (net_ccy -144.7m against a partial +21.0m cedis) it
    came out NEGATIVE, which is not an exchange rate at all.
    """
    warnings: list[str] = []
    partial = _FxLeg(
        assets_ccy=Decimal("100"),
        liabilities_ccy=Decimal("1000"),
        assets_reporting=Decimal("1285"),
        unconverted=1,
    )
    assert _resolve_spot("USD", None, partial, warnings) is None
    assert any("incomplete leg" in warning for warning in warnings)

    # An INGESTED rate is unaffected — absence of an implied fallback is not a
    # refusal of a rate the bank actually supplied.
    assert _resolve_spot("USD", Decimal("12.85"), partial, []) == (Decimal("12.85"), "ingested")


def test_unconverted_positions_are_excluded_from_the_balance_sheet_and_named() -> None:
    """Excluded and COUNTED — the ``sdi_capital._Exposures`` shape, not a zero."""
    canonical = _canonical(
        _row("L/1", "LOAN", balance="1000", regulatory_category="CORPORATE_UNRATED"),
        _row(
            "L/2",
            "LOAN",
            currency="USD",
            balance="500",
            regulatory_category="CORPORATE_UNRATED",
            converted=False,
        ),
    )
    warnings: list[str] = []
    loans = _classify_loans(canonical, warnings)
    groups: list[GroupResult] = []
    specs = _derive_loan_exposure(loans, groups)
    exposure = next(spec for spec in specs if spec.category == "corporate_unrated")
    # 1,000 — the USD 500 is NOT in the cedi total, and was never converted at
    # par or at an invented rate to get there.
    assert exposure.amount == Decimal("1000")


# ---------------------------------------------------------------------------
# D-22: an unrecognised loan category gets no risk weight, not RW100
# ---------------------------------------------------------------------------


def test_unmapped_loan_category_carries_no_risk_weight() -> None:
    """The fallback to ``corporate_unrated`` (RW100) is gone.

    ``LOAN_SME`` / ``LOAN_RETAIL`` / ``LOAN_MORTGAGE`` are real product labels on
    the primary today. At 2026-06-30 they carry GHS 387,209,829.04 across 363
    positions on one institution — and they do so in EVERY period of that
    institution's ten-year history — each one risk-weighted as an unrated
    corporate at 100% on the strength of a string the platform could not
    interpret.
    """
    canonical = _canonical(
        _row("L/1", "LOAN", balance="1000", regulatory_category="LOAN_MORTGAGE", product_code="M1"),
        _row("L/2", "LOAN", balance="500", regulatory_category="LOAN_SME", product_code="S1"),
        _row("L/3", "LOAN", balance="250", regulatory_category="CORPORATE_UNRATED"),
    )
    warnings: list[str] = []
    loans = _classify_loans(canonical, warnings)
    by_ref = {loan.row.source_reference: loan for loan in loans}

    assert by_ref["L/1"].risk_weight_code is None
    assert by_ref["L/1"].category == "unclassified_loan_mortgage"
    assert by_ref["L/2"].risk_weight_code is None
    assert by_ref["L/2"].category == "unclassified_loan_sme"
    # A mapped category is untouched.
    assert by_ref["L/3"].risk_weight_code == "RW100"
    assert by_ref["L/3"].category == "corporate_unrated"

    # The warning names the offending category AND the products to remap.
    assert any("'LOAN_MORTGAGE'" in warning and "M1" in warning for warning in warnings)
    assert not any("defaulted to" in warning for warning in warnings)


def test_a_loan_with_no_regulatory_category_at_all_carries_no_risk_weight() -> None:
    canonical = _canonical(_row("L/1", "LOAN", balance="1000", product_code="P1"))
    warnings: list[str] = []
    loans = _classify_loans(canonical, warnings)
    assert loans[0].risk_weight_code is None
    assert loans[0].category == "unclassified_unmapped"


def test_the_capital_engine_refuses_an_unclassified_exposure() -> None:
    """The other half of D-22: the derivation stops asserting, the engine refuses.

    ``resolve_risk_weight`` is the single authority that turns a code into a
    percentage, and it already refuses a missing code. Emitting ``None`` routes
    an unrecognised exposure into that existing refusal instead of into a
    plausible, wrong 100%.
    """
    canonical = _canonical(
        _row("L/1", "LOAN", balance="1000", regulatory_category="LOAN_MORTGAGE", product_code="M1")
    )
    loans = _classify_loans(canonical, [])
    groups: list[GroupResult] = []
    spec = _derive_loan_exposure(loans, groups)[0]
    assert spec.risk_weight_code is None

    params = CapitalParams(
        risk_weights={"RW100": Decimal("100")},
        bia_alpha_pct=Decimal("15"),
        fx_charge_pct=Decimal("10"),
        rwa_multiplier_pct=Decimal("100"),
        tier2_gp_cap_pct_credit_rwa=Decimal("1.25"),
        cet1_min_pct=Decimal("6.5"),
        tier1_min_pct=Decimal("8"),
        car_min_pct=Decimal("13"),
        leverage_min_pct=Decimal("6"),
        car_early_warning_pct=Decimal("14"),
        car_critical_pct=Decimal("10"),
    )
    with pytest.raises(RiskWeightUnavailable) as excinfo:
        resolve_risk_weight(params, spec.risk_weight_code, spec.category)
    detail = excinfo.value.details[0]
    assert detail.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert "unclassified_loan_mortgage" in detail.reason

    # And the recognised code still resolves, so nothing else moved.
    assert resolve_risk_weight(params, "RW100", "corporate_unrated") == Decimal("100")


def test_an_unclassified_exposure_never_joins_the_corporate_book() -> None:
    """It keeps its balance everywhere rate risk is measured, under its own name.

    Dropping it would understate the repricing gap; folding it into
    ``corporate_loans`` would assert it IS corporate, which is the very thing
    the platform does not know.
    """
    canonical = _canonical(
        _row(
            "L/1",
            "LOAN",
            balance="1000",
            regulatory_category="LOAN_MORTGAGE",
            maturity=AS_OF + timedelta(days=400),
            interest_rate="0.20",
        )
    )
    loans = _classify_loans(canonical, [])
    groups: list[GroupResult] = []
    specs = _derive_irr_positions(canonical, loans, groups)
    families = {spec.category.rsplit("_", 2)[0] for spec in specs}
    assert "unclassified_loans" in families
    assert "corporate_loans" not in families
    assert sum(spec.amount for spec in specs) == Decimal("1000")
