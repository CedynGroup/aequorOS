"""Every admitted reference kind validates at the shared ingestion boundary."""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.ingestion.constants import REFERENCE_DATASET_KINDS
from app.domain.ingestion.contracts import ReferenceRowData
from app.domain.ingestion.reference_schemas import SCHEMAS


def _row(kind: str, payload: dict[str, str | None]) -> ReferenceRowData:
    return ReferenceRowData(
        dataset_kind=kind,  # type: ignore[arg-type]
        source_locator="book.xlsx!A1",
        row_index=1,
        payload=payload,
    )


_VALID_TARGET: dict[str, str | None] = {
    "period": "2026-03-31",
    "grain": "quarter",
    "measure_id": "loans.balance_rc",
    "time_behaviour": "stock",
    "value": "1000",
    "version": "budget",
}
_VALID_UNIT: dict[str, str | None] = {
    "business_unit_id": "BR-001",
    "business_unit_name": "Head Office",
}


def test_a_well_formed_target_is_accepted() -> None:
    assert _row("performance_targets", dict(_VALID_TARGET)).row_index == 1


def test_a_period_that_is_not_the_end_of_its_grain_is_refused() -> None:
    """A quarterly target dated mid-quarter is a mistake about WHICH quarter is
    meant, and the message has to say which date that quarter ends on."""
    with pytest.raises(ValueError, match="last day of the quarter") as caught:
        _row("performance_targets", dict(_VALID_TARGET, period="2026-03-15"))
    assert "2026-03-31" in str(caught.value)


def test_a_missing_time_behaviour_is_refused_rather_than_defaulted() -> None:
    """The whole point of the field. A defaulted ``stock`` silently reads a flow
    target as a level, which is wrong by a period and looks like a real number."""
    payload: dict[str, str | None] = {
        k: v for k, v in _VALID_TARGET.items() if k != "time_behaviour"
    }
    with pytest.raises(ValueError, match="time_behaviour"):
        _row("performance_targets", payload)


def test_an_empty_time_behaviour_is_refused_too() -> None:
    with pytest.raises(ValueError, match="time_behaviour"):
        _row("performance_targets", dict(_VALID_TARGET, time_behaviour=""))


def test_a_business_unit_row_using_the_documented_aliases_is_accepted() -> None:
    """``unit_id`` / ``name`` is the spelling the public contract used to
    document; it stays a complete row."""
    assert _row("business_units", {"unit_id": "BR-001", "name": "Head Office"})


def test_a_row_that_spells_a_field_both_ways_with_different_values_is_refused() -> None:
    """Resolving it silently would pick a branch name by import order."""
    with pytest.raises(ValueError):
        _row("business_units", {**_VALID_UNIT, "unit_id": "BR-999"})


def test_a_unit_may_not_be_its_own_parent() -> None:
    with pytest.raises(ValueError):
        _row("business_units", {**_VALID_UNIT, "parent_unit_id": "BR-001"})


# --- audit A360 H3: the width the mart can store is enforced at the door ------------------


def test_an_over_long_unit_id_is_refused_at_the_door_with_the_limit_named() -> None:
    """A ``bi_dim_branch.branch_code`` is 120 characters wide. Accepted here, a
    longer id used to fail the tenant's whole nightly mart build on Postgres;
    the refusal now says which limit and never repeats the value."""
    wide = "x" * 121
    over: dict[str, str | None] = {**_VALID_UNIT, "business_unit_id": wide}
    with pytest.raises(ValueError, match="120-character limit") as caught:
        _row("business_units", over)
    assert "business_unit_id" in str(caught.value)
    assert "121 characters" in str(caught.value)
    assert wide not in str(caught.value)
    assert _row("business_units", {**_VALID_UNIT, "business_unit_id": "x" * 120})


def test_an_over_long_segment_branch_is_refused_at_the_door() -> None:
    row: dict[str, str | None] = {
        "as_of_date": "2026-06-30",
        "gl_account_code": "4001",
        "branch_id": "b" * 121,
        "ytd_balance": "1000",
    }
    with pytest.raises(ValueError, match="branch_id"):
        _row("gl_segment_balances", row)
    assert _row("gl_segment_balances", {**row, "branch_id": "b" * 120})


VALID_ROWS: dict[str, dict[str, str | None]] = {
    "capital_structure": {
        "capital_component": "paid_up_capital",
        "amount_ghs": "100",
        "tier": "CET1",
    },
    "behavioral_assumptions": {
        "product_code": "DEP-SAV",
        "assumption_type": "DEPOSIT_STABILITY",
        "value": "0.82",
    },
    "yield_curve": {
        "curve_name": "sovereign",
        "currency": "USD",
        "tenor_months": "12",
        "rate": "0.03",
        "quote_date": "2026-06-30",
    },
    "fx_rates_current": {"currency": "USD", "spot_rate": "15", "quote_date": "2026-06-30"},
    "fx_rates_historical": {"currency": "USD", "spot_rate": "15", "date": "2026-06-30"},
    "historical_cashflows": {
        "date": "2026-06-30",
        "deposit_inflow_ghs": "100",
        "deposit_outflow_ghs": "80",
    },
    "historical_financials": {"period_end": "2026-06-30", "net_interest_income_ghs": "100"},
    "business_units": _VALID_UNIT,
    "institution": {"institution_id": "source-bank-1", "name": "Source Bank"},
    "gl_mapping_bsd7": {"gl_account_code": "4001", "bsd7_item": "1a"},
    "subsidiaries": {
        "reporting_date": "2026-06-30",
        "subsidiary_id": "sub-1",
        "name": "Subsidiary",
        "country_code": "US",
        "entity_type": "bank",
        "functional_currency": "USD",
        "ownership_pct": "100",
        "consolidation_method": "full",
        "control_via_board": "true",
        "total_assets_ghs": "100",
        "total_liabilities_ghs": "80",
        "equity_ghs": "20",
        "net_profit_ytd_ghs": "1",
        "intercompany_receivable_ghs": "0",
        "intercompany_payable_ghs": "0",
    },
    "tariff_schedule": {
        "form": "BSD15A",
        "sheet": "DOMESTIC",
        "row_key": "bank_charge",
        "charge_value": "free",
    },
    "capital_expenditure": {
        "period_end": "2026-06-30",
        "asset_class": "computers",
        "opening_nbv_ghs": "80",
        "additions_purchased_ghs": "20",
        "additions_finance_lease_ghs": "0",
        "additions_hire_purchase_ghs": "0",
        "disposal_proceeds_ghs": "0",
        "disposals_nbv_ghs": "0",
        "depreciation_ghs": "10",
        "closing_cost_ghs": "100",
        "accumulated_depreciation_ghs": "10",
        "closing_nbv_ghs": "90",
    },
    "atm_operations": {
        "month": "2026-06-30",
        "atm_id": "atm-1",
        "station": "HQ",
        "cards_issued": "20",
        "min_withdrawal_ghs": "10",
        "max_withdrawal_ghs": "100",
    },
    "remittance_flows": {
        "month": "2026-06-30",
        "direction": "inbound",
        "corridor_country": "US",
        "region": "usa_canada",
        "recipient_class": "individual",
        "channel": "bank",
        "currency": "USD",
        "amount_fx": "10",
        "amount_usd": "10",
        "amount_ghs": "150",
    },
    "teller_withdrawals": {
        "txn_date": "2026-06-30",
        "branch": "HQ",
        "customer_reference": "cif-1",
        "customer_name": "Customer",
        "account_type": "current",
        "currency": "USD",
        "amount": "10",
        "amount_ghs": "150",
        "channel": "teller",
    },
    "interest_accruals": {
        "as_of_date": "2026-06-30",
        "bsd2_row": "20",
        "side": "asset",
        "currency": "USD",
        "accrued_interest_ghs": "10",
    },
    "performance_targets": _VALID_TARGET,
    "gl_segment_balances": {
        "as_of_date": "2026-06-30",
        "gl_account_code": "4001",
        "branch_id": "BR-001",
        "ytd_balance": "1000",
    },
}


def test_every_admitted_kind_has_a_registered_schema_and_valid_example() -> None:
    assert set(VALID_ROWS) == set(REFERENCE_DATASET_KINDS) == set(SCHEMAS)


@pytest.mark.parametrize("kind", REFERENCE_DATASET_KINDS)
def test_each_kind_accepts_a_valid_row_and_preserves_extra_source_fields(kind: str) -> None:
    payload = {**VALID_ROWS[kind], "source_note": "retained verbatim", "source_null": None}
    assert _row(kind, payload).payload == payload


@pytest.mark.parametrize("kind", REFERENCE_DATASET_KINDS)
def test_each_kind_refuses_a_malformed_row_with_kind_and_row_number(kind: str) -> None:
    with pytest.raises(ValueError, match=f"{kind} row 1: .*missing required field"):
        _row(kind, {"unrelated": "field"})


@pytest.mark.parametrize("kind", REFERENCE_DATASET_KINDS)
def test_each_required_field_is_checked_at_construction(kind: str) -> None:
    schema = SCHEMAS[kind]
    for field in schema.required:
        payload = dict(VALID_ROWS[kind])
        payload.pop(field)
        with pytest.raises(ValueError, match=field):
            _row(kind, payload)


@pytest.mark.parametrize("bad", ["not a number", "NaN", "Infinity", "-Infinity"])
def test_every_declared_numeric_field_refuses_invalid_values(bad: str) -> None:
    for kind, schema in SCHEMAS.items():
        for field in schema.numeric:
            with pytest.raises(ValueError, match=field):
                _row(kind, {**VALID_ROWS[kind], field: bad})


def test_every_declared_date_and_enum_is_checked_at_construction() -> None:
    for kind, schema in SCHEMAS.items():
        for field in schema.dates:
            for bad in (
                "not a date",
                "2026-02-30",
                "20260630",
                "2026-W23-2",
                "2026-6-30",
                " 2026-06-30",
                "2026-06-30 ",
                "2026-06-30T00:00:00",
            ):
                with pytest.raises(ValueError, match=field):
                    _row(kind, {**VALID_ROWS[kind], field: bad})
        for field in schema.enums:
            with pytest.raises(ValueError, match=field):
                _row(kind, {**VALID_ROWS[kind], field: "not-registered"})


@pytest.mark.parametrize(
    ("kind", "changes", "message"),
    [
        ("subsidiaries", {"ownership_pct": "80"}, "minority_interest_ghs"),
        ("subsidiaries", {"ownership_pct": "101"}, "between 0 and 100"),
        ("interest_accruals", {"side": "liability"}, "asset-side"),
        ("gl_mapping_bsd7", {"gl_prefix": "4"}, "not both"),
        ("gl_mapping_bsd7", {"gl_account_code": ""}, "is required"),
        ("capital_expenditure", {"closing_nbv_ghs": "80"}, "must equal"),
        (
            "capital_expenditure",
            {"forecast_next_6m_ghs": "10", "forecast_0_3m_ghs": "8", "forecast_3_6m_ghs": "8"},
            "must equal",
        ),
    ],
)
def test_previously_unbound_cross_field_rules_run_at_the_boundary(
    kind: str,
    changes: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _row(kind, {**VALID_ROWS[kind], **changes})


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        (
            "behavioral_assumptions",
            {"product_code": "DEP-SAV", "assumption": "DEPOSIT_STABILITY", "value": "0.82"},
        ),
        ("fx_rates_current", {"pair": "USD/EUR", "rate": "0.9", "quote_date": "2026-06-30"}),
        ("fx_rates_historical", {"pair": "USD/EUR", "rate": "0.9", "quote_date": "2026-06-30"}),
        ("historical_cashflows", {"date": "2026-06-30", "inflow": "100", "outflow": "80"}),
        (
            "historical_financials",
            {"month": "2026-06-30", "total_assets": "100", "net_income": "10"},
        ),
    ],
)
def test_documented_alternative_shapes_are_validated_without_rewriting(
    kind: str,
    payload: dict[str, str | None],
) -> None:
    assert _row(kind, payload).payload == payload
    with pytest.raises(ValueError):
        _row(
            kind,
            {
                key: value
                for key, value in payload.items()
                if key not in ("value", "rate", "inflow", "net_income")
            },
        )


def test_required_fields_reject_null_empty_and_whitespace_values() -> None:
    for kind, schema in SCHEMAS.items():
        for field in schema.required:
            for value in (None, "", "   "):
                with pytest.raises(ValueError, match=field):
                    _row(kind, {**VALID_ROWS[kind], field: value})


def test_a_missing_registration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(SCHEMAS, "institution")
    with pytest.raises(ValueError, match="no registered reference schema"):
        _row("institution", VALID_ROWS["institution"])


def test_a_malformed_optional_value_is_not_treated_as_absent() -> None:
    with pytest.raises(ValueError, match="accrued_interest_native"):
        _row(
            "interest_accruals",
            {**VALID_ROWS["interest_accruals"], "accrued_interest_native": "n/a"},
        )
    assert _row(
        "interest_accruals", {**VALID_ROWS["interest_accruals"], "accrued_interest_native": None}
    )


@pytest.mark.parametrize("field", ["assumption_type", "assumption"])
@pytest.mark.parametrize("bad", [None, "", "   "])
def test_a_behavioral_assumption_requires_a_nonblank_type_in_its_supplied_shape(
    field: str, bad: str | None
) -> None:
    payload = {"product_code": "DEP-SAV", field: bad, "value": "0.82"}
    if field == "assumption_type":
        payload["assumption"] = "DEPOSIT_STABILITY"
    with pytest.raises(ValueError, match=field):
        _row("behavioral_assumptions", payload)


@pytest.mark.parametrize(
    "assumption_type", ["DEPOSIT_STABILITY", "NMD_DURATION", "PREPAYMENT_RATE"]
)
def test_consumed_behavioral_types_are_preserved_with_documented_extra_fields(
    assumption_type: str,
) -> None:
    payload = {
        **VALID_ROWS["behavioral_assumptions"],
        "assumption_type": assumption_type,
        "assumption": "source description",
    }
    assert _row("behavioral_assumptions", payload).payload == payload


def test_every_declared_date_field_retains_a_canonical_calendar_date() -> None:
    for kind, schema in SCHEMAS.items():
        for field in schema.dates:
            payload = {**VALID_ROWS[kind], field: "2026-06-30"}
            assert _row(kind, payload).payload == payload


@given(days=st.lists(st.dates(), min_size=3, max_size=12, unique=True))
def test_accepted_fx_history_dates_sort_in_calendar_order(days: list[date]) -> None:
    retained: list[str] = []
    for day in days:
        value = _row(
            "fx_rates_historical", {**VALID_ROWS["fx_rates_historical"], "date": day.isoformat()}
        ).payload["date"]
        assert isinstance(value, str)
        retained.append(value)
    assert retained == [day.isoformat() for day in days]
    assert sorted(retained) == [day.isoformat() for day in sorted(days)]
