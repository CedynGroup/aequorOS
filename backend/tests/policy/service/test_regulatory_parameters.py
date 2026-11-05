"""Regulatory-parameter control plane — resolver + seed (SDI Phase C).

Pins the contracts every SDI engine depends on: precedence
(institution_type > institution_class > fail-loud), effective-dating, provenance,
and confirmation-status surfacing. The seed catalogue is the single source shared
with the migration and the hermetic conftest seed.

This module's other-suite cases live in ``test_regulatory_parameters_integration.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.policy.service import regulatory_parameters as rp


def test_normalized_value_strips_numeric_scale_padding() -> None:
    """A Numeric(18,6) round-trips 80 as Decimal('80.000000') on Postgres;
    normalized_value must render byte-identically to the in-code floor (audit M1)."""

    def _rp(value: str) -> rp.ResolvedParameter:
        return rp.ResolvedParameter(
            param_code="narrow_to_volatile",
            value=Decimal(value),
            value_json=None,
            unit="percent",
            source_citation="LMTD 2026 ¶9",
            confirmation_status="confirmed",
            scope_type="institution_class",
            scope_key="bank",
            jurisdiction_code="GH",
            effective_from=date(2020, 1, 1),
            parameter_id="id",
        )

    assert str(_rp("80.000000").normalized_value) == "80"
    assert str(_rp("100.000000").normalized_value) == "100"
    assert str(_rp("12.500000").normalized_value) == "12.5"
    assert str(_rp("0.000000").normalized_value) == "0"
