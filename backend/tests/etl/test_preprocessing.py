"""Deterministic preprocessing-stage tests (normalizers, coercion, reference resolution)."""

from __future__ import annotations

from app.domain.ingestion.contracts import RawRecord
from app.etl.contracts import Disposition, ETLOperationType
from app.etl.preprocessing import (
    CountryNormalizer,
    CurrencyNormalizer,
    DateNormalizer,
    ReferenceResolver,
    TextNormalizer,
    TypeCoercer,
)
from app.etl.preprocessing.reference_resolution import RESOLVED_CATEGORY_FIELD


def _counterparty(**data: object) -> RawRecord:
    return RawRecord(
        entity_type="counterparty",
        source_locator="upload#X",
        source_table="upload",
        data={"counterparty_id": "X", **data},
    )


def _position(**data: object) -> RawRecord:
    return RawRecord(
        entity_type="position",
        source_locator="loans#P",
        source_table="loans",
        data={"position_id": "P", "source_reference": "ARR/P", **data},
    )


# -- text normalizer -----------------------------------------------------------------


def test_text_normalizer_trims_and_collapses_whitespace() -> None:
    ops = TextNormalizer().apply(_counterparty(counterparty_name="  ACME   TRADING  LTD "))
    assert len(ops) == 1
    assert ops[0].after == "ACME TRADING LTD"
    assert ops[0].disposition is Disposition.SANCTIONED
    assert ops[0].provenance.operation_type is ETLOperationType.NORMALIZE


def test_text_normalizer_skips_clean_and_currency_country_fields() -> None:
    # A clean name yields no op; currency/country are owned by the ISO stages.
    record = _counterparty(counterparty_name="Acme", ccy="ghs", country="Ghana")
    assert TextNormalizer().apply(record) == []


# -- currency normalizer -------------------------------------------------------------


def test_currency_normalizer_canonicalises_iso_code() -> None:
    ops = CurrencyNormalizer().apply(_counterparty(ccy="ghs"))
    assert len(ops) == 1
    assert ops[0].after == "GHS"
    # currency is regulatory-critical; the value-preserving rewrite is admitted via the alias.
    assert ops[0].disposition is Disposition.SANCTIONED
    assert ops[0].field_name == "ccy"


def test_currency_normalizer_maps_curated_synonym() -> None:
    ops = CurrencyNormalizer().apply(_counterparty(ccy="Cedi"))
    assert ops[0].after == "GHS"
    assert ops[0].disposition is Disposition.SANCTIONED


def test_currency_normalizer_flags_unknown_code() -> None:
    ops = CurrencyNormalizer().apply(_counterparty(ccy="ZZZ"))
    assert len(ops) == 1
    # Unknown currency on a critical concept is flagged, never guessed.
    assert ops[0].disposition is Disposition.FLAGGED
    assert ops[0].after is None
    assert ops[0].reason


# -- country normalizer --------------------------------------------------------------


def test_country_normalizer_maps_name_and_alpha3_to_alpha2() -> None:
    assert CountryNormalizer().apply(_counterparty(country="Ghana"))[0].after == "GH"
    assert CountryNormalizer().apply(_counterparty(country="GHA"))[0].after == "GH"
    # country is not regulatory-critical, so these are plain SANCTIONED rewrites.
    assert CountryNormalizer().apply(_counterparty(country="Ghana"))[0].disposition is (
        Disposition.SANCTIONED
    )


def test_country_normalizer_leaves_unknown_untouched() -> None:
    assert CountryNormalizer().apply(_counterparty(country="Atlantis")) == []


# -- date normalizer -----------------------------------------------------------------


def test_date_normalizer_parses_day_first_string_dates() -> None:
    ops = DateNormalizer().apply(_position(maturity_date="30/04/2026"))
    assert ops[0].after == "2026-04-30"
    assert ops[0].disposition is Disposition.SANCTIONED


def test_date_normalizer_ignores_already_iso_and_non_date_fields() -> None:
    assert DateNormalizer().apply(_position(maturity_date="2026-04-30")) == []
    assert DateNormalizer().apply(_position(branch_name="Accra 12/05")) == []


# -- type coercion -------------------------------------------------------------------


def test_type_coercion_percent_to_fraction_value_preserving() -> None:
    ops = TypeCoercer().apply(_position(interest_rate_pct="15.5%"))
    assert ops[0].after == "0.155"
    # interest_rate_pct resolves to the critical interest_rate concept; value-preserving
    # via the alias, so SANCTIONED.
    assert ops[0].disposition is Disposition.SANCTIONED


def test_type_coercion_thousands_separator_value_preserving() -> None:
    ops = TypeCoercer().apply(_position(balance_ghs="1,234,567.89"))
    assert ops[0].after == "1234567.89"
    assert ops[0].disposition is Disposition.SANCTIONED


def test_type_coercion_null_sentinel_on_critical_field_is_flagged() -> None:
    for sentinel in ("N/A", "-", "TBC", ""):
        ops = TypeCoercer().apply(_position(balance_ghs=sentinel))
        assert len(ops) == 1, sentinel
        assert ops[0].disposition is Disposition.FLAGGED, sentinel
        assert ops[0].after is None


def test_type_coercion_null_sentinel_on_non_critical_field_is_sanctioned_null() -> None:
    ops = TypeCoercer().apply(_counterparty(rating="N/A"))
    assert len(ops) == 1
    assert ops[0].disposition is Disposition.SANCTIONED
    assert ops[0].after is None


def test_type_coercion_excel_serial_date() -> None:
    ops = TypeCoercer().apply(_position(maturity_date=46142))
    assert ops[0].after == "2026-04-30"
    assert ops[0].disposition is Disposition.SANCTIONED


def test_type_coercion_ignores_plain_numbers_and_text() -> None:
    assert TypeCoercer().apply(_position(balance_ghs="1000.00")) == []
    assert TypeCoercer().apply(_counterparty(counterparty_name="Acme")) == []


# -- reference resolution ------------------------------------------------------------


def test_reference_resolver_resolves_mapped_product_code() -> None:
    resolver = ReferenceResolver({"LN-RET-01": "RETAIL_MORTGAGE"})
    ops = resolver.apply(_position(product_code="ln-ret-01"))  # case-insensitive
    assert len(ops) == 1
    assert ops[0].disposition is Disposition.SANCTIONED
    assert ops[0].field_name == RESOLVED_CATEGORY_FIELD
    assert ops[0].after == "RETAIL_MORTGAGE"
    assert ops[0].provenance.operation_type is ETLOperationType.REFERENCE_RESOLVE


def test_reference_resolver_flags_unmapped_product_code() -> None:
    resolver = ReferenceResolver({"LN-RET-01": "RETAIL_MORTGAGE"})
    ops = resolver.apply(_position(product_code="UNKNOWN-XYZ"))
    assert len(ops) == 1
    assert ops[0].disposition is Disposition.FLAGGED
    assert ops[0].after is None
    assert ops[0].reason


def test_reference_resolver_ignores_counterparties() -> None:
    resolver = ReferenceResolver({"LN-RET-01": "RETAIL_MORTGAGE"})
    assert resolver.apply(_counterparty(product_code="LN-RET-01")) == []
