from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.ingestion.enrichment import (
    apply_manual_override,
    assign_behavioral_maturity,
    merge_ordered,
    translate_currency,
)

NOW = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)


class TestCurrencyTranslation:
    def test_translates_with_rate_provenance(self) -> None:
        result = translate_currency(
            balance=Decimal("100"),
            currency="USD",
            reporting_currency="GHS",
            fx_rates={"USD": Decimal("15.2")},
            now=NOW,
        )
        assert result.field_updates == {"reporting_currency_balance": Decimal("1520.0")}
        provenance = result.provenance["reporting_currency_balance"]
        assert provenance.source == "POLICY"
        assert provenance.model_id == "fx:USDGHS=15.2"
        assert provenance.original_value == Decimal("100")

    def test_reporting_currency_is_a_noop(self) -> None:
        result = translate_currency(
            balance=Decimal("100"),
            currency="GHS",
            reporting_currency="GHS",
            fx_rates={},
            now=NOW,
        )
        assert result.field_updates == {}

    def test_missing_rate_fails_loudly(self) -> None:
        with pytest.raises(LookupError, match="No FX rate for XOF"):
            translate_currency(
                balance=Decimal("100"),
                currency="XOF",
                reporting_currency="GHS",
                fx_rates={"USD": Decimal("15.2")},
                now=NOW,
            )


class TestBehavioralMaturity:
    def test_policy_value_applies_to_configured_types(self) -> None:
        result = assign_behavioral_maturity(
            position_type="DEPOSIT", policy_months={"DEPOSIT": 60}, now=NOW
        )
        assert result.field_updates == {"behavioral_maturity_months": 60}
        assert result.provenance["behavioral_maturity_months"].source == "POLICY"

    def test_unconfigured_type_is_untouched(self) -> None:
        result = assign_behavioral_maturity(
            position_type="LOAN", policy_months={"DEPOSIT": 60}, now=NOW
        )
        assert result.field_updates == {}


class TestManualOverride:
    def test_override_records_who_when_why_and_original(self) -> None:
        result = apply_manual_override(
            field_name="behavioral_maturity_months",
            value=48,
            original_value=60,
            user_id="u-123",
            reason="Bank policy: cap NMD duration at 4 years",
            now=NOW,
        )
        record = result.provenance["behavioral_maturity_months"].as_json()
        assert record["value"] == 48
        assert record["original_value"] == 60
        assert record["source"] == "MANUAL_OVERRIDE"
        assert record["override"]["user_id"] == "u-123"
        assert record["override"]["reason"].startswith("Bank policy")

    def test_overrides_apply_last_in_merge_order(self) -> None:
        policy = assign_behavioral_maturity(
            position_type="DEPOSIT", policy_months={"DEPOSIT": 60}, now=NOW
        )
        override = apply_manual_override(
            field_name="behavioral_maturity_months",
            value=48,
            original_value=60,
            user_id="u-123",
            reason="cap",
            now=NOW,
        )
        merged = merge_ordered(policy, override)
        assert merged.field_updates["behavioral_maturity_months"] == 48
        assert merged.provenance["behavioral_maturity_months"].source == "MANUAL_OVERRIDE"

    def test_decimal_values_serialize_as_strings(self) -> None:
        result = apply_manual_override(
            field_name="balance",
            value=Decimal("1600000.50"),
            original_value=Decimal("1500000.50"),
            user_id="u-123",
            reason="Confirmed with branch ledger",
            now=NOW,
        )
        record = result.provenance["balance"].as_json()
        assert record["value"] == "1600000.50"
        assert record["original_value"] == "1500000.50"
